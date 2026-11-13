"""Analyst decisions: approve a confirmed fix, keep the unit, log for audit, export corrections."""

from datetime import datetime, timezone
from io import BytesIO
import importlib.util
import json
from pathlib import Path

from openpyxl import load_workbook
import pytest

from address_intelligence import worklist_decisions as wd

NOW = datetime(2026, 11, 18, 14, 5, tzinfo=timezone.utc)
CONFIRMED_ROW = {
    "policy_id": "POL-1",
    "next_action": "analyst_review",
    "verification_status": "verified by Precisely",
    "suggestion": {"line1": "1141 Napoleon Ave", "city": "New Orleans", "zip5": "70115"},
}
ON_FILE = {"address_line1": "1141 Napoheon Ave", "address_line2": "Apt 3", "city": "New Orleans", "state": "LA", "zip5": "70115"}


def test_corrected_address_keeps_the_unit_and_fills_the_state_from_the_file():
    parts = wd.corrected_address(CONFIRMED_ROW["suggestion"], ON_FILE)
    assert wd.format_address(parts) == "1141 Napoleon Ave, Apt 3, New Orleans, LA 70115"
    assert wd.unit_kept(parts)


def test_unit_is_not_repeated_when_the_fix_already_has_it():
    parts = wd.corrected_address({"line1": "1700 District Ave Ste 300", "city": "Burlington", "state": "MA", "zip5": "01803"}, {"address_line2": "Ste 300"})
    assert parts["line2"] == ""
    assert wd.format_address(parts) == "1700 District Ave Ste 300, Burlington, MA 01803"


def test_only_confirmed_analyst_fixes_can_be_approved():
    assert wd.is_confirmed_fix(CONFIRMED_ROW)
    assert not wd.is_confirmed_fix({**CONFIRMED_ROW, "next_action": "customer_outreach"})
    assert not wd.is_confirmed_fix({**CONFIRMED_ROW, "verification_status": "not verified"})
    assert not wd.is_confirmed_fix({**CONFIRMED_ROW, "suggestion": {}})
    assert not wd.is_confirmed_fix({**CONFIRMED_ROW, "suggestion_status": "rejected"})
    with pytest.raises(ValueError):
        wd.decision_entry(policy_id="POL-1", decision="approved", address_on_file="x", confirmed_by_precisely=False)
    with pytest.raises(ValueError):
        wd.decision_entry(policy_id="POL-1", decision="deleted", address_on_file="x")
    with pytest.raises(ValueError):
        wd.decision_entry(policy_id=" ", decision="rejected", address_on_file="x")


def test_approval_leaves_the_list_and_undo_brings_it_back(tmp_path):
    log = wd.decision_log_path(tmp_path)
    parts = wd.corrected_address(CONFIRMED_ROW["suggestion"], ON_FILE)
    wd.append_log(log, wd.decision_entry(
        policy_id="POL-1", decision="approved", address_on_file="1141 Napoheon Ave, Apt 3",
        corrected=parts, confirmed_by_precisely=True, now=NOW,
    ))
    queue = [CONFIRMED_ROW, {"policy_id": "POL-2", "next_action": "customer_outreach"}]
    latest = wd.latest_decisions(wd.read_log(log))
    assert [row["policy_id"] for row in wd.open_cases(queue, latest)] == ["POL-2"]
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert entry == {
        "time": "2026-11-18T14:05:00+00:00",
        "policy_id": "POL-1",
        "decision": "approved",
        "decided_by": "Analyst",
        "address_on_file": "1141 Napoheon Ave, Apt 3",
        "corrected_address": "1141 Napoleon Ave, Apt 3, New Orleans, LA 70115",
        "confirmed_by_precisely": True,
        "source": "Overnight run",
    }
    wd.append_log(log, wd.decision_entry(policy_id="POL-1", decision="undone", address_on_file="1141 Napoheon Ave, Apt 3"))
    latest = wd.latest_decisions(wd.read_log(log))
    assert latest == {}
    assert len(wd.open_cases(queue, latest)) == 2
    assert len(wd.read_log(log)) == 2  # the audit log keeps both lines


def test_a_rejected_fix_stays_on_the_list():
    entry = wd.decision_entry(policy_id="POL-1", decision="rejected", address_on_file="x", now=NOW)
    assert entry["corrected_address"] == ""
    assert len(wd.open_cases([CONFIRMED_ROW], wd.latest_decisions([entry]))) == 1


def test_corrections_file_lists_approved_changes_only(tmp_path):
    parts = wd.corrected_address(CONFIRMED_ROW["suggestion"], ON_FILE)
    entries = [
        wd.decision_entry(policy_id="POL-1", decision="approved", address_on_file="1141 Napoheon Ave, Apt 3", corrected=parts, confirmed_by_precisely=True, now=NOW),
        wd.decision_entry(policy_id="POL-2", decision="rejected", address_on_file="2 Main St", now=NOW),
    ]
    rows = wd.corrections_rows(wd.latest_decisions(entries))
    assert rows == [{
        "Policy ID": "POL-1",
        "Address on file": "1141 Napoheon Ave, Apt 3",
        "Corrected address": "1141 Napoleon Ave, Apt 3, New Orleans, LA 70115",
        "Confirmed by Precisely": "Yes",
        "Approved (UTC)": "2026-11-18 14:05:00",
        "Source": "Overnight run",
    }]
    sheet = load_workbook(BytesIO(wd.corrections_workbook(rows))).active
    assert sheet.title == "Approved changes"
    assert [cell.value for cell in sheet[1]] == list(wd.CORRECTIONS_COLUMNS)
    assert sheet["C2"].value == "1141 Napoleon Ave, Apt 3, New Orleans, LA 70115"
    with pytest.raises(ValueError):
        wd.made_up_corrections_workbook(rows)
    with pytest.raises(ValueError):
        wd.save_real_corrections(rows, tmp_path / "elsewhere", tmp_path / "data" / "output")
    saved = wd.save_real_corrections(rows, tmp_path / "data" / "output" / "run", tmp_path / "data" / "output")
    assert saved.name == "approved_corrections.xlsx" and saved.is_file()


def test_archive_moves_logs_aside_and_never_deletes(tmp_path):
    log = wd.decision_log_path(tmp_path / "out" / "client" / "run")
    wd.append_log(log, wd.decision_entry(policy_id="POL-1", decision="rejected", address_on_file="x"))
    moved = wd.archive_logs(tmp_path / "out", now=datetime(2026, 11, 18, 7, 30))
    assert not log.exists()
    assert [path.name for path in moved] == ["worklist_decisions_20261118-073000.jsonl"]
    assert moved[0].read_text(encoding="utf-8").strip()


def test_warmup_resets_the_work_list(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location(
        "live_warmup_reset", Path(__file__).resolve().parents[2] / "scripts" / "live_warmup.py"
    )
    warmup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(warmup)
    monkeypatch.setattr(warmup, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    log = wd.decision_log_path(tmp_path / "data" / "output" / "run")
    wd.append_log(log, wd.decision_entry(policy_id="POL-1", decision="rejected", address_on_file="x"))
    warmup.main(["--keep-decisions"])
    assert log.exists()
    warmup.main([])
    assert not log.exists()
    assert "Work list reset: 1 decision log(s) moved aside" in capsys.readouterr().out


def test_audit_rows_keep_every_decision_newest_first():
    entries = [
        wd.decision_entry(policy_id="POL-1", decision="approved", address_on_file="1141 Napoheon Ave, Apt 3",
                          corrected=wd.corrected_address(CONFIRMED_ROW["suggestion"], ON_FILE),
                          confirmed_by_precisely=True, now=NOW),
        wd.decision_entry(policy_id="POL-1", decision="undone", address_on_file="1141 Napoheon Ave, Apt 3", now=NOW),
    ]
    rows = wd.audit_rows(entries)
    assert [row["Decision"] for row in rows] == ["Undone", "Approved"]
    assert rows[1] == {
        "Time (UTC)": "2026-11-18 14:05:00",
        "Policy ID": "POL-1",
        "Decision": "Approved",
        "Decided by": "Analyst",
        "Address on file": "1141 Napoheon Ave, Apt 3",
        "Corrected address": "1141 Napoleon Ave, Apt 3, New Orleans, LA 70115",
        "Confirmed by Precisely": "Yes",
        "Source": "Overnight run",
    }


def test_case_card_suggestion_shows_the_unit_kept():
    from address_intelligence.app_data import format_sales_worklist_rows

    row = {**CONFIRMED_ROW, "input_address": ON_FILE}
    shown = format_sales_worklist_rows([row])[0]
    assert shown["Suggested address"] == "1141 Napoleon Ave, Apt 3, New Orleans 70115"
    no_unit = format_sales_worklist_rows([{**CONFIRMED_ROW, "input_address": {**ON_FILE, "address_line2": ""}}])[0]
    assert no_unit["Suggested address"] == "1141 Napoleon Ave, New Orleans 70115"
