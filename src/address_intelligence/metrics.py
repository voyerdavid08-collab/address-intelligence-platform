"""Aggregate-only before/after metrics from local address-intelligence outputs."""

import argparse
import csv
from decimal import Decimal
from pathlib import Path
import re
import statistics
from typing import Iterable

from address_intelligence.credits import CreditLedger
from address_intelligence.client_profile import (
    DEFAULT_PROFILE_PATH,
    ClientProfile,
    load_client_profile,
    read_client_policies,
)
from address_intelligence.flood import flood_zone_base, risk_tier
from address_intelligence.remediation_actions import (
    _normalize_city,
    _normalize_verified_street,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PROFILE = load_client_profile(DEFAULT_PROFILE_PATH)
INPUT_PATH = _DEFAULT_PROFILE.input_path
GROUND_TRUTH_PATH = _DEFAULT_PROFILE.ground_truth_path
RESULTS_PATH = _DEFAULT_PROFILE.run_dir / "results_rowlevel.csv"
NARRATIVE_REPORT_PATH = _DEFAULT_PROFILE.reports_dir / "ai_narrative_summary.csv"
CALIBRATION_REPORT_PATH = _DEFAULT_PROFILE.reports_dir / "remediation_calibration.csv"
ACTIONS_REPORT_PATH = _DEFAULT_PROFILE.reports_dir / "remediation_actions.csv"
CSV_OUTPUT_PATH = _DEFAULT_PROFILE.reports_dir / "before_after_summary.csv"
MD_OUTPUT_PATH = _DEFAULT_PROFILE.reports_dir / "before_after_summary.md"
OUTPUT_COLUMNS = (
    "section",
    "metric",
    "before",
    "after",
    "numerator",
    "denominator",
    "source",
)
SOURCES = {"input", "ground_truth", "precisely_api", "ai_report", "ledger"}
_DIGITS = re.compile(r"\D")
_DIRECTIONAL_WORDS = re.compile(
    r"\b(EAST|WEST|NORTH|SOUTH|SAINT)\b", re.IGNORECASE
)
_UNIT_VALUE = re.compile(
    r"(?:\b(?:APT|APARTMENT|UNIT|STE|SUITE)\b\s*#?\s*|#\s*)([A-Z0-9]+)",
    re.IGNORECASE,
)
_POLICY_ID = re.compile(r"\bPOL-\d+\b", re.IGNORECASE)
_COORDINATE_LIKE = re.compile(r"(?<!\d)-?\d+\.\d{4,}(?!\d)")
ALLOWED_OUTPUT_COLUMNS = (
    "section",
    "metric",
    "before",
    "after",
    "numerator",
    "denominator",
    "source",
)
_SAFE_STATUS_NAMES = {
    "preciselyid_status": "PreciselyID stage",
}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        return list(csv.DictReader(source))


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(
            destination, fieldnames=OUTPUT_COLUMNS, lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def _zip5(value: str | None) -> str:
    digits = _DIGITS.sub("", value or "")
    return digits[:5] if len(digits) >= 5 else digits


def _canonical_line1(value: str | None) -> str:
    return _normalize_verified_street(_canonical_directional_words(value))


def _canonical_directional_words(value: str | None) -> str:
    def replace_direction(match: re.Match[str]) -> str:
        return {
            "EAST": "E",
            "WEST": "W",
            "NORTH": "N",
            "SOUTH": "S",
            "SAINT": "ST",
        }[match.group(1).upper()]

    return _DIRECTIONAL_WORDS.sub(replace_direction, value or "")


def _canonical_city(value: str | None) -> str:
    return _normalize_city(_canonical_directional_words(value))


def _unit(value: str | None, line2: str | None = None) -> str:
    for candidate in (value or "", line2 or ""):
        match = _UNIT_VALUE.search(candidate)
        if match:
            return re.sub(r"[^A-Z0-9]", "", match.group(1).upper())
    return ""


def _correct_street_city(line1: str | None, city: str | None, truth: dict[str, str]) -> bool:
    return (
        bool(line1)
        and _canonical_line1(line1) == _canonical_line1(truth.get("clean_address_line1", ""))
        and _normalize_city(city or "") == _normalize_city(truth.get("clean_city", ""))
    )


def _is_correct_building(line1: str | None, zip5: str | None, truth: dict[str, str]) -> bool:
    return (
        bool(line1)
        and _canonical_line1(line1) == _canonical_line1(truth.get("clean_address_line1", ""))
        and bool(zip5)
        and _zip5(zip5) == _zip5(truth.get("clean_zip5", ""))
    )


def _fully_correct(
    line1: str | None,
    line2: str | None,
    zip5: str | None,
    truth: dict[str, str],
) -> bool:
    return (
        _is_correct_building(line1, zip5, truth)
        and _unit(line1, line2)
        == _unit(truth.get("clean_address_line1"), truth.get("clean_address_line2"))
    )


def _format_proportion(numerator: int | float, denominator: int | float) -> str:
    if not denominator:
        return "n/a"
    return f"{numerator:g}/{denominator:g} ({100 * numerator / denominator:.1f}%)"


def _metric(
    section: str,
    name: str,
    numerator: int | float | str,
    denominator: int | str,
    source: str,
    *,
    before: str | None = None,
    after: str | None = None,
    proportion: bool = True,
) -> dict[str, str]:
    if source not in SOURCES:
        raise ValueError(f"Unsupported report source: {source}")
    n = str(numerator)
    d = str(denominator)
    if proportion and after is None:
        try:
            after = _format_proportion(float(n), float(d))
        except ValueError:
            after = f"{n}/{d}"
    elif after is None and d == "0":
        after = "n/a"
    return {
        "section": section,
        "metric": name,
        "before": before if before is not None else "n/a",
        "after": after if after is not None else f"{n}/{d}",
        "numerator": n,
        "denominator": d,
        "source": source,
    }


def _status_failed(value: str | None) -> bool:
    return (value or "").strip().casefold().startswith("failed")


def _is_attempted(value: str | None) -> bool:
    return bool((value or "").strip()) and (value or "").strip().casefold() not in {
        "not_eligible",
        "not_run",
    }


def _risk_rank(tier: str) -> int | None:
    return {"Minimal": 1, "Moderate": 2, "High": 3}.get(tier)


def _append_report_rows(
    rows: list[dict[str, str]],
    section: str,
    path: Path,
    *,
    input_denominator: int,
    actions_report_path: Path = ACTIONS_REPORT_PATH,
    remediation_headlines: bool = False,
) -> None:
    report_rows = _read_csv(path)
    reported_values = {
        row.get("metric") or row.get("status"): row.get("value") or row.get("count")
        for row in report_rows
        if (row.get("metric") or row.get("status"))
    }
    if section == "Remediation" and remediation_headlines:
        _append_remediation_headlines(
            rows,
            reported_values,
            _read_csv(actions_report_path),
            input_denominator,
        )
        return
    for report_row in report_rows:
        metric_name = report_row.get("metric") or report_row.get("status")
        value = report_row.get("value") or report_row.get("count")
        if not metric_name or value is None:
            continue
        if section == "Remediation" and metric_name in {"run_status", "stage"}:
            continue
        denominator = ""
        if section == "AI narratives" and report_row.get("count") is not None:
            denominator = str(input_denominator)
        elif section == "Remediation" and metric_name == "top_answer_correct":
            denominator = reported_values.get("top_answer_total", "")
        elif section == "Remediation" and metric_name == "top_answer_accuracy":
            denominator = reported_values.get("top_answer_total", "")
        elif section == "Remediation" and metric_name.startswith("per_cause_"):
            prefix, _, suffix = metric_name.rpartition("_")
            if suffix in {"correct", "accuracy"}:
                denominator = reported_values.get(f"{prefix}_total", "")
            elif suffix == "total":
                denominator = reported_values.get("labelled_records", "")
        elif section == "Remediation" and metric_name.startswith("confusion_"):
            true_cause = metric_name[len("confusion_"):].split("_to_", 1)[0]
            denominator = reported_values.get(f"per_cause_{true_cause}_total", "")
        elif section == "Remediation" and metric_name in {
            "test_contains_all_true_labels",
            "test_contains_at_least_one_true_label",
            "mean_prediction_set_size",
            "singleton_share",
        }:
            denominator = reported_values.get("test_records", "")
        elif section == "Remediation" and metric_name in {
            "calibration_records",
            "test_records",
        }:
            denominator = reported_values.get("labelled_records", "")
        elif section == "Remediation" and metric_name == "excluded_missing_unit_only":
            labelled = int(reported_values.get("labelled_records", "0") or "0")
            excluded = int(value or "0")
            denominator = str(labelled + excluded)
        elif section == "Remediation" and metric_name == "samples_failed":
            labelled = int(reported_values.get("labelled_records", "0") or "0")
            samples = int(reported_values.get("samples_per_record", "0") or "0")
            denominator = str(labelled * samples)
        elif (
            section == "Remediation"
            and metric_name == "records_with_fewer_than_k_valid_samples"
        ):
            denominator = reported_values.get("labelled_records", "")
        elif section == "Remediation" and metric_name == "labelled_records":
            denominator = str(input_denominator)
        elif section == "Remediation" and metric_name == "top_answer_total":
            denominator = reported_values.get("labelled_records", "")
        elif section == "Remediation" and metric_name == "records":
            denominator = str(input_denominator)
        elif section == "Remediation" and metric_name not in {
            "alpha",
            "q_hat",
            "uninformative_q_hat",
            "samples_per_record",
        }:
            denominator = reported_values.get("records", "")
        if section == "AI narratives" and report_row.get("count") is not None and denominator:
            after = _format_proportion(float(value), float(denominator))
        else:
            after = value
        rows.append(
            {
                "section": section,
                "metric": metric_name,
                "before": "n/a",
                "after": after,
                "numerator": value,
                "denominator": denominator,
                "source": "ai_report",
            }
        )


def _append_remediation_headlines(
    rows: list[dict[str, str]],
    calibration: dict[str, str | None],
    action_report: list[dict[str, str]],
    input_denominator: int,
) -> None:
    if not action_report:
        raise ValueError("Remediation actions summary is empty.")
    actions = {
        row.get("metric", ""): row.get("value", "")
        for row in action_report
    }

    def add(
        name: str,
        numerator: int | float | str,
        denominator: int | str,
        *,
        source: str = "ai_report",
        after: str | None = None,
        proportion: bool = True,
    ) -> None:
        rows.append(
            _metric(
                "Remediation",
                name,
                numerator,
                denominator,
                source,
                after=after,
                proportion=proportion,
            )
        )

    labelled = int(calibration.get("labelled_records") or 0)
    test_count = int(calibration.get("test_records") or 0)
    correct_count = int(calibration.get("top_answer_correct") or 0)
    add("Labelled records", labelled, input_denominator)
    add("Test records", test_count, labelled)
    add("Top-answer accuracy", correct_count, int(calibration.get("top_answer_total") or labelled))

    test_coverage = float(calibration.get("test_contains_all_true_labels") or 0)
    covered_count = round(test_coverage * test_count)
    add(
        "Test coverage (all true labels in set; target 90%)",
        covered_count,
        test_count,
    )
    mean_set_size = calibration.get("mean_prediction_set_size")
    mean_set_size_display = (
        f"{float(mean_set_size):.2f}" if mean_set_size else "n/a"
    )
    add(
        "Mean prediction-set size",
        mean_set_size_display,
        test_count,
        after=mean_set_size_display,
        proportion=False,
    )
    singleton_share = float(calibration.get("singleton_share") or 0)
    add("Singleton share", round(singleton_share * test_count), test_count)

    exceptions = int(actions.get("records") or 0)
    outreach = int(actions.get("action_customer_outreach") or 0)
    analyst = int(actions.get("action_analyst_review") or 0)
    suggestions_made = int(actions.get("suggestions_made") or 0)
    suggestions_valid = int(actions.get("suggestions_valid") or 0)
    verified = int(actions.get("verified") or 0)
    verified_correct = int(actions.get("verified_correct") or 0)
    verified_incorrect = int(actions.get("verified_incorrect") or 0)
    not_verified = int(actions.get("not_verified") or 0)
    no_suggestion = int(actions.get("no_suggestion") or 0)
    outreach_correct = int(actions.get("customer_outreach_correct") or 0)
    add("Exceptions", exceptions, input_denominator)
    add("Customer outreach", outreach, exceptions)
    add("Customer outreach correct", outreach_correct, outreach)
    add("Analyst review", analyst, exceptions)
    add("Suggestions made", suggestions_made, analyst)
    add("Suggestions valid", suggestions_valid, suggestions_made)
    add("Precisely-verified", verified, suggestions_valid)
    add("Verified correct", verified_correct, verified)
    add("Verified incorrect", verified_incorrect, verified)
    add("Not verified", not_verified, suggestions_valid)
    add("No suggestion", no_suggestion, analyst)

    q_hat = calibration.get("q_hat")
    if q_hat is not None:
        add("q-hat", q_hat, "", after=f"{float(q_hat):.2f}", proportion=False)
    rows.append(
        {
            "section": "Remediation",
            "metric": "Additional per-cause and confusion metrics: see reports/remediation_calibration.md",
            "before": "n/a",
            "after": "See source report",
            "numerator": "",
            "denominator": "",
            "source": "ai_report",
        }
    )


def compute_before_after_metrics(
    input_path: Path = INPUT_PATH,
    ground_truth_path: Path = GROUND_TRUTH_PATH,
    results_path: Path = RESULTS_PATH,
    narrative_report_path: Path = NARRATIVE_REPORT_PATH,
    calibration_report_path: Path = CALIBRATION_REPORT_PATH,
    actions_report_path: Path = ACTIONS_REPORT_PATH,
    *,
    ledger: CreditLedger | None = None,
    dashboard_credits_left: float | None = None,
    profile: ClientProfile | None = None,
) -> list[dict[str, str]]:
    if profile is not None:
        input_path = profile.input_path
        ground_truth_path = profile.ground_truth_path
        results_path = profile.run_dir / "results_rowlevel.csv"
        narrative_report_path = profile.reports_dir / "ai_narrative_summary.csv"
        calibration_report_path = profile.reports_dir / "remediation_calibration.csv"
        actions_report_path = profile.reports_dir / "remediation_actions.csv"
        policies = read_client_policies(profile)
    else:
        policies = _read_csv(input_path)
    has_ground_truth = ground_truth_path is not None
    truths = _read_csv(ground_truth_path) if has_ground_truth else []
    results = _read_csv(results_path)
    truth_by_id = {row["policy_id"]: row for row in truths}
    result_by_id = {row["policy_id"]: row for row in results}
    total = len(policies)
    rows: list[dict[str, str]] = []

    input_ids = [row["policy_id"] for row in policies]
    duplicate_count = len(input_ids) - len(set(input_ids))
    rows.extend(
        [
            _metric("Data integrity", "Input rows", total, total, "input"),
            _metric(
                "Data integrity",
                "Output rows",
                len(results),
                total,
                "precisely_api",
            ),
            _metric(
                "Data integrity",
                "Dropped rows",
                max(0, total - len(results)),
                total,
                "precisely_api",
            ),
            _metric(
                "Data integrity",
                "Duplicate identifiers",
                duplicate_count,
                total,
                "input",
            ),
        ]
    )
    status_columns = sorted(
        {
            column
            for record in results
            for column in record
            if column.endswith("_status")
        }
    )
    for column in status_columns:
        attempted = [record for record in results if _is_attempted(record.get(column))]
        failures = sum(_status_failed(record.get(column)) for record in attempted)
        safe_name = _SAFE_STATUS_NAMES.get(column, column.removesuffix("_status"))
        rows.append(
            _metric(
                "Data integrity",
                f"{safe_name} failures",
                failures,
                len(attempted),
                "precisely_api",
            )
        )

    eligible = [
        row
        for row in results
        if (row.get("precision_code") or "").strip().upper().startswith(
            ("S5", "S7", "S8")
        )
        and row.get("eligibility") == "eligible"
    ]
    manual = [row for row in results if row.get("eligibility") == "manual_review"]
    rows.extend(
        [
            _metric("Verification", "Eligible", len(eligible), total, "precisely_api"),
            _metric(
                "Verification",
                "Manual review",
                len(manual),
                total,
                "precisely_api",
            ),
        ]
    )
    manual_reasons = sorted(
        {row.get("manual_review_reason", "") for row in manual if row.get("manual_review_reason")}
    )
    for reason in manual_reasons:
        count = sum(row.get("manual_review_reason") == reason for row in manual)
        rows.append(
            _metric(
                "Verification",
                f"Manual review: {reason}",
                count,
                total,
                "precisely_api",
            )
        )

    paired = [
        (policy, truth_by_id[policy["policy_id"]], result_by_id.get(policy["policy_id"]))
        for policy in policies
        if policy["policy_id"] in truth_by_id
    ]
    correctness_groups: dict[str, list[tuple[dict[str, str], dict[str, str], dict[str, str] | None]]] = (
        {
            "all records": paired,
            "clean records": [
                item for item in paired if not (item[1].get("defects") or "").strip()
            ],
            "dirty records": [
                item for item in paired if (item[1].get("defects") or "").strip()
            ],
        }
        if has_ground_truth
        else {}
    )
    defect_types = sorted(
        {
            label.strip()
            for _, truth, _ in paired
            for label in re.split(r"[;,|]", truth.get("defects", ""))
            if label.strip()
        }
    )
    for defect in defect_types:
        correctness_groups[f"defect: {defect}"] = [
            item
            for item in paired
            if defect
            in {
                label.strip()
                for label in re.split(r"[;,|]", item[1].get("defects", ""))
                if label.strip()
            }
        ]
    for group_name, group in correctness_groups.items():
        denominator = len(group)
        before_count = sum(
            _is_correct_building(
                policy.get("raw_address_line1"),
                policy.get("raw_zip5"),
                truth,
            )
            for policy, truth, _ in group
        )
        after_count = sum(
            _is_correct_building(
                result.get("standardized_address_line1") if result else "",
                result.get("standardized_zip5") if result else "",
                truth,
            )
            for _, truth, result in group
        )
        rows.append(
            _metric(
                "Standardization vs ground truth",
                f"Correct building: {group_name}",
                after_count,
                denominator,
                "ground_truth",
                before=_format_proportion(before_count, denominator),
                after=_format_proportion(after_count, denominator),
            )
        )
        before_full = sum(
            _fully_correct(
                policy.get("raw_address_line1"),
                policy.get("raw_address_line2"),
                policy.get("raw_zip5"),
                truth,
            )
            for policy, truth, _ in group
        )
        after_full = sum(
            _fully_correct(
                result.get("standardized_address_line1") if result else "",
                result.get("standardized_address_line2") if result else "",
                result.get("standardized_zip5") if result else "",
                truth,
            )
            for _, truth, result in group
        )
        rows.append(
            _metric(
                "Standardization vs ground truth",
                f"Fully correct (incl. unit): {group_name}",
                after_full,
                denominator,
                "ground_truth",
                before=_format_proportion(before_full, denominator),
                after=_format_proportion(after_full, denominator),
            )
        )

    city_groups = (
        {
            "all records": paired,
            "clean records": correctness_groups["clean records"],
            "dirty records": correctness_groups["dirty records"],
            "defect: city_typo": correctness_groups.get("defect: city_typo", []),
        }
        if has_ground_truth
        else {}
    )
    for group_name, group in city_groups.items():
        before_count = sum(
            _canonical_city(policy.get("raw_city"))
            == _canonical_city(truth.get("clean_city"))
            for policy, truth, _ in group
        )
        after_count = sum(
            result is not None
            and _canonical_city(result.get("standardized_city"))
            == _canonical_city(truth.get("clean_city"))
            for _, truth, result in group
        )
        rows.append(
            _metric(
                "Standardization vs ground truth",
                f"City name matches reference: {group_name}",
                after_count,
                len(group),
                "ground_truth",
                before=_format_proportion(before_count, len(group)),
                after=_format_proportion(after_count, len(group)),
            )
        )

    for group_name in (
        ("all records", "clean records", "dirty records")
        if has_ground_truth
        else ()
    ):
        group = correctness_groups[group_name]
        before_count = sum(
            _correct_street_city(
                policy.get("raw_address_line1"),
                policy.get("raw_city"),
                truth,
            )
            and _zip5(policy.get("raw_zip5")) != _zip5(truth.get("clean_zip5", ""))
            for policy, truth, _ in group
        )
        after_count = sum(
            result is not None
            and _correct_street_city(
                result.get("standardized_address_line1"),
                result.get("standardized_city"),
                truth,
            )
            and _zip5(result.get("standardized_zip5")) != _zip5(truth.get("clean_zip5", ""))
            for _, truth, result in group
        )
        rows.append(
            _metric(
                "Standardization vs ground truth",
                f"Same street address, ZIP missing or different from reference: {group_name}",
                after_count,
                len(group),
                "ground_truth",
                before=_format_proportion(before_count, len(group)),
                after=_format_proportion(after_count, len(group)),
            )
        )
    if not has_ground_truth:
        rows.append(
            {
                "section": "Availability",
                "metric": (
                    "Not available: no reference data supplied for this client. "
                    "Accuracy against a reference needs a labelled sample."
                ),
                "before": "n/a",
                "after": "n/a",
                "numerator": "",
                "denominator": "",
                "source": "input",
            }
        )

    building_coordinates = sum(
        row.get("precision_code", "").upper().startswith(("S5", "S7", "S8"))
        and _valid_coordinate(row.get("latitude", ""), row.get("longitude", ""))
        for row in results
    )
    coarse_coordinates = sum(
        row.get("precision_code", "").upper().startswith(("Z", "G"))
        and _valid_coordinate(row.get("latitude", ""), row.get("longitude", ""))
        for row in results
    )
    street_coordinates = sum(
        row.get("precision_code", "").upper().startswith("S4")
        and _valid_coordinate(row.get("latitude", ""), row.get("longitude", ""))
        for row in results
    )
    rows.extend(
        [
            _metric(
                "Coordinate coverage",
                "Building-level coordinates (S5/S7/S8; includes 1 record held for state conflict)",
                building_coordinates,
                total,
                "precisely_api",
            ),
            _metric(
                "Coordinate coverage",
                "Street-level coordinates only",
                street_coordinates,
                total,
                "precisely_api",
            ),
            _metric(
                "Coordinate coverage",
                "City/postal-level coordinates only",
                coarse_coordinates,
                total,
                "precisely_api",
            ),
        ]
    )
    id_attempted = [
        row for row in results if _is_attempted(row.get("preciselyid_status"))
    ]
    id_count = sum(bool((row.get("preciselyid") or "").strip()) for row in id_attempted)
    rows.append(
        _metric(
            "PreciselyID coverage over attempted",
            "PreciselyID over attempted",
            id_count,
            len(id_attempted),
            "precisely_api",
        )
    )

    enrichment_fields = (
        "flood_zone_code",
        "flood_risk_level",
        "bfe",
        "distance_to_water",
        "built_year",
    )
    for field in enrichment_fields:
        present = lambda record: bool((record.get(field) or "").strip())
        rows.append(
            _metric(
                "Enrichment completeness",
                f"{field} over eligible",
                sum(present(record) for record in eligible),
                len(eligible),
                "precisely_api",
            )
        )
        rows.append(
            _metric(
                "Enrichment completeness",
                f"{field} over all input",
                sum(present(record) for record in results),
                total,
                "precisely_api",
            )
        )

    compared = [
        row
        for row in results
        if row.get("compare_status") == "api"
        and row.get("flood_zone_code")
        and row.get("comparison_flood_zone_code")
    ]
    base_changed = 0
    tier_changed = 0
    understates = 0
    overstates = 0
    distances: list[float] = []
    for record in compared:
        verified_base = flood_zone_base(record.get("flood_zone_code"))
        coarse_base = flood_zone_base(record.get("comparison_flood_zone_code"))
        base_changed += verified_base != coarse_base
        verified_tier = risk_tier(verified_base)
        coarse_tier = risk_tier(coarse_base)
        if verified_tier != coarse_tier:
            tier_changed += 1
            verified_rank = _risk_rank(verified_tier)
            coarse_rank = _risk_rank(coarse_tier)
            if verified_rank is not None and coarse_rank is not None:
                if coarse_rank < verified_rank:
                    understates += 1
                elif coarse_rank > verified_rank:
                    overstates += 1
        try:
            distance = float(record.get("comparison_distance_km", ""))
        except ValueError:
            continue
        if distance >= 0:
            distances.append(distance)
    rows.extend(
        [
            _metric("Coarse vs verified location (Risk 2)", "Records compared", len(compared), total, "precisely_api"),
            _metric("Coarse vs verified location (Risk 2)", "Flood-zone base changed", base_changed, len(compared), "precisely_api"),
            _metric("Coarse vs verified location (Risk 2)", "Flood-risk tier changed", tier_changed, len(compared), "precisely_api"),
            _metric("Coarse vs verified location (Risk 2)", "Coarse location understated verified risk", understates, len(compared), "precisely_api"),
            _metric("Coarse vs verified location (Risk 2)", "Coarse location overstated verified risk", overstates, len(compared), "precisely_api"),
            _metric(
                "Coarse vs verified location (Risk 2)",
                "Median comparison distance km",
                f"{statistics.median(distances):.3f}" if distances else "n/a",
                len(distances),
                "precisely_api",
                after=f"{statistics.median(distances):.3f}" if distances else "n/a",
            ),
        ]
    )
    _append_report_rows(
        rows,
        "AI narratives",
        narrative_report_path,
        input_denominator=total,
    )
    _append_report_rows(
        rows,
        "Remediation",
        calibration_report_path,
        input_denominator=total,
        actions_report_path=actions_report_path,
        remediation_headlines=True,
    )

    active_ledger = ledger or CreditLedger(Decimal("2150"))
    credits_used = active_ledger.cumulative_spent
    credits_left = Decimal("2500") - credits_used
    rows.append(
        _metric(
            "Credits",
            "Ledger credits used",
            str(credits_used),
            "2,500",
            "ledger",
            before="n/a",
            after=str(credits_used),
        )
    )
    rows.append(
        _metric(
            "Credits",
            "Ledger credits left",
            str(credits_left),
            "2,500",
            "ledger",
            before="n/a",
            after=str(credits_left),
        )
    )
    if dashboard_credits_left is not None:
        rows.append(
            _metric(
                "Credits",
                "Dashboard credits left",
                str(dashboard_credits_left),
                "2,500",
                "ledger",
                before="n/a",
                after=f"{dashboard_credits_left:.2f}",
            )
        )

    address_values = {
        value.strip()
        for policy in policies
        for key, value in policy.items()
        if key.startswith("raw_") and value.strip()
    }
    _validate_privacy(rows, address_values)
    return rows


def _valid_coordinate(latitude: str | None, longitude: str | None) -> bool:
    try:
        lat = float(latitude or "")
        lon = float(longitude or "")
    except ValueError:
        return False
    return -90 <= lat <= 90 and -180 <= lon <= 180


def _validate_privacy(
    rows: Iterable[dict[str, str]],
    address_values: Iterable[str] = (),
) -> None:
    address_values = {value.casefold() for value in address_values}
    for row in rows:
        if tuple(row) != OUTPUT_COLUMNS:
            raise ValueError("Metrics output has an unexpected CSV header.")
        for value in row.values():
            if (
                _POLICY_ID.search(value)
                or _COORDINATE_LIKE.search(value)
                or value.casefold() in address_values
            ):
                raise ValueError("Metrics output contains a forbidden private field.")


def write_before_after_report(
    rows: list[dict[str, str]],
    csv_path: Path = CSV_OUTPUT_PATH,
    markdown_path: Path = MD_OUTPUT_PATH,
    address_values: Iterable[str] = (),
) -> None:
    address_values = set(address_values)
    _validate_privacy(rows, address_values)
    _write_csv(csv_path, rows)
    with csv_path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != ALLOWED_OUTPUT_COLUMNS:
            raise ValueError("Metrics CSV has an unexpected header.")
        _validate_privacy(list(reader), address_values)
    sections: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        sections.setdefault(row["section"], []).append(row)
    lines = [
        "# Before/after summary",
        "",
        "aggregates only; row-level Precisely outputs are not published (AUP §4.1–4.2)",
        "Before/after counts use the input population as the top-level denominator.",
        "",
    ]
    for section, section_rows in sections.items():
        lines.extend(
            [
                f"## {section}",
                "",
                "| Metric | Before | After | Numerator | Denominator | Source |",
                "| --- | ---: | ---: | ---: | ---: | --- |",
            ]
        )
        for row in section_rows:
            cells = [
                row["metric"],
                row["before"],
                row["after"],
                row["numerator"],
                row["denominator"],
                row["source"],
            ]
            lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
        if section == "Standardization vs ground truth":
            lines.extend(
                [
                    "",
                    "City is reported separately, not as part of the building test: street + ZIP identify the building, and official place names vary (for 2 clean records Precisely returns the municipality, Coral Gables, where the reference uses the mailing city, Miami).",
                ]
            )
        lines.append("")
    lines.extend(
        [
            "## Limitations",
            "",
            "- Synthetic data; the 38% defect rate is by design.",
            "- Results cover 200 records and one run.",
            "",
        ]
    )
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text("\n".join(lines), encoding="utf-8")


def build_before_after_report(
    *,
    dashboard_credits_left: float | None = None,
    input_path: Path = INPUT_PATH,
    ground_truth_path: Path = GROUND_TRUTH_PATH,
    results_path: Path = RESULTS_PATH,
    narrative_report_path: Path = NARRATIVE_REPORT_PATH,
    calibration_report_path: Path = CALIBRATION_REPORT_PATH,
    actions_report_path: Path = ACTIONS_REPORT_PATH,
    csv_path: Path = CSV_OUTPUT_PATH,
    markdown_path: Path = MD_OUTPUT_PATH,
    ledger: CreditLedger | None = None,
    profile: ClientProfile | None = None,
) -> list[dict[str, str]]:
    if profile is not None:
        input_path = profile.input_path
        ground_truth_path = profile.ground_truth_path
        results_path = profile.run_dir / "results_rowlevel.csv"
        narrative_report_path = profile.reports_dir / "ai_narrative_summary.csv"
        calibration_report_path = profile.reports_dir / "remediation_calibration.csv"
        actions_report_path = profile.reports_dir / "remediation_actions.csv"
        csv_path = profile.reports_dir / "before_after_summary.csv"
        markdown_path = profile.reports_dir / "before_after_summary.md"
    rows = compute_before_after_metrics(
        input_path,
        ground_truth_path,
        results_path,
        narrative_report_path,
        calibration_report_path,
        actions_report_path,
        ledger=ledger,
        dashboard_credits_left=dashboard_credits_left,
        profile=profile,
    )
    source_policies = (
        read_client_policies(profile) if profile is not None else _read_csv(input_path)
    )
    address_values = {
        value.strip()
        for policy in source_policies
        for key, value in policy.items()
        if (key.startswith("raw_") or key in {"city", "state", "zip5"})
        and value.strip()
    }
    write_before_after_report(rows, csv_path, markdown_path, address_values)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build aggregate-only before/after address-intelligence metrics."
    )
    parser.add_argument("--dashboard-credits-left", type=float)
    parser.add_argument("--profile", type=Path, default=None)
    args = parser.parse_args(argv)
    profile = load_client_profile(args.profile)
    rows = build_before_after_report(
        dashboard_credits_left=args.dashboard_credits_left,
        profile=profile,
    )
    sections: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        sections.setdefault(row["section"], []).append(row)
    print("aggregates only; row-level Precisely outputs are not published (AUP §4.1–4.2)")
    print(
        f"Wrote {profile.reports_dir / 'before_after_summary.csv'} and "
        f"{profile.reports_dir / 'before_after_summary.md'}."
    )
    for section, section_rows in sections.items():
        print(f"\n[{section}]")
        for row in section_rows:
            print(
                f"{row['metric']}: before={row['before']}; after={row['after']}; "
                f"n={row['numerator']}; denominator={row['denominator']}; "
                f"source={row['source']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
