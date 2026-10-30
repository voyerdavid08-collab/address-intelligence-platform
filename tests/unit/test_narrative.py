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
    _append_missing_disclosure,
    _request_messages,
    build_evidence,
    compute_not_available,
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
            "Distance to water is 0.512 miles.",
            "evidence_fields_used": [
                "flood_zone_base",
                "flood_risk_level",
                "distance_to_water",
            ],
        }
    )


class _Response:
    status_code = 200

    def json(self):
        return {
            "model": "openai/gpt-oss-120b",
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
    assert evidence["match_quality"] == "location verified to an exact address point"
    assert evidence["flood_zone_base"] == "X"
    assert "policy_id" not in evidence
    assert not any(
        forbidden in json.dumps(evidence).casefold()
        for forbidden in ("not-sent", "latitude", "longitude", "preciselyid", "water_body")
    )
    serialized_prompt = json.dumps(_request_messages(evidence)).casefold()
    assert "not-sent" not in serialized_prompt
    assert "00000" not in serialized_prompt
    assert (
        "location verified to an exact address point" in serialized_prompt
    )
    assert "not_available" not in serialized_prompt
    assert "do not mention information that is not supplied" in serialized_prompt
    assert "write 2" in serialized_prompt
    assert "plain-english sentences for an underwriter" in serialized_prompt
    assert "you may combine related facts" in serialized_prompt
    assert "never imply that one field causes or determines another" in serialized_prompt


def test_missing_evidence_is_computed_with_bfe_risk_tier_rule():
    minimal = build_evidence(
        _eligible_row(flood_zone_code="X", distance_to_water="0.5"),
        _policy(),
    )
    high = build_evidence(
        _eligible_row(flood_zone_code="AE0008", distance_to_water="0.5"),
        _policy(),
    )
    high_with_bfe = build_evidence(
        _eligible_row(
            flood_zone_code="AE0008",
            bfe="13",
            bfe_unit="feet",
            distance_to_water="0.5",
        ),
        _policy(),
    )

    assert compute_not_available(minimal) == ["built_year"]
    assert compute_not_available(high) == [
        "built_year",
        "base_flood_elevation",
    ]
    assert compute_not_available(high_with_bfe) == ["built_year"]


def test_cleaning_and_plain_language_match_quality():
    result = _eligible_row(
        precision_code="S7",
        flood_zone_code="AE0013",
        bfe="0013",
        bfe_unit="FEET",
        distance_to_water="000.512",
        distance_to_water_unit="MILES",
        built_year="0013",
    )

    evidence = build_evidence(result, _policy())

    assert evidence["match_quality"] == (
        "location estimated along the street "
        "(house number not confirmed as a known address point)"
    )
    assert evidence["base_flood_elevation"] == "13 feet"
    assert evidence["distance_to_water"] == "0.512 miles"
    assert evidence["built_year"] == "13"


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
    assert output[0]["narrative"] == (
        "The location could not be verified to building level (reason: the "
        "standardized state differed from the input state). Flood enrichment "
        "was withheld pending address remediation."
    )


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
        }
    )
    with pytest.raises(NarrativeGuardrailError, match="unsupported number"):
        validate_model_result(unsupported, evidence)
    too_long = json.dumps(
        {
            "narrative": ("A" * 601) + ". B.",
            "evidence_fields_used": [],
        }
    )
    with pytest.raises(NarrativeGuardrailError, match="600 characters"):
        validate_model_result(too_long, evidence)


def test_sentence_guardrail_accepts_four_and_rejects_one_or_five():
    evidence = {"flood_zone_base": "X"}

    def content(sentence_count):
        return json.dumps(
            {
                "narrative": " ".join(
                    "The flood zone is X." for _ in range(sentence_count)
                ),
                "evidence_fields_used": ["flood_zone_base"],
            }
        )

    validate_model_result(content(4), evidence)
    for count in (1, 5):
        with pytest.raises(NarrativeGuardrailError, match="2 to 4 sentences"):
            validate_model_result(content(count), evidence)


def test_base_flood_elevation_number_requires_same_sentence_label():
    evidence = {
        "flood_zone_base": "AE",
        "flood_risk_level": "High",
        "base_flood_elevation": "5354 feet",
    }
    bad = json.dumps(
        {
            "narrative": "The property sits at an elevation of 5,354 feet. "
            "The flood zone is AE and the reported risk is High.",
            "evidence_fields_used": [
                "flood_zone_base",
                "flood_risk_level",
                "base_flood_elevation",
            ],
        }
    )
    with pytest.raises(
        NarrativeGuardrailError,
        match="elevation value not labelled as base flood elevation",
    ):
        validate_model_result(bad, evidence)


def test_labelled_base_flood_elevation_value_passes():
    evidence = {
        "flood_zone_base": "AE",
        "flood_risk_level": "High",
        "base_flood_elevation": "13 feet",
    }
    content = json.dumps(
        {
            "narrative": "The base flood elevation of 13 feet is reported. "
            "The flood zone is AE and the reported risk is High.",
            "evidence_fields_used": [
                "flood_zone_base",
                "flood_risk_level",
                "base_flood_elevation",
            ],
        }
    )

    validate_model_result(content, evidence)


def test_property_elevation_attribution_is_rejected_when_only_bfe_is_available():
    evidence = {
        "flood_zone_base": "AE",
        "flood_risk_level": "High",
        "base_flood_elevation": "5354 feet",
    }
    content = json.dumps(
        {
            "narrative": "The property elevation is 5,354 feet. "
            "The flood zone is AE and risk is High.",
            "evidence_fields_used": [
                "flood_zone_base",
                "flood_risk_level",
                "base_flood_elevation",
            ],
        }
    )

    with pytest.raises(
        NarrativeGuardrailError,
        match="elevation value not labelled as base flood elevation",
    ):
        validate_model_result(content, evidence)


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
    usage = json.loads((tmp_path / "daily.json").read_text())
    assert usage["models"]["openai/gpt-oss-120b"] == 23
    assert output["model"] == "openai/gpt-oss-120b"
    assert output["model_reported"] == "openai/gpt-oss-120b"
    assert output["missing_evidence"] == ["built_year"]
    assert output["narrative_model"] == (
        "The flood zone is X and the reported risk is Minimal. "
        "Distance to water is 0.512 miles."
    )
    assert output["narrative"] == (
        output["narrative_model"] + " Not available: year built."
    )
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


def test_failed_call_records_only_safe_status_and_error_identifiers(tmp_path):
    results_path, policies_path = _paths(tmp_path, [_eligible_row()], [_policy()])

    class ErrorResponse:
        status_code = 400
        headers = {}

        @staticmethod
        def json():
            return {
                "error": {
                    "type": "invalid_request_error",
                    "code": "json_validate_failed",
                    "message": "sensitive request and groq-test-secret",
                }
            }

    generator = NarrativeGenerator(
        _settings(),
        results_path=results_path,
        policies_path=policies_path,
        output_path=tmp_path / "narratives.jsonl",
        cache_root=tmp_path / "cache",
        daily_usage_path=tmp_path / "daily.json",
        post=lambda *args, **kwargs: ErrorResponse(),
    )

    summary = generator.execute()
    output = json.loads((tmp_path / "narratives.jsonl").read_text().splitlines()[0])

    assert summary["failed"] == 1
    assert output["status"] == "failed"
    assert output["guardrail_reason"] == (
        "HTTP 400 invalid_request_error/json_validate_failed"
    )
    assert "sensitive" not in output["guardrail_reason"]
    assert "groq-test-secret" not in output["guardrail_reason"]


def test_429_retries_once_and_honors_retry_after(tmp_path):
    results_path, policies_path = _paths(tmp_path, [_eligible_row()], [_policy()])
    calls = []
    delays = []

    class RateLimitResponse:
        status_code = 429
        headers = {"Retry-After": "2"}

        @staticmethod
        def json():
            return {"error": {"type": "rate_limit", "code": "rate_limit_exceeded"}}

    responses = [RateLimitResponse(), _Response()]

    def post(*args, **kwargs):
        calls.append(args)
        return responses.pop(0)

    generator = NarrativeGenerator(
        _settings(),
        results_path=results_path,
        policies_path=policies_path,
        output_path=tmp_path / "narratives.jsonl",
        cache_root=tmp_path / "cache",
        daily_usage_path=tmp_path / "daily.json",
        post=post,
        sleeper=delays.append,
    )

    summary = generator.execute()

    assert summary["generated"] == 1
    assert len(calls) == 2
    assert delays == [2.0]


def test_400_is_not_retried(tmp_path):
    results_path, policies_path = _paths(tmp_path, [_eligible_row()], [_policy()])
    calls = []

    class BadRequestResponse:
        status_code = 400
        headers = {"Retry-After": "0"}

        @staticmethod
        def json():
            return {"error": {"code": "json_validate_failed"}}

    def post(*args, **kwargs):
        calls.append(args)
        return BadRequestResponse()

    generator = NarrativeGenerator(
        _settings(),
        results_path=results_path,
        policies_path=policies_path,
        output_path=tmp_path / "narratives.jsonl",
        cache_root=tmp_path / "cache",
        daily_usage_path=tmp_path / "daily.json",
        post=post,
        sleeper=lambda delay: pytest.fail("400 response must not be retried"),
    )

    summary = generator.execute()

    assert summary["failed"] == 1
    assert len(calls) == 1


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


def test_evidence_fields_used_rejects_unavailable_names():
    evidence = {"flood_zone_base": "AE", "flood_risk_level": "High"}
    content = json.dumps({
        "narrative": "The flood zone is AE and the reported risk is High. "
        "The reported location is in the metro area.",
        "evidence_fields_used": [
            "flood_zone_base",
            "flood_risk_level",
        ],
    })
    assert "missing_evidence" not in validate_model_result(
        content, evidence, ["built_year", "base_flood_elevation"]
    )

    invented = json.loads(content)
    invented["evidence_fields_used"].append("built_year")
    with pytest.raises(
        NarrativeGuardrailError, match="unsupplied field"
    ):
        validate_model_result(
            json.dumps(invented),
            evidence,
            ["built_year", "base_flood_elevation"],
        )


@pytest.mark.parametrize(
    ("field", "phrase"),
    [
        ("built_year", "year built"),
        ("built_year", "built year"),
        ("built_year", "constructed"),
        ("built_year", "construction year"),
        ("base_flood_elevation", "base flood elevation"),
        ("distance_to_water", "distance to water"),
    ],
)
def test_model_narrative_cannot_mention_unavailable_fields(field, phrase):
    evidence = {"flood_zone_base": "X", "flood_risk_level": "Minimal"}
    content = json.dumps(
        {
            "narrative": f"The flood zone is X. The reported {phrase} is absent.",
            "evidence_fields_used": ["flood_zone_base", "flood_risk_level"],
        }
    )
    with pytest.raises(NarrativeGuardrailError, match="mentions unavailable field"):
        validate_model_result(content, evidence, [field])


def test_model_text_is_normalized_to_ascii_before_guardrails():
    evidence = {"flood_zone_base": "X", "flood_risk_level": "Minimal"}
    content = json.dumps(
        {
            "narrative": "The flood zone is X\u2011LOMR. \u201cRisk\u201d is Minimal. "
            "No additional facts\u2014were provided.",
            "evidence_fields_used": ["flood_zone_base", "flood_risk_level"],
        }
    )
    result = validate_model_result(content, evidence)
    assert "\u2011" not in result["narrative"]
    assert "\u201c" not in result["narrative"]
    assert "\u201d" not in result["narrative"]
    assert "\u2014" not in result["narrative"]
    assert result["narrative"] == (
        'The flood zone is X-LOMR. "Risk" is Minimal. No additional facts-were provided.'
    )


def test_missing_disclosure_uses_readable_names():
    narrative_model = "The flood zone is AE. The reported risk is High."

    assert _append_missing_disclosure(
        narrative_model, ["built_year", "base_flood_elevation"]
    ) == (
        "The flood zone is AE. The reported risk is High. "
        "Not available: year built and base flood elevation."
    )
    assert _append_missing_disclosure(narrative_model, []) == narrative_model


def test_prompt_instructs_separate_facts_without_causal_implications():
    prompt = "\n".join(
        message["content"]
        for message in _request_messages({"flood_zone_base": "AE"})
    )
    assert "Write 2\u20133 plain-English sentences for an underwriter." in prompt
    assert "You may combine related facts in one sentence" in prompt
    assert "never imply that one field causes or determines another." in prompt
    assert "not_available" not in prompt
    assert "do not mention information that is not supplied" in prompt


def test_model_response_missing_evidence_schema_does_not_include_code_field(tmp_path):
    results_path, policies_path = _paths(tmp_path, [_eligible_row()], [_policy()])
    calls = []

    def post(url, *, headers, json, timeout):
        calls.append(json)
        return _Response()

    NarrativeGenerator(
        _settings(),
        results_path=results_path,
        policies_path=policies_path,
        output_path=tmp_path / "narratives.jsonl",
        cache_root=tmp_path / "cache",
        daily_usage_path=tmp_path / "daily.json",
        post=post,
    ).execute()

    schema = calls[0]["response_format"]["json_schema"]["schema"]
    assert set(schema["properties"]) == {"narrative", "evidence_fields_used"}
    assert "missing_evidence" not in schema["required"]


def test_different_reported_model_is_stored_and_warned(tmp_path):
    results_path, policies_path = _paths(tmp_path, [_eligible_row()], [_policy()])

    class DifferentModelResponse(_Response):
        def json(self):
            body = super().json()
            body["model"] = "openai/gpt-oss-20b"
            return body

    generator = NarrativeGenerator(
        _settings(),
        results_path=results_path,
        policies_path=policies_path,
        output_path=tmp_path / "narratives.jsonl",
        cache_root=tmp_path / "cache",
        daily_usage_path=tmp_path / "daily.json",
        post=lambda *args, **kwargs: DifferentModelResponse(),
    )
    with pytest.warns(RuntimeWarning, match="differs"):
        generator.execute()
    output = json.loads((tmp_path / "narratives.jsonl").read_text().splitlines()[0])
    assert output["model"] == "openai/gpt-oss-120b"
    assert output["model_reported"] == "openai/gpt-oss-20b"


def test_revalidate_cached_rejects_labeling_issue_without_api_call(tmp_path):
    row = _eligible_row(
        policy_id="POL-000183",
        precision_code="S8HPNTSCZA",
        flood_zone_code="AE5354-LOMR",
        flood_risk_level="High",
        bfe="5354",
        bfe_unit="feet",
        distance_to_water="0.048",
        distance_to_water_unit="MILES",
    )
    policy = _policy("POL-000183")
    results_path, policies_path = _paths(tmp_path, [row], [policy])
    output_path = tmp_path / "narratives.jsonl"
    output_path.write_text(
        json.dumps(
            {
                "policy_id": "POL-000183",
                "status": "generated",
                "narrative_model": (
                    "The property is located 0.048 miles from water and sits at "
                    "an elevation of 5,354 feet. It is classified in flood zone "
                    "AE with a high flood risk level."
                ),
                "narrative": "old output",
                "evidence_fields_used": [
                    "distance_to_water",
                    "base_flood_elevation",
                    "flood_zone_base",
                    "flood_risk_level",
                    "match_quality",
                    "metro",
                    "line_of_business",
                ],
                "model": "openai/gpt-oss-120b",
                "model_reported": "openai/gpt-oss-120b",
                "tokens": 507,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    api_calls = []
    generator = NarrativeGenerator(
        _settings(),
        results_path=results_path,
        policies_path=policies_path,
        output_path=output_path,
        cache_root=tmp_path / "cache",
        daily_usage_path=tmp_path / "daily.json",
        post=lambda *args, **kwargs: api_calls.append(args),
    )

    summary = generator.revalidate_cached()
    output = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])

    assert api_calls == []
    assert summary["api_calls"] == 0
    assert summary["rejected_by_guardrail"] == 1
    assert output["status"] == "rejected_by_guardrail"
    assert output["guardrail_reason"] == (
        "elevation value not labelled as base flood elevation"
    )
    assert "base_flood_elevation" in output["evidence_fields_used"]
