import base64
from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
import requests

from address_intelligence.config import Settings
from address_intelligence.credits import CreditCapExceeded, CreditLedger
from address_intelligence.providers import precisely
from address_intelligence.providers.precisely import PreciselyClient


def _settings() -> Settings:
    return Settings(
        api_key="test-key",
        api_secret="test-secret",
        base_url="https://api.precisely.com",
        credit_hard_cap=Decimal("100"),
        trial_credits=Decimal("2500"),
        log_level="INFO",
    )


def _response(status_code, payload):
    response = Mock()
    response.status_code = status_code
    response.json.return_value = payload
    return response


def _client(tmp_path, session=None, ledger=None):
    return PreciselyClient(
        _settings(),
        ledger or CreditLedger(Decimal("100"), tmp_path / "ledger.json"),
        session=session or Mock(),
    )


def test_token_request_uses_documented_oauth_format(tmp_path):
    session = Mock()
    session.post.return_value = _response(
        200, {"access_token": "private-token", "expiresIn": "35999"}
    )
    session.request.return_value = _response(
        200, {"candidates": [{"precisionCode": "S8"}]}
    )

    client = _client(tmp_path, session=session)
    result = client.geocode("sample address", "premium")

    basic = base64.b64encode(b"test-key:test-secret").decode("ascii")
    session.post.assert_called_once_with(
        "https://api.precisely.com/oauth/token",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    api_call = session.request.call_args
    assert api_call.args == ("GET", "https://api.precisely.com/geocode/v1/premium/geocode")
    assert api_call.kwargs["headers"] == {"Authorization": "Bearer private-token"}
    assert api_call.kwargs["params"] == {"mainAddress": "sample address"}
    assert api_call.kwargs["timeout"] == 30
    assert result.succeeded


def test_tokens_are_cached_until_near_expiry(tmp_path):
    session = Mock()
    session.post.return_value = _response(
        200, {"access_token": "private-token", "expiresIn": "35999"}
    )
    session.request.return_value = _response(200, {"key": "id"})
    client = _client(tmp_path, session=session)

    client.preciselyid_by_address("sample address")
    client.preciselyid_by_address("sample address")

    session.post.assert_called_once()
    assert session.request.call_count == 2


def test_expired_token_is_refreshed(tmp_path):
    session = Mock()
    session.post.side_effect = [
        _response(200, {"access_token": "first-token", "expiresIn": "35999"}),
        _response(200, {"access_token": "second-token", "expiresIn": "35999"}),
    ]
    session.request.return_value = _response(200, {"key": "id"})
    client = _client(tmp_path, session=session)

    with patch.object(precisely.time, "monotonic", side_effect=[0, 36000, 36000]):
        client.preciselyid_by_address("sample address")
        client.preciselyid_by_address("sample address")

    assert session.post.call_count == 2
    assert session.request.call_args_list[0].kwargs["headers"]["Authorization"] == (
        "Bearer first-token"
    )
    assert session.request.call_args_list[1].kwargs["headers"]["Authorization"] == (
        "Bearer second-token"
    )


def test_secrets_and_token_are_absent_from_logs_and_error_text(tmp_path, caplog):
    session = Mock()
    session.post.return_value = _response(
        200, {"access_token": "private-token", "expiresIn": "35999"}
    )
    session.request.return_value = _response(401, {"message": "unauthorized"})
    client = _client(tmp_path, session=session)

    result = client.geocode("sample address", "premium")

    combined = caplog.text + str(result.error)
    for secret in ("test-key", "test-secret", "private-token", "Basic "):
        assert secret not in combined
    assert result.status_code == 401
    assert result.error == "Precisely returned an unsuccessful status."


def test_ledger_check_happens_before_any_request(tmp_path):
    session = Mock()
    ledger = Mock()
    ledger.check.return_value = Decimal("1")
    session.post.return_value = _response(
        200, {"access_token": "private-token", "expiresIn": "35999"}
    )
    session.request.return_value = _response(200, {"key": "id"})

    client = _client(tmp_path, session=session, ledger=ledger)
    client.preciselyid_by_address("sample address")

    ledger.check.assert_called_once_with("preciselyid_by_address", 1)
    assert session.post.call_count == 1
    assert session.request.call_count == 1


def test_credit_cap_prevents_token_and_api_requests(tmp_path):
    session = Mock()
    ledger = Mock()
    ledger.check.side_effect = CreditCapExceeded("cap reached")
    client = _client(tmp_path, session=session, ledger=ledger)

    with pytest.raises(CreditCapExceeded, match="cap reached"):
        client.geocode("sample address", "premium")

    session.post.assert_not_called()
    session.request.assert_not_called()


def test_non_200_records_zero_cost(tmp_path):
    ledger = Mock()
    ledger.check.return_value = Decimal("1")
    ledger.record_failure.return_value = None
    session = Mock()
    session.post.return_value = _response(
        200, {"access_token": "private-token", "expiresIn": "35999"}
    )
    session.request.return_value = _response(503, {"message": "unavailable"})

    response = _client(tmp_path, session=session, ledger=ledger).geocode(
        "sample address", "premium"
    )

    assert response.status_code == 503
    assert response.error
    assert response.credits_recorded == Decimal("0")
    ledger.record_failure.assert_called_once_with("geocode_premium")
    ledger.record_success.assert_not_called()


def test_empty_or_error_results_are_not_charged(tmp_path):
    session = Mock()
    session.post.return_value = _response(
        200, {"access_token": "private-token", "expiresIn": "35999"}
    )
    session.request.return_value = _response(200, {"candidates": []})
    ledger = Mock()
    ledger.check.return_value = Decimal("1")

    response = _client(tmp_path, session=session, ledger=ledger).geocode(
        "sample address", "premium"
    )

    assert response.error == "Precisely returned no results."
    assert response.credits_recorded == Decimal("0")
    ledger.record_failure.assert_called_once_with("geocode_premium")
    ledger.record_success.assert_not_called()


@pytest.mark.parametrize(
    ("method_name", "expected_url", "expected_params"),
    [
        (
            "flood_by_location",
            "https://api.precisely.com/risks/v1/flood/bylocation",
            {
                "latitude": 40.0,
                "longitude": -105.0,
                "includeZoneDesc": "Y",
                "includeGeometry": "N",
            },
        ),
        (
            "distance_to_flood_hazard_by_location",
            "https://api.precisely.com/risks/v1/shoreline/distancetofloodhazard/bylocation",
            {
                "latitude": 40.0,
                "longitude": -105.0,
                "maxCandidates": 1,
                "waterBodyType": "all",
                "searchDistance": 20,
                "searchDistanceUnit": "miles",
            },
        ),
        (
            "preciselyid_by_address",
            "https://api.precisely.com/geocode/v1/key/byaddress",
            {"address": "sample address", "country": "USA"},
        ),
    ],
)
def test_documented_get_operation_formats(
    tmp_path, method_name, expected_url, expected_params
):
    session = Mock()
    session.post.return_value = _response(
        200, {"access_token": "private-token", "expiresIn": "35999"}
    )
    session.request.return_value = _response(
        200, {"floodZone": {"code": "X"}, "key": "id", "waterBody": [{"name": "x"}]}
    )
    client = _client(tmp_path, session=session)

    if method_name == "flood_by_location":
        getattr(client, method_name)(40.0, -105.0)
    elif method_name == "distance_to_flood_hazard_by_location":
        getattr(client, method_name)(40.0, -105.0)
    else:
        getattr(client, method_name)("sample address")

    api_call = session.request.call_args
    assert api_call.args[1] == expected_url
    assert api_call.kwargs["params"] == expected_params


@pytest.mark.parametrize("attributes", ["", " ", "all", "ALL", "builtYear,all"])
def test_property_call_rejects_empty_or_all_attributes(tmp_path, attributes):
    session = Mock()
    ledger = Mock()
    client = _client(tmp_path, session=session, ledger=ledger)

    with pytest.raises(ValueError, match="named property attributes"):
        client.property_attributes_by_address("sample address", attributes)

    session.post.assert_not_called()
    session.request.assert_not_called()
    ledger.check.assert_not_called()


def test_property_call_uses_v2_endpoint_and_requested_fields(tmp_path):
    session = Mock()
    session.post.return_value = _response(
        200, {"access_token": "private-token", "expiresIn": "35999"}
    )
    session.request.return_value = _response(200, {"builtYear": 1980})

    response = _client(tmp_path, session=session).property_attributes_by_address(
        "sample address", " builtYear "
    )

    api_call = session.request.call_args
    assert api_call.args == (
        "GET",
        "https://api.precisely.com/property/v2/attributes/byaddress",
    )
    assert api_call.kwargs["params"] == {
        "address": "sample address",
        "attributes": "builtYear",
    }
    assert response.credits_recorded == Decimal("1")


def test_address_verification_uses_documented_post_body(tmp_path):
    session = Mock()
    session.post.return_value = _response(
        200, {"access_token": "private-token", "expiresIn": "35999"}
    )
    session.request.return_value = _response(200, {"Output": [{"AddressLine1": "x"}]})

    _client(tmp_path, session=session).validate_mailing_address_premium(
        "4750 Walnut Stret, Bolder, CO"
    )

    call = session.request.call_args
    assert call.args == (
        "POST",
        "https://api.precisely.com/addressverification/v1/validatemailingaddresspremium/results.json",
    )
    row = call.kwargs["json"]["Input"]["Row"][0]
    assert row["AddressLine1"] == "4750 Walnut Stret, Bolder, CO"
    assert row["Country"] == "USA"


def test_network_exceptions_return_safe_structured_error(tmp_path):
    session = Mock()
    session.post.side_effect = requests.Timeout("private-token test-secret")
    ledger = Mock()
    ledger.check.return_value = Decimal("1")
    response = _client(tmp_path, session=session, ledger=ledger).geocode(
        "sample address", "basic"
    )

    assert response.status_code is None
    assert response.error == "OAuth token request failed."
    assert "private-token" not in response.error
    assert "test-secret" not in response.error
    ledger.record_failure.assert_called_once_with("geocode_basic")
