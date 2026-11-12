from pathlib import Path
import json
import re
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from address_intelligence import app_data, client_profile, onboarding


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app" / "streamlit_app.py"


@pytest.fixture
def sales_app(monkeypatch, tmp_path):
    """Keep the app offline and supply two independent in-memory clients."""
    import requests

    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected network request or real save")

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(onboarding, "save_client_bundle", forbidden)
    saved = {}
    make_workbook = app_data.made_up_exception_workbook
    save_workbook = app_data.save_real_exception_workbook

    def download(rows):
        saved["download"] = make_workbook(rows)
        return saved["download"]

    def save(rows, run_dir, output_root):
        saved["path"] = save_workbook(rows, tmp_path / "output" / "client", tmp_path / "output")
        return saved["path"]

    monkeypatch.setattr(app_data, "made_up_exception_workbook", download)
    monkeypatch.setattr(app_data, "save_real_exception_workbook", save)
    monkeypatch.setenv("AIP_DEMO_MODE", "1")

    mapping = {
        "policy_id": "Contract",
        "address_line1": "Street",
        "address_line2": None,
        "city": "Town",
        "state": "Region",
        "zip5": "Postal",
        "metro": None,
    }
    inputs = {
        "Alpha": (
            b"Contract,Street,Town,Region,Postal\n"
            b"001,1 First St,Alpha City,TX,75001\n"
            b"B,2 Second St,Alpha City,TX,75001\n"
            b"C,3 Third St,Alpha City,TX,75001\n"
        ),
        "Beta": b"Contract,Street,Town,Region,Postal\nZ,9 Beta St,Beta City,TX,75002\n",
    }
    profiles = {
        name: SimpleNamespace(
            input_path=ROOT / "fake-sales-test" / name / "input.csv",
            run_dir=ROOT / "fake-sales-test" / name / "run",
            reports_dir=ROOT / "fake-sales-test" / name / "reports",
            data={"client": {"name": name}},
            column_mapping=mapping,
        )
        for name in inputs
    }
    second_profile_path = ROOT / "data" / "clients" / "fake-sales-test" / "client_profile.yaml"
    original_glob = Path.glob
    original_read_bytes = Path.read_bytes
    original_is_file = Path.is_file

    def glob(path, pattern):
        if path == ROOT / "data" / "clients":
            return iter([second_profile_path])
        return original_glob(path, pattern)

    def read_bytes(path):
        for name, profile in profiles.items():
            if path == profile.input_path:
                return inputs[name]
        return original_read_bytes(path)

    def is_file(path):
        if any(path == p.reports_dir / "before_after_summary.csv" for p in profiles.values()):
            return True
        return original_is_file(path)

    monkeypatch.setattr(Path, "glob", glob)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(Path, "is_file", is_file)
    monkeypatch.setattr(
        client_profile, "load_client_profile",
        lambda path=None, **kwargs: profiles["Beta" if path == second_profile_path else "Alpha"],
    )
    fractions = {"Alpha": "1/2 (50.0%)", "Beta": "1/4 (25.0%)"}
    monkeypatch.setattr(
        app_data, "read_summary_csv",
        lambda path: (
            [{"metric": "Eligible", "after": fractions[path.parent.parent.name]}]
            if fractions[path.parent.parent.name] else []
        ),
    )
    monkeypatch.setattr(
        app_data, "load_summary_kpis",
        lambda path: (
            {"has_reference": False, "correct_building": {"before": "n/a", "after": "n/a"}}
            if fractions[path.parent.parent.name] else None
        ),
    )
    monkeypatch.setattr(app_data, "load_fallback_kpis", lambda path: [
        {"label": "Located automatically", "value": fractions[path.parent.parent.name]},
        {"label": "Enriched with flood data", "value": "1/2"},
    ])
    queue = [
        {"policy_id": "C", "next_action": "customer_outreach", "verification_status": "verified by Precisely"},
        {"policy_id": "B", "next_action": "analyst_review", "verification_status": "not applicable"},
        {
            "policy_id": "001", "next_action": "analyst_review",
            "verification_status": "verified by Precisely",
            "suggestion_status": "suggested, unverified",
            "suggestion": {"line1": "1 Corrected St", "city": "Alpha City", "state": "TX", "zip5": "75001"},
            "manual_review_reason": "street-level only",
        },
        {"policy_id": "MISSING", "next_action": "analyst_review", "verification_status": "not_started"},
    ]
    beta_queue = [
        {"policy_id": "Z", "next_action": "customer_outreach", "verification_status": "not_started"},
    ]
    original_load_queue = app_data.load_exception_queue
    monkeypatch.setattr(
        app_data, "load_exception_queue",
        lambda run_dir, *args, **kwargs: (
            original_load_queue(run_dir, *args, **kwargs)
            if kwargs.get("demo", False)
            else (beta_queue if run_dir == profiles["Beta"].run_dir else queue, False)
        ),
    )
    app = AppTest.from_file(str(APP)).run(timeout=30)
    assert not app.exception
    return SimpleNamespace(app=app, fractions=fractions, queue=queue, saved=saved)


def markup(app):
    return "\n".join(item.value for item in app.markdown)


def visible_markup(app):
    return re.sub(r"<[^>]*>", "", markup(app))


def frame_with(app, column):
    return next(frame.value for frame in app.dataframe if column in frame.value.columns)


def phasing_grid(app):
    return next(item.value for item in app.markdown if "Day one" in item.value and "After corrections" in item.value)


def test_full_book_pricing_and_optional_mail_verification(sales_app):
    app = sales_app.app
    next(w for w in app.number_input if w.label == "Policies to price (full book)").set_value(100).run(timeout=30)
    assert not app.exception
    rows = frame_with(app, "Credits each")
    assert list(rows["Item"]) == [
        "Geocoding (location)", "Address Verification (mail delivery)",
        "Flood zone and risk", "Distance to flood hazard", "Property attributes",
    ]
    assert list(rows["Calls"]) == [100] * 5
    assert list(rows["Credits each"]) == [1, 0.33, 5, 2, 1]
    assert "9.33" in markup(app)
    phase = phasing_grid(app)
    assert all(f">{value}</" in phase for value in ("933", "533", "400"))
    assert ".00" not in phase
    assert 'aria-label="Help: Day one"' in phase
    assert 'aria-label="Help: After corrections"' in phase
    for text in (
        "1. Save the client bundle (tab 1)",
        "2. The technical team runs a free dry run to confirm the planned calls and credits.",
        "3. After approval, they run the clean-up. A budget limit stops any run that would go over.",
        "4. Results then appear in tab 4.",
    ):
        assert text in markup(app)
    assert "--execute" not in markup(app)
    commands = next(item for item in app.expander if item.label == "Commands for the technical team")
    assert "--execute" in commands.code[0].value
    next(w for w in app.checkbox if w.label == "Include Address Verification (mail delivery)").uncheck().run(timeout=30)
    assert not app.exception
    assert "Address Verification (mail delivery)" not in list(frame_with(app, "Credits each")["Item"])
    assert "9.00" in markup(app)
    phase = phasing_grid(app)
    assert all(f">{value}</" in phase for value in ("900", "500", "400"))
    assert ".00" not in phase


def test_phasing_uses_selected_client_or_is_not_shown_without_eligible(sales_app):
    app = sales_app.app
    next(w for w in app.number_input if w.label == "Policies to price (full book)").set_value(100).run(timeout=30)
    assert ">533</" in phasing_grid(app)
    next(w for w in app.selectbox if w.label == "Client").set_value("Beta").run(timeout=30)
    assert not app.exception
    assert all(f">{value}</" in phasing_grid(app) for value in ("333", "600", "933"))
    next(w for w in app.number_input if w.label == "Policies to price (full book)").set_value(300000).run(timeout=30)
    assert all(f">{value}</" in phasing_grid(app) for value in ("999,000", "1,800,000", "2,799,000"))
    sales_app.fractions["Beta"] = None
    app.run(timeout=30)
    assert not app.exception
    assert "2,799,000 credits" in markup(app)
    assert "Day one" not in markup(app)
    assert "After corrections" not in markup(app)


def test_real_worklist_counts_order_mapped_addresses_and_workflow(sales_app):
    app = sales_app.app
    next(w for w in app.radio if w.label == "Show:").set_value("Real results on this computer").run(timeout=30)
    assert not app.exception
    table = next(frame for frame in app.dataframe if frame.key and frame.key.startswith("exceptions_table_"))
    assert list(table.value["Policy ID"]) == ["001", "B", "MISSING", "C"]
    config = json.loads(table.proto.columns)
    widths = {label: config[label]["width"] for label in (
        "Policy ID", "Address on file", "Next action", "Why flagged", "Status"
    )}
    assert widths == {"Policy ID": 100, "Address on file": 260, "Next action": 145, "Why flagged": 250, "Status": 170}
    assert sum(widths.values()) + 40 < 1176
    assert list(table.value["Next action"]) == [
        "Analyst review", "Analyst review", "Analyst review", "Customer outreach",
    ]
    assert table.value["Status"].tolist() == ["Confirmed", "No AI suggestion", "Not started", "With the customer"]
    assert table.value["Address on file"].tolist() == [
        "1 First St, Alpha City, TX, 75001",
        "2 Second St, Alpha City, TX, 75001",
        "",
        "3 Third St, Alpha City, TX, 75001",
    ]
    assert any(
        "4 addresses need a person: 3 for your analysts, 1 for customer outreach." in item.value
        for item in app.caption
    )
    assert all(text in markup(app) for text in (
        "Automated checks", "AI finds the cause; 1 go to the customer",
        "Precisely checks; 1 fixes ready to approve", "Need research. Analysts approve every change",
    ))
    assert "1 First St" in visible_markup(app)
    assert "1 Corrected St" in visible_markup(app)
    assert any(item.label == "Save work list to the client folder" for item in app.button)

    app.session_state[table.key] = {"selection": {"rows": [1], "columns": [], "cells": []}}
    app.run(timeout=30)
    assert "No AI suggestion</span>" in markup(app)
    app.session_state[table.key] = {"selection": {"rows": [2], "columns": [], "cells": []}}
    app.run(timeout=30)
    assert not app.exception
    detail = next(
        container for container in app.container
        if any("Policy ID: MISSING" in item.value for item in container.markdown)
    )
    detail_text = "\n".join(item.value for item in detail.markdown)
    assert "1 First St" not in detail_text
    assert "2 Second St" not in detail_text

    next(w for w in app.selectbox if w.label == "Filter by next action").set_value("customer_outreach").run(timeout=30)
    assert not app.exception
    table = next(frame for frame in app.dataframe if frame.key and frame.key.startswith("exceptions_table_"))
    assert list(table.value["Policy ID"]) == ["C"]
    assert table.value["Status"].tolist() == ["With the customer"]
    assert "With the customer</span>" in markup(app)
    assert any(
        "1 address needs a person: 0 for your analysts, 1 for customer outreach." in item.value
        for item in app.caption
    )
    next(w for w in app.selectbox if w.label == "Filter by next action").set_value("All").run(timeout=30)
    next(w for w in app.selectbox if w.label == "Client").set_value("Beta").run(timeout=30)
    assert not app.exception
    table = next(frame for frame in app.dataframe if frame.key and frame.key.startswith("exceptions_table_"))
    assert list(table.value["Policy ID"]) == ["Z"]
    assert "9 Beta St" in visible_markup(app)
    assert table.value["Status"].tolist() == ["With the customer"]
    assert "With the customer</span>" in markup(app)


def test_made_up_addresses_and_customer_status(sales_app):
    app = sales_app.app
    examples = json.loads((ROOT / "app" / "demo_data" / "demo_exceptions.json").read_text(encoding="utf-8"))
    assert all(
        "EXAMPLE" in row["address_line1"] and all(row.get(field) for field in ("city", "state", "zip5"))
        for row in examples
    )
    for row in examples:
        suggestion = row.get("suggestion", {})
        if suggestion:
            assert sum(row[field] != suggestion[suggestion_field] for field, suggestion_field in (
                ("address_line1", "line1"), ("city", "city"), ("zip5", "zip5")
            )) == 1
            assert row["address_line1"] != suggestion["line1"]
            original_words = row["address_line1"].split()
            suggestion_words = suggestion["line1"].split()
            assert len(original_words) == len(suggestion_words)
            changed = [(a, b) for a, b in zip(original_words, suggestion_words) if a != b]
            assert len(changed) == 1 and sorted(changed[0][0]) == sorted(changed[0][1])
            assert f"<mark>{changed[0][1]}</mark>" in app_data.highlight_changed_words(
                suggestion["line1"], row["address_line1"]
            )
    table = next(frame for frame in app.dataframe if frame.key and frame.key.startswith("exceptions_table_"))
    assert "Address on file" in table.value.columns
    assert all(table.value["Address on file"])
    assert "On file" in markup(app)
    next(w for w in app.selectbox if w.label == "Filter by next action").set_value("customer_outreach").run(timeout=30)
    table = next(frame for frame in app.dataframe if frame.key and frame.key.startswith("exceptions_table_"))
    assert table.value["Status"].tolist() == ["With the customer", "With the customer"]
    assert "With the customer</span>" in markup(app)
    app.session_state[table.key] = {"selection": {"rows": [1], "columns": [], "cells": []}}
    app.run(timeout=30)
    assert "With the customer</span>" in markup(app)
    assert "On file" in markup(app)


def test_exports_follow_filtered_table_in_both_modes(sales_app):
    from io import BytesIO
    from openpyxl import load_workbook

    app = sales_app.app
    next(w for w in app.selectbox if w.label == "Filter by next action").set_value("customer_outreach").run(timeout=30)
    table = next(frame for frame in app.dataframe if frame.key and frame.key.startswith("exceptions_table_"))
    download = load_workbook(BytesIO(sales_app.saved["download"])).active
    headers = [cell.value for cell in download[1]]
    assert headers == list(app_data.SALES_WORKLIST_COLUMNS)
    assert [row[0].value for row in list(download.rows)[1:]] == table.value["Policy ID"].tolist()
    assert [cell.value for cell in download[2]][:5] == table.value.iloc[0].tolist()
    next(w for w in app.radio if w.label == "Show:").set_value("Real results on this computer").run(timeout=30)
    next(w for w in app.button if w.label == "Save work list to the client folder").click().run(timeout=30)
    assert not app.exception
    table = next(frame for frame in app.dataframe if frame.key and frame.key.startswith("exceptions_table_"))
    sheet = load_workbook(sales_app.saved["path"]).active
    assert [cell.value for cell in sheet[1]] == headers
    assert [row[0].value for row in list(sheet.rows)[1:]] == table.value["Policy ID"].tolist()
    assert [cell.value or "" for cell in sheet[2]][:5] == table.value.iloc[0].tolist()


@pytest.mark.parametrize("real", [False, True])
def test_export_confirmed_status_matches_case_chip(sales_app, real):
    from io import BytesIO
    from openpyxl import load_workbook

    app = sales_app.app
    if real:
        next(w for w in app.radio if w.label == "Show:").set_value("Real results on this computer").run(timeout=30)
        next(w for w in app.button if w.label == "Save work list to the client folder").click().run(timeout=30)
        sheet = load_workbook(sales_app.saved["path"]).active
    else:
        sheet = load_workbook(BytesIO(sales_app.saved["download"])).active
    assert "Confirmed by Precisely</span>" in markup(app)
    assert sheet["E2"].value == "Confirmed by Precisely"


@pytest.mark.parametrize(
    ("error", "message", "element"),
    [
        (PermissionError, "The work list is open in Excel. Close it, then save again.", "warning"),
        (OSError, "The work list could not be saved. Check that the client folder is available.", "error"),
    ],
)
def test_real_save_io_error_is_reported_without_traceback(sales_app, monkeypatch, error, message, element):
    app = sales_app.app
    next(w for w in app.radio if w.label == "Show:").set_value("Real results on this computer").run(timeout=30)
    next(w for w in app.button if w.label == "Save work list to the client folder").click().run(timeout=30)
    assert sales_app.saved["path"].is_file()
    calls = []

    def fail_save(*args):
        calls.append(args)
        raise error("private operating system detail")

    monkeypatch.setattr(app_data, "save_real_exception_workbook", fail_save)
    next(w for w in app.button if w.label == "Save work list to the client folder").click().run(timeout=30)
    assert not app.exception
    assert len(calls) == 1
    assert any(item.value == message for item in getattr(app, element))
    assert not any("Work list saved to:" in item.value for item in app.success)
    assert "private operating system detail" not in markup(app)


def test_approving_a_confirmed_fix_logs_it_and_builds_the_corrections_file(sales_app, monkeypatch, tmp_path):
    from address_intelligence import worklist_decisions

    log = tmp_path / "decisions" / "worklist_decisions.jsonl"
    monkeypatch.setattr(worklist_decisions, "decision_log_path", lambda run_dir: log)
    app = sales_app.app
    next(w for w in app.radio if w.label == "Show:").set_value("Real results on this computer").run(timeout=30)
    assert not app.exception
    assert "Address to approve: **1 Corrected St, Alpha City, TX 75001**" in markup(app)
    assert any("No approved changes yet" in item.value for item in app.caption)

    next(b for b in app.button if b.label == "Approve the fix").click().run(timeout=30)
    assert not app.exception
    table = next(frame for frame in app.dataframe if frame.key and frame.key.startswith("exceptions_table_"))
    assert "001" not in list(table.value["Policy ID"])
    assert any("001 approved. It has left the open work list." in item.value for item in app.success)
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert entry["policy_id"] == "001" and entry["decision"] == "approved"
    assert entry["corrected_address"] == "1 Corrected St, Alpha City, TX 75001"
    corrections = frame_with(app, "Corrected address")
    assert list(corrections["Policy ID"]) == ["001"]
    assert any(item.label == "Save corrections file to the client folder" for item in app.button)

    next(b for b in app.button if b.label == "Undo").click().run(timeout=30)
    assert not app.exception
    table = next(frame for frame in app.dataframe if frame.key and frame.key.startswith("exceptions_table_"))
    assert "001" in list(table.value["Policy ID"])
    assert [json.loads(line)["decision"] for line in log.read_text(encoding="utf-8").splitlines()] == ["approved", "undone"]


def test_made_up_mode_keeps_decisions_in_the_session_only(sales_app, monkeypatch):
    from address_intelligence import worklist_decisions

    def no_file(*args, **kwargs):
        raise AssertionError("made-up mode must not write the audit log")

    monkeypatch.setattr(worklist_decisions, "append_log", no_file)
    app = sales_app.app
    approve = [b for b in app.button if b.label == "Approve the fix"]
    if not approve:
        pytest.skip("the first made-up case has no confirmed fix")
    approve[0].click().run(timeout=30)
    assert not app.exception
    assert any(item.label == "Download corrections file (Excel)" for item in app.get("download_button"))
