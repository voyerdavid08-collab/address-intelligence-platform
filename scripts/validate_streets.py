"""Dry-run-first validation of public street anchors using Geocode Premium."""

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
from typing import Any, Protocol

from address_intelligence.config import Settings
from address_intelligence.credits import CreditCapExceeded, CreditLedger
from address_intelligence.providers.precisely import PreciselyClient, ProviderResponse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = PROJECT_ROOT / "data" / "input" / "reference_streets.csv"
RESULT_PATH = PROJECT_ROOT / "data" / "input" / "reference_streets_validation.csv"
OUTPUT_ROOT = PROJECT_ROOT / "data" / "output" / "validation"
RESULT_COLUMNS = ("street_id", "status", "reason", "validated_on")
LEVEL_PREFIXES = ("S5", "S7", "S8")


class Geocoder(Protocol):
    def geocode(self, address: str, tier: str) -> ProviderResponse: ...


@dataclass(frozen=True)
class ValidationResult:
    street_id: str
    status: str
    reason: str
    validated_on: str
    returned_zip5: str | None = None


def _midpoint(row: dict[str, str]) -> int:
    minimum = int(row["house_number_min"])
    maximum = int(row["house_number_max"])
    if minimum <= 0 or maximum < minimum:
        raise ValueError(f"Invalid house-number range for {row['street_id']}.")
    return (minimum + maximum) // 2


def _planned_address(row: dict[str, str]) -> str:
    return (
        f"{_midpoint(row)} {row['street_name']}, {row['city']}, "
        f"{row['state']} {row['zip5']}"
    )


def _returned_zip5(candidate: dict[str, Any]) -> str | None:
    address = candidate.get("address")
    if not isinstance(address, dict):
        return None
    postal_code = address.get("postCode1")
    if not isinstance(postal_code, str):
        return None
    digits = "".join(character for character in postal_code if character.isdigit())
    return digits[:5] if len(digits) >= 5 else None


def classify_response(
    row: dict[str, str], response: ProviderResponse
) -> tuple[str, str]:
    if response.error is not None or response.status_code != 200:
        return "rejected", "API error"

    data = response.data
    candidates = data.get("candidates") if isinstance(data, dict) else None
    if not isinstance(candidates, list) or not candidates:
        return "rejected", "no candidates"
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        return "rejected", "no candidates"

    precision_code = candidate.get("precisionCode")
    if not isinstance(precision_code, str) or not precision_code.startswith(LEVEL_PREFIXES):
        return "rejected", "street-level match only (not address-level)"

    address = candidate.get("address")
    if not isinstance(address, dict):
        return "rejected", "state mismatch"
    state = address.get("areaName1")
    if not isinstance(state, str) or state.strip().upper() != row["state"].upper():
        return "rejected", "state mismatch"

    returned_zip5 = _returned_zip5(candidate)
    if returned_zip5 != row["zip5"]:
        return "rejected", "ZIP mismatch"

    return "accepted", "validated"


def _read_reference(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        required = {
            "street_id",
            "street_name",
            "city",
            "state",
            "zip5",
            "house_number_min",
            "house_number_max",
            "metro",
            "selection_note",
        }
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("Reference street CSV is missing required columns.")
        return list(reader)


def _write_results(path: Path, results: list[ValidationResult]) -> None:
    merged: dict[str, dict[str, str]] = {}
    if path.exists():
        with path.open(newline="", encoding="utf-8-sig") as source:
            reader = csv.DictReader(source)
            if tuple(reader.fieldnames or ()) != RESULT_COLUMNS:
                raise ValueError("Validation CSV has unexpected columns.")
            merged = {
                row["street_id"]: {column: row[column] for column in RESULT_COLUMNS}
                for row in reader
            }
    for result in results:
        merged[result.street_id] = {
            "street_id": result.street_id,
            "status": result.status,
            "reason": result.reason,
            "validated_on": result.validated_on,
        }

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=RESULT_COLUMNS)
        writer.writeheader()
        writer.writerows(merged.values())


def _save_raw_response(directory: Path, street_id: str, response: ProviderResponse) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    payload: Any = response.data
    if payload is None:
        payload = {"status_code": response.status_code, "error": response.error}
    (directory / f"{street_id}.json").write_text(
        json.dumps(payload, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def validate_rows(
    rows: list[dict[str, str]],
    client: Geocoder,
    raw_output_dir: Path,
    *,
    validated_on: str | None = None,
) -> tuple[list[ValidationResult], bool]:
    date = validated_on or datetime.now(timezone.utc).date().isoformat()
    results: list[ValidationResult] = []
    stopped_for_cap = False
    for row in rows:
        try:
            response = client.geocode(_planned_address(row), "premium")
        except CreditCapExceeded:
            results.append(
                ValidationResult(row["street_id"], "rejected", "credit cap exceeded", date)
            )
            stopped_for_cap = True
            break
        _save_raw_response(raw_output_dir, row["street_id"], response)
        status, reason = classify_response(row, response)
        data = response.data
        candidates = data.get("candidates") if isinstance(data, dict) else None
        candidate = candidates[0] if isinstance(candidates, list) and candidates else None
        returned_zip5 = (
            _returned_zip5(candidate) if isinstance(candidate, dict) and reason == "ZIP mismatch" else None
        )
        results.append(
            ValidationResult(row["street_id"], status, reason, date, returned_zip5)
        )
    return results, stopped_for_cap


def _print_plan(rows: list[dict[str, str]], ledger: CreditLedger) -> None:
    print("Planned Geocode Premium validation calls:")
    for row in rows:
        print(f"- {row['street_id']} ({row['metro']}): {_planned_address(row)} - 1 credit")
    total = Decimal(len(rows))
    print(f"Planned calls: {len(rows)}")
    print(f"Estimated credits: {total}")
    print(
        f"Ledger: spent {ledger.cumulative_spent} / {ledger.hard_cap}; "
        f"remaining {ledger.remaining}"
    )


def _print_summary(
    rows: list[dict[str, str]],
    results: list[ValidationResult],
    *,
    stopped_for_cap: bool,
) -> None:
    metro_by_id = {row["street_id"]: row["metro"] for row in rows}
    counts: dict[str, dict[str, int]] = {}
    rejection_reasons: dict[str, int] = {}
    for result in results:
        metro = metro_by_id[result.street_id]
        metro_counts = counts.setdefault(metro, {"accepted": 0, "rejected": 0})
        outcome = "accepted" if result.status.startswith("accepted") else "rejected"
        metro_counts[outcome] += 1
        if outcome == "rejected":
            rejection_reasons[result.reason] = rejection_reasons.get(result.reason, 0) + 1

    print("Validation summary by metro:")
    for metro in dict.fromkeys(row["metro"] for row in rows):
        metro_counts = counts.get(metro, {"accepted": 0, "rejected": 0})
        not_run = sum(row["metro"] == metro for row in rows) - sum(metro_counts.values())
        print(
            f"- {metro}: accepted {metro_counts['accepted']}, "
            f"rejected {metro_counts['rejected']}, not run {not_run}"
        )
        if metro_counts["accepted"] < 5:
            print(f"  WARNING: fewer than 5 accepted streets for {metro}.")

    print("Rejection reasons:")
    if rejection_reasons:
        for reason, count in sorted(rejection_reasons.items()):
            print(f"- {reason}: {count}")
    else:
        print("- none")
    zip_mismatches = [
        result
        for result in results
        if result.reason == "ZIP mismatch" and result.returned_zip5 is not None
    ]
    for result in zip_mismatches:
        print(
            f"- {result.street_id}: ZIP mismatch; returned ZIP5 "
            f"{result.returned_zip5} (console only)"
        )
    if stopped_for_cap:
        print("Stopped before the next API request because the credit cap was reached.")


def main(
    argv: list[str] | None = None,
    *,
    input_path: Path = INPUT_PATH,
    result_path: Path = RESULT_PATH,
    output_root: Path = OUTPUT_ROOT,
) -> int:
    parser = argparse.ArgumentParser(description="Validate reference street anchors")
    parser.add_argument("--execute", action="store_true", help="run calls after confirmation")
    parser.add_argument(
        "--ids",
        help="comma-separated street IDs to validate; defaults to all rows",
    )
    args = parser.parse_args(argv)

    rows = _read_reference(input_path)
    if args.ids is not None:
        requested_ids = {street_id.strip() for street_id in args.ids.split(",") if street_id.strip()}
        available_ids = {row["street_id"] for row in rows}
        unknown_ids = requested_ids - available_ids
        if not requested_ids:
            parser.error("--ids must contain at least one street ID")
        if unknown_ids:
            parser.error(f"Unknown street ID(s): {', '.join(sorted(unknown_ids))}")
        rows = [row for row in rows if row["street_id"] in requested_ids]
    settings = Settings.from_env(dotenv_path=os.devnull)
    ledger = CreditLedger(settings.credit_hard_cap)
    _print_plan(rows, ledger)
    if not args.execute:
        return 0
    if input("Type YES to authorize Geocode Premium validation calls: ").strip() != "YES":
        print("Execution cancelled.")
        return 1

    live_settings = Settings.from_env()
    live_settings.require_api_credentials()
    client = PreciselyClient(live_settings, ledger)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results, stopped_for_cap = validate_rows(
        rows,
        client,
        output_root / timestamp,
    )
    _write_results(result_path, results)
    _print_summary(rows, results, stopped_for_cap=stopped_for_cap)
    return 1 if stopped_for_cap else 0


if __name__ == "__main__":
    raise SystemExit(main())
