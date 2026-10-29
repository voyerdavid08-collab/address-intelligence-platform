"""Dry-run-first tooling for the owner-operated Precisely smoke test."""

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
from typing import Any, Callable

from address_intelligence.config import Settings
from address_intelligence.credits import CreditLedger
from address_intelligence.providers.precisely import PreciselyClient, ProviderResponse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ADDRESSES = {
    "A1 clean": "4750 Walnut St, Boulder, CO 80301",
    "A2 defective": "4750 Walnut Stret, Bolder, CO",
    "A3 incomplete": "Walnut St, Boulder, CO 80301",
}


@dataclass(frozen=True)
class PlannedCall:
    label: str
    operation: str
    credits: Decimal


@dataclass
class CallResult:
    label: str
    operation: str
    response: ProviderResponse | None
    error: str | None = None


def _plan(stage: str) -> list[PlannedCall]:
    if stage == "b":
        return [PlannedCall("A1 clean", "property_attributes_v2", Decimal("1"))]
    calls = [
        PlannedCall(f"{tier} / {label}", f"geocode_{tier}", cost)
        for tier, cost in (
            ("premium", Decimal("1")),
            ("basic", Decimal("0.1")),
            ("advanced", Decimal("0.5")),
        )
        for label in ADDRESSES
    ]
    calls.extend(
        [
            PlannedCall("A1 clean", "preciselyid_by_address", Decimal("0.01")),
            PlannedCall("A1 clean coordinates", "flood_by_location", Decimal("5")),
            PlannedCall(
                "A1 clean coordinates",
                "distance_to_flood_hazard_by_location",
                Decimal("2"),
            ),
            PlannedCall("A2 defective", "address_verification_us", Decimal("1")),
        ]
    )
    return calls


def _print_plan(stage: str, plan: list[PlannedCall], ledger: CreditLedger) -> None:
    print(f"Stage {stage} planned calls:")
    for call in plan:
        print(
            f"- {call.label}: {call.operation} "
            f"(estimated {call.credits} credits)"
        )
    total = sum((call.credits for call in plan), Decimal("0"))
    print(f"Planned credit estimate: {total}")
    print(
        f"Ledger: spent {ledger.cumulative_spent} / {ledger.hard_cap}; "
        f"remaining {ledger.remaining}"
    )


def _coordinates(response: ProviderResponse | None) -> tuple[float, float] | None:
    if response is None or not isinstance(response.data, dict):
        return None
    candidates = response.data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None
    first = candidates[0]
    if not isinstance(first, dict):
        return None
    geometry = first.get("geometry")
    if not isinstance(geometry, dict):
        return None
    coordinates = geometry.get("coordinates")
    if isinstance(coordinates, str):
        pieces = coordinates.split(",")
        if len(pieces) == 2:
            try:
                return float(pieces[1]), float(pieces[0])
            except ValueError:
                return None
    if isinstance(coordinates, list) and len(coordinates) >= 2:
        try:
            return float(coordinates[1]), float(coordinates[0])
        except (TypeError, ValueError):
            return None
    return None


def _nested(data: Any, *path: str) -> Any:
    current = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _save_response(directory: Path, filename: str, result: CallResult) -> None:
    payload: Any
    if result.response is not None and result.response.data is not None:
        payload = result.response.data
    else:
        payload = {
            "status_code": result.response.status_code if result.response else None,
            "error": result.error or (result.response.error if result.response else None),
        }
    (directory / f"{filename}.json").write_text(
        json.dumps(payload, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def _print_summary(results: list[CallResult]) -> Decimal:
    headers = (
        "label",
        "operation",
        "HTTP status",
        "precisionCode",
        "precisionLevel",
        "lat/lon",
        "PreciselyID",
        "flood zone / risk / BFE",
        "distance result",
        "builtYear",
        "credits recorded",
    )
    rows: list[list[str]] = []
    credits_total = Decimal("0")
    for item in results:
        response = item.response
        data = response.data if response else None
        candidate = None
        if isinstance(data, dict):
            candidates = data.get("candidates")
            if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
                candidate = candidates[0]
        geometry = candidate.get("geometry", {}) if candidate else {}
        coords = geometry.get("coordinates") if isinstance(geometry, dict) else None
        lat_lon = "-"
        if isinstance(coords, str):
            parts = coords.split(",")
            if len(parts) == 2:
                lat_lon = f"{parts[1]}/{parts[0]}"
        elif isinstance(coords, list) and len(coords) >= 2:
            lat_lon = f"{coords[1]}/{coords[0]}"
        flood = _nested(data, "floodZone")
        flood_text = "—"
        if isinstance(flood, dict):
            flood_text = " / ".join(
                str(value) if value is not None else "-"
                for value in (
                    flood.get("code"),
                    flood.get("riskLevel"),
                    _nested(flood, "baseFloodElevation", "value"),
                )
            )
        water_body = data.get("waterBody") if isinstance(data, dict) else None
        if isinstance(water_body, list) and water_body:
            distance = _nested(water_body[0], "distance", "value")
        else:
            distance = _nested(data, "waterBody", "distance", "value")
        if distance is None and isinstance(data, dict):
            distance = data.get("distance")
        built_year = _nested(data, "propertyAttributes", "builtYear")
        recorded = response.credits_recorded if response else Decimal("0")
        credits_total += recorded
        rows.append(
            [
                item.label,
                item.operation,
                str(response.status_code) if response and response.status_code else item.error or "-",
                str(candidate.get("precisionCode", "-")) if candidate else "-",
                str(candidate.get("precisionLevel", "-")) if candidate else "-",
                lat_lon,
                str(data.get("key", "-")) if isinstance(data, dict) else "-",
                flood_text,
                str(distance) if distance is not None else "-",
                str(built_year) if built_year is not None else "-",
                str(recorded),
            ]
        )
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        for index in range(len(headers))
    ]
    print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(headers)))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(row)))
    return credits_total


def _execute_stage(
    stage: str,
    client: PreciselyClient,
    output_dir: Path,
) -> list[CallResult]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[CallResult] = []
    premium_a1: ProviderResponse | None = None

    def run(label: str, operation: str, filename: str, call: Callable[[], ProviderResponse]) -> ProviderResponse:
        result = call()
        results.append(CallResult(label, operation, result))
        _save_response(output_dir, filename, results[-1])
        return result

    if stage == "b":
        run(
            "A1 clean",
            "property_attributes_v2",
            "property_attributes_v2_A1",
            lambda: client.property_attributes_by_address(ADDRESSES["A1 clean"], "builtYear"),
        )
        return results

    for tier in ("premium", "basic", "advanced"):
        for label, address in ADDRESSES.items():
            response = run(
                label,
                f"geocode_{tier}",
                f"geocode_{tier}_{label.split()[0]}",
                lambda address=address, tier=tier: client.geocode(address, tier),
            )
            if tier == "premium" and label == "A1 clean":
                premium_a1 = response

    run(
        "A1 clean",
        "preciselyid_by_address",
        "preciselyid_A1",
        lambda: client.preciselyid_by_address(ADDRESSES["A1 clean"]),
    )
    coords = _coordinates(premium_a1)
    if coords is None:
        for operation, filename in (
            ("flood_by_location", "flood_A1"),
            ("distance_to_flood_hazard_by_location", "distance_A1"),
        ):
            result = CallResult(
                "A1 clean coordinates",
                operation,
                None,
                "Skipped: Premium geocoding returned no usable coordinates.",
            )
            results.append(result)
            _save_response(output_dir, filename, result)
    else:
        lat, lon = coords
        run(
            "A1 clean coordinates",
            "flood_by_location",
            "flood_A1",
            lambda: client.flood_by_location(lat, lon),
        )
        run(
            "A1 clean coordinates",
            "distance_to_flood_hazard_by_location",
            "distance_A1",
            lambda: client.distance_to_flood_hazard_by_location(lat, lon),
        )
    run(
        "A2 defective",
        "address_verification_us",
        "address_verification_premium_A2",
        lambda: client.validate_mailing_address_premium(ADDRESSES["A2 defective"]),
    )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Precisely Phase 0 smoke-test tooling")
    parser.add_argument("--stage", choices=("a", "b"), required=True)
    parser.add_argument("--execute", action="store_true", help="run calls after owner confirmation")
    args = parser.parse_args(argv)

    # Dry runs read process settings only and do not load local secrets.
    settings = Settings.from_env(dotenv_path=os.devnull)
    ledger = CreditLedger(settings.credit_hard_cap)
    plan = _plan(args.stage)
    _print_plan(args.stage, plan, ledger)
    if not args.execute:
        return 0
    prompt = (
        "Confirm portal dashboard reconciliation and type YES: "
        if args.stage == "b"
        else "Type YES to authorize these live Precisely calls: "
    )
    if input(prompt).strip() != "YES":
        print("Execution cancelled.")
        return 1

    live_settings = Settings.from_env()
    client = PreciselyClient(live_settings, ledger)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = PROJECT_ROOT / "data" / "output" / "smoke" / timestamp
    results = _execute_stage(args.stage, client, output_dir)
    recorded = _print_summary(results)
    print(
        f"Credits recorded this run: {recorded}. "
        "Compare with the portal Credit Usage dashboard before running the next stage."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
