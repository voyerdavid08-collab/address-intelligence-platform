import csv
from collections import deque
from decimal import Decimal
import json
from pathlib import Path

import pytest
import requests

from address_intelligence.config import Settings
from address_intelligence.flood import flood_zone_base, risk_tier
from address_intelligence.narrative import (
    EVIDENCE_FIELDS,
    MAX_COMPLETION_TOKENS,
    NarrativeGenerator,
    NarrativeGuardrailError,
    _request_messages,
    build_evidence,
    validate_model_result,
)


def _settings(*, key="groq-test-secret", budget=180000):
    return Settings(
        api_key=None,
        api_secret=None,
        base_url="https://api.precisely.com",
        credit_hard_cap=Decimal("2150"),
        trial_credits=Decimal("2500"),
        log_level="INFO",
        groq_api_key=key,
        groq_daily_token_budget=budget,
    )


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _paths(tmp_path, rows, policies):
    result_path = tmp_path / "results.csv"
    policy_path = tmp_path / "policies.csv"
    _write_csv(result_path, tuple(rows[0]), rows)
    _write_csv(policy_path, tuple(policies[0]), policies)
    return result_path, policy_path


def _eligible_row(policy_id="POL-000001", **updates):
    row = {
        "policy_id": policy_id,
        "standardized_address_line1": "not for model",
        "standardized_city": "not for model",
        "standardized_zip5": "00000",
        "precision_code": "S8HPNTSC-A",
        "eligibility": "eligible",
        "manual_review_reason": "",
        "latitude": "40.0",
        "longitude": "-100.0",
        "preciselyid": "NOT-SENT",
        "flood_zone_code": "X-LOMR",
        "flood_risk_level": "Minimal",
        "bfe": "",
        "bfe_unit": "",
        "distance_to_water": "0.512",
        "distance_to_water_unit": "MILES",
        "water_body_name": "NOT-SENT",
        "built_year": "",
    }
    return {**row, **updates}


def _policy(policy_id="POL-000001"):
    return {
        "policy_id": policy_id,
        "line_of_business": "Homeowners",
        "metro": "Sample Metro",
        "effective_date": "2025-01-01",
        "raw_address_line1": "NOT-SENT",
    }


def _valid_content():
    return json.dumps(
        {
            "narrative": "The flood zone is X and the reported risk is Minimal. "
            "Distance to water is 0.512 MILES.",
            "evidence_fields_used": [
                "flood_zone_base",
                "flood_risk_level",
                "distance_to_water",
            ],
            "missing_evidence": ["built_year"],
        }
    )


class _Response:
    status_code = 200

    def json(self):
        return {
            "choices": [{"message": {"content": _valid_content()}}],
            "usage": {"total_tokens": 23},
        }


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("AE0008", "AE"),
        ("SHX-LOMR", "SHX"),
        ("X-LOMR", "X"),
        ("VE0013", "VE"),
        (None, ""),
    ],
)
def test_flood_zone_base(code, expected):
    assert flood_zone_base(code) == expected


@pytest.mark.parametrize(
    ("base", "expected"),
    [("AE", "High"), ("VE", "High"), ("SHX", "Moderate"), ("X", "Minimal"), ("D", "Unknown")],
)
def test_risk_tier(base, expected):
    assert risk_tier(base) == expected


def test_evidence_allowlist_excludes_private_and_non_allowlisted_fields():
    evidence = build_evidence(_eligible_row(), _policy())

    assert set(evidence).issubset(EVIDENCE_FIELDS)
    assert evidence["match_quality"] == "building-level"
    assert evidence["flood_zone_base"] == "X"
    assert "policy_id" not in evidence
    assert not any(
        forbidden in json.dumps(evidence).casefold()
        for forbidden in ("not-sent", "latitude", "longitude", "preciselyid", "water_body")
    )
    serialized_prompt = json.dumps(_request_messages(evidence)).casefold()
    assert "not-sent" not in serialized_prompt
    assert "00000" not in serialized_prompt


def test_manual_template_and_abstention_do_not_call_model(tmp_path):
    rows = [
        _eligible_row(
            "POL-000001",
            eligibility="manual_review",
            manual_review_reason="location conflict (state mismatch)",
        ),
        _eligible_row("POL-000002", flood_zone_code="", flood_risk_level=""),
    ]
    policies = [_policy(row["policy_id"]) for row in rows]
    results_path, policies_path = _paths(tmp_path, rows, policies)
    calls = []
    generator = NarrativeGenerator(
        _settings(),
        results_path=results_path,
        policies_path=policies_path,
        output_path=tmp_path / "narratives.jsonl",
        cache_root=tmp_path / "cache",
        daily_usage_path=tmp_path / "daily.json",
        post=lambda *args, **kwargs: calls.append(args),
    )

    summary = generator.execute()
    output = [json.loads(line) for line in (tmp_path / "narratives.jsonl").read_text().splitlines()]

    assert calls == []
    assert summary["template"] == 1
    assert summary["abstained"] == 1
    assert "location conflict (state mismatch)" in output[0]["narrative"]
    assert "Enrichment was withheld pending address remediation." in output[0]["narrative"]


def test_guardrails_reject_malformed_json_wrong_keys_and_unsupported_fields():
    evidence = {"flood_zone_base": "X", "flood_risk_level": "Minimal"}
    with pytest.raises(NarrativeGuardrailError, match="valid JSON"):
        validate_model_result("no json", evidence)
    with pytest.raises(NarrativeGuardrailError, match="exactly the required keys"):
        validate_model_result("{}", evidence)
    invalid = json.loads(_valid_content())
    invalid["evidence_fields_used"] = ["latitude"]
    with pytest.raises(NarrativeGuardrailError, match="unsupplied field"):
        validate_model_result(json.dumps(invalid), evidence)


@pytest.mark.parametrize(
    "banned",
    [
        "premium",
        "price",
        "pricing",
        "rate increase",
        "deny",
        "denial",
        "approve",
        "approval",
        "eligible",
        "ineligible",
        "coverage decision",
        "will flood",
        "guaranteed",
    ],
)
def test_guardrails_reject_each_banned_term(banned):
    evidence = {"flood_zone_base": "X", "flood_risk_level": "Minimal"}
    content = json.dumps(
        {
            "narrative": f"The {banned} is unsupported. No further action is indicated.",
            "evidence_fields_used": [],
            "missing_evidence": [],
        }
    )
    with pytest.raises(NarrativeGuardrailError, match="banned term"):
        validate_model_result(content, evidence)


def test_guardrails_reject_ungrounded_numbers_and_overlong_narrative():
    evidence = {"flood_zone_base": "X", "flood_risk_level": "Minimal"}
    unsupported = json.dumps(
        {
            "narrative": "The zone is X and risk is Minimal. The value is 2030.",
            "evidence_fields_used": ["flood_zone_base", "flood_risk_level"],
            "missing_evidence": [],
        }
    )
    with pytest.raises(NarrativeGuardrailError, match="unsupported number"):
        validate_model_result(unsupported, evidence)
    too_long = json.dumps(
        {
            "narrative": ("A" * 601) + ". B.",
            "evidence_fields_used": [],
            "missing_evidence": [],
        }
    )
    with pytest.raises(NarrativeGuardrailError, match="600 characters"):
        validate_model_result(too_long, evidence)


def test_model_response_cache_hit_makes_no_second_call(tmp_path):
    row = _eligible_row()
    results_path, policies_path = _paths(tmp_path, [row], [_policy()])
    calls = []

    def post(url, *, headers, json, timeout):
        calls.append((url, headers, json, timeout))
        return _Response()

    arguments = {
        "settings": _settings(),
        "results_path": results_path,
        "policies_path": policies_path,
        "output_path": tmp_path / "narratives.jsonl",
        "cache_root": tmp_path / "cache",
        "daily_usage_path": tmp_path / "daily.json",
        "post": post,
    }
    first = NarrativeGenerator(**arguments).execute()
    second = NarrativeGenerator(**arguments).execute()
    output = json.loads((tmp_path / "narratives.jsonl").read_text().splitlines()[0])

    assert len(calls) == 1
    assert calls[0][0] == "https://api.groq.com/openai/v1/chat/completions"
    assert calls[0][1]["Authorization"] == "Bearer groq-test-secret"
    payload = calls[0][2]
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["reasoning_effort"] == "low"
    assert payload["temperature"] == 0.2
    assert first["generated"] == second["generated"] == 1
    assert output["status"] == "generated"
    assert output["tokens"] == 23
    assert json.loads((tmp_path / "daily.json").read_text())["tokens"] == 23
    counts = NarrativeGenerator(**arguments).dry_run()
    assert counts["cached"] == 1
    assert counts["model"] == 0


def test_budget_stop_does_not_make_call_and_reports_remaining_tokens(tmp_path):
    results_path, policies_path = _paths(tmp_path, [_eligible_row()], [_policy()])
    calls = []
    generator = NarrativeGenerator(
        _settings(budget=1),
        results_path=results_path,
        policies_path=policies_path,
        output_path=tmp_path / "narratives.jsonl",
        cache_root=tmp_path / "cache",
        daily_usage_path=tmp_path / "daily.json",
        post=lambda *args, **kwargs: calls.append(args),
    )

    summary = generator.execute()

    assert calls == []
    assert summary["stopped_for_budget"] == 1
    assert summary["tokens_remaining_today"] == 1


def test_token_and_request_pacing_respect_minute_limits(tmp_path):
    now = [100.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    generator = NarrativeGenerator(
        _settings(),
        cache_root=tmp_path / "cache",
        clock=lambda: now[0],
        sleeper=sleep,
    )
    generator._request_times = deque([100.0] * 30)
    generator._wait_for_capacity(1)
    assert sleeps[-1] >= 60

    now[0] = 200.0
    generator._token_usage = deque([(200.0, 7900)])
    generator._wait_for_capacity(MAX_COMPLETION_TOKENS)
    assert sleeps[-1] >= 60


def test_secret_is_not_in_settings_repr_or_request_exception(tmp_path):
    results_path, policies_path = _paths(tmp_path, [_eligible_row()], [_policy()])

    def fail_with_secret(*args, **kwargs):
        raise requests.ConnectionError("failed with groq-test-secret")

    generator = NarrativeGenerator(
        _settings(),
        results_path=results_path,
        policies_path=policies_path,
        output_path=tmp_path / "narratives.jsonl",
        cache_root=tmp_path / "cache",
        daily_usage_path=tmp_path / "daily.json",
        post=fail_with_secret,
    )

    assert "groq-test-secret" not in repr(_settings())
    summary = generator.execute()
    output = (tmp_path / "narratives.jsonl").read_text(encoding="utf-8")
    assert summary["failed"] == 1
    assert "groq-test-secret" not in output


def test_dry_run_counts_routes_without_calling_transport(tmp_path):
    rows = [
        _eligible_row("POL-000001", eligibility="manual_review"),
        _eligible_row("POL-000002", flood_zone_code="", flood_risk_level=""),
        _eligible_row("POL-000003"),
    ]
    policies = [_policy(row["policy_id"]) for row in rows]
    results_path, policies_path = _paths(tmp_path, rows, policies)
    calls = []
    generator = NarrativeGenerator(
        _settings(),
        results_path=results_path,
        policies_path=policies_path,
        post=lambda *args, **kwargs: calls.append(args),
    )

    counts = generator.dry_run()

    assert counts["template"] == 1
    assert counts["abstained"] == 1
    assert counts["model"] == 1
    assert counts["estimated_tokens"] > MAX_COMPLETION_TOKENS
    assert calls == []


def test_missing_evidence_only_accepts_allowlisted_names():
    evidence = {"flood_zone_base": "X", "flood_risk_level": "Minimal"}
    content = json.dumps(
        {
            "narrative": "The flood zone is X and risk is Minimal. No year built was supplied.",
            "evidence_fields_used": ["flood_zone_base", "flood_risk_level"],
            "missing_evidence": ["built_year"],
        }
    )
    assert validate_model_result(content, evidence)["missing_evidence"] == ["built_year"]
    result = json.loads(content)
    result["missing_evidence"] = ["policy_id"]
    with pytest.raises(NarrativeGuardrailError, match="unknown field"):
        validate_model_result(json.dumps(result), evidence)
    result["missing_evidence"] = ["flood_zone_base"]
    with pytest.raises(NarrativeGuardrailError, match="supplied field"):
        validate_model_result(json.dumps(result), evidence)
