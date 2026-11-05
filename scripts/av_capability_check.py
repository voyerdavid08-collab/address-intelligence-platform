"""One-off Address Verification capability check (not a pipeline stage)."""

import argparse
import csv
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable

from address_intelligence.config import Settings
from address_intelligence.credits import PRICE_TABLE, CreditCapExceeded, CreditLedger
from address_intelligence.providers.precisely import PreciselyClient, ProviderResponse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = PROJECT_ROOT / "data" / "output" / "av_check"
PRODUCT_OPERATIONS = {
    "premium": "address_verification_us",
    "uscan": "address_verification_uscan",
}
GEOCODE_OPERATION = "geocode_premium"
INPUT_COLUMNS = (
    "record_id",
    "variant",
    "firm_name",
    "address_line1",
    "address_line2",
    "city",
    "state",
    "postal_code",
)
AV_FIELDS = (
    "Status",
    "Status.Code",
    "Status.Description",
    "Confidence",
    "AddressQuality",
    "Deliverability",
    "DPV",
    "DPVFootnote",
    "DPVVacant",
    "DPVNoStat",
    "CMRA",
    "SuiteLinkReturnCode",
)
STANDARDISED_FIELDS = (
    ("std_address_line1", "AddressLine1"),
    ("std_address_line2", "AddressLine2"),
    ("std_city", "City"),
    ("std_state", "StateProvince"),
    ("std_postal_code", "PostalCode"),
)
RESULT_COLUMNS = (
    "record_id",
    "variant",
    "product",
    *AV_FIELDS,
    *(name for name, _ in STANDARDISED_FIELDS),
    "geocode_precision_code",
    "PB_KEY",
)


@dataclass(frozen=True)
class PlannedCall:
    record_id: str
    variant: str
    kind: str  # "premium", "uscan" or "geocode"
    operation: str
    credits: Decimal
    row: dict[str, str]


def read_input(path: Path, limit: int | None) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = [name for name in INPUT_COLUMNS if name not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"Input CSV is missing columns: {', '.join(missing)}")
        rows = [
            {name: (row.get(name) or "").strip() for name in INPUT_COLUMNS}
            for row in reader
        ]
    return rows[:limit] if limit is not None else rows


def _parse_products(value: str) -> list[str]:
    products = [item.strip().lower() for item in value.split(",") if item.strip()]
    unknown = [item for item in products if item not in PRODUCT_OPERATIONS]
    if unknown or not products:
        raise argparse.ArgumentTypeError("products must be premium and/or uscan.")
    return list(dict.fromkeys(products))


def build_plan(
    rows: list[dict[str, str]], products: list[str], geocode: bool
) -> list[PlannedCall]:
    plan: list[PlannedCall] = []
    for row in rows:
        kinds = [*products, *(["geocode"] if geocode else [])]
        for kind in kinds:
            operation = PRODUCT_OPERATIONS.get(kind, GEOCODE_OPERATION)
            plan.append(
                PlannedCall(
                    row["record_id"],
                    row["variant"],
                    kind,
                    operation,
                    PRICE_TABLE[operation],
                    row,
                )
            )
    return plan


def _geocode_address(row: dict[str, str]) -> dict[str, str]:
    main_address = " ".join(
        part.strip()
        for part in (row["address_line1"], row["address_line2"])
        if part.strip()
    )
    params = {"mainAddress": main_address} if main_address else {}
    params.update(
        {
            key: value
            for key, value in (
                ("areaName3", row["city"]),
                ("areaName1", row["state"]),
                ("postalCode", row["postal_code"]),
                ("country", "USA"),
            )
            if value.strip()
        }
    )
    return params


def _request_payload(call: PlannedCall) -> dict[str, Any]:
    row = call.row
    if call.kind == "geocode":
        return {"operation": call.operation, **_geocode_address(row)}
    return {
        "operation": call.operation,
        "AddressLine1": row["address_line1"],
        "AddressLine2": row["address_line2"],
        "City": row["city"],
        "StateProvince": row["state"],
        "PostalCode": row["postal_code"],
        "Country": "USA",
        "FirmName": row["firm_name"],
    }


def cache_path(cache_dir: Path, call: PlannedCall) -> Path:
    digest = hashlib.sha256(
        json.dumps(_request_payload(call), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return cache_dir / f"{call.kind}_{digest}.json"


def _send(client: PreciselyClient, call: PlannedCall) -> ProviderResponse:
    if call.kind == "geocode":
        return client.geocode(_geocode_address(call.row), "premium")
    row = call.row
    return client.validate_mailing_address_structured(
        call.kind,
        address_line1=row["address_line1"],
        address_line2=row["address_line2"],
        city=row["city"],
        state_province=row["state"],
        postal_code=row["postal_code"],
        firm_name=row["firm_name"],
    )


def _print_plan(
    plan: list[PlannedCall], cache_dir: Path, ledger: CreditLedger
) -> Decimal:
    print("Planned calls:")
    total = Decimal("0")
    for call in plan:
        cached = cache_path(cache_dir, call).exists()
        cost = Decimal("0") if cached else call.credits
        total += cost
        suffix = "cached, 0 credits" if cached else f"estimated {cost} credits"
        print(f"- {call.record_id} / {call.variant}: {call.operation} ({suffix})")
    print(f"Planned credit estimate: {total}")
    print(
        f"Ledger: spent {ledger.cumulative_spent} / {ledger.hard_cap}; "
        f"remaining {ledger.remaining}"
    )
    return total


def _flatten_output(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    outputs = data.get("Output")
    if not isinstance(outputs, list) or not outputs or not isinstance(outputs[0], dict):
        return {}
    flat = dict(outputs[0])
    extra = flat.get("user_fields")
    if isinstance(extra, list):
        for item in extra:
            if isinstance(item, dict) and "name" in item:
                flat.setdefault(item["name"], item.get("value"))
    return flat


def _find_key(data: Any, key: str) -> Any:
    if isinstance(data, dict):
        if key in data:
            return data[key]
        for value in data.values():
            found = _find_key(value, key)
            if found is not None:
                return found
    elif isinstance(data, list):
        for value in data:
            found = _find_key(value, key)
            if found is not None:
                return found
    return None


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _geocode_fields(data: Any) -> dict[str, str]:
    candidates = data.get("candidates") if isinstance(data, dict) else None
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        return {"geocode_precision_code": "", "PB_KEY": ""}
    first = candidates[0]
    return {
        "geocode_precision_code": _text(first.get("precisionCode")),
        "PB_KEY": _text(_find_key(first, "PB_KEY")),
    }


def result_row(
    call: PlannedCall, data: Any, geocode: dict[str, str] | None
) -> dict[str, str]:
    flat = _flatten_output(data)
    out = {name: "" for name in RESULT_COLUMNS}
    out.update(record_id=call.record_id, variant=call.variant, product=call.kind)
    for name in AV_FIELDS:
        out[name] = _text(flat.get(name))
    for column, source in STANDARDISED_FIELDS:
        out[column] = _text(flat.get(source))
    if geocode:
        out.update(geocode)
    return out


def _execute(
    plan: list[PlannedCall],
    client: PreciselyClient,
    ledger: CreditLedger,
    output_dir: Path,
) -> int:
    cache_dir = output_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    data_by_call: dict[int, Any] = {}
    stopped = False
    live_calls = 0
    for index, call in enumerate(plan):
        path = cache_path(cache_dir, call)
        if path.exists():
            data_by_call[index] = json.loads(path.read_text(encoding="utf-8"))
            continue
        try:
            ledger.check(call.operation, 1)
        except CreditCapExceeded as exc:
            print(f"Stopping: {exc}")
            stopped = True
            break
        response = _send(client, call)
        live_calls += 1
        if response.status_code == 200:
            data_by_call[index] = response.data
            path.write_text(json.dumps(response.data, indent=2) + "\n", encoding="utf-8")
        else:
            print(f"Call failed for {call.record_id} / {call.variant} ({call.kind}); not cached.")

    geocodes = {
        (call.record_id, call.variant): _geocode_fields(data_by_call.get(index))
        for index, call in enumerate(plan)
        if call.kind == "geocode"
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_dir / "results.csv", "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_COLUMNS)
        writer.writeheader()
        for index, call in enumerate(plan):
            if call.kind == "geocode":
                continue
            writer.writerow(
                result_row(
                    call,
                    data_by_call.get(index),
                    geocodes.get((call.record_id, call.variant)),
                )
            )
    print(
        f"Live calls made: {live_calls}. Ledger spent {ledger.cumulative_spent} / "
        f"{ledger.hard_cap}; remaining {ledger.remaining}"
    )
    print(f"Results written to {output_dir / 'results.csv'}")
    return 1 if stopped else 0


def main(
    argv: list[str] | None = None,
    *,
    ledger: CreditLedger | None = None,
    output_dir: Path | None = None,
    client_factory: Callable[[Settings, CreditLedger], PreciselyClient] = PreciselyClient,
) -> int:
    parser = argparse.ArgumentParser(description="Precisely Address Verification capability check")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--products", type=_parse_products, default=["premium", "uscan"])
    parser.add_argument("--geocode", action="store_true", help="also call Geocode Premium")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--execute", action="store_true", help="run calls after typed YES")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be a positive integer.")

    output_dir = output_dir or OUTPUT_DIR
    rows = read_input(args.input, args.limit)
    # Dry runs read process settings only and do not load local secrets.
    settings = Settings.from_env(dotenv_path=os.devnull)
    ledger = ledger or CreditLedger(settings.credit_hard_cap)
    plan = build_plan(rows, args.products, args.geocode)
    _print_plan(plan, output_dir / "cache", ledger)
    if not args.execute:
        return 0
    if input("Type YES to authorize these live Precisely calls: ").strip() != "YES":
        print("Execution cancelled.")
        return 1

    client = client_factory(Settings.from_env(), ledger)
    return _execute(plan, client, ledger, output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
