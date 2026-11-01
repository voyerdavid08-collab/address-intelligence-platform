import json
from decimal import Decimal

import pytest

from address_intelligence.credits import (
    CreditCapExceeded,
    CreditLedger,
    UnknownOperationError,
)


def test_decimal_precision_for_small_credit_cost(tmp_path):
    ledger = CreditLedger(Decimal("10"), tmp_path / "ledger.json")

    assert ledger.check("preciselyid_by_address", 200) == Decimal("2.00")
    assert ledger.record_success("preciselyid_by_address", 200) == Decimal("2.00")
    assert ledger.cumulative_spent == Decimal("2.00")


def test_cap_is_checked_before_spending(tmp_path):
    ledger = CreditLedger(Decimal("1"), tmp_path / "ledger.json")

    with pytest.raises(CreditCapExceeded, match="spent 0.*estimate 2.*cap 1"):
        ledger.check("geocode_premium", 2)

    assert ledger.cumulative_spent == Decimal("0")
    assert not ledger.path.exists()


def test_spend_persists_across_ledger_instances(tmp_path):
    path = tmp_path / "state" / "ledger.json"
    first = CreditLedger(Decimal("10"), path)
    first.record_success("preciselyid_by_address", 200)

    second = CreditLedger(Decimal("10"), path)

    assert second.cumulative_spent == Decimal("2.00")
    assert second.check("geocode_premium", 8) == Decimal("8")


def test_failure_records_zero_cost(tmp_path, caplog):
    ledger = CreditLedger(Decimal("10"), tmp_path / "ledger.json")

    ledger.record_failure("flood_by_location")

    saved = json.loads(ledger.path.read_text(encoding="utf-8"))
    assert ledger.cumulative_spent == Decimal("0")
    assert saved["entries"][0]["quantity"] == 0
    assert saved["entries"][0]["credits"] == "0"
    assert saved["entries"][0]["outcome"] == "failure"
    assert "zero credits recorded" in caplog.text


def test_unknown_operation_is_rejected(tmp_path):
    ledger = CreditLedger(Decimal("10"), tmp_path / "ledger.json")

    with pytest.raises(UnknownOperationError, match="Unknown credit operation"):
        ledger.check("unknown_operation", 1)


def test_success_entry_contains_only_accounting_metadata(tmp_path):
    ledger = CreditLedger(Decimal("10"), tmp_path / "ledger.json")
    ledger.record_success("geocode_basic", 2)

    entry = json.loads(ledger.path.read_text(encoding="utf-8"))["entries"][0]
    assert set(entry) == {"timestamp", "operation", "quantity", "credits", "outcome"}
    assert entry["operation"] == "geocode_basic"
    assert entry["quantity"] == 2
    assert entry["credits"] == "0.2"


def test_external_spend_persists_and_counts_toward_cap(tmp_path):
    path = tmp_path / "ledger.json"
    first = CreditLedger(Decimal("10"), path)
    first.record_external(Decimal("3.25"), "portal test call")

    second = CreditLedger(Decimal("10"), path)
    assert second.cumulative_spent == Decimal("3.25")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["entries"][0]["outcome"] == "external"
    assert saved["entries"][0]["note"] == "portal test call"

    with pytest.raises(CreditCapExceeded, match="spent 3.25.*estimate 7.*cap 10"):
        second.check("geocode_premium", 7)


@pytest.mark.parametrize(
    "credits",
    [Decimal("0"), Decimal("-1"), Decimal("NaN"), Decimal("Infinity")],
)
def test_external_spend_rejects_non_positive_or_non_finite_values(
    tmp_path, credits
):
    ledger = CreditLedger(Decimal("10"), tmp_path / "ledger.json")

    with pytest.raises(ValueError, match="positive finite"):
        ledger.record_external(credits, "portal test call")


def test_no_result_and_failure_log_wording(tmp_path, caplog):
    import logging
    from address_intelligence.credits import CreditLedger, PRICE_TABLE
    from decimal import Decimal

    operation = next(iter(PRICE_TABLE))
    ledger = CreditLedger(Decimal("100"), tmp_path / "ledger.json")
    with caplog.at_level(logging.INFO):
        ledger.record_no_result(operation)
        ledger.record_failure(operation)
    messages = [r.getMessage() for r in caplog.records]
    assert f"No result returned for {operation} (not billed)" in messages
    assert any(m.startswith("Provider operation failed; zero credits recorded") for m in messages)
