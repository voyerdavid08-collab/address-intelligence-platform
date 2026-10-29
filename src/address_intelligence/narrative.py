"""Evidence-limited underwriting narratives using Groq chat completions."""

import csv
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re
import time
from typing import Any, Callable

import requests

from address_intelligence.config import Settings
from address_intelligence.flood import flood_zone_base


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUN_ROOT = PROJECT_ROOT / "data" / "output" / "run"
RESULTS_PATH = RUN_ROOT / "results_rowlevel.csv"
POLICIES_PATH = PROJECT_ROOT / "data" / "input" / "policyholders_synthetic.csv"
NARRATIVES_PATH = RUN_ROOT / "narratives.jsonl"
CACHE_ROOT = RUN_ROOT / "cache" / "narratives"
DAILY_USAGE_PATH = CACHE_ROOT / "daily_usage.json"
GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"
PROMPT_VERSION = "underwriting-flood-context-v1"
MAX_REQUESTS_PER_MINUTE = 30
MAX_TOKENS_PER_MINUTE = 8000
MAX_COMPLETION_TOKENS = 512
MAX_NARRATIVE_CHARS = 600

EVIDENCE_FIELDS = (
    "line_of_business",
    "metro",
    "match_quality",
    "flood_zone_base",
    "flood_risk_level",
    "base_flood_elevation",
    "distance_to_water",
    "built_year",
)
REQUIRED_RESPONSE_KEYS = {
    "narrative",
    "evidence_fields_used",
    "missing_evidence",
}
_BANNED_TERMS = (
    "premium",
    "price",
    "pricing",
    "rate increase",
    "deny",
    "denial",
    "approve",
    "approval",
    "eligible",
    "ineligible",
    "coverage decision",
    "will flood",
    "guaranteed",
)
_NUMBER_PATTERN = re.compile(r"(?<![\w.])-?\d+(?:,\d{3})*(?:\.\d+)?")
_SENTENCE_END_PATTERN = re.compile(r"[.!?](?:\s|$)")


class NarrativeAPIError(RuntimeError):
    """Raised when a Groq response cannot be safely consumed."""


class NarrativeGuardrailError(ValueError):
    """Raised when a model response violates a deterministic guardrail."""


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        return list(csv.DictReader(source))


def _json_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_evidence(
    result: dict[str, str], policy: dict[str, str]
) -> dict[str, str]:
    """Build the only fields permitted in a model request."""
    precision = result.get("precision_code", "").upper()
    if precision.startswith("S8"):
        match_quality = "building-level"
    elif precision.startswith(("S5", "S7")):
        match_quality = "street-interpolated"
    else:
        match_quality = ""

    flood_base = flood_zone_base(result.get("flood_zone_code"))
    evidence: dict[str, str] = {
        "line_of_business": policy.get("line_of_business", ""),
        "metro": policy.get("metro", ""),
        "match_quality": match_quality,
        "flood_zone_base": flood_base,
        "flood_risk_level": result.get("flood_risk_level", "").strip(),
    }
    bfe = result.get("bfe", "").strip()
    bfe_unit = result.get("bfe_unit", "").strip()
    if bfe:
        evidence["base_flood_elevation"] = " ".join(
            part for part in (bfe, bfe_unit) if part
        )
    distance = result.get("distance_to_water", "").strip()
    distance_unit = result.get("distance_to_water_unit", "").strip()
    if distance:
        evidence["distance_to_water"] = " ".join(
            part for part in (distance, distance_unit) if part
        )
    built_year = result.get("built_year", "").strip()
    if built_year:
        evidence["built_year"] = built_year
    return {
        name: value
        for name, value in evidence.items()
        if name in EVIDENCE_FIELDS and value
    }


def validate_model_result(content: str, evidence: dict[str, str]) -> dict[str, Any]:
    """Parse and validate the model's JSON against evidence and output constraints."""
    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        raise NarrativeGuardrailError("response is not valid JSON") from None
    if not isinstance(result, dict) or set(result) != REQUIRED_RESPONSE_KEYS:
        raise NarrativeGuardrailError("response must contain exactly the required keys")
    narrative = result["narrative"]
    used = result["evidence_fields_used"]
    missing = result["missing_evidence"]
    if not isinstance(narrative, str) or not isinstance(used, list) or not isinstance(missing, list):
        raise NarrativeGuardrailError("response values have invalid types")
    if not all(isinstance(item, str) for item in used + missing):
        raise NarrativeGuardrailError("evidence lists must contain strings")
    if not set(used).issubset(evidence):
        raise NarrativeGuardrailError("evidence_fields_used includes an unsupplied field")
    if not set(missing).issubset(EVIDENCE_FIELDS):
        raise NarrativeGuardrailError("missing_evidence includes an unknown field")
    if not set(missing).issubset(set(EVIDENCE_FIELDS) - set(evidence)):
        raise NarrativeGuardrailError("missing_evidence includes a supplied field")
    if len(narrative) > MAX_NARRATIVE_CHARS:
        raise NarrativeGuardrailError("narrative exceeds 600 characters")
    sentence_count = len(_SENTENCE_END_PATTERN.findall(narrative.strip()))
    if sentence_count not in (2, 3):
        raise NarrativeGuardrailError("narrative must contain 2 or 3 sentences")
    lowered = narrative.casefold()
    for term in _BANNED_TERMS:
        if re.search(rf"(?<!\w){re.escape(term)}(?!\w)", lowered):
            raise NarrativeGuardrailError(f"narrative contains banned term: {term}")
    allowed_numbers = {
        _decimal_number(match.group(0))
        for value in evidence.values()
        for match in _NUMBER_PATTERN.finditer(value)
    }
    for match in _NUMBER_PATTERN.finditer(narrative):
        number = _decimal_number(match.group(0))
        if number not in allowed_numbers:
            raise NarrativeGuardrailError("narrative contains an unsupported number")
    return result


def _decimal_number(value: str) -> Decimal:
    try:
        return Decimal(value.replace(",", "")).normalize()
    except InvalidOperation:
        return Decimal("NaN")


def _load_daily_usage(path: Path, today: str) -> int:
    if not path.exists():
        return 0
    stored = json.loads(path.read_text(encoding="utf-8"))
    return int(stored["tokens"]) if stored.get("date") == today else 0


def _write_daily_usage(path: Path, today: str, tokens: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"date": today, "tokens": tokens}, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _token_estimate(messages: list[dict[str, str]]) -> int:
    return math.ceil(sum(len(message["content"]) for message in messages) / 4)


def _request_messages(evidence: dict[str, str]) -> list[dict[str, str]]:
    system = (
        "You are an underwriting-support assistant. Write 2-3 plain-English "
        "sentences for an underwriter using only the supplied evidence fields. "
        "State material missing evidence, such as a missing year built. Do not "
        "make pricing, premium, eligibility, coverage, or claims decisions. "
        "Do not predict future flooding. Return only JSON with exactly these "
        "keys: narrative (string), evidence_fields_used (array of supplied "
        "field names), missing_evidence (array of absent allowlisted field names)."
    )
    user = (
        f"Prompt version: {PROMPT_VERSION}\n"
        "Allowlisted evidence:\n"
        f"{json.dumps(evidence, sort_keys=True, ensure_ascii=True)}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


class NarrativeGenerator:
    """Generate local narrative output, defaulting to a network-free dry run."""

    def __init__(
        self,
        settings: Settings,
        *,
        results_path: Path = RESULTS_PATH,
        policies_path: Path = POLICIES_PATH,
        output_path: Path = NARRATIVES_PATH,
        cache_root: Path = CACHE_ROOT,
        daily_usage_path: Path = DAILY_USAGE_PATH,
        post: Callable[..., Any] = requests.post,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.settings = settings
        self.results_path = results_path
        self.policies_path = policies_path
        self.output_path = output_path
        self.cache_root = cache_root
        self.daily_usage_path = daily_usage_path
        self.post = post
        self.clock = clock
        self.sleeper = sleeper
        self._request_times: deque[float] = deque()
        self._token_usage: deque[tuple[float, int]] = deque()

    def _records(self, limit: int | None) -> list[tuple[dict[str, str], dict[str, str]]]:
        policies = {row["policy_id"]: row for row in _read_csv(self.policies_path)}
        results = _read_csv(self.results_path)
        records = []
        for result in results:
            policy = policies.get(result["policy_id"])
            if policy is None:
                raise ValueError(f"No synthetic policy found for {result['policy_id']}.")
            records.append((result, policy))
        return records[:limit] if limit is not None else records

    @staticmethod
    def _route(result: dict[str, str], evidence: dict[str, str]) -> str:
        if result.get("eligibility") != "eligible":
            return "template"
        if not evidence.get("flood_zone_base") or not evidence.get("flood_risk_level"):
            return "abstained"
        return "model"

    def dry_run(self, limit: int | None = None) -> dict[str, int]:
        routes = {"template": 0, "abstained": 0, "model": 0, "cached": 0}
        estimated_tokens = 0
        for result, policy in self._records(limit):
            evidence = build_evidence(result, policy)
            route = self._route(result, evidence)
            if route == "model" and self._cache_path(result["policy_id"], evidence).exists():
                routes["cached"] += 1
                continue
            routes[route] += 1
            if route == "model":
                estimated_tokens += _token_estimate(_request_messages(evidence))
                estimated_tokens += MAX_COMPLETION_TOKENS
        today = datetime.now(timezone.utc).date().isoformat()
        tokens_used = _load_daily_usage(self.daily_usage_path, today)
        return {
            **routes,
            "estimated_tokens": estimated_tokens,
            "tokens_used_today": tokens_used,
            "daily_budget_remaining": max(
                0, self.settings.groq_daily_token_budget - tokens_used
            ),
        }

    def execute(self, limit: int | None = None) -> dict[str, int]:
        if not self.settings.groq_api_key:
            raise ValueError("GROQ_API_KEY is required for --execute.")
        selected = self._records(limit)
        output_records = self._existing_output()
        today = datetime.now(timezone.utc).date().isoformat()
        tokens_used = _load_daily_usage(self.daily_usage_path, today)
        processed = {"template": 0, "abstained": 0, "generated": 0, "rejected_by_guardrail": 0, "failed": 0}
        stopped_for_budget = False

        for result, policy in selected:
            policy_id = result["policy_id"]
            evidence = build_evidence(result, policy)
            route = self._route(result, evidence)
            if route == "template":
                output = self._template(policy_id, result)
            elif route == "abstained":
                output = self._base_output(policy_id, "abstained")
            else:
                try:
                    response_data, provenance = self._get_model_response(
                        policy_id, evidence, today, tokens_used
                    )
                except BudgetReached:
                    stopped_for_budget = True
                    break
                except NarrativeAPIError:
                    output = self._base_output(policy_id, "failed")
                    processed["failed"] += 1
                else:
                    if provenance == "api":
                        tokens_used += response_data["tokens"]
                        _write_daily_usage(self.daily_usage_path, today, tokens_used)
                    try:
                        validated = validate_model_result(response_data["content"], evidence)
                    except NarrativeGuardrailError as error:
                        output = self._base_output(policy_id, "rejected_by_guardrail")
                        output["guardrail_reason"] = str(error)
                        output["model"] = self.settings.groq_model
                        output["prompt_version"] = PROMPT_VERSION
                        output["tokens"] = response_data["tokens"]
                        processed["rejected_by_guardrail"] += 1
                    else:
                        output = self._base_output(policy_id, "generated")
                        output.update(validated)
                        output["model"] = self.settings.groq_model
                        output["prompt_version"] = PROMPT_VERSION
                        output["tokens"] = response_data["tokens"]
                        output["cache"] = provenance
                        processed["generated"] += 1
            if route in ("template", "abstained"):
                processed[route] += 1
            output_records[policy_id] = output

        self._write_output(output_records)
        remaining = max(0, self.settings.groq_daily_token_budget - tokens_used)
        result = {**processed, "tokens_used_today": tokens_used, "tokens_remaining_today": remaining}
        if stopped_for_budget:
            result["stopped_for_budget"] = 1
        return result

    def _get_model_response(
        self, policy_id: str, evidence: dict[str, str], today: str, tokens_used: int
    ) -> tuple[dict[str, Any], str]:
        cache_path = self._cache_path(policy_id, evidence)
        if cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8")), "cached"

        messages = _request_messages(evidence)
        estimated_tokens = _token_estimate(messages) + MAX_COMPLETION_TOKENS
        remaining = self.settings.groq_daily_token_budget - tokens_used
        if estimated_tokens > remaining:
            raise BudgetReached
        self._wait_for_capacity(estimated_tokens)
        headers = {
            "Authorization": f"Bearer {self.settings.groq_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.settings.groq_model,
            "messages": messages,
            "temperature": 0.2,
            "max_completion_tokens": MAX_COMPLETION_TOKENS,
            "reasoning_effort": "low",
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "underwriting_narrative",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "narrative": {"type": "string"},
                            "evidence_fields_used": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "missing_evidence": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": [
                            "narrative",
                            "evidence_fields_used",
                            "missing_evidence",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
        }
        self._request_times.append(self.clock())
        try:
            response = self.post(
                GROQ_CHAT_COMPLETIONS_URL,
                headers=headers,
                json=payload,
                timeout=60,
            )
        except requests.RequestException:
            raise NarrativeAPIError("Groq request failed.") from None
        if response.status_code < 200 or response.status_code >= 300:
            raise NarrativeAPIError(f"Groq returned HTTP {response.status_code}.")
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            usage = body["usage"]
            tokens = int(usage["total_tokens"])
        except (ValueError, KeyError, TypeError, IndexError):
            raise NarrativeAPIError("Groq returned an incomplete response.") from None
        if not isinstance(content, str) or tokens < 0:
            raise NarrativeAPIError("Groq returned an invalid response.")
        cached = {"content": content, "tokens": tokens}
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cached, sort_keys=True) + "\n", encoding="utf-8")
        self._token_usage.append((self.clock(), tokens))
        return cached, "api"

    def _cache_path(self, policy_id: str, evidence: dict[str, str]) -> Path:
        cache_key = _json_hash(
            {
                "prompt_version": PROMPT_VERSION,
                "model": self.settings.groq_model,
                "evidence": evidence,
            }
        )
        return self.cache_root / f"{policy_id}_{cache_key}.json"

    def _wait_for_capacity(self, estimated_tokens: int) -> None:
        while True:
            now = self.clock()
            while self._request_times and now - self._request_times[0] >= 60:
                self._request_times.popleft()
            while self._token_usage and now - self._token_usage[0][0] >= 60:
                self._token_usage.popleft()
            delays = []
            if len(self._request_times) >= MAX_REQUESTS_PER_MINUTE:
                delays.append(60 - (now - self._request_times[0]))
            used = sum(tokens for _, tokens in self._token_usage)
            if used + estimated_tokens > MAX_TOKENS_PER_MINUTE and self._token_usage:
                delays.append(60 - (now - self._token_usage[0][0]))
            if not delays:
                return
            self.sleeper(max(0.01, min(delays)))

    @staticmethod
    def _base_output(policy_id: str, status: str) -> dict[str, Any]:
        return {
            "policy_id": policy_id,
            "status": status,
            "narrative": "",
            "evidence_fields_used": [],
            "missing_evidence": [],
            "guardrail_reason": "",
            "model": "",
            "prompt_version": PROMPT_VERSION,
            "tokens": 0,
        }

    def _template(self, policy_id: str, result: dict[str, str]) -> dict[str, Any]:
        reason = result.get("manual_review_reason", "").strip()
        if not reason:
            reason = "the address match did not meet building-level verification"
        output = self._base_output(policy_id, "template")
        output["narrative"] = (
            f"The location could not be verified to building level because {reason}. "
            "Enrichment was withheld pending address remediation."
        )
        return output

    def _existing_output(self) -> dict[str, dict[str, Any]]:
        if not self.output_path.exists():
            return {}
        records = {}
        with self.output_path.open(encoding="utf-8") as source:
            for line in source:
                if line.strip():
                    row = json.loads(line)
                    records[row["policy_id"]] = row
        return records

    def _write_output(self, records: dict[str, dict[str, Any]]) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with self.output_path.open("w", encoding="utf-8", newline="\n") as destination:
            for row in records.values():
                row.pop("cache", None)
                destination.write(json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n")


class BudgetReached(Exception):
    """Internal stop signal when the daily token budget is exhausted."""


def run_narrative_cli(argv: list[str] | None = None) -> int:
    import argparse

    from address_intelligence.logging_setup import configure_logging

    parser = argparse.ArgumentParser(description="Generate evidence-limited narratives.")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--execute", action="store_true", help="Call Groq (requires typing YES).")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    settings = Settings.from_env()
    configure_logging(settings)
    generator = NarrativeGenerator(settings)
    counts = generator.dry_run(args.limit)
    if not args.execute:
        print("Mode: DRY RUN (no network calls)")
    else:
        print("Mode: EXECUTE (plan; confirmation required)")
    print(
        f"Records: {sum(counts[key] for key in ('template', 'abstained', 'model', 'cached'))}; "
        f"templates: {counts['template']}; abstentions: {counts['abstained']}; "
        f"model calls: {counts['model']}; cached: {counts['cached']}; "
        f"estimated tokens: {counts['estimated_tokens']}; "
        f"daily budget remaining: {counts['daily_budget_remaining']}"
    )
    if not args.execute:
        return 0
    if input("Type YES to send allowlisted evidence to Groq: ") != "YES":
        print("Execution cancelled.")
        return 1
    summary = generator.execute(args.limit)
    print("Narrative stage summary:")
    for name, value in summary.items():
        print(f"{name.replace('_', ' ').capitalize()}: {value}")
    return 0
