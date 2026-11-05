"""One-off comparison of verified flood tiers with ZIP-level flood tiers."""

import argparse
import csv
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Callable

from address_intelligence.config import Settings
from address_intelligence.credits import CreditLedger, PRICE_TABLE
from address_intelligence.flood import flood_zone_base, risk_tier
from address_intelligence.providers.precisely import PreciselyClient, ProviderResponse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS_PATH = PROJECT_ROOT / "data" / "output" / "run" / "results_rowlevel.csv"
VERIFIED_FLOOD_CACHE = (
    PROJECT_ROOT / "data" / "output" / "run" / "cache" / "flood_by_location"
)
INPUT_PATH = PROJECT_ROOT / "data" / "input" / "policyholders_synthetic.csv"
OUTPUT_DIR = PROJECT_ROOT / "data" / "output" / "zip_comparison"
GEOCODE_OPERATION = "geocode_basic"
FLOOD_OPERATION = "flood_by_location"
RISK_RANK = {"Minimal": 1, "Moderate": 2, "High": 3}
RESULT_COLUMNS = (
    "policy_id",
    "metro",
    "verified_zip5",
    "verified_precision_code",
    "zip_precision_code",
    "verified_flood_zone_code",
    "verified_tier",
    "zip_flood_zone_code",
    "zip_tier",
    "tier_changed",
    "comparison",
    "status",
)


@dataclass(frozen=True)
class ZipGroup:
    zip5: str
    records: tuple[dict[str, str], ...]


def _read_csv(path: Path, required: tuple[str, ...] = ()) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        missing = [
            column for column in required if column not in (reader.fieldnames or [])
        ]
        if missing:
            raise ValueError(f"{path} is missing required columns: {', '.join(missing)}")
        return list(reader)


def _standard_zip(value: str) -> str:
    match = re.match(r"^\s*(\d{5})(?:\D|$)", value or "")
    return match.group(1) if match else ""


def _read_metros(path: Path) -> dict[str, str]:
    return {
        row["policy_id"].strip(): row.get("metro", "").strip()
        for row in _read_csv(path, ("policy_id", "metro"))
        if row.get("policy_id", "").strip()
    }


def _cached_response(path: Path) -> ProviderResponse | None:
    if not path.exists():
        return None
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError(f"Unable to read response cache at {path}.") from None
    if not isinstance(cached, dict) or "status_code" not in cached:
        raise ValueError(f"Invalid response cache at {path}.")
    return ProviderResponse(
        cached["status_code"],
        cached.get("data"),
        cached.get("error"),
        Decimal("0"),
    )


def _write_response(path: Path, response: ProviderResponse) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
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


def _candidate(data: Any) -> dict[str, Any] | None:
    candidates = data.get("candidates") if isinstance(data, dict) else None
    if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
        return candidates[0]
    return None


def _coordinates(candidate: dict[str, Any] | None) -> tuple[float, float] | None:
    geometry = candidate.get("geometry") if candidate else None
    values = geometry.get("coordinates") if isinstance(geometry, dict) else None
    try:
        if isinstance(values, str):
            longitude, latitude = (float(part.strip()) for part in values.split(",", 1))
            return latitude, longitude
        if isinstance(values, list) and len(values) >= 2:
            return float(values[1]), float(values[0])
    except (TypeError, ValueError):
        return None
    return None


def _flood_code(data: Any) -> str:
    zone = data.get("floodZone") if isinstance(data, dict) else None
    return str(zone.get("code") or "") if isinstance(zone, dict) else ""


def _cache_key(kind: str, values: dict[str, Any]) -> str:
    canonical = json.dumps(values, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{kind}_{digest}.json"


def _geocode_cache_path(cache_dir: Path, zip5: str) -> Path:
    return cache_dir / _cache_key(
        "geocode_basic",
        {"operation": GEOCODE_OPERATION, "mainAddress": zip5, "country": "USA"},
    )


def _flood_cache_path(
    cache_dir: Path, zip5: str, point: tuple[float, float]
) -> Path:
    latitude, longitude = point
    return cache_dir / _cache_key(
        "flood_by_location",
        {
            "operation": FLOOD_OPERATION,
            "latitude": latitude,
            "longitude": longitude,
            "zip5": zip5,
        },
    )


def load_records(
    results_path: Path, verified_flood_cache: Path, input_path: Path
) -> list[dict[str, str]]:
    metros = _read_metros(input_path)
    records: list[dict[str, str]] = []
    for row in _read_csv(
        results_path,
        (
            "policy_id",
            "eligibility",
            "standardized_zip5",
            "precision_code",
        ),
    ):
        if row.get("eligibility") != "eligible":
            continue
        zip5 = _standard_zip(row.get("standardized_zip5", ""))
        if not zip5:
            continue
        policy_id = row.get("policy_id", "").strip()
        if not policy_id:
            continue
        cached = _cached_response(verified_flood_cache / f"{policy_id}.json")
        if cached is None or cached.status_code != 200:
            continue
        code = _flood_code(cached.data)
        if not code:
            continue
        records.append(
            {
                "policy_id": policy_id,
                "metro": row.get("metro", "").strip() or metros.get(policy_id, ""),
                "verified_zip5": zip5,
                "verified_precision_code": row.get("precision_code", "").strip(),
                "verified_flood_zone_code": code,
                "verified_tier": risk_tier(flood_zone_base(code)),
            }
        )
    return records


def group_by_zip(records: list[dict[str, str]], limit_zips: int | None) -> list[ZipGroup]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for record in records:
        grouped.setdefault(record["verified_zip5"], []).append(record)
    zip_codes = sorted(grouped)
    if limit_zips is not None:
        zip_codes = zip_codes[:limit_zips]
    return [ZipGroup(code, tuple(grouped[code])) for code in zip_codes]


def _print_plan(groups: list[ZipGroup], cache_dir: Path, ledger: CreditLedger) -> None:
    estimated = Decimal("0")
    print("ZIP-level flood comparison plan:")
    print(f"Unique ZIPs: {len(groups)}")
    print(f"Records covered: {sum(len(group.records) for group in groups)}")
    for group in groups:
        geocode_response = _cached_response(
            _geocode_cache_path(cache_dir, group.zip5)
        )
        geocode_cost = (
            Decimal("0")
            if geocode_response is not None
            else PRICE_TABLE[GEOCODE_OPERATION]
        )
        estimated += geocode_cost
        if geocode_response is not None:
            _, point = _zip_point(geocode_response)
        else:
            point = None
        flood_response = (
            _cached_response(_flood_cache_path(cache_dir, group.zip5, point))
            if point is not None
            else None
        )
        flood_is_needed = geocode_response is None or point is not None
        flood_cost = (
            PRICE_TABLE[FLOOD_OPERATION]
            if flood_is_needed and flood_response is None
            else Decimal("0")
        )
        estimated += flood_cost
        geocode_note = (
            "cached" if geocode_response is not None else f"{geocode_cost} credits"
        )
        flood_note = (
            "skipped (cached response has no ZIP point)"
            if geocode_response is not None and point is None
            else (
                "cached"
                if flood_response is not None
                else f"{flood_cost} credits if a ZIP point is returned"
            )
        )
        print(
            f"- ZIP {group.zip5}: Geocode Basic "
            f"({geocode_note}); flood_by_location ({flood_note})"
        )
    print(
        f"Planned calls: up to {len(groups) * 2} "
        f"({len(groups)} Geocode Basic, up to {len(groups)} flood)"
    )
    print(f"Planned credit estimate: {estimated}")
    print(
        f"Ledger: spent {ledger.cumulative_spent} / {ledger.hard_cap}; "
        f"remaining {ledger.remaining}"
    )


def _zip_point(response: ProviderResponse) -> tuple[str, tuple[float, float] | None]:
    candidate = _candidate(response.data)
    precision = str((candidate or {}).get("precisionCode") or "")
    if response.status_code != 200 or not precision.upper().startswith("Z"):
        return precision, None
    return precision, _coordinates(candidate)


def _compare_record(
    record: dict[str, str],
    zip_precision: str,
    zip_flood_code: str,
    status: str,
) -> dict[str, str]:
    zip_tier = risk_tier(flood_zone_base(zip_flood_code))
    verified_tier = record["verified_tier"]
    changed = bool(zip_flood_code) and zip_tier != verified_tier
    verified_rank = RISK_RANK.get(verified_tier)
    zip_rank = RISK_RANK.get(zip_tier)
    comparison = ""
    if changed and verified_rank is not None and zip_rank is not None:
        comparison = "understated" if zip_rank < verified_rank else "overstated"
    return {
        **record,
        "zip_precision_code": zip_precision,
        "zip_flood_zone_code": zip_flood_code,
        "zip_tier": zip_tier if zip_flood_code else "",
        "tier_changed": str(changed).lower() if zip_flood_code else "",
        "comparison": comparison,
        "status": status,
    }


def _execute(
    groups: list[ZipGroup],
    client: PreciselyClient,
    ledger: CreditLedger,
    output_dir: Path,
) -> list[dict[str, str]]:
    cache_dir = output_dir / "cache"
    output_rows: list[dict[str, str]] = []
    for group in groups:
        geocode_path = _geocode_cache_path(cache_dir, group.zip5)
        geocode = _cached_response(geocode_path)
        if geocode is None:
            ledger.check(GEOCODE_OPERATION, 1)
            geocode = client.geocode(
                {"mainAddress": group.zip5, "country": "USA"}, "basic"
            )
            if geocode.status_code == 200:
                _write_response(geocode_path, geocode)
        precision, point = _zip_point(geocode)
        if point is None:
            output_rows.extend(
                _compare_record(record, precision, "", "no_zip_point")
                for record in group.records
            )
            continue

        flood_path = _flood_cache_path(cache_dir, group.zip5, point)
        flood = _cached_response(flood_path)
        if flood is None:
            ledger.check(FLOOD_OPERATION, 1)
            flood = client.flood_by_location(*point)
            if flood.status_code == 200:
                _write_response(flood_path, flood)
        code = _flood_code(flood.data) if flood.status_code == 200 else ""
        status = "compared" if code else "zip_flood_unavailable"
        output_rows.extend(
            _compare_record(record, precision, code, status)
            for record in group.records
        )
    return output_rows


def _summary_markdown(rows: list[dict[str, str]]) -> str:
    compared = [row for row in rows if row["status"] == "compared"]

    def counts(selected: list[dict[str, str]]) -> tuple[int, int, int, int]:
        return (
            len(selected),
            sum(row["tier_changed"] == "true" for row in selected),
            sum(row["comparison"] == "understated" for row in selected),
            sum(row["comparison"] == "overstated" for row in selected),
        )

    lines = [
        "# ZIP-level flood comparison",
        "",
        f"- Records compared: {len(compared)}",
        f"- Tier changed: {sum(row['tier_changed'] == 'true' for row in compared)}",
        f"- Understated: {sum(row['comparison'] == 'understated' for row in compared)}",
        f"- Overstated: {sum(row['comparison'] == 'overstated' for row in compared)}",
        (
            "- ZIPs with no ZIP-level point: "
            f"{len({row['verified_zip5'] for row in rows if row['status'] == 'no_zip_point'})}"
        ),
        "",
        "## By verified precision",
        "",
        "| Precision | Records compared | Tier changed | Understated | Overstated |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    precision_groups = {
        prefix: [
            row for row in compared
            if row["verified_precision_code"].upper().startswith(prefix)
        ]
        for prefix in ("S5", "S7", "S8")
    }
    for prefix, selected in precision_groups.items():
        values = counts(selected)
        lines.append(f"| {prefix} | {values[0]} | {values[1]} | {values[2]} | {values[3]} |")
    lines.extend(
        [
            "",
            "## By metro",
            "",
            "| Metro | Records compared | Tier changed | Understated | Overstated |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    metros = sorted({row["metro"] or "Unknown" for row in compared})
    for metro in metros:
        selected = [
            row for row in compared if (row["metro"] or "Unknown") == metro
        ]
        values = counts(selected)
        lines.append(f"| {metro} | {values[0]} | {values[1]} | {values[2]} | {values[3]} |")
    return "\n".join(lines) + "\n"


def _write_outputs(output_dir: Path, rows: list[dict[str, str]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "results.csv").open(
        "w", newline="", encoding="utf-8"
    ) as destination:
        writer = csv.DictWriter(
            destination, fieldnames=RESULT_COLUMNS, lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
    (output_dir / "summary.md").write_text(
        _summary_markdown(rows), encoding="utf-8"
    )


def main(
    argv: list[str] | None = None,
    *,
    results_path: Path = RESULTS_PATH,
    verified_flood_cache: Path = VERIFIED_FLOOD_CACHE,
    input_path: Path = INPUT_PATH,
    output_dir: Path = OUTPUT_DIR,
    ledger: CreditLedger | None = None,
    client_factory: Callable[[Settings, CreditLedger], PreciselyClient] = PreciselyClient,
) -> int:
    parser = argparse.ArgumentParser(
        description="Compare verified and ZIP-level flood tiers"
    )
    parser.add_argument("--limit-zips", type=int)
    parser.add_argument("--execute", action="store_true", help="run calls after typed YES")
    args = parser.parse_args(argv)
    if args.limit_zips is not None and args.limit_zips < 1:
        parser.error("--limit-zips must be a positive integer.")

    settings = Settings.from_env(dotenv_path=os.devnull)
    ledger = ledger or CreditLedger(settings.credit_hard_cap)
    records = load_records(results_path, verified_flood_cache, input_path)
    groups = group_by_zip(records, args.limit_zips)
    _print_plan(groups, output_dir / "cache", ledger)
    if not args.execute:
        return 0
    if input("Type YES to authorize these live Precisely calls: ").strip() != "YES":
        print("Execution cancelled.")
        return 1

    client = client_factory(Settings.from_env(), ledger)
    rows = _execute(groups, client, ledger, output_dir)
    _write_outputs(output_dir, rows)
    print(
        f"Ledger: spent {ledger.cumulative_spent} / {ledger.hard_cap}; "
        f"remaining {ledger.remaining}"
    )
    print(f"Results written to {output_dir / 'results.csv'}")
    print(f"Aggregate summary written to {output_dir / 'summary.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
