"""Read-only local data access for the Streamlit results and exceptions tabs."""

import csv
from decimal import Decimal
from io import BytesIO
import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from address_intelligence.onboarding import format_confidence, friendly_label

_DEFECT_LABELS = {
    "city_typo": "City typo",
    "missing_house_number": "Missing house number",
    "missing_unit": "Missing unit",
    "missing_zip": "Missing ZIP",
    "nonexistent_address": "Non-existent address",
    "street_typo": "Street typo",
    "wrong_zip": "Wrong ZIP",
}
_DEFECT_ORDER = tuple(_DEFECT_LABELS)
EXCEPTION_EXPORT_COLUMNS = (
    ("Policy ID", "policy_id"),
    ("Manual review reason", "manual_review_reason"),
    ("Match level", "precision_code"),
    ("AI cause", "ai_cause_set"),
    ("Confidence", "confidence"),
    ("Next action", "next_action"),
    ("Suggestion status", "suggestion_status"),
    ("Verification status", "verification_status"),
)
_REVIEW_REASON_LABELS = {
    "street-level only": "Matched to the street, not the building",
    "postal or city level": "Matched to the ZIP or city only",
    "location conflict (state mismatch)": "State does not match the address",
    "po box": "PO Box cannot identify a building",
    "invalid state": "State is not recognized",
    "state conflict": "State does not match the address",
}


def _match_level(value: Any) -> str:
    tokens = [token.strip().upper()[:2] for token in str(value).split(",") if token.strip()]
    code = ", ".join(tokens)
    if code.startswith(("S5", "S7", "S8")):
        return f"Building ({code})"
    if code.startswith("S4"):
        return f"Street ({code})"
    if code.startswith(("Z", "G")):
        return f"ZIP/city ({code})"
    return code


_PLAIN_FLAG_REASONS = {
    "street-level only": "Precisely found the street but not the building (no house number match).",
    "postal or city level": "Precisely could only match the ZIP code or city.",
    "location conflict (state mismatch)": "The state does not match the rest of the address.",
    "state conflict": "The state does not match the rest of the address.",
}


def plain_flag_reason(value: Any) -> str:
    reason = str(value).strip()
    return _PLAIN_FLAG_REASONS.get(reason.casefold(), _review_reason(reason) if reason else "")


def _review_reason(value: Any) -> str:
    reason = str(value).strip()
    return _REVIEW_REASON_LABELS.get(
        reason.casefold(),
        friendly_label(reason),
    )


def _verification_status(value: Any) -> str:
    status = str(value).strip().casefold()
    return {
        "verified by precisely": "Confirmed",
        "verified correct": "Confirmed",
        "did not verify": "Not verified",
        "not_started": "Not started",
        "not started": "Not started",
        "rejected": "Rejected",
        "verified incorrect": "Rejected",
    }.get(status, friendly_label(str(value)))


def read_summary_csv(path: str | Path) -> list[dict[str, str]]:
    summary_path = Path(path)
    if not summary_path.is_file():
        return []
    with summary_path.open(newline="", encoding="utf-8-sig") as source:
        return list(csv.DictReader(source))


def load_summary_kpis(path: str | Path) -> dict[str, Any] | None:
    rows = read_summary_csv(path)
    if not rows:
        return None
    metrics = {row.get("metric", ""): row for row in rows}

    def value(metric: str, column: str) -> str:
        return metrics.get(metric, {}).get(column, "n/a") or "n/a"

    defects = []
    for metric, row in metrics.items():
        prefix = "Correct building: defect: "
        if metric.startswith(prefix):
            defects.append(
                {
                    "defect": metric[len(prefix):],
                    "before": row.get("before", "n/a"),
                    "after": row.get("after", "n/a"),
                }
            )
    return {
        "has_reference": any(
            "ground truth" in row.get("section", "").casefold()
            or "ground_truth" in row.get("section", "").casefold()
            for row in rows
        ),
        "correct_building": {
            "label": "Right address",
            "before": value("Correct building: all records", "before"),
            "after": value("Correct building: all records", "after"),
        },
        "dirty_fully_correct": {
            "label": "Flawed records fully corrected",
            "before": value("Fully correct (incl. unit): dirty records", "before"),
            "after": value("Fully correct (incl. unit): dirty records", "after"),
        },
        "flood_tier_changes": {
            "label": "Flood-risk tier changes from city-level location",
            "value": value("Flood-risk tier changed", "after"),
        },
        "ai_verified": {
            "label": "AI fixes verified",
            "verified": value("Precisely-verified", "numerator"),
            "correct": value("Verified correct", "numerator"),
            "incorrect": value("Verified incorrect", "numerator"),
        },
        "defects": sorted(defects, key=lambda row: row["defect"]),
    }


def load_fallback_kpis(path: str | Path) -> list[dict[str, str]]:
    rows = read_summary_csv(path)
    metrics = {row.get("metric", ""): row for row in rows}
    definitions = (
        ("Located automatically", "Eligible"),
        ("Enriched with flood data", "flood_zone_code over all input"),
        ("Sent to manual review", "Manual review"),
        ("AI suggestions confirmed by Precisely", "Precisely-verified"),
    )
    fallback = []
    for label, metric_name in definitions:
        row = metrics.get(metric_name, {})
        fallback.append(
            {
                "label": label,
                "value": row.get("after") or row.get("numerator") or "n/a",
            }
        )
    return fallback


def defect_chart_rows(kpis: dict[str, Any]) -> list[dict[str, str | int]]:
    """Prepare ordered, grouped-bar chart data from aggregate KPI rows."""
    indexed = {row["defect"]: row for row in kpis["defects"]}
    chart_rows = []
    for defect in _DEFECT_ORDER:
        row = indexed.get(defect)
        if row is None:
            continue
        try:
            before = int(row["before"].split("/", 1)[0])
            after = int(row["after"].split("/", 1)[0])
        except (ValueError, AttributeError):
            continue
        chart_rows.append(
            {
                "Defect": _DEFECT_LABELS[defect],
                "Before": before,
                "After": after,
            }
        )
    return chart_rows


def defect_chart_series(kpis: dict[str, Any]) -> list[dict[str, str | int]]:
    """Return one chart row per defect and before/after series for grouped bars."""
    return [
        {"Defect": row["Defect"], "Series": series, "Count": row[series]}
        for row in defect_chart_rows(kpis)
        for series in ("Before", "After")
    ]


def defect_chart_data(kpis: dict[str, Any]) -> list[dict[str, str | int]]:
    return [
        {
            **row,
            "Label": str(row["Count"]),
            "Label position": row["Count"],
        }
        for row in defect_chart_series(kpis)
        if int(row["Count"]) > 0
    ]


def zero_category_note(rows: list[dict[str, Any]]) -> str:
    categories = [
        str(row["Defect"])
        for row in rows
        if int(row["Before"]) == 0 and int(row["After"]) == 0
    ]
    if not categories:
        return ""
    if len(categories) == 1:
        category_text = categories[0]
    else:
        category_text = f'{", ".join(categories[:-1])} and {categories[-1]}'
    return (
        f"{category_text}: 0 before and 0 after. "
        "These need client follow-up."
    )


def selected_policy_id(
    queue: list[dict[str, Any]],
    selected_rows: list[int] | tuple[int, ...],
) -> str | None:
    if not selected_rows:
        return None
    index = selected_rows[0]
    if not isinstance(index, int) or index < 0 or index >= len(queue):
        return None
    return str(queue[index].get("policy_id", ""))


def format_exception_table_rows(
    queue: list[dict[str, Any]],
) -> list[dict[str, str]]:
    formatted = []
    for row in queue:
        cause_values = [
            item.strip() for item in str(row.get("ai_cause_set", "")).split(",")
            if item.strip()
        ]
        formatted.append(
            {
                header: (
                    format_confidence(row.get(key))
                    if key == "confidence"
                    else _match_level(row.get(key, ""))
                    if key == "precision_code"
                    else _review_reason(row.get(key, ""))
                    if key == "manual_review_reason"
                    else _verification_status(row.get(key, ""))
                    if key == "verification_status"
                    else format_suggestion_status(row.get(key, ""))
                    if key == "suggestion_status"
                    else ", ".join(friendly_label(item) for item in cause_values)
                    if key == "ai_cause_set"
                    else friendly_label(str(row.get(key, "")))
                    if key == "next_action"
                    else str(row.get(key, ""))
                )
                for header, key in EXCEPTION_EXPORT_COLUMNS
            }
        )
    return formatted


def format_suggestion_status(value: Any) -> str:
    status = str(value).strip().casefold()
    return {
        "suggested, unverified": "AI suggestion made",
        "suggested": "AI suggestion made",
        "ai suggestion made": "AI suggestion made",
        "no_suggestion": "No suggestion",
        "no suggestion": "No suggestion",
        "not_requested": "Not requested",
        "not requested": "Not requested",
        "blocked: house number change": "Blocked: house number change",
        "blocked_house_number_change": "Blocked: house number change",
        "failed": "Failed",
        "error": "Failed",
    }.get(status, friendly_label(str(value)) if status else "Not requested")


def format_cost_breakdown_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    formatted = []
    for row in rows:
        if row["Item"] == "Total":
            continue
        credits_each = row.get("Credits each", "")
        formatted.append(
            {
                "Item": row["Item"],
                "Applies to": row["Applies to"],
                "Calls": int(row["Calls"]) if row.get("Calls", "") != "" else None,
                "Credits each": float(Decimal(str(credits_each))) if credits_each != "" else None,
                "Credits": float(Decimal(str(row["Credits"]))),
            }
        )
    return formatted


def made_up_exception_workbook(queue: list[dict[str, Any]]) -> bytes:
    if any(not str(row.get("policy_id", "")).startswith("EXAMPLE-") for row in queue):
        raise ValueError("Made-up workbook export only accepts EXAMPLE policy IDs.")
    return _exception_workbook_bytes(format_exception_table_rows(queue))


def _exception_workbook_bytes(rows: list[dict[str, str]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Work list"
    headers = [header for header, _ in EXCEPTION_EXPORT_COLUMNS]
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(fill_type="solid", fgColor="0B2341")
    for row in rows:
        sheet.append([row.get(header, "") for header in headers])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column in sheet.columns:
        letter = column[0].column_letter
        sheet.column_dimensions[letter].width = min(
            max(max(len(str(cell.value or "")) for cell in column) + 2, 12),
            42,
        )
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def safe_exception_export_path(
    run_dir: str | Path,
    data_output_root: str | Path,
) -> Path:
    output_root = Path(data_output_root).resolve()
    destination = (Path(run_dir) / "exceptions_work_list.xlsx").resolve()
    try:
        destination.relative_to(output_root)
    except ValueError:
        raise ValueError("Real work lists must be saved inside data/output/.") from None
    return destination


REAL_SAVE_EXPLANATION = (
    "Saved to this client's private results folder instead of Downloads, so "
    "Precisely data stays in one place and is deleted on schedule."
)


def open_folder(folder: str | Path) -> bool:
    """Open a folder in File Explorer on Windows; False where unavailable."""
    import os

    if hasattr(os, "startfile"):
        os.startfile(str(folder))
        return True
    return False


def save_real_exception_workbook(
    queue: list[dict[str, Any]],
    run_dir: str | Path,
    data_output_root: str | Path,
) -> Path:
    destination = safe_exception_export_path(run_dir, data_output_root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(
        _exception_workbook_bytes(format_exception_table_rows(queue))
    )
    return destination


def defect_chart_options() -> dict[str, Any]:
    """Visual options for a side-by-side before/after comparison."""
    return {
        "stack": False,
        "color": ["#9AA5B1", "#8017E1"],
        "y_label": "Records correct",
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as source:
        return list(csv.DictReader(source))


def load_exception_queue(
    run_dir: str | Path,
    *,
    demo_path: str | Path | None = None,
    demo: bool = False,
) -> tuple[list[dict[str, Any]], bool]:
    """Load a local exception queue, or the made-up queue when no run exists."""
    if demo:
        if demo_path is None:
            return [], True
        with Path(demo_path).open(encoding="utf-8") as source:
            return json.load(source), True

    root = Path(run_dir)
    results = _read_csv(root / "results_rowlevel.csv")
    diagnoses = {
        row.get("policy_id", ""): row
        for row in _read_jsonl(root / "remediation_diagnosis.jsonl")
    }
    actions = {
        row.get("policy_id", ""): row
        for row in _read_jsonl(root / "remediation_actions.jsonl")
    }
    narratives = {
        row.get("policy_id", ""): row
        for row in _read_jsonl(root / "narratives.jsonl")
    }
    if not demo and (results or diagnoses or actions):
        ai_off = not diagnoses and not actions
        narrative_text = lambda pid: (
            narratives.get(pid, {}).get("narrative")
            or narratives.get(pid, {}).get("text", "")
        )
        na = "Not available"
        queue = []
        for result in results:
            if result.get("eligibility") != "manual_review":
                continue
            if ai_off:
                queue.append(
                    {
                        "policy_id": result.get("policy_id", ""),
                        "manual_review_reason": result.get("manual_review_reason", ""),
                        "precision_code": result.get("precision_code", ""),
                        "ai_cause_set": na,
                        "confidence": na,
                        "next_action": "analyst_review",
                        "suggestion_status": na,
                        "verification_status": na,
                        "suggestion": {},
                        "rationale": "",
                        "verification": {},
                        "narrative": narrative_text(result.get("policy_id", "")),
                    }
                )
                continue
            policy_id = result.get("policy_id", "")
            diagnosis = diagnoses.get(policy_id, {})
            action = actions.get(policy_id, {})
            suggestion = action.get("suggestion") or {}
            frequencies = diagnosis.get("frequencies") or {}
            valid_samples = diagnosis.get("valid_samples") or 0
            confidence = diagnosis.get("confidence", "")
            if not confidence and isinstance(frequencies, dict) and valid_samples:
                try:
                    confidence = max(float(count) for count in frequencies.values()) / float(valid_samples)
                except (TypeError, ValueError):
                    confidence = ""
            cause_set = diagnosis.get("prediction_set", [])
            next_action = action.get("action")
            if not next_action:
                next_action = (
                    "customer_outreach"
                    if set(cause_set) == {"house_number_issue"}
                    else "analyst_review"
                )
            queue.append(
                {
                    "policy_id": policy_id,
                    "manual_review_reason": result.get("manual_review_reason", ""),
                    "precision_code": result.get("precision_code", ""),
                    "ai_cause_set": ", ".join(cause_set),
                    "confidence": confidence,
                    "next_action": next_action,
                    "suggestion_status": action.get("suggestion_status", "not_requested"),
                    "verification_status": action.get("verification_status", "not_started"),
                    "suggestion": suggestion,
                    "rationale": action.get("rationale") or suggestion.get("rationale", ""),
                    "verification": action.get("evaluation", {}),
                    "narrative": narratives.get(policy_id, {}).get("narrative", ""),
                }
            )
        return queue, False
    return [], False
