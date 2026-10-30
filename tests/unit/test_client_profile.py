import csv
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from address_intelligence.client_profile import (
    DEFAULT_PROFILE_PATH,
    estimate_cost,
    load_client_profile,
    read_client_policies,
)
from address_intelligence.config import ConfigError, Settings
from address_intelligence.credits import PRICE_TABLE
from address_intelligence.pipeline import StagedPipeline
from address_intelligence.providers.precisely import ProviderResponse


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_PROFILE = ROOT / "config" / "examples" / "client_profile_other_carrier.yaml"


def _read_yaml(path):
    with path.open(encoding="utf-8") as source:
        return yaml.safe_load(source)


def _write_yaml(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as destination:
        yaml.safe_dump(data, destination, sort_keys=False)


def _profile_fixture(tmp_path, *, columns=None):
    columns = columns or [
        "policy_id",
        "raw_address_line1",
        "raw_address_line2",
        "raw_city",
        "raw_state",
        "raw_zip5",
        "metro",
    ]
    input_path = tmp_path / "input.csv"
    with input_path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        row = {
                "policy_id": "P-1",
                "raw_address_line1": "12 Sample Rd",
                "raw_address_line2": "",
                "raw_city": "Sample City",
                "raw_state": "TX",
                "raw_zip5": "12345",
                "metro": "Sample TX",
                "contract_ref": "C-1",
                "street_primary": "12 Sample Rd",
                "street_secondary": "",
                "municipality": "Sample City",
                "region_code": "TX",
                "postal": "12345",
                "market": "Sample TX",
            }
        writer.writerow({column: row.get(column, "") for column in columns})
    data = _read_yaml(DEFAULT_PROFILE_PATH)
    data["input"]["path"] = str(input_path)
    profile_path = tmp_path / "client_profile.yaml"
    _write_yaml(profile_path, data)
    return profile_path, input_path, data


def test_default_profile_reproduces_hardcoded_pipeline_values():
    profile = load_client_profile()
    assert profile.data["client"]["name"] == "Fictional national insurance carrier (synthetic)"
    assert profile.input_path == ROOT / "data" / "input" / "policyholders_synthetic.csv"
    assert profile.column_mapping == {
        "policy_id": "policy_id",
        "address_line1": "raw_address_line1",
        "address_line2": "raw_address_line2",
        "city": "raw_city",
        "state": "raw_state",
        "zip5": "raw_zip5",
        "metro": "metro",
    }
    assert profile.eligible_precision_prefixes == ("S5", "S7", "S8")
    assert profile.comparison_sample_size == 40
    assert profile.comparison_seed == 20261029
    assert profile.enabled_operations == (
        "flood_by_location",
        "distance_to_flood_hazard_by_location",
        "property_attributes_v2",
        "preciselyid_by_address",
    )
    assert profile.remediation_samples == 5
    assert profile.conformal_alpha == 0.10
    assert PRICE_TABLE["geocode_premium"] == Decimal("1")


def test_other_carrier_column_mapping_normalizes_fixture(tmp_path):
    input_path = tmp_path / "fixtures" / "other_carrier.csv"
    input_path.parent.mkdir(parents=True)
    with input_path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(
            destination,
            fieldnames=(
                "contract_ref",
                "street_primary",
                "street_secondary",
                "municipality",
                "region_code",
                "postal",
                "market",
            ),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerow(
            {
                "contract_ref": "C-42",
                "street_primary": "8 Oak St",
                "street_secondary": "Unit 2",
                "municipality": "Austin",
                "region_code": "TX",
                "postal": "78701",
                "market": "Austin TX",
            }
        )
    profile = load_client_profile(EXAMPLE_PROFILE, project_root=tmp_path)
    policies = read_client_policies(profile)
    assert len(policies) == 1
    assert policies[0]["policy_id"] == "C-42"
    assert policies[0]["raw_address_line1"] == "8 Oak St"
    assert policies[0]["raw_address_line2"] == "Unit 2"
    assert policies[0]["raw_city"] == "Austin"
    assert policies[0]["raw_state"] == "TX"
    assert policies[0]["raw_zip5"] == "78701"
    assert policies[0]["metro"] == "Austin TX"


def test_unknown_top_level_key_is_rejected(tmp_path):
    path, _, data = _profile_fixture(tmp_path)
    data["surprise"] = True
    _write_yaml(path, data)
    with pytest.raises(ConfigError, match="Unknown client profile key"):
        load_client_profile(path, project_root=tmp_path)


def test_unknown_operation_is_rejected(tmp_path):
    path, _, data = _profile_fixture(tmp_path)
    data["enrichment"]["operations"][0]["name"] = "not_a_priced_operation"
    _write_yaml(path, data)
    with pytest.raises(ConfigError, match="Unknown enrichment operation"):
        load_client_profile(path, project_root=tmp_path)


def test_missing_mapped_column_is_rejected_with_column_name(tmp_path):
    path, _, data = _profile_fixture(tmp_path)
    data["input"]["column_mapping"]["metro"] = "market_missing"
    _write_yaml(path, data)
    with pytest.raises(ConfigError, match="market_missing"):
        load_client_profile(path, project_root=tmp_path)


@pytest.mark.parametrize("alpha", [0, 1, -0.1, 1.1])
def test_alpha_outside_open_unit_interval_is_rejected(tmp_path, alpha):
    path, _, data = _profile_fixture(tmp_path)
    data["ai"]["remediation"]["conformal_alpha"] = alpha
    _write_yaml(path, data)
    with pytest.raises(ConfigError, match="conformal_alpha"):
        load_client_profile(path, project_root=tmp_path)


def test_sample_count_below_one_is_rejected(tmp_path):
    path, _, data = _profile_fixture(tmp_path)
    data["ai"]["remediation"]["samples"] = 0
    _write_yaml(path, data)
    with pytest.raises(ConfigError, match="samples must be at least 1"):
        load_client_profile(path, project_root=tmp_path)


def test_secret_like_profile_key_is_rejected(tmp_path):
    path, _, data = _profile_fixture(tmp_path)
    data["client"]["api_token"] = "should-never-be-here"
    _write_yaml(path, data)
    with pytest.raises(ConfigError, match="keep secrets in .env"):
        load_client_profile(path, project_root=tmp_path)


def test_disabled_operation_is_not_planned(tmp_path):
    path, input_path, data = _profile_fixture(tmp_path)
    data["enrichment"]["operations"][0]["enabled"] = False
    _write_yaml(path, data)
    profile = load_client_profile(path, project_root=tmp_path)
    pipeline = StagedPipeline(
        settings=Settings(
            api_key=None,
            api_secret=None,
            base_url="https://api.precisely.com",
            credit_hard_cap=Decimal("100"),
            trial_credits=Decimal("100"),
            log_level="INFO",
        ),
        ledger=type("Ledger", (), {})(),
        input_path=input_path,
        project_root=tmp_path,
        profile=profile,
    )
    policies = read_client_policies(profile)
    request = pipeline._geocode_request(
        policies[0],
        tier="premium",
    )
    data = {
        "candidates": [
            {
                "precisionCode": "S8",
                "formattedStreetAddress": "12 Sample Rd",
                "address": {
                    "mainAddressLine": "12 Sample Rd",
                    "areaName3": "Sample City",
                    "areaName1": "TX",
                    "postCode1": "12345",
                },
                "geometry": {"coordinates": "-97.0,30.0"},
            }
        ]
    }
    pipeline._write_cache(request, ProviderResponse(200, data, None, Decimal("0")))
    planned = pipeline.plan("enrich")
    assert "flood_by_location" not in [item.operation for item in planned]
    assert {item.operation for item in planned} == {
        "distance_to_flood_hazard_by_location",
        "property_attributes_v2",
    }


def test_cost_estimate_uses_credit_table_math():
    profile = load_client_profile()
    estimate = estimate_cost(profile, records=10, eligible_share=0.5)
    assert estimate["geocode_credits"] == Decimal("10")
    assert estimate["eligible_operations_credits"] == Decimal("40.05")
    assert estimate["total_credits"] == Decimal("50.05")
    assert estimate["list_price_usd"] == Decimal("0.5005")
    assert estimate["credits_per_policy"] == Decimal("5.005")


def test_environment_alpha_overrides_profile_value(tmp_path, monkeypatch):
    path, _, data = _profile_fixture(tmp_path)
    data["ai"]["remediation"]["conformal_alpha"] = 0.25
    _write_yaml(path, data)
    monkeypatch.setenv("CONFORMAL_ALPHA", "0.07")
    monkeypatch.setenv("REMEDIATION_SAMPLES", "3")
    settings = Settings.from_env(
        dotenv_path=tmp_path / "absent.env",
        profile_path=path,
    )
    assert settings.conformal_alpha == 0.07
    assert settings.remediation_samples == 3
