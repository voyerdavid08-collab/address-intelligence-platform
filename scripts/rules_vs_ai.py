"""Offline aggregate comparison of fixed address rules and saved AI diagnoses."""

import csv
import json
from pathlib import Path
from typing import Any

from address_intelligence.remediation import (
    CAUSE_LABELS,
    build_model_input,
    map_ground_truth_causes,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIAGNOSIS_PATH = (
    PROJECT_ROOT / "data" / "output" / "run" / "remediation_diagnosis.jsonl"
)
DEFAULT_ACTIONS_PATH = (
    PROJECT_ROOT / "data" / "output" / "run" / "remediation_actions.jsonl"
)
DEFAULT_RESULTS_PATH = PROJECT_ROOT / "data" / "output" / "run" / "results_rowlevel.csv"
DEFAULT_INPUT_PATH = PROJECT_ROOT / "data" / "input" / "policyholders_synthetic.csv"
DEFAULT_GROUND_TRUTH_PATH = PROJECT_ROOT / "data" / "input" / "ground_truth.csv"
DEFAULT_OUTPUT_PATH = (
    PROJECT_ROOT / "data" / "output" / "analysis" / "rules_vs_ai.md"
)
RULE_COMPONENT_CAUSES = {
    "house_number": "house_number_issue",
    "street_name": "street_name_issue",
    "city": "city_issue",
    "zip": "zip_issue",
}


def _read_csv(path: Path, required: tuple[str, ...]) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        missing = [field for field in required if field not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path} is missing required columns: {', '.join(missing)}")
        return list(reader)


def _read_jsonl(path: Path, required: tuple[str, ...]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(f"{path} line {line_number} must be a JSON object.")
            missing = [field for field in required if field not in record]
            if missing:
                raise ValueError(
                    f"{path} line {line_number} is missing fields: {', '.join(missing)}"
                )
            records.append(record)
    return records


def _rule_cause(model_input: dict[str, Any]) -> str:
    features = model_input["submitted_features"]
    matches = model_input["match_signals"]["component_matches"]
    if features["house_number_present"] is False:
        return "house_number_issue"
    if features["zip_present"] is False:
        return "zip_issue"
    values = [matches[name] for name in RULE_COMPONENT_CAUSES]
    if "unknown" not in values:
        false_components = [
            name for name, value in zip(RULE_COMPONENT_CAUSES, values) if value is False
        ]
        if len(false_components) == 1:
            return RULE_COMPONENT_CAUSES[false_components[0]]
    return "ambiguous"


def _ai_top_answer(frequencies: Any) -> str | None:
    if not isinstance(frequencies, dict):
        return None
    ranked: list[tuple[str, float]] = []
    for label in CAUSE_LABELS:
        value = frequencies.get(label)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        ranked.append((label, float(value)))
    if not ranked:
        return None
    highest = max(count for _, count in ranked)
    return next(label for label in CAUSE_LABELS if (label, highest) in ranked)


def _load_records(
    diagnosis_path: Path,
    actions_path: Path,
    results_path: Path,
    input_path: Path,
    ground_truth_path: Path,
) -> tuple[list[dict[str, Any]], set[str]]:
    policies = {
        row["policy_id"]: row
        for row in _read_csv(
            input_path,
            ("policy_id", "raw_address_line1", "raw_address_line2", "raw_city", "raw_state", "raw_zip5"),
        )
    }
    results = {
        row["policy_id"]: row
        for row in _read_csv(results_path, ("policy_id", "precision_code", "eligibility"))
    }
    truths = {
        row["policy_id"]: row
        for row in _read_csv(ground_truth_path, ("policy_id", "defects"))
    }
    diagnoses = _read_jsonl(
        diagnosis_path, ("policy_id", "frequencies", "prediction_set", "split")
    )
    actions = _read_jsonl(actions_path, ("policy_id", "action"))
    exception_ids = {str(record["policy_id"]) for record in actions}

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for diagnosis in diagnoses:
        policy_id = str(diagnosis["policy_id"])
        if policy_id in seen:
            raise ValueError("Diagnosis input contains duplicate policy records.")
        seen.add(policy_id)
        policy = policies.get(policy_id)
        result = results.get(policy_id)
        truth = truths.get(policy_id)
        if policy is None or result is None or truth is None:
            raise ValueError("A diagnosis record is missing a matching local input record.")
        true_causes = map_ground_truth_causes(truth.get("defects", ""))
        model_input = build_model_input(policy, result.get("precision_code", ""))
        rule = _rule_cause(model_input)
        ai_top = _ai_top_answer(diagnosis["frequencies"])
        split = str(diagnosis["split"])
        records.append(
            {
                "rule": rule,
                "ai_top": ai_top,
                "true_causes": true_causes,
                "split": split,
                "exception": policy_id in exception_ids,
                "eligible": result.get("eligibility", "").strip() == "eligible",
                "house_number_present": model_input["submitted_features"][
                    "house_number_present"
                ],
                "house_number_match": model_input["match_signals"][
                    "component_matches"
                ]["house_number"],
                "action_outreach": any(
                    str(item["policy_id"]) == policy_id
                    and str(item["action"]) == "customer_outreach"
                    for item in actions
                ),
            }
        )
    unknown_exception_ids = exception_ids - seen
    if unknown_exception_ids:
        raise ValueError("An action record has no matching diagnosis record.")
    return records, exception_ids


def _accuracy(correct: int, total: int) -> str:
    return f"{correct}/{total} ({100 * correct / total:.1f}%)" if total else "0/0 (n/a)"


def _group_metrics(name: str, records: list[dict[str, Any]]) -> list[str]:
    covered = [record for record in records if record["rule"] != "ambiguous"]
    ambiguous = [record for record in records if record["rule"] == "ambiguous"]
    rule_correct = sum(record["rule"] in record["true_causes"] for record in covered)
    ai_covered_correct = sum(
        record["ai_top"] in record["true_causes"] for record in covered
    )
    ai_ambiguous_correct = sum(
        record["ai_top"] in record["true_causes"] for record in ambiguous
    )
    ai_all_correct = sum(
        record["ai_top"] in record["true_causes"] for record in records
    )
    return [
        f"| {name} | {len(records)} | {len(covered)}/{len(records)} "
        f"({100 * len(covered) / len(records):.1f}%) | "
        f"{_accuracy(rule_correct, len(covered))} | "
        f"{_accuracy(ai_covered_correct, len(covered))} | "
        f"{_accuracy(ai_ambiguous_correct, len(ambiguous))} | "
        f"{_accuracy(ai_all_correct, len(records))} | {len(ambiguous)} |"
    ]


def _routing_counts(
    records: list[dict[str, Any]], predicts_outreach: Any
) -> tuple[int, int, int]:
    true_positive = false_positive = false_negative = 0
    for record in records:
        predicted = bool(predicts_outreach(record))
        actual = "house_number_issue" in record["true_causes"]
        true_positive += predicted and actual
        false_positive += predicted and not actual
        false_negative += not predicted and actual
    return true_positive, false_positive, false_negative


def _report(records: list[dict[str, Any]]) -> str:
    exceptions = [record for record in records if record["exception"]]
    others = [record for record in records if not record["exception"]]
    groups = (
        ("All diagnosed records", records),
        ("Exceptions", exceptions),
        ("Other diagnosed records", others),
    )
    lines = [
        "# Fixed rules vs saved AI diagnoses",
        "",
        "| Group | Records | Rule coverage | Rule accuracy (covered) | "
        "AI accuracy (covered) | AI accuracy (ambiguous) | AI accuracy (all) | "
        "Ambiguous records |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, selected in groups:
        lines.extend(_group_metrics(name, selected))

    lines.extend(
        [
            "",
            "## Customer outreach routing (exceptions)",
            "",
            "| Routing rule | True positives | False positives | False negatives |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    routing = (
        (
            "Rule A",
            lambda record: record["rule"] == "house_number_issue",
        ),
        (
            "Rule B",
            lambda record: record["house_number_present"] is False
            or record["house_number_match"] is False,
        ),
        ("AI action", lambda record: record["action_outreach"]),
    )
    for name, predicate in routing:
        tp, fp, fn = _routing_counts(exceptions, predicate)
        lines.append(f"| {name} | {tp} | {fp} | {fn} |")

    ambiguous_exceptions = [
        record for record in exceptions if record["rule"] == "ambiguous"
    ]
    split_names = ("calibration", "test")
    lines.extend(
        [
            "",
            "## Ambiguous exceptions by split",
            "",
            "| Split | Records |",
            "| --- | ---: |",
        ]
    )
    for split in split_names:
        count = sum(record["split"] == split for record in ambiguous_exceptions)
        lines.append(f"| {split} | {count} |")
    lines.append(
        f"| Other/unspecified | "
        f"{sum(record['split'] not in split_names for record in ambiguous_exceptions)} |"
    )
    return "\n".join(lines) + "\n"


def main(
    diagnosis_path: Path = DEFAULT_DIAGNOSIS_PATH,
    actions_path: Path = DEFAULT_ACTIONS_PATH,
    results_path: Path = DEFAULT_RESULTS_PATH,
    input_path: Path = DEFAULT_INPUT_PATH,
    ground_truth_path: Path = DEFAULT_GROUND_TRUTH_PATH,
    output_path: Path = DEFAULT_OUTPUT_PATH,
) -> int:
    records, _ = _load_records(
        diagnosis_path, actions_path, results_path, input_path, ground_truth_path
    )
    report = _report(records)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(f"Aggregate rules-vs-AI analysis written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
