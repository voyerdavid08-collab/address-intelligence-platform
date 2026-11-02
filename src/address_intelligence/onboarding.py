"""Local-only client onboarding, file checks, and profile creation."""

import csv
import difflib
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO, StringIO
import re
from pathlib import Path
from typing import Any, Iterable
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
import yaml

from address_intelligence.client_profile import (
    PROJECT_ROOT,
    ClientProfile,
    estimate_cost,
    load_client_profile,
)
from address_intelligence.credits import PRICE_TABLE


CANONICAL_COLUMNS = (
    "policy_id",
    "address_line1",
    "address_line2",
    "city",
    "state",
    "zip5",
    "metro",
)
REQUIRED_COLUMNS = ("policy_id", "address_line1", "city", "state", "zip5")
VALID_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN "
    "MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA "
    "WA WV WI WY DC PR VI GU AS MP".split()
)
STATE_NAMES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
    "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY", "District of Columbia": "DC",
}
_GROUP_ORDER = (
    "Needs client follow-up",
    "Precisely usually fixes",
    "Review",
    "No issues found",
)
ENRICHMENT_OPERATIONS = (
    "flood_by_location",
    "distance_to_flood_hazard_by_location",
    "property_attributes_v2",
    "preciselyid_by_address",
)
_SLUG = re.compile(r"^[a-z0-9-]+$")
_HOUSE_NUMBER = re.compile(r"^\s*\d+[A-Za-z]?\b")
_ZIP = re.compile(r"^\d{5}(?:-\d{4})?$")
_LEADING_ZERO_ZIP_STATES = frozenset({"CT", "MA", "ME", "NH", "NJ", "RI", "VT", "PR", "VI"})
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_PO_BOX = re.compile(r"\bP(?:OST)?\.?\s*O(?:FFICE)?\.?\s*BOX\b", re.IGNORECASE)

_ALIASES = {
    "policy_id": (
        "policy", "policy #", "policy no", "policy number", "policy id",
        "contract ref", "contract", "id",
    ),
    "address_line1": (
        "street", "street address", "address", "address1", "address 1", "addr",
        "street line 1", "address line 1",
    ),
    "address_line2": (
        "apt", "apartment", "unit", "suite", "address2", "address 2", "address line 2",
    ),
    "city": ("city", "town", "municipality", "locality"),
    "state": (
        "state", "prov", "province", "province code", "st", "st.", "state code",
        "state/province", "state_cd", "region",
    ),
    "zip5": (
        "zip", "zip5", "zipcode", "zip code", "postal", "postal code",
        "postcode", "post code",
    ),
    "metro": ("metro", "market", "region market"),
}
_CANONICAL_TOKENS = {
    "policy_id": ("policy id", "policy", "contract ref", "contract", "id"),
    "address_line1": ("address line1", "address line 1", "street address", "street", "address"),
    "address_line2": ("address line2", "address line 2", "apt", "unit", "suite"),
    "city": ("city", "town", "municipality"),
    "state": ("state", "province", "region"),
    "zip5": ("zipcode", "zip code", "zip", "postal code", "postcode", "postal"),
    "metro": ("metro", "market"),
}
_FRIENDLY_LABELS = {
    "policy_id": "Policy ID column",
    "address_line1": "Street address column",
    "address_line2": "Unit / apartment column (optional)",
    "city": "City column",
    "state": "State column",
    "zip5": "ZIP code column",
    "metro": "Metro column (optional)",
    "street_typo": "Street typo",
    "city_typo": "City typo",
    "missing_house_number": "Missing house number",
    "missing_unit": "Missing unit",
    "missing_zip": "Missing ZIP",
    "nonexistent_address": "Non-existent address",
    "wrong_zip": "Wrong ZIP",
    "street_name_issue": "Street name",
    "house_number_issue": "House number",
    "city_issue": "City",
    "zip_issue": "ZIP",
    "state_conflict": "State conflict",
    "analyst_review": "Analyst review",
    "customer_outreach": "Customer outreach",
    "no_suggestion": "No suggestion",
    "not_requested": "Not requested",
    "not_started": "Not started",
    "suggested, unverified": "Suggested, unverified",
    "verified by Precisely": "Verified by Precisely",
    "did not verify": "Did not verify",
    "rejected": "Rejected",
    "Records": "Records",
    "Empty street line": "Empty street line",
    "Missing house number": "Missing house number",
    "Missing ZIP": "Missing ZIP",
    "ZIP format invalid": "ZIP format invalid",
    "State not a valid US code": "State not a valid US code",
    "PO Box": "PO Box",
    "Duplicate addresses (normalized)": "Duplicate addresses (normalized)",
    "Duplicate IDs": "Duplicate IDs",
    "Records with at least one blocking issue": "Records with at least one blocking issue",
    "ZIP lost its leading zero": "ZIP lost its leading zero",
    "Missing city (valid ZIP)": "Missing city (valid ZIP)",
    "Missing city (no valid ZIP)": "Missing city (no valid ZIP)",
    "State written in full": "State written in full",
}
_CHECK_GROUPS = {
    "Missing ZIP": "Precisely usually fixes",
    "ZIP format invalid": "Precisely usually fixes",
    "ZIP lost its leading zero": "Precisely usually fixes",
    "State written in full": "Precisely usually fixes",
    "Missing city (valid ZIP)": "Precisely usually fixes",
    "Empty street line": "Needs client follow-up",
    "Missing house number": "Needs client follow-up",
    "PO Box": "Needs client follow-up",
    "State not a valid US code": "Needs client follow-up",
    "Missing city (no valid ZIP)": "Needs client follow-up",
    "Duplicate IDs": "Needs client follow-up",
    "Duplicate addresses (normalized)": "Review",
}
_CHECK_MEANINGS = {
    "Missing ZIP": "No ZIP code was supplied.",
    "ZIP format invalid": "ZIP must be five digits or ZIP+4.",
    "ZIP lost its leading zero": "A four-digit ZIP may have lost its first zero (e.g. 2134 instead of 02134) in Excel.",
    "State written in full": "Use the two-letter state code, such as TX instead of Texas.",
    "Missing city (valid ZIP)": "City is blank; a valid ZIP is available for matching.",
    "Missing city (no valid ZIP)": "City and a valid ZIP are both missing.",
    "Empty street line": "No street address was supplied.",
    "Missing house number": "Street address does not begin with a house number.",
    "PO Box": "A PO Box does not identify a building location.",
    "State not a valid US code": "State is not a recognized US state or territory code.",
    "Duplicate IDs": "The same policy identifier appears more than once.",
    "Duplicate addresses (normalized)": "Several records share the same normalized address; this can be legitimate.",
}


def friendly_label(value: str) -> str:
    """Convert internal field and status names to labels suitable for UI display."""
    if value in _FRIENDLY_LABELS:
        return _FRIENDLY_LABELS[value]
    return value.replace("_", " ").strip().capitalize()


def display_preflight_rows(
    checks: Iterable[dict[str, int | float | str]],
) -> list[dict[str, str | int | float]]:
    """Format aggregate pre-flight data for a human-readable table."""
    group_rank = {group: index for index, group in enumerate(_GROUP_ORDER)}
    return sorted(
        [
            {
                "Check": friendly_label(str(row["check"])),
                "Group": str(row.get("group", "All records")),
                "What it means": str(row.get("meaning", "")),
                "Records with this issue": int(row["count"]),
                "Out of": int(row["denominator"]),
                "Percent": float(row["percentage"]),
            }
            for row in checks
            if row.get("kind") != "group_summary"
        ],
        key=lambda row: (
            group_rank.get(row["Group"], -1),
            row["Check"],
        ),
    )


def preflight_group_summaries(
    checks: Iterable[dict[str, int | float | str]],
) -> list[dict[str, int | float | str]]:
    return [row for row in checks if row.get("kind") == "group_summary"]


def fraction_value(value: str) -> tuple[str, str]:
    """Split an aggregate fraction into a compact KPI value and percentage."""
    fraction, separator, percentage = value.partition(" ")
    if "/" not in fraction:
        return value, ""
    return fraction, percentage.strip("()") if separator else ""


def format_confidence(value: Any) -> str:
    """Format a probability-like confidence as a whole percentage."""
    if value in ("", None):
        return "n/a"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    percentage = numeric * 100 if 0 <= numeric <= 1 else numeric
    return f"{percentage:.0f}%"


def enrichment_help_texts() -> dict[str, str]:
    descriptions = {
        "flood_by_location": ("Flood zone and risk", "Flood-zone code and flood-risk level."),
        "distance_to_flood_hazard_by_location": (
            "Distance to flood hazard",
            "Distance from the address to the nearest mapped flood hazard.",
        ),
        "property_attributes_v2": ("Property attributes", "Available building attributes such as year built."),
        "preciselyid_by_address": ("PreciselyID", "A Precisely location identifier for the verified address."),
    }
    return {
        operation: (
            f"{description} Price: {PRICE_TABLE[operation]:,.0f} credits per verified policy."
        )
        for operation, (_, description) in descriptions.items()
    }


def cost_breakdown(
    profile_data: dict[str, Any],
    policies: int,
    verified_share: float,
) -> dict[str, Any]:
    """Return user-readable item estimates using the shared verified rate table."""
    profile = ClientProfile(
        path=PROJECT_ROOT / "profile-in-memory.yaml",
        project_root=PROJECT_ROOT,
        data=profile_data,
    )
    displayed_calls = (
        Decimal(policies) * Decimal(str(verified_share))
    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    operation_labels = {
        "flood_by_location": "Flood zone and risk",
        "distance_to_flood_hazard_by_location": "Distance to flood hazard",
        "property_attributes_v2": "Property attributes",
        "preciselyid_by_address": "PreciselyID",
    }
    rows = []
    base_calls = Decimal(policies)
    base_rate = PRICE_TABLE["geocode_premium"]
    rows.append(
        {
            "Item": "Address verification",
            "Applies to": "Every policy",
            "Calls": base_calls,
            "Credits each": base_rate,
            "Credits": base_calls * base_rate,
        }
    )
    enabled_rate = Decimal("0")
    for operation in profile.enabled_operations:
        rate = PRICE_TABLE[operation]
        enabled_rate += rate
        rows.append(
            {
                "Item": operation_labels[operation],
                "Applies to": "Verified policies only",
                "Calls": displayed_calls,
                "Credits each": rate,
                "Credits": displayed_calls * rate,
            }
        )
    total_credits = sum(
        (row["Credits"] for row in rows),
        start=Decimal("0"),
    )
    rows.append(
        {
            "Item": "Total",
            "Applies to": "",
            "Calls": "",
            "Credits each": "",
            "Credits": total_credits,
        }
    )
    return {
        "rows": rows,
        "total_credits": total_credits,
        "credits_per_policy": total_credits / Decimal(policies) if policies else Decimal("0"),
        "enabled_item_rate": enabled_rate,
    }


def _normalize_header(value: str) -> str:
    return _NON_ALNUM.sub("", value.casefold())


def suggest_mapping(columns: Iterable[str]) -> dict[str, str | None]:
    """Suggest one-to-one mappings from client headers to canonical fields."""
    column_list = list(columns)
    normalized = {_normalize_header(column): column for column in column_list}
    available = set(column_list)
    parts: dict[str, str] = {}
    for column in column_list:
        if "/" in column:
            for part in column.split("/"):
                parts.setdefault(_normalize_header(part), column)
    mapping: dict[str, str | None] = {}
    for canonical in CANONICAL_COLUMNS:
        candidates = [canonical.replace("_", " "), *_ALIASES[canonical]]
        selected = next(
            (
                normalized[_normalize_header(candidate)]
                for candidate in candidates
                if _normalize_header(candidate) in normalized
                and normalized[_normalize_header(candidate)] in available
            ),
            None,
        )
        if selected is None:
            selected = next(
                (
                    parts[_normalize_header(candidate)]
                    for candidate in candidates
                    if parts.get(_normalize_header(candidate)) in available
                ),
                None,
            )
        if selected is None:
            scored: list[tuple[float, str]] = []
            for candidate in candidates:
                matches = difflib.get_close_matches(
                    _normalize_header(candidate),
                    [_normalize_header(name) for name in available],
                    n=1,
                    cutoff=0.78,
                )
                if matches:
                    name = normalized[matches[0]]
                    score = difflib.SequenceMatcher(
                        None,
                        _normalize_header(candidate),
                        matches[0],
                    ).ratio()
                    scored.append((score, name))
            if scored:
                selected = max(scored)[1]
        mapping[canonical] = selected
        if selected is not None:
            available.remove(selected)
    return mapping


def _value(row: dict[str, Any], mapping: dict[str, str | None], canonical: str) -> str:
    column = mapping.get(canonical)
    value = row.get(column, "") if column else ""
    return "" if value is None else str(value).strip()


def _normalize_address(value: str) -> str:
    return _NON_ALNUM.sub("", value.casefold())


def _valid_zip(value: str) -> bool:
    return bool(_ZIP.fullmatch(value))


def preflight_checks(
    rows: Iterable[dict[str, Any]],
    mapping: dict[str, str | None],
) -> list[dict[str, int | float | str]]:
    """Return file-level issue counts only; no row values are included."""
    records = list(rows)
    duplicate_ids: set[str] = set()
    duplicate_addresses: set[str] = set()
    id_counts: dict[str, int] = {}
    address_counts: dict[str, int] = {}
    issues_by_row: list[set[str]] = [set() for _ in records]

    addresses = []
    identifiers = []
    for index, row in enumerate(records):
        identifier = _normalize_address(_value(row, mapping, "policy_id"))
        address = "|".join(
            _normalize_address(_value(row, mapping, field))
            for field in ("address_line1", "address_line2", "city", "state", "zip5")
        )
        identifiers.append(identifier)
        addresses.append(address)
        if identifier:
            id_counts[identifier] = id_counts.get(identifier, 0) + 1
        if address.strip("|"):
            address_counts[address] = address_counts.get(address, 0) + 1

    duplicate_ids = {key for key, count in id_counts.items() if count > 1}
    duplicate_addresses = {
        key for key, count in address_counts.items() if count > 1
    }

    per_check = {check: 0 for check in _CHECK_GROUPS}
    group_record_ids = {name: set() for name in _GROUP_ORDER}
    for index, row in enumerate(records):
        street = _value(row, mapping, "address_line1")
        zip_code = _value(row, mapping, "zip5")
        state_raw = _value(row, mapping, "state")
        state = state_raw.upper()
        normalized_state = state_raw.strip().casefold()
        has_valid_zip = _valid_zip(zip_code)
        identifier = identifiers[index]
        address = addresses[index]
        row_issues = set()
        if not street:
            row_issues.add("Empty street line")
        is_po_box = bool(street and _PO_BOX.search(street))
        if street and not _HOUSE_NUMBER.match(street) and not is_po_box:
            row_issues.add("Missing house number")
        if not zip_code:
            row_issues.add("Missing ZIP")
        elif not has_valid_zip:
            row_issues.add("ZIP format invalid")
        if (
            len(zip_code) == 4
            and zip_code.isdigit()
            and state in _LEADING_ZERO_ZIP_STATES
        ):
            row_issues.discard("ZIP format invalid")
            row_issues.add("ZIP lost its leading zero")
        if not _value(row, mapping, "city"):
            row_issues.add(
                "Missing city (valid ZIP)" if has_valid_zip
                else "Missing city (no valid ZIP)"
            )
        if normalized_state in {name.casefold() for name in STATE_NAMES}:
            row_issues.add("State written in full")
        elif state not in VALID_STATES:
            row_issues.add("State not a valid US code")
        if is_po_box:
            row_issues.add("PO Box")
        if address in duplicate_addresses:
            row_issues.add("Duplicate addresses (normalized)")
        if identifier and identifier in duplicate_ids:
            row_issues.add("Duplicate IDs")
        issues_by_row[index] = row_issues
        for issue in row_issues:
            per_check[issue] += 1
        row_groups = {_CHECK_GROUPS[issue] for issue in row_issues}
        if row_groups:
            if "Needs client follow-up" in row_groups:
                group = "Needs client follow-up"
            elif "Precisely usually fixes" in row_groups:
                group = "Precisely usually fixes"
            else:
                group = "Review"
            group_record_ids[group].add(index)
        else:
            group_record_ids["No issues found"].add(index)

    result: list[dict[str, int | float | str]] = [
        {
            "check": "Records",
            "count": len(records),
            "denominator": len(records),
            "percentage": 100.0 if records else 0.0,
            "group": "Total",
            "meaning": "Number of records in the uploaded file.",
        }
    ]
    for check, count in per_check.items():
        result.append(
            {
                "check": check,
                "count": count,
                "denominator": len(records),
                "percentage": round(100 * count / len(records), 1)
                if records
                else 0.0,
                "group": _CHECK_GROUPS[check],
                "meaning": _CHECK_MEANINGS[check],
            }
        )
    group_percentages = {
        group: round(100 * len(indexes) / len(records), 1) if records else 0.0
        for group, indexes in group_record_ids.items()
    }
    if records:
        largest_group = max(_GROUP_ORDER, key=lambda group: len(group_record_ids[group]))
        group_percentages[largest_group] = round(
            100 - sum(
                percentage
                for group, percentage in group_percentages.items()
                if group != largest_group
            ),
            1,
        )
    for group, indexes in group_record_ids.items():
        count = len(indexes)
        result.append(
            {
                "check": group,
                "count": count,
                "denominator": len(records),
                "percentage": group_percentages[group],
                "group": group,
                "meaning": "",
                "kind": "group_summary",
            }
        )
    return result


def unused_columns(
    columns: Iterable[str], mapping: dict[str, str | None]
) -> list[str]:
    used = {value for value in mapping.values() if value is not None}
    return [column for column in columns if column not in used]


def missing_required_message(mapping: dict[str, str | None]) -> str | None:
    """Plain-language prompt naming required fields that have no column yet."""
    names = {
        "policy_id": "Policy ID",
        "address_line1": "Street address",
        "city": "City",
        "state": "State",
        "zip5": "ZIP code",
    }
    missing = [names[f] for f in REQUIRED_COLUMNS if mapping.get(f) is None]
    if not missing:
        return None
    return f"Choose the column for: {', '.join(missing)}."


def build_profile(
    client_name: str,
    slug: str,
    mapping: dict[str, str | None],
    enrichment_choices: dict[str, bool] | Iterable[str] | None = None,
    ai_flags: dict[str, bool] | None = None,
) -> dict[str, Any]:
    enabled = (
        {name for name, selected in enrichment_choices.items() if selected}
        if isinstance(enrichment_choices, dict)
        else set(enrichment_choices or ())
    )
    unknown_operations = enabled - set(ENRICHMENT_OPERATIONS)
    if unknown_operations:
        raise ValueError(f"Unknown enrichment choice(s): {', '.join(sorted(unknown_operations))}.")
    if any(mapping.get(field) is None for field in REQUIRED_COLUMNS):
        raise ValueError("Required mappings must be selected before creating a profile.")
    ai = ai_flags or {}
    return {
        "client": {"name": client_name.strip(), "country": "US"},
        "input": {
            "path": f"data/clients/{slug}/input.csv",
            "id_column": mapping["policy_id"],
            "column_mapping": {field: mapping.get(field) for field in CANONICAL_COLUMNS},
        },
        "output": {
            "run_dir": f"data/output/{slug}",
            "reports_dir": f"data/output/{slug}/reports",
        },
        "verification": {"eligible_precision_prefixes": ["S5", "S7", "S8"]},
        "enrichment": {
            "operations": [
                {
                    "name": operation,
                    "enabled": operation in enabled,
                    **({"attributes": ["builtYear"]} if operation == "property_attributes_v2" else {}),
                }
                for operation in ENRICHMENT_OPERATIONS
                if operation != "preciselyid_by_address"
            ],
            "preciselyid_by_address": {
                "enabled": "preciselyid_by_address" in enabled
            },
        },
        "comparison": {"enabled": False, "sample_size": 40, "seed": 20261029},
        "ai": {
            "narratives": {"enabled": bool(ai.get("narratives", False))},
            "remediation": {
                "enabled": bool(ai.get("remediation", False)),
                "samples": 5,
                "conformal_alpha": 0.10,
            },
        },
        "data_handling": {
            "publish_row_level": False,
            "retention_days": 30,
            "note": (
                "Precisely APIs AUP (April 2021) §4.1–4.2: no public row-level "
                "display; temporary secure storage only."
            ),
        },
    }


def serialize_profile(profile: dict[str, Any]) -> str:
    return yaml.safe_dump(profile, sort_keys=False, allow_unicode=True)


def _excel_cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def read_excel_workbook(
    content: bytes,
    sheet_name: str | None = None,
) -> tuple[list[str], list[dict[str, str]], list[str]]:
    """Read one worksheet as text rows and return the workbook's sheet names."""
    try:
        workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    except (BadZipFile, InvalidFileException, OSError, EOFError) as error:
        raise ValueError(
            "This Excel file could not be opened. Check that it is a valid .xlsx file."
        ) from error
    try:
        sheet_names = list(workbook.sheetnames)
        if not sheet_names:
            raise ValueError("The Excel file does not contain a worksheet.")
        selected_sheet = sheet_name or sheet_names[0]
        if selected_sheet not in sheet_names:
            raise ValueError("Choose a sheet from the uploaded Excel file.")
        worksheet = workbook[selected_sheet]
        values = worksheet.iter_rows(values_only=True)
        header_values = next(values, None)
        if header_values is None:
            raise ValueError("The selected Excel sheet must include a header row.")
        columns = [_excel_cell_text(value) for value in header_values]
        rows = []
        for values_row in values:
            cell_values = list(values_row)
            cell_values.extend([None] * (len(columns) - len(cell_values)))
            rows.append(
                {
                    column: _excel_cell_text(cell_values[index])
                    for index, column in enumerate(columns)
                }
            )
        return columns, rows, sheet_names
    finally:
        workbook.close()


def serialize_input_csv(
    columns: Iterable[str],
    rows: Iterable[dict[str, Any]],
) -> bytes:
    """Serialize parsed client rows as a UTF-8 CSV pipeline input."""
    fieldnames = list(columns)
    output = StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=fieldnames,
        extrasaction="ignore",
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def estimate_profile_cost(
    profile: dict[str, Any], records: int, eligible_share: float
) -> dict[str, Any]:
    """Delegate all price arithmetic to the shared client-profile estimator."""
    client_profile = ClientProfile(
        path=PROJECT_ROOT / "profile-in-memory.yaml",
        project_root=PROJECT_ROOT,
        data=profile,
    )
    return estimate_cost(client_profile, records, eligible_share)


def _validate_slug(slug: str) -> None:
    if not _SLUG.fullmatch(slug) or slug == "run":
        raise ValueError("Slug must contain lowercase letters, digits, or hyphens; 'run' is reserved.")


def _mapped_columns_only(csv_bytes: bytes, profile: dict[str, Any]) -> bytes:
    """Keep only mapped columns, with original header names in mapping order."""
    column_mapping = profile.get("input", {}).get("column_mapping", {})
    wanted: list[str] = []
    for field in CANONICAL_COLUMNS:
        name = column_mapping.get(field)
        if name and name not in wanted:
            wanted.append(name)
    reader = csv.DictReader(StringIO(csv_bytes.decode("utf-8-sig"), newline=""))
    if not wanted or not reader.fieldnames or any(
        name not in reader.fieldnames for name in wanted
    ):
        return csv_bytes
    return serialize_input_csv(wanted, reader)


def save_client_bundle(
    slug: str,
    csv_bytes: bytes,
    profile: dict[str, Any],
    *,
    overwrite: bool = False,
    project_root: str | Path = PROJECT_ROOT,
) -> tuple[Path, Path]:
    """Save an uploaded CSV/profile under data/clients and validate the bundle."""
    _validate_slug(slug)
    root = Path(project_root).resolve()
    clients_root = (root / "data" / "clients").resolve()
    folder = clients_root / slug
    try:
        folder.resolve().relative_to(clients_root)
    except ValueError:
        raise ValueError("Client bundle must be written inside data/clients/.") from None
    csv_bytes = _mapped_columns_only(csv_bytes, profile)
    if folder.exists() and not overwrite:
        raise FileExistsError(f"Client folder already exists: {folder}")
    if folder.exists() and not folder.is_dir():
        raise FileExistsError(f"Client bundle path is not a folder: {folder}")
    input_path = folder / "input.csv"
    profile_path = folder / "client_profile.yaml"
    previous = {
        path: path.read_bytes()
        for path in (input_path, profile_path)
        if path.is_file()
    }
    created: list[Path] = []
    folder.mkdir(parents=True, exist_ok=True)
    try:
        created.append(input_path)
        input_path.write_bytes(csv_bytes)
        created.append(profile_path)
        profile_path.write_text(serialize_profile(profile), encoding="utf-8")
        load_client_profile(profile_path, project_root=root)
    except Exception:
        for path in created:
            if path in previous:
                path.write_bytes(previous[path])
            else:
                path.unlink(missing_ok=True)
        if not previous and folder.exists() and not any(folder.iterdir()):
            folder.rmdir()
        raise
    return input_path, profile_path
