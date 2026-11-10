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
    "If the address looks misspelled, you may verify your own corrected spelling with the same house number and state. "
    "If Precisely matches a different street name in a different city, treat it as a different place, not a correction: "
    "propose no fix and send it to analyst research. "
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
    if not summary.get("precisely_id"):
        summary["precisely_id"] = find_precisely_id(payload)
    return {key: value for key, value in summary.items() if value not in (None, "", [])}


def _flatten(value: Any, prefix: str = "", out: dict[str, Any] | None = None) -> dict[str, Any]:
    out = {} if out is None else out
    if isinstance(value, dict):
        for key, item in value.items():
            _flatten(item, f"{prefix}{key}.", out)
    elif isinstance(value, list):
        if value:
            _flatten(value[0], prefix, out)
    elif value not in (None, ""):
        out[prefix.rstrip(".")] = value
    return out


FLOOD_FIELDS = (
    ("floodzone", "Flood zone"),
    ("zonedescription", "What the zone means"),
    ("zonedesc", "What the zone means"),
    ("sfha", "Special flood hazard area"),
    ("riskLevel", "Flood risk level"),
    ("basefloodelevation", "Base flood elevation"),
    ("distancetonearestwaterbody", "Distance to nearest water"),
    ("distanceto100", "Distance to 100-year flood zone"),
    ("distanceto500", "Distance to 500-year flood zone"),
    ("communitystatus", "Community status"),
)


def summarize_flood_result(payload: Any) -> dict[str, Any]:
    """Keep the flood fields a business reader understands, with plain labels."""
    result = payload.get("result", payload) if isinstance(payload, dict) else {}
    flat = {
        key: value
        for key, value in _flatten(result).items()
        if not re.search(r"(metadata|geometry|coordinates|credits|query|cursor|status$)", key, re.IGNORECASE)
    }
    kept: dict[str, Any] = {}
    for needle, label in FLOOD_FIELDS:
        for key, value in flat.items():
            last = key.split(".")[-1].lower().replace("_", "")
            if needle.lower() in last and label not in kept:
                kept[label] = value
                break
    if not kept:
        for key, value in list(flat.items())[:3]:
            last = key.split(".")[-1]
            kept[re.sub(r"([a-z])([A-Z])", r"\1 \2", last).replace("_", " ").capitalize()] = value
    if not kept:
        return {"found": False}
    return {"found": True, **kept}


def find_precisely_id(payload: Any) -> str:
    """Find the PreciselyID (PB_KEY) anywhere in a Precisely answer."""
    for key, value in _flatten(payload).items():
        if re.search(r"(PB_KEY|preciselyId|pbKey)$", key, re.IGNORECASE) and isinstance(value, str):
            return value
    return ""


def summarize(tool: str, payload: Any) -> dict[str, Any]:
    if tool == "get_flood_risk":
        return summarize_flood_result(payload)
    return summarize_address_result(payload)


# ---------------------------------------------------------------- routes


class McpRoute:
    """Calls Precisely's AI Studio MCP connector with fixed, read-only actions.

    Use it as a context manager to keep one MCP session open for a whole
    investigation (faster). Without it, each call opens its own session.
    """

    name = "Precisely AI Studio MCP connector"

    def __init__(self, token: str, *, url: str = MCP_URL, timeout_seconds: float = 15.0):
        if not token:
            raise LiveAIError("The MCP connector token is missing.")
        self._token = token
        self.url = url
        self.timeout_seconds = timeout_seconds
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: Any = None
        self._stack: Any = None
        self._session: Any = None

    async def _open_session(self, stack: Any) -> Any:
        from mcp import ClientSession
        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

        client = await stack.enter_async_context(
            create_mcp_http_client(headers={"Authorization": f"Apikey {self._token}"})
        )
        streams = await stack.enter_async_context(streamable_http_client(self.url, http_client=client))
        session = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
        await session.initialize()
        return session

    @staticmethod
    def _parse(action_id: str, result: Any) -> Any:
        if getattr(result, "is_error", False) or getattr(result, "isError", False):
            raise LiveAIError(f"Precisely returned an error for {action_id}.")
        text = "\n".join(getattr(block, "text", "") or "" for block in (result.content or []))
        try:
            return json.loads(text)
        except ValueError as error:
            raise LiveAIError(f"Precisely returned an unreadable answer for {action_id}.") from error

    async def _execute_once(self, action_id: str, arguments: dict[str, Any]) -> Any:
        from contextlib import AsyncExitStack

        async with AsyncExitStack() as stack:
            session = await self._open_session(stack)
            result = await session.call_tool(
                "precisely_actions_execute", {"action_id": action_id, "arguments": arguments}
            )
        return self._parse(action_id, result)

    async def _execute_open(self, action_id: str, arguments: dict[str, Any]) -> Any:
        result = await self._session.call_tool(
            "precisely_actions_execute", {"action_id": action_id, "arguments": arguments}
        )
        return self._parse(action_id, result)

    def __enter__(self) -> "McpRoute":
        import threading
        from contextlib import AsyncExitStack

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()

        async def opener() -> Any:
            self._stack = AsyncExitStack()
            return await self._open_session(self._stack)

        try:
            self._session = asyncio.run_coroutine_threadsafe(opener(), self._loop).result(self.timeout_seconds)
        except Exception as error:
            self.__exit__(None, None, None)
            raise LiveAIError("The MCP connector did not answer.") from error
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._loop is None:
            return
        if self._stack is not None:
            try:
                asyncio.run_coroutine_threadsafe(self._stack.aclose(), self._loop).result(5)
            except Exception:
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._loop = self._thread = self._stack = self._session = None

    def call(self, tool: str, address: str) -> dict[str, Any]:
        action = TOOL_ACTIONS[tool]
        arguments = {"address": address}
        try:
            if self._session is not None and self._loop is not None:
                payload = asyncio.run_coroutine_threadsafe(
                    self._execute_open(action, arguments), self._loop
                ).result(self.timeout_seconds)
            else:
                payload = asyncio.run(
                    asyncio.wait_for(self._execute_once(action, arguments), self.timeout_seconds)
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
    precisely_data: dict[str, Any] = field(default_factory=dict)
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
            "precisely_data": self.precisely_data,
            "tool_calls": self.tool_calls,
            "seconds": round(self.seconds, 1),
        }


def _house_number(line: str) -> str:
    match = re.match(r"\s*(\d+[A-Za-z]?)\b", line or "")
    return match.group(1) if match else ""


def _split_address(text: str) -> dict[str, str]:
    """Best-effort split of 'line1[, unit], city, ST[,] zip' into line1, city and state."""
    parts = [part.strip() for part in (text or "").split(",") if part.strip()]
    state = ""
    state_index = None
    for index in range(len(parts) - 1, -1, -1):
        found = re.fullmatch(r"([A-Za-z]{2})(?:\s+\d{5}(?:-\d{4})?)?", parts[index])
        if found:
            state, state_index = found.group(1).upper(), index
            break
    if state_index is None and parts:
        tail = re.search(r"\b([A-Za-z]{2})\s+\d{5}(?:-\d{4})?\s*$", parts[-1])
        if tail:
            state = tail.group(1).upper()
            parts[-1] = parts[-1][: tail.start()].strip()
            state_index = len(parts)
    head = parts[:state_index] if state_index is not None else parts
    unit = re.compile(r"^(apt|apartment|unit|ste|suite|#|fl|floor|bldg|rm|room)\b", re.IGNORECASE)
    line_parts, city = [], ""
    for index, part in enumerate(head):
        if index == 0 or unit.match(part):
            line_parts.append(part)
        else:
            city = part
    return {"line1": " ".join(line_parts), "city": city, "state": state}


def _street_name(line1: str) -> str:
    return re.sub(r"^\s*\d+[A-Za-z]?\s+", "", (line1 or "")).upper().strip()


def street_similarity(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    return SequenceMatcher(None, _street_name(a), _street_name(b)).ratio()


def address_change_allowed(candidate: str, original: str) -> bool:
    """A tool may run on a corrected spelling only if house number and state stay the same."""
    if _normalize(candidate) == _normalize(original):
        return True
    cand, orig = _split_address(candidate), _split_address(original)
    if _house_number(cand["line1"]) != _house_number(orig["line1"]):
        return False
    if orig["state"] and cand["state"] and cand["state"] != orig["state"]:
        return False
    return street_similarity(cand["line1"], orig["line1"]) >= 0.5


def check_fix_guardrails(fix: dict[str, str] | None, original: str) -> tuple[dict[str, str] | None, str]:
    """Drop a fix that changes the house number or state, or points at a different place."""
    if not fix:
        return None, ""
    orig = _split_address(original)
    fix_number = _house_number(fix.get("line1", ""))
    if fix_number and fix_number != _house_number(orig["line1"]):
        return None, "The AI's fix changed the house number, so the guardrail removed it."
    if orig["state"] and fix.get("state") and fix["state"].upper() != orig["state"]:
        return None, "The AI's fix changed the state, so the guardrail removed it."
    similarity = street_similarity(fix.get("line1", ""), orig["line1"])
    city_changed = bool(orig["city"]) and _normalize(fix.get("city", "")) != _normalize(orig["city"])
    if similarity < 0.5 or (city_changed and similarity < 0.75):
        return None, (
            "Precisely's closest match is a different street"
            + (" in another city" if city_changed else "")
            + ", so the guardrail treated it as a different place, not a fix. This goes to analyst research."
        )
    return fix, ""


def describe_precision(code: str) -> str:
    """Turn a Precisely precision code into plain words."""
    code = (code or "").upper()
    if code.startswith("S8"):
        return "Exact address point"
    if code.startswith(("S5", "S6", "S7")):
        return "On the street, not the exact building"
    if code.startswith(("S1", "S2", "S3", "S4")):
        return "Approximate street location"
    if code.startswith(("Z", "G")):
        return "ZIP or city level only"
    return ""


def format_fix(fix: dict[str, str]) -> str:
    return f'{fix.get("line1", "")}, {fix.get("city", "")}, {fix.get("state", "")} {fix.get("zip5", "")}'.strip(" ,")


def _normalize(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (text or "").upper())


CORRECTIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
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
        },
    },
    "required": ["candidates"],
    "additionalProperties": False,
}

EXPLANATION_SCHEMA = {
    "type": "object",
    "properties": {
        "finding": {"type": "string"},
        "likely_cause": {"type": "string"},
        "why_it_matters": {"type": "string"},
        "next_step": {"type": "string", "enum": ["customer_outreach", "analyst_research", "approve_fix", "no_change_needed"]},
    },
    "required": ["finding", "likely_cause", "why_it_matters", "next_step"],
    "additionalProperties": False,
}


def confirms_address(check: dict[str, Any], address: str) -> bool:
    """Precisely confirms an address when it finds it with a high score, on the same house and street."""
    if not check.get("found") or (check.get("score") or 0) < 80:
        return False
    given = _split_address(address)
    street = check.get("street_line") or ""
    if _house_number(street) and _house_number(street) != _house_number(given["line1"]):
        return False
    if street_similarity(street, given["line1"]) < 0.85:
        return False
    if given["city"] and check.get("city") and _normalize(check["city"]) != _normalize(given["city"]):
        return bool(check.get("zip5")) and check.get("zip5") in address
    return True


class Investigator:
    """Runs one guided investigation: the AI proposes and explains, Precisely checks every step."""

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
            "reasoning_effort": "low",
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

    def _ask_json(self, messages: list[dict[str, Any]], name: str, schema: dict[str, Any]) -> dict[str, Any]:
        payload_messages = list(messages)
        message = self._chat_schema(payload_messages, name, schema)
        try:
            return json.loads(message.get("content") or "{}")
        except ValueError as error:
            raise LiveAIError("The AI's answer was not valid.") from error

    def _chat_schema(self, messages: list[dict[str, Any]], name: str, schema: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "max_completion_tokens": 900,
            "reasoning_effort": "low",
            "response_format": {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}},
        }
        return self._send(payload)

    def _send(self, payload: dict[str, Any]) -> dict[str, Any]:
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

    def _ai_step(self, label: str, investigation: Investigation, began: float) -> None:
        step = Step(label, None, "done", self.clock() - began, {})
        investigation.steps.append(step)
        self.on_step(step)

    # ------------------------------------------------------------ the run

    def investigate(self, address: str) -> Investigation:
        """Guided investigation: the AI reasons and proposes, Precisely's tools check every step.

        1. Precisely checks the address as given.
        2. If it isn't a clean match, the AI proposes up to two corrected spellings.
        3. Precisely checks each correction; the guardrails reject other houses, states or places.
        4. Precisely locates the best address and checks its flood risk.
        5. The AI explains the finding in plain words. Only a Precisely-confirmed fix can be approved.
        """
        address = " ".join((address or "").split())
        if not address:
            raise LiveAIError("Enter an address to investigate.")
        start = self.clock()
        investigation = Investigation(address=address, route=self.route.name, model=self.model)
        investigation.ai_input = {"address_to_investigate": address, "tools_allowed": list(TOOL_ACTIONS)}
        evidence: dict[str, Any] = {"address_on_file": address}

        as_given = self._run_tool("verify_address", address, start, investigation)
        evidence["precisely_check_as_given"] = as_given
        clean = confirms_address(as_given, address)

        best: str | None = address if clean else None
        if not clean:
            began = self.clock()
            proposal = self._ask_json(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": json.dumps({
                        "task": "The address did not match cleanly. Propose up to two corrected spellings of this "
                                "same address (same house number, same unit, same state). Fix typos only.",
                        "address_on_file": address,
                        "precisely_check_as_given": as_given,
                    })},
                ],
                "corrections",
                CORRECTIONS_SCHEMA,
            )
            self._ai_step("The AI proposes corrected spellings", investigation, began)
            candidates = [c for c in proposal.get("candidates", [])[:2] if str(c.get("line1", "")).strip()]
            tried = []
            for candidate in candidates:
                kept, note = check_fix_guardrails(candidate, address)
                if not kept:
                    tried.append({"candidate": format_fix(candidate), "guardrail": note})
                    continue
                text = format_fix(kept)
                check = self._run_tool("verify_address", text, start, investigation)
                investigation.steps[-1].label = f"Precisely checks the AI's correction: {text}"
                ok = confirms_address(check, text)
                tried.append({"candidate": text, "precisely_check": check, "confirmed": ok})
                if ok:
                    kept = {
                        "line1": kept.get("line1", ""),
                        "city": (check.get("city") or kept.get("city", "")).title(),
                        "state": (check.get("state") or kept.get("state", "")).upper(),
                        "zip5": check.get("zip5") or kept.get("zip5", ""),
                    }
                    investigation.proposed_fix = kept
                    investigation.fix_check = check
                    investigation.fix_confirmed = True
                    best = text
                    break
            evidence["corrections_tried"] = tried
            if investigation.proposed_fix is None and candidates:
                investigation.fix_confirmed = False
            if as_given.get("found") and not confirms_address(as_given, address):
                _, place_note = check_fix_guardrails(
                    {"line1": as_given.get("street_line", ""), "city": as_given.get("city", ""),
                     "state": as_given.get("state", ""), "zip5": as_given.get("zip5", "")},
                    address,
                )
                if place_note:
                    where = ", ".join(
                        x for x in (
                            as_given.get("street_line", "").title(),
                            as_given.get("city", "").title(),
                            as_given.get("state", "").upper(),
                        ) if x
                    )
                    investigation.guardrail_note = (
                        f"Precisely returned the closest match it could find for the address as written ({where}). "
                        "The guardrail rejected it because it changed the street and town"
                        + (", and the AI tried the likely spelling." if investigation.fix_confirmed else ". This goes to analyst research.")
                    )

        if best:
            located = self._run_tool("geocode_address", best, start, investigation)
            evidence["precisely_location"] = located
            investigation.precisely_data = {
                "located": describe_precision(located.get("precision_code", "")),
                "precision_code": located.get("precision_code", ""),
                "precisely_id": located.get("precisely_id", ""),
            }
            if located.get("found"):
                flood = self._run_tool("get_flood_risk", best, start, investigation)
                evidence["precisely_flood"] = flood
                investigation.precisely_data["flood"] = (
                    {k: v for k, v in flood.items() if k != "found"}
                    if flood.get("found")
                    else {"Flood data": "No flood record returned for this address"}
                )

        self._check_time(start)
        began = self.clock()
        answer = self._ask_json(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({
                    "task": "Explain this investigation for a business reader in short, plain sentences. "
                            "Say what was wrong, the likely cause, why it matters for the insurer, and the next step.",
                    "evidence": evidence,
                    "confirmed_fix": format_fix(investigation.proposed_fix) if investigation.proposed_fix else None,
                })},
            ],
            "explanation",
            EXPLANATION_SCHEMA,
        )
        self._ai_step("The AI explains the finding", investigation, began)
        investigation.finding = str(answer.get("finding", "")).strip()
        investigation.likely_cause = str(answer.get("likely_cause", "")).strip()
        investigation.why_it_matters = str(answer.get("why_it_matters", "")).strip()
        if investigation.proposed_fix:
            investigation.next_step = "approve_fix"
        elif clean:
            investigation.next_step = "no_change_needed"
        else:
            suggested = answer.get("next_step")
            investigation.next_step = suggested if suggested in ("customer_outreach", "analyst_research") else "analyst_research"
        investigation.seconds = self.clock() - start
        return investigation


def record_decision(log_path: Path, *, case_id: str, decision: str, investigation: dict[str, Any]) -> dict[str, Any]:
    """Write an analyst's Approve or Reject to the local decision log. Nothing touches a policy record."""
    if decision not in ("approved", "rejected", "sent_to_research", "sent_to_customer", "reviewed"):
        raise ValueError("unknown decision")
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
        "precisely_data": {"located": "Exact address point", "flood": {"Flood zone": "X (made-up)"}, "precisely_id": "P0000EXAMPLE"},
        "tool_calls": 4,
        "seconds": 0.0,
        "made_up": True,
    }
