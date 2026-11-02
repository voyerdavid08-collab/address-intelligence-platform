import csv
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
import requests

from address_intelligence.credits import CreditCapExceeded
from address_intelligence.providers.precisely import ProviderResponse
from scripts import validate_streets
from scripts.validate_streets import (
    RESULT_COLUMNS,
    classify_response,
    main,
    validate_rows,
)


def _row(**overrides):
    row = {
        "street_id": "HOU-01",
        "street_name": "Westheimer Rd",
        "city": "Houston",
        "state": "TX",
        "zip5": "77098",
        "house_number_min": "2000",
        "house_number_max": "2198",
        "metro": "Houston TX",
        "selection_note": "inland",
    }
    row.update(overrides)
    return row


def _response(data=None, *, status_code=200, error=None):
    return ProviderResponse(
        status_code=status_code,
        data=data,
        error=error,
        credits_recorded=Decimal("1") if error is None else Decimal("0"),
    )


def _candidate_response(
    precision_code="S8",
    *,
    state="TX",
    post_code1="77098",
    post_code2="1234",
):
    return _response(
        {
            "candidates": [
                {
                    "precisionCode": precision_code,
                    "address": {
                        "areaName1": state,
                        "postCode1": post_code1,
                        "postCode2": post_code2,
                    },
                }
            ]
        }
    )


@pytest.mark.parametrize("precision_code", ["S5", "S7", "S8"])
def test_accepts_address_level_code_and_matching_state_zip(precision_code):
    response = _candidate_response(precision_code, post_code1="77098", post_code2="1234")

    assert classify_response(_row(), response) == ("accepted", "validated")


@pytest.mark.parametrize(
    ("response", "expected_reason"),
    [
        (_response(error="safe API error"), "API error"),
        (_response({"candidates": []}), "no candidates"),
        (
            _candidate_response("S4"),
            "street-level match only (not address-level)",
        ),
        (_candidate_response(state="LA"), "state mismatch"),
        (_candidate_response(post_code1="77099"), "ZIP mismatch"),
        (_candidate_response(post_code1=""), "ZIP mismatch"),
    ],
)
def test_rejection_reasons(response, expected_reason):
    status, reason = classify_response(_row(), response)

    assert status == "rejected"
    assert reason == expected_reason


def test_post_code1_is_used_and_post_code2_is_not_combined():
    response = _candidate_response(post_code1="77098", post_code2="9999")

    assert classify_response(_row(), response) == ("accepted", "validated")


def test_zip_mismatch_shows_returned_zip_only_in_console_not_csv(tmp_path, capsys):
    row = _row()
    client = Mock()
    client.geocode.return_value = _candidate_response(post_code1="77099")
    results, stopped = validate_rows(
        [row], client, tmp_path / "raw", validated_on="2026-10-28"
    )

    validate_streets._print_summary([row], results, stopped_for_cap=stopped)
    console = capsys.readouterr().out
    assert "HOU-01: ZIP mismatch; returned ZIP5 77099 (console only)" in console

    path = tmp_path / "reference_streets_validation.csv"
    validate_streets._write_results(path, results)
    saved = path.read_text(encoding="utf-8")
    assert "ZIP mismatch" in saved
    assert "77099" not in saved


def test_dry_run_lists_all_plans_without_http_or_credentials(
    tmp_path, monkeypatch, capsys
):
    source = tmp_path / "references.csv"
    source.write_text(
        "street_id,street_name,city,state,zip5,house_number_min,house_number_max,metro,selection_note\n"
        "HOU-01,Westheimer Rd,Houston,TX,77098,2000,2198,Houston TX,inland\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CREDIT_HARD_CAP", "2150")
    monkeypatch.setattr(
        validate_streets,
        "CreditLedger",
        lambda cap: Mock(cumulative_spent=Decimal("0"), hard_cap=Decimal(cap), remaining=Decimal(cap)),
    )

    with patch.object(requests, "Session") as session_factory:
        assert main([], input_path=source, result_path=tmp_path / "out.csv") == 0
        session_factory.assert_not_called()

    output = capsys.readouterr().out
    assert "HOU-01" in output
    assert "2099 Westheimer Rd, Houston, TX 77098" in output
    assert "Planned calls: 1" in output
    assert "Estimated credits: 1" in output
    assert "Account credits left: 2500" in output
    assert not (tmp_path / "out.csv").exists()


def test_validation_csv_contains_only_allowed_columns(tmp_path):
    row = _row()
    client = Mock()
    client.geocode.return_value = _candidate_response()
    results, stopped = validate_rows(
        [row], client, tmp_path / "raw", validated_on="2026-10-28"
    )
    path = tmp_path / "reference_streets_validation.csv"
    validate_streets._write_results(path, results)

    with path.open(newline="", encoding="utf-8") as saved:
        reader = csv.DictReader(saved)
        assert tuple(reader.fieldnames or ()) == RESULT_COLUMNS
        written = list(reader)
    assert written == [
        {
            "street_id": "HOU-01",
            "status": "accepted",
            "reason": "validated",
            "validated_on": "2026-10-28",
        }
    ]
    assert "77098" not in path.read_text(encoding="utf-8")
    assert (tmp_path / "raw" / "HOU-01.json").exists()
    assert stopped is False


def test_subset_results_merge_without_removing_other_streets(tmp_path):
    path = tmp_path / "reference_streets_validation.csv"
    path.write_text(
        "street_id,status,reason,validated_on\n"
        "BOU-02,pending revalidation,pending revalidation,2026-10-29\n"
        "BOU-05,pending revalidation,pending revalidation,2026-10-29\n",
        encoding="utf-8",
    )

    validate_streets._write_results(
        path,
        [
            validate_streets.ValidationResult(
                "BOU-02", "accepted", "validated", "2026-10-30"
            )
        ],
    )

    with path.open(newline="", encoding="utf-8") as saved:
        rows = list(csv.DictReader(saved))
    assert rows == [
        {
            "street_id": "BOU-02",
            "status": "accepted",
            "reason": "validated",
            "validated_on": "2026-10-30",
        },
        {
            "street_id": "BOU-05",
            "status": "pending revalidation",
            "reason": "pending revalidation",
            "validated_on": "2026-10-29",
        },
    ]


def test_committed_non_address_level_reason_has_no_api_code(tmp_path):
    results, _ = validate_rows(
        [_row()],
        Mock(geocode=Mock(return_value=_candidate_response("S4-PNTSC-A"))),
        tmp_path / "raw",
        validated_on="2026-10-29",
    )
    path = tmp_path / "validation.csv"

    validate_streets._write_results(path, results)

    saved = path.read_text(encoding="utf-8")
    assert "street-level match only (not address-level)" in saved
    assert "S4" not in saved
    assert "PNTSC" not in saved


def test_ids_filter_dry_run_to_requested_rows(tmp_path, monkeypatch, capsys):
    source = tmp_path / "references.csv"
    source.write_text(
        "street_id,street_name,city,state,zip5,house_number_min,house_number_max,metro,selection_note\n"
        "BOU-01,Broadway,Boulder,CO,80302,1000,1198,Boulder CO,riverine\n"
        "BOU-02,Pearl St,Boulder,CO,80302,1000,1198,Boulder CO,near creek\n"
        "BOU-05,Spruce St,Boulder,CO,80302,1000,1198,Boulder CO,inland\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        validate_streets,
        "CreditLedger",
        lambda cap: Mock(cumulative_spent=Decimal("0"), hard_cap=Decimal(cap), remaining=Decimal(cap)),
    )

    with patch.object(requests, "Session") as session_factory:
        assert main(
            ["--ids", "BOU-02, BOU-05"],
            input_path=source,
            result_path=tmp_path / "out.csv",
        ) == 0
        session_factory.assert_not_called()

    output = capsys.readouterr().out
    assert "BOU-01" not in output
    assert "BOU-02" in output and "BOU-05" in output
    assert "Planned calls: 2" in output
    assert "Estimated credits: 2" in output


def test_execution_summary_uses_full_merged_validation_file(
    tmp_path, monkeypatch, capsys
):
    source = tmp_path / "references.csv"
    rows = [
        _row(street_id=f"HOU-{index:02}", metro="Houston TX")
        for index in range(1, 6)
    ]
    rows.append(
        _row(
            street_id="BOU-99",
            street_name="Pearl St",
            city="Boulder",
            state="CO",
            zip5="80302",
            metro="Boulder CO",
        )
    )
    with source.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=rows[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    results_path = tmp_path / "validation.csv"
    results_path.write_text(
        "street_id,status,reason,validated_on\n"
        + "".join(
            f"HOU-{index:02},accepted,validated,2026-10-29\n"
            for index in range(1, 6)
        )
        + "BOU-99,pending revalidation,pending revalidation,2026-10-29\n",
        encoding="utf-8",
    )
    settings = type(
        "Settings",
        (),
        {
            "credit_hard_cap": Decimal("2150"),
            "trial_credits": Decimal("2500"),
            "api_key": "test-key",
            "api_secret": "test-secret",
            "require_api_credentials": lambda self: ("test-key", "test-secret"),
        },
    )()
    monkeypatch.setattr(
        validate_streets.Settings,
        "from_env",
        classmethod(lambda cls, **kwargs: settings),
    )
    monkeypatch.setattr(
        validate_streets,
        "CreditLedger",
        lambda cap: Mock(
            cumulative_spent=Decimal("0"),
            hard_cap=Decimal(cap),
            remaining=Decimal(cap),
        ),
    )
    client = Mock()
    client.geocode.return_value = _candidate_response(
        state="CO",
        post_code1="80302",
    )
    monkeypatch.setattr(
        validate_streets,
        "PreciselyClient",
        lambda configured, ledger: client,
    )
    monkeypatch.setattr("builtins.input", lambda prompt: "YES")

    assert main(
        ["--execute", "--ids", "BOU-99"],
        input_path=source,
        result_path=results_path,
        output_root=tmp_path / "raw",
    ) == 0

    output = capsys.readouterr().out
    assert "Houston TX: accepted 5, rejected 0, pending 0, not run 0" in output
    assert "WARNING: fewer than 5 accepted streets for Houston TX." not in output
    assert "Boulder CO: accepted 1, rejected 0, pending 0, not run 0" in output
    assert client.geocode.call_count == 1


def test_cap_exceeded_stops_remaining_validation_calls(tmp_path):
    rows = [_row(street_id=f"HOU-{index:02}", house_number_min="1", house_number_max="3") for index in range(1, 4)]
    client = Mock()
    client.geocode.side_effect = [
        _candidate_response(),
        CreditCapExceeded("cap reached"),
    ]

    results, stopped = validate_rows(rows, client, tmp_path / "raw")

    assert stopped is True
    assert client.geocode.call_count == 2
    assert [result.status for result in results] == ["accepted", "rejected"]
    assert results[-1].reason == "credit cap exceeded"
    assert not (tmp_path / "raw" / "HOU-02.json").exists()


def test_geocoder_client_uses_premium_and_midpoint(tmp_path):
    client = Mock()
    response = _response({"candidates": []})
    client.geocode.return_value = response

    validate_rows([_row()], client, tmp_path / "raw", validated_on="2026-10-28")

    client.geocode.assert_called_once_with(
        "2099 Westheimer Rd, Houston, TX 77098", "premium"
    )
