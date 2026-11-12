"""Analyst decisions on the work list: approve a confirmed fix, keep an audit log, and
build the corrections file a client loads into its own systems.

Nothing here changes a policy record. An approval is written to a local audit log, the
case leaves the open work list, and the approved address goes into a corrections file.
The client's own process loads that file into the policy system.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

DECISIONS = ("approved", "rejected", "undone")
LOG_NAME = "worklist_decisions.jsonl"
CORRECTIONS_NAME = "approved_corrections.xlsx"
CORRECTIONS_COLUMNS = (
    "Policy ID",
    "Address on file",
    "Corrected address",
    "Confirmed by Precisely",
    "Approved (UTC)",
    "Source",
)
_CONFIRMED = {"verified by precisely", "verified correct", "confirmed", "confirmed correct"}
_REJECTED = {"rejected", "verified incorrect", "did not verify", "not verified"}


def decision_log_path(run_dir: str | Path) -> Path:
    return Path(run_dir) / "decisions" / LOG_NAME


def is_confirmed_fix(row: Mapping[str, Any]) -> bool:
    """True when an analyst case carries an AI fix that Precisely confirmed."""
    if str(row.get("next_action") or "").strip().casefold() != "analyst_review":
        return False
    status = str(row.get("verification_status") or "").strip().casefold()
    verification = row.get("verification")
    verification = verification if isinstance(verification, Mapping) else {}
    if (
        str(row.get("suggestion_status") or "").strip().casefold() == "rejected"
        or status in _REJECTED
        or verification.get("verified_incorrect") is True
        or verification.get("not_verified") is True
    ):
        return False
    suggestion = row.get("suggestion")
    if not isinstance(suggestion, Mapping) or not str(suggestion.get("line1") or "").strip():
        return False
    return status in _CONFIRMED or verification.get("verified_correct") is True


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def corrected_address(fix: Mapping[str, Any], on_file: Mapping[str, Any] | None) -> dict[str, str]:
    """The address to approve: the confirmed street, with the unit kept from the file.

    The AI fixes the street, town or ZIP. It never removes a unit the customer gave us,
    so the apartment or suite on file is carried over. City, state and ZIP come from the
    confirmed fix, falling back to the file where the fix leaves them blank.
    """
    on_file = on_file or {}
    line1 = _text(fix.get("line1"))
    unit = _text(on_file.get("address_line2"))
    if unit and unit.casefold() in line1.casefold():
        unit = ""
    return {
        "line1": line1,
        "line2": unit,
        "city": _text(fix.get("city")) or _text(on_file.get("city")),
        "state": _text(fix.get("state")) or _text(on_file.get("state")),
        "zip5": _text(fix.get("zip5")) or _text(on_file.get("zip5")),
    }


def format_address(parts: Mapping[str, Any]) -> str:
    state_zip = " ".join(part for part in (_text(parts.get("state")), _text(parts.get("zip5"))) if part)
    return ", ".join(
        part for part in (_text(parts.get("line1")), _text(parts.get("line2")), _text(parts.get("city")), state_zip) if part
    )


def unit_kept(parts: Mapping[str, Any]) -> bool:
    return bool(_text(parts.get("line2")))


def decision_entry(
    *,
    policy_id: str,
    decision: str,
    address_on_file: str,
    corrected: Mapping[str, Any] | None = None,
    confirmed_by_precisely: bool = False,
    source: str = "Overnight run",
    now: datetime | None = None,
) -> dict[str, Any]:
    if decision not in DECISIONS:
        raise ValueError("unknown decision")
    if not str(policy_id).strip():
        raise ValueError("a policy ID is required")
    if decision == "approved" and not confirmed_by_precisely:
        raise ValueError("only a fix confirmed by Precisely can be approved")
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "time": stamp.isoformat(timespec="seconds"),
        "policy_id": str(policy_id),
        "decision": decision,
        "decided_by": "Analyst",
        "address_on_file": address_on_file,
        "corrected_address": format_address(corrected) if corrected and decision == "approved" else "",
        "confirmed_by_precisely": bool(confirmed_by_precisely),
        "source": source,
    }


def read_log(path: str | Path) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.is_file():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
    return entries


def append_log(path: str | Path, entry: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(entry)) + "\n")


def latest_decisions(entries: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """The current decision per policy. An "undone" entry clears the earlier one."""
    latest: dict[str, dict[str, Any]] = {}
    for entry in entries:
        policy_id = str(entry.get("policy_id") or "")
        if not policy_id:
            continue
        if entry.get("decision") == "undone":
            latest.pop(policy_id, None)
        else:
            latest[policy_id] = dict(entry)
    return latest


def open_cases(queue: Iterable[Mapping[str, Any]], latest: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Cases still waiting for a person. Approved fixes leave the list."""
    return [
        dict(row) for row in queue
        if latest.get(str(row.get("policy_id") or ""), {}).get("decision") != "approved"
    ]


def corrections_rows(latest: Mapping[str, Mapping[str, Any]]) -> list[dict[str, str]]:
    rows = []
    for policy_id, entry in sorted(latest.items()):
        if entry.get("decision") != "approved":
            continue
        rows.append({
            "Policy ID": policy_id,
            "Address on file": _text(entry.get("address_on_file")),
            "Corrected address": _text(entry.get("corrected_address")),
            "Confirmed by Precisely": "Yes" if entry.get("confirmed_by_precisely") else "No",
            "Approved (UTC)": _text(entry.get("time")).replace("T", " ").replace("+00:00", ""),
            "Source": _text(entry.get("source")),
        })
    return rows


def corrections_workbook(rows: list[Mapping[str, str]]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Approved changes"
    sheet.append(list(CORRECTIONS_COLUMNS))
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill(fill_type="solid", fgColor="39006B")
    for row in rows:
        sheet.append([row.get(column, "") for column in CORRECTIONS_COLUMNS])
        for cell in sheet[sheet.max_row]:
            cell.data_type = "s"
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    sheet.freeze_panes = "A2"
    for column in sheet.columns:
        letter = column[0].column_letter
        sheet.column_dimensions[letter].width = min(max(max(len(str(cell.value or "")) for cell in column) + 2, 12), 46)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def made_up_corrections_workbook(rows: list[Mapping[str, str]]) -> bytes:
    if any(not str(row.get("Policy ID", "")).startswith("EXAMPLE-") for row in rows):
        raise ValueError("Made-up export only accepts EXAMPLE policy IDs.")
    return corrections_workbook(rows)


def save_real_corrections(rows: list[Mapping[str, str]], run_dir: str | Path, data_output_root: str | Path) -> Path:
    output_root = Path(data_output_root).resolve()
    destination = (Path(run_dir) / CORRECTIONS_NAME).resolve()
    try:
        destination.relative_to(output_root)
    except ValueError:
        raise ValueError("Real corrections must be saved inside data/output/.") from None
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(corrections_workbook(rows))
    return destination


def archive_logs(output_root: str | Path, *, now: datetime | None = None) -> list[Path]:
    """Start a rehearsal with a fresh work list: move each decision log aside, never delete it."""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    moved = []
    for path in sorted(Path(output_root).glob(f"**/decisions/{LOG_NAME}")):
        target = path.with_name(f"worklist_decisions_{stamp}.jsonl")
        path.rename(target)
        moved.append(target)
    return moved
