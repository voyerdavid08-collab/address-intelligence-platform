"""Tests for the live Investigate flow. Every service is faked: no network calls."""

import json

import pytest

from address_intelligence import live_ai
from address_intelligence.live_ai import (
    FIXED_ORDER,
    MAX_TOOL_CALLS,
    Investigator,
    LiveAIError,
    check_fix_guardrails,
    made_up_investigation,
    record_decision,
    summarize_address_result,
    summarize_flood_result,
)

VERIFY_PAYLOAD = {
    "action_id": "geo_addressing.verify_address",
    "result": {"responses": [{"status": "OK", "results": [{
        "score": 100,
        "address": {
            "formattedAddress": "1700 DISTRICT AVE, BURLINGTON MA 01803-5230",
            "formattedStreetAddress": "1700 DISTRICT AVE",
            "addressNumber": "1700",
            "city": {"longName": "BURLINGTON"},
            "admin1": {"shortName": "MA"},
            "postalCode": "01803",
            "postalCodeExt": "5230",
        },
        "explanation": {"addressMatch": {"description": [
            {"label": "street", "matchType": "EXACT"},
            {"label": "postalCode", "matchType": "CORRECTED"},
        ]}},
        "customFields": {"MATCH_CODE": "S800", "PB_KEY": "P0000BJ68DL3", "SECRET_FIELD": "x"},
    }]}]},
}


class FakeRoute:
    name = "fake route"

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def call(self, tool, address):
        self.calls.append((tool, address))
        if tool == self.fail_on:
            raise LiveAIError("down")
        if tool == "get_flood_risk":
            return {"floodZone": "X", "riskLevel": "Low"}
        return {"found": True, "score": 100, "street_line": "1700 DISTRICT AVE", "latitude": 42.5, "longitude": -71.2}


class FakeResponse:
    def __init__(self, message, status=200):
        self.status_code = status
        self._message = message

    def json(self):
        return {"choices": [{"message": self._message}]}


def final_answer(fix=None, next_step="approve_fix"):
    return {
        "content": json.dumps({
            "finding": "The ZIP was wrong.",
            "likely_cause": "Wrong ZIP",
            "why_it_matters": "Flood rating uses the location.",
            "has_fix": fix is not None,
            "proposed_fix": fix or {"line1": "", "city": "", "state": "", "zip5": ""},
            "next_step": next_step,
        })
    }


class ScriptedPost:
    """Returns pre-set AI messages in order and records every payload."""

    def __init__(self, messages, status=200):
        self.messages = list(messages)
        self.payloads = []
        self.status = status

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.payloads.append(json)
        return FakeResponse(self.messages.pop(0), self.status)


def tool_call(name, call_id="c1", address="1700 District Ave, Burlington, MA 01803"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps({"address": address})}}


ADDRESS = "1700 District Ave, Burlington, MA 01804"
FIX = {"line1": "1700 District Ave", "city": "Burlington", "state": "MA", "zip5": "01803"}


def make(route, post, **kwargs):
    return Investigator(route, groq_api_key="test-key", model="test-model", post=post, **kwargs)


def test_summary_keeps_only_needed_fields():
    summary = summarize_address_result(VERIFY_PAYLOAD)
    assert summary["score"] == 100
    assert summary["zip5"] == "01803"
    assert summary["precisely_id"] == "P0000BJ68DL3"
    assert summary["fields_corrected"] == ["postalCode"]
    assert "SECRET_FIELD" not in json.dumps(summary)


def test_flood_summary_keeps_business_fields():
    summary = summarize_flood_result({"result": {"floodZone": {"code": "AE", "description": "High risk"}, "other": "noise"}})
    assert summary["floodZone.code"] == "AE"
    assert "other" not in summary


def test_ai_chosen_tools_then_fix_rechecked_by_precisely(tmp_path):
    route = FakeRoute()
    post = ScriptedPost([
        {"content": "", "tool_calls": [tool_call("verify_address")]},
        {"content": "", "tool_calls": [tool_call("geocode_address", "c2"), tool_call("get_flood_risk", "c3")]},
        {"content": "done"},
        final_answer(FIX),
    ])
    usage = tmp_path / "live_ai" / "usage.jsonl"
    result = make(route, post, usage_log=usage).investigate(ADDRESS)
    assert [tool for tool, _ in route.calls] == ["verify_address", "geocode_address", "get_flood_risk", "verify_address"]
    assert route.calls[-1][1] == "1700 District Ave, Burlington, MA 01803"
    assert result.fix_confirmed is True
    assert result.steps[-1].label == "Precisely re-checks the AI's fix"
    assert len(usage.read_text().splitlines()) == 4
    assert all("test-key" not in json.dumps(payload) for payload in post.payloads)


def test_only_the_address_goes_to_the_ai():
    post = ScriptedPost([{"content": ""}, final_answer()])
    result = make(FakeRoute(), post).investigate(ADDRESS)
    first_user = post.payloads[0]["messages"][1]["content"]
    assert json.loads(first_user) == {"address_to_investigate": ADDRESS}
    assert result.ai_input == {"address_to_investigate": ADDRESS, "tools_allowed": list(live_ai.TOOL_ACTIONS)}


def test_fixed_order_runs_three_tools_without_ai_choice():
    route = FakeRoute()
    post = ScriptedPost([final_answer()])
    result = make(route, post, fixed_order=True).investigate(ADDRESS)
    assert tuple(tool for tool, _ in route.calls) == FIXED_ORDER
    assert result.proposed_fix is None
    assert "tools" not in post.payloads[0]


def test_unknown_tool_is_refused():
    post = ScriptedPost([{"content": "", "tool_calls": [tool_call("delete_everything")]}])
    with pytest.raises(LiveAIError):
        make(FakeRoute(), post).investigate(ADDRESS)


def test_tool_call_cap_is_enforced():
    loop = [{"content": "", "tool_calls": [tool_call("verify_address", f"c{i}")]} for i in range(MAX_TOOL_CALLS + 2)]
    with pytest.raises(LiveAIError):
        make(FakeRoute(), ScriptedPost(loop)).investigate(ADDRESS)


def test_ai_cannot_point_tools_at_another_address():
    route = FakeRoute()
    post = ScriptedPost([
        {"content": "", "tool_calls": [tool_call("verify_address", address="10 Other St, Boston, MA")]},
        {"content": ""},
        final_answer(),
    ])
    make(route, post).investigate(ADDRESS)
    assert route.calls == [("verify_address", ADDRESS)]


def test_timeout_raises_for_fallback():
    ticks = iter([0, 0, 25, 25, 25, 25])
    with pytest.raises(LiveAIError):
        make(FakeRoute(), ScriptedPost([final_answer()]), fixed_order=True, clock=lambda: next(ticks)).investigate(ADDRESS)


def test_service_failure_raises_for_fallback():
    with pytest.raises(LiveAIError):
        make(FakeRoute(fail_on="geocode_address"), ScriptedPost([final_answer()]), fixed_order=True).investigate(ADDRESS)


def test_ai_error_status_raises_for_fallback():
    with pytest.raises(LiveAIError):
        make(FakeRoute(), ScriptedPost([{"content": ""}], status=500)).investigate(ADDRESS)


@pytest.mark.parametrize(
    ("fix", "kept"),
    [
        ({"line1": "1700 District Ave", "city": "Burlington", "state": "MA", "zip5": "01803"}, True),
        ({"line1": "1800 District Ave", "city": "Burlington", "state": "MA", "zip5": "01803"}, False),
        ({"line1": "1700 District Ave", "city": "Burlington", "state": "NH", "zip5": "01803"}, False),
    ],
)
def test_guardrails_on_house_number_and_state(fix, kept):
    result, note = check_fix_guardrails(fix, "1700 District Ave, Burlington, MA 01804")
    assert (result is not None) is kept
    assert bool(note) is not kept


def test_unconfirmed_fix_is_labelled_not_confirmed():
    class NoMatchRoute(FakeRoute):
        def call(self, tool, address):
            out = super().call(tool, address)
            return {**out, "street_line": "SOMETHING ELSE"} if "01803" in address else out

    post = ScriptedPost([final_answer(FIX)])
    result = make(NoMatchRoute(), post, fixed_order=True).investigate(ADDRESS)
    assert result.fix_confirmed is False


def test_decision_log_records_without_touching_anything_else(tmp_path):
    log = tmp_path / "live_ai" / "decisions.jsonl"
    entry = record_decision(log, case_id="POL-1", decision="approved", investigation={"address": "a", "proposed_fix": FIX, "fix_confirmed": True})
    assert entry["decision"] == "approved"
    assert json.loads(log.read_text())["case"] == "POL-1"
    assert [p.name for p in tmp_path.rglob("*") if p.is_file()] == ["decisions.jsonl"]
    with pytest.raises(ValueError):
        record_decision(log, case_id="x", decision="maybe", investigation={})


def test_made_up_result_is_labelled_and_makes_no_calls():
    result = made_up_investigation("101 EXAMPLE OKA ST, AUSTIN, TX 78701")
    assert result["made_up"] is True
    assert result["proposed_fix"]["line1"] == "101 EXAMPLE OAK ST"
    assert result["finding"].startswith("Made-up example")


def test_missing_keys_are_refused():
    with pytest.raises(LiveAIError):
        Investigator(FakeRoute(), groq_api_key="", model="m")
    with pytest.raises(LiveAIError):
        live_ai.McpRoute("")


def test_live_settings_and_readiness(tmp_path, monkeypatch):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))
    import live_panel

    for name in ("AIP_LIVE_AI", "AIP_LIVE_ROUTE", "AIP_LIVE_ORDER", "PRECISELY_MCP_APP_TOKEN", "GROQ_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / ".env").write_text("GROQ_API_KEY=g\nPRECISELY_MCP_APP_TOKEN=t\n", encoding="utf-8")
    settings = live_panel.live_settings(tmp_path)
    assert settings["enabled"] is False
    monkeypatch.setenv("AIP_LIVE_AI", "1")
    monkeypatch.setenv("AIP_LIVE_ORDER", "fixed")
    settings = live_panel.live_settings(tmp_path)
    assert settings["enabled"] and settings["fixed_order"] and settings["route"] == "mcp"
    assert live_panel._ready(settings)
    assert not live_panel._ready({**settings, "groq_key": ""})
    assert not live_panel._ready({**settings, "route": "direct", "has_direct_keys": False})
