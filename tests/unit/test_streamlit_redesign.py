from pathlib import Path
from types import SimpleNamespace

from streamlit.testing.v1 import AppTest

from address_intelligence import app_data, client_profile


ROOT = Path(__file__).resolve().parents[2]


def test_result_views_use_selected_client_data_and_keep_mode_protection(monkeypatch, tmp_path):
    import requests

    def reject_network(*args, **kwargs):
        raise AssertionError("Unexpected network request")

    monkeypatch.setattr(requests.sessions.Session, "request", reject_network)
    monkeypatch.delenv("AIP_DEMO_MODE", raising=False)
    input_path = tmp_path / "input.csv"
    input_path.write_text("policy_id\nA\nB\nC\nD\nE\n", encoding="utf-8")
    (tmp_path / "before_after_summary.csv").touch()
    profile = SimpleNamespace(
        input_path=input_path,
        run_dir=tmp_path,
        reports_dir=tmp_path,
        data={"client": {"name": "Example Insurer"}},
        column_mapping={"policy_id": "policy_id"},
    )
    monkeypatch.setattr(client_profile, "load_client_profile", lambda *args, **kwargs: profile)
    metrics = [
        {"metric": "Eligible", "after": "4/5 (80.0%)"},
        {"metric": "Coarse location understated verified risk", "numerator": "1"},
        {"metric": "Coarse location overstated verified risk", "numerator": "2"},
    ]
    kpis = {
        "has_reference": True,
        "correct_building": {"label": "Right address", "before": "1/5 (20.0%)", "after": "4/5 (80.0%)"},
        "dirty_fully_correct": {"label": "Fully correct", "before": "0/2", "after": "1/2"},
        "flood_tier_changes": {"value": "3/5"},
        "ai_verified": {"label": "AI fixes", "correct": "1", "verified": "1", "incorrect": "0"},
        "defects": [
            {"defect": "street_typo", "before": "0/2 (0.0%)", "after": "1/2 (50.0%)"},
            {"defect": "missing_house_number", "before": "0/1 (0.0%)", "after": "0/1 (0.0%)"},
        ],
    }
    queue = [
        {
            "policy_id": "A", "next_action": "analyst_review",
            "manual_review_reason": "street-level only",
            "suggestion_status": "suggested, unverified",
            "verification_status": "verified by Precisely",
            "suggestion": {"line1": "1 Example St", "city": "Example", "zip5": "12345"},
            "rationale": "Review the address.",
        },
        {"policy_id": "B", "next_action": "analyst_review", "verification_status": "not_started"},
        {"policy_id": "C", "next_action": "customer_outreach", "verification_status": "not_started"},
    ]
    monkeypatch.setattr(app_data, "load_summary_kpis", lambda path: kpis)
    monkeypatch.setattr(app_data, "read_summary_csv", lambda path: metrics)
    monkeypatch.setattr(app_data, "load_exception_queue", lambda *args, **kwargs: (queue, kwargs.get("demo", False)))
    app = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py")).run(timeout=30)
    assert not app.exception
    html = "\n".join(element.value for element in app.markdown)
    assert "4 of 5 addresses are right, up from 1." in html
    assert "80.0%" in html
    assert "width:50.00%" in html
    results_grid = next(element.value for element in app.markdown if 'class="ai-kpi-grid"' in element.value)
    assert "repeat(3,minmax(0,1fr))" in results_grid
    assert "AI finds the cause" not in html
    assert any("Made-up example data, not Precisely output." in element.value for element in app.info)
    show = next(radio for radio in app.radio if radio.label == "Show:")
    assert show.value == "Made-up examples (safe to share)"
    show.set_value("Real results on this computer").run(timeout=30)
    assert not app.exception
    html = "\n".join(element.value for element in app.markdown)
    assert "Automated checks" in html and "AI finds the cause" in html
    assert "Precisely checks" in html and "Need research. Analysts approve every change" in html
    assert any("Live Precisely results. Private screen share only." in item.value for item in app.warning)
    assert any(
        list(frame.value.columns) == ["Policy ID", "Address on file", "Next action", "Why flagged", "Status"]
        for frame in app.dataframe
    )
    assert "AI suggested" in html


def test_result_without_reference_uses_eligible_without_before_after(monkeypatch, tmp_path):
    import requests

    def reject_network(*args, **kwargs):
        raise AssertionError("Unexpected network request")

    monkeypatch.setattr(requests.sessions.Session, "request", reject_network)
    monkeypatch.delenv("AIP_DEMO_MODE", raising=False)
    input_path = tmp_path / "input.csv"
    input_path.write_text("policy_id\nX\nY\nZ\n", encoding="utf-8")
    (tmp_path / "before_after_summary.csv").touch()
    profile = SimpleNamespace(
        input_path=input_path,
        run_dir=tmp_path,
        reports_dir=tmp_path,
        data={"client": {"name": "Second Insurer"}},
    )
    monkeypatch.setattr(client_profile, "load_client_profile", lambda *args, **kwargs: profile)
    monkeypatch.setattr(
        app_data, "load_summary_kpis",
        lambda path: {"has_reference": False, "correct_building": {"before": "n/a", "after": "n/a"}},
    )
    monkeypatch.setattr(
        app_data, "read_summary_csv",
        lambda path: [{"metric": "Eligible", "after": "2/3 (66.7%)"}],
    )
    monkeypatch.setattr(app_data, "load_fallback_kpis", lambda path: [
        {"label": "Located automatically", "value": "2/3 (66.7%)"},
        {"label": "Enriched with flood data", "value": "2/3"},
        {"label": "Sent to manual review", "value": "1/3"},
    ])
    monkeypatch.setattr(app_data, "load_exception_queue", lambda *args, **kwargs: ([], True))
    app = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py")).run(timeout=30)
    assert not app.exception
    html = "\n".join(element.value for element in app.markdown)
    assert "2 of 3 addresses were located automatically." in html
    band = next(element.value for element in app.markdown if 'class="ai-results-band"' in element.value)
    assert "66.7%" in band
    assert "Before" not in band and "After" not in band
    results_grid = next(element.value for element in app.markdown if 'class="ai-kpi-grid"' in element.value)
    assert "repeat(2,minmax(0,1fr))" in results_grid


def test_work_list_selection_filters_and_detail(monkeypatch, tmp_path):
    import requests

    def reject_network(*args, **kwargs):
        raise AssertionError("Unexpected network request")

    monkeypatch.setattr(requests.sessions.Session, "request", reject_network)
    monkeypatch.delenv("AIP_DEMO_MODE", raising=False)
    profile = SimpleNamespace(
        input_path=tmp_path / "input.csv", run_dir=tmp_path, reports_dir=tmp_path,
        data={"client": {"name": "Selection Insurer"}},
        column_mapping={"policy_id": "policy_id"},
    )
    queue = [
        {
            "policy_id": "A", "next_action": "analyst_review",
            "suggestion_status": "suggested, unverified",
            "verification_status": "verified by Precisely",
            "suggestion": {"line1": "1 Example St", "city": "Example"},
            "manual_review_reason": "street-level only",
            "rationale": "Possible street_name_issue needs review.",
            "narrative": "The address could not be verified to building level. Flood enrichment was withheld pending address remediation.",
            "precision_code": "S4", "ai_cause_set": "street_name_issue", "confidence": "0.91",
        },
        {
            "policy_id": "B", "next_action": "analyst_review",
            "suggestion_status": "no_suggestion", "verification_status": "not_started",
        },
        {
            "policy_id": "C", "next_action": "customer_outreach",
            "suggestion_status": "suggested, unverified",
            "verification_status": "verified by Precisely",
            "suggestion": {"line1": "3 Example St"},
            "verification": {"verified_incorrect": True},
            "rationale": "Requires customer confirmation.",
        },
        {"policy_id": "D", "next_action": "customer_outreach", "verification": {"verified_correct": False}},
    ]
    monkeypatch.setattr(client_profile, "load_client_profile", lambda *args, **kwargs: profile)
    monkeypatch.setattr(app_data, "load_summary_kpis", lambda path: None)
    monkeypatch.setattr(app_data, "load_exception_queue", lambda *args, **kwargs: (queue, True))
    monkeypatch.setattr(app_data, "made_up_exception_workbook", lambda rows: b"fixture workbook")
    app = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py")).run(timeout=30)
    assert not app.exception

    def work_table():
        return next(frame for frame in app.dataframe if frame.key and frame.key.startswith("exceptions_table_"))

    def markup():
        return "\n".join(item.value for item in app.markdown)

    def parent_of(root, target):
        for child in getattr(root, "children", {}).values():
            if child is target:
                return root
            parent = parent_of(child, target)
            if parent is not None:
                return parent
        return None

    assert "<strong>Policy ID: A</strong>" in markup()
    assert "ai-case-status" in markup() and "Confirmed by Precisely</span>" in markup()
    assert any(item.value == "Click a row to open the case." for item in app.caption)
    panels = next(item.value for item in app.markdown if 'class="ai-case-panels"' in item.value)
    assert "Suggested address" in panels and "What the AI found" in panels and "Summary for operations" in panels
    assert "Possible street name issue needs review." in panels
    assert "The address could not be confirmed at the building level. Flood data is on hold until the address is corrected." in panels
    assert "repeat(2,minmax(0,1fr))" in panels and panels.count('class="ai-case-panel"') == 3
    assert panels.count("grid-column:1 / -1;") == 1
    assert panels.index("grid-column:1 / -1;") > panels.index("What the AI found")
    assert "#F5EFFC" in panels and panels.count("#FFFFFF") == 2
    assert "**Precisely verification**" not in markup()
    assert "AI proposes, Precisely verifies: every suggestion goes to an analyst for review." not in markup()
    technical = next(item for item in app.expander if item.label == "Technical detail")
    assert technical.proto.expanded is False
    technical_html = "\n".join(item.value for item in technical.markdown)
    assert "repeat(3,minmax(0,1fr))" in technical_html
    assert "grid-column:1 / -1;" not in technical_html
    assert "@media(max-width:600px)" in technical_html
    for label in ("Match level", "AI cause", "Confidence"):
        assert label in technical_html
    assert technical_html.count('class="ai-help"') == 3
    assert '[role="tabpanel"] [data-testid="stVerticalBlock"]' in markup()
    assert "gap: var(--ai-section-spacing) !important" in markup()
    assert all(item.label != "Exception detail" for item in app.selectbox)
    flow = next(item.value for item in app.markdown if 'class="ai-case-flow"' in item.value)
    assert flow.index("AI suggested") < flow.index("Precisely confirmed") < flow.index("Analyst decides")
    assert flow.count('aria-hidden="true">→</span>') == 2
    assert "color:#1B1433" in flow
    assert "background:transparent;border:1px solid #8017E1" in flow
    action_filter = next(item for item in app.selectbox if item.label == "Filter by next action")
    verification_filter = next(item for item in app.selectbox if item.label == "Filter by verification status")
    show = next(item for item in app.radio if item.label == "Show:")
    assert show.proto.label_visibility.value == 2
    action_column = parent_of(app._tree, action_filter)
    verification_column = parent_of(app._tree, verification_filter)
    assert action_column.type == verification_column.type == "column"
    assert parent_of(app._tree, action_column) is parent_of(app._tree, verification_column)
    detail = next(
        container for container in app.container
        if any("<strong>Policy ID: A</strong>" in item.value for item in container.markdown)
    )
    assert parent_of(app._tree, work_table()) is parent_of(app._tree, detail)
    parent = parent_of(app._tree, work_table())
    siblings = list(parent.children.values())
    save_button = next(item for item in siblings if item.type == "download_button")
    assert siblings.index(work_table()) < siblings.index(save_button) < siblings.index(detail)

    app.session_state[work_table().key] = {"selection": {"rows": [1], "columns": [], "cells": []}}
    app.run(timeout=30)
    assert not app.exception
    assert "<strong>Policy ID: B</strong>" in markup()
    assert 'class="ai-case-panels"' not in markup()
    assert all(item.label != "Technical detail" for item in app.expander)
    assert "Analyst decides</span>" in markup()

    old_key = work_table().key
    original_order = queue[:]
    queue[:] = [queue[2], queue[0], queue[1], queue[3]]
    app.run(timeout=30)
    assert not app.exception
    assert work_table().key == old_key
    assert list(work_table().value["Policy ID"]) == ["A", "B", "C", "D"]
    assert "<strong>Policy ID: B</strong>" in markup()

    queue[:] = original_order
    app.run(timeout=30)
    assert not app.exception
    assert "<strong>Policy ID: B</strong>" in markup()

    app.session_state[work_table().key] = {"selection": {"rows": [3], "columns": [], "cells": []}}
    app.run(timeout=30)
    assert not app.exception
    assert "<strong>Policy ID: D</strong>" in markup()
    assert "**Precisely verification**" not in markup()
    assert "With the customer</span>" in markup() and 'class="ai-case-panels"' not in markup()
    assert 'class="ai-case-flow"' not in markup()

    next(item for item in app.selectbox if item.label == "Filter by next action").set_value("customer_outreach")
    app.run(timeout=30)
    assert not app.exception
    assert list(work_table().value["Policy ID"]) == ["C", "D"]
    assert list(work_table().value["Status"]) == ["With the customer", "With the customer"]
    assert "<strong>Policy ID: C</strong>" in markup()
    assert "With the customer</span>" in markup()
    flow = next(item.value for item in app.markdown if 'class="ai-case-flow"' in item.value)
    assert "Precisely confirmed" not in flow and "AI suggested" in flow
    panels = next(item.value for item in app.markdown if 'class="ai-case-panels"' in item.value)
    assert "repeat(2,minmax(0,1fr))" in panels
    assert "grid-column:1 / -1;" not in panels

    next(item for item in app.selectbox if item.label == "Filter by verification status").set_value("not_started")
    app.run(timeout=30)
    assert not app.exception
    assert any(item.value == "No work items match these filters." for item in app.info)
    assert all(not frame.key or not frame.key.startswith("exceptions_table_") for frame in app.dataframe)
    assert "<strong>Policy ID:" not in markup()

    next(item for item in app.selectbox if item.label == "Filter by next action").set_value("All")
    app.run(timeout=30)
    assert not app.exception
    assert list(work_table().value["Policy ID"]) == ["B"]
    assert "<strong>Policy ID: B</strong>" in markup()

    next(item for item in app.radio if item.label == "Show:").set_value("Real results on this computer")
    app.run(timeout=30)
    assert not app.exception
    assert any("Live Precisely results. Private screen share only." in item.value for item in app.warning)
    assert not app.download_button
    assert any(item.label == "Save work list to the client folder" for item in app.button)
    save_button = next(item for item in app.button if item.label == "Save work list to the client folder")
    detail = next(
        container for container in app.container
        if any("<strong>Policy ID: B</strong>" in item.value for item in container.markdown)
    )
    siblings = list(parent_of(app._tree, work_table()).children.values())
    assert siblings.index(work_table()) < siblings.index(save_button) < siblings.index(detail)
    assert "data-testid=\"stElementToolbar\"" in markup()
