import csv

import pytest
from openpyxl import load_workbook

from address_intelligence.workbook import (
    ENRICHED_COLUMNS,
    PUBLIC_BANNER,
    SHEET_NAMES,
    VALIDATED_COLUMNS,
    build_workbooks,
)


def _write_csv(path, columns, rows):
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _fixture(tmp_path, *, with_results=False):
    input_path = tmp_path / "input.csv"
    truth_path = tmp_path / "truth.csv"
    results_path = tmp_path / "results.csv"
    summary_path = tmp_path / "summary.csv"
    public_path = tmp_path / "reports" / "before_after_workbook.xlsx"
    private_root = tmp_path / "data" / "output"
    private_path = private_root / "run" / "before_after_workbook_PRIVATE.xlsx"
    policies = [
        {
            "policy_id": "SYN-1",
            "raw_address_line1": "10 Main St",
            "raw_address_line2": "",
            "raw_city": "Sample City",
            "raw_state": "ST",
            "raw_zip5": "12345",
        },
        {
            "policy_id": "SYN-2",
            "raw_address_line1": "20 Oak Ave",
            "raw_address_line2": "Apt 2",
            "raw_city": "Sample City",
            "raw_state": "ST",
            "raw_zip5": "54321",
        },
    ]
    truths = [
        {
            "policy_id": "SYN-1",
            "clean_address_line1": "10 Main St",
            "clean_address_line2": "",
            "clean_city": "Sample City",
            "clean_zip5": "12345",
        },
        {
            "policy_id": "SYN-2",
            "clean_address_line1": "20 Oak Ave",
            "clean_address_line2": "Apt 2",
            "clean_city": "Sample City",
            "clean_zip5": "54321",
        },
    ]
    _write_csv(input_path, list(policies[0]), policies)
    _write_csv(truth_path, list(truths[0]), truths)
    _write_csv(
        summary_path,
        ["section", "metric", "before", "after", "numerator", "denominator", "source"],
        [
            {"section": "Verification", "metric": "Eligible", "before": "n/a", "after": "1/2", "numerator": "1", "denominator": "2", "source": "precisely_api"},
            {"section": "Data integrity", "metric": "Input rows", "before": "n/a", "after": "2/2", "numerator": "2", "denominator": "2", "source": "input"},
        ],
    )
    if with_results:
        results = [
            {
                "policy_id": "SYN-1",
                "standardized_address_line1": "10 Main St",
                "standardized_address_line2": "",
                "standardized_city": "Sample City",
                "standardized_state": "ST",
                "standardized_zip5": "12345",
                "precision_code": "S5",
                "eligibility": "eligible",
                "manual_review_reason": "",
                "latitude": "1.0",
                "longitude": "2.0",
                "preciselyid": "PRIVATE-ID-1",
                "geocode_status": "api",
                "correct_building": "",
                "fully_correct": "",
                "flood_zone_code": "X",
                "flood_risk_level": "Minimal",
                "bfe": "",
                "bfe_unit": "",
                "distance_to_water": "0.5",
                "distance_to_water_unit": "MILES",
                "water_body_name": "Private water body",
                "built_year": "1980",
                "comparison_flood_zone_code": "A",
                "comparison_flood_zones_differ": "true",
                "comparison_distance_km": "1.2",
            },
            {
                "policy_id": "SYN-2",
                "standardized_address_line1": "20 Oak Ave",
                "standardized_address_line2": "Apt 3",
                "standardized_city": "Sample City",
                "standardized_state": "ST",
                "standardized_zip5": "54321",
                "precision_code": "S8",
                "eligibility": "eligible",
                "manual_review_reason": "",
                "latitude": "3.0",
                "longitude": "4.0",
                "preciselyid": "PRIVATE-ID-2",
                "geocode_status": "api",
                "correct_building": "",
                "fully_correct": "",
                "flood_zone_code": "A",
                "flood_risk_level": "High",
                "bfe": "12",
                "bfe_unit": "FEET",
                "distance_to_water": "0.8",
                "distance_to_water_unit": "MILES",
                "water_body_name": "Private water body 2",
                "built_year": "1990",
                "comparison_flood_zone_code": "X",
                "comparison_flood_zones_differ": "true",
                "comparison_distance_km": "2.1",
            },
        ]
        _write_csv(results_path, list(results[0]), results)
    return {
        "input_path": input_path,
        "ground_truth_path": truth_path,
        "results_path": results_path,
        "summary_path": summary_path,
        "public_path": public_path,
        "private_path": private_path,
        "data_output_root": private_root,
    }


def test_public_workbook_builds_without_results_and_has_only_example_rows(tmp_path):
    paths = _fixture(tmp_path)
    assert not paths["results_path"].exists()
    counts = build_workbooks(**paths)
    assert counts["PUBLIC"]["Raw"] == 2
    assert counts["PUBLIC"]["Validated"] == 3
    assert counts["PUBLIC"]["Enriched"] == 3
    assert counts["PUBLIC"]["Summary stats"] == 2
    assert counts["PRIVATE"] is None

    workbook = load_workbook(paths["public_path"], data_only=False)
    assert tuple(workbook.sheetnames) == SHEET_NAMES
    validated = workbook["Validated"]
    enriched = workbook["Enriched"]
    assert tuple(cell.value for cell in validated[2]) == VALIDATED_COLUMNS
    assert tuple(cell.value for cell in enriched[2]) == ENRICHED_COLUMNS
    assert validated["A1"].value == PUBLIC_BANNER
    assert validated["A1"].font.italic
    assert [validated.cell(row, 1).value for row in (3, 4, 5)] == [
        "EXAMPLE-1",
        "EXAMPLE-2",
        "EXAMPLE-3",
    ]
    assert [enriched.cell(row, 1).value for row in (3, 4, 5)] == [
        "EXAMPLE-1",
        "EXAMPLE-2",
        "EXAMPLE-3",
    ]
    assert all(validated.cell(row, 10).value == 0 for row in (3, 4, 5))
    assert all(validated.cell(row, 11).value == 0 for row in (3, 4, 5))
    about_text = " ".join(
        str(cell.value or "")
        for row in workbook["About"].iter_rows()
        for cell in row
    )
    assert "§4.1" in about_text
    assert "before_after_workbook_PRIVATE.xlsx" in about_text
    raw = workbook["Raw"]
    assert raw.max_row == 3
    assert workbook["Summary stats"].max_row == 3
    assert workbook["Validated"].freeze_panes == "A3"
    assert workbook["Validated"].auto_filter.ref.startswith("A2:")
    assert not [
        cell.coordinate
        for sheet in workbook.worksheets
        for row in sheet.iter_rows()
        for cell in row
        if cell.data_type == "f"
    ]


def test_private_path_outside_data_output_is_rejected(tmp_path):
    paths = _fixture(tmp_path, with_results=True)
    paths["private_path"] = tmp_path / "outside" / "private.xlsx"
    with pytest.raises(ValueError, match="within data/output"):
        build_workbooks(**paths)


def test_private_workbook_has_complete_rows_and_truth_checks(tmp_path):
    paths = _fixture(tmp_path, with_results=True)
    counts = build_workbooks(**paths)
    assert counts["PRIVATE"]["Validated"] == 2
    assert counts["PRIVATE"]["Enriched"] == 2
    workbook = load_workbook(paths["private_path"], data_only=True)
    validated = workbook["Validated"]
    headers = [cell.value for cell in validated[1]]
    correct_index = headers.index("correct_building") + 1
    full_index = headers.index("fully_correct") + 1
    assert [validated.cell(row, correct_index).value for row in (2, 3)] == ["Y", "Y"]
    assert [validated.cell(row, full_index).value for row in (2, 3)] == ["Y", "N"]


def test_summary_stats_rows_match_csv_and_raw_keeps_all_input_rows(tmp_path):
    paths = _fixture(tmp_path)
    _, summary_rows = _read_fixture_csv(paths["summary_path"])
    _, raw_rows = _read_fixture_csv(paths["input_path"])
    build_workbooks(**paths, public_only=True)
    workbook = load_workbook(paths["public_path"], data_only=True)
    assert workbook["Summary stats"].max_row - 1 == len(summary_rows)
    assert workbook["Raw"].max_row - 1 == len(raw_rows)


def _read_fixture_csv(path):
    with path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        return reader.fieldnames, list(reader)
