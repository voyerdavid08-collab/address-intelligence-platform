"""Precisely API client for the documented Phase 0 smoke-test operations."""

import base64
from dataclasses import dataclass
from decimal import Decimal
import math
import time
from typing import Any

import requests

from address_intelligence.config import ConfigError, Settings
from address_intelligence.credits import CreditLedger


@dataclass(frozen=True)
class ProviderResponse:
    status_code: int | None
    data: Any | None
    error: str | None
    credits_recorded: Decimal
    retry_after: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


class _ProviderFailure(Exception):
    def __init__(
        self,
        status_code: int | None,
        message: str,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class PreciselyClient:
    """Make explicitly requested API calls without retries or secret logging."""

    TIMEOUT_SECONDS = 30

    def __init__(
        self,
        settings: Settings,
        ledger: CreditLedger,
        *,
        session: requests.Session | None = None,
        min_request_interval: float = 0.0,
    ) -> None:
        self.settings = settings
        self.ledger = ledger
        self.session = session or requests.Session()
        if min_request_interval < 0:
            raise ValueError("min_request_interval cannot be negative.")
        self.min_request_interval = min_request_interval
        self._last_request_at = 0.0
        self._token: str | None = None
        self._token_expires_at = 0.0

    def _throttle(self) -> None:
        if self.min_request_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        delay = self.min_request_interval - elapsed
        if delay > 0:
            time.sleep(delay)
        self._last_request_at = time.monotonic()

    def geocode(
        self,
        address: str | dict[str, str],
        tier: str,
    ) -> ProviderResponse:
        tiers = {
            "premium": "geocode_premium",
            "advanced": "geocode_advanced",
            "basic": "geocode_basic",
        }
        if tier not in tiers:
            raise ValueError("tier must be premium, advanced, or basic.")
        params = {"mainAddress": address} if isinstance(address, str) else address
        if not isinstance(params.get("mainAddress"), str) or not params["mainAddress"].strip():
            raise ValueError("Geocode requires a non-empty mainAddress.")
        return self._get(
            tiers[tier],
            f"/geocode/v1/{tier}/geocode",
            params,
        )

    def preciselyid_by_address(self, address: str) -> ProviderResponse:
        return self._get(
            "preciselyid_by_address",
            "/geocode/v1/key/byaddress",
            {"address": address, "country": "USA"},
        )

    def flood_by_location(
        self, latitude: float, longitude: float
    ) -> ProviderResponse:
        return self._get(
            "flood_by_location",
            "/risks/v1/flood/bylocation",
            {
                "latitude": latitude,
                "longitude": longitude,
                "includeZoneDesc": "Y",
                "includeGeometry": "N",
            },
        )

    def distance_to_flood_hazard_by_location(
        self, latitude: float, longitude: float
    ) -> ProviderResponse:
        return self._get(
            "distance_to_flood_hazard_by_location",
            "/risks/v1/shoreline/distancetofloodhazard/bylocation",
            {
                "latitude": latitude,
                "longitude": longitude,
                "maxCandidates": 1,
                "waterBodyType": "all",
                "searchDistance": 20,
                "searchDistanceUnit": "miles",
            },
        )

    def validate_mailing_address_premium(self, address: str) -> ProviderResponse:
        return self._request(
            "address_verification_us",
            1,
            "POST",
            "/addressverification/v1/validatemailingaddresspremium/results.json",
            json={
                "options": {
                    "OutputCasing": "M",
                    "MaximumResults": "10",
                    "KeepMultimatch": "N",
                    "OutputScript": "InputScript",
                    "OutputAddressBlocks": "Y",
                    "OutputCountryFormat": "E",
                    "OutputFieldLevelReturnCodes": "N",
                    "OutputRecordType": "A",
                },
                "Input": {
                    "Row": [
                        {
                            "AddressLine1": address,
                            "AddressLine2": "",
                            "AddressLine3": "",
                            "AddressLine4": "",
                            "AddressLine5": "",
                            "City": "",
                            "Country": "USA",
                            "StateProvince": "",
                            "PostalCode": "",
                            "FirmName": "",
                        }
                    ]
                },
            },
        )

    def property_attributes_by_address(
        self, address: str, attributes: str
    ) -> ProviderResponse:
        if not isinstance(attributes, str):
            raise ValueError("Specify one or more named property attributes; 'all' is not allowed.")
        requested = [item.strip() for item in attributes.split(",") if item.strip()]
        if not requested or any(item.lower() == "all" for item in requested):
            raise ValueError("Specify one or more named property attributes; 'all' is not allowed.")
        return self._get(
            "property_attributes_v2",
            "/property/v2/attributes/byaddress",
            {"address": address, "attributes": ",".join(requested)},
            quantity=len(requested),
        )

    def _get(
        self,
        operation: str,
        endpoint: str,
        params: dict[str, Any],
        *,
        quantity: int = 1,
    ) -> ProviderResponse:
        return self._request(operation, quantity, "GET", endpoint, params=params)

    def _request(
        self,
        operation: str,
        quantity: int,
        method: str,
        endpoint: str,
        **kwargs: Any,
    ) -> ProviderResponse:
        self.ledger.check(operation, quantity)
        try:
            token = self._get_token()
            self._throttle()
            response = self.session.request(
                method,
                f"{self.settings.base_url.rstrip('/')}{endpoint}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.TIMEOUT_SECONDS,
                **kwargs,
            )
        except _ProviderFailure as exc:
            self.ledger.record_failure(operation)
            return ProviderResponse(
                exc.status_code,
                None,
                str(exc),
                Decimal("0"),
                exc.retry_after,
            )
        except ConfigError:
            self.ledger.record_failure(operation)
            return ProviderResponse(None, None, "Precisely credentials are not configured.", Decimal("0"))
        except requests.RequestException:
            self.ledger.record_failure(operation)
            return ProviderResponse(None, None, "Precisely request failed.", Decimal("0"))

        try:
            data = response.json()
        except ValueError:
            data = None

        if response.status_code != 200:
            self.ledger.record_failure(operation)
            return ProviderResponse(
                response.status_code,
                data,
                "Precisely returned an unsuccessful status.",
                Decimal("0"),
                response.headers.get("Retry-After"),
            )
        if not self._has_results(data):
            self.ledger.record_no_result(operation)
            return ProviderResponse(
                response.status_code, data, "Precisely returned no results.", Decimal("0")
            )

        credits = self.ledger.record_success(operation, quantity)
        return ProviderResponse(response.status_code, data, None, credits)

    def _get_token(self) -> str:
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token

        api_key, api_secret = self.settings.require_api_credentials()
        encoded = base64.b64encode(f"{api_key}:{api_secret}".encode("utf-8")).decode("ascii")
        try:
            self._throttle()
            response = self.session.post(
                f"{self.settings.base_url.rstrip('/')}/oauth/token",
                headers={
                    "Authorization": f"Basic {encoded}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"grant_type": "client_credentials"},
                timeout=self.TIMEOUT_SECONDS,
            )
        except requests.RequestException:
            raise _ProviderFailure(None, "OAuth token request failed.") from None
        if response.status_code != 200:
            raise _ProviderFailure(
                response.status_code,
                "OAuth token request was unsuccessful.",
                response.headers.get("Retry-After"),
            )
        try:
            payload = response.json()
            token = payload["access_token"]
            lifetime = float(payload["expiresIn"])
        except (ValueError, KeyError, TypeError):
            raise _ProviderFailure(response.status_code, "OAuth token response was invalid.") from None
        if not isinstance(token, str) or not token or not math.isfinite(lifetime) or lifetime <= 0:
            raise _ProviderFailure(response.status_code, "OAuth token response was invalid.")

        self._token = token
        self._token_expires_at = time.monotonic() + max(0.0, lifetime - 30.0)
        return token

    @staticmethod
    def _has_results(data: Any) -> bool:
        if not data:
            return False
        if isinstance(data, dict):
            if any(key in data for key in ("error", "errors", "errorCode", "fault")):
                return False
            for key in ("candidates", "Output", "waterBody"):
                if key in data:
                    return bool(data[key])
            return any(value not in (None, "", [], {}) for value in data.values())
        return bool(data)
