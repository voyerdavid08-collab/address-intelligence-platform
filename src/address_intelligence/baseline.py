"""Aggregate-only pre-API quality and synthetic defect metrics."""

import csv
from pathlib import Path
import re
from collections import Counter

from address_intelligence.synthetic_data import DEFECT_COMBINATIONS, DEFECT_TARGETS


PROJECT_ROOT = Path(__file__).resolve().parents[2]
POLICYHOLDERS_PATH = PROJECT_ROOT / "data" / "input" / "policyholders_synthetic.csv"
GROUND_TRUTH_PATH = PROJECT_ROOT / "data" / "input" / "ground_truth.csv"
REFERENCE_PATH = PROJECT_ROOT / "data" / "input" / "reference_streets.csv"
SUMMARY_CSV_PATH = PROJECT_ROOT / "reports" / "baseline_summary.csv"
SUMMARY_MD_PATH = PROJECT_ROOT / "reports" / "baseline_summary.md"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        return list(csv.DictReader(source))


def _percent(count: int, total: int) -> str:
    return f"{(count * 100 / total) if total else 0:.1f}%"


def compute_baseline_metrics(
    policyholders_path: Path = POLICYHOLDERS_PATH,
    ground_truth_path: Path = GROUND_TRUTH_PATH,
    reference_path: Path = REFERENCE_PATH,
) -> dict[str, str]:
    """Return aggregate metrics only; address values are never written to reports."""
    policies = _read_csv(policyholders_path)
    truths = _read_csv(ground_truth_path)
    references = _read_csv(reference_path)
    row_count = len(policies)

    missing_zip = sum(not row["raw_zip5"].strip() for row in policies)
    street_names_without_number = {
        row["street_name"].strip().casefold() for row in references
    }
    missing_house = sum(
        row["raw_address_line1"].strip().casefold() in street_names_without_number
        or re.match(r"^\s*\d+", row["raw_address_line1"]) is None
        for row in policies
    )
    valid_zip = sum(re.fullmatch(r"\d{5}", row["raw_zip5"].strip()) is not None for row in policies)
    valid_state = sum(
        re.fullmatch(r"[A-Za-z]{2}", row["raw_state"].strip()) is not None
        for row in policies
    )
    reference_pairs = {
        (row["city"].strip().casefold(), row["state"].strip().upper(), row["zip5"].strip())
        for row in references
    }
    city_zip_consistent = sum(
        (
            row["raw_city"].strip().casefold(),
            row["raw_state"].strip().upper(),
            row["raw_zip5"].strip(),
        )
        in reference_pairs
        for row in policies
    )
    addresses = Counter(
        (
            row["raw_address_line1"].strip().casefold(),
            row["raw_address_line2"].strip().casefold(),
            row["raw_city"].strip().casefold(),
            row["raw_state"].strip().upper(),
            row["raw_zip5"].strip(),
        )
        for row in policies
    )
    duplicate_records = sum(count - 1 for count in addresses.values() if count > 1)

    defects_by_policy = [
        [value for value in row["defects"].split(";") if value]
        for row in truths
    ]
    defect_counts = {
        defect: sum(defect in defects for defects in defects_by_policy)
        for defect in DEFECT_TARGETS
    }
    unique_dirty = sum(bool(defects) for defects in defects_by_policy)
    overlap_records = sum(len(defects) > 1 for defects in defects_by_policy)
    combination_counts = {
        "+".join(pair): sum(
            len(defects) == len(pair) and set(defects) == set(pair)
            for defects in defects_by_policy
        )
        for pair in DEFECT_COMBINATIONS
    }

    metrics = {
        "Input rows": str(row_count),
        "Missing ZIP": f"{missing_zip} ({_percent(missing_zip, row_count)})",
        "Missing house number": f"{missing_house} ({_percent(missing_house, row_count)})",
        "Unit where expected unknown": "not measurable pre-API",
        "ZIP format valid": f"{valid_zip} ({_percent(valid_zip, row_count)})",
        "State format valid": f"{valid_state} ({_percent(valid_state, row_count)})",
        "City-state-ZIP matches reference": (
            f"{city_zip_consistent} ({_percent(city_zip_consistent, row_count)})"
        ),
        "Duplicate address records beyond first": (
            f"{duplicate_records} ({_percent(duplicate_records, row_count)})"
        ),
    }
    for defect, target in DEFECT_TARGETS.items():
        count = defect_counts[defect]
        metrics[f"{defect} actual (target {target:.0%})"] = (
            f"{count} ({_percent(count, len(truths))})"
        )
    metrics["Unique dirty records"] = (
        f"{unique_dirty} ({_percent(unique_dirty, len(truths))})"
    )
    metrics["Overlapping records (2+ defects)"] = (
        f"{overlap_records} ({_percent(overlap_records, len(truths))})"
    )
    for combination, count in combination_counts.items():
        metrics[f"Defect combination: {combination}"] = str(count)
    return metrics


def write_baseline_report(
    metrics: dict[str, str],
    csv_path: Path = SUMMARY_CSV_PATH,
    markdown_path: Path = SUMMARY_MD_PATH,
) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.writer(destination, lineterminator="\n")
        writer.writerow(("section", "metric", "value", "combination", "count"))
        for metric, value in metrics.items():
            if metric.startswith("Defect combination: "):
                writer.writerow(
                    (
                        "Defect combinations",
                        "",
                        "",
                        metric.removeprefix("Defect combination: "),
                        value,
                    )
                )
            else:
                section = "Input quality" if metric in list(metrics)[:8] else "Defect injection"
                writer.writerow((section, metric, value, "", ""))

    lines = [
        "# Pre-API baseline summary",
        "",
        "Aggregate metrics from synthetic input and its separate ground truth; no API data.",
        "",
        "## Input quality",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
    ]
    input_metrics = list(metrics.items())[:8]
    lines.extend(f"| {name} | {value} |" for name, value in input_metrics)
    lines.extend(
        [
            "",
            "## Ground-truth defect injection",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
        ]
    )
    ground_truth_metrics = [
        (name, value)
        for name, value in list(metrics.items())[8:]
        if not name.startswith("Defect combination: ")
    ]
    lines.extend(f"| {name} | {value} |" for name, value in ground_truth_metrics)
    lines.extend(
        [
            "",
            "## Defect combinations",
            "",
            "| Combination | Count |",
            "| --- | ---: |",
        ]
    )
    lines.extend(
        f"| {name.removeprefix('Defect combination: ')} | {value} |"
        for name, value in metrics.items()
        if name.startswith("Defect combination: ")
    )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_baseline_report(
    policyholders_path: Path = POLICYHOLDERS_PATH,
    ground_truth_path: Path = GROUND_TRUTH_PATH,
    reference_path: Path = REFERENCE_PATH,
    csv_path: Path = SUMMARY_CSV_PATH,
    markdown_path: Path = SUMMARY_MD_PATH,
) -> dict[str, str]:
    metrics = compute_baseline_metrics(
        policyholders_path,
        ground_truth_path,
        reference_path,
    )
    write_baseline_report(metrics, csv_path, markdown_path)
    return metrics
