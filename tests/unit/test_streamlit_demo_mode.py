from pathlib import Path
import csv
from io import StringIO

import pytest
from streamlit.testing.v1 import AppTest

from address_intelligence import app_data, onboarding
from address_intelligence.client_profile import load_client_profile


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app" / "streamlit_app.py"


@pytest.fixture
def offline_app(monkeypatch):
    import requests

    def reject_network(*args, **kwargs):
        raise AssertionError("The app must not make network requests")

    monkeypatch.setattr(requests.sessions.Session, "request", reject_network)
    monkeypatch.setattr(app_data, "load_summary_kpis", lambda path: None)
    monkeypatch.setattr(app_data, "load_exception_queue", lambda *args, **kwargs: ([], True))
    return AppTest.from_file(str(APP))


@pytest.mark.parametrize("value", [None, "0"])
def test_demo_mode_off_leaves_session_empty(monkeypatch, offline_app, value):
    if value is None:
        monkeypatch.delenv("AIP_DEMO_MODE", raising=False)
    else:
        monkeypatch.setenv("AIP_DEMO_MODE", value)
    app = offline_app.run(timeout=30)
    assert not app.exception
    assert app.session_state["uploaded_content"] is None
    assert app.session_state["uploaded_name"] == ""
    assert any("Upload a client file in tab 1" in element.value for element in app.info)
    assert next(radio for radio in app.radio if radio.label == "Show:").value == (
        "Made-up examples (safe to share)"
    )


def test_real_mode_without_results_does_not_claim_work_was_completed(monkeypatch, offline_app):
    monkeypatch.delenv("AIP_DEMO_MODE", raising=False)
    app = offline_app.run(timeout=30)
    next(radio for radio in app.radio if radio.label == "Show:").set_value(
        "Real results on this computer"
    ).run(timeout=30)
    assert not app.exception
    html = "\n".join(element.value for element in app.markdown)
    assert "Automated checks" not in html
    assert any("Live Precisely results. Private screen share only." in item.value for item in app.warning)


def test_header_is_visible_before_and_after_loading_sample(monkeypatch, offline_app):
    monkeypatch.delenv("AIP_DEMO_MODE", raising=False)
    app = offline_app.run(timeout=30)
    assert not app.exception
    header = [item.value for item in app.markdown if '<div class="ai-header">' in item.value]
    assert len(header) == 1
    assert "Address Intelligence" in header[0] and "Runs on this computer" in header[0]

    next(button for button in app.button if button.label == "Use made-up sample client").click().run(timeout=30)
    assert not app.exception
    header = [item.value for item in app.markdown if '<div class="ai-header">' in item.value]
    assert len(header) == 1
    client_name = next(item for item in app.text_input if item.label == "Client name").value
    assert f"<strong>{client_name}</strong>" in header[0]
    assert "Loaded file:" not in header[0]
    assert "records" in header[0]


def test_demo_mode_preloads_mapped_file_and_preflight(monkeypatch, offline_app):
    monkeypatch.setenv("AIP_DEMO_MODE", "1")

    def reject_guess(*args, **kwargs):
        raise AssertionError("The demo mapping must come from the client profile")

    monkeypatch.setattr(onboarding, "suggest_mapping", reject_guess)
    app = offline_app.run(timeout=30)
    assert not app.exception
    assert app.session_state["uploaded_name"] == "marova_policy_sample.csv"
    expected_mapping = load_client_profile().column_mapping
    assert app.session_state["onboarding_mapping"] == expected_mapping
    assert len(app.session_state["uploaded_content"].splitlines()) == 201
    for field, column in expected_mapping.items():
        assert column in [widget.value for widget in app.selectbox]
    assert next(widget for widget in app.text_input if widget.label == "Client name").value == (
        "Marova Insurance"
    )
    assert next(radio for radio in app.radio if radio.label == "Show:").value == (
        "Made-up examples (safe to share)"
    )
    assert any("200 records in the uploaded file." in element.value for element in app.caption)
    assert all("Free check on the submitted file" not in element.value for element in app.caption)
    assert any("A free check on" in element.value for element in app.markdown)
    assert any(
        "Out of" in frame.value.columns and set(frame.value["Out of"]) == {200}
        for frame in app.dataframe
    )
    assert next(widget for widget in app.number_input if widget.label == "Policies to price (full book)").value == 200
    visible = " ".join(
        str(element.value)
        for elements in (app.markdown, app.caption, app.info, app.warning, app.error)
        for element in elements
    ).casefold()
    assert "synthetic" not in visible
    assert "fictional" not in visible


def test_demo_mode_preserves_file_already_loaded_at_start(monkeypatch, offline_app):
    monkeypatch.setenv("AIP_DEMO_MODE", "1")
    content = b"Policy,Street,City,State,ZIP\nP1,1 MAIN ST,EXAMPLE,TX,78701\n"
    offline_app.session_state["uploaded_content"] = content
    offline_app.session_state["uploaded_name"] = "existing.csv"
    app = offline_app.run(timeout=30)
    assert not app.exception
    assert app.session_state["uploaded_content"] == content
    assert app.session_state["uploaded_name"] == "existing.csv"


def test_sample_button_updates_preflight_headline_and_share(monkeypatch, offline_app):
    monkeypatch.delenv("AIP_DEMO_MODE", raising=False)
    app = offline_app.run(timeout=30)
    next(button for button in app.button if button.label == "Use made-up sample client").click().run(timeout=30)
    assert not app.exception
    content = app.session_state["uploaded_content"].decode("utf-8-sig")
    rows = list(csv.DictReader(StringIO(content)))
    summaries = onboarding.preflight_group_summaries(
        onboarding.preflight_checks(rows, app.session_state["onboarding_mapping"])
    )
    clean = next(int(row["count"]) for row in summaries if row["group"] == "No issues found")
    headline = app_data.preflight_headline(clean, len(rows))
    html = "\n".join(element.value for element in app.markdown)
    assert headline in html
    for row in summaries:
        if row["percentage"]:
            assert f'{row["group"]}: {row["count"]} · {row["percentage"]:.0f}%' in html


def test_demo_mode_does_not_replace_later_upload_or_sample(monkeypatch, offline_app):
    import streamlit as st

    monkeypatch.setenv("AIP_DEMO_MODE", "1")
    app = offline_app.run(timeout=30)
    content = b"Policy,Street,City,State,ZIP\nP1,1 MAIN ST,EXAMPLE,TX,78701\n"

    class Upload:
        name = "later.csv"

        def getvalue(self):
            return content

    monkeypatch.setattr(
        st, "file_uploader",
        lambda *args, **kwargs: Upload() if kwargs.get("key") == "demo_uploader_0" else None,
    )
    app.run(timeout=30)
    assert not app.exception
    assert app.session_state["uploaded_content"] == content
    assert app.session_state["uploaded_name"] == "later.csv"
    app.run(timeout=30)
    assert app.session_state["uploaded_content"] == content
    next(button for button in app.button if button.label == "Use made-up sample client").click().run(timeout=30)
    assert not app.exception
    assert app.session_state["uploaded_name"] == "sample_client.csv"
    assert next(widget for widget in app.text_input if widget.label == "Client name").value == (
        "Marova Insurance"
    )
    app.run(timeout=30)
    assert app.session_state["uploaded_name"] == "sample_client.csv"
