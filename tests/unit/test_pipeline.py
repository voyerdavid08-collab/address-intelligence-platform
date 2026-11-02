import csv
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
import requests

from address_intelligence.config import Settings
from address_intelligence.credits import CreditCapExceeded, CreditLedger
from address_intelligence.pipeline import (
    StagedPipeline,
    _geocode_reason,
    haversine_km,
    run_pipeline,
)
from address_intelligence.providers.precisely import ProviderResponse


def _settings():
    return Settings(
        api_key="key",
        api_secret="secret",
        base_url="https://api.precisely.com",
        credit_hard_cap=Decimal("2150"),
        trial_credits=Decimal("2500"),
        log_level="INFO",
    )


def _response(data=None, *, status_code=200, error=None, retry_after=None):
    return ProviderResponse(
        status_code=status_code,
        data=data,
        error=error,
        credits_recorded=Decimal("0"),
        retry_after=retry_after,
    )


def _geocode_data(
    code="S8",
    *,
    line1="10 Main St",
    line2="",
    city="Alpha",
    state="TX",
    zip5="70001",
    coords="-95.0,29.0",
):
    return {
        "candidates": [
            {
                "precisionCode": code,
                "formattedStreetAddress": line1,
                "address": {
                    "mainAddressLine": line1,
                    "areaName3": city,
                    "areaName1": state,
                    "postCode1": zip5,
                },
                "geometry": {"coordinates": coords},
            }
        ]
    }


def _write_input(path, rows):
    columns = (
        "policy_id",
        "line_of_business",
        "effective_date",
        "insured_value_band",
        "metro",
        "street_id",
        "raw_address_line1",
        "raw_address_line2",
        "raw_city",
        "raw_state",
        "raw_zip5",
    )
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _row(policy_id="POL-000001", metro="Houston TX"):
    return {
        "policy_id": policy_id,
        "line_of_business": "Homeowners",
        "effective_date": "2025-01-01",
        "insured_value_band": "250-500k",
        "metro": metro,
        "street_id": "HOU-01",
        "raw_address_line1": "999 Raw Rd",
        "raw_address_line2": "Apt 5",
        "raw_city": "Raw City",
        "raw_state": "TX",
        "raw_zip5": "70002",
    }


class FakeClient:
    def __init__(self, ledger, responses=None):
        self.ledger = ledger
        self.responses = responses or {}
        self.calls = []

    def _call(self, operation, *args):
        self.calls.append((operation, args))
        self.ledger.check(operation, 1)
        queue = self.responses.get(operation)
        response = queue.pop(0) if queue else None
        if response is None:
            if operation.startswith("geocode_"):
                tier = operation.removeprefix("geocode_")
                if tier == "basic":
                    response = _response(
                        _geocode_data(
                            "G3",
                            line1="",
                            city="Alpha",
                            coords="-95.1,29.1",
                        )
                    )
                else:
                    response = _response(_geocode_data())
            elif operation == "preciselyid_by_address":
                response = _response({"key": "PID-1"})
            elif operation == "flood_by_location":
                response = _response(
                    {
                        "floodZone": {
                            "code": "X",
                            "riskLevel": "Low",
                            "baseFloodElevation": {"value": "3", "unit": "FT"},
                        }
                    }
                )
            elif operation == "distance_to_flood_hazard_by_location":
                response = _response(
                    {
                        "waterBody": [
                            {
                                "name": "Sample Bay",
                                "distance": {"value": "2.5", "unit": "MILES"},
                            }
                        ]
                    }
                )
            elif operation == "property_attributes_v2":
                response = _response({"propertyAttributes": {"builtYear": 1980}})
        if response.status_code == 200:
            self.ledger.record_success(operation, 1)
        else:
            self.ledger.record_failure(operation)
        return response

    def geocode(self, address, tier):
        return self._call(f"geocode_{tier}", address)

    def preciselyid_by_address(self, address):
        return self._call("preciselyid_by_address", address)

    def flood_by_location(self, latitude, longitude):
        return self._call("flood_by_location", latitude, longitude)

    def distance_to_flood_hazard_by_location(self, latitude, longitude):
        return self._call("distance_to_flood_hazard_by_location", latitude, longitude)

    def property_attributes_by_address(self, address, attributes):
        return self._call("property_attributes_v2", address, attributes)


def _pipeline(tmp_path, rows, *, ledger_cap="2150", client=None):
    project_root = tmp_path / "project"
    input_path = project_root / "data" / "input" / "policyholders_synthetic.csv"
    input_path.parent.mkdir(parents=True)
    _write_input(input_path, rows)
    ledger = CreditLedger(
        Decimal(ledger_cap),
        project_root / "data" / "state" / "ledger.json",
    )
    fake_client = client or FakeClient(ledger)
    pipeline = StagedPipeline(
        _settings(),
        ledger,
        client=fake_client,
        input_path=input_path,
        project_root=project_root,
    )
    return pipeline, fake_client


@pytest.mark.parametrize("code", ["S5", "S7", "S8", "S8HPN-S--A"])
def test_premium_address_precision_codes_are_eligible(code):
    assert _geocode_reason(_response(_geocode_data(code)))[0] == "eligible"


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("S4-PNTSC-A", "street-level only"),
        ("Z2", "postal or city level"),
        ("G3", "postal or city level"),
        ("S0", "no match"),
    ],
)
def test_non_address_precision_codes_route_to_manual_review(code, reason):
    assert _geocode_reason(_response(_geocode_data(code))) == ("manual_review", reason)


def test_no_candidate_and_api_error_are_manual_review():
    assert _geocode_reason(_response({"candidates": []})) == (
        "manual_review",
        "no match",
    )
    assert _geocode_reason(_response(status_code=503, error="safe")) == (
        "manual_review",
        "API error",
    )


def test_state_consistency_is_required_for_address_level_eligibility():
    response = _response(_geocode_data(state="LA"))

    assert _geocode_reason(response, "TX") == (
        "manual_review",
        "location conflict (state mismatch)",
    )
    assert _geocode_reason(response, "la") == ("eligible", "")


def test_location_conflict_is_excluded_from_all_followup_plans(tmp_path):
    conflicted = _row("POL-CONFLICT")
    matching = _row("POL-MATCH")
    pipeline, _ = _pipeline(tmp_path, [conflicted, matching])

    conflict_geocode = _response(_geocode_data(state="LA"))
    matching_geocode = _response(_geocode_data(state="TX"))
    for row, response in (
        (conflicted, conflict_geocode),
        (matching, matching_geocode),
    ):
        pipeline._write_cache(
            pipeline._geocode_request(row, tier="premium"),
            response,
        )
    pipeline._write_cache(
        pipeline._flood_request(matching, (29.0, -95.0)),
        _response({"floodZone": {"code": "X"}}),
    )

    for stage in ("preciselyid", "enrich", "compare"):
        planned = pipeline.plan(stage)
        assert planned
        assert all(request.policy_id == matching["policy_id"] for request in planned)
        assert all(request.policy_id != conflicted["policy_id"] for request in planned)


def test_standardized_address_used_for_preciselyid_and_property(tmp_path, capsys):
    pipeline, client = _pipeline(tmp_path, [_row()])

    pipeline.run_stage("geocode")
    pipeline.run_stage("preciselyid")
    pipeline.run_stage("enrich")
    capsys.readouterr()

    assert ("preciselyid_by_address", ("10 Main St, Alpha, TX 70001",)) in client.calls
    assert (
        "property_attributes_v2",
        ("10 Main St, Alpha, TX 70001", "builtYear"),
    ) in client.calls
    with pipeline.results_path.open(newline="", encoding="utf-8") as source:
        result = next(csv.DictReader(source))
    assert result["preciselyid"] == "PID-1"
    assert result["built_year"] == "1980"
    assert result["flood_zone_code"] == "X"
    assert result["distance_to_water"] == "2.5"


def test_real_response_shape_contract_maps_street_and_request_addresses(tmp_path):
    fixture_path = (
        Path(__file__).parents[1]
        / "fixtures"
        / "geocode_premium_response_shape.json"
    )
    geocode_data = json.loads(fixture_path.read_text(encoding="utf-8"))
    pipeline, _ = _pipeline(tmp_path, [_row()])
    from address_intelligence.pipeline import _apply_geocode, _empty_result

    mapped = _empty_result("POL-000001")
    _apply_geocode(mapped, _response(geocode_data))

    assert mapped["standardized_address_line1"] == "100 EXAMPLE ST"
    assert mapped["standardized_city"] == "SAMPLE CITY"
    assert mapped["standardized_state"] == "ZZ"
    assert mapped["standardized_zip5"] == "00000"
    row = _row()
    preciselyid = pipeline._preciselyid_request(row, geocode_data)
    property_request = pipeline._property_request(row, geocode_data)
    assert preciselyid.params["address"] == (
        "100 EXAMPLE ST, SAMPLE CITY, ZZ 00000"
    )
    assert property_request.params["address"] == (
        "100 EXAMPLE ST, SAMPLE CITY, ZZ 00000"
    )


def test_formatted_street_address_is_fallback(tmp_path):
    fixture_path = (
        Path(__file__).parents[1]
        / "fixtures"
        / "geocode_premium_response_shape.json"
    )
    geocode_data = json.loads(fixture_path.read_text(encoding="utf-8"))
    del geocode_data["candidates"][0]["address"]["mainAddressLine"]
    from address_intelligence.pipeline import _apply_geocode, _empty_result

    mapped = _empty_result("POL-000001")
    _apply_geocode(mapped, _response(geocode_data))

    assert mapped["standardized_address_line1"] == "100 EXAMPLE ST"


def test_missing_standardized_street_prevents_preciselyid_and_property(
    tmp_path, capsys
):
    data = _geocode_data(line1="", city="SAMPLE CITY", state="ZZ", zip5="00000")
    data["candidates"][0]["formattedStreetAddress"] = ""
    row = _row()
    row["raw_state"] = "ZZ"
    pipeline, client = _pipeline(tmp_path, [row])
    geocode_request = pipeline._geocode_request(row, tier="premium")
    pipeline._write_cache(
        geocode_request,
        _response(data),
    )

    preciselyid_plan = pipeline.plan("preciselyid")
    enrich_plan = pipeline.plan("enrich")
    assert all(request.operation != "preciselyid_by_address" for request in preciselyid_plan)
    assert all(request.operation != "property_attributes_v2" for request in enrich_plan)

    before = pipeline.ledger.cumulative_spent
    pipeline.run_stage("preciselyid")
    capsys.readouterr()

    assert all(
        operation not in {"preciselyid_by_address", "property_attributes_v2"}
        for operation, _ in client.calls
    )
    assert pipeline.ledger.cumulative_spent == before
    with pipeline.results_path.open(newline="", encoding="utf-8") as source:
        result_row = next(csv.DictReader(source))
    assert result_row["preciselyid_status"] == "failed: missing standardized street"


def test_cache_hit_skips_call_and_credit(tmp_path, capsys):
    pipeline, first_client = _pipeline(tmp_path, [_row()])
    pipeline.run_stage("geocode")
    capsys.readouterr()
    spent_after_first = pipeline.ledger.cumulative_spent

    second_client = FakeClient(pipeline.ledger)
    resumed = StagedPipeline(
        _settings(),
        pipeline.ledger,
        client=second_client,
        input_path=pipeline.input_path,
        project_root=pipeline.project_root,
    )
    resumed.run_stage("geocode")
    capsys.readouterr()

    assert first_client.calls
    assert second_client.calls == []
    assert pipeline.ledger.cumulative_spent == spent_after_first
    with resumed.results_path.open(newline="", encoding="utf-8") as source:
        result = next(csv.DictReader(source))
    assert result["geocode_provenance"] == "cached"


def test_http_200_no_match_response_is_cached(tmp_path, capsys):
    pipeline, client = _pipeline(
        tmp_path,
        [_row()],
        client=None,
    )
    client.responses["geocode_premium"] = [
        _response({"candidates": []}),
    ]
    pipeline.run_stage("geocode")
    capsys.readouterr()
    spent = pipeline.ledger.cumulative_spent
    retry_client = FakeClient(pipeline.ledger)
    pipeline.client = retry_client
    pipeline.run_stage("geocode")
    capsys.readouterr()

    assert len(client.calls) == 1
    assert retry_client.calls == []
    assert pipeline.ledger.cumulative_spent == spent
    cached = next(
        (pipeline.cache_root / "geocode_premium_structured").glob("*.json")
    )
    assert json.loads(cached.read_text(encoding="utf-8"))["status_code"] == 200
    with pipeline.results_path.open(newline="", encoding="utf-8") as source:
        result = next(csv.DictReader(source))
    assert result["eligibility"] == "manual_review"
    assert result["manual_review_reason"] == "no match"


@pytest.mark.parametrize(
    ("statuses", "expected_calls"),
    [
        ([429, 200], 2),
        ([503, 502, 200], 3),
        ([400, 200], 1),
    ],
)
def test_transient_statuses_retry_but_other_4xx_do_not(
    tmp_path, capsys, monkeypatch, statuses, expected_calls
):
    pipeline, client = _pipeline(tmp_path, [_row()])
    client.responses["geocode_premium"] = [
        _response(
            _geocode_data() if status == 200 else {"message": "failure"},
            status_code=status,
            error=None if status == 200 else "safe",
            retry_after="0" if status == 429 else None,
        )
        for status in statuses
    ]
    monkeypatch.setattr("address_intelligence.pipeline.time.sleep", lambda delay: None)
    request = pipeline._geocode_request(_row(), tier="premium")

    result = pipeline._invoke(request)

    assert result.status_code == statuses[expected_calls - 1]
    assert len(client.calls) == expected_calls
    capsys.readouterr()


def test_retry_after_is_honored(tmp_path, monkeypatch):
    pipeline, client = _pipeline(tmp_path, [_row()])
    client.responses["geocode_premium"] = [
        _response({"message": "slow down"}, status_code=429, retry_after="2"),
        _response(_geocode_data()),
    ]
    delays = []
    monkeypatch.setattr(
        "address_intelligence.pipeline.time.sleep",
        lambda delay: delays.append(delay),
    )

    pipeline._invoke(pipeline._geocode_request(_row(), tier="premium"))

    assert delays == [2.0]


def test_request_hash_prevents_stale_cache_reuse(tmp_path, capsys):
    pipeline, client = _pipeline(tmp_path, [_row()])
    pipeline.run_stage("geocode")
    capsys.readouterr()
    changed = _row()
    changed["raw_address_line1"] = "100 Changed St"
    request = pipeline._geocode_request(changed, tier="premium")

    assert pipeline._read_cache(request) is None


def test_structured_geocode_parameters_and_empty_fields_omitted(tmp_path):
    row = _row()
    row.update(
        {
            "raw_address_line1": "1141 Napoheon Ave",
            "raw_address_line2": "Apt 3",
            "raw_city": "",
            "raw_state": "LA",
            "raw_zip5": "",
        }
    )
    pipeline, _ = _pipeline(tmp_path, [row])

    request = pipeline._geocode_request(row, tier="premium")
    client = Mock()
    request.invoke(client)

    assert request.request_mode == "structured"
    assert request.params == {
        "mainAddress": "1141 Napoheon Ave Apt 3",
        "areaName1": "LA",
        "country": "USA",
    }
    client.geocode.assert_called_once_with(
        {
            "mainAddress": "1141 Napoheon Ave Apt 3",
            "areaName1": "LA",
            "country": "USA",
        },
        "premium",
    )


def test_single_line_mode_still_uses_original_format(tmp_path):
    pipeline, client = _pipeline(tmp_path, [_row()])
    pipeline.geocode_input_mode = "single-line"

    request = pipeline._geocode_request(_row(), tier="premium")
    request.invoke(client)

    assert request.params == {
        "mainAddress": "999 Raw Rd Apt 5, Raw City, TX 70002"
    }
    assert request.request_mode == "single-line"
    assert request.cache_operation == "geocode_premium"
    assert client.calls == [
        ("geocode_premium", ("999 Raw Rd Apt 5, Raw City, TX 70002",))
    ]


def test_geocode_mode_is_part_of_cache_key_and_cache_location(tmp_path):
    pipeline, client = _pipeline(tmp_path, [_row()])
    structured = pipeline._geocode_request(_row(), tier="premium")
    single = pipeline._geocode_request(
        _row(),
        tier="premium",
        input_mode="single-line",
    )
    client.responses["geocode_premium"] = [_response(_geocode_data())]

    assert pipeline._cache_path(structured) != pipeline._cache_path(single)
    assert structured.params != single.params
    pipeline._invoke(structured)
    pipeline._invoke(single)

    assert pipeline._cache_path(structured).exists()
    assert pipeline._cache_path(single).exists()
    assert pipeline._read_cache(structured) is not None
    assert pipeline._read_cache(single) is not None


def test_legacy_single_line_cache_remains_readable(tmp_path):
    pipeline, _ = _pipeline(tmp_path, [_row()])
    request = pipeline._geocode_request(
        _row(),
        tier="premium",
        input_mode="single-line",
    )
    path = pipeline._cache_path(request)
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy_hash = json.dumps(
        {
            "operation": request.operation,
            "params": request.params,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    path.write_text(
        json.dumps(
            {
                "request_hash": hashlib.sha256(legacy_hash.encode("utf-8")).hexdigest(),
                "status_code": 200,
                "data": _geocode_data(),
                "error": None,
            }
        ),
        encoding="utf-8",
    )

    assert pipeline._read_cache(request) is not None


def test_budget_gate_refuses_before_operation(tmp_path, capsys):
    pipeline, client = _pipeline(tmp_path, [_row()], ledger_cap="0.5")
    request = pipeline.plan("geocode")
    estimate = pipeline.print_plan("geocode", request)
    capsys.readouterr()

    with pytest.raises(CreditCapExceeded, match="budget gate refused"):
        pipeline.run_stage("geocode")
    assert estimate == Decimal("1")
    assert client.calls == []


def test_limit_selects_first_n_policy_ids(tmp_path):
    rows = [_row(f"POL-{index:06d}") for index in range(1, 5)]
    pipeline, _ = _pipeline(tmp_path, rows)

    planned = pipeline.plan("geocode", limit=2)

    assert [request.policy_id for request in planned] == [
        "POL-000001",
        "POL-000002",
    ]


def test_comparison_sample_is_deterministic_and_stratified(tmp_path, capsys):
    rows = []
    for metro_index, metro in enumerate(
        ("A Metro", "B Metro", "C Metro", "D Metro", "E Metro", "F Metro")
    ):
        for sequence in range(8):
            rows.append(_row(f"POL-{metro_index * 8 + sequence + 1:06d}", metro))
    first, first_client = _pipeline(tmp_path / "first", rows)
    second, second_client = _pipeline(tmp_path / "second", rows)
    first.run_stage("geocode")
    second.run_stage("geocode")
    capsys.readouterr()

    first_sample = first._compare_sample(first._eligible_records(_read_input(first.input_path)))
    second_sample = second._compare_sample(second._eligible_records(_read_input(second.input_path)))

    first_ids = [row["policy_id"] for row, _ in first_sample]
    second_ids = [row["policy_id"] for row, _ in second_sample]
    counts = {}
    for row, _ in first_sample:
        counts[row["metro"]] = counts.get(row["metro"], 0) + 1
    assert first_ids == second_ids
    assert len(first_ids) == 40
    assert sorted(counts.values()) == [6, 6, 7, 7, 7, 7]
    assert len(first_client.calls) == len(second_client.calls) == 48


def _read_input(path):
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def test_haversine_sanity():
    assert haversine_km((0, 0), (0, 1)) == pytest.approx(111.195, rel=0.001)
    assert haversine_km((10, 10), (10, 10)) == 0
    assert haversine_km(None, (0, 0)) is None


def test_rowlevel_output_is_only_under_data_output_and_ground_truth_is_not_opened(
    tmp_path, monkeypatch
):
    pipeline, _ = _pipeline(tmp_path, [_row()])
    original_open = Path.open

    def checked_open(path, *args, **kwargs):
        if path.name == "ground_truth.csv":
            raise AssertionError("Pipeline must not open ground_truth.csv")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", checked_open)
    pipeline.run_stage("geocode")

    assert pipeline.results_path.exists()
    assert pipeline.results_path.parts[-3:] == (
        "output",
        "run",
        "results_rowlevel.csv",
    )
    assert "reports" not in pipeline.results_path.parts


def test_dry_run_has_no_http_calls_and_reports_budget(tmp_path, monkeypatch, capsys):
    project_root = tmp_path / "project"
    input_path = project_root / "data" / "input" / "policyholders_synthetic.csv"
    input_path.parent.mkdir(parents=True)
    _write_input(input_path, [_row(), _row("POL-000002")])
    monkeypatch.setattr(
        "address_intelligence.pipeline.Settings.from_env",
        classmethod(lambda cls: _settings()),
    )
    monkeypatch.setattr(
        "address_intelligence.pipeline.CreditLedger",
        lambda cap: CreditLedger(Decimal(cap), project_root / "data" / "state" / "ledger.json"),
    )
    with patch.object(requests, "Session") as session_factory:
        assert run_pipeline(
            ["--stage", "geocode", "--limit", "1"],
            input_path=input_path,
            project_root=project_root,
        ) == 0
        session_factory.assert_not_called()
    output = capsys.readouterr().out
    assert "geocode_premium: 1 calls, 1 credits" in output
    assert "Estimated credits: 1" in output
    assert "Account credits left: 2500" in output


def test_compare_execution_uses_basic_point_and_enrich_verified_point(tmp_path, capsys):
    pipeline, client = _pipeline(tmp_path, [_row()])
    pipeline.run_stage("geocode")
    pipeline.run_stage("enrich")
    capsys.readouterr()

    pipeline.run_stage("compare")
    output = capsys.readouterr().out

    calls = [call for call in client.calls if call[0] == "flood_by_location"]
    assert len(calls) == 2
    assert calls[0][1] == (29.0, -95.0)
    assert calls[1][1] == pytest.approx((29.1, -95.1))
    with pipeline.results_path.open(newline="", encoding="utf-8") as source:
        result = next(csv.DictReader(source))
    assert result["comparison_flood_zone_code"] == "X"
    assert result["comparison_flood_zones_differ"] == "false"
    assert float(result["comparison_distance_km"]) > 0
    assert "Compare with the portal dashboard" in output


def test_compare_requires_enrich_verified_flood_cache(tmp_path, capsys):
    pipeline, client = _pipeline(tmp_path, [_row()])
    pipeline.run_stage("geocode")
    capsys.readouterr()

    with pytest.raises(ValueError, match="run enrich first"):
        pipeline.run_stage("compare")
    assert [operation for operation, _ in client.calls] == ["geocode_premium"]
