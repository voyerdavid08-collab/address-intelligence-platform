"""Cause diagnosis and split-conformal calibration for address exceptions."""

import csv
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import re
import time
from typing import Any, Callable
import warnings

import requests

from address_intelligence.config import Settings
from address_intelligence.narrative import (
    CACHE_ROOT as NARRATIVE_CACHE_ROOT,
    DAILY_USAGE_PATH as NARRATIVE_DAILY_USAGE_PATH,
    GROQ_CHAT_COMPLETIONS_URL,
    NarrativeAPIError,
    NarrativeGenerator,
    _json_hash,
    _load_daily_usage,
    _token_estimate,
    _write_daily_usage,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUN_ROOT = PROJECT_ROOT / "data" / "output" / "run"
POLICIES_PATH = PROJECT_ROOT / "data" / "input" / "policyholders_synthetic.csv"
RESULTS_PATH = RUN_ROOT / "results_rowlevel.csv"
GROUND_TRUTH_PATH = PROJECT_ROOT / "data" / "input" / "ground_truth.csv"
GEOCODE_CACHE_ROOT = RUN_ROOT / "cache" / "geocode_premium_structured"
REMEDIATION_OUTPUT_PATH = RUN_ROOT / "remediation_diagnosis.jsonl"
REMEDIATION_CACHE_ROOT = RUN_ROOT / "cache" / "remediation"
REPORT_MD_PATH = PROJECT_ROOT / "reports" / "remediation_calibration.md"
REPORT_CSV_PATH = PROJECT_ROOT / "reports" / "remediation_calibration.csv"
PROMPT_VERSION = "address-cause-diagnosis-v4"
RANDOM_SEED = 42
MAX_COMPLETION_TOKENS = 300

CAUSE_LABELS = (
    "street_name_issue",
    "city_issue",
    "zip_issue",
    "house_number_issue",
)
GROUND_TRUTH_CAUSES = {
    "street_typo": "street_name_issue",
    "city_typo": "city_issue",
    "missing_zip": "zip_issue",
    "wrong_zip": "zip_issue",
    "missing_house_number": "house_number_issue",
    "nonexistent_address": "house_number_issue",
}
UNDETECTABLE_CAUSE = "missing_unit"
COMPONENT_POSITIONS = {
    "house_number": 0,
    "street_name": 2,
    "street_type": 3,
    "city": 5,
    "zip": 6,
}
PRECISION_CATEGORIES = {
    "address_point": "address point",
    "street_address": "street address",
    "street_centroid": "street centroid",
    "postal_or_city_level": "postal or city level",
    "no_match": "no match",
}
REPORT_COLUMNS = ("metric", "value")
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9_.-]{1,80}")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        return list(csv.DictReader(source))


def map_ground_truth_causes(value: str) -> set[str]:
    """Map raw defect labels to diagnosis labels; missing_unit is not observable."""
    causes = set()
    for raw_label in re.split(r"[,;|]", value):
        label = raw_label.strip()
        if not label or label == UNDETECTABLE_CAUSE:
            continue
        try:
            causes.add(GROUND_TRUTH_CAUSES[label])
        except KeyError:
            raise ValueError(f"Unknown ground-truth defect label: {label}") from None
    return causes


def precision_category(precision_code: str | None) -> str:
    code = (precision_code or "").strip().upper()
    if code.startswith("S8"):
        return PRECISION_CATEGORIES["address_point"]
    if code.startswith(("S5", "S7")):
        return PRECISION_CATEGORIES["street_address"]
    if code.startswith("S4"):
        return PRECISION_CATEGORIES["street_centroid"]
    if code.startswith(("Z", "G")):
        return PRECISION_CATEGORIES["postal_or_city_level"]
    return PRECISION_CATEGORIES["no_match"]


def parse_precision_match_flags(
    precision_code: str | None,
) -> dict[str, bool | str]:
    """Read component matches from the eight positional suffix characters."""
    code = (precision_code or "").strip().upper()
    suffix = code[2:] if re.match(r"^S\d", code) else ""
    if len(suffix) != 8:
        return {name: "unknown" for name in COMPONENT_POSITIONS}
    return {
        name: suffix[position] != "-"
        for name, position in COMPONENT_POSITIONS.items()
    }


def build_model_input(
    policy: dict[str, str], precision_code: str | None
) -> dict[str, Any]:
    """Construct the strict submitted-address and derived-match allowlist."""
    submitted_line1 = policy.get("raw_address_line1", "").strip()
    submitted_zip5 = policy.get("raw_zip5", "").strip()
    return {
        "submitted_address": {
            "line1": submitted_line1,
            "line2": policy.get("raw_address_line2", ""),
            "city": policy.get("raw_city", ""),
            "state": policy.get("raw_state", ""),
            "zip5": submitted_zip5,
        },
        "submitted_features": {
            "house_number_present": bool(re.match(r"^\s*\d+\b", submitted_line1)),
            "zip_present": bool(submitted_zip5),
        },
        "match_signals": {
            "precision_category": precision_category(precision_code),
            "component_matches": parse_precision_match_flags(precision_code),
        },
    }


def stratified_split(
    records: list[dict[str, Any]], seed: int = RANDOM_SEED
) -> tuple[set[str], set[str]]:
    """Split by exact multilabel combination, balancing each stratum deterministically."""
    strata: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        strata[tuple(sorted(record["true_causes"]))].append(record)
    rng = random.Random(seed)
    calibration: set[str] = set()
    test: set[str] = set()
    for label_set in sorted(strata):
        group = sorted(strata[label_set], key=lambda record: record["policy_id"])
        rng.shuffle(group)
        for record in group:
            destination = calibration if len(calibration) <= len(test) else test
            destination.add(record["policy_id"])
    return calibration, test


def conformal_quantile(scores: list[float], alpha: float) -> float:
    """Compute ceil((n+1)(1-alpha))/n's finite-sample upper empirical quantile."""
    if not scores:
        raise ValueError("At least one calibration score is required.")
    if not 0 < alpha < 1:
        raise ValueError("alpha must be between 0 and 1.")
    rank = math.ceil((len(scores) + 1) * (1 - alpha))
    if rank > len(scores):
        return 1.0
    return sorted(scores)[rank - 1]


def build_prediction_set(
    frequencies: dict[str, int], samples: int, quantile: float
) -> list[str]:
    if samples <= 0:
        raise ValueError("samples must be positive.")
    return [
        cause
        for cause in CAUSE_LABELS
        if 1 - frequencies.get(cause, 0) / samples <= quantile
    ]


def calculate_test_metrics(
    records: list[dict[str, Any]], test_ids: set[str]
) -> dict[str, float | int]:
    test_records = [record for record in records if record["policy_id"] in test_ids]
    if not test_records:
        return {
            "test_records": 0,
            "contains_all_true_labels": 0.0,
            "contains_at_least_one_true_label": 0.0,
            "mean_prediction_set_size": 0.0,
            "singleton_share": 0.0,
        }
    contains_all = [
        record["true_causes"].issubset(record["prediction_set"])
        for record in test_records
    ]
    contains_any = [
        bool(record["true_causes"].intersection(record["prediction_set"]))
        for record in test_records
    ]
    sizes = [len(record["prediction_set"]) for record in test_records]
    return {
        "test_records": len(test_records),
        "contains_all_true_labels": sum(contains_all) / len(test_records),
        "contains_at_least_one_true_label": sum(contains_any) / len(test_records),
        "mean_prediction_set_size": sum(sizes) / len(sizes),
        "singleton_share": sum(size == 1 for size in sizes) / len(sizes),
    }


def top_answer_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare each record's most frequent cause against its set of true causes."""
    per_cause: dict[str, dict[str, int]] = {
        cause: {"correct": 0, "total": 0} for cause in CAUSE_LABELS
    }
    confusion: Counter[tuple[str, str]] = Counter()
    correct_records = 0
    for record in records:
        frequencies = record["frequencies"]
        if not record.get("valid_samples", 0) or not any(frequencies.values()):
            continue
        predicted = max(
            CAUSE_LABELS, key=lambda cause: frequencies.get(cause, 0)
        )
        true_causes = record["true_causes"]
        if predicted in true_causes:
            correct_records += 1
        for true_cause in true_causes:
            per_cause[true_cause]["total"] += 1
            if predicted == true_cause:
                per_cause[true_cause]["correct"] += 1
            confusion[(true_cause, predicted)] += 1

    return {
        "top_answer_correct": correct_records,
        "top_answer_total": len(records),
        "top_answer_accuracy": (
            correct_records / len(records) if records else 0.0
        ),
        "per_cause_accuracy": {
            cause: {
                **counts,
                "accuracy": (
                    counts["correct"] / counts["total"]
                    if counts["total"]
                    else 0.0
                ),
            }
            for cause, counts in per_cause.items()
            if counts["total"]
        },
        "confusion_table": [
            {
                "true_cause": true_cause,
                "predicted_cause": predicted_cause,
                "count": count,
            }
            for (true_cause, predicted_cause), count in sorted(confusion.items())
        ],
    }


def _request_messages(model_input: dict[str, Any]) -> list[dict[str, str]]:
    system = (
        "Classify the likely cause of the submitted synthetic address defect "
        "using only the submitted address and derived match signals. Return "
        "one or two distinct taxonomy labels as strict JSON. Do not output an address, "
        "coordinates, identifiers, explanations, or facts not provided. "
        "Component match flags describe the matched candidate and may show a "
        "house number as matched even when none was submitted; rely on "
        "house_number_present for whether the submitted address included one."
    )
    user = (
        f"Prompt version: {PROMPT_VERSION}\n"
        f"Allowed causes: {', '.join(CAUSE_LABELS)}\n"
        f"Input: {json.dumps(model_input, sort_keys=True, ensure_ascii=True)}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _validate_causes(content: str) -> list[str]:
    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        raise NarrativeAPIError("invalid_json_response") from None
    if not isinstance(result, dict) or set(result) != {"causes"}:
        raise NarrativeAPIError("invalid_response_schema")
    causes = result["causes"]
    if not isinstance(causes, list) or not 1 <= len(causes) <= 2:
        raise NarrativeAPIError("invalid cause count")
    if any(
        not isinstance(cause, str) or cause not in CAUSE_LABELS
        for cause in causes
    ):
        raise NarrativeAPIError("invalid cause label")
    if len(set(causes)) != len(causes):
        raise NarrativeAPIError("invalid cause count")
    return causes


def _safe_groq_error_reason(
    response: Any, payload: dict[str, Any], api_key: str | None
) -> str:
    """Capture a bounded provider message without echoing request data or secrets."""
    identifiers = []
    message = ""
    try:
        body = response.json()
    except (ValueError, TypeError):
        body = {}
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        error = body["error"]
        for key in ("type", "code"):
            value = error.get(key)
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value):
                if value not in identifiers:
                    identifiers.append(value)
        candidate = error.get("message")
        if isinstance(candidate, str):
            message = candidate

    redactions: set[str] = set()

    def collect_strings(value: Any) -> None:
        if isinstance(value, str):
            if value:
                redactions.add(value)
            input_marker = "Input: "
            if input_marker in value:
                try:
                    collect_strings(json.loads(value.split(input_marker, 1)[1]))
                except json.JSONDecodeError:
                    pass
        elif isinstance(value, dict):
            for nested in value.values():
                collect_strings(nested)
        elif isinstance(value, list):
            for nested in value:
                collect_strings(nested)

    collect_strings(payload)
    if api_key:
        redactions.add(api_key)
    for value in sorted(redactions, key=len, reverse=True):
        message = message.replace(value, "[redacted]")
    message = message[:300]
    safe_identifiers = []
    for identifier in identifiers:
        for value in sorted(redactions, key=len, reverse=True):
            identifier = identifier.replace(value, "[redacted]")
        if _SAFE_IDENTIFIER.fullmatch(identifier):
            safe_identifiers.append(identifier)

    reason = f"HTTP {response.status_code}"
    if safe_identifiers:
        reason += " " + "/".join(safe_identifiers)
    if message:
        reason += f": {message}"
    return reason


class RemediationDiagnoser:
    """Dry-run-first cause sampler with split-conformal calibration."""

    def __init__(
        self,
        settings: Settings,
        *,
        policies_path: Path = POLICIES_PATH,
        results_path: Path = RESULTS_PATH,
        ground_truth_path: Path = GROUND_TRUTH_PATH,
        geocode_cache_root: Path = GEOCODE_CACHE_ROOT,
        output_path: Path = REMEDIATION_OUTPUT_PATH,
        cache_root: Path = REMEDIATION_CACHE_ROOT,
        daily_usage_path: Path = NARRATIVE_DAILY_USAGE_PATH,
        report_md_path: Path = REPORT_MD_PATH,
        report_csv_path: Path = REPORT_CSV_PATH,
        post: Callable[..., Any] = requests.post,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ):
        self.settings = settings
        self.policies_path = policies_path
        self.results_path = results_path
        self.ground_truth_path = ground_truth_path
        self.geocode_cache_root = geocode_cache_root
        self.output_path = output_path
        self.cache_root = cache_root
        self.daily_usage_path = daily_usage_path
        self.report_md_path = report_md_path
        self.report_csv_path = report_csv_path
        self.post = post
        self.clock = clock
        self.sleeper = sleeper
        self._api_calls = 0
        self._pacing = NarrativeGenerator(
            settings,
            cache_root=NARRATIVE_CACHE_ROOT,
            daily_usage_path=daily_usage_path,
            post=post,
            clock=clock,
            sleeper=sleeper,
        )

    def records(self) -> tuple[list[dict[str, Any]], int]:
        policies = {
            row["policy_id"]: row for row in _read_csv(self.policies_path)
        }
        results = {row["policy_id"]: row for row in _read_csv(self.results_path)}
        labeled: list[dict[str, Any]] = []
        excluded_missing_unit = 0
        for truth in _read_csv(self.ground_truth_path):
            raw_defects = truth.get("defects", "")
            if not raw_defects.strip():
                continue
            causes = map_ground_truth_causes(raw_defects)
            if not causes:
                excluded_missing_unit += 1
                continue
            policy_id = truth["policy_id"]
            policy = policies.get(policy_id)
            result = results.get(policy_id)
            if policy is None:
                raise ValueError(f"No synthetic policy found for {policy_id}.")
            if result is None:
                raise ValueError(f"No pipeline result found for {policy_id}.")
            response_path = self.geocode_cache_root / f"{policy_id}.json"
            if not response_path.exists():
                raise FileNotFoundError(
                    f"Missing cached Geocode Premium response for {policy_id}."
                )
            response = json.loads(response_path.read_text(encoding="utf-8"))
            candidates = response.get("data", {}).get("candidates", [])
            candidate = candidates[0] if candidates else {}
            precision_code = candidate.get("precisionCode", "")
            labeled.append(
                {
                    "policy_id": policy_id,
                    "model_input": build_model_input(policy, precision_code),
                    "true_causes": causes,
                }
            )
        return labeled, excluded_missing_unit

    def _cache_path(
        self, policy_id: str, model_input: dict[str, Any], sample_index: int
    ) -> Path:
        key = _json_hash(
            {
                "prompt_version": PROMPT_VERSION,
                "model": self.settings.remediation_model,
                "input": model_input,
                "sample_index": sample_index,
            }
        )
        return self.cache_root / f"{policy_id}_{key}.json"

    def dry_run(self, limit: int | None = None) -> dict[str, Any]:
        records, excluded = self.records()
        if limit is not None:
            records = records[:limit]
        calibration, test = stratified_split(records)
        calls = 0
        cached = 0
        estimated_tokens = 0
        for record in records:
            messages = _request_messages(record["model_input"])
            estimate = _token_estimate(messages) + MAX_COMPLETION_TOKENS
            for sample_index in range(self.settings.remediation_samples):
                if self._cache_path(
                    record["policy_id"], record["model_input"], sample_index
                ).exists():
                    cached += 1
                else:
                    calls += 1
                    estimated_tokens += estimate
        today = datetime.now(timezone.utc).date().isoformat()
        tokens_used = _load_daily_usage(
            self.daily_usage_path,
            today,
            self.settings.remediation_model,
            legacy_model=self.settings.groq_model,
        )
        return {
            "labelled_records": len(records),
            "calibration_records": len(calibration),
            "test_records": len(test),
            "excluded_missing_unit_only": excluded,
            "planned_calls": calls,
            "cached_samples": cached,
            "estimated_tokens": estimated_tokens,
            "tokens_used_today": tokens_used,
            "daily_budget_remaining": max(
                0, self.settings.groq_daily_token_budget - tokens_used
            ),
            "budget_sufficient": estimated_tokens
            <= max(0, self.settings.groq_daily_token_budget - tokens_used),
        }

    def execute(self, limit: int | None = None) -> dict[str, Any]:
        if not self.settings.groq_api_key:
            raise ValueError("GROQ_API_KEY is required for --execute.")
        self._api_calls = 0
        records, excluded = self.records()
        if limit is not None:
            records = records[:limit]
        calibration, test = stratified_split(records)
        today = datetime.now(timezone.utc).date().isoformat()
        tokens_used = _load_daily_usage(
            self.daily_usage_path,
            today,
            self.settings.remediation_model,
            legacy_model=self.settings.groq_model,
        )
        sampled_records: list[dict[str, Any]] = []
        stopped_for_budget = False

        for record in records:
            frequencies: Counter[str] = Counter()
            reported_models: list[str] = []
            sample_tokens: list[int] = []
            sample_failures: list[dict[str, Any]] = []
            valid_samples = 0
            for sample_index in range(self.settings.remediation_samples):
                try:
                    sampled, provenance = self._sample(
                        record["policy_id"],
                        record["model_input"],
                        sample_index,
                        today,
                        tokens_used,
                    )
                except DailyBudgetReached:
                    stopped_for_budget = True
                    break
                except NarrativeAPIError as error:
                    sample_failures.append(
                        {"sample_index": sample_index, "reason": error.reason}
                    )
                    continue
                if provenance == "api":
                    tokens_used += sampled["tokens"]
                    _write_daily_usage(
                        self.daily_usage_path,
                        today,
                        self.settings.remediation_model,
                        tokens_used,
                        legacy_model=self.settings.groq_model,
                    )
                try:
                    if sampled.get("finish_reason") == "length":
                        raise NarrativeAPIError("truncated_response")
                    causes = _validate_causes(sampled["content"])
                except NarrativeAPIError as error:
                    sample_failures.append(
                        {"sample_index": sample_index, "reason": error.reason}
                    )
                    continue
                frequencies.update(causes)
                valid_samples += 1
                reported_models.append(sampled["model_reported"])
                sample_tokens.append(sampled["tokens"])
            if stopped_for_budget:
                break
            sampled_records.append(
                {
                    "policy_id": record["policy_id"],
                    "true_causes": record["true_causes"],
                    "frequencies": {
                        cause: frequencies.get(cause, 0) for cause in CAUSE_LABELS
                    },
                    "model_reported": reported_models,
                    "sample_tokens": sample_tokens,
                    "valid_samples": valid_samples,
                    "status": "failed" if sample_failures else "ok",
                    "sample_failures": sample_failures,
                    "split": (
                        "calibration"
                        if record["policy_id"] in calibration
                        else "test"
                    ),
                }
            )

        complete = len(sampled_records) == len(records)
        calibration_scores = []
        if complete:
            for record in sampled_records:
                if record["split"] == "calibration":
                    calibration_scores.append(
                        max(
                            1
                            - record["frequencies"].get(cause, 0)
                            / self.settings.remediation_samples
                            for cause in record["true_causes"]
                        )
                    )
            quantile = conformal_quantile(
                calibration_scores, self.settings.conformal_alpha
            )
            for record in sampled_records:
                record["prediction_set"] = build_prediction_set(
                    record["frequencies"],
                    self.settings.remediation_samples,
                    quantile,
                )
                record["singleton"] = len(record["prediction_set"]) == 1
        else:
            quantile = None
            for record in sampled_records:
                record["prediction_set"] = []
                record["singleton"] = False

        output_records = [
            {
                "policy_id": record["policy_id"],
                "frequencies": record["frequencies"],
                "prediction_set": record["prediction_set"],
                "singleton": record["singleton"],
                "split": record["split"],
                "model": self.settings.remediation_model,
                "model_reported": record["model_reported"],
                "sample_tokens": record["sample_tokens"],
                "valid_samples": record["valid_samples"],
                "status": record["status"],
                "sample_failures": record["sample_failures"],
            }
            for record in sampled_records
        ]
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with self.output_path.open("w", encoding="utf-8", newline="\n") as destination:
            for record in output_records:
                destination.write(
                    json.dumps(record, sort_keys=True, ensure_ascii=True) + "\n"
                )

        metrics = (
            calculate_test_metrics(sampled_records, test)
            if complete
            else {"test_records": 0}
        )
        warning = bool(quantile is not None and quantile >= 1)
        samples_failed = sum(
            len(record["sample_failures"]) for record in sampled_records
        )
        records_with_fewer_than_k_valid_samples = sum(
            record["valid_samples"] < self.settings.remediation_samples
            for record in sampled_records
        )
        if warning:
            warnings.warn(
                "Conformal q-hat is at least 1; prediction sets are uninformative.",
                RuntimeWarning,
                stacklevel=2,
            )
        summary = {
            "labelled_records": len(records),
            "calibration_records": len(calibration),
            "test_records": len(test),
            "excluded_missing_unit_only": excluded,
            "api_calls": self._api_calls,
            "samples_failed": samples_failed,
            "records_with_fewer_than_k_valid_samples": (
                records_with_fewer_than_k_valid_samples
            ),
            "quantile": quantile,
            "alpha": self.settings.conformal_alpha,
            "uninformative_quantile_warning": warning,
            "complete": complete,
            **metrics,
            **(top_answer_metrics(sampled_records) if complete else {}),
            "tokens_used_today": tokens_used,
            "tokens_remaining_today": max(
                0, self.settings.groq_daily_token_budget - tokens_used
            ),
            "records_remaining": max(0, len(records) - len(sampled_records)),
        }
        if stopped_for_budget:
            summary["stopped_for_budget"] = True
        if complete:
            self._write_reports(summary)
        return summary

    def _sample(
        self,
        policy_id: str,
        model_input: dict[str, Any],
        sample_index: int,
        today: str,
        tokens_used: int,
    ) -> tuple[dict[str, Any], str]:
        cache_path = self._cache_path(policy_id, model_input, sample_index)
        if cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8")), "cached"
        messages = _request_messages(model_input)
        estimated_tokens = _token_estimate(messages) + MAX_COMPLETION_TOKENS
        if estimated_tokens > self.settings.groq_daily_token_budget - tokens_used:
            raise DailyBudgetReached
        payload = {
            "model": self.settings.remediation_model,
            "messages": messages,
            "temperature": 1.0,
            "max_completion_tokens": MAX_COMPLETION_TOKENS,
            "reasoning_effort": "low",
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "address_cause",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "causes": {
                                "type": "array",
                                "items": {
                                    "type": "string",
                                    "enum": list(CAUSE_LABELS),
                                },
                            }
                        },
                        "required": ["causes"],
                        "additionalProperties": False,
                    },
                },
            },
        }
        headers = {
            "Authorization": f"Bearer {self.settings.groq_api_key}",
            "Content-Type": "application/json",
        }
        for attempt in range(2):
            self._pacing._wait_for_capacity(estimated_tokens)
            self._pacing._request_times.append(self.clock())
            self._api_calls += 1
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
            reason = _safe_groq_error_reason(
                response, payload, self.settings.groq_api_key
            )
            if attempt == 0 and (
                response.status_code == 429 or response.status_code >= 500
            ):
                self.sleeper(
                    self._pacing._retry_after_seconds(
                        response.headers.get("Retry-After")
                    )
                )
                continue
            raise NarrativeAPIError(reason)
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            finish_reason = body["choices"][0]["finish_reason"]
            usage = body["usage"]
            tokens = int(usage["total_tokens"])
            model_reported = body["model"]
        except (ValueError, KeyError, TypeError, IndexError):
            raise NarrativeAPIError("invalid_response") from None
        if (
            not isinstance(content, str)
            or not isinstance(model_reported, str)
            or not model_reported
            or not isinstance(finish_reason, str)
            or tokens < 0
        ):
            raise NarrativeAPIError("invalid_response")
        cached = {
            "content": content,
            "tokens": tokens,
            "model_reported": model_reported,
            "finish_reason": finish_reason,
        }
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(cached, sort_keys=True) + "\n", encoding="utf-8"
        )
        self._pacing._token_usage.append((self.clock(), tokens))
        return cached, "api"

    def _write_reports(self, summary: dict[str, Any]) -> None:
        rows = [
            ("labelled_records", summary["labelled_records"]),
            ("calibration_records", summary["calibration_records"]),
            ("test_records", summary["test_records"]),
            ("excluded_missing_unit_only", summary["excluded_missing_unit_only"]),
            ("samples_per_record", self.settings.remediation_samples),
            ("samples_failed", summary["samples_failed"]),
            (
                "records_with_fewer_than_k_valid_samples",
                summary["records_with_fewer_than_k_valid_samples"],
            ),
            ("top_answer_correct", summary["top_answer_correct"]),
            ("top_answer_total", summary["top_answer_total"]),
            ("top_answer_accuracy", summary["top_answer_accuracy"]),
            ("alpha", summary["alpha"]),
            ("q_hat", summary["quantile"]),
            ("test_contains_all_true_labels", summary["contains_all_true_labels"]),
            (
                "test_contains_at_least_one_true_label",
                summary["contains_at_least_one_true_label"],
            ),
            ("mean_prediction_set_size", summary["mean_prediction_set_size"]),
            ("singleton_share", summary["singleton_share"]),
            ("uninformative_q_hat", summary["uninformative_quantile_warning"]),
        ]
        rows.extend(
            (
                f"per_cause_{cause}_{metric}",
                value,
            )
            for cause, counts in summary["per_cause_accuracy"].items()
            for metric, value in counts.items()
        )
        rows.extend(
            (
                f"confusion_{row['true_cause']}_to_{row['predicted_cause']}",
                row["count"],
            )
            for row in summary["confusion_table"]
        )
        self.report_csv_path.parent.mkdir(parents=True, exist_ok=True)
        with self.report_csv_path.open(
            "w", newline="", encoding="utf-8"
        ) as destination:
            writer = csv.writer(destination)
            writer.writerow(REPORT_COLUMNS)
            writer.writerows(rows)
        self.report_md_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Exception cause diagnosis calibration",
            "",
            "Aggregate split-conformal evaluation; no address-level records or "
            "Precisely response values are included.",
            "",
            "The taxonomy maps both missing house numbers and synthetic "
            "nonexistent addresses to `house_number_issue`: the synthetic "
            "nonexistent case is an out-of-range house number.",
            "",
            f"- Labelled records: {summary['labelled_records']}",
            f"- Calibration / test: {summary['calibration_records']} / "
            f"{summary['test_records']}",
            f"- Missing-unit-only records excluded: "
            f"{summary['excluded_missing_unit_only']}",
            f"- Samples per record: {self.settings.remediation_samples}",
            f"- Samples failed: {summary['samples_failed']}",
            f"- Records with fewer than K valid samples: "
            f"{summary['records_with_fewer_than_k_valid_samples']}",
            f"- Top-answer accuracy: {summary['top_answer_correct']}/"
            f"{summary['top_answer_total']} "
            f"({summary['top_answer_accuracy']:.3f})",
            "",
            "## Top-answer accuracy by true cause",
            "",
            "| True cause | Correct | Total | Accuracy |",
            "|---|---:|---:|---:|",
        ]
        lines.extend(
            f"| {cause} | {counts['correct']} | {counts['total']} | "
            f"{counts['accuracy']:.3f} |"
            for cause, counts in summary["per_cause_accuracy"].items()
        )
        lines.extend(
            [
                "",
                "## Confusion table (true cause vs most frequent prediction)",
                "",
                "| True cause | Predicted cause | Count |",
                "|---|---|---:|",
            ]
        )
        lines.extend(
            f"| {row['true_cause']} | {row['predicted_cause']} | "
            f"{row['count']} |"
            for row in summary["confusion_table"]
        )
        lines.extend(
            [
                f"- Alpha: {summary['alpha']}",
                f"- q-hat: {summary['quantile']}",
                f"- Test set contains all true causes: "
                f"{summary['contains_all_true_labels']:.3f}",
                f"- Test set contains at least one true cause: "
                f"{summary['contains_at_least_one_true_label']:.3f}",
                f"- Mean prediction-set size: "
                f"{summary['mean_prediction_set_size']:.3f}",
                f"- Singleton prediction-set share: "
                f"{summary['singleton_share']:.3f}",
                f"- Uninformative q-hat warning: "
                f"{summary['uninformative_quantile_warning']}",
                "",
                "Missing-unit-only records are excluded because the submitted "
                "address and geocoder precision/match signals do not reveal "
                "whether a unit was omitted or mismatched.",
            ]
        )
        self.report_md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class DailyBudgetReached(Exception):
    """Internal stop signal when the shared daily Groq token budget is reached."""


def run_diagnosis_cli(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Sample address exception causes and calibrate prediction sets."
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--execute", action="store_true", help="Call Groq (requires typing YES)."
    )
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    from address_intelligence.config import Settings

    diagnoser = RemediationDiagnoser(Settings.from_env())
    counts = diagnoser.dry_run(args.limit)
    if not args.execute:
        print("Mode: DRY RUN (no network calls)")
        print(
            f"Labelled records: {counts['labelled_records']}; "
            f"calibration/test: {counts['calibration_records']}/"
            f"{counts['test_records']}; "
            f"excluded missing_unit-only: {counts['excluded_missing_unit_only']}"
        )
        print(
            f"Planned calls: {counts['planned_calls']} "
            f"({counts['labelled_records']} records x "
            f"{diagnoser.settings.remediation_samples} samples, "
            f"{counts['cached_samples']} cached); estimated tokens: "
            f"{counts['estimated_tokens']}; daily budget remaining: "
            f"{counts['daily_budget_remaining']}; "
            f"budget sufficient: {counts['budget_sufficient']}"
        )
        return 0
    print("Mode: EXECUTE (plan; confirmation required)")
    print(
        f"Planned calls: {counts['planned_calls']}; estimated tokens: "
        f"{counts['estimated_tokens']}; daily budget remaining: "
        f"{counts['daily_budget_remaining']}"
    )
    if input("Type YES to send submitted synthetic addresses to Groq: ") != "YES":
        print("Execution cancelled.")
        return 1
    summary = diagnoser.execute(args.limit)
    print("Remediation diagnosis summary:")
    for name, value in summary.items():
        print(f"{name.replace('_', ' ').capitalize()}: {value}")
    return 0
