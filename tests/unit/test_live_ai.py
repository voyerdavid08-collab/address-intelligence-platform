"""Tests for the live Investigate flow. Every service is faked: no network calls."""

import json

import pytest

from address_intelligence import live_ai
from address_intelligence.live_ai import (
    MAX_TOOL_CALLS,
    Investigator,
    LiveAIError,
    check_fix_guardrails,
    confirms_address,
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

TYPO = "1141 Napoheon Ave, Apt 3, New Orleans, LA, 70115"
NAPOLEON = {"line1": "1141 Napoleon Ave Apt 3", "city": "New Orleans", "state": "LA", "zip5": "70115"}
HARVEY_CHECK = {"found": True, "score": 90, "street_line": "1141 NEW ORLEANS AVE", "city": "HARVEY", "state": "LA", "zip5": "70058"}
NAPOLEON_CHECK = {"found": True, "score": 98, "street_line": "1141 NAPOLEON AVE APT 3", "city": "NEW ORLEANS", "state": "LA", "zip5": "70115"}
HQ = "1700 District Ave, Burlington, MA 01803"
HQ_CHECK = {"found": True, "score": 100, "street_line": "1700 DISTRICT AVE", "city": "BURLINGTON", "state": "MA", "zip5": "01803"}


class FakeRoute:
    """Answers like Precisely: the typo matches Harvey, the corrected spelling matches Napoleon Ave."""

    name = "fake route"

    def __init__(self, fail_on=None, answers=None):
        self.calls = []
        self.fail_on = fail_on
        self.answers = answers or {}

    def call(self, tool, address):
        self.calls.append((tool, address))
        if tool == self.fail_on:
            raise LiveAIError("down")
        if tool == "get_flood_risk":
            return {"floodZone": "X"}
        if tool == "geocode_address":
            return {"found": True, "latitude": 29.9, "longitude": -90.1}
        if address in self.answers:
            return self.answers[address]
        if "Napoleon" in address:
            return NAPOLEON_CHECK
        if "Napoheon" in address:
            return HARVEY_CHECK
        return HQ_CHECK


class FakeResponse:
    def __init__(self, message, status=200):
        self.status_code = status
        self._message = message

    def json(self):
        return {"choices": [{"message": self._message}]}


class ScriptedPost:
    def __init__(self, messages, status=200):
        self.messages = list(messages)
        self.payloads = []
        self.status = status

    def __call__(self, url, headers=None, json=None, timeout=None):
        self.payloads.append(json)
        return FakeResponse(self.messages.pop(0), self.status)


def corrections(*candidates):
    return {"content": json.dumps({"candidates": list(candidates)})}


def explanation(next_step="analyst_research"):
    return {"content": json.dumps({
        "finding": "The street name was misspelled.",
        "likely_cause": "Street name typo",
        "why_it_matters": "Flood rating needs the exact location.",
        "next_step": next_step,
    })}


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
    payload = {"result": {"getByAddress": {"floodRisk": {
        "metadata": {"pageNumber": 1, "count": 1},
        "data": [{"floodZone": "AE", "zoneDescription": "High risk", "baseFloodElevation": 12, "other": "noise"}],
    }}}}
    summary = summarize_flood_result(payload)
    assert summary == {"found": True, "Flood zone": "AE", "What the zone means": "High risk", "Base flood elevation": 12}
    assert summarize_flood_result({"result": {"getByAddress": {"floodRisk": None}}}) == {"found": False}


def test_typo_case_ai_corrects_and_precisely_confirms(tmp_path):
    route = FakeRoute()
    post = ScriptedPost([corrections(NAPOLEON), explanation()])
    usage = tmp_path / "live_ai" / "usage.jsonl"
    result = make(route, post, usage_log=usage).investigate(TYPO)
    assert [tool for tool, _ in route.calls] == ["verify_address", "verify_address", "geocode_address", "get_flood_risk"]
    assert route.calls[1][1] == "1141 Napoleon Ave Apt 3, New Orleans, LA 70115"
    assert result.proposed_fix == NAPOLEON
    assert result.fix_confirmed is True
    assert result.next_step == "approve_fix"
    assert "different place" in result.guardrail_note
    assert any("Precisely checks the AI's correction" in step.label for step in result.steps)
    assert len(usage.read_text().splitlines()) == 4
    assert all("test-key" not in json.dumps(payload) for payload in post.payloads)


def test_clean_address_needs_no_correction():
    route = FakeRoute()
    post = ScriptedPost([explanation("no_change_needed")])
    result = make(route, post).investigate(HQ)
    assert [tool for tool, _ in route.calls] == ["verify_address", "geocode_address", "get_flood_risk"]
    assert result.proposed_fix is None
    assert result.next_step == "no_change_needed"
    assert len(post.payloads) == 1


def test_only_the_address_and_precisely_answers_go_to_the_ai():
    post = ScriptedPost([corrections(), explanation()])
    result = make(FakeRoute(), post).investigate(TYPO)
    sent = json.loads(post.payloads[0]["messages"][1]["content"])
    assert set(sent) == {"task", "address_on_file", "precisely_check_as_given"}
    assert result.ai_input == {"address_to_investigate": TYPO, "tools_allowed": list(live_ai.TOOL_ACTIONS)}


def test_correction_that_changes_the_house_is_blocked():
    route = FakeRoute()
    other_house = {**NAPOLEON, "line1": "1143 Napoleon Ave Apt 3"}
    post = ScriptedPost([corrections(other_house), explanation()])
    result = make(route, post).investigate(TYPO)
    assert [tool for tool, _ in route.calls] == ["verify_address"]
    assert result.proposed_fix is None
    assert result.next_step == "analyst_research"


def test_unconfirmed_correction_cannot_be_approved():
    unconfirmed = {"found": True, "score": 40, "street_line": "1141 NAPOLEON AVE"}
    route = FakeRoute(answers={"1141 Napoleon Ave Apt 3, New Orleans, LA 70115": unconfirmed})
    post = ScriptedPost([corrections(NAPOLEON), explanation()])
    result = make(route, post).investigate(TYPO)
    assert result.proposed_fix is None
    assert result.fix_confirmed is False
    assert result.next_step == "analyst_research"


def test_customer_outreach_suggestion_is_kept_when_no_fix():
    post = ScriptedPost([corrections(), explanation("customer_outreach")])
    result = make(FakeRoute(), post).investigate(TYPO)
    assert result.next_step == "customer_outreach"


def test_tool_call_cap_is_enforced():
    investigator = make(FakeRoute(), ScriptedPost([]))
    investigation = live_ai.Investigation(address=HQ, route="r", model="m", tool_calls=MAX_TOOL_CALLS)
    with pytest.raises(LiveAIError):
        investigator._run_tool("verify_address", HQ, 0, investigation)


def test_unknown_tool_is_refused():
    investigator = make(FakeRoute(), ScriptedPost([]))
    with pytest.raises(LiveAIError):
        investigator._run_tool("delete_everything", HQ, 0, live_ai.Investigation(address=HQ, route="r", model="m"))


def test_timeout_raises_for_fallback():
    ticks = iter([0] + [25] * 20)
    with pytest.raises(LiveAIError):
        make(FakeRoute(), ScriptedPost([explanation()]), clock=lambda: next(ticks)).investigate(HQ)


def test_service_failure_raises_for_fallback():
    with pytest.raises(LiveAIError):
        make(FakeRoute(fail_on="geocode_address"), ScriptedPost([explanation()])).investigate(HQ)


def test_ai_error_status_raises_for_fallback():
    with pytest.raises(LiveAIError):
        make(FakeRoute(), ScriptedPost([corrections()], status=500)).investigate(TYPO)


def test_ai_requests_use_low_reasoning_effort():
    post = ScriptedPost([explanation("no_change_needed")])
    make(FakeRoute(), post).investigate(HQ)
    assert post.payloads[0]["reasoning_effort"] == "low"


@pytest.mark.parametrize(
    ("fix", "kept"),
    [
        ({"line1": "1700 District Ave", "city": "Burlington", "state": "MA", "zip5": "01803"}, True),
        ({"line1": "1800 District Ave", "city": "Burlington", "state": "MA", "zip5": "01803"}, False),
        ({"line1": "1700 District Ave", "city": "Burlington", "state": "NH", "zip5": "01803"}, False),
        ({"line1": "1700 New Orleans Ave", "city": "Harvey", "state": "MA", "zip5": "01803"}, False),
    ],
)
def test_guardrails_on_house_number_state_and_place(fix, kept):
    result, note = check_fix_guardrails(fix, "1700 District Ave, Burlington, MA 01804")
    assert (result is not None) is kept
    assert bool(note) is not kept


def test_confirmation_needs_same_house_and_street():
    assert confirms_address(NAPOLEON_CHECK, "1141 Napoleon Ave Apt 3, New Orleans, LA 70115")
    assert not confirms_address(HARVEY_CHECK, TYPO)
    assert not confirms_address({"found": False}, TYPO)


def test_decision_log_records_without_touching_anything_else(tmp_path):
    log = tmp_path / "live_ai" / "decisions.jsonl"
    entry = record_decision(log, case_id="POL-1", decision="approved", investigation={"address": "a", "proposed_fix": NAPOLEON, "fix_confirmed": True})
    assert entry["decision"] == "approved"
    assert json.loads(log.read_text())["case"] == "POL-1"
    assert [p.name for p in tmp_path.rglob("*") if p.is_file()] == ["decisions.jsonl"]
    for decision in ("sent_to_research", "sent_to_customer", "reviewed", "rejected"):
        record_decision(log, case_id="x", decision=decision, investigation={})
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
    assert live_panel.live_settings(tmp_path)["enabled"] is False
    monkeypatch.setenv("AIP_LIVE_AI", "1")
    settings = live_panel.live_settings(tmp_path)
    assert settings["enabled"] and settings["route"] == "mcp"
    assert live_panel._ready(settings)
    assert not live_panel._ready({**settings, "groq_key": ""})
    assert not live_panel._ready({**settings, "route": "direct", "has_direct_keys": False})


def test_result_shows_precisely_data_and_a_clear_first_match_note():
    post = ScriptedPost([corrections(NAPOLEON), explanation()])
    result = make(FakeRoute(), post).investigate(TYPO)
    assert result.precisely_data["flood"] == {"Flood data": "No flood record returned for this address"}
    assert result.proposed_fix["zip5"] == "70115"
    assert "set it aside and the AI tried the likely spelling" in result.guardrail_note
    assert "Harvey" in result.guardrail_note
    assert "analyst research" not in result.guardrail_note


def test_precision_codes_in_plain_words():
    assert live_ai.describe_precision("S8HPNTSCZA") == "Exact address point"
    assert live_ai.describe_precision("S5HPNTSCZA").startswith("On the street")
    assert live_ai.describe_precision("Z1") == "ZIP or city level only"


def test_finds_precisely_id_anywhere():
    assert live_ai.find_precisely_id({"a": {"customFields": {"PB_KEY": "P1"}}}) == "P1"
