"""Read-only local data access for the Streamlit results and exceptions tabs."""

import csv
from copy import copy
from decimal import Decimal, InvalidOperation
from html import escape
from io import BytesIO
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from address_intelligence.onboarding import format_confidence, friendly_label


def _fraction(value: str | None) -> tuple[int, int] | None:
    """Parse the count and denominator from a report fraction."""
    if not isinstance(value, str):
        return None
    number = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?"
    match = re.fullmatch(
        rf"\s*({number})\s*/\s*({number})(?:\s*\(\s*[0-9]+(?:\.[0-9]+)?%\s*\))?\s*",
        value,
    )
    if not match:
        return None
    try:
        numbers = [Decimal(part.replace(",", "")) for part in match.groups()]
    except InvalidOperation:
        return None
    if any(number.adjusted() > 100 or number != number.to_integral_value() for number in numbers):
        return None
    count, total = map(int, numbers)
    return (count, total) if total and count <= total else None


def preflight_headline(clean: int | None, total: int | None) -> str:
    if clean is None or total is None or clean < 0 or total < 0 or clean > total:
        return "No pre-flight data available."
    if total == 0:
        return "No addresses to check."
    address = "address" if total == 1 else "addresses"
    issues = total - clean
    if issues == 0:
        return f"All {total:,} {address} {'looks' if total == 1 else 'look'} clean."
    issue_verb = "shows" if issues == 1 else "show"
    clean_verb = "looks" if total == 1 else "look"
    return f"{clean:,} of {total:,} {address} {clean_verb} clean. {issues:,} {issue_verb} an issue."


def results_headline(
    before: str | None, after: str | None, eligible: str | None, has_reference: bool
) -> str:
    result = _fraction(after if has_reference else eligible)
    if result is None:
        return "No results data available."
    count, total = result
    address = "address" if total == 1 else "addresses"
    if not has_reference:
        verb = "was" if total == 1 else "were"
        return f"{count:,} of {total:,} {address} {verb} located automatically."
    verb = "is" if total == 1 else "are"
    earlier = _fraction(before)
    gain = f", up from {earlier[0]:,}" if earlier and earlier[1] == total and count > earlier[0] else ""
    return f"{count:,} of {total:,} {address} {verb} right{gain}."


def cost_headline(
    credits: int | float | Decimal | None,
    policies: int | None,
    price: float | Decimal | None,
) -> str:
    if credits is None or policies is None or price is None:
        return "No cost estimate available."
    credits_value, price_value = Decimal(str(credits)), Decimal(str(price))
    if not credits_value.is_finite() or not price_value.is_finite() or min(credits_value, price_value, policies) < 0:
        return "No cost estimate available."
    credit_display = f"{credits_value:,.0f}"
    credit_unit = "credit" if credit_display == "1" else "credits"
    policy_unit = "policy" if policies == 1 else "policies"
    price_display = f"${price_value:,.0f}" if price_value >= 1000 else f"${price_value:,.2f}"
    return f"About {credit_display} {credit_unit} for {policies:,} {policy_unit}: {price_display} at list price."


def onboard_headline(records: int | None, matched: int | None, required: int | None) -> str:
    if records is None or matched is None or required is None or min(records, matched, required) < 0 or matched > required:
        return "No file data available."
    record_unit = "record" if records == 1 else "records"
    column_unit = "column" if required == 1 else "columns"
    return f"{records:,} {record_unit} loaded. {matched:,} of {required:,} required {column_unit} matched."


def work_path(total: int, queue: list[dict[str, Any]]) -> dict[str, int]:
    analyst_rows = [row for row in queue if row.get("next_action") == "analyst_review"]
    verified = sum(
        str(row.get("verification_status", "")).strip().casefold() == "verified by precisely"
        for row in analyst_rows
    )
    return {"total": total, "queue": len(queue), "analyst": len(analyst_rows), "remaining": len(analyst_rows) - verified}


_WORKLIST_ADDRESS_FIELDS = ("address_line1", "address_line2", "city", "state", "zip5")
_WORD_PATTERN = re.compile(r"\w+", re.UNICODE)
_SNAKE_CASE_PATTERN = re.compile(r"\b\w+(?:_\w+)+\b")


def sort_sales_worklist(queue: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return copies ordered by analyst-confirmed, other analyst, then outreach.

    Unknown/missing actions follow the known groups.
    Policy IDs are sorted as text, case-insensitively with a deterministic tie-break.
    A rejected or explicitly incorrect/not-verified suggestion is never confirmed.
    """
    def key(row: Mapping[str, Any]) -> tuple[int, str, str]:
        action = str(row.get("next_action") or "").strip().casefold()
        status = str(row.get("verification_status") or "").strip().casefold()
        verification = row.get("verification")
        verification = verification if isinstance(verification, Mapping) else {}
        rejected = (
            str(row.get("suggestion_status") or "").strip().casefold() == "rejected"
            or status in {"rejected", "verified incorrect", "did not verify", "not verified"}
            or verification.get("verified_incorrect") is True
            or verification.get("not_verified") is True
        )
        confirmed = not rejected and (
            status in {"verified by precisely", "verified correct", "confirmed", "confirmed correct"}
            or verification.get("verified_correct") is True
        )
        group = (
            0 if action == "analyst_review" and confirmed
            else 1 if action == "analyst_review"
            else 2 if action == "customer_outreach"
            else 3
        )
        policy_id = str(row.get("policy_id") or "")
        return group, policy_id.casefold(), policy_id

    return [dict(row) for row in sorted(queue, key=key)]


def sales_worklist_counts(queue: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Count rows by the recorded action, including unclassified rows."""
    rows = list(queue)
    analyst = sum(
        str(row.get("next_action") or "").strip().casefold() == "analyst_review"
        for row in rows
    )
    outreach = sum(
        str(row.get("next_action") or "").strip().casefold() == "customer_outreach"
        for row in rows
    )
    return {"total": len(rows), "analyst": analyst, "outreach": outreach, "unclassified": len(rows) - analyst - outreach}


def sales_worklist_count_line(queue: Iterable[Mapping[str, Any]]) -> str:
    """Return a display-ready total/analyst/outreach count line."""
    counts = sales_worklist_counts(queue)
    unclassified = (
        f", {counts['unclassified']:,} with no recognised next step"
        if counts["unclassified"] else ""
    )
    return (
        f"{counts['total']:,} {'address needs' if counts['total'] == 1 else 'addresses need'} a person: "
        f"{counts['analyst']:,} for your analysts, {counts['outreach']:,} for customer outreach{unclassified}."
    )


def join_sales_worklist_addresses(
    queue: Iterable[Mapping[str, Any]],
    input_rows: Iterable[Mapping[str, Any]],
    column_mapping: Mapping[str, str | None],
    *,
    demo: bool = False,
) -> list[dict[str, Any]]:
    """Return queue copies with an ``input_address`` canonical-field dictionary.

    Real mode joins source rows on the exact text policy ID using canonical-to-source
    column_mapping. CSV readers should retain strings to preserve leading zeroes.
    Blank/missing IDs never match; duplicate nonblank input IDs raise ValueError.
    Missing fields and unmatched IDs are blank. Demo mode never touches input_rows
    or the mapping: it uses canonical or raw_* address fields on each example row.
    No files are read by this helper.
    """
    indexed: dict[str, Mapping[str, Any]] = {}
    if not demo:
        id_column = column_mapping.get("policy_id")
        if not id_column:
            raise ValueError("A policy_id source column is required.")
        for source in input_rows:
            policy_id = source.get(id_column)
            if policy_id is None or str(policy_id) == "":
                continue
            policy_id = str(policy_id)
            if policy_id in indexed:
                raise ValueError("Duplicate policy IDs in input rows.")
            indexed[policy_id] = source

    result = []
    for row in queue:
        policy_id = row.get("policy_id")
        source = row if demo else indexed.get(str(policy_id), {}) if policy_id is not None else {}
        address = {}
        for field in _WORKLIST_ADDRESS_FIELDS:
            if demo:
                value = source.get(field, source.get(f"raw_{field}", ""))
            else:
                column = column_mapping.get(field)
                value = source.get(column, "") if column else ""
            address[field] = "" if value is None else str(value)
        result.append({**row, "input_address": address})
    return result


def highlight_changed_words(text: str, original: str) -> str:
    """Escape text for HTML and mark words absent from original, ignoring case.

    Words are Unicode alphanumeric/underscore runs; punctuation and spacing are
    retained. Matching is by word occurrence, not position, so reordered words
    are not highlighted. The only generated markup is <mark>.
    """
    from collections import Counter

    remaining = Counter(word.casefold() for word in _WORD_PATTERN.findall(original))
    parts = []
    end = 0
    for match in _WORD_PATTERN.finditer(text):
        parts.append(escape(text[end:match.start()]))
        word = match.group()
        folded = word.casefold()
        if remaining[folded]:
            remaining[folded] -= 1
            parts.append(escape(word))
        else:
            parts.append(f"<mark>{escape(word)}</mark>")
        end = match.end()
    parts.append(escape(text[end:]))
    return "".join(parts)


def clean_worklist_text(text: str, replacements: Mapping[str, str] | None = None) -> str:
    """Apply exact, case-sensitive literal phrase replacements and clean snake_case.

    Replacements are applied once simultaneously, longest phrase first; unrelated
    prose retains its case and whitespace. Empty replacement keys are rejected.
    This returns plain text, not HTML.
    """
    if replacements:
        if "" in replacements:
            raise ValueError("Replacement phrases must not be empty.")
        pattern = re.compile("|".join(re.escape(key) for key in sorted(replacements, key=len, reverse=True)))
        text = pattern.sub(lambda match: replacements[match.group()], text)
    text = _SNAKE_CASE_PATTERN.sub(lambda match: match.group().replace("_", " "), text)
    return _trim_cut_off_text(text)


def _trim_cut_off_text(text: str) -> str:
    """Saved AI explanations are capped at 200 characters and may end mid-word.

    When the text was cut off (it ends in an ellipsis or a stray character), keep
    only its complete sentences, so the screen never shows a broken word.
    """
    stripped = text.rstrip()
    if not stripped or stripped.endswith((".", "!", "?")):
        return text
    cut = max(stripped.rfind(". "), stripped.rfind('." '), stripped.rfind("! "), stripped.rfind("? "))
    if cut == -1:
        return stripped.rstrip("….� ")
    return stripped[: cut + 1].rstrip() if stripped[cut] == "." else stripped[: cut + 2].rstrip()


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
            before_fraction = _fraction(row["before"])
            after_fraction = _fraction(row["after"])
            before = before_fraction[0] if before_fraction else int(row["before"].split("/", 1)[0])
            after = after_fraction[0] if after_fraction else int(row["after"].split("/", 1)[0])
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


SALES_WORKLIST_COLUMNS = (
    "Policy ID", "Address on file", "Next action", "Why flagged", "Status",
    "Suggested address", "What the AI found", "Summary for operations",
    "Match level", "AI cause", "Confidence",
)


def worklist_verification_text(row: Mapping[str, Any]) -> str:
    verification = row.get("verification", {})
    if row.get("suggestion_status") == "rejected":
        return "Rejected"
    if isinstance(verification, Mapping) and verification.get("verified_correct") is True:
        return "Confirmed correct"
    if isinstance(verification, Mapping) and verification.get("verified_incorrect") is True:
        return "Confirmed incorrect"
    if (
        row.get("verification_status") == "did not verify"
        or isinstance(verification, Mapping) and verification.get("not_verified") is True
    ):
        return "Not verified"
    return friendly_label(str(row.get("verification_status", "not_started")))


STATUS_HELP = (
    "**Confirmed by Precisely:** a fix is ready for the analyst to approve.\n\n"
    "**Not confirmed by Precisely:** the AI proposed a fix, but Precisely could not confirm it, so it was stopped.\n\n"
    "**No fix found:** there is no safe fix, so the case goes to research.\n\n"
    "**Rejected by analyst:** the analyst turned down a confirmed fix, so the case goes to research.\n\n"
    "**With the customer:** only the customer can give the missing information."
)
_PLAIN_CONFIRMED = {"verified by precisely", "verified correct", "confirmed", "confirmed correct"}
_PLAIN_STOPPED = {"not verified", "did not verify", "verified incorrect"}


def plain_status(row: Mapping[str, Any]) -> str:
    """One plain status per case, the same in the table, the case card, the filter and the export."""
    action = str(row.get("next_action") or "").strip().casefold()
    if action == "customer_outreach":
        return "With the customer"
    if action != "analyst_review":
        return ""
    decision = row.get("analyst_decision")
    if isinstance(decision, Mapping) and decision.get("decision") == "rejected":
        return "Rejected by analyst"
    status = str(row.get("verification_status") or "").strip().casefold().replace("_", " ")
    verification = row.get("verification")
    verification = verification if isinstance(verification, Mapping) else {}
    blocked = str(row.get("suggestion_status") or "").strip().casefold() == "rejected"
    if not blocked and verification.get("verified_incorrect") is True:
        return "Confirmed incorrect"  # only known when reference answers exist
    if not blocked and (status in _PLAIN_CONFIRMED or verification.get("verified_correct") is True):
        return "Confirmed by Precisely"
    if not blocked and (status in _PLAIN_STOPPED or verification.get("not_verified") is True
                        or verification.get("verified_incorrect") is True):
        return "Not confirmed by Precisely"
    if status in {"", "not started"}:
        return "Not started"
    return "No fix found"


def worklist_case_status(row: Mapping[str, Any], table_status: str) -> str:
    if plain_status(row):
        return plain_status(row)
    verification_details = row.get("verification")
    available = bool(row.get("verification_status")) or (
        isinstance(verification_details, Mapping)
        and any(verification_details.get(key) is True for key in (
            "verified_correct", "verified_incorrect", "not_verified",
        ))
    )
    verification = worklist_verification_text(row) if available else ""
    status = verification if verification in {"Confirmed correct", "Confirmed incorrect", "Rejected"} else (
        table_status or verification
    )
    if table_status in {"With the customer", "No AI suggestion"}:
        status = table_status
    return "Confirmed by Precisely" if status in {"Confirmed correct", "Confirmed"} else status


def format_sales_worklist_rows(queue: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Prepare the same plain-language values for the table, case and workbook."""
    formatted = format_exception_table_rows(queue)
    result = []
    for row, values in zip(queue, formatted):
        on_file = row.get("input_address") or {}
        address = ", ".join(
            str(on_file.get(key) or "").strip()
            for key in _WORKLIST_ADDRESS_FIELDS if str(on_file.get(key) or "").strip()
        )
        suggestion = row.get("suggestion") or {}
        if not isinstance(suggestion, Mapping):
            suggestion = {}
        # Once Precisely has confirmed a fix, show Precisely's address, not the AI's draft.
        confirmed = row.get("precisely_address")
        if isinstance(confirmed, Mapping) and confirmed.get("line1"):
            suggestion = confirmed
        line = str(suggestion.get("line1") or "").strip()
        city_zip = " ".join(
            str(suggestion.get(key) or "").strip()
            for key in ("city", "zip5") if str(suggestion.get(key) or "").strip()
        )
        # The AI corrects the street, town or ZIP. It never removes a unit the customer gave,
        # so the unit on file is shown with the suggestion, exactly as it would be approved.
        unit = str(on_file.get("address_line2") or "").strip()
        if not line or not unit or unit.casefold() in line.casefold():
            unit = ""
        suggested_address = ", ".join(part for part in (line, unit, city_zip) if part)
        action = str(row.get("next_action") or "").strip().casefold()
        verification = str(row.get("verification_status") or "").strip().casefold().replace("_", " ")
        status = values["Verification status"] if verification else ""
        if action == "customer_outreach":
            status = "With the customer"
        elif action == "analyst_review" and verification == "not applicable":
            status = "No AI suggestion"
        reason = row.get("rationale") or plain_flag_reason(row.get("manual_review_reason") or "")
        summary = str(row.get("narrative") or "")
        summary = (summary
            .replace("could not be verified to building level", "could not be confirmed at the building level")
            .replace("The location could not be confirmed", "The address could not be confirmed")
            .replace("Flood enrichment was withheld pending address remediation.", "Flood data is on hold until the address is corrected.")
            .replace("Flood enrichment was withheld pending address remediation", "Flood data is on hold until the address is corrected."))
        status = plain_status(row) or status
        result.append({
            "Policy ID": str(row.get("policy_id") or ""),
            "Address on file": address,
            "Next action": values["Next action"] if action else "",
            "Why flagged": values["Manual review reason"] if row.get("manual_review_reason") else "",
            "Status": status,
            "Suggested address": suggested_address,
            "What the AI found": clean_worklist_text(str(reason)),
            "Summary for operations": summary,
            **{
                label: values[label] if row.get(key) is not None and str(row.get(key, "")).strip() else ""
                for label, key in (("Match level", "precision_code"), ("AI cause", "ai_cause_set"), ("Confidence", "confidence"))
            },
        })
    return result


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
    return _exception_workbook_bytes(_workbook_rows(queue))


def _workbook_rows(queue: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows = format_sales_worklist_rows(queue)
    for source, row in zip(queue, rows):
        row["Status"] = worklist_case_status(source, row["Status"])
    return rows


def _exception_workbook_bytes(rows: list[dict[str, str]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Work list"
    headers = SALES_WORKLIST_COLUMNS
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(fill_type="solid", fgColor="0B2341")
    for row in rows:
        sheet.append([row.get(header, "") for header in headers])
        for cell in sheet[sheet.max_row]:
            if cell.value is not None:
                cell.data_type = "s"
            if headers[cell.column - 1] in {
                "Address on file", "Why flagged", "Suggested address",
                "What the AI found", "Summary for operations", "AI cause",
            }:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
    for row in sheet.iter_rows():
        for cell in row:
            alignment = copy(cell.alignment)
            alignment.vertical = "center"
            cell.alignment = alignment
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
        _exception_workbook_bytes(_workbook_rows(queue))
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
