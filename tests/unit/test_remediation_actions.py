import csv
from decimal import Decimal
import json

import pytest

from address_intelligence.config import Settings
from address_intelligence.credits import CreditLedger
from address_intelligence.narrative import NarrativeAPIError
from address_intelligence.providers.precisely import ProviderResponse
from address_intelligence.remediation_actions import (
    ExceptionActionWorkflow,
    _request_messages,
    _suggestion_input,
    triage_action,
    validate_suggestion,
)


def _settings():
    return Settings(
        api_key=None,
        api_secret=None,
        base_url="https://api.precisely.com",
        credit_hard_cap=Decimal("2150"),
        trial_credits=Decimal("2500"),
        log_level="INFO",
        groq_api_key="test-key",
        groq_model="openai/gpt-oss-120b",
        remediation_model="openai/gpt-oss-20b",
        groq_daily_token_budget=180000,
    )


def _write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _fixture(tmp_path, *, include_output=False):
    policy_id = "POL-000001"
    policy = {
        "policy_id": policy_id,
        "raw_address_line1": "12 EXAMPLE ST",
        "raw_address_line2": "",
        "raw_city": "OLD CITY",
        "raw_state": "CA",
        "raw_zip5": "12345",
    }
    result = {
        "policy_id": policy_id,
        "eligibility": "manual_review",
        "manual_review_reason": "street-level only",
        "precision_code": "S5HPN-S--A",
    }
    diagnosis = {
        "policy_id": policy_id,
        "prediction_set": ["city_issue"],
        "frequencies": {"city_issue": 4},
        "status": "ok",
    }
    truth = {
        "policy_id": policy_id,
        "defects": "city_typo;missing_unit",
        "clean_address_line1": "12 EXAMPLE ST",
        "clean_address_line2": "",
        "clean_city": "NEW CITY",
        "clean_state": "CA",
        "clean_zip5": "12345",
        "evaluation_secret": "groundtruthsecret",
    }
    paths = {}
    for key, rows in (
        ("policies", [policy]),
        ("results", [result]),
        ("truth", [truth]),
    ):
        paths[key] = tmp_path / f"{key}.csv"
        _write_csv(paths[key], rows)
    paths["diagnosis"] = tmp_path / "diagnosis.jsonl"
    paths["diagnosis"].write_text(json.dumps(diagnosis) + "\n", encoding="utf-8")
    paths["output"] = tmp_path / "actions.jsonl"
    if include_output:
        paths["output"].write_text(
            json.dumps(
                {
                    "policy_id": policy_id,
                    "action": "analyst_review",
                    "cause_shortlist": ["city_issue"],
                    "suggestion": {
                        "line1": "12 EXAMPLE ST",
                        "city": "NEW CITY",
                        "zip5": "12345",
                        "changed_fields": ["city"],
                        "rationale": "Correct the city spelling.",
                    },
                    "suggestion_status": "suggested, unverified",
                    "verification_status": "not_run",
                    "evaluation": {},
                }
            )
            + "\n",
            encoding="utf-8",
        )
    kwargs = {
        "results_path": paths["results"],
        "policies_path": paths["policies"],
        "diagnosis_path": paths["diagnosis"],
        "ground_truth_path": paths["truth"],
        "output_path": paths["output"],
        "suggestion_cache_root": tmp_path / "suggestion-cache",
        "geocode_cache_root": tmp_path / "remediation-geocode",
        "daily_usage_path": tmp_path / "daily-usage.json",
        "report_md_path": tmp_path / "reports" / "actions.md",
        "report_csv_path": tmp_path / "reports" / "actions.csv",
    }
    ledger = CreditLedger(Decimal("2150"), path=tmp_path / "ledger.json")
    return policy, paths, ledger, kwargs


def test_triage_requires_exact_house_number_only_set():
    assert triage_action(["house_number_issue"]) == "customer_outreach"
    assert triage_action(["house_number_issue", "city_issue"]) == "analyst_review"
    assert triage_action(["city_issue"]) == "analyst_review"


def test_suggestion_model_allowlist_uses_only_submitted_and_derived_signals(tmp_path):
    policy, paths, ledger, kwargs = _fixture(tmp_path)
    workflow = ExceptionActionWorkflow(_settings(), ledger, **kwargs)
    record = workflow._records()[0]
    serialized = json.dumps(record["model_input"])
    assert "groundtruthsecret" not in serialized
    assert "standardized_address" not in serialized
    assert "coordinates" not in serialized
    assert "preciselyid" not in serialized
    assert record["model_input"]["submitted_address"]["line1"] == policy["raw_address_line1"]
    assert record["model_input"]["match_signals"]["precision_category"] == "street address"
    assert record["model_input"]["match_signals"]["component_matches"]["house_number"] is True
    messages = json.dumps(_request_messages(record["model_input"]))
    assert "groundtruthsecret" not in messages


@pytest.mark.parametrize(
    ("policy", "suggestion", "shortlist", "reason"),
    [
        (
            {"raw_address_line1": "MAIN ST", "raw_city": "OLD", "raw_zip5": "12345"},
            {
                "line1": "12 MAIN ST",
                "city": "OLD",
                "zip5": "12345",
                "changed_fields": ["street"],
                "rationale": "Add the missing number.",
            },
            ["street_name_issue"],
            "house number",
        ),
        (
            {"raw_address_line1": "12 MAIN ST", "raw_city": "OLD", "raw_zip5": "12345"},
            {
                "line1": "13 MAIN ST",
                "city": "OLD",
                "zip5": "12345",
                "changed_fields": ["street"],
                "rationale": "Change the number.",
            },
            ["street_name_issue"],
            "house number",
        ),
        (
            {"raw_address_line1": "12 MAIN ST", "raw_city": "OLD", "raw_zip5": "12345"},
            {
                "line1": "12 MAIN ST",
                "city": "NEW",
                "zip5": "12345",
                "changed_fields": ["city"],
                "rationale": "Correct the city.",
            },
            ["street_name_issue"],
            "cause shortlist",
        ),
        (
            {"raw_address_line1": "12 MAIN ST", "raw_city": "OLD", "raw_zip5": "12345"},
            {
                "line1": "12 MAIN ST",
                "city": "OLD",
                "zip5": "1234",
                "changed_fields": ["zip5"],
                "rationale": "Correct the ZIP.",
            },
            ["zip_issue"],
            "five digits",
        ),
    ],
)
def test_suggestion_guardrails_reject_invalid_values(
    policy, suggestion, shortlist, reason
):
    with pytest.raises(ValueError, match=reason):
        validate_suggestion(json.dumps(suggestion), policy, shortlist)


def test_suggestion_guardrail_rejects_state_output():
    content = json.dumps(
        {
            "line1": "12 MAIN ST",
            "city": "OLD",
            "zip5": "12345",
            "changed_fields": [],
            "rationale": "",
            "state": "NY",
        }
    )
    with pytest.raises(ValueError, match="schema"):
        validate_suggestion(
            content,
            {"raw_address_line1": "12 MAIN ST", "raw_city": "OLD", "raw_zip5": "12345"},
            ["city_issue"],
        )


def test_changed_field_must_match_declared_changes_and_shortlist():
    content = json.dumps(
        {
            "line1": "12 MAIN ST",
            "city": "NEW",
            "zip5": "12345",
            "changed_fields": [],
            "rationale": "Correct the city.",
        }
    )
    with pytest.raises(ValueError, match="changed_fields"):
        validate_suggestion(
            content,
            {"raw_address_line1": "12 MAIN ST", "raw_city": "OLD", "raw_zip5": "12345"},
            ["city_issue"],
        )


@pytest.mark.parametrize(
    ("suggestion", "reason"),
    [
        (
            {
                "line1": "12 MAIN ST",
                "city": "NEW",
                "zip5": "12345",
                "changed_fields": ["address"],
                "rationale": "Correct the city.",
            },
            "invalid changed_fields",
        ),
        (
            {
                "line1": "12 MAIN ST",
                "city": "OLD",
                "zip5": "12345",
                "changed_fields": [],
                "rationale": "x" * 201,
            },
            "rationale exceeds",
        ),
    ],
)
def test_suggestion_rejects_unsupported_field_names_and_long_rationale(
    suggestion, reason
):
    with pytest.raises(ValueError, match=reason):
        validate_suggestion(
            json.dumps(suggestion),
            {"raw_address_line1": "12 MAIN ST", "raw_city": "OLD", "raw_zip5": "12345"},
            ["city_issue"],
        )


def test_schema_and_request_keep_ground_truth_private(tmp_path):
    policy, paths, ledger, kwargs = _fixture(tmp_path)
    captured = {}

    class Response:
        status_code = 200
        headers = {}

        @staticmethod
        def json():
            return {
                "model": "openai/gpt-oss-20b",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "line1": policy["raw_address_line1"],
                                    "city": "NEW CITY",
                                    "zip5": "12345",
                                    "changed_fields": ["city"],
                                    "rationale": "Correct the city spelling.",
                                }
                            )
                        }
                    }
                ],
                "usage": {"total_tokens": 40},
            }

    def fake_post(url, *, headers, json, timeout):
        captured.update(url=url, headers=headers, payload=json)
        return Response()

    workflow = ExceptionActionWorkflow(
        _settings(), ledger, post=fake_post, **kwargs
    )
    record = workflow._records()[0]
    response, source = workflow._suggestion_response(record, tokens_used=0)
    schema = captured["payload"]["response_format"]["json_schema"]["schema"]
    assert source == "api"
    assert response["model_reported"] == "openai/gpt-oss-20b"
    assert captured["payload"]["model"] == "openai/gpt-oss-20b"
    assert captured["payload"]["temperature"] == 0
    assert captured["payload"]["reasoning_effort"] == "low"
    assert "minItems" not in json.dumps(schema)
    assert "maxItems" not in json.dumps(schema)
    assert "uniqueItems" not in json.dumps(schema)
    assert "groundtruthsecret" not in json.dumps(captured["payload"])
    assert captured["headers"]["Authorization"] == "Bearer test-key"


def test_suggest_writes_labelled_unverified_output(tmp_path, monkeypatch):
    policy, paths, ledger, kwargs = _fixture(tmp_path)
    workflow = ExceptionActionWorkflow(_settings(), ledger, **kwargs)
    content = json.dumps(
        {
            "line1": policy["raw_address_line1"],
            "city": "NEW CITY",
            "zip5": "12345",
            "changed_fields": ["city"],
            "rationale": "Correct the city spelling.",
        }
    )
    monkeypatch.setattr(
        workflow,
        "_suggestion_response",
        lambda record, tokens_used: (
            {
                "content": content,
                "model_reported": "openai/gpt-oss-20b",
                "tokens": 0,
            },
            "cached",
        ),
    )
    summary = workflow.suggest()
    output = json.loads(paths["output"].read_text(encoding="utf-8"))
    assert output["suggestion_status"] == "suggested, unverified"
    assert output["suggestion_label"] == "suggested, unverified"
    assert output["model_reported"] == "openai/gpt-oss-20b"
    assert summary["suggestions_made"] == 1
    assert summary["suggestions_valid"] == 1


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            ProviderResponse(
                200,
                {
                    "candidates": [
                        {
                            "precisionCode": "S5HPN-S--A",
                            "address": {"areaName1": "CA"},
                        }
                    ]
                },
                None,
                Decimal("0"),
            ),
            True,
        ),
        (
            ProviderResponse(
                200,
                {
                    "candidates": [
                        {
                            "precisionCode": "S4HPN-S--A",
                            "address": {"areaName1": "CA"},
                        }
                    ]
                },
                None,
                Decimal("0"),
            ),
            False,
        ),
        (
            ProviderResponse(
                200,
                {
                    "candidates": [
                        {
                            "precisionCode": "S8HPN-S--A",
                            "address": {"areaName1": "NV"},
                        }
                    ]
                },
                None,
                Decimal("0"),
            ),
            False,
        ),
    ],
)
def test_verification_requires_eligible_precision_and_matching_state(
    response, expected
):
    assert ExceptionActionWorkflow._verified(response, "CA") is expected


def test_ledger_check_occurs_before_precisely_call(tmp_path):
    policy, paths, ledger, kwargs = _fixture(tmp_path, include_output=True)
    check_count = 0
    original_check = ledger.check

    def checked(operation, quantity):
        nonlocal check_count
        check_count += 1
        return original_check(operation, quantity)

    ledger.check = checked

    class Client:
        def geocode(self, address, tier):
            assert check_count == 1
            assert tier == "premium"
            return ProviderResponse(
                200,
                {
                    "candidates": [
                        {
                            "precisionCode": "S5HPN-S--A",
                            "address": {
                                "areaName1": "CA",
                                "mainAddressLine": "12 EXAMPLE ST",
                                "areaName3": "NEW CITY",
                                "postCode1": "12345",
                            },
                        }
                    ]
                },
                None,
                Decimal("1"),
            )

    workflow = ExceptionActionWorkflow(
        _settings(), ledger, client=Client(), **kwargs
    )
    summary = workflow.verify()
    assert check_count == 1
    assert summary["verified"] == 1
    assert summary["verified_correct"] == 1


def test_verify_requires_completed_suggest_stage(tmp_path):
    _, _, ledger, kwargs = _fixture(tmp_path)
    workflow = ExceptionActionWorkflow(_settings(), ledger, **kwargs)
    with pytest.raises(ValueError, match="suggest"):
        workflow.verify()


def test_analyst_record_without_plausible_edit_skips_suggestion(tmp_path):
    _, paths, ledger, kwargs = _fixture(tmp_path)
    paths["diagnosis"].write_text(
        json.dumps({"policy_id": "POL-000001", "prediction_set": []}) + "\n",
        encoding="utf-8",
    )
    workflow = ExceptionActionWorkflow(
        _settings(),
        ledger,
        post=lambda *args, **kwargs: pytest.fail("no suggestion should be requested"),
        **kwargs,
    )
    plan = workflow.dry_run("suggest")
    assert plan["analyst_review"] == 1
    assert plan["groq_calls"] == 0


def test_dry_runs_are_network_free_and_report_plan(tmp_path):
    _, _, ledger, kwargs = _fixture(tmp_path)
    workflow = ExceptionActionWorkflow(
        _settings(),
        ledger,
        post=lambda *args, **kwargs: pytest.fail("dry run must not call Groq"),
        **kwargs,
    )
    suggest = workflow.dry_run("suggest")
    verify = workflow.dry_run("verify")
    assert suggest["customer_outreach"] == 0
    assert suggest["analyst_review"] == 1
    assert suggest["planned_groq_calls"] == 1
    assert verify["planned_precisely_calls"] == 1
    assert verify["suggest_stage_required"] is True
    assert not (tmp_path / "actions.jsonl").exists()
    report = (tmp_path / "reports" / "actions.md").read_text(encoding="utf-8")
    assert "dry-run planning only" in report
    assert "12 EXAMPLE ST" not in report
