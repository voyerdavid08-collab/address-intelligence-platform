import csv
import json
from decimal import Decimal
from unittest.mock import Mock, patch

import requests

from address_intelligence.credits import CreditLedger
from address_intelligence.providers.precisely import ProviderResponse
from scripts import zip_flood_comparison as comparison


def _fixtures(tmp_path):
    results_path = tmp_path / "results_rowlevel.csv"
    input_path = tmp_path / "input.csv"
    verified_cache = tmp_path / "verified_flood"
    with results_path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(
            destination,
            fieldnames=(
                "policy_id",
                "eligibility",
                "standardized_zip5",
                "precision_code",
                "flood_zone_code",
            ),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(
            (
                {
                    "policy_id": "REC-1",
                    "eligibility": "eligible",
                    "standardized_zip5": "12345-0001",
                    "precision_code": "S8",
                    "flood_zone_code": "A",
                },
                {
                    "policy_id": "REC-2",
                    "eligibility": "eligible",
                    "standardized_zip5": "12345",
                    "precision_code": "S5",
                    "flood_zone_code": "X",
                },
                {
                    "policy_id": "REC-3",
                    "eligibility": "eligible",
                    "standardized_zip5": "99999",
                    "precision_code": "S8",
                    "flood_zone_code": "V",
                },
                {
                    "policy_id": "INELIGIBLE",
                    "eligibility": "manual_review",
                    "standardized_zip5": "12345",
                    "precision_code": "G3",
                    "flood_zone_code": "A",
                },
            )
        )
    with input_path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(
            destination, fieldnames=("policy_id", "metro"), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(
            (
                {"policy_id": "REC-1", "metro": "Metro Alpha"},
                {"policy_id": "REC-2", "metro": "Metro Alpha"},
                {"policy_id": "REC-3", "metro": "Metro Beta"},
                {"policy_id": "INELIGIBLE", "metro": "Metro Alpha"},
            )
        )
    for policy_id, code in (("REC-1", "A"), ("REC-2", "X"), ("REC-3", "V")):
        path = verified_cache / f"{policy_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"status_code": 200, "data": {"floodZone": {"code": code}}}
            ),
            encoding="utf-8",
        )
    return results_path, verified_cache, input_path


def _run_args(paths, output_dir, ledger, client_factory, extra=()):
    results_path, verified_cache, input_path = paths
    return comparison.main(
        list(extra),
        results_path=results_path,
        verified_flood_cache=verified_cache,
        input_path=input_path,
        output_dir=output_dir,
        ledger=ledger,
        client_factory=client_factory,
    )


def _client():
    client = Mock()
    client.geocode.side_effect = [
        ProviderResponse(
            200,
            {
                "candidates": [
                    {
                        "precisionCode": "Z1",
                        "geometry": {"coordinates": [-95.0, 29.0]},
                    }
                ]
            },
            None,
            Decimal("0"),
        ),
        ProviderResponse(
            200,
            {
                "candidates": [
                    {
                        "precisionCode": "G3",
                        "geometry": {"coordinates": [-96.0, 30.0]},
                    }
                ]
            },
            None,
            Decimal("0"),
        ),
    ]
    client.flood_by_location.return_value = ProviderResponse(
        200, {"floodZone": {"code": "X"}}, None, Decimal("0")
    )
    return client


def _ledger(tmp_path):
    return CreditLedger(Decimal("100"), tmp_path / "ledger.json")


def test_dry_run_has_no_network_calls_and_plans_one_pair_per_zip(
    tmp_path, monkeypatch, capsys
):
    paths = _fixtures(tmp_path)
    output_dir = tmp_path / "out"
    ledger = _ledger(tmp_path)
    factory = Mock()
    monkeypatch.setattr(
        comparison.Settings,
        "from_env",
        Mock(return_value=Mock(credit_hard_cap=Decimal("100"))),
    )
    with patch.object(requests, "Session") as session_factory:
        assert _run_args(paths, output_dir, ledger, factory) == 0
    session_factory.assert_not_called()
    factory.assert_not_called()
    output = capsys.readouterr().out
    assert "Unique ZIPs: 2" in output
    assert "Records covered: 3" in output
    assert "Planned calls: up to 4 (2 Geocode Basic, up to 2 flood)" in output
    assert "Planned credit estimate: 10.2" in output
    assert "spent 0 / 100; remaining 100" in output
    assert not output_dir.exists()


def test_execute_calls_once_per_zip_and_writes_aggregate_only_summary(
    tmp_path, monkeypatch
):
    paths = _fixtures(tmp_path)
    output_dir = tmp_path / "out"
    ledger = _ledger(tmp_path)
    client = _client()
    monkeypatch.setattr(
        comparison.Settings,
        "from_env",
        Mock(return_value=Mock(credit_hard_cap=Decimal("100"))),
    )
    with patch("builtins.input", return_value="YES"):
        assert _run_args(
            paths, output_dir, ledger, lambda settings, credits: client, ("--execute",)
        ) == 0

    assert client.geocode.call_count == 2
    client.geocode.assert_any_call({"mainAddress": "12345", "country": "USA"}, "basic")
    client.geocode.assert_any_call({"mainAddress": "99999", "country": "USA"}, "basic")
    client.flood_by_location.assert_called_once_with(29.0, -95.0)
    with (output_dir / "results.csv").open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    assert len(rows) == 3
    assert rows[0]["comparison"] == "understated"
    assert rows[1]["comparison"] == ""
    assert rows[2]["status"] == "no_zip_point"

    summary = (output_dir / "summary.md").read_text(encoding="utf-8")
    assert "- Records compared: 2" in summary
    assert "- Tier changed: 1" in summary
    assert "- Understated: 1" in summary
    assert "- Overstated: 0" in summary
    assert "- ZIPs with no ZIP-level point: 1" in summary
    assert "| S5 | 1 | 0 | 0 | 0 |" in summary
    assert "| S8 | 1 | 1 | 1 | 0 |" in summary
    assert "| Metro Alpha | 2 | 1 | 1 | 0 |" in summary
    assert "REC-1" not in summary
    assert "REC-2" not in summary
    assert "12345" not in summary
    assert "99999" not in summary


def test_cached_geocode_and_flood_requests_are_not_repeated(tmp_path, monkeypatch):
    paths = _fixtures(tmp_path)
    output_dir = tmp_path / "out"
    ledger = _ledger(tmp_path)
    client = _client()
    monkeypatch.setattr(
        comparison.Settings,
        "from_env",
        Mock(return_value=Mock(credit_hard_cap=Decimal("100"))),
    )
    with patch("builtins.input", return_value="YES"):
        for _ in range(2):
            assert _run_args(
                paths, output_dir, ledger, lambda settings, credits: client, ("--execute",)
            ) == 0
    assert client.geocode.call_count == 2
    client.flood_by_location.assert_called_once_with(29.0, -95.0)
    assert len(list((output_dir / "cache").glob("*.json"))) == 3


def test_non_200_geocode_is_not_cached_and_retried(tmp_path, monkeypatch):
    paths = _fixtures(tmp_path)
    output_dir = tmp_path / "out"
    client = Mock()
    client.geocode.return_value = ProviderResponse(
        500, {"error": "temporary"}, "failed", Decimal("0")
    )
    monkeypatch.setattr(
        comparison.Settings,
        "from_env",
        Mock(return_value=Mock(credit_hard_cap=Decimal("100"))),
    )
    with patch("builtins.input", return_value="YES"):
        for _ in range(2):
            assert _run_args(
                paths,
                output_dir,
                _ledger(tmp_path),
                lambda settings, credits: client,
                ("--execute", "--limit-zips", "1"),
            ) == 0

    assert client.geocode.call_count == 2
    assert not comparison._geocode_cache_path(output_dir / "cache", "12345").exists()
    with (output_dir / "results.csv").open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    assert all(row["status"] == "no_zip_point" for row in rows)


def test_non_200_flood_response_is_not_cached_and_retried(tmp_path, monkeypatch):
    paths = _fixtures(tmp_path)
    output_dir = tmp_path / "out"
    client = Mock()
    client.geocode.return_value = ProviderResponse(
        200,
        {
            "candidates": [
                {
                    "precisionCode": "Z1",
                    "geometry": {"coordinates": [-95.0, 29.0]},
                }
            ]
        },
        None,
        Decimal("0"),
    )
    client.flood_by_location.return_value = ProviderResponse(
        500, {"error": "temporary"}, "failed", Decimal("0")
    )
    monkeypatch.setattr(
        comparison.Settings,
        "from_env",
        Mock(return_value=Mock(credit_hard_cap=Decimal("100"))),
    )
    with patch("builtins.input", return_value="YES"):
        for _ in range(2):
            assert _run_args(
                paths,
                output_dir,
                _ledger(tmp_path),
                lambda settings, credits: client,
                ("--execute", "--limit-zips", "1"),
            ) == 0

    client.geocode.assert_called_once()
    assert client.flood_by_location.call_count == 2
    assert not comparison._flood_cache_path(
        output_dir / "cache", "12345", (29.0, -95.0)
    ).exists()
    with (output_dir / "results.csv").open(newline="", encoding="utf-8") as source:
        rows = list(csv.DictReader(source))
    assert all(row["status"] == "zip_flood_unavailable" for row in rows)


def test_limit_zips_selects_one_group(tmp_path, monkeypatch, capsys):
    paths = _fixtures(tmp_path)
    monkeypatch.setattr(
        comparison.Settings,
        "from_env",
        Mock(return_value=Mock(credit_hard_cap=Decimal("100"))),
    )
    assert _run_args(
        paths,
        tmp_path / "out",
        _ledger(tmp_path),
        Mock(),
        ("--limit-zips", "1"),
    ) == 0
    output = capsys.readouterr().out
    assert "Unique ZIPs: 1" in output
    assert "Records covered: 2" in output
