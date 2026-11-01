"""Staged, resumable address processing with request-keyed API caching."""

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import random
import time
from typing import Any, Callable
from email.utils import parsedate_to_datetime

from address_intelligence.client_profile import (
    DEFAULT_PROFILE_PATH,
    ClientProfile,
    load_client_profile,
    read_client_policies,
)
from address_intelligence.config import Settings
from address_intelligence.credits import CreditCapExceeded, CreditLedger, PRICE_TABLE
from address_intelligence.providers.precisely import PreciselyClient, ProviderResponse


PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PROFILE = load_client_profile(DEFAULT_PROFILE_PATH)
INPUT_PATH = _DEFAULT_PROFILE.input_path
OUTPUT_ROOT = _DEFAULT_PROFILE.run_dir
RESULTS_PATH = OUTPUT_ROOT / "results_rowlevel.csv"
STAGES = ("geocode", "preciselyid", "enrich", "compare")
GEOCODE_INPUT_MODES = ("structured", "single-line")
COMPARISON_SEED = _DEFAULT_PROFILE.comparison_seed
COMPARISON_SAMPLE_SIZE = _DEFAULT_PROFILE.comparison_sample_size
RETRY_DELAYS = (0.5, 1.0)
EARTH_RADIUS_KM = 6371.0088

RESULT_COLUMNS = (
    "policy_id",
    "standardized_address_line1",
    "standardized_address_line2",
    "standardized_city",
    "standardized_state",
    "standardized_zip5",
    "precision_code",
    "eligibility",
    "manual_review_reason",
    "latitude",
    "longitude",
    "preciselyid",
    "flood_zone_code",
    "flood_risk_level",
    "bfe",
    "bfe_unit",
    "distance_to_water",
    "distance_to_water_unit",
    "water_body_name",
    "built_year",
    "comparison_basic_precision_code",
    "comparison_latitude",
    "comparison_longitude",
    "comparison_flood_zone_code",
    "comparison_flood_zones_differ",
    "comparison_distance_km",
    "geocode_status",
    "geocode_provenance",
    "preciselyid_status",
    "preciselyid_provenance",
    "enrich_status",
    "enrich_provenance",
    "compare_status",
    "compare_provenance",
)


@dataclass(frozen=True)
class PlannedRequest:
    policy_id: str
    operation: str
    cache_operation: str
    params: dict[str, Any]
    invoke: Callable[[PreciselyClient], ProviderResponse]
    request_mode: str = "default"


def _read_policies(
    path: Path,
    profile: ClientProfile | None = None,
) -> list[dict[str, str]]:
    selected_profile = profile or load_client_profile()
    if path == selected_profile.input_path:
        return read_client_policies(selected_profile)
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        mapping = selected_profile.column_mapping
        missing = sorted(
            {name for name in mapping.values() if name is not None}
            - set(reader.fieldnames or ())
        )
        if missing:
            raise ValueError(
                f"Client input is missing mapped column(s): {', '.join(missing)}."
            )
        rows = []
        internal_names = {
            "policy_id": "policy_id",
            "address_line1": "raw_address_line1",
            "address_line2": "raw_address_line2",
            "city": "raw_city",
            "state": "raw_state",
            "zip5": "raw_zip5",
            "metro": "metro",
        }
        for source_row in reader:
            row = dict(source_row)
            for canonical, source_name in mapping.items():
                if source_name is None:
                    row[internal_names[canonical]] = ""
                    continue
                row[internal_names[canonical]] = source_row[source_name]
            rows.append(row)
        return rows


def _address(
    line1: str,
    line2: str,
    city: str,
    state: str,
    zip5: str,
) -> str:
    street = " ".join(part.strip() for part in (line1, line2) if part.strip())
    state_zip = " ".join(part.strip() for part in (state, zip5) if part.strip())
    if zip5.strip():
        state_zip = f"{state.strip()} {zip5.strip()}".strip()
    return ", ".join(part for part in (street, city.strip(), state_zip) if part)


def _raw_address(row: dict[str, str]) -> str:
    return _address(
        row["raw_address_line1"],
        row["raw_address_line2"],
        row["raw_city"],
        row["raw_state"],
        row["raw_zip5"],
    )


def _nested(data: Any, *path: str) -> Any:
    current = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _candidate(data: Any) -> dict[str, Any] | None:
    candidates = data.get("candidates") if isinstance(data, dict) else None
    if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
        return candidates[0]
    return None


def _coordinates(candidate: dict[str, Any] | None) -> tuple[float, float] | None:
    geometry = candidate.get("geometry") if candidate else None
    coords = geometry.get("coordinates") if isinstance(geometry, dict) else None
    try:
        if isinstance(coords, str):
            longitude, latitude = (float(part.strip()) for part in coords.split(",", 1))
            return latitude, longitude
        if isinstance(coords, list) and len(coords) >= 2:
            return float(coords[1]), float(coords[0])
    except (TypeError, ValueError):
        return None
    return None


def haversine_km(
    first: tuple[float, float] | None,
    second: tuple[float, float] | None,
) -> float | None:
    """Return great-circle distance for (latitude, longitude) points."""
    if first is None or second is None:
        return None
    lat1, lon1 = map(math.radians, first)
    lat2, lon2 = map(math.radians, second)
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    hav = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(min(1.0, hav)))


def _geocode_reason(
    response: ProviderResponse,
    expected_state: str | None = None,
    eligible_prefixes: tuple[str, ...] = ("S5", "S7", "S8"),
) -> tuple[str, str]:
    if response.status_code != 200:
        return "manual_review", "API error"
    candidate = _candidate(response.data)
    if candidate is None:
        return "manual_review", "no match"
    code = candidate.get("precisionCode")
    if isinstance(code, str) and code.startswith(eligible_prefixes):
        if expected_state is not None:
            address = candidate.get("address")
            returned_state = (
                address.get("areaName1") if isinstance(address, dict) else None
            )
            if (
                not isinstance(returned_state, str)
                or returned_state.strip().casefold()
                != expected_state.strip().casefold()
            ):
                return "manual_review", "location conflict (state mismatch)"
        return "eligible", ""
    if isinstance(code, str) and code.startswith("S4"):
        return "manual_review", "street-level only"
    if isinstance(code, str) and code.startswith(("Z", "G")):
        return "manual_review", "postal or city level"
    return "manual_review", "no match"


def _std_address(result: dict[str, str]) -> str:
    return _address(
        result["standardized_address_line1"],
        result["standardized_address_line2"],
        result["standardized_city"],
        result["standardized_state"],
        result["standardized_zip5"],
    )


def _request_hash(
    operation: str,
    params: dict[str, Any],
    request_mode: str = "default",
) -> str:
    canonical = json.dumps(
        {"operation": operation, "mode": request_mode, "params": params},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _legacy_request_hash(operation: str, params: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"operation": operation, "params": params},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class StagedPipeline:
    def __init__(
        self,
        settings: Settings,
        ledger: CreditLedger,
        *,
        client: PreciselyClient | None = None,
        input_path: Path | None = None,
        project_root: Path = PROJECT_ROOT,
        seed: int = COMPARISON_SEED,
        geocode_input_mode: str = "structured",
        profile: ClientProfile | None = None,
    ) -> None:
        if geocode_input_mode not in GEOCODE_INPUT_MODES:
            raise ValueError(
                f"geocode_input_mode must be one of {', '.join(GEOCODE_INPUT_MODES)}."
            )
        self.settings = settings
        self.ledger = ledger
        self.client = client
        self.profile = profile or _DEFAULT_PROFILE
        self.input_path = input_path or self.profile.input_path
        self.project_root = project_root
        if self.profile.project_root.resolve() == project_root.resolve():
            self.output_root = self.profile.run_dir
        else:
            relative_run_dir = self.profile.run_dir.relative_to(
                self.profile.project_root
            )
            self.output_root = project_root / relative_run_dir
        self.cache_root = self.output_root / "cache"
        self.results_path = self.output_root / "results_rowlevel.csv"
        self.seed = self.profile.comparison_seed if profile is not None else seed
        self.comparison_sample_size = self.profile.comparison_sample_size
        self.geocode_input_mode = geocode_input_mode
        self._run_provenance: dict[tuple[str, str], str] = {}
        self._run_responses: dict[tuple[str, str], ProviderResponse] = {}

    def _eligible(self, response: ProviderResponse, state: str) -> bool:
        return (
            _geocode_reason(
                response,
                state,
                self.profile.eligible_precision_prefixes,
            )[0]
            == "eligible"
        )

    def _cache_path(self, request: PlannedRequest) -> Path:
        return self.cache_root / request.cache_operation / f"{request.policy_id}.json"

    def _read_cache(self, request: PlannedRequest) -> ProviderResponse | None:
        path = self._cache_path(request)
        if not path.exists():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise ValueError(f"Unable to read response cache at {path}.") from None
        cache_hash = _request_hash(
            request.operation,
            request.params,
            request.request_mode,
        )
        legacy_single_line_hash = (
            _legacy_request_hash(request.operation, request.params)
            if request.request_mode == "single-line"
            else None
        )
        if record.get("request_hash") not in (cache_hash, legacy_single_line_hash):
            return None
        return ProviderResponse(
            status_code=record["status_code"],
            data=record.get("data"),
            error=record.get("error"),
            credits_recorded=Decimal("0"),
        )

    def _response_for(self, request: PlannedRequest) -> ProviderResponse | None:
        cached = self._read_cache(request)
        if cached is not None:
            return cached
        return self._run_responses.get(
            (request.cache_operation, request.policy_id)
        )

    def _write_cache(
        self,
        request: PlannedRequest,
        response: ProviderResponse,
    ) -> None:
        if response.status_code != 200:
            return
        path = self._cache_path(request)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "request_hash": _request_hash(
                        request.operation,
                        request.params,
                        request.request_mode,
                    ),
                    "status_code": response.status_code,
                    "data": response.data,
                    "error": response.error,
                },
                indent=2,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _retry_after_seconds(value: str | None) -> float | None:
        if not value:
            return None
        try:
            seconds = float(value)
            return max(0.0, seconds)
        except ValueError:
            try:
                target = parsedate_to_datetime(value)
                return max(
                    0.0,
                    (target - datetime.now(timezone.utc)).total_seconds(),
                )
            except (TypeError, ValueError, OverflowError):
                return None

    def _invoke(self, request: PlannedRequest) -> ProviderResponse:
        if self.client is None:
            raise RuntimeError("An API client is required for execution.")
        cached = self._read_cache(request)
        key = (request.cache_operation, request.policy_id)
        if cached is not None:
            self._run_provenance[key] = "cached"
            self._run_responses[key] = cached
            return cached

        for attempt in range(len(RETRY_DELAYS) + 1):
            response = request.invoke(self.client)
            if response.status_code == 200:
                self._write_cache(request, response)
                self._run_provenance[key] = "api"
                self._run_responses[key] = response
                return response
            transient = (
                response.status_code is None
                or response.status_code == 429
                or (
                    response.status_code is not None
                    and 500 <= response.status_code <= 599
                )
            )
            if not transient or attempt >= len(RETRY_DELAYS):
                self._run_provenance[key] = "failed"
                self._run_responses[key] = response
                return response
            wait = self._retry_after_seconds(response.retry_after)
            time.sleep(wait if wait is not None else RETRY_DELAYS[attempt])

        raise RuntimeError("Retry loop exited without a response.")

    def _geocode_request(
        self,
        row: dict[str, str],
        *,
        tier: str,
        cache_operation: str | None = None,
        input_mode: str | None = None,
    ) -> PlannedRequest:
        mode = input_mode or self.geocode_input_mode
        if mode not in GEOCODE_INPUT_MODES:
            raise ValueError(
                f"geocode input mode must be one of {', '.join(GEOCODE_INPUT_MODES)}."
            )
        if mode == "structured":
            main_address = " ".join(
                part.strip()
                for part in (row["raw_address_line1"], row["raw_address_line2"])
                if part.strip()
            )
            params = {"mainAddress": main_address}
            params.update(
                {
                    key: value
                    for key, value in (
                        ("areaName3", row["raw_city"]),
                        ("areaName1", row["raw_state"]),
                        ("postalCode", row["raw_zip5"]),
                        ("country", "USA"),
                    )
                    if value.strip()
                }
            )
            request_value: str | dict[str, str] = params
        else:
            address = _raw_address(row)
            params = {"mainAddress": address}
            request_value = address
        mode_suffix = "structured" if mode == "structured" else "single_line"
        operation_dir = (
            f"{cache_operation}_{mode_suffix}"
            if cache_operation is not None
            else (
                f"geocode_{tier}_structured"
                if mode == "structured"
                else f"geocode_{tier}"
            )
        )
        return PlannedRequest(
            row["policy_id"],
            f"geocode_{tier}",
            operation_dir,
            params,
            lambda client, value=request_value, tier=tier: client.geocode(value, tier),
            request_mode=mode,
        )

    def _preciselyid_request(
        self, row: dict[str, str], geocode_data: Any
    ) -> PlannedRequest:
        base = _row_from_geocode(row["policy_id"], geocode_data)
        if not base["standardized_address_line1"].strip():
            raise ValueError("Cannot create PreciselyID request: missing standardized street.")
        address = _std_address(base)
        return PlannedRequest(
            row["policy_id"],
            "preciselyid_by_address",
            "preciselyid_by_address",
            {"address": address, "country": "USA"},
            lambda client, address=address: client.preciselyid_by_address(address),
        )

    def _flood_request(
        self,
        row: dict[str, str],
        point: tuple[float, float],
        *,
        cache_operation: str = "flood_by_location",
    ) -> PlannedRequest:
        latitude, longitude = point
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "includeZoneDesc": "Y",
            "includeGeometry": "N",
        }
        return PlannedRequest(
            row["policy_id"],
            "flood_by_location",
            cache_operation,
            params,
            lambda client, latitude=latitude, longitude=longitude: client.flood_by_location(
                latitude, longitude
            ),
        )

    def _distance_request(
        self, row: dict[str, str], point: tuple[float, float]
    ) -> PlannedRequest:
        latitude, longitude = point
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "maxCandidates": 1,
            "waterBodyType": "all",
            "searchDistance": 20,
            "searchDistanceUnit": "miles",
        }
        return PlannedRequest(
            row["policy_id"],
            "distance_to_flood_hazard_by_location",
            "distance_to_flood_hazard_by_location",
            params,
            lambda client, latitude=latitude, longitude=longitude: client.distance_to_flood_hazard_by_location(
                latitude, longitude
            ),
        )

    def _property_request(
        self, row: dict[str, str], geocode_data: Any
    ) -> PlannedRequest:
        base = _row_from_geocode(row["policy_id"], geocode_data)
        if not base["standardized_address_line1"].strip():
            raise ValueError("Cannot create property request: missing standardized street.")
        address = _std_address(base)
        operation = next(
            item
            for item in self.profile.data["enrichment"]["operations"]
            if item["name"] == "property_attributes_v2"
        )
        attributes = ",".join(operation.get("attributes", ["builtYear"]))
        if not attributes:
            raise ValueError("property_attributes_v2 requires at least one attribute.")
        return PlannedRequest(
            row["policy_id"],
            "property_attributes_v2",
            "property_attributes_v2",
            {"address": address, "attributes": attributes},
            lambda client, address=address: client.property_attributes_by_address(
                address, attributes
            ),
        )

    def _load_geocode(self, row: dict[str, str]) -> ProviderResponse | None:
        return self._read_cache(
            self._geocode_request(row, tier="premium", input_mode=self.geocode_input_mode)
        )

    def _eligible_records(
        self, rows: list[dict[str, str]]
    ) -> list[tuple[dict[str, str], ProviderResponse]]:
        eligible = []
        for row in rows:
            response = self._load_geocode(row)
            if (
                response is not None
                and self._eligible(response, row["raw_state"])
            ):
                eligible.append((row, response))
        return eligible

    def _compare_sample(
        self,
        eligible: list[tuple[dict[str, str], ProviderResponse]],
    ) -> list[tuple[dict[str, str], ProviderResponse]]:
        grouped: dict[str, list[tuple[dict[str, str], ProviderResponse]]] = {}
        group_by_state = self.profile.column_mapping.get("metro") is None
        for item in eligible:
            group_name = item[0]["raw_state"] if group_by_state else item[0]["metro"]
            grouped.setdefault(group_name, []).append(item)
        groups = sorted(grouped)
        if not groups or not self.profile.comparison_enabled:
            return []
        target = min(self.comparison_sample_size, len(eligible))
        base, remainder = divmod(target, len(groups))
        rng = random.Random(self.seed)
        selected = []
        for index, group_name in enumerate(groups):
            records = sorted(grouped[group_name], key=lambda item: item[0]["policy_id"])
            count = min(len(records), base + (index < remainder))
            selected.extend(rng.sample(records, count))
        if len(selected) < target:
            selected_ids = {row["policy_id"] for row, _ in selected}
            remainder_records = sorted(
                (item for item in eligible if item[0]["policy_id"] not in selected_ids),
                key=lambda item: item[0]["policy_id"],
            )
            selected.extend(rng.sample(remainder_records, target - len(selected)))
        return sorted(selected, key=lambda item: item[0]["policy_id"])

    def plan(self, stage: str, *, limit: int | None = None) -> list[PlannedRequest]:
        if stage not in STAGES:
            raise ValueError(f"stage must be one of {', '.join(STAGES)}.")
        rows = _read_policies(self.input_path, self.profile)
        if limit is not None:
            if limit <= 0:
                raise ValueError("limit must be a positive integer.")
            rows = rows[:limit]
        requests: list[PlannedRequest] = []
        if stage == "geocode":
            requests = [
                self._geocode_request(row, tier="premium", input_mode=self.geocode_input_mode)
                for row in rows
            ]
        elif stage == "preciselyid":
            if self.profile.operation_enabled("preciselyid_by_address"):
                for row, geocode in self._eligible_records(rows):
                    base = _row_from_geocode(row["policy_id"], geocode.data)
                    if base["standardized_address_line1"].strip():
                        requests.append(self._preciselyid_request(row, geocode.data))
        elif stage == "enrich":
            for row, geocode in self._eligible_records(rows):
                candidate = _candidate(geocode.data)
                point = _coordinates(candidate)
                if point is None:
                    continue
                base = _row_from_geocode(row["policy_id"], geocode.data)
                property_request = (
                    self._property_request(row, geocode.data)
                    if base["standardized_address_line1"].strip()
                    else None
                )
                enrich_requests = []
                if self.profile.operation_enabled("flood_by_location"):
                    enrich_requests.append(self._flood_request(row, point))
                if self.profile.operation_enabled("distance_to_flood_hazard_by_location"):
                    enrich_requests.append(self._distance_request(row, point))
                if (
                    property_request is not None
                    and self.profile.operation_enabled("property_attributes_v2")
                ):
                    enrich_requests.append(property_request)
                requests.extend(enrich_requests)
        else:
            if (
                not self.profile.comparison_enabled
                or not self.profile.operation_enabled("flood_by_location")
            ):
                return []
            selected = self._compare_sample(self._eligible_records(rows))
            for row, _geocode in selected:
                verified = self._load_geocode(row)
                verified_candidate = _candidate(verified.data) if verified else None
                verified_point = _coordinates(verified_candidate)
                if verified_point is None:
                    continue
                requests.append(
                    self._geocode_request(
                        row,
                        tier="basic",
                        cache_operation="compare_geocode_basic",
                    )
                )
                basic_cache = self._geocode_request(
                    row,
                    tier="basic",
                    cache_operation="compare_geocode_basic",
                )
                basic = self._read_cache(basic_cache)
                basic_point = _coordinates(_candidate(basic.data)) if basic else None
                if basic_point is None:
                    # The coarse point is produced by the planned Basic request.
                    requests.append(
                        PlannedRequest(
                            row["policy_id"],
                            "flood_by_location",
                            "compare_flood_by_location",
                            {"requires": "basic_geocode_coordinates"},
                            lambda client: ProviderResponse(None, None, "Pending Basic point.", Decimal("0")),
                        )
                    )
                else:
                    requests.append(
                        self._flood_request(
                            row,
                            basic_point,
                            cache_operation="compare_flood_by_location",
                        )
                    )
        return requests

    def _uncached_requests(
        self, requests: list[PlannedRequest]
    ) -> list[PlannedRequest]:
        return [request for request in requests if self._read_cache(request) is None]

    def print_plan(
        self,
        stage: str,
        requests: list[PlannedRequest],
    ) -> Decimal:
        pending = self._uncached_requests(requests)
        by_operation: dict[str, tuple[int, Decimal]] = {}
        for request in pending:
            count, credits = by_operation.get(request.operation, (0, Decimal("0")))
            by_operation[request.operation] = (
                count + 1,
                credits + PRICE_TABLE[request.operation],
            )
        total = sum((credits for _, credits in by_operation.values()), Decimal("0"))
        print(f"Stage {stage} planned calls (uncached only):")
        if by_operation:
            for operation, (count, credits) in sorted(by_operation.items()):
                print(f"- {operation}: {count} calls, {credits} credits")
        else:
            print("- none")
        if stage != "geocode" and not requests:
            print(
                "No eligible cached geocode results are available; run geocode first "
                "to estimate this stage."
            )
        print(f"Planned calls: {len(pending)}")
        print(f"Estimated credits: {total}")
        print(
            f"Ledger: spent {self.ledger.cumulative_spent} / {self.ledger.hard_cap}; "
            f"remaining {self.ledger.remaining}"
        )
        print(
            f"Account credits left: "
            f"{self.settings.trial_credits - self.ledger.cumulative_spent}"
        )
        return total

    def run_stage(
        self,
        stage: str,
        *,
        limit: int | None = None,
    ) -> Decimal:
        if stage == "preciselyid" and not self.profile.operation_enabled(
            "preciselyid_by_address"
        ):
            return Decimal("0")
        if stage == "enrich" and not any(
            self.profile.operation_enabled(operation)
            for operation in (
                "flood_by_location",
                "distance_to_flood_hazard_by_location",
                "property_attributes_v2",
            )
        ):
            return Decimal("0")
        if stage == "compare" and (
            not self.profile.comparison_enabled
            or not self.profile.operation_enabled("flood_by_location")
        ):
            return Decimal("0")
        rows = _read_policies(self.input_path, self.profile)
        selected_rows = rows[:limit] if limit is not None else rows
        before = self.ledger.cumulative_spent
        requests = self.plan(stage, limit=limit)
        if stage != "geocode" and not requests and not self._eligible_records(selected_rows):
            raise ValueError(
                f"No eligible cached Geocode Premium results are available for {stage}; "
                "run geocode first."
            )
        if stage == "compare":
            eligible = self._eligible_records(selected_rows)
            for row, geocode in self._compare_sample(eligible):
                point = _coordinates(_candidate(geocode.data))
                if point is None or self._read_cache(self._flood_request(row, point)) is None:
                    raise ValueError(
                        "Comparison requires the verified-point flood result from enrich; "
                        "run enrich first."
                    )
        estimated = sum(
            (
                PRICE_TABLE[request.operation]
                for request in self._uncached_requests(requests)
            ),
            Decimal("0"),
        )
        if estimated > self.ledger.remaining:
            raise CreditCapExceeded(
                f"Stage budget gate refused {stage}: estimate {estimated}, "
                f"remaining {self.ledger.remaining}."
            )
        for request in requests:
            # Requests with deferred parameters are materialized after the first call.
            if request.params.get("requires") == "basic_geocode_coordinates":
                row = next(item for item in selected_rows if item["policy_id"] == request.policy_id)
                basic = self._read_cache(
                    self._geocode_request(
                        row,
                        tier="basic",
                        cache_operation="compare_geocode_basic",
                    )
                )
                point = _coordinates(_candidate(basic.data)) if basic else None
                if point is None:
                    self._run_provenance[
                        ("compare_flood_by_location", request.policy_id)
                    ] = "failed"
                    continue
                request = self._flood_request(
                    row,
                    point,
                    cache_operation="compare_flood_by_location",
                )
            response = self._invoke(request)
        self._rebuild_results(rows)
        credits = self.ledger.cumulative_spent - before
        self._print_stage_summary(stage, rows, selected_rows, credits)
        return credits

    def _rebuild_results(self, policies: list[dict[str, str]]) -> None:
        self.output_root.mkdir(parents=True, exist_ok=True)
        output_rows = []
        for policy in policies:
            result = _empty_result(policy["policy_id"])
            geocode_request = self._geocode_request(policy, tier="premium")
            geocode = self._response_for(geocode_request)
            if geocode is not None:
                _apply_geocode(result, geocode)
                eligibility, reason = _geocode_reason(
                    geocode,
                    policy["raw_state"],
                    self.profile.eligible_precision_prefixes,
                )
                result["eligibility"] = eligibility
                result["manual_review_reason"] = reason
                result["geocode_status"] = (
                    "failed" if geocode.status_code != 200 else "api"
                )
                result["geocode_provenance"] = self._run_provenance.get(
                    (geocode_request.cache_operation, policy["policy_id"]),
                    "cached",
                )
            else:
                result["geocode_status"] = ""
                result["geocode_provenance"] = ""
            eligible = result["eligibility"] == "eligible"
            if eligible and geocode is not None:
                has_standardized_street = bool(
                    result["standardized_address_line1"].strip()
                )
                if (
                    has_standardized_street
                    and self.profile.operation_enabled("preciselyid_by_address")
                ):
                    preciselyid_request = self._preciselyid_request(
                        policy, geocode.data
                    )
                    preciselyid = self._response_for(preciselyid_request)
                    if preciselyid is not None:
                        result["preciselyid"] = str(
                            _nested(preciselyid.data, "key")
                            or _nested(preciselyid.data, "preciselyId")
                            or _nested(preciselyid.data, "preciselyID")
                            or ""
                        )
                        result["preciselyid_status"] = (
                            "failed" if preciselyid.status_code != 200 else "api"
                        )
                        result["preciselyid_provenance"] = self._run_provenance.get(
                            ("preciselyid_by_address", policy["policy_id"]), "cached"
                        )
                else:
                    if self.profile.operation_enabled("preciselyid_by_address"):
                        result["preciselyid_status"] = "failed: missing standardized street"
                        result["preciselyid_provenance"] = "failed"
                    else:
                        result["preciselyid_status"] = "disabled"
                        result["preciselyid_provenance"] = "disabled"
                point = _coordinates(_candidate(geocode.data))
                if point is not None:
                    flood = None
                    distance = None
                    property_response = None
                    responses: list[ProviderResponse | None] = []
                    response_keys: list[tuple[str, str]] = []
                    if self.profile.operation_enabled("flood_by_location"):
                        flood = self._response_for(self._flood_request(policy, point))
                        responses.append(flood)
                        response_keys.append(("flood_by_location", policy["policy_id"]))
                    if self.profile.operation_enabled("distance_to_flood_hazard_by_location"):
                        distance = self._response_for(self._distance_request(policy, point))
                        responses.append(distance)
                        response_keys.append(
                            ("distance_to_flood_hazard_by_location", policy["policy_id"])
                        )
                    if (
                        has_standardized_street
                        and self.profile.operation_enabled("property_attributes_v2")
                    ):
                        property_request = self._property_request(
                            policy, geocode.data
                        )
                        property_response = self._response_for(property_request)
                        responses.append(property_response)
                        response_keys.append(("property_attributes_v2", policy["policy_id"]))
                    if flood:
                        _apply_flood(result, flood.data)
                    if distance:
                        _apply_distance(result, distance.data)
                    if property_response:
                        result["built_year"] = str(
                            _nested(property_response.data, "propertyAttributes", "builtYear")
                            or ""
                        )
                    result["enrich_status"] = (
                        _aggregate_status(tuple(responses)) if responses else "disabled"
                    )
                    if not has_standardized_street and responses:
                        result["enrich_status"] = (
                            "failed: missing standardized street"
                        )
                    result["enrich_provenance"] = (
                        _aggregate_provenance(
                            self._run_provenance,
                            tuple(response_keys),
                            tuple(responses),
                        )
                        if responses
                        else "disabled"
                    )
                    if not has_standardized_street and responses:
                        result["enrich_provenance"] = "failed"
                else:
                    any_enrichment_enabled = any(
                        self.profile.operation_enabled(operation)
                        for operation in (
                            "flood_by_location",
                            "distance_to_flood_hazard_by_location",
                            "property_attributes_v2",
                        )
                    )
                    result["enrich_status"] = (
                        "failed" if any_enrichment_enabled else "disabled"
                    )
                    result["enrich_provenance"] = result["enrich_status"]
                result["preciselyid_status"] = result["preciselyid_status"] or ""
                result["compare_status"] = ""
                result["compare_provenance"] = ""
            elif result["eligibility"] == "manual_review":
                for stage_name in ("preciselyid", "enrich", "compare"):
                    result[f"{stage_name}_status"] = "not_eligible"
                    result[f"{stage_name}_provenance"] = "not_eligible"
            output_rows.append(result)

        self._apply_comparison_outputs(policies, output_rows)
        with self.results_path.open("w", newline="", encoding="utf-8") as destination:
            writer = csv.DictWriter(
                destination,
                fieldnames=RESULT_COLUMNS,
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(output_rows)

    def _apply_comparison_outputs(
        self,
        policies: list[dict[str, str]],
        output_rows: list[dict[str, str]],
    ) -> None:
        result_by_id = {row["policy_id"]: row for row in output_rows}
        eligible = []
        for policy in policies:
            geocode = self._load_geocode(policy)
            if (
                geocode is not None
                and self._eligible(geocode, policy["raw_state"])
            ):
                eligible.append((policy, geocode))
        for policy, _ in self._compare_sample(eligible):
            result = result_by_id[policy["policy_id"]]
            verified = self._load_geocode(policy)
            verified_point = _coordinates(_candidate(verified.data)) if verified else None
            if verified_point is None:
                continue
            verified_flood = self._response_for(self._flood_request(policy, verified_point))
            basic_request = self._geocode_request(
                policy,
                tier="basic",
                cache_operation="compare_geocode_basic",
            )
            basic = self._response_for(basic_request)
            if basic is not None and basic.status_code != 200:
                result["compare_status"] = "failed"
                result["compare_provenance"] = "failed"
                continue
            if basic is None:
                continue
            basic_candidate = _candidate(basic.data)
            basic_point = _coordinates(basic_candidate)
            result["comparison_basic_precision_code"] = str(
                (basic_candidate or {}).get("precisionCode") or ""
            )
            if basic_point:
                result["comparison_latitude"] = str(basic_point[0])
                result["comparison_longitude"] = str(basic_point[1])
            comparison_flood_request = None
            comparison_flood = None
            if basic_point:
                comparison_flood_request = self._flood_request(
                    policy,
                    basic_point,
                    cache_operation="compare_flood_by_location",
                )
                comparison_flood = self._response_for(comparison_flood_request)
            if comparison_flood is not None:
                if comparison_flood.status_code != 200:
                    result["compare_status"] = "failed"
                    result["compare_provenance"] = "failed"
                    continue
                result["comparison_flood_zone_code"] = _flood_fields(
                    comparison_flood.data
                )[0]
            if verified_flood is not None and comparison_flood is not None:
                verified_code = _flood_fields(verified_flood.data)[0]
                comparison_code = _flood_fields(comparison_flood.data)[0]
                result["comparison_flood_zones_differ"] = str(
                    verified_code != comparison_code
                ).lower()
                distance = haversine_km(verified_point, basic_point)
                result["comparison_distance_km"] = (
                    f"{distance:.6f}" if distance is not None else ""
                )
                statuses = (
                    verified_flood,
                    basic,
                    comparison_flood,
                )
                result["compare_status"] = _aggregate_status(statuses)
                result["compare_provenance"] = _aggregate_provenance(
                    self._run_provenance,
                    (
                        (basic_request.cache_operation, policy["policy_id"]),
                        ("compare_flood_by_location", policy["policy_id"]),
                    ),
                    (basic, comparison_flood),
                )
            else:
                result["compare_status"] = "failed"
                result["compare_provenance"] = "failed"

    def _print_stage_summary(
        self,
        stage: str,
        all_policies: list[dict[str, str]],
        selected: list[dict[str, str]],
        credits: Decimal,
    ) -> None:
        result_rows: list[dict[str, str]] = []
        if self.results_path.exists():
            with self.results_path.open(newline="", encoding="utf-8") as source:
                result_rows = list(csv.DictReader(source))
        results = {row["policy_id"]: row for row in result_rows}
        status_column = f"{stage}_status"
        selected_ids = {row["policy_id"] for row in selected}
        status_counts: dict[str, int] = {}
        for policy_id in selected_ids:
            status = results.get(policy_id, {}).get(status_column, "") or "not_run"
            status_counts[status] = status_counts.get(status, 0) + 1
        print(f"Stage {stage} summary:")
        print("Counts by status: " + ", ".join(
            f"{status}={count}" for status, count in sorted(status_counts.items())
        ))
        eligible_count = sum(
            results.get(policy_id, {}).get("eligibility") == "eligible"
            for policy_id in selected_ids
        )
        manual_rows = [
            results[policy_id]
            for policy_id in selected_ids
            if policy_id in results
            and results[policy_id].get("eligibility") == "manual_review"
        ]
        reasons: dict[str, int] = {}
        for row in manual_rows:
            reason = row.get("manual_review_reason", "unknown")
            reasons[reason] = reasons.get(reason, 0) + 1
        print(
            f"Eligible: {eligible_count}; manual_review: {len(manual_rows)}"
            + (
                " (" + ", ".join(f"{reason}={count}" for reason, count in sorted(reasons.items())) + ")"
                if reasons else ""
            )
        )
        print(f"Credits recorded this run: {credits}")
        print(
            f"Ledger: spent {self.ledger.cumulative_spent} / {self.ledger.hard_cap}; "
            f"remaining {self.ledger.remaining}"
        )
        print(
            f"Account credits left: "
            f"{self.settings.trial_credits - self.ledger.cumulative_spent}"
        )
        print("Compare with the portal dashboard before running the next stage.")


def _row_from_geocode(policy_id: str, data: Any) -> dict[str, str]:
    result = _empty_result(policy_id)
    _apply_geocode(result, ProviderResponse(200, data, None, Decimal("0")))
    return result


def _empty_result(policy_id: str) -> dict[str, str]:
    result = {column: "" for column in RESULT_COLUMNS}
    result["policy_id"] = policy_id
    return result


def _apply_geocode(result: dict[str, str], response: ProviderResponse) -> None:
    eligibility, reason = _geocode_reason(response)
    result["eligibility"] = eligibility
    result["manual_review_reason"] = reason
    candidate = _candidate(response.data)
    if candidate is None:
        return
    result["precision_code"] = str(candidate.get("precisionCode") or "")
    address = candidate.get("address")
    line1 = (
        address.get("mainAddressLine")
        if isinstance(address, dict)
        else None
    ) or candidate.get("formattedStreetAddress")
    if line1 is not None:
        result["standardized_address_line1"] = str(line1)
    if isinstance(address, dict):
        mappings = {
            "standardized_city": ("areaName3",),
            "standardized_state": ("areaName1",),
            "standardized_zip5": ("postCode1",),
        }
        for destination, sources in mappings.items():
            value = next((address.get(source) for source in sources if address.get(source)), None)
            if value is not None:
                result[destination] = str(value)
    point = _coordinates(candidate)
    if point is not None:
        result["latitude"] = str(point[0])
        result["longitude"] = str(point[1])


def _flood_fields(data: Any) -> tuple[str, str, str, str]:
    return (
        str(_nested(data, "floodZone", "code") or ""),
        str(_nested(data, "floodZone", "riskLevel") or ""),
        str(_nested(data, "floodZone", "baseFloodElevation", "value") or ""),
        str(_nested(data, "floodZone", "baseFloodElevation", "unit") or ""),
    )


def _apply_flood(result: dict[str, str], data: Any) -> None:
    code, risk, bfe, unit = _flood_fields(data)
    result["flood_zone_code"] = code
    result["flood_risk_level"] = risk
    result["bfe"] = bfe
    result["bfe_unit"] = unit


def _apply_distance(result: dict[str, str], data: Any) -> None:
    water_body = _nested(data, "waterBody")
    if isinstance(water_body, list):
        water_body = water_body[0] if water_body else None
    distance = _nested(water_body, "distance")
    result["distance_to_water"] = str(_nested(distance, "value") or "")
    result["distance_to_water_unit"] = str(_nested(distance, "unit") or "")
    name = _nested(water_body, "name") or _nested(water_body, "waterBodyName")
    result["water_body_name"] = str(name or "")


def _aggregate_status(
    responses: tuple[ProviderResponse | None, ...],
) -> str:
    if not any(response is not None for response in responses):
        return ""
    if any(response is not None and response.status_code != 200 for response in responses):
        return "failed"
    return "api"


def _aggregate_provenance(
    provenance: dict[tuple[str, str], str],
    keys: tuple[tuple[str, str], ...],
    responses: tuple[ProviderResponse | None, ...],
) -> str:
    if any(response is not None and response.status_code != 200 for response in responses):
        return "failed"
    values = [provenance.get(key, "cached") for key, response in zip(keys, responses) if response]
    if not values:
        return ""
    if all(value == "cached" for value in values):
        return "cached"
    if any(value == "failed" for value in values):
        return "failed"
    return "api"


def print_plan_and_gate(
    pipeline: StagedPipeline,
    stage: str,
    *,
    limit: int | None = None,
) -> tuple[list[PlannedRequest], Decimal]:
    requests = pipeline.plan(stage, limit=limit)
    estimate = pipeline.print_plan(stage, requests)
    if estimate > pipeline.ledger.remaining:
        print(
            f"Budget gate: refusing stage; estimate {estimate} exceeds "
            f"remaining {pipeline.ledger.remaining}."
        )
    return requests, estimate


def run_pipeline(
    argv: list[str] | None = None,
    *,
    input_path: Path | None = None,
    project_root: Path = PROJECT_ROOT,
) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run one staged address pipeline operation")
    parser.add_argument("--stage", choices=STAGES, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--profile", type=Path, default=None)
    parser.add_argument(
        "--geocode-input",
        choices=GEOCODE_INPUT_MODES,
        default="structured",
        help="Geocode address parameters format (default: structured)",
    )
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be a positive integer")
    profile = load_client_profile(args.profile)
    settings = (
        Settings.from_env(profile_path=args.profile)
        if args.profile is not None
        else Settings.from_env()
    )
    ledger = CreditLedger(settings.credit_hard_cap)
    pipeline = StagedPipeline(
        settings,
        ledger,
        input_path=input_path,
        project_root=project_root,
        geocode_input_mode=args.geocode_input,
        profile=profile,
    )
    requests, estimate = print_plan_and_gate(pipeline, args.stage, limit=args.limit)
    if estimate > ledger.remaining:
        return 2
    if not args.execute:
        return 0
    if input("Type YES to authorize this staged Precisely run: ").strip() != "YES":
        print("Execution cancelled.")
        return 1
    live_settings = Settings.from_env(profile_path=args.profile)
    live_settings.require_api_credentials()
    pipeline.settings = live_settings
    pipeline.client = PreciselyClient(
        live_settings,
        ledger,
        min_request_interval=0.2,
    )
    try:
        pipeline.run_stage(args.stage, limit=args.limit)
    except CreditCapExceeded as exc:
        print(str(exc))
        return 2
    except ValueError as exc:
        print(str(exc))
        return 2
    return 0
