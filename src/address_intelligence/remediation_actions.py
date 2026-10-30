"""Human-gated exception triage, address suggestions, and Precisely verification."""

import csv
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
import time
from typing import Any, Callable

import requests

from address_intelligence.config import Settings
from address_intelligence.credits import CreditLedger
from address_intelligence.narrative import (
    GROQ_CHAT_COMPLETIONS_URL,
    NarrativeGenerator,
    NarrativeAPIError,
    _json_hash,
    _load_daily_usage,
    _token_estimate,
    _write_daily_usage,
)
from address_intelligence.pipeline import _candidate, _geocode_reason
from address_intelligence.providers.precisely import PreciselyClient, ProviderResponse
from address_intelligence.remediation import (
    _safe_groq_error_reason,
    build_model_input,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUN_ROOT = PROJECT_ROOT / "data" / "output" / "run"
RESULTS_PATH = RUN_ROOT / "results_rowlevel.csv"
POLICIES_PATH = PROJECT_ROOT / "data" / "input" / "policyholders_synthetic.csv"
DIAGNOSIS_PATH = RUN_ROOT / "remediation_diagnosis.jsonl"
GROUND_TRUTH_PATH = PROJECT_ROOT / "data" / "input" / "ground_truth.csv"
OUTPUT_PATH = RUN_ROOT / "remediation_actions.jsonl"
SUGGESTION_CACHE_ROOT = RUN_ROOT / "cache" / "remediation_suggestions"
GEOCODE_CACHE_ROOT = RUN_ROOT / "cache" / "remediation_geocode_premium"
DAILY_USAGE_PATH = RUN_ROOT / "cache" / "narratives" / "daily_usage.json"
REPORT_MD_PATH = PROJECT_ROOT / "reports" / "remediation_actions.md"
REPORT_CSV_PATH = PROJECT_ROOT / "reports" / "remediation_actions.csv"
PROMPT_VERSION = "address-remediation-suggestion-v1"
MAX_COMPLETION_TOKENS = 300
SUGGESTION_FIELDS = frozenset({"street", "city", "zip5"})
CAUSE_TO_FIELD = {
    "street_name_issue": "street",
    "city_issue": "city",
    "zip_issue": "zip5",
}
GROUND_TRUTH_CAUSES = {
    "street_typo": "street_name_issue",
    "city_typo": "city_issue",
    "missing_zip": "zip_issue",
    "wrong_zip": "zip_issue",
    "missing_house_number": "house_number_issue",
    "nonexistent_address": "house_number_issue",
}
REPORT_COLUMNS = ("metric", "value")
_ZIP_PATTERN = re.compile(r"^\d{5}$")
_LEADING_HOUSE_NUMBER = re.compile(r"^\s*(\d+[A-Za-z]?)\b")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        return list(csv.DictReader(source))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as destination:
        for row in rows:
            destination.write(
                json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n"
            )


def _truth_labels(raw: str) -> set[str]:
    labels = set()
    for label in re.split(r"[,;|]", raw):
        label = label.strip()
        if label and label != "missing_unit":
            try:
                labels.add(GROUND_TRUTH_CAUSES[label])
            except KeyError:
                raise ValueError(f"Unknown ground-truth defect label: {label}") from None
    return labels


def _normalize_street(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _normalize_city(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _submitted_house_number(line1: str) -> str | None:
    match = _LEADING_HOUSE_NUMBER.match(line1)
    return match.group(1).casefold() if match else None


def triage_action(prediction_set: list[str]) -> str:
    if set(prediction_set) == {"house_number_issue"}:
        return "customer_outreach"
    return "analyst_review"


def _suggestion_input(
    policy: dict[str, str],
    shortlist: list[str],
    diagnosis_input: dict[str, Any],
) -> dict[str, Any]:
    return {
        "submitted_address": {
            "line1": policy.get("raw_address_line1", ""),
            "line2": policy.get("raw_address_line2", ""),
            "city": policy.get("raw_city", ""),
            "state": policy.get("raw_state", ""),
            "zip5": policy.get("raw_zip5", ""),
        },
        "cause_shortlist": shortlist,
        "submitted_features": diagnosis_input.get("submitted_features", {}),
        "match_signals": diagnosis_input.get("match_signals", {}),
    }


def _request_messages(model_input: dict[str, Any]) -> list[dict[str, str]]:
    system = (
        "You propose a possible correction to a synthetic address for analyst "
        "review. Use only submitted address fields, cause shortlist, and match "
        "signals. Do not change state or add/change a house number. Return a "
        "strict JSON object with line1, city, zip5, changed_fields, rationale. "
        "Label the proposal suggested, unverified outside the JSON."
    )
    user = (
        f"Prompt version: {PROMPT_VERSION}\n"
        f"Input: {json.dumps(model_input, sort_keys=True, ensure_ascii=True)}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def validate_suggestion(
    content: str, policy: dict[str, str], shortlist: list[str]
) -> dict[str, Any]:
    try:
        suggestion = json.loads(content)
    except json.JSONDecodeError:
        raise ValueError("invalid JSON") from None
    required = {"line1", "city", "zip5", "changed_fields", "rationale"}
    if not isinstance(suggestion, dict) or set(suggestion) != required:
        raise ValueError("invalid response schema")
    if any(
        not isinstance(suggestion[field], str)
        for field in ("line1", "city", "zip5", "rationale")
    ):
        raise ValueError("response fields must be strings")
    changed = suggestion["changed_fields"]
    if not isinstance(changed, list) or not all(
        isinstance(field, str) for field in changed
    ):
        raise ValueError("changed_fields must be a string array")
    if not set(changed).issubset(SUGGESTION_FIELDS) or len(changed) != len(set(changed)):
        raise ValueError("invalid changed_fields")
    if len(suggestion["rationale"]) > 200:
        raise ValueError("rationale exceeds 200 characters")
    if not _ZIP_PATTERN.fullmatch(suggestion["zip5"]):
        raise ValueError("zip5 must contain exactly five digits")

    original = {
        "street": policy.get("raw_address_line1", ""),
        "city": policy.get("raw_city", ""),
        "zip5": policy.get("raw_zip5", ""),
    }
    proposed = {
        "street": suggestion["line1"],
        "city": suggestion["city"],
        "zip5": suggestion["zip5"],
    }
    actual_changes = {
        field for field in SUGGESTION_FIELDS if proposed[field] != original[field]
    }
    if actual_changes != set(changed):
        raise ValueError("changed_fields do not match changed address fields")
    available_causes = {cause for cause in shortlist if cause in CAUSE_TO_FIELD}
    allowed_changes = {CAUSE_TO_FIELD[cause] for cause in available_causes}
    if not actual_changes.issubset(allowed_changes):
        raise ValueError("changed field is not supported by the cause shortlist")

    submitted_number = _submitted_house_number(original["street"])
    proposed_number = _submitted_house_number(suggestion["line1"])
    if proposed_number != submitted_number:
        raise ValueError("house number cannot be added or changed")
    if submitted_number is None and proposed_number is not None:
        raise ValueError("house number cannot be added or changed")
    return suggestion


def _read_error(response: Any, payload: dict[str, Any], api_key: str) -> str:
    reason = _safe_groq_error_reason(response, payload, api_key)
    return reason


class ExceptionActionWorkflow:
    """Dry-run-first action workflow with separately gated Groq and Precisely stages."""

    def __init__(
        self,
        settings: Settings,
        ledger: CreditLedger,
        *,
        client: PreciselyClient | None = None,
        results_path: Path = RESULTS_PATH,
        policies_path: Path = POLICIES_PATH,
        diagnosis_path: Path = DIAGNOSIS_PATH,
        ground_truth_path: Path = GROUND_TRUTH_PATH,
        output_path: Path = OUTPUT_PATH,
        suggestion_cache_root: Path = SUGGESTION_CACHE_ROOT,
        geocode_cache_root: Path = GEOCODE_CACHE_ROOT,
        daily_usage_path: Path = DAILY_USAGE_PATH,
        report_md_path: Path = REPORT_MD_PATH,
        report_csv_path: Path = REPORT_CSV_PATH,
        post: Callable[..., Any] = requests.post,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.settings = settings
        self.ledger = ledger
        self.client = client
        self.results_path = results_path
        self.policies_path = policies_path
        self.diagnosis_path = diagnosis_path
        self.ground_truth_path = ground_truth_path
        self.output_path = output_path
        self.suggestion_cache_root = suggestion_cache_root
        self.geocode_cache_root = geocode_cache_root
        self.daily_usage_path = daily_usage_path
        self.report_md_path = report_md_path
        self.report_csv_path = report_csv_path
        self.post = post
        self.clock = clock
        self.sleeper = sleeper
        self._pacing = NarrativeGenerator(
            settings,
            cache_root=suggestion_cache_root,
            daily_usage_path=daily_usage_path,
            post=post,
            clock=clock,
            sleeper=sleeper,
        )

    def _records(self) -> list[dict[str, Any]]:
        policies = {
            row["policy_id"]: row for row in _read_csv(self.policies_path)
        }
        diagnosis = {
            row["policy_id"]: row for row in _read_jsonl(self.diagnosis_path)
        }
        results = [
            row
            for row in _read_csv(self.results_path)
            if row.get("eligibility") == "manual_review"
        ]
        records = []
        for result in results:
            policy_id = result["policy_id"]
            policy = policies.get(policy_id)
            diagnostic = diagnosis.get(policy_id)
            if policy is None:
                raise ValueError(f"No submitted address found for {policy_id}.")
            if diagnostic is None:
                raise ValueError(f"No remediation diagnosis found for {policy_id}.")
            shortlist = sorted(set(diagnostic.get("prediction_set", [])))
            action = triage_action(shortlist)
            records.append(
                {
                    "policy_id": policy_id,
                    "result": result,
                    "policy": policy,
                    "diagnosis": diagnostic,
                    "cause_shortlist": shortlist,
                    "action": action,
                    "model_input": _suggestion_input(
                        policy,
                        shortlist,
                        build_model_input(policy, result.get("precision_code", "")),
                    ),
                }
            )
        return records

    def _suggestion_cache_path(self, record: dict[str, Any]) -> Path:
        key = _json_hash(
            {
                "prompt_version": PROMPT_VERSION,
                "model": self.settings.remediation_model,
                "input": record["model_input"],
            }
        )
        return self.suggestion_cache_root / f"{record['policy_id']}_{key}.json"

    @staticmethod
    def _has_plausible_change(record: dict[str, Any]) -> bool:
        return any(
            cause in CAUSE_TO_FIELD for cause in record["cause_shortlist"]
        )

    def dry_run(self, stage: str, limit: int | None = None) -> dict[str, Any]:
        if stage not in ("suggest", "verify"):
            raise ValueError("stage must be suggest or verify.")
        records = self._records()
        if limit is not None:
            records = records[:limit]
        outreach = sum(record["action"] == "customer_outreach" for record in records)
        analyst = len(records) - outreach
        today = datetime.now(timezone.utc).date().isoformat()
        tokens_used = _load_daily_usage(
            self.daily_usage_path,
            today,
            self.settings.remediation_model,
            legacy_model=self.settings.groq_model,
        )
        groq_calls = 0
        estimated_tokens = 0
        verify_calls = 0
        for record in records:
            if record["action"] != "analyst_review":
                continue
            if not self._has_plausible_change(record):
                continue
            if stage == "suggest":
                if self._suggestion_cache_path(record).exists():
                    continue
                estimate = _token_estimate(_request_messages(record["model_input"]))
                groq_calls += 1
                estimated_tokens += estimate + MAX_COMPLETION_TOKENS
            else:
                verify_calls += 1
        missing_suggest_records = False
        if stage == "verify" and self.output_path.exists():
            action_by_id = {
                row["policy_id"]: row for row in _read_jsonl(self.output_path)
            }
            missing_suggest_records = any(
                record["policy_id"] not in action_by_id for record in records
            )
            verify_calls = sum(
                bool(action_by_id.get(record["policy_id"], {}).get("suggestion"))
                and action_by_id[record["policy_id"]].get("suggestion_status")
                == "suggested, unverified"
                for record in records
            )
        credits = self.ledger.check("geocode_premium", verify_calls) if verify_calls else Decimal("0")
        summary = {
            "stage": stage,
            "run_mode": "dry-run planning only",
            "records": len(records),
            "customer_outreach": outreach,
            "analyst_review": analyst,
            "groq_calls": 0,
            "planned_groq_calls": groq_calls,
            "estimated_tokens": estimated_tokens,
            "model": self.settings.remediation_model,
            "tokens_used_today": tokens_used,
            "model_daily_budget_remaining": max(
                0, self.settings.groq_daily_token_budget - tokens_used
            ),
            "precisely_calls": 0,
            "planned_precisely_calls": verify_calls,
            "planned_precisely_credits": credits,
            "ledger_spent": self.ledger.cumulative_spent,
            "ledger_remaining": self.ledger.remaining,
            "account_credits_left": (
                self.settings.trial_credits - self.ledger.cumulative_spent
            ),
            "suggest_stage_required": stage == "verify"
            and (not self.output_path.exists() or missing_suggest_records),
        }
        summary["counts"] = {
            "customer_outreach": outreach,
            "analyst_review": analyst,
            (
                "suggestion_pending"
                if stage == "suggest"
                else "verification_pending"
            ): groq_calls if stage == "suggest" else verify_calls,
        }
        summary.update(
            {
                "suggestions_made": 0,
                "suggestions_valid": 0,
                "suggestions_rejected": 0,
                "verified": 0,
                "verified_correct": 0,
                "verified_incorrect": 0,
                "not_verified": 0,
                "no_suggestion": 0,
                "customer_outreach_correct": 0,
                "tokens_used": 0,
                "credits_used": "0",
            }
        )
        self._write_reports(summary)
        return summary

    def suggest(self, limit: int | None = None) -> dict[str, Any]:
        if not self.settings.groq_api_key:
            raise ValueError("GROQ_API_KEY is required for suggestion execution.")
        records = self._records()
        if limit is not None:
            records = records[:limit]
        today = datetime.now(timezone.utc).date().isoformat()
        tokens_used = _load_daily_usage(
            self.daily_usage_path,
            today,
            self.settings.remediation_model,
            legacy_model=self.settings.groq_model,
        )
        tokens_before = tokens_used
        prior = {
            row["policy_id"]: row for row in _read_jsonl(self.output_path)
        }
        actions = []
        calls = 0
        for record in records:
            existing = prior.get(record["policy_id"], {})
            action_row = {
                "policy_id": record["policy_id"],
                "action": record["action"],
                "cause_shortlist": record["cause_shortlist"],
                "work_item": "",
                "suggestion": None,
                "suggestion_status": "not_requested",
                "suggestion_reason": "",
                "model": self.settings.remediation_model,
                "model_reported": "",
                "verification_status": "not_run",
                "evaluation": {},
            }
            if record["action"] == "customer_outreach":
                action_row["work_item"] = (
                    "Request house number confirmation from the policyholder; "
                    "resubmit for verification."
                )
                action_row["evaluation"] = self._evaluate_outreach(record)
            elif existing.get("suggestion"):
                action_row.update(existing)
            elif not self._has_plausible_change(record):
                action_row["suggestion_status"] = "no_suggestion"
            else:
                try:
                    response_data, provenance = self._suggestion_response(
                        record, tokens_used
                    )
                except DailyBudgetReached:
                    action_row["suggestion_status"] = "failed"
                    action_row["suggestion_reason"] = "daily token budget reached"
                except NarrativeAPIError as error:
                    action_row["suggestion_status"] = "failed"
                    action_row["suggestion_reason"] = error.reason
                else:
                    if provenance == "api":
                        tokens_used += response_data["tokens"]
                        _write_daily_usage(
                            self.daily_usage_path,
                            today,
                            self.settings.remediation_model,
                            tokens_used,
                            legacy_model=self.settings.groq_model,
                        )
                        calls += 1
                    action_row["model_reported"] = response_data["model_reported"]
                    try:
                        suggestion = validate_suggestion(
                            response_data["content"],
                            record["policy"],
                            record["cause_shortlist"],
                        )
                    except ValueError as error:
                        action_row["suggestion_status"] = "rejected"
                        action_row["suggestion_reason"] = str(error)
                    else:
                        action_row["suggestion"] = suggestion
                        action_row["suggestion_status"] = "suggested, unverified"
                        action_row["suggestion_label"] = "suggested, unverified"
            actions.append(action_row)

        action_by_id = dict(prior)
        action_by_id.update({row["policy_id"]: row for row in actions})
        merged_actions = list(action_by_id.values())
        _write_jsonl(self.output_path, merged_actions)
        summary = self._aggregate(merged_actions)
        summary.update(
            {
                "run_mode": "suggest execution",
                "stage": "suggest",
                "groq_calls": calls,
                "tokens_used": tokens_used - tokens_before,
            }
        )
        self._write_reports(summary)
        return summary

    def _suggestion_response(
        self, record: dict[str, Any], tokens_used: int
    ) -> tuple[dict[str, Any], str]:
        cache_path = self._suggestion_cache_path(record)
        if cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8")), "cached"
        messages = _request_messages(record["model_input"])
        estimate = _token_estimate(messages) + MAX_COMPLETION_TOKENS
        if estimate > self.settings.groq_daily_token_budget - tokens_used:
            raise DailyBudgetReached
        payload = {
            "model": self.settings.remediation_model,
            "messages": messages,
            "temperature": 0,
            "max_completion_tokens": MAX_COMPLETION_TOKENS,
            "reasoning_effort": "low",
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "address_suggestion",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "line1": {"type": "string"},
                            "city": {"type": "string"},
                            "zip5": {"type": "string"},
                            "changed_fields": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "enum": ["street", "city", "zip5"],
                                },
                            },
                            "rationale": {"type": "string"},
                        },
                        "required": [
                            "line1",
                            "city",
                            "zip5",
                            "changed_fields",
                            "rationale",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
        }
        headers = {
            "Authorization": f"Bearer {self.settings.groq_api_key}",
            "Content-Type": "application/json",
        }
        headers["Authorization"] = "Bearer " + str(self.settings.groq_api_key)
        pacing = self._pacing
        for attempt in range(2):
            pacing._wait_for_capacity(estimate)
            pacing._request_times.append(self.clock())
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
            reason = _read_error(response, payload, self.settings.groq_api_key)
            if attempt == 0 and (
                response.status_code == 429 or response.status_code >= 500
            ):
                self.sleeper(
                    pacing._retry_after_seconds(response.headers.get("Retry-After"))
                )
                continue
            raise NarrativeAPIError(reason)
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            model_reported = body["model"]
            tokens = int(body["usage"]["total_tokens"])
        except (ValueError, KeyError, TypeError, IndexError):
            raise NarrativeAPIError("invalid_response") from None
        if not isinstance(content, str) or not isinstance(model_reported, str):
            raise NarrativeAPIError("invalid_response")
        cached = {
            "content": content,
            "model_reported": model_reported,
            "tokens": tokens,
        }
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cached) + "\n", encoding="utf-8")
        pacing._token_usage.append((self.clock(), tokens))
        return cached, "api"

    def _verify_cache_path(self, policy_id: str) -> Path:
        return self.geocode_cache_root / f"{policy_id}.json"

    @staticmethod
    def _verification_params(
        record: dict[str, Any], suggestion: dict[str, Any]
    ) -> dict[str, str]:
        policy = record["policy"]
        main_address = " ".join(
            part.strip()
            for part in (suggestion["line1"], policy.get("raw_address_line2", ""))
            if part.strip()
        )
        return {
            "mainAddress": main_address,
            "areaName3": suggestion["city"],
            "areaName1": policy["raw_state"],
            "postalCode": suggestion["zip5"],
            "country": "USA",
        }

    def _verify_request_hash(
        self, params: dict[str, str]
    ) -> str:
        return _json_hash(
            {
                "operation": "remediation_geocode_premium",
                "mode": "structured",
                "params": params,
            }
        )

    def _read_verify_cache(
        self, record: dict[str, Any], suggestion: dict[str, Any]
    ) -> ProviderResponse | None:
        path = self._verify_cache_path(record["policy_id"])
        if not path.exists():
            return None
        stored = json.loads(path.read_text(encoding="utf-8"))
        params = self._verification_params(record, suggestion)
        if stored.get("request_hash") != self._verify_request_hash(params):
            return None
        return ProviderResponse(
            status_code=stored["status_code"],
            data=stored.get("data"),
            error=stored.get("error"),
            credits_recorded=Decimal("0"),
        )

    @staticmethod
    def _verified(response: ProviderResponse, expected_state: str) -> bool:
        if response.status_code != 200:
            return False
        eligibility, _ = _geocode_reason(response, expected_state)
        return eligibility == "eligible"

    def verify(self, limit: int | None = None) -> dict[str, Any]:
        if not self.output_path.exists():
            raise ValueError("Stage suggest must complete before stage verify.")
        if self.client is None:
            raise ValueError("A PreciselyClient is required for verification.")
        records = self._records()
        if limit is not None:
            records = records[:limit]
        actions = {
            row["policy_id"]: row for row in _read_jsonl(self.output_path)
        }
        if any(record["policy_id"] not in actions for record in records):
            raise ValueError("Stage suggest must complete before stage verify.")
        verified_calls = 0
        credits_used = Decimal("0")
        for record in records:
            action = actions.get(record["policy_id"])
            if not action or action.get("suggestion_status") != "suggested, unverified":
                if action:
                    action["verification_status"] = (
                        "no_suggestion"
                        if action.get("suggestion_status") == "no_suggestion"
                        else "not_applicable"
                    )
                    action["evaluation"] = self._evaluate_action(
                        record, action, None
                    )
                continue
            suggestion = action["suggestion"]
            cache = self._read_verify_cache(record, suggestion)
            if cache is not None:
                response = cache
                provenance = "cached"
            else:
                self.ledger.check("geocode_premium", 1)
                response = self.client.geocode(
                    self._verification_params(record, suggestion), "premium"
                )
                verified_calls += 1
                credits_used += response.credits_recorded
                provenance = "api"
                if response.status_code == 200:
                    path = self._verify_cache_path(record["policy_id"])
                    path.parent.mkdir(parents=True, exist_ok=True)
                    params = self._verification_params(record, suggestion)
                    path.write_text(
                        json.dumps(
                            {
                                "request_hash": self._verify_request_hash(params),
                                "status_code": response.status_code,
                                "data": response.data,
                                "error": response.error,
                            },
                            sort_keys=True,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
            is_verified = self._verified(response, record["policy"]["raw_state"])
            action["verification_status"] = (
                "verified by Precisely" if is_verified else "did not verify"
            )
            action["verification_provenance"] = provenance
            action["evaluation"] = self._evaluate_action(
                record, action, response if is_verified else None
            )
        _write_jsonl(self.output_path, list(actions.values()))
        summary = self._aggregate(list(actions.values()))
        summary["run_mode"] = "verification execution"
        summary["stage"] = "verify"
        summary["precisely_calls"] = verified_calls
        summary["credits_used"] = str(credits_used)
        self._write_reports(summary)
        return summary

    def _evaluate_outreach(self, record: dict[str, Any]) -> dict[str, Any]:
        truth = self._ground_truth().get(record["policy_id"], set())
        return {
            "customer_outreach_correct": "house_number_issue" in truth,
        }

    def _ground_truth(self) -> dict[str, set[str]]:
        return {
            row["policy_id"]: _truth_labels(row.get("defects", ""))
            for row in _read_csv(self.ground_truth_path)
        }

    def _evaluate_action(
        self,
        record: dict[str, Any],
        action: dict[str, Any],
        response: ProviderResponse | None,
    ) -> dict[str, Any]:
        truth = self._ground_truth().get(record["policy_id"], set())
        if action["action"] == "customer_outreach":
            return {"customer_outreach_correct": "house_number_issue" in truth}
        if action.get("suggestion_status") == "no_suggestion":
            return {"no_suggestion": True}
        if action.get("suggestion_status") != "suggested, unverified":
            return {}
        if response is None:
            return {"not_verified": True}
        candidate = _candidate(response.data)
        address = candidate.get("address", {}) if candidate else {}
        line1 = (
            address.get("mainAddressLine")
            if isinstance(address, dict)
            else None
        ) or (candidate or {}).get("formattedStreetAddress", "")
        city = address.get("areaName3", "") if isinstance(address, dict) else ""
        zip5 = address.get("postCode1", "") if isinstance(address, dict) else ""
        clean = self._ground_truth_row(record["policy_id"])
        correct = (
            _normalize_street(str(line1))
            == _normalize_street(clean.get("clean_address_line1", ""))
            and _normalize_city(str(city))
            == _normalize_city(clean.get("clean_city", ""))
            and str(zip5).strip() == clean.get("clean_zip5", "").strip()
        )
        return {
            "verified_correct": correct,
            "verified_incorrect": not correct,
        }

    def _ground_truth_row(self, policy_id: str) -> dict[str, str]:
        for row in _read_csv(self.ground_truth_path):
            if row["policy_id"] == policy_id:
                return row
        raise ValueError(f"No ground truth found for {policy_id}.")

    @staticmethod
    def _aggregate(actions: list[dict[str, Any]]) -> dict[str, Any]:
        counts: dict[str, int] = {}
        rejection_reasons: dict[str, int] = {}
        for row in actions:
            counts[row["action"]] = counts.get(row["action"], 0) + 1
            status = row.get("suggestion_status", "")
            counts[f"suggestion_{status}"] = counts.get(
                f"suggestion_{status}", 0
            ) + 1
            if status == "rejected":
                reason = row.get("suggestion_reason", "unknown")
                rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
        eval_counts = {
            key: sum(
                item.get("evaluation", {}).get(key) is True for item in actions
            )
            for key in (
                "verified_correct",
                "verified_incorrect",
                "not_verified",
                "no_suggestion",
                "customer_outreach_correct",
            )
        }
        suggestions_made = sum(
            row.get("suggestion_status") in ("suggested, unverified", "rejected")
            for row in actions
        )
        suggestions_valid = sum(
            row.get("suggestion_status") == "suggested, unverified"
            for row in actions
        )
        verified = sum(
            row.get("verification_status") == "verified by Precisely"
            for row in actions
        )
        return {
            "records": len(actions),
            "counts": counts,
            "rejection_reasons": rejection_reasons,
            "suggestions_made": suggestions_made,
            "suggestions_valid": suggestions_valid,
            "suggestions_rejected": sum(rejection_reasons.values()),
            "verified": verified,
            **eval_counts,
        }

    def _write_reports(self, summary: dict[str, Any]) -> None:
        rows: list[tuple[str, Any]] = [
            ("run_status", summary.get("run_mode", "execution")),
            ("stage", summary.get("stage", "")),
            ("records", summary.get("records", 0)),
            ("suggestions_made", summary.get("suggestions_made", 0)),
            ("suggestions_valid", summary.get("suggestions_valid", 0)),
            ("suggestions_rejected", summary.get("suggestions_rejected", 0)),
            ("verified", summary.get("verified", 0)),
            ("verified_correct", summary.get("verified_correct", 0)),
            ("verified_incorrect", summary.get("verified_incorrect", 0)),
            ("not_verified", summary.get("not_verified", 0)),
            ("no_suggestion", summary.get("no_suggestion", 0)),
            ("customer_outreach_correct", summary.get("customer_outreach_correct", 0)),
            ("credits_used", summary.get("credits_used", "0")),
            ("tokens_used", summary.get("tokens_used", 0)),
            ("groq_calls", summary.get("groq_calls", 0)),
            ("precisely_calls", summary.get("precisely_calls", 0)),
            ("estimated_tokens", summary.get("estimated_tokens", 0)),
            ("planned_groq_calls", summary.get("planned_groq_calls", 0)),
            ("planned_precisely_calls", summary.get("planned_precisely_calls", 0)),
            ("planned_precisely_credits", summary.get("planned_precisely_credits", 0)),
        ]
        rows.extend(
            (f"action_{key}", value)
            for key, value in summary.get("counts", {}).items()
        )
        rows.extend(
            (f"rejected_{reason}", count)
            for reason, count in summary.get("rejection_reasons", {}).items()
        )
        self.report_csv_path.parent.mkdir(parents=True, exist_ok=True)
        with self.report_csv_path.open(
            "w", newline="", encoding="utf-8"
        ) as destination:
            writer = csv.writer(destination)
            writer.writerow(REPORT_COLUMNS)
            writer.writerows(rows)
        lines = [
            "# Exception remediation actions",
            "",
            "Aggregate counts only; no addresses, policy identifiers, or Precisely "
            "response values are included.",
            "",
            f"- Run status: {summary.get('run_mode', 'execution')}",
            f"- Stage: {summary.get('stage', 'all')}",
            f"- Records: {summary.get('records', 0)}",
            f"- Suggestions made: {summary.get('suggestions_made', 0)}",
            f"- Suggestions valid: {summary.get('suggestions_valid', 0)}",
            f"- Suggestions rejected: {summary.get('suggestions_rejected', 0)}",
            f"- Verified by Precisely: {summary.get('verified', 0)}",
            f"- Customer outreach correct by ground truth: "
            f"{summary.get('customer_outreach_correct', 0)}",
            f"- Verified suggestions matching ground truth: "
            f"{summary.get('verified_correct', 0)}",
            f"- Verified but incorrect suggestions: "
            f"{summary.get('verified_incorrect', 0)}",
            f"- Not verified: {summary.get('not_verified', 0)}",
            f"- No suggestion: {summary.get('no_suggestion', 0)}",
            f"- Groq calls: {summary.get('groq_calls', 0)}",
            f"- Tokens used: {summary.get('tokens_used', 0)}",
            f"- Precisely calls: {summary.get('precisely_calls', 0)}",
            f"- Credits used: {summary.get('credits_used', '0')}",
            f"- Planned Groq calls: {summary.get('planned_groq_calls', 0)}",
            f"- Estimated tokens: {summary.get('estimated_tokens', 0)}",
            f"- Planned Precisely calls: "
            f"{summary.get('planned_precisely_calls', 0)}",
            f"- Planned Precisely credits: "
            f"{summary.get('planned_precisely_credits', 0)}",
            "",
            "## Counts",
            "",
        ]
        lines.extend(
            f"- {name}: {count}"
            for name, count in sorted(summary.get("counts", {}).items())
        )
        lines.extend(
            f"- Rejected ({reason}): {count}"
            for reason, count in sorted(
                summary.get("rejection_reasons", {}).items()
            )
        )
        self.report_md_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class DailyBudgetReached(Exception):
    """Internal stop signal when the per-model daily budget is exhausted."""


def run_remediation_cli(argv: list[str] | None = None) -> int:
    import argparse

    from address_intelligence.config import Settings

    parser = argparse.ArgumentParser(
        description="Triage and verify address remediation actions."
    )
    parser.add_argument("--stage", choices=("suggest", "verify"), required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    settings = Settings.from_env()
    ledger = CreditLedger(settings.credit_hard_cap)
    client = PreciselyClient(settings, ledger, min_request_interval=2.0)
    workflow = ExceptionActionWorkflow(settings, ledger, client=client)
    counts = workflow.dry_run(args.stage, args.limit)
    print(f"Mode: {'EXECUTE' if args.execute else 'DRY RUN'}; stage: {args.stage}")
    print(
        f"Records: {counts['records']}; customer outreach: "
        f"{counts['customer_outreach']}; analyst review: "
        f"{counts['analyst_review']}"
    )
    print(
        f"Planned Groq calls: {counts['planned_groq_calls']} using "
        f"{counts['model']}; estimated tokens: {counts['estimated_tokens']}; "
        f"model budget remaining: {counts['model_daily_budget_remaining']}"
    )
    print(
        f"Planned Precisely calls: {counts['planned_precisely_calls']}; "
        f"credits: {counts['planned_precisely_credits']}; ledger remaining: "
        f"{counts['ledger_remaining']}; account credits left: "
        f"{counts['account_credits_left']}"
    )
    if not args.execute:
        if counts["suggest_stage_required"]:
            print("Verification execution requires the suggest stage to complete first.")
        return 0
    if args.stage == "verify" and counts["suggest_stage_required"]:
        raise ValueError("Stage suggest must complete before stage verify.")
    if input("Type YES to execute this remediation stage: ") != "YES":
        print("Execution cancelled.")
        return 1
    if args.stage == "suggest":
        summary = workflow.suggest(args.limit)
    else:
        summary = workflow.verify(args.limit)
    print("Stage summary:")
    for name, value in summary.items():
        print(f"{name.replace('_', ' ').capitalize()}: {value}")
    return 0
