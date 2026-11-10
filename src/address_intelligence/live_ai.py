"""Live AI investigation of one address, with Precisely tools behind one interface.

The AI (Groq, tool calling) may use exactly three read-only tools: verify an
address, geocode it and get its flood risk. Each tool runs through a route:

* ``McpRoute``: Precisely's AI Studio MCP connector (the default).
* ``DirectRoute``: the existing Precisely API client, as a backup.

Only the address text and Precisely's answers about it go to the AI. Tokens are
never logged or returned. Every Precisely call is written to a usage log.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
from typing import Any, Callable, Protocol

import requests

MCP_URL = "https://api-stg.cloud.precisely.com/mcp"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

TOOL_ACTIONS = {
    "verify_address": "geo_addressing.verify_address",
    "geocode_address": "geo_addressing.geocode",
    "get_flood_risk": "risk.flood",
}
TOOL_STEP_LABELS = {
    "verify_address": "Checking the address with Precisely",
    "geocode_address": "Locating it with Precisely",
    "get_flood_risk": "Checking flood risk with Precisely",
}
FIXED_ORDER = ("verify_address", "geocode_address", "get_flood_risk")
MAX_TOOL_CALLS = 6
DEFAULT_TIMEOUT_SECONDS = 20.0
NEXT_STEPS = ("approve_fix", "customer_outreach", "analyst_research", "no_change_needed")

LIMITS_NOTE = (
    "The AI can be wrong. That's why Precisely checks every fix, "
    "and an analyst approves every change."
)

SYSTEM_PROMPT = (
    "You help an insurance operations analyst investigate one problem address. "
    "You may only use the tools provided, which return Precisely's data about the address. "
    "Check the address first, then locate it, then check its flood risk if it can be located. "
    "Never invent data. Do not add or change a house number, and do not change the state. "
    "Precisely confirming an address means it exists; it does not prove it belongs to the "
    "policyholder. Keep explanations short and plain, for a business reader."
)

FINAL_SCHEMA = {
    "type": "object",
    "properties": {
        "finding": {"type": "string"},
        "likely_cause": {"type": "string"},
        "why_it_matters": {"type": "string"},
        "has_fix": {"type": "boolean"},
        "proposed_fix": {
            "type": "object",
            "properties": {
                "line1": {"type": "string"},
                "city": {"type": "string"},
                "state": {"type": "string"},
                "zip5": {"type": "string"},
            },
            "required": ["line1", "city", "state", "zip5"],
            "additionalProperties": False,
        },
        "next_step": {"type": "string", "enum": list(NEXT_STEPS)},
    },
    "required": ["finding", "likely_cause", "why_it_matters", "has_fix", "proposed_fix", "next_step"],
    "additionalProperties": False,
}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {"address": {"type": "string", "description": "Full US street address."}},
                "required": ["address"],
                "additionalProperties": False,
            },
        },
    }
    for name, description in (
        ("verify_address", "Verify, standardise and correct a US postal address with Precisely."),
        ("geocode_address", "Locate a US address with Precisely: coordinates, match precision and PreciselyID."),
        ("get_flood_risk", "Get Precisely's FEMA flood zone and flood risk details for a US address."),
    )
]


class LiveAIError(RuntimeError):
    """A live service failed; the caller shows the saved fallback instead."""


class ToolRoute(Protocol):
    name: str

    def call(self, tool: str, address: str) -> dict[str, Any]:
        """Run one read-only tool for one address and return a compact summary."""


# ---------------------------------------------------------------- summaries


def _first_candidate(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        result = payload.get("result", payload)
        responses = result.get("responses") if isinstance(result, dict) else None
        if isinstance(responses, list) and responses:
            candidates = responses[0].get("results") or []
            if candidates and isinstance(candidates[0], dict):
                return {"status": responses[0].get("status"), **candidates[0]}
        candidates = result.get("candidates") if isinstance(result, dict) else None
        if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
            return candidates[0]
        if isinstance(result, dict):
            return result
    return {}


def summarize_address_result(payload: Any) -> dict[str, Any]:
    """Keep only the fields the AI and the analyst need from a verify or geocode answer."""
    candidate = _first_candidate(payload)
    if not candidate:
        return {"found": False}
    address = candidate.get("address") or {}
    custom = candidate.get("customFields") or {}
    location = candidate.get("location") or {}
    coordinates = (location.get("feature") or {}).get("geometry", {}).get("coordinates") or []
    explanation = ((candidate.get("explanation") or {}).get("addressMatch") or {}).get("description") or []
    mismatched = [
        item.get("label")
        for item in explanation
        if isinstance(item, dict) and item.get("matchType") not in ("EXACT", "NONE")
    ]
    summary = {
        "found": True,
        "status": candidate.get("status"),
        "score": candidate.get("score"),
        "standardized_address": address.get("formattedAddress") or candidate.get("formattedAddress"),
        "street_line": address.get("formattedStreetAddress"),
        "house_number": address.get("addressNumber"),
        "city": (address.get("city") or {}).get("longName") if isinstance(address.get("city"), dict) else address.get("city"),
        "state": (address.get("admin1") or {}).get("shortName") if isinstance(address.get("admin1"), dict) else address.get("admin1"),
        "zip5": address.get("postalCode"),
        "zip4": address.get("postalCodeExt"),
        "match_code": custom.get("MATCH_CODE"),
        "precision_code": custom.get("PRECISION_CODE") or custom.get("LOC_CODE"),
        "precisely_id": custom.get("PB_KEY"),
        "fields_corrected": mismatched,
    }
    if len(coordinates) == 2:
        summary["longitude"], summary["latitude"] = coordinates
    return {key: value for key, value in summary.items() if value not in (None, "", [])}


def summarize_flood_result(payload: Any) -> dict[str, Any]:
    """Keep the flood fields a business reader understands; drop everything else."""
    result = payload.get("result", payload) if isinstance(payload, dict) else {}
    flat: dict[str, Any] = {}

    def walk(value: Any, prefix: str = "") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                walk(item, f"{prefix}{key}.")
        elif isinstance(value, list):
            if value:
                walk(value[0], prefix)
        elif value not in (None, ""):
            flat[prefix.rstrip(".")] = value

    walk(result)
    wanted = re.compile(
        r"(floodZone|zone$|zoneDesc|riskLevel|description|baseFloodElevation|"
        r"distanceToNearestWaterbody|100|500|communityStatus|sfha)",
        re.IGNORECASE,
    )
    kept = {key: value for key, value in flat.items() if wanted.search(key)}
    return dict(list(kept.items())[:12]) or {"found": False}


def summarize(tool: str, payload: Any) -> dict[str, Any]:
    if tool == "get_flood_risk":
        return summarize_flood_result(payload)
    return summarize_address_result(payload)


# ---------------------------------------------------------------- routes


class McpRoute:
    """Calls Precisely's AI Studio MCP connector with fixed, read-only actions."""

    name = "Precisely AI Studio MCP connector"

    def __init__(self, token: str, *, url: str = MCP_URL, timeout_seconds: float = 15.0):
        if not token:
            raise LiveAIError("The MCP connector token is missing.")
        self._token = token
        self.url = url
        self.timeout_seconds = timeout_seconds

    async def _execute(self, action_id: str, arguments: dict[str, Any]) -> Any:
        from mcp import ClientSession
        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

        client = create_mcp_http_client(headers={"Authorization": f"Apikey {self._token}"})
        async with client:
            async with streamable_http_client(self.url, http_client=client) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    result = await session.call_tool(
                        "precisely_actions_execute",
                        {"action_id": action_id, "arguments": arguments},
                    )
        if getattr(result, "is_error", False) or getattr(result, "isError", False):
            raise LiveAIError(f"Precisely returned an error for {action_id}.")
        text = "\n".join(getattr(block, "text", "") or "" for block in (result.content or []))
        try:
            return json.loads(text)
        except ValueError as error:
            raise LiveAIError(f"Precisely returned an unreadable answer for {action_id}.") from error

    def call(self, tool: str, address: str) -> dict[str, Any]:
        action = TOOL_ACTIONS[tool]
        try:
            payload = asyncio.run(
                asyncio.wait_for(self._execute(action, {"address": address}), self.timeout_seconds)
            )
        except LiveAIError:
            raise
        except Exception as error:  # network, timeout, protocol
            raise LiveAIError(f"The MCP connector did not answer for {action}.") from error
        return summarize(tool, payload)


class DirectRoute:
    """Backup route: the existing Precisely API client and credit ledger."""

    name = "Precisely APIs (direct)"

    def __init__(self, client: Any):
        self.client = client

    def call(self, tool: str, address: str) -> dict[str, Any]:
        # The direct route verifies with Geocode Premium, which also standardises the
        # address; Address Verification stays on the MCP route.
        if tool in ("verify_address", "geocode_address"):
            response = self.client.geocode(address, "premium")
        else:
            located = self.client.geocode(address, "premium")
            if not located.succeeded:
                raise LiveAIError("Precisely could not locate the address for the flood check.")
            summary = summarize_address_result(located.data)
            if "latitude" not in summary:
                return {"found": False}
            response = self.client.flood_by_location(summary["latitude"], summary["longitude"])
        if not response.succeeded:
            raise LiveAIError(f"Precisely returned an error for {tool}.")
        return summarize(tool, response.data)


# ---------------------------------------------------------------- the AI


@dataclass
class Step:
    label: str
    tool: str | None = None
    status: str = "done"
    seconds: float = 0.0
    output: dict[str, Any] = field(default_factory=dict)


@dataclass
class Investigation:
    address: str
    route: str
    model: str
    steps: list[Step] = field(default_factory=list)
    ai_input: dict[str, Any] = field(default_factory=dict)
    finding: str = ""
    likely_cause: str = ""
    why_it_matters: str = ""
    proposed_fix: dict[str, str] | None = None
    next_step: str = "analyst_research"
    fix_check: dict[str, Any] | None = None
    fix_confirmed: bool | None = None
    guardrail_note: str = ""
    tool_calls: int = 0
    seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "route": self.route,
            "model": self.model,
            "steps": [step.__dict__ for step in self.steps],
            "ai_input": self.ai_input,
            "finding": self.finding,
            "likely_cause": self.likely_cause,
            "why_it_matters": self.why_it_matters,
            "proposed_fix": self.proposed_fix,
            "next_step": self.next_step,
            "fix_check": self.fix_check,
            "fix_confirmed": self.fix_confirmed,
            "guardrail_note": self.guardrail_note,
            "tool_calls": self.tool_calls,
            "seconds": round(self.seconds, 1),
        }


def _house_number(line: str) -> str:
    match = re.match(r"\s*(\d+[A-Za-z]?)\b", line or "")
    return match.group(1) if match else ""


def check_fix_guardrails(fix: dict[str, str] | None, original: str) -> tuple[dict[str, str] | None, str]:
    """Drop a fix that adds or changes a house number or changes the state."""
    if not fix:
        return None, ""
    original_number = _house_number(original)
    fix_number = _house_number(fix.get("line1", ""))
    if fix_number and fix_number != original_number:
        return None, "The AI's fix changed the house number, so the guardrail removed it."
    states = re.findall(r"\b([A-Z]{2})\b(?=\s*\d{5}|\s*$)", original.upper())
    if states and fix.get("state") and fix["state"].upper() != states[-1]:
        return None, "The AI's fix changed the state, so the guardrail removed it."
    return fix, ""


def format_fix(fix: dict[str, str]) -> str:
    return f'{fix.get("line1", "")}, {fix.get("city", "")}, {fix.get("state", "")} {fix.get("zip5", "")}'.strip(" ,")


def _normalize(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


class Investigator:
    """Runs one investigation: AI-chosen or fixed-order tools, then a re-check of the fix."""

    def __init__(
        self,
        route: ToolRoute,
        *,
        groq_api_key: str,
        model: str,
        fixed_order: bool = False,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        post: Callable[..., Any] = requests.post,
        clock: Callable[[], float] = time.monotonic,
        on_step: Callable[[Step], None] | None = None,
        usage_log: Path | None = None,
    ):
        if not groq_api_key:
            raise LiveAIError("The AI key is missing.")
        self.route = route
        self._groq_api_key = groq_api_key
        self.model = model
        self.fixed_order = fixed_order
        self.timeout_seconds = timeout_seconds
        self.post = post
        self.clock = clock
        self.on_step = on_step or (lambda step: None)
        self.usage_log = usage_log

    # ------------------------------------------------------------ helpers

    def _check_time(self, start: float) -> None:
        if self.clock() - start > self.timeout_seconds:
            raise LiveAIError("The investigation took longer than the time limit.")

    def _chat(self, messages: list[dict[str, Any]], *, tools: bool, final: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "max_completion_tokens": 900,
        }
        if tools:
            payload["tools"] = TOOL_SCHEMAS
            payload["tool_choice"] = "auto"
        if final:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "investigation", "strict": True, "schema": FINAL_SCHEMA},
            }
        try:
            response = self.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {self._groq_api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=15,
            )
        except requests.RequestException as error:
            raise LiveAIError("The AI service did not answer.") from error
        if not 200 <= response.status_code < 300:
            raise LiveAIError(f"The AI service returned status {response.status_code}.")
        try:
            return response.json()["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise LiveAIError("The AI service returned an unreadable answer.") from error

    def _run_tool(self, tool: str, address: str, start: float, investigation: Investigation) -> dict[str, Any]:
        if tool not in TOOL_ACTIONS:
            raise LiveAIError(f"The AI asked for a tool it is not allowed to use: {tool}.")
        if investigation.tool_calls >= MAX_TOOL_CALLS:
            raise LiveAIError("The AI reached the limit of tool calls for one click.")
        self._check_time(start)
        began = self.clock()
        output = self.route.call(tool, address)
        investigation.tool_calls += 1
        step = Step(TOOL_STEP_LABELS[tool], tool, "done", self.clock() - began, output)
        investigation.steps.append(step)
        self.on_step(step)
        self._log_usage(tool, address)
        return output

    def _log_usage(self, tool: str, address: str) -> None:
        if self.usage_log is None:
            return
        self.usage_log.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "route": self.route.name,
            "action": TOOL_ACTIONS[tool],
        }
        with self.usage_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    # ------------------------------------------------------------ the run

    def investigate(self, address: str) -> Investigation:
        address = " ".join((address or "").split())
        if not address:
            raise LiveAIError("Enter an address to investigate.")
        start = self.clock()
        investigation = Investigation(address=address, route=self.route.name, model=self.model)
        user = {"role": "user", "content": json.dumps({"address_to_investigate": address})}
        investigation.ai_input = {"address_to_investigate": address, "tools_allowed": list(TOOL_ACTIONS)}
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}, user]

        if self.fixed_order:
            located = True
            for tool in FIXED_ORDER:
                if tool == "get_flood_risk" and not located:
                    break
                output = self._run_tool(tool, address, start, investigation)
                if tool == "geocode_address":
                    located = bool(output.get("found"))
                messages.append({"role": "user", "content": json.dumps({f"precisely_{tool}": output})})
        else:
            for _ in range(MAX_TOOL_CALLS + 1):
                self._check_time(start)
                message = self._chat(messages, tools=True, final=False)
                calls = message.get("tool_calls") or []
                if not calls:
                    break
                messages.append({"role": "assistant", "content": message.get("content") or "", "tool_calls": calls})
                for call in calls:
                    name = (call.get("function") or {}).get("name", "")
                    try:
                        arguments = json.loads((call.get("function") or {}).get("arguments") or "{}")
                    except ValueError:
                        arguments = {}
                    # The AI may only investigate the address it was given.
                    output = self._run_tool(name, address, start, investigation)
                    if arguments.get("address") and _normalize(arguments["address"]) != _normalize(address):
                        output = {**output, "note": "Tool run on the original address only."}
                    messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": json.dumps(output)})

        self._check_time(start)
        messages.append({
            "role": "user",
            "content": "Now give your answer as the JSON object requested. Propose a fix only if Precisely's data supports it.",
        })
        final = self._chat(messages, tools=False, final=True)
        try:
            answer = json.loads(final.get("content") or "{}")
        except ValueError as error:
            raise LiveAIError("The AI's answer was not valid.") from error

        investigation.finding = str(answer.get("finding", "")).strip()
        investigation.likely_cause = str(answer.get("likely_cause", "")).strip()
        investigation.why_it_matters = str(answer.get("why_it_matters", "")).strip()
        next_step = answer.get("next_step")
        investigation.next_step = next_step if next_step in NEXT_STEPS else "analyst_research"
        raw_fix = answer.get("proposed_fix") if answer.get("has_fix") else None
        if raw_fix and not str(raw_fix.get("line1", "")).strip():
            raw_fix = None
        fix, note = check_fix_guardrails(raw_fix, address)
        investigation.proposed_fix, investigation.guardrail_note = fix, note

        if fix:
            check = self._run_tool("verify_address", format_fix(fix), start, investigation)
            investigation.steps[-1].label = "Precisely re-checks the AI's fix"
            investigation.fix_check = check
            investigation.fix_confirmed = bool(
                check.get("found")
                and (check.get("score") or 0) >= 90
                and _normalize(check.get("street_line", "")) == _normalize(fix.get("line1", ""))
            )
        investigation.seconds = self.clock() - start
        return investigation


def record_decision(log_path: Path, *, case_id: str, decision: str, investigation: dict[str, Any]) -> dict[str, Any]:
    """Write an analyst's Approve or Reject to the local decision log. Nothing touches a policy record."""
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be approved or rejected")
    entry = {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "case": case_id,
        "decision": decision,
        "address": investigation.get("address"),
        "proposed_fix": investigation.get("proposed_fix"),
        "fix_confirmed_by_precisely": investigation.get("fix_confirmed"),
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")
    return entry


def made_up_investigation(address: str) -> dict[str, Any]:
    """A labelled made-up result for made-up mode and the backup video. No calls."""
    fixed = re.sub(r"\bOKA\b", "OAK", address.upper()) if address else "101 EXAMPLE OAK ST, AUSTIN, TX 78701"
    return {
        "address": address,
        "route": "Made-up example (no calls)",
        "model": "Made-up example",
        "steps": [
            {"label": TOOL_STEP_LABELS[tool], "tool": tool, "status": "done", "seconds": 0.0, "output": {}}
            for tool in FIXED_ORDER
        ] + [{"label": "Precisely re-checks the AI's fix", "tool": "verify_address", "status": "done", "seconds": 0.0, "output": {}}],
        "ai_input": {"address_to_investigate": address, "tools_allowed": list(TOOL_ACTIONS)},
        "finding": "Made-up example: the street name is misspelled, so the address only matched at street level.",
        "likely_cause": "Street name typo",
        "why_it_matters": "Made-up example: until it is fixed, the policy cannot be placed on its exact building for flood rating.",
        "proposed_fix": {"line1": fixed.split(",")[0].strip(), "city": "AUSTIN", "state": "TX", "zip5": "78701"},
        "next_step": "approve_fix",
        "fix_check": {"found": True, "score": 100},
        "fix_confirmed": True,
        "guardrail_note": "",
        "tool_calls": 4,
        "seconds": 0.0,
        "made_up": True,
    }
