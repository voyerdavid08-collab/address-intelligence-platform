"""Evidence-limited underwriting narratives using Groq chat completions."""

import csv
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
from pathlib import Path
import re
import time
from typing import Any, Callable
import warnings

import requests

from address_intelligence.config import Settings
from address_intelligence.flood import flood_zone_base, risk_tier
from address_intelligence.client_profile import DEFAULT_PROFILE_PATH, load_client_profile
from address_intelligence.client_profile import ClientProfile, read_client_policies


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PROFILE = load_client_profile(DEFAULT_PROFILE_PATH)
RUN_ROOT = _DEFAULT_PROFILE.run_dir
RESULTS_PATH = RUN_ROOT / "results_rowlevel.csv"
POLICIES_PATH = _DEFAULT_PROFILE.input_path
NARRATIVES_PATH = RUN_ROOT / "narratives.jsonl"
CACHE_ROOT = RUN_ROOT / "cache" / "narratives"
DAILY_USAGE_PATH = CACHE_ROOT / "daily_usage.json"
REPORTS_ROOT = _DEFAULT_PROFILE.reports_dir
SUMMARY_CSV_PATH = REPORTS_ROOT / "ai_narrative_summary.csv"
SUMMARY_MD_PATH = REPORTS_ROOT / "ai_narrative_summary.md"
GROQ_CHAT_COMPLETIONS_URL = "https://api.groq.com/openai/v1/chat/completions"
PROMPT_VERSION = "underwriting-flood-context-v5"
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
}
OPTIONAL_EVIDENCE_FIELDS = (
    "built_year",
    "base_flood_elevation",
    "distance_to_water",
)
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
_ASCII_PUNCTUATION = str.maketrans(
    {
        "\u2010": "-",
        "\u2011": "-",
        "\u2012": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u00a0": " ",
        "\u202f": " ",
    }
)
_UNAVAILABLE_FIELD_PHRASES = {
    "built_year": ("year built", "built year", "constructed", "construction year"),
    "base_flood_elevation": ("base flood elevation",),
    "distance_to_water": ("distance to water",),
}
_ELEVATION_LABEL_RE = re.compile(r"\b(?:base flood elevation|bfe)\b", re.IGNORECASE)
_PROPERTY_ELEVATION_RE = re.compile(
    r"\b(?:sits at an elevation|property elevation|elevation of the property)\b",
    re.IGNORECASE,
)
_DISCLOSURE_NAMES = {
    "built_year": "year built",
    "base_flood_elevation": "base flood elevation",
    "distance_to_water": "distance to water",
}


class NarrativeAPIError(RuntimeError):
    """Raised when a Groq response cannot be safely consumed."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class NarrativeGuardrailError(ValueError):
    """Raised when a model response violates a deterministic guardrail."""


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        return list(csv.DictReader(source))


def _json_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _clean_numeric_string(value: str) -> str:
    """Remove redundant leading zeros without changing decimal precision."""
    pattern = re.compile(r"(?<![\w.])([+-]?)(\d+)(\.\d+)?")

    def replace(match: re.Match[str]) -> str:
        integer = match.group(2).lstrip("0") or "0"
        return f"{match.group(1)}{integer}{match.group(3) or ''}"

    return pattern.sub(replace, value.strip())


def build_evidence(
    result: dict[str, str], policy: dict[str, str]
) -> dict[str, str]:
    """Build the only fields permitted in a model request."""
    precision = result.get("precision_code", "").upper()
    if precision.startswith("S8"):
        match_quality = "location verified to an exact address point"
    elif precision.startswith(("S5", "S7")):
        match_quality = (
            "location estimated along the street "
            "(house number not confirmed as a known address point)"
        )
    else:
        match_quality = ""

    flood_base = flood_zone_base(result.get("flood_zone_code"))
    evidence: dict[str, str] = {
        "line_of_business": policy.get("line_of_business", "").strip(),
        "metro": policy.get("metro", "").strip(),
        "match_quality": match_quality,
        "flood_zone_base": flood_base,
        "flood_risk_level": result.get("flood_risk_level", "").strip(),
    }
    bfe = _clean_numeric_string(result.get("bfe", ""))
    bfe_unit = result.get("bfe_unit", "").strip().lower()
    if bfe:
        evidence["base_flood_elevation"] = " ".join(
            part for part in (bfe, bfe_unit) if part
        )
    distance = _clean_numeric_string(result.get("distance_to_water", ""))
    distance_unit = result.get("distance_to_water_unit", "").strip().lower()
    if distance:
        evidence["distance_to_water"] = " ".join(
            part for part in (distance, distance_unit) if part
        )
    built_year = _clean_numeric_string(result.get("built_year", ""))
    if built_year:
        evidence["built_year"] = built_year
    return {
        name: value
        for name, value in evidence.items()
        if name in EVIDENCE_FIELDS and value
    }


def compute_not_available(evidence: dict[str, str]) -> list[str]:
    """Return absent optional evidence, applying the BFE risk-tier exception."""
    not_available = [
        field for field in OPTIONAL_EVIDENCE_FIELDS if not evidence.get(field)
    ]
    base = evidence.get("flood_zone_base", "")
    if risk_tier(base) != "High":
        not_available = [
            field for field in not_available if field != "base_flood_elevation"
        ]
    return not_available


def _append_missing_disclosure(
    narrative_model: str, not_available: list[str]
) -> str:
    names = [_DISCLOSURE_NAMES[field] for field in not_available]
    if not names:
        return narrative_model
    if len(names) == 1:
        listing = names[0]
    elif len(names) == 2:
        listing = f"{names[0]} and {names[1]}"
    else:
        listing = f"{', '.join(names[:-1])}, and {names[-1]}"
    return f"{narrative_model.rstrip()} Not available: {listing}."


def validate_model_result(
    content: str, evidence: dict[str, str], not_available: list[str] | None = None
) -> dict[str, Any]:
    """Parse and validate the model's JSON against evidence and output constraints."""
    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        raise NarrativeGuardrailError("response is not valid JSON") from None
    if not isinstance(result, dict) or set(result) != REQUIRED_RESPONSE_KEYS:
        raise NarrativeGuardrailError("response must contain exactly the required keys")
    narrative = result["narrative"]
    used = result["evidence_fields_used"]
    if not isinstance(narrative, str) or not isinstance(used, list):
        raise NarrativeGuardrailError("response values have invalid types")
    if not all(isinstance(item, str) for item in used):
        raise NarrativeGuardrailError("evidence_fields_used must contain strings")
    narrative = narrative.translate(_ASCII_PUNCTUATION)
    result["narrative"] = narrative
    if not set(used).issubset(evidence):
        raise NarrativeGuardrailError(
            "evidence_fields_used includes an unsupplied field"
        )
    lowered = narrative.casefold()
    for field in not_available or []:
        phrases = _UNAVAILABLE_FIELD_PHRASES.get(field, ())
        if any(phrase in lowered for phrase in phrases):
            raise NarrativeGuardrailError("mentions unavailable field")
    if len(narrative) > MAX_NARRATIVE_CHARS:
        raise NarrativeGuardrailError("narrative exceeds 600 characters")
    sentence_count = len(_SENTENCE_END_PATTERN.findall(narrative.strip()))
    if sentence_count not in (2, 3, 4):
        raise NarrativeGuardrailError("narrative must contain 2 to 4 sentences")
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
    bfe_value = evidence.get("base_flood_elevation", "")
    if bfe_value:
        bfe_numbers = {
            _decimal_number(match.group(0))
            for match in _NUMBER_PATTERN.finditer(bfe_value)
        }
        sentences = re.split(r"(?<=[.!?])\s+", narrative.strip())
        for sentence in sentences:
            contains_bfe_value = any(
                _decimal_number(match.group(0)) in bfe_numbers
                for match in _NUMBER_PATTERN.finditer(sentence)
            )
            if not contains_bfe_value:
                continue
            if _PROPERTY_ELEVATION_RE.search(sentence):
                raise NarrativeGuardrailError(
                    "elevation value not labelled as base flood elevation"
                )
            if not _ELEVATION_LABEL_RE.search(sentence):
                raise NarrativeGuardrailError(
                    "elevation value not labelled as base flood elevation"
                )
    return result


def _decimal_number(value: str) -> Decimal:
    try:
        return Decimal(value.replace(",", "")).normalize()
    except InvalidOperation:
        return Decimal("NaN")


def _safe_http_error_reason(response: Any) -> str:
    """Keep only status and allowlisted error identifiers from provider errors."""
    reason = f"HTTP {response.status_code}"
    try:
        body = response.json()
    except (ValueError, TypeError):
        return reason
    if not isinstance(body, dict):
        return reason
    error = body.get("error")
    if not isinstance(error, dict):
        return reason
    identifiers = []
    for key in ("type", "code"):
        value = error.get(key)
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value):
            if value not in identifiers:
                identifiers.append(value)
    if identifiers:
        reason += " " + "/".join(identifiers)
    return reason


def _load_daily_usage(
    path: Path,
    today: str,
    model: str,
    *,
    legacy_model: str | None = None,
) -> int:
    if not path.exists():
        return 0
    stored = json.loads(path.read_text(encoding="utf-8"))
    if stored.get("date") != today:
        return 0
    models = stored.get("models")
    if isinstance(models, dict):
        return int(models.get(model, 0))
    if legacy_model == model:
        return int(stored.get("tokens", 0))
    return 0


def _write_daily_usage(
    path: Path,
    today: str,
    model: str,
    tokens: int,
    *,
    legacy_model: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        stored = json.loads(path.read_text(encoding="utf-8"))
    else:
        stored = {}
    models = stored.get("models", {}) if stored.get("date") == today else {}
    if not isinstance(models, dict):
        models = {}
    if (
        stored.get("date") == today
        and not stored.get("models")
        and legacy_model
        and int(stored.get("tokens", 0))
    ):
        models[legacy_model] = int(stored["tokens"])
    models[model] = tokens
    path.write_text(
        json.dumps({"date": today, "models": models}, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _token_estimate(messages: list[dict[str, str]]) -> int:
    return math.ceil(sum(len(message["content"]) for message in messages) / 4)


def _request_messages(evidence: dict[str, str]) -> list[dict[str, str]]:
    system = (
        "You are an underwriting-support assistant. Describe only the facts "
        "supplied; do not mention information that is not supplied. Write 2–3 "
        "plain-English sentences for an underwriter. You may combine related "
        "facts in one sentence, but never imply that one field causes or "
        "determines another. Do not "
        "make pricing, premium, eligibility, coverage, or claims decisions. "
        "Do not predict future flooding. Return only JSON with exactly these "
        "keys: narrative (string), evidence_fields_used (array of supplied "
        "field names)."
    )
    user = (
        f"Prompt version: {PROMPT_VERSION}\n"
        "Allowlisted evidence:\n"
        f"{json.dumps(evidence, sort_keys=True, ensure_ascii=True)}\n"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


class NarrativeGenerator:
    """Generate local narrative output, defaulting to a network-free dry run."""

    def __init__(
        self,
        settings: Settings,
        *,
        profile: ClientProfile | None = None,
        results_path: Path = RESULTS_PATH,
        policies_path: Path = POLICIES_PATH,
        output_path: Path = NARRATIVES_PATH,
        cache_root: Path = CACHE_ROOT,
        daily_usage_path: Path = DAILY_USAGE_PATH,
        post: Callable[..., Any] = requests.post,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        if profile is not None:
            results_path = profile.run_dir / "results_rowlevel.csv"
            policies_path = profile.input_path
            output_path = profile.run_dir / "narratives.jsonl"
            cache_root = profile.run_dir / "cache" / "narratives"
            daily_usage_path = cache_root / "daily_usage.json"
        self.settings = settings
        self.profile = profile
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
        policy_rows = (
            read_client_policies(self.profile)
            if self.profile is not None
            else _read_csv(self.policies_path)
        )
        policies = {row["policy_id"]: row for row in policy_rows}
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
            not_available = compute_not_available(evidence)
            route = self._route(result, evidence)
            if route == "model" and self._cache_path(result["policy_id"], evidence).exists():
                routes["cached"] += 1
                continue
            routes[route] += 1
            if route == "model":
                estimated_tokens += _token_estimate(_request_messages(evidence))
                estimated_tokens += MAX_COMPLETION_TOKENS
        today = datetime.now(timezone.utc).date().isoformat()
        tokens_used = _load_daily_usage(
            self.daily_usage_path, today, self.settings.groq_model,
            legacy_model=self.settings.groq_model,
        )
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
        tokens_used = _load_daily_usage(
            self.daily_usage_path, today, self.settings.groq_model,
            legacy_model=self.settings.groq_model,
        )
        processed = {"template": 0, "abstained": 0, "generated": 0, "rejected_by_guardrail": 0, "failed": 0}
        stopped_for_budget = False

        for result, policy in selected:
            policy_id = result["policy_id"]
            evidence = build_evidence(result, policy)
            not_available = compute_not_available(evidence)
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
                except NarrativeAPIError as error:
                    output = self._base_output(policy_id, "failed")
                    output["guardrail_reason"] = error.reason
                    processed["failed"] += 1
                else:
                    model_reported = response_data["model_reported"]
                    if model_reported != self.settings.groq_model:
                        warnings.warn(
                            f"Groq-reported model {model_reported!r} differs from "
                            f"requested model {self.settings.groq_model!r}.",
                            RuntimeWarning,
                            stacklevel=2,
                        )
                    if provenance == "api":
                        tokens_used += response_data["tokens"]
                        _write_daily_usage(
                            self.daily_usage_path,
                            today,
                            self.settings.groq_model,
                            tokens_used,
                            legacy_model=self.settings.groq_model,
                        )
                    try:
                        validated = validate_model_result(
                            response_data["content"], evidence, not_available
                        )
                    except NarrativeGuardrailError as error:
                        output = self._base_output(policy_id, "rejected_by_guardrail")
                        output["guardrail_reason"] = str(error)
                        output["model"] = self.settings.groq_model
                        output["model_reported"] = model_reported
                        output["prompt_version"] = PROMPT_VERSION
                        output["tokens"] = response_data["tokens"]
                        processed["rejected_by_guardrail"] += 1
                    else:
                        output = self._base_output(policy_id, "generated")
                        output.update(validated)
                        narrative_model = validated["narrative"]
                        output["narrative_model"] = narrative_model
                        output["narrative"] = _append_missing_disclosure(
                            narrative_model, not_available
                        )
                        output["model"] = self.settings.groq_model
                        output["model_reported"] = model_reported
                        output["prompt_version"] = PROMPT_VERSION
                        output["tokens"] = response_data["tokens"]
                        output["cache"] = provenance
                        processed["generated"] += 1
            output["missing_evidence"] = not_available
            if route in ("template", "abstained"):
                processed[route] += 1
            output_records[policy_id] = output

        self._write_output(output_records)
        remaining = max(0, self.settings.groq_daily_token_budget - tokens_used)
        result = {**processed, "tokens_used_today": tokens_used, "tokens_remaining_today": remaining}
        if stopped_for_budget:
            result["stopped_for_budget"] = 1
        return result

    def revalidate_cached(self, limit: int | None = None) -> dict[str, int]:
        """Rebuild generated records from stored model text without any API calls."""
        selected = self._records(limit)
        output_records = self._existing_output()
        processed = {
            "template": 0,
            "abstained": 0,
            "generated": 0,
            "rejected_by_guardrail": 0,
            "failed": 0,
            "api_calls": 0,
        }

        for result, policy in selected:
            policy_id = result["policy_id"]
            evidence = build_evidence(result, policy)
            not_available = compute_not_available(evidence)
            route = self._route(result, evidence)
            if route == "template":
                output = self._template(policy_id, result)
            elif route == "abstained":
                output = self._base_output(policy_id, "abstained")
            else:
                cached_output = output_records.get(policy_id, {})
                narrative_model = cached_output.get("narrative_model", "")
                if not narrative_model:
                    output = self._base_output(policy_id, "failed")
                    output["guardrail_reason"] = "cached model response unavailable"
                    processed["failed"] += 1
                    output["missing_evidence"] = not_available
                    output_records[policy_id] = output
                    continue
                content = json.dumps(
                    {
                        "narrative": narrative_model,
                        "evidence_fields_used": cached_output.get(
                            "evidence_fields_used", []
                        ),
                    }
                )
                try:
                    validated = validate_model_result(
                        content, evidence, not_available
                    )
                except NarrativeGuardrailError as error:
                    output = self._base_output(
                        policy_id, "rejected_by_guardrail"
                    )
                    output["guardrail_reason"] = str(error)
                    output["narrative_model"] = narrative_model.translate(
                        _ASCII_PUNCTUATION
                    )
                    output["evidence_fields_used"] = cached_output.get(
                        "evidence_fields_used", []
                    )
                    output["model"] = cached_output.get("model", self.settings.groq_model)
                    output["model_reported"] = cached_output.get("model_reported", "")
                    output["tokens"] = cached_output.get("tokens", 0)
                    processed["rejected_by_guardrail"] += 1
                else:
                    output = dict(cached_output)
                    output.update(validated)
                    narrative_model = validated["narrative"]
                    output["narrative_model"] = narrative_model
                    output["narrative"] = _append_missing_disclosure(
                        narrative_model, not_available
                    )
                    output["missing_evidence"] = not_available
                    output["prompt_version"] = PROMPT_VERSION
                    output["status"] = "generated"
                    output["guardrail_reason"] = ""
                    processed["generated"] += 1
            output["missing_evidence"] = not_available
            if route in ("template", "abstained"):
                processed[route] += 1
            output_records[policy_id] = output

        self._write_output(output_records)
        return processed

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
                        },
                        "required": ["narrative", "evidence_fields_used"],
                        "additionalProperties": False,
                    },
                },
            },
        }
        for attempt in range(2):
            self._wait_for_capacity(estimated_tokens)
            self._request_times.append(self.clock())
            try:
                response = self.post(
                    GROQ_CHAT_COMPLETIONS_URL,
                    headers=headers,
                    json=payload,
                    timeout=60,
                )
            except requests.Timeout:
                if attempt == 0:
                    self.sleeper(1.0)
                    continue
                raise NarrativeAPIError("timeout") from None
            except requests.RequestException:
                raise NarrativeAPIError("request_error") from None
            if 200 <= response.status_code < 300:
                break
            reason = _safe_http_error_reason(response)
            retryable = response.status_code == 429 or response.status_code >= 500
            if attempt == 0 and retryable:
                self.sleeper(
                    self._retry_after_seconds(response.headers.get("Retry-After"))
                )
                continue
            raise NarrativeAPIError(reason)
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            usage = body["usage"]
            tokens = int(usage["total_tokens"])
            model_reported = body["model"]
        except (ValueError, KeyError, TypeError, IndexError):
            raise NarrativeAPIError("invalid_response") from None
        if (
            not isinstance(content, str)
            or not isinstance(model_reported, str)
            or not model_reported
            or tokens < 0
        ):
            raise NarrativeAPIError("invalid_response")
        cached = {
            "content": content,
            "tokens": tokens,
            "model_reported": model_reported,
        }
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cached, sort_keys=True) + "\n", encoding="utf-8")
        self._token_usage.append((self.clock(), tokens))
        return cached, "api"

    @staticmethod
    def _retry_after_seconds(value: str | None) -> float:
        if not value:
            return 1.0
        try:
            delay = float(value)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
            except (TypeError, ValueError, OverflowError):
                return 1.0
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
        return max(0.0, delay)

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
            "narrative_model": "",
            "narrative": "",
            "evidence_fields_used": [],
            "missing_evidence": [],
            "guardrail_reason": "",
            "model": "",
            "model_reported": "",
            "prompt_version": PROMPT_VERSION,
            "tokens": 0,
        }

    def _template(self, policy_id: str, result: dict[str, str]) -> dict[str, Any]:
        reason = result.get("manual_review_reason", "").strip().casefold()
        readable_reasons = {
            "street-level only": "matched only at street level",
            "postal or city level": "matched only at postal or city level",
            "no match": "no address match was found",
            "api error": "the geocoding service returned an API error",
            "location conflict (state mismatch)": (
                "the standardized state differed from the input state"
            ),
        }
        reason = readable_reasons.get(
            reason, "the address match did not meet building-level verification"
        )
        output = self._base_output(policy_id, "template")
        output["narrative"] = (
            f"The location could not be verified to building level (reason: {reason}). "
            "Flood enrichment was withheld pending address remediation."
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
    parser.add_argument("--profile", type=Path, default=None)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print estimates without updating local narrative outputs.",
    )
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    profile = load_client_profile(args.profile)
    if not profile.ai_enabled("narratives"):
        print(
            f"Narratives are disabled by client profile '{profile.data['client']['name']}'; "
            "no narrative work was run."
        )
        return 0
    settings = Settings.from_env(profile_path=args.profile)
    configure_logging(settings)
    generator = NarrativeGenerator(settings, profile=profile)
    if args.dry_run:
        counts = generator.dry_run(args.limit)
        print("Mode: DRY RUN (no network calls)")
        print(
            f"Records: {sum(counts[key] for key in ('template', 'abstained', 'model', 'cached'))}; "
            f"templates: {counts['template']}; abstentions: {counts['abstained']}; "
            f"model calls: {counts['model']}; cached: {counts['cached']}; "
            f"estimated tokens: {counts['estimated_tokens']}; "
            f"daily budget remaining: {counts['daily_budget_remaining']}"
        )
        return 0
    if not args.execute:
        print("Mode: CACHE-ONLY REVALIDATION (no API calls)")
        summary = generator.revalidate_cached(args.limit)
        for name, value in summary.items():
            print(f"{name.replace('_', ' ').capitalize()}: {value}")
        return 0
    counts = generator.dry_run(args.limit)
    print("Mode: EXECUTE (plan; confirmation required)")
    print(
        f"Model calls: {counts['model']}; cached: {counts['cached']}; "
        f"estimated tokens: {counts['estimated_tokens']}; "
        f"daily budget remaining: {counts['daily_budget_remaining']}"
    )
    if input("Type YES to send allowlisted evidence to Groq: ") != "YES":
        print("Execution cancelled.")
        return 1
    summary = generator.execute(args.limit)
    print("Narrative stage summary:")
    for name, value in summary.items():
        print(f"{name.replace('_', ' ').capitalize()}: {value}")
    return 0
