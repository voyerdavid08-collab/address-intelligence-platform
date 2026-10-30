"""Build public and private before/after Excel workbooks."""

import argparse
import csv
from pathlib import Path
from typing import Any, Iterable

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from address_intelligence.metrics import _fully_correct, _is_correct_building


PROJECT_ROOT = Path(__file__).resolve().parents[2]
INPUT_PATH = PROJECT_ROOT / "data" / "input" / "policyholders_synthetic.csv"
GROUND_TRUTH_PATH = PROJECT_ROOT / "data" / "input" / "ground_truth.csv"
RESULTS_PATH = PROJECT_ROOT / "data" / "output" / "run" / "results_rowlevel.csv"
SUMMARY_PATH = PROJECT_ROOT / "reports" / "before_after_summary.csv"
PUBLIC_PATH = PROJECT_ROOT / "reports" / "before_after_workbook.xlsx"
PRIVATE_PATH = PROJECT_ROOT / "data" / "output" / "run" / "before_after_workbook_PRIVATE.xlsx"
DATA_OUTPUT_ROOT = PROJECT_ROOT / "data" / "output"
SHEET_NAMES = ("About", "Raw", "Validated", "Enriched", "Summary stats")
PUBLIC_BANNER = (
    "MADE-UP EXAMPLE VALUES - NOT PRECISELY OUTPUT. Full results: see "
    "before_after_workbook_PRIVATE.xlsx, shared privately with the submission "
    "per the Precisely APIs Acceptable Use Policy §4.1–4.2."
)
PUBLIC_LICENSE = (
    "Validated and Enriched tabs show made-up example rows, not Precisely output. "
    "Raw and Summary stats tabs are complete in both versions.",
    "The complete version of this workbook, with all 200 validated and enriched "
    "records, was shared privately with the Precisely review team with the "
    "submission (file: before_after_workbook_PRIVATE.xlsx).",
    "Reason: the Precisely APIs Acceptable Use Policy (April 2021) does not permit "
    "public display of API results or building a database from them (§4.1), and "
    "limits storage to temporary, secure use (§4.2). Row-level results are "
    "therefore not published; only aggregate statistics are.",
)
PRIVATE_LICENSE = (
    "Confidential: contains Precisely API Content (Precisely APIs Acceptable Use "
    "Policy, April 2021, §4.1–4.2).",
    "Shared only with the Precisely review team for assignment evaluation. Not "
    "published; local copies are stored securely and deleted by 2026-11-27 "
    "(within 30 days of retrieval).",
    "The public version of this workbook in the repository contains the same "
    "tabs, with made-up example rows in Validated and Enriched.",
)
VALIDATED_COLUMNS = (
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
    "geocode_status",
    "correct_building",
    "fully_correct",
)
ENRICHED_COLUMNS = (
    "policy_id",
    "flood_zone_code",
    "flood_risk_level",
    "bfe",
    "bfe_unit",
    "distance_to_water",
    "distance_to_water_unit",
    "water_body_name",
    "built_year",
    "comparison_flood_zone_code",
    "comparison_flood_zones_differ",
    "comparison_distance_km",
)
TAB_DESCRIPTIONS = (
    ("About", "Workbook purpose, tab descriptions, definitions, and data licensing."),
    ("Raw", "The complete synthetic policyholder input file."),
    ("Validated", "Standardized address and validation fields."),
    ("Enriched", "Location enrichment and coarse-versus-verified comparison fields."),
    ("Summary stats", "Aggregate rows from reports/before_after_summary.csv."),
)
_HEADER_FILL = PatternFill(fill_type="solid", fgColor="0B2341")
_HEADER_FONT = Font(color="FFFFFF", bold=True)


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header: {path}")
        return list(reader.fieldnames), list(reader)


def _about_rows(private: bool) -> list[tuple[str, str]]:
    license_lines = PRIVATE_LICENSE if private else PUBLIC_LICENSE
    rows = [
        ("Title", "Address Intelligence: before/after data file"),
        *TAB_DESCRIPTIONS,
        ("Definition", "Eligible = Precisely precision S5/S7/S8"),
        ("Definition", "Correct building = street line + ZIP match the reference, unit ignored"),
        ("Data licensing", ""),
    ]
    rows.extend((f"Data licensing {index}", text) for index, text in enumerate(license_lines, start=1))
    return rows


def _public_examples(columns: Iterable[str], *, enriched: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(1, 4):
        row: dict[str, Any] = {column: "" for column in columns}
        row["policy_id"] = f"EXAMPLE-{index}"
        if enriched:
            row.update(
                {
                    "flood_zone_code": "EXAMPLE",
                    "flood_risk_level": "EXAMPLE",
                    "bfe": "EXAMPLE",
                    "bfe_unit": "EXAMPLE",
                    "distance_to_water": "EXAMPLE",
                    "distance_to_water_unit": "EXAMPLE",
                    "water_body_name": "EXAMPLE",
                    "built_year": "EXAMPLE",
                    "comparison_flood_zone_code": "EXAMPLE",
                    "comparison_flood_zones_differ": "EXAMPLE",
                    "comparison_distance_km": "EXAMPLE",
                }
            )
        else:
            row.update(
                {
                    "standardized_address_line1": f"123 EXAMPLE ST UNIT {index}",
                    "standardized_address_line2": f"UNIT {index}",
                    "standardized_city": "SAMPLE CITY",
                    "standardized_state": "EX",
                    "standardized_zip5": "00000",
                    "precision_code": "EXAMPLE",
                    "eligibility": "EXAMPLE",
                    "manual_review_reason": "EXAMPLE",
                    "latitude": 0.0,
                    "longitude": 0.0,
                    "preciselyid": "EXAMPLE-ID",
                    "geocode_status": "EXAMPLE",
                    "correct_building": "N",
                    "fully_correct": "N",
                }
            )
        rows.append(row)
    return rows


def _validate_public_examples(
    validated_rows: list[dict[str, Any]],
    enriched_rows: list[dict[str, Any]],
) -> None:
    for row in (*validated_rows, *enriched_rows):
        if not str(row.get("policy_id", "")).startswith("EXAMPLE-"):
            raise ValueError("Public workbook rows must use EXAMPLE- policy IDs.")
    for row in validated_rows:
        if float(row.get("latitude") or 0) != 0 or float(row.get("longitude") or 0) != 0:
            raise ValueError("Public workbook coordinates must be the made-up zero values.")


def _private_rows(
    raw_rows: list[dict[str, str]],
    results: list[dict[str, str]],
    truths: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    result_by_id = {row["policy_id"]: row for row in results}
    truth_by_id = {row["policy_id"]: row for row in truths}
    validated: list[dict[str, Any]] = []
    enriched: list[dict[str, Any]] = []
    for policy in raw_rows:
        policy_id = policy["policy_id"]
        result = result_by_id.get(policy_id, {})
        truth = truth_by_id.get(policy_id, {})
        line1 = result.get("standardized_address_line1", "")
        line2 = result.get("standardized_address_line2", "")
        zip5 = result.get("standardized_zip5", "")
        validated.append(
            {
                column: (
                    "Y"
                    if _is_correct_building(line1, zip5, truth)
                    else "N"
                )
                if column == "correct_building"
                else (
                    "Y"
                    if _fully_correct(line1, line2, zip5, truth)
                    else "N"
                )
                if column == "fully_correct"
                else result.get(column, "")
                for column in VALIDATED_COLUMNS
                if column != "policy_id"
            }
            | {"policy_id": policy_id}
        )
        enriched.append(
            {
                "policy_id": policy_id,
                **{column: result.get(column, "") for column in ENRICHED_COLUMNS if column != "policy_id"},
            }
        )
    return validated, enriched


def _append_table(
    sheet: Any,
    columns: list[str] | tuple[str, ...],
    rows: Iterable[dict[str, Any]],
    *,
    banner: str | None = None,
) -> None:
    header_row = 1
    if banner is not None:
        header_row = 2
        sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(columns))
        cell = sheet.cell(row=1, column=1, value=banner)
        cell.font = Font(italic=True)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        sheet.row_dimensions[1].height = 42
    for column_index, column in enumerate(columns, start=1):
        cell = sheet.cell(row=header_row, column=column_index, value=column)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(wrap_text=True, vertical="center")
    for row_index, record in enumerate(rows, start=header_row + 1):
        for column_index, column in enumerate(columns, start=1):
            value = record.get(column, "")
            cell = sheet.cell(row=row_index, column=column_index, value=value)
            if isinstance(value, str) and value.startswith("="):
                cell.data_type = "s"
    sheet.freeze_panes = f"A{header_row + 1}"
    end_row = max(header_row, sheet.max_row)
    sheet.auto_filter.ref = f"A{header_row}:{get_column_letter(len(columns))}{end_row}"
    sheet.row_dimensions[header_row].height = 32
    for column_index, column in enumerate(columns, start=1):
        values = [str(column)]
        values.extend(
            str(sheet.cell(row=row_index, column=column_index).value or "")
            for row_index in range(header_row + 1, sheet.max_row + 1)
        )
        width = min(max(max(map(len, values), default=0) + 2, 12), 42)
        sheet.column_dimensions[get_column_letter(column_index)].width = width
    sheet.sheet_view.showGridLines = False


def _write_workbook(
    path: Path,
    *,
    private: bool,
    raw_columns: list[str],
    raw_rows: list[dict[str, str]],
    summary_columns: list[str],
    summary_rows: list[dict[str, str]],
    validated_rows: list[dict[str, Any]],
    enriched_rows: list[dict[str, Any]],
) -> dict[str, int]:
    if not private:
        _validate_public_examples(validated_rows, enriched_rows)
    workbook = Workbook()
    about = workbook.active
    about.title = "About"
    _append_table(
        about,
        ("Item", "Details"),
        [
            {"Item": item, "Details": details}
            for item, details in _about_rows(private)
        ],
    )
    raw = workbook.create_sheet("Raw")
    _append_table(raw, raw_columns, raw_rows)
    validated = workbook.create_sheet("Validated")
    _append_table(
        validated,
        VALIDATED_COLUMNS,
        validated_rows,
        banner=None if private else PUBLIC_BANNER,
    )
    enriched = workbook.create_sheet("Enriched")
    _append_table(
        enriched,
        ENRICHED_COLUMNS,
        enriched_rows,
        banner=None if private else PUBLIC_BANNER,
    )
    summary = workbook.create_sheet("Summary stats")
    _append_table(summary, summary_columns, summary_rows)
    workbook.properties.title = "Address Intelligence: before/after data file"
    workbook.properties.subject = "Before/after address intelligence workbook"
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    counts = {
        "About": len(_about_rows(private)),
        "Raw": len(raw_rows),
        "Validated": len(validated_rows),
        "Enriched": len(enriched_rows),
        "Summary stats": len(summary_rows),
    }
    if private:
        counts["correct_building_Y"] = sum(
            row.get("correct_building") == "Y" for row in validated_rows
        )
        counts["fully_correct_Y"] = sum(
            row.get("fully_correct") == "Y" for row in validated_rows
        )
    return counts


def build_workbooks(
    *,
    input_path: Path = INPUT_PATH,
    ground_truth_path: Path = GROUND_TRUTH_PATH,
    results_path: Path = RESULTS_PATH,
    summary_path: Path = SUMMARY_PATH,
    public_path: Path = PUBLIC_PATH,
    private_path: Path = PRIVATE_PATH,
    data_output_root: Path = DATA_OUTPUT_ROOT,
    public_only: bool = False,
) -> dict[str, dict[str, int] | None]:
    if not public_only and results_path.exists():
        try:
            private_path.resolve().relative_to(data_output_root.resolve())
        except ValueError as exc:
            raise ValueError(
                "Private workbook output must be within data/output/."
            ) from exc
    raw_columns, raw_rows = _read_csv(input_path)
    summary_columns, summary_rows = _read_csv(summary_path)
    public_validated = _public_examples(VALIDATED_COLUMNS, enriched=False)
    public_enriched = _public_examples(ENRICHED_COLUMNS, enriched=True)
    result: dict[str, dict[str, int] | None] = {
        "PUBLIC": _write_workbook(
            public_path,
            private=False,
            raw_columns=raw_columns,
            raw_rows=raw_rows,
            summary_columns=summary_columns,
            summary_rows=summary_rows,
            validated_rows=public_validated,
            enriched_rows=public_enriched,
        ),
        "PRIVATE": None,
    }
    if public_only:
        return result
    if not results_path.exists():
        print(
            f"PRIVATE workbook skipped: local results file is missing ({results_path})."
        )
        return result
    _, truth_rows = _read_csv(ground_truth_path)
    _, result_rows = _read_csv(results_path)
    private_validated, private_enriched = _private_rows(raw_rows, result_rows, truth_rows)
    result["PRIVATE"] = _write_workbook(
        private_path,
        private=True,
        raw_columns=raw_columns,
        raw_rows=raw_rows,
        summary_columns=summary_columns,
        summary_rows=summary_rows,
        validated_rows=private_validated,
        enriched_rows=private_enriched,
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build public and private before/after workbooks.")
    parser.add_argument("--public-only", action="store_true")
    args = parser.parse_args(argv)
    counts = build_workbooks(public_only=args.public_only)
    for version, tab_counts in counts.items():
        if tab_counts is None:
            continue
        path = PUBLIC_PATH if version == "PUBLIC" else PRIVATE_PATH
        print(f"{version}: {path}")
        print(f"Tabs: {', '.join(SHEET_NAMES)}")
        for tab in SHEET_NAMES:
            print(f"  {tab}: {tab_counts[tab]} data rows")
        if version == "PRIVATE":
            print(f"  correct_building Y: {tab_counts['correct_building_Y']}")
            print(f"  fully_correct Y: {tab_counts['fully_correct_Y']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
