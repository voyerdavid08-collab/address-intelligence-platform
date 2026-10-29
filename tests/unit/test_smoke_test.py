from decimal import Decimal
from unittest.mock import patch

import requests

from address_intelligence.credits import CreditLedger
from address_intelligence.providers.precisely import ProviderResponse
from scripts.smoke_test import CallResult, _print_summary, main


def _use_temporary_ledger(monkeypatch, path):
    def initialize(self, hard_cap, ledger_path=None):
        self.hard_cap = Decimal(hard_cap)
        self.path = path

    monkeypatch.setattr(CreditLedger, "__init__", initialize)


def test_dry_run_prints_call_plan_and_makes_no_http_calls(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CREDIT_HARD_CAP", "2150")
    _use_temporary_ledger(monkeypatch, tmp_path / "ledger.json")

    with patch.object(requests, "Session") as session_factory:
        assert main(["--stage", "a"]) == 0
        session_factory.assert_not_called()

    output = capsys.readouterr().out
    assert "geocode_advanced" in output
    assert "preciselyid_by_address" in output
    assert "address_verification_us" in output
    assert "Planned credit estimate: 12.81" in output
    assert "Account credits left: 2500" in output


def test_stage_b_dry_run_estimates_one_credit(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CREDIT_HARD_CAP", "2150")
    _use_temporary_ledger(monkeypatch, tmp_path / "ledger.json")

    assert main(["--stage", "b"]) == 0

    output = capsys.readouterr().out
    assert "property_attributes_v2" in output
    assert "Planned credit estimate: 1" in output


def test_summary_reads_nested_property_built_year(capsys):
    response = ProviderResponse(
        status_code=200,
        data={
            "builtYear": 1900,
            "propertyAttributes": {"builtYear": 1980},
        },
        error=None,
        credits_recorded=Decimal("1"),
    )

    _print_summary(
        [CallResult("A1 clean", "property_attributes_v2", response)]
    )

    output = capsys.readouterr().out
    assert "builtYear" in output
    assert "1980" in output
    assert "1900" not in output
