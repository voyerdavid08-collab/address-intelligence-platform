"""Offline aggregate breakdowns for presentation use."""

import csv
import json
from pathlib import Path
from typing import Any

from address_intelligence.metrics import (
    _canonical_line1,
    _fully_correct,
    _is_correct_building,
)
from address_intelligence.remediation import PRECISION_CATEGORIES, precision_category


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS_PATH = PROJECT_ROOT / "data" / "output" / "run" / "results_rowlevel.csv"
DEFAULT_INPUT_PATH = PROJECT_ROOT / "data" / "input" / "policyholders_synthetic.csv"
DEFAULT_GROUND_TRUTH_PATH = PROJECT_ROOT / "data" / "input" / "ground_truth.csv"
DEFAULT_CACHE_DIR = (
    PROJECT_ROOT
    / "data"
    / "output"
    / "run"
    / "cache"
    / "geocode_premium_structured"
)
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "output" / "analysis" / "breakdown.md"
PRECISION_BUCKETS = ("S8", "S5", "S7", "S4", "Z", "G", "Other/blank")


def _read_csv(path: Path, required: tuple[str, ...]) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        missing = [key for key in required if key not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{path} is missing required columns: {', '.join(missing)}")
        return list(reader)


def _defects(value: str) -> tuple[str, ...]:
    return tuple(
        sorted({label.strip() for label in value.replace("|", ";").replace(",", ";").split(";") if label.strip()})
    )


def _precision_bucket(value: str) -> str:
    code = value.strip().upper()
    for prefix in PRECISION_BUCKETS[:-1]:
        if code.startswith(prefix):
            return prefix
    return "Other/blank"


def _find_pb_key(value: Any) -> str:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "PB_KEY" and child is not None and str(child).strip():
                return str(child).strip()
        for child in value.values():
            found = _find_pb_key(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_pb_key(child)
            if found:
                return found
    return ""


def _cache_has_pb_key(cache_dir: Path, policy_id: str) -> bool:
    path = cache_dir / f"{policy_id}.json"
    if not path.exists():
        return False
    try:
        response = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError(f"Unable to read geocode cache at {path}.") from None
    if not isinstance(response, dict) or response.get("status_code") != 200:
        return False
    data = response.get("data")
    candidates = data.get("candidates") if isinstance(data, dict) else None
    if not isinstance(candidates, list) or not candidates:
        return False
    return bool(_find_pb_key(candidates[0]))


def _records(
    results_path: Path,
    input_path: Path,
    ground_truth_path: Path,
    cache_dir: Path,
) -> list[dict[str, Any]]:
    policies = _read_csv(
        input_path,
        ("policy_id", "raw_address_line1", "raw_address_line2", "raw_zip5"),
    )
    truth_rows = _read_csv(
        ground_truth_path,
        (
            "policy_id",
            "clean_address_line1",
            "clean_address_line2",
            "clean_zip5",
            "defects",
        ),
    )
    results = _read_csv(
        results_path,
        (
            "policy_id",
            "precision_code",
            "eligibility",
            "standardized_address_line1",
            "standardized_address_line2",
            "standardized_zip5",
            "preciselyid",
        ),
    )
    truth_by_id = {row["policy_id"]: row for row in truth_rows}
    results_by_id = {row["policy_id"]: row for row in results}
    records: list[dict[str, Any]] = []
    for policy in policies:
        policy_id = policy["policy_id"]
        truth = truth_by_id.get(policy_id)
        if truth is None:
            raise ValueError(f"Ground truth is missing a policy record: {policy_id}")
        result = results_by_id.get(policy_id, {})
        labels = _defects(truth.get("defects", ""))
        eligible = result.get("eligibility", "").strip() == "eligible"
        precision = result.get("precision_code", "").strip()
        preciselyid = result.get("preciselyid", "").strip()
        pb_key = _cache_has_pb_key(cache_dir, policy_id)
        raw_line = policy.get("raw_address_line1", "")
        raw_line2 = policy.get("raw_address_line2", "")
        raw_zip = policy.get("raw_zip5", "")
        std_line = result.get("standardized_address_line1", "")
        std_line2 = result.get("standardized_address_line2", "")
        std_zip = result.get("standardized_zip5", "")
        records.append(
            {
                "truth": truth,
                "labels": labels,
                "clean": not labels,
                "eligible": eligible,
                "precision": precision,
                "bucket": _precision_bucket(precision),
                "category": precision_category(precision),
                "preciselyid": bool(preciselyid),
                "pb_key": pb_key,
                "both_ids": bool(eligible and preciselyid and pb_key),
                "before_building": _is_correct_building(raw_line, raw_zip, truth),
                "after_building": _is_correct_building(std_line, std_zip, truth),
                "raw_line_matches": bool(raw_line.strip())
                and _canonical_line1(raw_line)
                == _canonical_line1(truth.get("clean_address_line1", "")),
                "before_fully_correct": _fully_correct(
                    raw_line, raw_line2, raw_zip, truth
                ),
                "after_fully_correct": _fully_correct(
                    std_line, std_line2, std_zip, truth
                ),
            }
        )
    return records


def _precision_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    return {
        bucket: sum(record["bucket"] == bucket for record in records)
        for bucket in PRECISION_BUCKETS
    }


def _precision_section(records: list[dict[str, Any]]) -> list[str]:
    lines = [
        "## Precision",
        "",
        "| Population | S8 | S5 | S7 | S4 | Z | G | Other/blank |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    populations = (
        ("All", records),
        ("Clean", [record for record in records if record["clean"]]),
        ("Flawed", [record for record in records if not record["clean"]]),
        ("Eligible", [record for record in records if record["eligible"]]),
        (
            "Eligible clean",
            [
                record
                for record in records
                if record["eligible"] and record["clean"]
            ],
        ),
        (
            "Eligible flawed",
            [
                record
                for record in records
                if record["eligible"] and not record["clean"]
            ],
        ),
    )
    for name, selected in populations:
        counts = _precision_counts(selected)
        lines.append(
            f"| {name} ({len(selected)}) | "
            + " | ".join(str(counts[key]) for key in PRECISION_BUCKETS)
            + " |"
        )
    return lines


def _building_counts(records: list[dict[str, Any]]) -> tuple[int, int, int]:
    before = sum(record["before_building"] for record in records)
    after = sum(record["after_building"] for record in records)
    return before, after, after - before


def _defect_section(records: list[dict[str, Any]]) -> list[str]:
    flawed = [record for record in records if not record["clean"]]
    labels = sorted({label for record in flawed for label in record["labels"]})
    selections = [
        (label, [record for record in flawed if label in record["labels"]])
        for label in labels
    ]
    selections.append(("all flawed", flawed))
    lines = [
        "## Right building by defect",
        "",
        "| Defect | Records | Right before | Right after | Gain |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for label, selected in selections:
        before, after, gain = _building_counts(selected)
        lines.append(f"| {label} | {len(selected)} | {before} | {after} | {gain} |")
    return lines


def _gain_section(records: list[dict[str, Any]]) -> list[str]:
    gained = [
        record
        for record in records
        if not record["clean"]
        and not record["before_building"]
        and record["after_building"]
    ]
    zip_only = sum(record["raw_line_matches"] for record in gained)
    return [
        "## Gain type",
        "",
        "| Type | Records |",
        "| --- | ---: |",
        f"| ZIP-only gain (raw line already matched) | {zip_only} |",
        f"| Street or number gain | {len(gained) - zip_only} |",
        f"| Total flawed records wrong before and right after | {len(gained)} |",
    ]


def _fully_correct_section(records: list[dict[str, Any]]) -> list[str]:
    flawed = [record for record in records if not record["clean"]]
    before = sum(record["before_fully_correct"] for record in flawed)
    after = sum(record["after_fully_correct"] for record in flawed)
    return [
        "## Fully correct including unit",
        "",
        "| Population | Records | Fully correct |",
        "| --- | ---: | ---: |",
        f"| Flawed before | {len(flawed)} | {before} |",
        f"| Flawed after | {len(flawed)} | {after} |",
        f"| Gain | {len(flawed)} | {after - before} |",
    ]


def _preciselyid_section(records: list[dict[str, Any]]) -> list[str]:
    eligible = [record for record in records if record["eligible"]]
    lines = [
        "## PreciselyID coverage (eligible records)",
        "",
        f"- Eligible records: {len(eligible)}",
        f"- Non-blank PreciselyID in results: {sum(record['preciselyid'] for record in eligible)}",
        f"- Non-blank PB_KEY in geocode cache: {sum(record['pb_key'] for record in eligible)}",
        f"- Records with both: {sum(record['both_ids'] for record in eligible)}",
        "",
        "| Precision category | PreciselyID | PB_KEY | Both |",
        "| --- | ---: | ---: | ---: |",
    ]
    categories = sorted(set(PRECISION_CATEGORIES.values()))
    for category in categories:
        selected = [record for record in eligible if record["category"] == category]
        lines.append(
            f"| {category} | "
            f"{sum(record['preciselyid'] for record in selected)} | "
            f"{sum(record['pb_key'] for record in selected)} | "
            f"{sum(record['both_ids'] for record in selected)} |"
        )
    return lines


def build_report(records: list[dict[str, Any]]) -> str:
    sections = (
        ["# Presentation breakdown", ""]
        + _precision_section(records)
        + [""]
        + _defect_section(records)
        + [""]
        + _gain_section(records)
        + [""]
        + _fully_correct_section(records)
        + [""]
        + _preciselyid_section(records)
    )
    return "\n".join(sections) + "\n"


def main(
    results_path: Path = DEFAULT_RESULTS_PATH,
    input_path: Path = DEFAULT_INPUT_PATH,
    ground_truth_path: Path = DEFAULT_GROUND_TRUTH_PATH,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    output_path: Path = DEFAULT_OUTPUT_PATH,
) -> int:
    report = build_report(
        _records(results_path, input_path, ground_truth_path, cache_dir)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    print(f"Aggregate breakdown written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
