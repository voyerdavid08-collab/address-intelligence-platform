import csv
from decimal import Decimal
from unittest.mock import Mock, patch

import requests

from address_intelligence.credits import CreditLedger
from address_intelligence.providers.precisely import ProviderResponse
from scripts import av_capability_check as av

HEADER = "record_id,variant,firm_name,address_line1,address_line2,city,state,postal_code\n"
ROW = "r1,clean,Acme,1 Main St,Ste 2,Boulder,CO,80301\n"
AV_DATA = {
    "Output": [
        {
            "Status": "F",
            "Status.Code": "C",
            "Confidence": "90",
            "DPV": "Y",
            "AddressLine1": "1 MAIN ST",
            "City": "BOULDER",
        }
    ]
}


def _setup(tmp_path, monkeypatch, rows=ROW):
    monkeypatch.setenv("CREDIT_HARD_CAP", "100")
    csv_path = tmp_path / "in.csv"
    csv_path.write_text(HEADER + rows, encoding="utf-8")
    ledger = CreditLedger(Decimal("100"), tmp_path / "ledger.json")
    return csv_path, ledger, tmp_path / "out"


def _client(calls):
    client = Mock()

    def validate(product, **kwargs):
        calls.append((product, kwargs))
        return ProviderResponse(200, AV_DATA, None, Decimal("1"))

    client.validate_mailing_address_structured.side_effect = validate
    client.geocode.return_value = ProviderResponse(
        200,
        {"candidates": [{"precisionCode": "S8", "customFields": {"PB_KEY": "P0001"}}]},
        None,
        Decimal("1"),
    )
    return client


def test_dry_run_makes_no_network_calls(tmp_path, monkeypatch, capsys):
    csv_path, ledger, out = _setup(tmp_path, monkeypatch)
    with patch.object(requests, "Session") as session_factory, patch(
        "builtins.input"
    ) as ask:
        assert av.main(["--input", str(csv_path), "--geocode"], ledger=ledger, output_dir=out) == 0
        session_factory.assert_not_called()
        ask.assert_not_called()
    text = capsys.readouterr().out
    assert "address_verification_uscan" in text
    assert "Planned credit estimate: 2.33" in text
    assert "remaining 100" in text
    assert not out.exists()


def test_non_yes_answer_stops_execution(tmp_path, monkeypatch):
    csv_path, ledger, out = _setup(tmp_path, monkeypatch)
    factory = Mock()
    with patch("builtins.input", return_value="yes"):
        code = av.main(
            ["--input", str(csv_path), "--execute"],
            ledger=ledger,
            output_dir=out,
            client_factory=factory,
        )
    assert code == 1
    factory.assert_not_called()
    assert not out.exists()


def test_execute_caches_and_never_repeats_calls(tmp_path, monkeypatch):
    csv_path, ledger, out = _setup(tmp_path, monkeypatch)
    calls = []
    client = _client(calls)
    argv = ["--input", str(csv_path), "--execute", "--geocode"]
    with patch("builtins.input", return_value="YES"), patch.object(
        av.Settings, "from_env", return_value=Mock(credit_hard_cap=Decimal("100"))
    ):
        for _ in range(2):
            assert av.main(argv, ledger=ledger, output_dir=out, client_factory=lambda s, l: client) == 0
    assert len(calls) == 2
    assert client.geocode.call_count == 1
    assert len(list((out / "cache").glob("*.json"))) == 3

    with open(out / "results.csv", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [r["product"] for r in rows] == ["premium", "uscan"]
    assert rows[0]["DPV"] == "Y"
    assert rows[0]["std_address_line1"] == "1 MAIN ST"
    assert rows[0]["CMRA"] == ""
    assert rows[0]["geocode_precision_code"] == "S8"
    assert rows[0]["PB_KEY"] == "P0001"


def test_fields_are_mapped_separately(tmp_path, monkeypatch):
    csv_path, ledger, out = _setup(tmp_path, monkeypatch)
    calls = []
    with patch("builtins.input", return_value="YES"), patch.object(
        av.Settings, "from_env", return_value=Mock(credit_hard_cap=Decimal("100"))
    ):
        av.main(
            ["--input", str(csv_path), "--execute", "--products", "uscan", "--limit", "1"],
            ledger=ledger,
            output_dir=out,
            client_factory=lambda s, l: _client(calls),
        )
    assert calls == [
        (
            "uscan",
            {
                "address_line1": "1 Main St",
                "address_line2": "Ste 2",
                "city": "Boulder",
                "state_province": "CO",
                "postal_code": "80301",
                "firm_name": "Acme",
            },
        )
    ]


def test_geocode_uses_separate_structured_parameters():
    row = {
        "record_id": "r1",
        "variant": "clean",
        "firm_name": "Acme",
        "address_line1": "1 Main St",
        "address_line2": "Ste 2",
        "city": "Boulder",
        "state": "CO",
        "postal_code": "80301",
    }
    call = av.build_plan([row], [], geocode=True)[0]
    client = Mock()

    av._send(client, call)

    client.geocode.assert_called_once_with(
        {
            "mainAddress": "1 Main St Ste 2",
            "areaName3": "Boulder",
            "areaName1": "CO",
            "postalCode": "80301",
            "country": "USA",
        },
        "premium",
    )


def test_ledger_cap_stops_before_call(tmp_path, monkeypatch):
    csv_path, _, out = _setup(tmp_path, monkeypatch)
    ledger = CreditLedger(Decimal("0.5"), tmp_path / "ledger.json")
    calls = []
    with patch("builtins.input", return_value="YES"), patch.object(
        av.Settings, "from_env", return_value=Mock(credit_hard_cap=Decimal("0.5"))
    ):
        code = av.main(
            ["--input", str(csv_path), "--execute", "--products", "premium"],
            ledger=ledger,
            output_dir=out,
            client_factory=lambda s, l: _client(calls),
        )
    assert code == 1
    assert calls == []
