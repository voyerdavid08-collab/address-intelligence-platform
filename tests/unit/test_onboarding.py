import ast
import csv
import json
import re
import sys
from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from openpyxl import Workbook

from address_intelligence.client_profile import (
    ClientProfile,
    estimate_cost,
    load_client_profile,
)
from address_intelligence.config import Settings
from address_intelligence.credits import CreditLedger, PRICE_TABLE
from address_intelligence.app_data import zero_category_note
from address_intelligence.onboarding import (
    build_profile,
    display_preflight_rows,
    estimate_profile_cost,
    format_confidence,
    fraction_value,
    friendly_label,
    cost_breakdown,
    preflight_checks,
    preflight_group_summaries,
    read_excel_workbook,
    save_client_bundle,
    serialize_input_csv,
    suggest_mapping,
)
from address_intelligence.pipeline import StagedPipeline, _read_policies
from address_intelligence.providers.precisely import ProviderResponse


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app"))
import ui as app_ui

SAMPLE_CSV = ROOT / "app" / "demo_data" / "sample_client.csv"
DEMO_EXCEPTIONS = ROOT / "app" / "demo_data" / "demo_exceptions.json"


def _sample_rows():
    with SAMPLE_CSV.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        return list(reader.fieldnames or ()), list(reader)


def _map_for_sample():
    columns, _ = _sample_rows()
    return suggest_mapping(columns)


def test_suggest_mapping_maps_sample_required_headers_one_to_one():
    mapping = _map_for_sample()
    assert mapping["policy_id"] == "Contract Ref"
    assert mapping["address_line1"] == "Street Address"
    assert mapping["address_line2"] == "Apt"
    assert mapping["city"] == "Town"
    assert mapping["state"] == "State"
    assert mapping["zip5"] == "Postal Code"
    assert len([column for column in mapping.values() if column]) == len(
        set(column for column in mapping.values() if column)
    )


def test_preflight_returns_aggregate_counts_for_fixture_rows():
    mapping = {
        "policy_id": "id",
        "address_line1": "street",
        "address_line2": None,
        "city": "city",
        "state": "state",
        "zip5": "zip",
        "metro": None,
    }
    rows = [
        {"id": "A", "street": "10 EXAMPLE OAK ST", "city": "Austin", "state": "TX", "zip": "78701"},
        {"id": "B", "street": "", "city": "Dallas", "state": "ZZ", "zip": ""},
        {"id": "C", "street": "PO BOX 3 EXAMPLE", "city": "Miami", "state": "FL", "zip": "3310"},
        {"id": "A", "street": "10 EXAMPLE OAK ST", "city": "Austin", "state": "TX", "zip": "78701"},
    ]
    checks = {row["check"]: row for row in preflight_checks(rows, mapping)}
    assert checks["Records"]["count"] == 4
    assert checks["Empty street line"]["count"] == 1
    assert checks["Missing house number"]["count"] == 0
    assert checks["ZIP lost its leading zero"]["group"] == "Precisely usually fixes"
    assert checks["Missing ZIP"]["count"] == 1
    assert checks["ZIP format invalid"]["count"] == 1
    assert checks["State not a valid US code"]["count"] == 1
    assert checks["PO Box"]["count"] == 1
    assert checks["Duplicate addresses (normalized)"]["count"] == 2
    assert checks["ZIP format invalid"]["count"] == 1
    assert checks["Duplicate IDs"]["count"] == 2
    groups = {row["group"]: row for row in preflight_group_summaries(checks.values())}
    assert groups["Precisely usually fixes"]["count"] == 0
    assert groups["Needs client follow-up"]["count"] == 4
    assert groups["Review"]["count"] == 0
    assert groups["No issues found"]["count"] == 0
    assert sum(row["count"] for row in groups.values()) == 4
    assert checks["Missing ZIP"]["group"] == "Precisely usually fixes"
    assert checks["ZIP format invalid"]["group"] == "Precisely usually fixes"
    assert checks["ZIP lost its leading zero"]["group"] == "Precisely usually fixes"
    assert checks["State written in full"]["group"] == "Precisely usually fixes"
    assert checks["Missing city (valid ZIP)"]["group"] == "Precisely usually fixes"
    assert checks["Empty street line"]["group"] == "Needs client follow-up"
    assert checks["Missing house number"]["group"] == "Needs client follow-up"
    assert checks["PO Box"]["group"] == "Needs client follow-up"
    assert checks["Duplicate addresses (normalized)"]["group"] == "Review"
    displayed = display_preflight_rows(checks.values())
    assert "Group" in displayed[0]
    assert [row["Group"] for row in displayed] == sorted(
        [row["Group"] for row in displayed],
        key=lambda group: (
            ("Needs client follow-up", "Precisely usually fixes", "Review").index(group)
            if group in ("Needs client follow-up", "Precisely usually fixes", "Review")
            else -1
        ),
    )
    assert checks["Records"]["denominator"] == 4
    assert checks["Records"]["percentage"] == 100
    assert "EXAMPLE OAK" not in str(checks)


def test_profile_save_bundle_and_load_succeeds(tmp_path):
    columns, _ = _sample_rows()
    mapping = suggest_mapping(columns)
    profile = build_profile(
        "Made-up sample carrier",
        "sample-client",
        mapping,
        {"flood_by_location": True, "preciselyid_by_address": False},
    )
    input_path, profile_path = save_client_bundle(
        "sample-client",
        SAMPLE_CSV.read_bytes(),
        profile,
        project_root=tmp_path,
    )
    loaded = load_client_profile(profile_path, project_root=tmp_path)
    assert input_path.read_bytes() != b""
    assert input_path.read_text(encoding="utf-8").splitlines()[0].count(",") == 5
    assert loaded.input_path == input_path
    assert loaded.run_dir == tmp_path / "data" / "output" / "sample-client"
    assert loaded.column_mapping["metro"] is None
    assert loaded.ground_truth_path is None


def test_excel_upload_parsing_and_saved_csv_bundle(tmp_path):
    workbook = Workbook()
    address_sheet = workbook.active
    address_sheet.title = "Addresses"
    address_sheet.append(
        ["id", "street", "city", "state", "zip", "whole_float", "empty", "date"]
    )
    address_sheet.append(
        [
            "EXAMPLE-1",
            "1 EXAMPLE OAK ST",
            "Boston",
            "MA",
            78701,
            42.0,
            None,
            date(2026, 10, 30),
        ]
    )
    other_sheet = workbook.create_sheet("Other")
    other_sheet.append(["id", "street", "city", "state", "zip"])
    other_sheet.append(["EXAMPLE-2", "2 EXAMPLE OAK ST", "Austin", "TX", 78702])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    content = output.getvalue()

    columns, rows, sheets = read_excel_workbook(content)
    assert sheets == ["Addresses", "Other"]
    assert rows == [
        {
            "id": "EXAMPLE-1",
            "street": "1 EXAMPLE OAK ST",
            "city": "Boston",
            "state": "MA",
            "zip": "78701",
            "whole_float": "42",
            "empty": "",
            "date": "2026-10-30",
        }
    ]
    other_columns, other_rows, _ = read_excel_workbook(content, "Other")
    assert other_columns == ["id", "street", "city", "state", "zip"]
    assert other_rows[0]["zip"] == "78702"

    profile = build_profile(
        "Excel sample",
        "excel-sample",
        {
            "policy_id": "id",
            "address_line1": "street",
            "address_line2": None,
            "city": "city",
            "state": "state",
            "zip5": "zip",
            "metro": None,
        },
    )
    input_path, _ = save_client_bundle(
        "excel-sample",
        serialize_input_csv(columns, rows),
        profile,
        project_root=tmp_path,
    )
    with input_path.open(newline="", encoding="utf-8") as source:
        saved_rows = list(csv.DictReader(source))
    mapped = [c for c in columns if c in set(profile["input"]["column_mapping"].values())]
    assert saved_rows == [{c: row[c] for c in mapped} for row in rows]


@pytest.mark.parametrize("slug", ["../outside", "Sample", "run", "a_b"])
def test_save_bundle_rejects_unsafe_or_reserved_slug(tmp_path, slug):
    profile = build_profile(
        "Client",
        "safe-client",
        {
            "policy_id": "id",
            "address_line1": "street",
            "address_line2": None,
            "city": "city",
            "state": "state",
            "zip5": "zip",
            "metro": None,
        },
    )
    with pytest.raises(ValueError):
        save_client_bundle(slug, b"id,street,city,state,zip\n", profile, project_root=tmp_path)
    assert not (tmp_path / "data" / "clients").exists()


def test_save_bundle_rejects_existing_directory_without_overwrite(tmp_path):
    folder = tmp_path / "data" / "clients" / "sample-client"
    folder.mkdir(parents=True)
    profile = build_profile(
        "Client",
        "sample-client",
        {
            "policy_id": "id",
            "address_line1": "street",
            "address_line2": None,
            "city": "city",
            "state": "state",
            "zip5": "zip",
            "metro": None,
        },
    )
    with pytest.raises(FileExistsError):
        save_client_bundle(
            "sample-client",
            b"id,street,city,state,zip\n",
            profile,
            project_root=tmp_path,
        )


def test_cost_passthrough_uses_shared_estimator():
    columns, _ = _sample_rows()
    profile_data = build_profile(
        "Made-up sample carrier",
        "sample-client",
        suggest_mapping(columns),
        {"flood_by_location": True, "preciselyid_by_address": True},
    )
    estimate = estimate_profile_cost(profile_data, 25, 0.8)
    profile = ClientProfile(Path("profile.yaml"), ROOT, profile_data)
    assert estimate == estimate_cost(profile, 25, 0.8)


def test_unmapped_metro_groups_comparison_sample_by_state(tmp_path):
    csv_bytes = (
        "id,street,city,state,zip\n"
        "EXAMPLE-1,1 EXAMPLE A ST,Austin,TX,78701\n"
        "EXAMPLE-2,2 EXAMPLE B ST,Austin,TX,78702\n"
        "EXAMPLE-3,3 EXAMPLE C ST,Miami,FL,33101\n"
        "EXAMPLE-4,4 EXAMPLE D ST,Miami,FL,33102\n"
    ).encode()
    mapping = {
        "policy_id": "id",
        "address_line1": "street",
        "address_line2": None,
        "city": "city",
        "state": "state",
        "zip5": "zip",
        "metro": None,
    }
    profile_data = build_profile("Test", "state-group", mapping)
    profile_data["comparison"]["enabled"] = True
    input_path, profile_path = save_client_bundle(
        "state-group", csv_bytes, profile_data, project_root=tmp_path
    )
    profile = load_client_profile(profile_path, project_root=tmp_path)
    settings = Settings(
        api_key=None,
        api_secret=None,
        base_url="https://api.precisely.com",
        credit_hard_cap=Decimal("100"),
        trial_credits=Decimal("100"),
        log_level="INFO",
    )
    pipeline = StagedPipeline(
        settings,
        CreditLedger(Decimal("100")),
        project_root=tmp_path,
        profile=profile,
    )
    rows = _read_policies(input_path, profile)
    eligible = [
        (
            row,
            ProviderResponse(200, {}, None, Decimal("0")),
        )
        for row in rows
    ]
    pipeline.comparison_sample_size = 2
    selected = pipeline._compare_sample(eligible)
    assert {row["raw_state"] for row, _ in selected} == {"TX", "FL"}


def test_demo_content_has_only_example_identifiers_and_safe_coordinates():
    columns, rows = _sample_rows()
    assert all(row["Contract Ref"].startswith("EXAMPLE-") for row in rows)
    assert all(
        not row["Street Address"] or "EXAMPLE" in row["Street Address"]
        for row in rows
    )
    assert len(rows) == 25
    demo = json.loads(DEMO_EXCEPTIONS.read_text(encoding="utf-8"))
    assert [row["policy_id"] for row in demo] == [
        f"EXAMPLE-{index}" for index in range(1, 7)
    ]
    combined = SAMPLE_CSV.read_text(encoding="utf-8") + DEMO_EXCEPTIONS.read_text(encoding="utf-8")
    assert not __import__("re").search(r"POL-\d+", combined)
    assert not __import__("re").search(r"-?\d+\.\d{4,}", combined)


def test_friendly_labels_for_mapping_actions_causes_and_confidence():
    assert friendly_label("policy_id") == "Policy ID column"
    assert friendly_label("address_line2") == "Unit / apartment column (optional)"
    assert friendly_label("analyst_review") == "Analyst review"
    assert friendly_label("street_name_issue") == "Street name"
    assert format_confidence("0.91") == "91%"
    assert fraction_value("169/200 (84.5%)") == ("169/200", "84.5%")


def test_sample_preflight_triggers_each_check_and_has_exclusive_groups():
    columns, rows = _sample_rows()
    checks = {
        row["check"]: row
        for row in preflight_checks(rows, suggest_mapping(columns))
    }
    assert checks["Duplicate addresses (normalized)"]["count"] == 2
    assert checks["Empty street line"]["count"] > 0
    assert checks["Missing house number"]["count"] > 0
    assert checks["Missing ZIP"]["count"] > 0
    assert checks["ZIP format invalid"]["count"] > 0
    assert checks["ZIP lost its leading zero"]["count"] > 0
    assert checks["State written in full"]["count"] > 0
    assert checks["State not a valid US code"]["count"] > 0
    assert checks["PO Box"]["count"] > 0
    assert checks["Missing city (valid ZIP)"]["count"] > 0
    assert checks["Missing city (no valid ZIP)"]["count"] > 0
    assert checks["Duplicate IDs"]["count"] > 0
    assert checks["Duplicate addresses (normalized)"]["count"] == 2
    display = {row["Check"]: row for row in display_preflight_rows(checks.values())}
    assert "What it means" in display["ZIP lost its leading zero"]
    summaries = preflight_group_summaries(preflight_checks(rows, suggest_mapping(columns)))
    group_counts = {row["group"]: row["count"] for row in summaries}
    assert sum(group_counts.values()) == 25
    assert sum(row["percentage"] for row in summaries) == 100


def test_clean_record_counts_in_no_issues_group():
    mapping = {
        "policy_id": "id",
        "address_line1": "street",
        "address_line2": None,
        "city": "city",
        "state": "state",
        "zip5": "zip",
        "metro": None,
    }
    rows = [{
        "id": "EXAMPLE-1",
        "street": "10 EXAMPLE OAK ST",
        "city": "Boston",
        "state": "MA",
        "zip": "02134",
    }]
    summaries = preflight_group_summaries(preflight_checks(rows, mapping))
    assert {row["group"]: row["count"] for row in summaries} == {
        "Needs client follow-up": 0,
        "Precisely usually fixes": 0,
        "Review": 0,
        "No issues found": 1,
    }


def test_preflight_edge_cases_for_zero_prefix_city_state_and_territory():
    mapping = {
        "policy_id": "id",
        "address_line1": "street",
        "address_line2": None,
        "city": "city",
        "state": "state",
        "zip5": "zip",
        "metro": None,
    }
    rows = [
        {"id": "A", "street": "1 EXAMPLE OAK ST", "city": "", "state": "MA", "zip": "02134"},
        {"id": "B", "street": "PO BOX 2 EXAMPLE", "city": "San Juan", "state": "PR", "zip": "00901"},
        {"id": "C", "street": "3 EXAMPLE PINE ST", "city": "", "state": "Texas", "zip": ""},
        {"id": "D", "street": "4 EXAMPLE ELM ST", "city": "Boston", "state": "MA", "zip": "2134"},
        {"id": "E", "street": "5 EXAMPLE MAPLE ST", "city": "Phoenix", "state": "AZ", "zip": "8570"},
    ]
    checks = {row["check"]: row for row in preflight_checks(rows, mapping)}
    assert checks["ZIP lost its leading zero"]["count"] == 1
    assert checks["ZIP format invalid"]["count"] == 1
    assert checks["Missing city (valid ZIP)"]["count"] == 1
    assert checks["Missing city (no valid ZIP)"]["count"] == 1
    assert checks["State written in full"]["count"] == 1
    assert checks["State not a valid US code"]["count"] == 0
    assert checks["PO Box"]["count"] == 1
    assert checks["Missing house number"]["count"] == 0


@pytest.mark.parametrize(
    "state",
    ["CT", "MA", "ME", "NH", "NJ", "RI", "VT", "PR", "VI"],
)
def test_four_digit_zip_is_a_leading_zero_case_only_in_eligible_states(state):
    mapping = {
        "policy_id": "id",
        "address_line1": "street",
        "address_line2": None,
        "city": "city",
        "state": "state",
        "zip5": "zip",
        "metro": None,
    }
    checks = {
        row["check"]: row
        for row in preflight_checks(
            [{"id": "EXAMPLE-1", "street": "10 EXAMPLE MAIN ST", "city": "Boston", "state": state, "zip": "2134"}],
            mapping,
        )
    }
    assert checks["ZIP lost its leading zero"]["count"] == 1
    assert checks["ZIP format invalid"]["count"] == 0


@pytest.mark.parametrize("state", ["AZ", "NY", "DC", "GU", "AS", "MP"])
def test_other_four_digit_zips_are_invalid_only(state):
    mapping = {
        "policy_id": "id",
        "address_line1": "street",
        "address_line2": None,
        "city": "city",
        "state": "state",
        "zip5": "zip",
        "metro": None,
    }
    checks = {
        row["check"]: row
        for row in preflight_checks(
            [{"id": "EXAMPLE-1", "street": "10 EXAMPLE MAIN ST", "city": "Phoenix", "state": state, "zip": "8570"}],
            mapping,
        )
    }
    assert checks["ZIP lost its leading zero"]["count"] == 0
    assert checks["ZIP format invalid"]["count"] == 1


def test_cost_uses_full_book_policy_count():
    columns, _ = _sample_rows()
    profile = build_profile(
        "Made-up sample carrier",
        "sample-client",
        suggest_mapping(columns),
        {
            "flood_by_location": True,
            "distance_to_flood_hazard_by_location": True,
            "property_attributes_v2": True,
            "preciselyid_by_address": True,
        },
    )
    estimate = estimate_profile_cost(profile, 300_000, 1.0)
    assert estimate["total_credits"] == Decimal("2703000.000")


def test_cost_breakdown_uses_price_table_and_matches_shared_estimate():
    columns, _ = _sample_rows()
    profile_data = build_profile(
        "Made-up sample carrier",
        "sample-client",
        suggest_mapping(columns),
        {
            "flood_by_location": True,
            "distance_to_flood_hazard_by_location": False,
            "property_attributes_v2": True,
            "preciselyid_by_address": True,
        },
    )
    breakdown = cost_breakdown(profile_data, 300_000, 0.85)
    estimate = estimate_profile_cost(profile_data, 300_000, 0.85)
    assert sum(row["Credits"] for row in breakdown["rows"][:-1]) == estimate["total_credits"]
    assert breakdown["rows"][-1]["Credits"] == estimate["total_credits"]
    assert breakdown["rows"][0]["Credits each"] == PRICE_TABLE["geocode_premium"]
    assert breakdown["enabled_item_rate"] == sum(
        PRICE_TABLE[operation]
        for operation in ("flood_by_location", "property_attributes_v2", "preciselyid_by_address")
    )
    all_enabled = build_profile(
        "Made-up sample carrier",
        "sample-client",
        suggest_mapping(columns),
        {
            operation: True
            for operation in (
                "flood_by_location",
                "distance_to_flood_hazard_by_location",
                "property_attributes_v2",
                "preciselyid_by_address",
            )
        },
    )
    full_breakdown = cost_breakdown(all_enabled, 10, 0.85)
    assert full_breakdown["enabled_item_rate"] == Decimal("8.01")
    assert all(
        isinstance(row["Calls"], Decimal)
        and row["Calls"] == row["Calls"].to_integral_value()
        for row in full_breakdown["rows"][:-1]
    )
    assert full_breakdown["rows"][1]["Calls"] == Decimal("9")
    streamlit_source = (ROOT / "app" / "streamlit_app.py").read_text(encoding="utf-8")
    parsed = ast.parse(streamlit_source)
    formula_function = next(
        node for node in parsed.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_average_credits_formula"
    )
    namespace = {"Any": Any}
    exec(
        compile(
            ast.Module(body=[formula_function], type_ignores=[]),
            "<formula-test>",
            "exec",
        ),
        namespace,
    )
    assert namespace["_average_credits_formula"](full_breakdown["enabled_item_rate"]).endswith(
        "8.01 credits for enabled data items."
    )


def test_cost_breakdown_prices_whole_calls_and_total_matches_displayed_rows():
    columns, _ = _sample_rows()
    profile = build_profile(
        "Made-up sample carrier",
        "sample-client",
        suggest_mapping(columns),
        {
            operation: True
            for operation in (
                "flood_by_location",
                "distance_to_flood_hazard_by_location",
                "property_attributes_v2",
                "preciselyid_by_address",
            )
        },
    )
    breakdown = cost_breakdown(profile, 25, 0.84)
    item_rows = breakdown["rows"][:-1]
    total_row = breakdown["rows"][-1]
    assert all(row["Credits"] == row["Calls"] * row["Credits each"] for row in item_rows)
    assert total_row["Calls"] == ""
    assert total_row["Credits each"] == ""
    assert total_row["Credits"] == sum(row["Credits"] for row in item_rows)
    precisely_id = next(row for row in item_rows if row["Item"] == "PreciselyID")
    assert precisely_id["Calls"] == 21
    assert precisely_id["Credits"] == Decimal("0.21")
    streamlit_source = (ROOT / "app" / "streamlit_app.py").read_text(encoding="utf-8")
    assert 'st.markdown(f"**Total: {credit_total} credits**")' in streamlit_source


def test_results_chart_spec_uses_full_names_integer_ticks_and_title():
    rows = [
        {"Defect": "Non-existent address", "Before": 0, "After": 2},
        {"Defect": "Missing house number", "Before": 1, "After": 3},
        {"Defect": "Street typo", "Before": 0, "After": 0},
    ]
    chart_data = [
        {
            "Defect": row["Defect"],
            "Series": series,
            "Count": row[series],
            "Label": str(row[series]),
            "Label position": row[series],
        }
        for row in rows
        for series in ("Before", "After")
        if row[series] > 0
    ]
    spec = app_ui.build_results_chart(rows, chart_data).to_dict()
    assert spec["title"]["text"] == (
        "Records with the right address, before and after, by issue type"
    )
    assert spec["layer"][0]["encoding"]["y"]["axis"]["labelLimit"] == 0
    assert spec["layer"][0]["encoding"]["x"]["axis"]["format"] == "d"
    assert spec["layer"][0]["encoding"]["x"]["axis"]["tickMinStep"] == 1
    assert spec["layer"][0]["encoding"]["color"]["legend"]["orient"] == "top-right"
    assert spec["layer"][1]["transform"] == [{"filter": "datum.Count > 0"}]
    assert spec["layer"][1]["mark"]["dx"] == 6
    assert spec["layer"][1]["encoding"]["yOffset"]["field"] == "Series"
    assert "section.main" not in app_ui._GLOBAL_CSS
    assert '[data-testid="stMainBlockContainer"]' in app_ui._GLOBAL_CSS
    assert "padding-top: 16px" in app_ui._GLOBAL_CSS
    assert ":material/folder_lock:" not in (
        ROOT / "app" / "streamlit_app.py"
    ).read_text(encoding="utf-8")
    assert all(row["Count"] > 0 for row in chart_data)
    assert [row["Defect"] for row in chart_data] == [
        "Non-existent address",
        "Missing house number",
        "Missing house number",
    ]
    assert zero_category_note(rows) == (
        "Street typo: 0 before and 0 after. These need client follow-up."
    )


def test_share_bar_has_proportional_nonzero_segments():
    html = app_ui.share_bar_html(
        [
            {"group": "Needs client follow-up", "count": 7, "percentage": 28.0},
            {"group": "Precisely usually fixes", "count": 10, "percentage": 40.0},
            {"group": "Review", "count": 8, "percentage": 32.0},
            {"group": "No issues found", "count": 0, "percentage": 0.0},
        ]
    )
    widths = [float(width) for width in re.findall(r'width:([\d.]+)%;flex-basis:', html)]
    assert sum(widths) == pytest.approx(100)
    assert len(widths) == 3
    assert "7 · 28%" in html
    assert 'aria-label="No issues found:' not in html
    narrow_html = app_ui.share_bar_html(
        [
            {"group": "Needs client follow-up", "count": 9, "percentage": 90.0},
            {"group": "Precisely usually fixes", "count": 1, "percentage": 5.0},
            {"group": "Review", "count": 1, "percentage": 5.0},
            {"group": "No issues found", "count": 0, "percentage": 0.0},
        ]
    )
    assert narrow_html.count("ai-share-segment-label") == 1


def test_start_here_and_no_inner_steppers():
    source = (ROOT / "app" / "streamlit_app.py").read_text(encoding="utf-8")
    ui_source = (ROOT / "app" / "ui.py").read_text(encoding="utf-8")
    start_tab = source[source.index("with start_tab:"):source.index("with onboard_tab:")]
    tab_names = source[source.index("start_tab, onboard_tab"):source.index("for key, default")]
    assert tab_names.index(":material/flag: Start here") < tab_names.index(
        ":material/upload_file: 1 Onboard"
    )
    assert (
        "This tool helps a Sales Engineer assess a prospect's address data, "
        "price a clean-up, and show the client what they receive after the run. "
        "It runs only on this computer."
    ) in start_tab
    assert len(re.findall(r'"title": "[1-4] ', start_tab)) == 4
    for safety_line in (
        "Only address columns are used; every other column stays on this computer.",
        "This app never contacts Precisely or the AI. Paid runs start only from the command line, after approval.",
        "Made-up examples are safe to share. Real results are for local review only (Precisely APIs Acceptable Use Policy, sections 4.1–4.2).",
        "Client files and settings are saved in a private project folder that is never uploaded to GitHub.",
    ):
        assert safety_line in start_tab
    assert "Open tab 1 and click \"Use made-up sample client\"" in start_tab
    assert "enter 300,000 policies and set the verified share to 100%" in start_tab
    for question in (
        "What does 'located automatically' mean?",
        "Why is the price a list price?",
        "Who is the work list for?",
        "Why is there a 'made-up / real' switch?",
        "What never leaves this computer?",
    ):
        assert question in start_tab
    assert (
        "An automatically located record has an S8 exact address point or an S5 or S7 placement on the street segment."
        in start_tab
    )
    assert "Live Precisely results. Private screen share only." in source
    assert (
        "AI proposes, Precisely verifies: every suggestion goes to an analyst for review."
        in source
    )
    assert 'header[data-testid="stHeader"] { display:none; height:0; }' in app_ui._GLOBAL_CSS
    assert "padding-top: 16px" in ui_source
    assert "active_step" not in app_ui.__dict__
    assert "render_stepper" not in ui_source
    assert ".ai-stepper" not in ui_source
    assert "render_stepper" not in source
    assert "font-family" not in app_ui._GLOBAL_CSS.casefold()
    assert "stIconMaterial" not in app_ui._GLOBAL_CSS
    assert "material-symbols" not in app_ui._GLOBAL_CSS
    streamlit_source = (ROOT / "app" / "streamlit_app.py").read_text(encoding="utf-8")
    assert "use_container_width" not in streamlit_source
    assert 'st.selectbox("Sheet", sheets, key="excel_sheet")' in source


def test_local_modules_do_not_import_network_or_provider_clients():
    modules = [
        ROOT / "src" / "address_intelligence" / "onboarding.py",
        ROOT / "src" / "address_intelligence" / "app_data.py",
        ROOT / "app" / "streamlit_app.py",
        ROOT / "app" / "ui.py",
    ]
    banned = {
        "address_intelligence.providers",
        "requests",
        "Settings",
        "dotenv",
        "httpx",
        "openai",
        "groq",
    }
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {node.module or ""}
                names.update(alias.name for alias in node.names)
            else:
                continue
            assert not any(
                name in banned
                or name.startswith("address_intelligence.providers")
                for name in names
            ), f"{path} imports a prohibited client: {names & banned}"


def test_alias_guessing_maps_prov_postal_and_leaves_agent():
    mapping = suggest_mapping(
        ["Policy #", "Street", "Apt", "Town", "Prov", "Postal", "Agent"]
    )
    assert mapping["policy_id"] == "Policy #"
    assert mapping["address_line1"] == "Street"
    assert mapping["address_line2"] == "Apt"
    assert mapping["city"] == "Town"
    assert mapping["state"] == "Prov"
    assert mapping["zip5"] == "Postal"
    assert "Agent" not in mapping.values()


def test_save_bundle_keeps_only_mapped_columns(tmp_path):
    headers = ["Policy #", "Street", "Apt", "Town", "Prov", "Postal", "Agent"]
    rows = [
        dict(zip(headers, ["P1", "1 MAIN ST", "", "AUSTIN", "TX", "78701", "Bob"]))
    ]
    mapping = suggest_mapping(headers)
    profile = build_profile("Acme", "acme", mapping)
    input_path, profile_path = save_client_bundle(
        "acme", serialize_input_csv(headers, rows), profile, project_root=tmp_path
    )
    text = input_path.read_text(encoding="utf-8")
    assert "Agent" not in text and "Bob" not in text
    assert text.splitlines()[0] == "Policy #,Street,Apt,Town,Prov,Postal"
    load_client_profile(profile_path, project_root=tmp_path)


def test_wording_and_unmapped_guard_texts():
    from address_intelligence.onboarding import missing_required_message

    source = (ROOT / "app" / "streamlit_app.py").read_text(encoding="utf-8")
    for text in (
        "Default figures come from a reference run on 200 synthetic records; replace them with the client's own results after a sample run.",
        "the rate seen in our reference run, and should be updated once a sample of the client's own file has been run.",
        "Whoever maintains the client's address data, often an operations or data quality team.",
        "Starts at 85% (reference run); replace with the client's own rate after a sample run.",
        "Grouping reflects what Precisely corrected in our reference run: most missing ZIPs were fixed automatically, missing house numbers were not.",
        "Records with this issue",
        "A record can have more than one issue, so the table can add up to more than the file.",
        "Map all required columns in tab 1 first.",
        "Only the mapped address columns are saved; other columns are not kept.",
    ):
        assert text in source
    assert missing_required_message({"state": None, "policy_id": "a", "address_line1": "b", "city": "c", "zip5": "d"}) == "Choose the column for: State."
    rows = display_preflight_rows(
        preflight_checks([{"a": "1"}], {"policy_id": "a", "address_line1": None, "city": None, "state": None, "zip5": None})
    )
    assert rows and "Records with this issue" in rows[0]


def test_open_folder_calls_startfile(monkeypatch, tmp_path):
    import os
    from address_intelligence import app_data

    calls = []
    monkeypatch.setattr(os, "startfile", calls.append, raising=False)
    assert app_data.open_folder(tmp_path) is True
    assert calls == [str(tmp_path)]
    assert "private results folder instead of Downloads" in app_data.REAL_SAVE_EXPLANATION


def test_new_file_does_not_inherit_old_mapping_and_unmapped_is_safe():
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py")).run(timeout=30)
    next(b for b in app.button if b.label == "Use made-up sample client").click().run(timeout=30)
    state = next(s for s in app.selectbox if s.label == "State column")
    first_key = state.key
    state.set_value("— Not mapped —").run(timeout=30)
    assert not app.exception
    assert any("Choose the column for: State." in w.value for w in app.warning)
    assert not [b for b in app.button if b.label == "Save client bundle for a pipeline run"]
    app.session_state["uploaded_content"] = b"Policy,Street,City,Prov,ZIP\nP1,1 A ST,X,TX,78701\n"
    app.session_state["uploaded_name"] = "other.csv"
    app.run(timeout=30)
    assert not app.exception
    state = next(s for s in app.selectbox if s.label == "State column")
    assert state.key != first_key
    assert state.value == "Prov"


def test_streamlit_demo_smoke_if_dependency_is_installed():
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py")).run(timeout=30)
    assert not app.exception
    assert len(app.tabs) == 5
    sample_button = next(
        button for button in app.button if button.label == "Use made-up sample client"
    )
    sample_button.click().run(timeout=30)
    assert not app.exception
    show = next(radio for radio in app.radio if radio.label == "Show:")
    show.set_value("Real results on this computer").run(timeout=30)
    assert not app.exception
    show = next(radio for radio in app.radio if radio.label == "Show:")
    show.set_value("Made-up examples (safe to share)").run(timeout=30)
    assert not app.exception


def test_visual_tokens_and_real_mode_compliance_css():
    assert "Segoe UI" in app_ui.FONT_STACK
    assert app_ui.COLORS["brand_dark"] == "#39006B"
    assert app_ui.COLORS["brand"] == "#8017E1"
    assert "stElementToolbar" not in app_ui.real_mode_compliance_css(False)
    assert "stElementToolbar" in app_ui.real_mode_compliance_css(True)
    assert "[data-testid=\"stElementToolbar\"]" not in app_ui._GLOBAL_CSS
    config = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert app_ui.COLORS["brand_dark"] in app_ui._GLOBAL_CSS
    assert app_ui.COLORS["brand"] in config
    assert app_ui.FONT_STACK.split(",")[0].strip('"') in config


def test_apply_styles_renders_same_markdown_count_in_each_mode(monkeypatch):
    markdown_calls = []
    monkeypatch.setattr(
        app_ui.st,
        "markdown",
        lambda body, **kwargs: markdown_calls.append(body),
    )

    app_ui.apply_styles(real_mode=True)
    real_mode_count = len(markdown_calls)
    real_mode_compliance = markdown_calls[-1]

    markdown_calls.clear()
    app_ui.apply_styles(real_mode=False)

    assert len(markdown_calls) == real_mode_count
    assert real_mode_compliance == app_ui.real_mode_compliance_css(True)
    assert markdown_calls[-1] == "<style></style>"


def test_header_pill_is_neutral_and_mode_independent(monkeypatch):
    markdown_calls = []
    monkeypatch.setattr(
        app_ui.st,
        "markdown",
        lambda body, **kwargs: markdown_calls.append(body),
    )

    app_ui.render_header()

    header_html = markdown_calls[0]
    assert '<div class="ai-mode">Local only</div>' in header_html
    assert "Made-up examples" not in header_html
    assert "Real results" not in header_html
    assert "ai-mode-real" not in markdown_calls[1]


def test_demo_banner_is_rendered_after_measured_kpis():
    source = (ROOT / "app" / "streamlit_app.py").read_text(encoding="utf-8")
    results_tab = source[source.index("with results_tab:"):]
    assert results_tab.count("· aggregates only") == 1
    assert "Measured on the 200-record synthetic run" not in results_tab
    assert results_tab.index("From the completed run for") < results_tab.index(
        "_display_kpis(summary_path)"
    )
    assert results_tab.index(
        "_display_kpis(summary_path)"
    ) < results_tab.index('st.subheader("Exceptions", anchor=False)')
    assert (
        "Work list for whoever maintains the client's address data: what to fix, who does it "
        "(customer outreach or analyst review), and whether Precisely confirmed an AI fix."
    ) in results_tab
    assert results_tab.index('st.subheader("Exceptions", anchor=False)') < results_tab.index(
        'if demo_mode:'
    ) < results_tab.index('st.info("Made-up example data, not Precisely output.")')


def test_slash_headers_match_each_part():
    mapping = suggest_mapping(["Policy #", "Street", "Suite/Unit", "City", "State/Province", "Zip"])
    assert mapping["address_line2"] == "Suite/Unit"
    assert mapping["state"] == "State/Province"


def test_stage_commands_full_order():
    source = (ROOT / "app" / "streamlit_app.py").read_text(encoding="utf-8")
    start = source.index("def _stage_commands(")
    namespace = {}
    exec(source[start:source.index("st.set_page_config(")], namespace)
    full = namespace["_stage_commands"]("p.yaml", {"narratives": True, "remediation": True}, True)
    text = "\n".join(full)
    order = ["--stage geocode", "--stage preciselyid", "--stage enrich", "--stage compare",
             "generate_narratives.py", "evaluate_narratives", "diagnose_exceptions", "remediate_exceptions",
             "metrics_report", "build_workbook"]
    positions = [text.index(item) for item in order]
    assert positions == sorted(positions)
    assert text.count("--execute") == 5
    assert "--dry-run" in text and "# Step 1" in text
    assert "Prints 'not available' until a labelled calibration sample exists." in text
    minimal = "\n".join(namespace["_stage_commands"]("p.yaml", {}, False))
    assert "compare" not in minimal and "narratives" not in minimal and "diagnose" not in minimal


def test_folder_name_and_client_name_behaviour():
    pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py")).run(timeout=30)
    app.session_state["uploaded_content"] = b"Policy,Street,City,State,ZIP\nP1,1 A ST,X,TX,78701\n"
    app.session_state["uploaded_name"] = "up.csv"
    app.run(timeout=30)
    name = next(w for w in app.text_input if w.label == "Client name")
    assert name.value == ""
    assert name.placeholder == "e.g. Acme Insurance"
    assert any("Enter the client name." in w.value for w in app.warning)
    assert not [b for b in app.button if b.label == "Save client bundle for a pipeline run"]
    name.set_value("Acme Insurance").run(timeout=30)
    folder = next(w for w in app.text_input if w.label == "Folder name")
    assert folder.value == "acme-insurance"
    folder.set_value("my-folder").run(timeout=30)
    next(w for w in app.text_input if w.label == "Client name").set_value("Other Co").run(timeout=30)
    assert next(w for w in app.text_input if w.label == "Folder name").value == "my-folder"


def test_section_wording_and_icons_in_app():
    for path in ("app/streamlit_app.py", "app/ui.py"):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert "\u00a7" not in text
        assert "Acceptable Use Policy, sections 4.1–4.2" in text


def test_results_only_client_queue_and_reports(tmp_path):
    from address_intelligence.app_data import format_exception_table_rows, load_exception_queue, plain_flag_reason

    (tmp_path / "results_rowlevel.csv").write_text(
        "policy_id,eligibility,manual_review_reason,precision_code\n"
        "P1,manual_review,street-level only,S4HPNTSCZA\n",
        encoding="utf-8",
    )
    (tmp_path / "narratives.jsonl").write_text(
        json.dumps({"policy_id": "P1", "narrative": "Needs a house number."}) + "\n",
        encoding="utf-8",
    )
    queue, is_demo = load_exception_queue(tmp_path)
    assert is_demo is False and len(queue) == 1
    row = format_exception_table_rows(queue)[0]
    assert row["Match level"] == "Street (S4)"
    assert queue[0]["narrative"] == "Needs a house number."
    assert plain_flag_reason("street-level only") == "Precisely found the street but not the building (no house number match)."
    assert plain_flag_reason("postal or city level") == "Precisely could only match the ZIP code or city."
    assert plain_flag_reason("location conflict (state mismatch)") == "The state does not match the rest of the address."
    assert all(
        str(r.get("confidence", "")).strip() in ("", "Not available") for r in queue
    )


def test_build_profile_does_not_set_private_workbook_note():
    profile = build_profile("Acme", "acme", _map_for_sample())
    assert "private_workbook_note" not in profile["data_handling"]
