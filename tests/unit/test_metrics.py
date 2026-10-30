import csv
from decimal import Decimal
import pytest

from address_intelligence.credits import CreditLedger
from address_intelligence.metrics import (
    OUTPUT_COLUMNS,
    _canonical_city,
    _canonical_line1,
    _fully_correct,
    _is_correct_building,
    _validate_privacy,
    build_before_after_report,
    compute_before_after_metrics,
)


def _write(path, columns, rows):
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(
            destination, fieldnames=columns, lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def _fixture(tmp_path, *, no_eligible=False):
    policies_path = tmp_path / "input.csv"
    truth_path = tmp_path / "truth.csv"
    results_path = tmp_path / "results.csv"
    narrative_path = tmp_path / "narrative.csv"
    calibration_path = tmp_path / "calibration.csv"
    actions_path = tmp_path / "actions.csv"
    policies = [
        {
            "policy_id": "P1",
            "raw_address_line1": "1141 NAPOLEON AVE APT 3",
            "raw_address_line2": "",
            "raw_city": "New Orleans",
            "raw_zip5": "70115",
        },
        {
            "policy_id": "P2",
            "raw_address_line1": "Wrong St",
            "raw_address_line2": "",
            "raw_city": "Wrong City",
            "raw_zip5": "00000",
        },
        {
            "policy_id": "P3",
            "raw_address_line1": "8 Oak Rd",
            "raw_address_line2": "",
            "raw_city": "Oak City",
            "raw_zip5": "12345",
        },
    ]
    truths = [
        {
            "policy_id": "P1",
            "clean_address_line1": "1141 Napoleon Ave",
            "clean_address_line2": "Apt 3",
            "clean_city": "New Orleans",
            "clean_zip5": "70115",
            "defects": "",
        },
        {
            "policy_id": "P2",
            "clean_address_line1": "1141 Napoleon Ave",
            "clean_address_line2": "",
            "clean_city": "New Orleans",
            "clean_zip5": "70115",
            "defects": "street_typo;wrong_zip",
        },
        {
            "policy_id": "P3",
            "clean_address_line1": "8 Oak Rd",
            "clean_address_line2": "",
            "clean_city": "Oak City",
            "clean_zip5": "12345",
            "defects": "",
        },
    ]
    eligible = "manual_review" if no_eligible else "eligible"
    results = [
        {
            "policy_id": "P1",
            "standardized_address_line1": "1141 NAPOLEON AVE APT 3",
            "standardized_address_line2": "",
            "standardized_city": "NEW ORLEANS",
            "standardized_zip5": "70115",
            "precision_code": "S8",
            "eligibility": eligible,
            "manual_review_reason": "street-level only" if no_eligible else "",
            "geocode_status": "api",
            "preciselyid_status": "api",
            "preciselyid": "private",
            "latitude": "29.9",
            "longitude": "-90.1",
            "flood_zone_code": "X",
            "flood_risk_level": "Minimal",
            "bfe": "",
            "distance_to_water": "0.5",
            "built_year": "1980",
            "compare_status": "api",
            "comparison_flood_zone_code": "A1" if not no_eligible else "",
            "comparison_distance_km": "1.4" if not no_eligible else "",
        },
        {
            "policy_id": "P2",
            "standardized_address_line1": "1141 Napoleon Ave",
            "standardized_address_line2": "",
            "standardized_city": "New Orleans",
            "standardized_zip5": "70115",
            "precision_code": "Z1",
            "eligibility": "manual_review",
            "manual_review_reason": "postal or city level",
            "geocode_status": "api",
            "preciselyid_status": "not_eligible",
            "preciselyid": "",
            "latitude": "",
            "longitude": "",
            "flood_zone_code": "",
            "flood_risk_level": "",
            "bfe": "",
            "distance_to_water": "",
            "built_year": "",
            "compare_status": "not_eligible",
            "comparison_flood_zone_code": "",
            "comparison_distance_km": "",
        },
        {
            "policy_id": "P3",
            "standardized_address_line1": "8 Oak Rd",
            "standardized_address_line2": "",
            "standardized_city": "Oak City",
            "standardized_zip5": "12345",
            "precision_code": "S5",
            "eligibility": eligible,
            "manual_review_reason": "",
            "geocode_status": "api",
            "preciselyid_status": "api",
            "preciselyid": "",
            "latitude": "29.9",
            "longitude": "-90.1",
            "flood_zone_code": "A1",
            "flood_risk_level": "High",
            "bfe": "13",
            "distance_to_water": "1.0",
            "built_year": "",
            "compare_status": "api",
            "comparison_flood_zone_code": "X",
            "comparison_distance_km": "2.0",
        },
    ]
    _write(policies_path, list(policies[0]), policies)
    _write(truth_path, list(truths[0]), truths)
    _write(results_path, list(results[0]), results)
    _write(narrative_path, ("status", "guardrail_reason", "count"), [
        {"status": "generated", "guardrail_reason": "", "count": "2"}
    ])
    _write(
        calibration_path,
        ("metric", "value"),
        [
            {"metric": "labelled_records", "value": "2"},
            {"metric": "test_records", "value": "34"},
            {"metric": "top_answer_correct", "value": "2"},
            {"metric": "top_answer_total", "value": "2"},
            {"metric": "test_contains_all_true_labels", "value": "0.9411764705882353"},
            {"metric": "mean_prediction_set_size", "value": "1.0"},
            {"metric": "singleton_share", "value": "1.0"},
            {"metric": "q_hat", "value": "0.8"},
        ],
    )
    _write(
        actions_path,
        ("metric", "value"),
        [
            {"metric": "records", "value": "1"},
            {"metric": "action_customer_outreach", "value": "0"},
            {"metric": "action_analyst_review", "value": "1"},
            {"metric": "suggestions_made", "value": "1"},
            {"metric": "suggestions_valid", "value": "1"},
            {"metric": "verified", "value": "1"},
            {"metric": "verified_correct", "value": "1"},
            {"metric": "verified_incorrect", "value": "0"},
            {"metric": "not_verified", "value": "0"},
            {"metric": "no_suggestion", "value": "0"},
            {"metric": "customer_outreach_correct", "value": "0"},
        ],
    )
    return {
        "input_path": policies_path,
        "ground_truth_path": truth_path,
        "results_path": results_path,
        "narrative_report_path": narrative_path,
        "calibration_report_path": calibration_path,
        "actions_report_path": actions_path,
    }


def _find(rows, section, metric):
    return next(
        row for row in rows
        if row["section"] == section and row["metric"] == metric
    )


def test_three_row_fixture_denominators_and_before_after(tmp_path):
    paths = _fixture(tmp_path)
    rows = compute_before_after_metrics(**paths)
    all_correct = _find(
        rows,
        "Standardization vs ground truth",
        "Correct building: all records",
    )
    assert all_correct["before"] == "2/3 (66.7%)"
    assert all_correct["after"] == "3/3 (100.0%)"
    assert all_correct["numerator"] == "3"
    assert all_correct["denominator"] == "3"
    assert _find(rows, "Verification", "Eligible")["after"] == "2/3 (66.7%)"
    assert _find(
        rows, "Enrichment completeness", "built_year over eligible"
    )["after"] == "1/2 (50.0%)"
    narrative = _find(
        rows,
        "AI narratives",
        "generated",
    )
    assert narrative["after"] == "2/3 (66.7%)"
    assert narrative["denominator"] == "3"
    assert _find(
        rows,
        "Coarse vs verified location (Risk 2)",
        "Median comparison distance km",
    )["after"] == "1.700"


def test_zero_eligible_uses_na_denominator_safely(tmp_path):
    paths = _fixture(tmp_path, no_eligible=True)
    rows = compute_before_after_metrics(**paths)
    metric = _find(rows, "Enrichment completeness", "bfe over eligible")
    assert metric["after"] == "n/a"
    assert metric["denominator"] == "0"


def test_privacy_guard_on_written_csv_and_report_sources(tmp_path):
    paths = _fixture(tmp_path)
    ledger = CreditLedger(Decimal("2150"), path=tmp_path / "ledger.json")
    ledger.record_success("geocode_premium", 1)
    csv_path = tmp_path / "summary.csv"
    md_path = tmp_path / "summary.md"
    build_before_after_report(
        **paths,
        ledger=ledger,
        dashboard_credits_left=123.5,
        csv_path=csv_path,
        markdown_path=md_path,
    )
    contents = csv_path.read_text(encoding="utf-8").casefold()
    for forbidden in (
        "policy_id",
        "private",
    ):
        assert forbidden not in contents
    with csv_path.open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    assert tuple(rows[0]) == OUTPUT_COLUMNS
    assert any(row["metric"] == "PreciselyID over attempted" for row in rows)
    assert any(row["metric"] == "Dashboard credits left" for row in rows)
    assert next(
        row["after"] for row in rows if row["metric"] == "Dashboard credits left"
    ) == "123.50"
    assert "aggregates only; row-level Precisely outputs are not published" in (
        md_path.read_text(encoding="utf-8")
    )
    assert (
        "City is reported separately, not as part of the building test: street + ZIP "
        "identify the building, and official place names vary (for 2 clean records "
        "Precisely returns the municipality, Coral Gables, where the reference uses "
        "the mailing city, Miami)."
    ) in md_path.read_text(encoding="utf-8")


def test_directional_and_saint_tokens_canonicalize():
    assert _canonical_line1("123 EAST SAINT JOHN ST") == _canonical_line1(
        "123 E ST JOHN ST"
    )


def test_saint_and_st_city_names_are_equivalent():
    assert _canonical_city("St. John") == _canonical_city("Saint John")


def test_city_typo_does_not_affect_correct_building(tmp_path):
    paths = _fixture(tmp_path)
    with paths["input_path"].open(newline="", encoding="utf-8") as source:
        policies = list(csv.DictReader(source))
    policies[2]["raw_city"] = "Wrong City"
    _write(paths["input_path"], list(policies[0]), policies)
    rows = compute_before_after_metrics(**paths)
    building = _find(
        rows,
        "Standardization vs ground truth",
        "Correct building: clean records",
    )
    city = _find(
        rows,
        "Standardization vs ground truth",
        "City name matches reference: clean records",
    )
    assert building["before"] == "2/2 (100.0%)"
    assert city["before"] == "1/2 (50.0%)"


def test_zip_difference_metric_requires_street_and_city_match(tmp_path):
    paths = _fixture(tmp_path)
    with paths["results_path"].open(newline="", encoding="utf-8") as source:
        results = list(csv.DictReader(source))
    results[1]["standardized_address_line1"] = "1141 Napoleon Ave"
    results[1]["standardized_city"] = "New Orleans"
    results[1]["standardized_zip5"] = "70114"
    _write(paths["results_path"], list(results[0]), results)
    rows = compute_before_after_metrics(**paths)
    metric = _find(
        rows,
        "Standardization vs ground truth",
        "Same street address, ZIP differs from reference: dirty records",
    )
    assert metric["after"] == "1/1 (100.0%)"


def test_unit_only_difference_is_building_correct_but_not_fully_correct():
    truth = {
        "clean_address_line1": "1141 Napoleon Ave",
        "clean_address_line2": "Apt 3",
        "clean_city": "New Orleans",
        "clean_zip5": "70115",
    }
    assert _is_correct_building("1141 Napoleon Ave APT 4", "70115", truth)
    assert not _fully_correct(
        "1141 Napoleon Ave APT 4", "", "70115", truth
    )


def test_coordinate_categories_sum_to_input_total(tmp_path):
    paths = _fixture(tmp_path)
    with paths["results_path"].open(newline="", encoding="utf-8") as source:
        results = list(csv.DictReader(source))
    results[1]["latitude"] = "29.9"
    results[1]["longitude"] = "-90.1"
    results[2]["precision_code"] = "S4"
    _write(paths["results_path"], list(results[0]), results)
    rows = compute_before_after_metrics(**paths)
    coordinates = [
        row for row in rows
        if row["section"] == "Coordinate coverage"
    ]
    assert [row["numerator"] for row in coordinates] == ["1", "1", "1"]
    assert sum(int(row["numerator"]) for row in coordinates) == 3


def test_remediation_test_coverage_uses_all_true_labels(tmp_path):
    rows = compute_before_after_metrics(**_fixture(tmp_path))
    coverage = _find(
        rows,
        "Remediation",
        "Test coverage (all true labels in set; target 90%)",
    )
    assert coverage["numerator"] == "32"
    assert coverage["denominator"] == "34"
    assert coverage["after"] == "32/34 (94.1%)"


@pytest.mark.parametrize(
    "value",
    ["POL-000001", "-90.12345", "1141 NAPOLEON AVE"],
)
def test_privacy_guard_rejects_sensitive_cell_values(value):
    row = {
        "section": "Test",
        "metric": "metric",
        "before": "n/a",
        "after": value,
        "numerator": "0",
        "denominator": "1",
        "source": "input",
    }
    with pytest.raises(ValueError, match="forbidden private"):
        _validate_privacy([row], {"1141 NAPOLEON AVE"})


def test_privacy_guard_rejects_unexpected_csv_header():
    row = {
        "section": "Test",
        "metric": "metric",
        "before": "n/a",
        "after": "0/1",
        "numerator": "0",
        "denominator": "1",
        "source": "input",
        "latitude": "",
    }
    with pytest.raises(ValueError, match="unexpected CSV header"):
        _validate_privacy([row])
