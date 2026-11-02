"""Compare existing single-line and structured Geocode Premium caches."""

import csv
import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_PATH = PROJECT_ROOT / "data" / "input" / "policyholders_synthetic.csv"
CACHE_ROOT = PROJECT_ROOT / "data" / "output" / "run" / "cache"
SINGLE_CACHE = CACHE_ROOT / "geocode_premium"
STRUCTURED_CACHE = CACHE_ROOT / "geocode_premium_structured"


def _read_cache(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError(f"Unable to read Geocode cache at {path}.") from None
    return record.get("data") if isinstance(record, dict) else None


def _candidate(data: Any) -> dict[str, Any] | None:
    candidates = data.get("candidates") if isinstance(data, dict) else None
    if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
        return candidates[0]
    return None


def _precision_category(data: Any) -> str:
    candidate = _candidate(data)
    code = candidate.get("precisionCode") if candidate else None
    if not isinstance(code, str) or not code:
        return "none"
    if code.startswith(("S5", "S7", "S8")):
        return "S5/S7/S8"
    if code.startswith("S4"):
        return "S4"
    if code.startswith(("Z", "G")):
        return "Z/G"
    return "none"


def _matches_input(data: Any, city: str, zip5: str) -> bool:
    candidate = _candidate(data)
    address = candidate.get("address") if candidate else None
    if not isinstance(address, dict):
        return False
    returned_city = address.get("areaName3")
    returned_zip = address.get("postCode1")
    returned_zip5 = (
        "".join(character for character in returned_zip if character.isdigit())[:5]
        if isinstance(returned_zip, str)
        else ""
    )
    return (
        isinstance(returned_city, str)
        and returned_city.strip().casefold() == city.strip().casefold()
        and returned_zip5 == zip5.strip()
    )


def main() -> int:
    with INPUT_PATH.open(newline="", encoding="utf-8-sig") as source:
        policies = list(csv.DictReader(source))

    rows = []
    for policy in policies:
        policy_id = policy["policy_id"]
        single = _read_cache(SINGLE_CACHE / f"{policy_id}.json")
        structured = _read_cache(STRUCTURED_CACHE / f"{policy_id}.json")
        if single is None or structured is None:
            continue
        rows.append(
            (
                policy_id,
                _precision_category(single),
                _precision_category(structured),
                f"single:{'yes' if _matches_input(single, policy['raw_city'], policy['raw_zip5']) else 'no'}; "
                f"structured:{'yes' if _matches_input(structured, policy['raw_city'], policy['raw_zip5']) else 'no'}",
            )
        )

    headers = (
        "policy_id",
        "single-line precision",
        "structured precision",
        "input city/ZIP match",
    )
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in rows))
        if rows
        else len(headers[index])
        for index in range(len(headers))
    ]
    print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(headers)))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(" | ".join(value.ljust(widths[index]) for index, value in enumerate(row)))
    if not rows:
        print("No policy IDs are present in both Geocode Premium caches.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
