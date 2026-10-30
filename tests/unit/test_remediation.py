import csv
import json
from decimal import Decimal

import pytest

from address_intelligence.config import ConfigError, Settings
from address_intelligence.remediation import (
    MAX_COMPLETION_TOKENS,
    RemediationDiagnoser,
    build_model_input,
    build_prediction_set,
    calculate_test_metrics,
    conformal_quantile,
    map_ground_truth_causes,
    parse_precision_match_flags,
    precision_category,
    stratified_split,
)


def _settings(*, samples=5, alpha=0.10, budget=180000):
    return Settings(
        api_key=None,
        api_secret=None,
        base_url="https://api.precisely.com",
        credit_hard_cap=Decimal("2150"),
        trial_credits=Decimal("2500"),
        log_level="INFO",
        groq_api_key="groq-test-secret",
        groq_model="openai/gpt-oss-120b",
        groq_daily_token_budget=budget,
        remediation_samples=samples,
        conformal_alpha=alpha,
    )


def _write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _fixture_inputs(tmp_path, labels=None):
    labels = labels or ["street_typo", "missing_zip"]
    policy_rows = []
    result_rows = []
    truth_rows = []
    cache_root = tmp_path / "geocode"
    cache_root.mkdir()
    for index, defect in enumerate(labels, start=1):
        policy_id = f"POL-{index:06d}"
        policy_rows.append(
            {
                "policy_id": policy_id,
                "raw_address_line1": f"{index} Example St",
                "raw_address_line2": "Apt 4",
                "raw_city": "Sample City",
                "raw_state": "CA",
                "raw_zip5": "12345",
            }
        )
        result_rows.append(
            {
                "policy_id": policy_id,
                "eligibility": "manual_review",
                "manual_review_reason": "street-level only",
                "precision_code": "S8HPNTSCZA",
            }
        )
        truth_rows.append(
            {"policy_id": policy_id, "defects": defect}
        )
        cache = {
            "data": {
                "candidates": [
                    {"precisionCode": "S8HPN-S--A", "formattedStreetAddress": "DO NOT SEND"}
                ]
            }
        }
        (cache_root / f"{policy_id}.json").write_text(json.dumps(cache))
    paths = {}
    for name, rows in (
        ("policies", policy_rows),
        ("results", result_rows),
        ("truth", truth_rows),
    ):
        paths[name] = tmp_path / f"{name}.csv"
        _write_csv(paths[name], rows)
    return paths, cache_root


def _diagnoser(tmp_path, *, labels=None, **kwargs):
    paths, cache_root = _fixture_inputs(tmp_path, labels)
    return RemediationDiagnoser(
        _settings(**kwargs),
        policies_path=paths["policies"],
        results_path=paths["results"],
        ground_truth_path=paths["truth"],
        geocode_cache_root=cache_root,
        output_path=tmp_path / "diagnosis.jsonl",
        cache_root=tmp_path / "remediation-cache",
        daily_usage_path=tmp_path / "daily.json",
        report_md_path=tmp_path / "reports" / "remediation_calibration.md",
        report_csv_path=tmp_path / "reports" / "remediation_calibration.csv",
        post=lambda *args, **kwargs: pytest.fail("dry run must not call network"),
    )


def test_taxonomy_maps_multiple_defects_and_excludes_missing_unit_only():
    assert map_ground_truth_causes(
        "street_typo;wrong_zip,missing_zip;missing_house_number"
    ) == {"street_name_issue", "zip_issue", "missing_house_number"}
    assert map_ground_truth_causes("missing_unit") == set()
    assert map_ground_truth_causes("city_typo;missing_unit") == {"city_issue"}


def test_precision_categories_and_suffix_component_matches():
    assert precision_category("S8HPNTSCZA") == "address point"
    assert precision_category("S5HPNTSCZA") == "street address"
    assert precision_category("S4HPNTSCZA") == "street centroid"
    assert precision_category("Z1") == "postal or city level"
    assert precision_category("G3") == "postal or city level"
    assert precision_category("S0") == "no match"
    assert parse_precision_match_flags("S8HPN-S--A") == {
        "house_number": True,
        "street_name": True,
        "street_type": False,
        "city": False,
        "zip": False,
    }
    assert all(parse_precision_match_flags("S8HPNTSCZA").values())
    assert parse_precision_match_flags("Z1") == {
        "house_number": "unknown",
        "street_name": "unknown",
        "street_type": "unknown",
        "city": "unknown",
        "zip": "unknown",
    }
    assert parse_precision_match_flags("S4-PN-S--A") == {
        "house_number": False,
        "street_name": True,
        "street_type": False,
        "city": False,
        "zip": False,
    }
    model_input = build_model_input(
        {"raw_address_line1": "12 Example St"}, "Z1"
    )
    assert set(model_input["match_signals"]["component_matches"].values()) == {
        "unknown"
    }


def test_model_input_allowlist_uses_only_submitted_and_derived_signals():
    model_input = build_model_input(
        {
            "raw_address_line1": "12 Example St",
            "raw_address_line2": "Apt 4",
            "raw_city": "Sample City",
            "raw_state": "CA",
            "raw_zip5": "12345",
            "policy_id": "NOT-SENT",
            "clean_address_line1": "NOT-SENT",
        },
        "S8HPN-S--A",
    )
    serialized = json.dumps(model_input)
    assert set(model_input) == {"submitted_address", "match_signals"}
    assert "12 Example St" in serialized
    assert "NOT-SENT" not in serialized
    assert all(
        forbidden not in serialized.casefold()
        for forbidden in ("formattedstreetaddress", "coordinates", "preciselyid", "defects")
    )


def test_missing_unit_only_records_are_excluded_from_labelled_set(tmp_path):
    diagnoser = _diagnoser(
        tmp_path, labels=["missing_unit", "street_typo;missing_unit"]
    )
    records, excluded = diagnoser.records()
    assert len(records) == 1
    assert excluded == 1
    assert records[0]["true_causes"] == {"street_name_issue"}


def test_stratified_split_is_seeded_and_balanced():
    records = [
        {
            "policy_id": f"POL-{index:06d}",
            "true_causes": {"street_name_issue"} if index % 2 else {"zip_issue"},
        }
        for index in range(10)
    ]
    first = stratified_split(records, seed=19)
    second = stratified_split(records, seed=19)
    assert first == second
    calibration, test = first
    assert len(calibration) == len(test) == 5


def test_conformal_quantile_uses_finite_sample_rank_and_handles_rank_overflow():
    assert conformal_quantile([0.2, 0.4, 0.6, 0.8], 0.5) == 0.6
    assert conformal_quantile([0.2, 0.4, 0.6, 0.8], 0.1) == 1.0


def test_prediction_set_and_test_coverage_metrics():
    prediction = build_prediction_set(
        {
            "street_name_issue": 3,
            "city_issue": 2,
            "zip_issue": 0,
            "missing_house_number": 0,
            "address_not_found": 0,
        },
        5,
        0.6,
    )
    assert prediction == ["street_name_issue", "city_issue"]
    records = [
        {
            "policy_id": "a",
            "true_causes": {"street_name_issue", "city_issue"},
            "prediction_set": prediction,
        },
        {
            "policy_id": "b",
            "true_causes": {"zip_issue"},
            "prediction_set": ["city_issue"],
        },
    ]
    metrics = calculate_test_metrics(records, {"a", "b"})
    assert metrics["test_records"] == 2
    assert metrics["contains_all_true_labels"] == 0.5
    assert metrics["contains_at_least_one_true_label"] == 0.5
    assert metrics["mean_prediction_set_size"] == 1.5
    assert metrics["singleton_share"] == 0.5


def test_two_cause_sample_frequency_can_cover_every_true_label():
    frequencies = {
        "street_name_issue": 1,
        "city_issue": 1,
        "zip_issue": 0,
        "missing_house_number": 0,
        "address_not_found": 0,
    }
    prediction = build_prediction_set(frequencies, 5, 0.8)
    record = {
        "policy_id": "multi",
        "true_causes": {"street_name_issue", "city_issue"},
        "prediction_set": prediction,
    }
    assert set(prediction) == record["true_causes"]
    assert calculate_test_metrics([record], {"multi"})[
        "contains_all_true_labels"
    ] == 1.0


def test_cache_key_includes_sample_index_and_dry_run_never_calls_network(tmp_path):
    diagnoser = _diagnoser(tmp_path, labels=["street_typo"])
    records, _ = diagnoser.records()
    model_input = records[0]["model_input"]
    sample_zero = diagnoser._cache_path("POL-000001", model_input, 0)
    sample_one = diagnoser._cache_path("POL-000001", model_input, 1)
    assert sample_zero != sample_one
    sample_zero.parent.mkdir(parents=True)
    sample_zero.write_text('{"content":"cached"}', encoding="utf-8")
    counts = diagnoser.dry_run()
    assert counts["labelled_records"] == 1
    assert counts["planned_calls"] == 4
    assert counts["cached_samples"] == 1
    assert counts["estimated_tokens"] > 0
    assert counts["budget_sufficient"] is True


def test_execute_uses_one_call_per_sample_and_writes_aggregate_reports(tmp_path):
    paths, cache_root = _fixture_inputs(tmp_path, labels=["street_typo", "missing_zip"])
    calls = []

    class Response:
        status_code = 200
        headers = {}

        @staticmethod
        def json():
            return {
                "model": "openai/gpt-oss-120b",
                "choices": [
                    {
                        "message": {
                            "content": '{"causes":["street_name_issue","city_issue"]}'
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"total_tokens": 12},
            }

    def post(url, *, headers, json, timeout):
        calls.append((url, headers, json, timeout))
        return Response()

    diagnoser = RemediationDiagnoser(
        _settings(samples=2),
        policies_path=paths["policies"],
        results_path=paths["results"],
        ground_truth_path=paths["truth"],
        geocode_cache_root=cache_root,
        output_path=tmp_path / "diagnosis.jsonl",
        cache_root=tmp_path / "cache",
        daily_usage_path=tmp_path / "daily.json",
        report_md_path=tmp_path / "reports" / "remediation_calibration.md",
        report_csv_path=tmp_path / "reports" / "remediation_calibration.csv",
        post=post,
    )
    summary = diagnoser.execute()

    assert summary["api_calls"] == 4
    assert len(calls) == 4
    assert all(call[1]["Authorization"] == "Bearer groq-test-secret" for call in calls)
    assert all(call[2]["temperature"] == 1.0 for call in calls)
    assert all(call[2]["max_completion_tokens"] == 300 for call in calls)
    assert all(call[2]["reasoning_effort"] == "low" for call in calls)
    assert all("n" not in call[2] for call in calls)
    assert all(call[2]["response_format"]["json_schema"]["strict"] for call in calls)
    causes_schema = calls[0][2]["response_format"]["json_schema"]["schema"]
    assert causes_schema["required"] == ["causes"]
    assert causes_schema["properties"]["causes"]["minItems"] == 1
    assert causes_schema["properties"]["causes"]["maxItems"] == 2
    assert causes_schema["properties"]["causes"]["uniqueItems"] is True
    assert MAX_COMPLETION_TOKENS == 300
    for _, _, payload, _ in calls:
        sent = json.dumps(payload["messages"])
        assert "street_typo" not in sent
        assert "formattedStreetAddress" not in sent
        assert "DO NOT SEND" not in sent
    output = [
        json.loads(line)
        for line in (tmp_path / "diagnosis.jsonl").read_text().splitlines()
    ]
    assert len(output) == 2
    assert all(output_row["model_reported"] == ["openai/gpt-oss-120b"] * 2 for output_row in output)
    assert output[0]["frequencies"]["street_name_issue"] == 2
    assert output[0]["frequencies"]["city_issue"] == 2
    assert "12 Example St" not in (tmp_path / "diagnosis.jsonl").read_text()
    assert "formattedStreetAddress" not in (
        tmp_path / "reports" / "remediation_calibration.md"
    ).read_text()
    assert (tmp_path / "reports" / "remediation_calibration.csv").exists()


def test_truncated_and_invalid_json_samples_fail_without_frequency_counts(tmp_path):
    paths, cache_root = _fixture_inputs(
        tmp_path, labels=["street_typo", "missing_zip"]
    )
    calls = []

    class Response:
        status_code = 200
        headers = {}

        def __init__(self, index):
            self.index = index

        def json(self):
            if self.index % 2:
                content, finish_reason = "{invalid", "stop"
            else:
                content, finish_reason = '{"causes":["street_name_issue"]}', "length"
            return {
                "model": "openai/gpt-oss-120b",
                "choices": [
                    {
                        "message": {"content": content},
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": {"total_tokens": 9},
            }

    def post(url, *, headers, json, timeout):
        calls.append(url)
        return Response(len(calls) - 1)

    diagnoser = RemediationDiagnoser(
        _settings(samples=2),
        policies_path=paths["policies"],
        results_path=paths["results"],
        ground_truth_path=paths["truth"],
        geocode_cache_root=cache_root,
        output_path=tmp_path / "diagnosis.jsonl",
        cache_root=tmp_path / "cache",
        daily_usage_path=tmp_path / "daily.json",
        report_md_path=tmp_path / "reports" / "remediation_calibration.md",
        report_csv_path=tmp_path / "reports" / "remediation_calibration.csv",
        post=post,
    )
    summary = diagnoser.execute()

    assert len(calls) == 4
    assert summary["samples_failed"] == 4
    assert summary["records_with_fewer_than_k_valid_samples"] == 2
    output = [
        json.loads(line)
        for line in (tmp_path / "diagnosis.jsonl").read_text().splitlines()
    ]
    assert all(record["status"] == "failed" for record in output)
    reasons = {
        failure["reason"]
        for record in output
        for failure in record["sample_failures"]
    }
    assert reasons == {"truncated_response", "invalid_json_response"}
    assert all(not any(record["frequencies"].values()) for record in output)
    report = (tmp_path / "reports" / "remediation_calibration.md").read_text()
    assert "- Samples failed: 4" in report
    assert "- Records with fewer than K valid samples: 2" in report


def test_uninformative_quantile_emits_warning(tmp_path):
    diagnoser = _diagnoser(
        tmp_path,
        labels=["street_typo", "missing_zip"],
        samples=1,
        alpha=0.1,
    )

    class Response:
        status_code = 200
        headers = {}

        @staticmethod
        def json():
            return {
                "model": "openai/gpt-oss-120b",
                "choices": [
                    {
                        "message": {"content": '{"causes":["street_name_issue"]}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"total_tokens": 2},
            }

    diagnoser.post = lambda *args, **kwargs: Response()
    with pytest.warns(RuntimeWarning, match="uninformative"):
        summary = diagnoser.execute()
    assert summary["quantile"] == 1.0
    assert summary["uninformative_quantile_warning"] is True


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("REMEDIATION_SAMPLES", "0"),
        ("REMEDIATION_SAMPLES", "invalid"),
        ("CONFORMAL_ALPHA", "0"),
        ("CONFORMAL_ALPHA", "1.1"),
        ("CONFORMAL_ALPHA", "invalid"),
    ],
)
def test_remediation_settings_validation(monkeypatch, tmp_path, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError):
        Settings.from_env(dotenv_path=tmp_path / "none.env")


def test_default_remediation_settings(monkeypatch, tmp_path):
    monkeypatch.delenv("REMEDIATION_SAMPLES", raising=False)
    monkeypatch.delenv("CONFORMAL_ALPHA", raising=False)
    settings = Settings.from_env(dotenv_path=tmp_path / "none.env")
    assert settings.remediation_samples == 5
    assert settings.conformal_alpha == 0.10
