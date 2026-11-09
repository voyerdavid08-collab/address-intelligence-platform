import ast
import csv
import json
from pathlib import Path
from decimal import Decimal

import pytest

from address_intelligence.app_data import (
    _fraction,
    defect_chart_data,
    defect_chart_series,
    defect_chart_options,
    defect_chart_rows,
    format_cost_breakdown_rows,
    format_exception_table_rows,
    format_suggestion_status,
    load_fallback_kpis,
    load_exception_queue,
    load_summary_kpis,
    made_up_exception_workbook,
    safe_exception_export_path,
    save_real_exception_workbook,
    selected_policy_id,
    zero_category_note,
)
from address_intelligence.onboarding import friendly_label


def _write_summary(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(
            destination,
            fieldnames=(
                "section",
                "metric",
                "before",
                "after",
                "numerator",
                "denominator",
                "source",
            ),
        )
        writer.writeheader()
        writer.writerows(rows)


def test_kpis_are_read_from_summary_csv_fixture(tmp_path):
    path = tmp_path / "before_after_summary.csv"
    _write_summary(
        path,
        [
            {"metric": "Correct building: all records", "before": "5/10 (50.0%)", "after": "8/10 (80.0%)"},
            {"metric": "Fully correct (incl. unit): dirty records", "before": "1/4 (25.0%)", "after": "3/4 (75.0%)"},
            {"metric": "Flood-risk tier changed", "after": "2/7 (28.6%)"},
            {"metric": "Precisely-verified", "numerator": "6"},
            {"metric": "Verified correct", "numerator": "6"},
            {"metric": "Verified incorrect", "numerator": "0"},
            {"metric": "Correct building: defect: street_typo", "before": "1/2 (50.0%)", "after": "2/2 (100.0%)"},
        ],
    )
    kpis = load_summary_kpis(path)
    assert kpis is not None
    assert kpis["correct_building"] == {
        "label": "Right address",
        "before": "5/10 (50.0%)",
        "after": "8/10 (80.0%)",
    }
    assert kpis["dirty_fully_correct"]["after"] == "3/4 (75.0%)"
    assert kpis["flood_tier_changes"]["value"] == "2/7 (28.6%)"
    assert kpis["ai_verified"]["verified"] == "6"
    assert kpis["ai_verified"]["correct"] == "6"
    assert kpis["ai_verified"]["incorrect"] == "0"
    assert kpis["defects"][0]["defect"] == "street_typo"


def test_before_after_chart_data_is_grouped_not_stacked(tmp_path):
    path = tmp_path / "before_after_summary.csv"
    _write_summary(
        path,
        [
            {"metric": "Correct building: defect: street_typo", "before": "1/2 (50.0%)", "after": "2/2 (100.0%)"},
            {"metric": "Correct building: defect: city_typo", "before": "0/2 (0.0%)", "after": "1/2 (50.0%)"},
        ],
    )
    kpis = load_summary_kpis(path)
    assert kpis is not None
    assert defect_chart_rows(kpis) == [
        {"Defect": "City typo", "Before": 0, "After": 1},
        {"Defect": "Street typo", "Before": 1, "After": 2},
    ]
    assert defect_chart_options() == {
        "stack": False,
        "color": ["#9AA5B1", "#8017E1"],
        "y_label": "Records correct",
    }
    assert defect_chart_series(kpis) == [
        {"Defect": "City typo", "Series": "Before", "Count": 0},
        {"Defect": "City typo", "Series": "After", "Count": 1},
        {"Defect": "Street typo", "Series": "Before", "Count": 1},
        {"Defect": "Street typo", "Series": "After", "Count": 2},
    ]
    assert defect_chart_data(kpis) == [
        {"Defect": "City typo", "Series": "After", "Count": 1, "Label": "1", "Label position": 1},
        {"Defect": "Street typo", "Series": "Before", "Count": 1, "Label": "1", "Label position": 1},
        {"Defect": "Street typo", "Series": "After", "Count": 2, "Label": "2", "Label position": 2},
    ]


def test_zero_categories_are_reported_below_results_chart():
    assert zero_category_note(
        [
            {"Defect": "Missing house number", "Before": 0, "After": 0},
            {"Defect": "Non-existent address", "Before": 0, "After": 0},
            {"Defect": "Street typo", "Before": 2, "After": 4},
        ]
    ) == (
        "Missing house number and Non-existent address: 0 before and 0 after. "
        "These need client follow-up."
    )
    assert zero_category_note(
        [{"Defect": "Street typo", "Before": 1, "After": 2}]
    ) == ""


def test_kpi_tile_deltas_are_derived_from_fixture_csv(tmp_path):
    path = tmp_path / "before_after_summary.csv"
    _write_summary(
        path,
        [
            {
                "metric": "Correct building: all records",
                "before": "138/200 (69.0%)",
                "after": "169/200 (84.5%)",
            },
            {
                "metric": "Fully correct (incl. unit): dirty records",
                "before": "6/76 (7.9%)",
                "after": "37/76 (48.7%)",
            },
            {"metric": "Flood-risk tier changed", "after": "14/39 (35.9%)"},
            {
                "metric": "Coarse location understated verified risk",
                "numerator": "6",
                "after": "6/39 (15.4%)",
            },
            {
                "metric": "Coarse location overstated verified risk",
                "numerator": "8",
                "after": "8/39 (20.5%)",
            },
            {"metric": "Precisely-verified", "numerator": "6"},
            {"metric": "Verified correct", "numerator": "6"},
            {"metric": "Verified incorrect", "numerator": "0"},
        ],
    )
    root = Path(__file__).resolve().parents[2]
    source = (root / "app" / "streamlit_app.py").read_text(encoding="utf-8")
    parsed = ast.parse(source)
    needed = {"_fraction_count", "_build_kpi_tile_values"}
    definitions = [
        node for node in parsed.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in needed
    ]
    namespace = {
        "Path": Path,
        "_fraction": _fraction,
        "load_summary_kpis": load_summary_kpis,
        "read_summary_csv": lambda csv_path: _read_fixture_csv(csv_path),
    }
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(root / "app" / "streamlit_app.py"), "exec"), namespace)
    tiles = namespace["_build_kpi_tile_values"](path)
    assert namespace["_fraction_count"]("1e+06/2e+06 (50.0%)") == ("1e+06/2e+06", 1000000)
    assert tiles[0]["delta"] == "+31 records vs before"
    assert tiles[1]["delta"] == "+31 records vs before"
    assert tiles[2]["label"] == "City-level location changes flood-risk tier"
    assert tiles[2]["value"] == "14/39"
    assert tiles[2]["delta"] == "6 under-priced, 8 over-priced"
    assert tiles[2]["delta_color"] == "off"
    assert tiles[3]["delta"] == "0 incorrect"
    assert tiles[3]["delta_color"] == "off"
    assert all("%" not in str(tile["delta"]) for tile in tiles)
    assert all(tile["delta_color"] == "normal" for tile in tiles[:2])
    ui_source = (root / "app" / "ui.py").read_text(encoding="utf-8")
    assert 'text="Records with the right address, before and after, by issue type"' in ui_source


def _read_fixture_csv(path):
    with Path(path).open(newline="", encoding="utf-8-sig") as source:
        return list(csv.DictReader(source))


def test_missing_run_files_use_demo_exception_fallback(tmp_path):
    demo_path = tmp_path / "demo_exceptions.json"
    demo_path.write_text(
        json.dumps([{"policy_id": "EXAMPLE-1", "next_action": "analyst_review"}]),
        encoding="utf-8",
    )
    queue, is_demo = load_exception_queue(
        tmp_path / "empty-run",
        demo_path=demo_path,
        demo=True,
    )
    assert is_demo is True
    assert queue == [{"policy_id": "EXAMPLE-1", "next_action": "analyst_review"}]


def test_real_exception_queue_is_read_from_local_files(tmp_path):
    (tmp_path / "results_rowlevel.csv").write_text(
        "policy_id,eligibility,manual_review_reason,precision_code\n"
        "EXAMPLE-4,manual_review,street-level only,S4\n",
        encoding="utf-8",
    )
    (tmp_path / "remediation_diagnosis.jsonl").write_text(
        json.dumps({"policy_id": "EXAMPLE-4", "prediction_set": ["city_issue"], "confidence": "0.8"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "remediation_actions.jsonl").write_text(
        json.dumps({
            "policy_id": "EXAMPLE-4",
            "action": "analyst_review",
            "suggestion_status": "suggested, unverified",
            "verification_status": "verified by Precisely",
            "suggestion": {"line1": "4 EXAMPLE ST"},
        }) + "\n",
        encoding="utf-8",
    )
    queue, is_demo = load_exception_queue(tmp_path)
    assert is_demo is False
    assert queue[0]["policy_id"] == "EXAMPLE-4"
    assert queue[0]["ai_cause_set"] == "city_issue"
    assert queue[0]["verification_status"] == "verified by Precisely"


def test_no_ground_truth_summary_uses_fallback_kpis(tmp_path):
    path = tmp_path / "before_after_summary.csv"
    _write_summary(
        path,
        [
            {"section": "Verification", "metric": "Eligible", "after": "8/10 (80.0%)"},
            {"section": "Enrichment completeness", "metric": "flood_zone_code over all input", "after": "6/10 (60.0%)"},
            {"section": "Verification", "metric": "Manual review", "after": "2/10 (20.0%)"},
            {"section": "AI remediation", "metric": "Precisely-verified", "numerator": "n/a"},
        ],
    )
    kpis = load_summary_kpis(path)
    assert kpis is not None
    assert kpis["has_reference"] is False
    fallback = load_fallback_kpis(path)
    assert [tile["label"] for tile in fallback] == [
        "Located automatically",
        "Enriched with flood data",
        "Sent to manual review",
        "AI suggestions confirmed by Precisely",
    ]
    assert [tile["value"] for tile in fallback] == [
        "8/10 (80.0%)",
        "6/10 (60.0%)",
        "2/10 (20.0%)",
        "n/a",
    ]


def test_selected_exception_row_maps_to_policy_id_and_labels_are_plain():
    queue = [
        {"policy_id": "EXAMPLE-1"},
        {"policy_id": "EXAMPLE-2"},
    ]
    assert selected_policy_id(queue, [1]) == "EXAMPLE-2"
    assert selected_policy_id(queue, []) is None
    assert friendly_label("analyst_review") == "Analyst review"
    assert friendly_label("street_name_issue") == "Street name"
    formatted = format_exception_table_rows(
        [{"policy_id": "EXAMPLE-1", "confidence": "0.91", "next_action": "analyst_review"}]
    )
    assert formatted[0]["Confidence"] == "91%"
    assert formatted[0]["Next action"] == "Analyst review"


def test_work_list_uses_plain_review_reason_and_match_level_labels():
    rows = format_exception_table_rows(
        [
            {
                "policy_id": "EXAMPLE-1",
                "manual_review_reason": "street-level only",
                "precision_code": "S4",
                "verification_status": "verified by Precisely",
            },
            {
                "policy_id": "EXAMPLE-2",
                "manual_review_reason": "postal or city level",
                "precision_code": "Z1",
                "verification_status": "did not verify",
            },
            {
                "policy_id": "EXAMPLE-3",
                "manual_review_reason": "location conflict (state mismatch)",
                "precision_code": "S8",
                "verification_status": "not_started",
            },
            {
                "policy_id": "EXAMPLE-4",
                "manual_review_reason": "PO Box",
                "precision_code": "G1",
                "verification_status": "rejected",
            },
        ]
    )
    assert rows[0]["Manual review reason"] == "Matched to the street, not the building"
    assert rows[0]["Match level"] == "Street (S4)"
    assert rows[0]["Verification status"] == "Confirmed"
    assert rows[1]["Manual review reason"] == "Matched to the ZIP or city only"
    assert rows[1]["Match level"] == "ZIP/city (Z1)"
    assert rows[1]["Verification status"] == "Not verified"
    assert rows[2]["Manual review reason"] == "State does not match the address"
    assert rows[2]["Match level"] == "Building (S8)"
    assert rows[2]["Verification status"] == "Not started"
    assert rows[3]["Manual review reason"] == "PO Box cannot identify a building"
    assert rows[3]["Match level"] == "ZIP/city (G1)"
    assert rows[3]["Verification status"] == "Rejected"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("suggested, unverified", "AI suggestion made"),
        ("no_suggestion", "No suggestion"),
        ("not_requested", "Not requested"),
        ("blocked_house_number_change", "Blocked: house number change"),
        ("failed", "Failed"),
    ],
)
def test_suggestion_status_uses_plain_work_list_labels(raw, expected):
    assert format_suggestion_status(raw) == expected


def test_confirmed_verification_does_not_show_unverified_suggestion_status():
    row = format_exception_table_rows(
        [
            {
                "policy_id": "EXAMPLE-9",
                "suggestion_status": "suggested, unverified",
                "verification_status": "verified by Precisely",
            }
        ]
    )[0]
    assert row["Suggestion status"] == "AI suggestion made"
    assert row["Verification status"] == "Confirmed"
    assert "unverified" not in str(row.values()).casefold()


def test_cost_table_display_omits_total_row_and_preserves_precise_small_credits():
    rows = format_cost_breakdown_rows(
        [
            {
                "Item": "PreciselyID",
                "Applies to": "Verified policies only",
                "Calls": 21,
                "Credits each": Decimal("0.01"),
                "Credits": Decimal("0.21"),
            },
            {
                "Item": "Total",
                "Applies to": "",
                "Calls": "",
                "Credits each": "",
                "Credits": Decimal("0.21"),
            },
        ]
    )
    assert len(rows) == 1
    assert rows[0]["Item"] == "PreciselyID"
    assert rows[0]["Calls"] == 21
    assert rows[0]["Credits each"] == pytest.approx(0.01)
    assert rows[0]["Credits"] == pytest.approx(0.21)


def test_real_exception_export_is_guarded_inside_output_root(tmp_path):
    output_root = tmp_path / "data" / "output"
    run_dir = output_root / "client-a"
    destination = safe_exception_export_path(run_dir, output_root)
    assert destination == run_dir / "exceptions_work_list.xlsx"
    saved = save_real_exception_workbook(
        [{"policy_id": "CLIENT-1", "next_action": "analyst_review"}],
        run_dir,
        output_root,
    )
    assert saved.is_file()
    with pytest.raises(ValueError):
        safe_exception_export_path(tmp_path / "outside", output_root)


def test_made_up_workbook_export_contains_only_example_ids(tmp_path):
    from io import BytesIO
    from openpyxl import load_workbook

    content = made_up_exception_workbook(
        [{"policy_id": "EXAMPLE-7", "next_action": "analyst_review"}]
    )
    sheet = load_workbook(BytesIO(content), read_only=True).active
    assert list(sheet.values)[1][0] == "EXAMPLE-7"
    with pytest.raises(ValueError):
        made_up_exception_workbook([{"policy_id": "CLIENT-7"}])


@pytest.mark.parametrize("demo", [True, False])
def test_workbook_matches_work_list_rows_and_plain_case_details(tmp_path, demo):
    from io import BytesIO
    from openpyxl import load_workbook
    from address_intelligence.app_data import format_sales_worklist_rows, worklist_case_status

    prefix = "EXAMPLE" if demo else "CLIENT"
    queue = [
        {
            "policy_id": f"{prefix}-2", "next_action": "customer_outreach",
            "verification_status": "verified by Precisely",
            "input_address": {"address_line1": "12 EXAMPLE ROAD", "city": "Example", "state": "TX", "zip5": "00001"},
            "suggestion": {"line1": "12 EXAMPLE RD", "city": "Example", "zip5": "00001"},
            "manual_review_reason": "street-level only", "rationale": "street_name_issue",
            "narrative": "could not be verified to building level. Flood enrichment was withheld pending address remediation.",
            "precision_code": "S4", "ai_cause_set": "street_name_issue", "confidence": "0.91",
        },
        {"policy_id": f"{prefix}-1", "next_action": "analyst_review", "verification_status": "not applicable"},
        {"policy_id": f"{prefix}-3", "next_action": "analyst_review", "rationale": "=1+1"},
        {"policy_id": f"{prefix}-4", "next_action": "analyst_review", "verification_status": "verified by Precisely"},
        {"policy_id": f"{prefix}-5", "next_action": "analyst_review", "verification_status": "verified by Precisely", "verification": {"verified_incorrect": True}},
        {"policy_id": f"{prefix}-6", "next_action": "analyst_review", "verification": {"verified_correct": True}},
    ]
    if demo:
        workbook = load_workbook(BytesIO(made_up_exception_workbook(queue)))
    else:
        root = tmp_path / "output"
        workbook = load_workbook(save_real_exception_workbook(queue, root / "client", root))
    sheet = workbook.active
    headers = [
        "Policy ID", "Address on file", "Next action", "Why flagged", "Status",
        "Suggested address", "What the AI found", "Summary for operations",
        "Match level", "AI cause", "Confidence",
    ]
    assert [cell.value for cell in sheet[1]] == headers
    assert [cell.value for cell in sheet[2]] == [
        f"{prefix}-2", "12 EXAMPLE ROAD, Example, TX, 00001", "Customer outreach",
        "Matched to the street, not the building", "With the customer",
        "12 EXAMPLE RD, Example 00001", "street name issue",
        "was located only to the ZIP or city. Flood data is on hold until the address is approved.",
        "Street (S4)", "Street name", "91%",
    ]
    assert sheet["E3"].value == "No AI suggestion"
    assert [sheet[f"E{index}"].value for index in (5, 6, 7)] == [
        "Confirmed by Precisely", "Confirmed incorrect", "Confirmed by Precisely",
    ]
    assert sheet["B3"].value is None and sheet["F3"].value is None and sheet["K3"].value is None
    assert sheet["G4"].value == "=1+1" and sheet["G4"].data_type == "s"
    assert sheet.freeze_panes == "A2" and all(cell.font.bold for cell in sheet[1])
    assert all(cell.alignment.vertical == "center" for row in sheet.iter_rows() for cell in row)
    assert all(cell.alignment.horizontal is None for row in sheet.iter_rows() for cell in row)
    assert sheet["B2"].alignment.wrap_text and sheet["G2"].alignment.wrap_text and sheet["H2"].alignment.wrap_text
    assert not sheet["A2"].alignment.wrap_text
    assert sheet.column_dimensions["G"].width >= 12
    for source, expected, actual in zip(queue, format_sales_worklist_rows(queue), list(sheet.values)[1:]):
        expected["Status"] = worklist_case_status(source, expected["Status"])
        assert [value or "" for value in actual] == [expected[header] for header in headers]


def test_real_mode_with_missing_files_does_not_show_made_up_rows(tmp_path):
    queue, is_demo = load_exception_queue(
        tmp_path / "empty-run",
        demo_path=tmp_path / "demo.json",
        demo=False,
    )
    assert queue == []
    assert is_demo is False


def test_queue_from_results_only_has_not_available_and_no_demo(tmp_path):
    from address_intelligence.app_data import format_exception_table_rows, load_exception_queue

    (tmp_path / "results_rowlevel.csv").write_text(
        "policy_id,eligibility,manual_review_reason,precision_code\n"
        "P1,manual_review,street-level only,S4\n"
        "P2,eligible,,S8\n",
        encoding="utf-8",
    )
    queue, is_demo = load_exception_queue(tmp_path)
    assert is_demo is False
    assert [row["policy_id"] for row in queue] == ["P1"]
    row = format_exception_table_rows(queue)[0]
    assert row["Next action"] == "Analyst review"
    for header in ("AI cause", "Confidence", "Suggestion status", "Verification status"):
        assert row[header] == "Not available"
    assert row["Manual review reason"] == "Matched to the street, not the building"
