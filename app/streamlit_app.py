"""Local-only client onboarding and results viewer. Never run this in production."""

import csv
import hashlib
from html import escape
from io import StringIO
import os
from pathlib import Path
import re
import sys
from typing import Any

import streamlit as st

# Imported up front to avoid a start-up race when several browser sessions load at once.
import altair as alt  # noqa: F401
import pandas as pd  # noqa: F401
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "app"))

import ui as app_ui
import live_panel

from address_intelligence.app_data import (
    _fraction,
    cost_headline,
    defect_chart_rows,
    format_cost_breakdown_rows,
    format_exception_table_rows,
    format_sales_worklist_rows,
    load_exception_queue,
    load_fallback_kpis,
    load_summary_kpis,
    highlight_changed_words,
    join_sales_worklist_addresses,
    made_up_exception_workbook,
    open_folder,
    onboard_headline,
    preflight_headline,
    REAL_SAVE_EXPLANATION as app_data_real_save_explanation,
    read_summary_csv,
    results_headline,
    sales_worklist_count_line,
    save_real_exception_workbook,
    sort_sales_worklist,
    worklist_case_status,
    worklist_verification_text,
    work_path,
)
from address_intelligence.client_profile import DEFAULT_PROFILE_PATH, load_client_profile
from address_intelligence import worklist_decisions as decisions_lib
from address_intelligence.onboarding import (
    CANONICAL_COLUMNS,
    REQUIRED_COLUMNS,
    build_profile,
    rollout_cost_breakdown,
    display_preflight_rows,
    enrichment_help_texts,
    friendly_label,
    missing_required_message,
    preflight_checks,
    preflight_group_summaries,
    read_excel_workbook,
    save_client_bundle,
    serialize_input_csv,
    serialize_profile,
    suggest_mapping,
    unused_columns,
)


SAMPLE_PATH = ROOT / "app" / "demo_data" / "sample_client.csv"
DEMO_EXCEPTIONS_PATH = ROOT / "app" / "demo_data" / "demo_exceptions.json"
NO_MAPPING = "— Not mapped —"
MAPPING_LABELS = {
    "policy_id": "Policy ID column",
    "address_line1": "Street address column",
    "address_line2": "Unit / apartment column (optional)",
    "city": "City column",
    "state": "State column",
    "zip5": "ZIP code column",
    "metro": "Metro column (optional)",
}
MAPPING_HELP = {
    "policy_id": "The client's unique policy or contract reference for each row.",
    "address_line1": "The building's street number and street name.",
    "address_line2": "Apartment, unit, or suite number, if the client file has one.",
    "city": "The city or town where the building is located.",
    "state": "The two-letter state or territory code for the address.",
    "zip5": "The five-digit ZIP code, or ZIP+4, for the address.",
    "metro": "Optional market or metro name used to group comparison samples.",
}

MAX_UPLOAD_BYTES = 200 * 1024 * 1024


def _read_uploaded_csv(content: bytes) -> tuple[list[str], list[dict[str, str]]]:
    text = content.decode("utf-8-sig")
    reader = csv.DictReader(StringIO(text))
    if reader.fieldnames is None:
        raise ValueError("The uploaded CSV must include a header row.")
    return list(reader.fieldnames), list(reader)


def _read_uploaded_file(
    file_name: str,
    content: bytes,
    sheet_name: str | None = None,
) -> tuple[list[str], list[dict[str, str]], list[str]]:
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("The client file must be 200 MB or smaller.")
    extension = Path(file_name).suffix.casefold()
    if extension == ".csv":
        columns, rows = _read_uploaded_csv(content)
        return columns, rows, []
    if extension == ".xlsx":
        return read_excel_workbook(content, sheet_name)
    raise ValueError("Choose a CSV or Excel (.xlsx) file.")


def _file_fingerprint() -> str:
    digest = hashlib.sha1(st.session_state.uploaded_content).hexdigest()[:10]
    name = st.session_state.uploaded_name
    sheet = st.session_state.get("excel_sheet") or ""
    return hashlib.sha1(f"{name}|{sheet}|{digest}".encode()).hexdigest()[:10]


REQUIRED_FIELD_NAMES = {
    "policy_id": "Policy ID",
    "address_line1": "Street address",
    "city": "City",
    "state": "State",
    "zip5": "ZIP code",
}


def _reset_mapping_state() -> None:
    st.session_state.pop("onboarding_mapping", None)
    st.session_state.pop("onboarding_profile", None)
    for field in CANONICAL_COLUMNS:
        st.session_state.pop(f"mapping_{field}", None)


def _slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return value or "new-client"


def _client_file(name: str) -> str:
    return f"{name.strip()}'s file" if name.strip() else "this client's file"


def _profile_error_message(error: ValueError) -> str:
    message = str(error)
    if "Slug must" in message:
        return "Folder name must use lowercase letters, numbers, and hyphens; for example, acme-insurance."
    if "Required mappings" in message:
        return "Choose a file column for each required field before continuing."
    return message


def _fraction_count(value: str) -> tuple[str, int]:
    fraction, _, _ = value.partition(" ")
    if "/" not in fraction:
        return fraction, 0
    counts = _fraction(value)
    return fraction, counts[0] if counts else 0


def _build_kpi_tile_values(summary_path: Path) -> list[dict[str, str | int]]:
    """Build compact KPI values and meaningful changes from aggregate CSV rows."""
    kpis = load_summary_kpis(summary_path)
    if kpis is None:
        return []
    summary_rows = read_summary_csv(summary_path)
    metrics = {row.get("metric", ""): row for row in summary_rows}
    tiles: list[dict[str, str | int]] = []

    for metric_key in ("correct_building", "dirty_fully_correct"):
        metric = kpis[metric_key]
        value, after_count = _fraction_count(metric["after"])
        _, before_count = _fraction_count(metric["before"])
        tiles.append(
            {
                "label": metric["label"],
                "value": value,
                "delta": f"{after_count - before_count:+d} records vs before",
                "delta_color": "normal",
                "caption": f"Before: {metric['before']}",
            }
        )

    flood_value, _ = _fraction_count(kpis["flood_tier_changes"]["value"])

    def metric_count(name: str) -> str:
        row = metrics.get(name, {})
        numerator = row.get("numerator", "")
        if numerator:
            return numerator
        _, count = _fraction_count(row.get("after", ""))
        return str(count)

    tiles.append(
        {
            "label": "City-level location changes flood-risk tier",
            "value": flood_value,
            "delta": (
                f"{metric_count('Coarse location understated verified risk')} under-rated, "
                f"{metric_count('Coarse location overstated verified risk')} over-rated"
            ),
            "delta_color": "off",
            "caption": "",
        }
    )
    verified = kpis["ai_verified"]
    tiles.append(
        {
            "label": verified["label"],
            "value": f"{verified['correct']}/{verified['verified']}",
            "delta": f"{verified['incorrect']} incorrect",
            "delta_color": "off",
            "caption": "",
        }
    )
    return tiles


def _render_accuracy_chart(kpis: dict[str, Any]) -> None:
    rows = defect_chart_rows(kpis)
    if not rows:
        return
    original = {
        friendly_label(defect["defect"]): defect
        for defect in kpis["defects"]
    }
    chart_rows = [
        {
            "Defect": row["Defect"],
            "Before": str(original[row["Defect"]]["before"]),
            "After": str(original[row["Defect"]]["after"]),
        }
        for row in rows
    ]
    app_ui.render_defect_rows(chart_rows)


def _display_kpis(summary_path: Path) -> None:
    if not summary_path.is_file():
        st.info(":material/insights: No completed run yet. Run the clean-up from tab 3, step 4.")
        return
    kpis = load_summary_kpis(summary_path)
    if kpis is None:
        st.info(":material/insights: No completed run yet. Run the clean-up from tab 3, step 4.")
        return
    if kpis["has_reference"]:
        app_ui.render_results_band(
            kpis["correct_building"]["before"], kpis["correct_building"]["after"]
        )
        cards = []
        for tile in _build_kpi_tile_values(summary_path)[1:]:
            tone = "positive" if tile["delta_color"] == "normal" else "neutral"
            cards.append(
                {
                    "label": tile["label"],
                    "value": tile["value"],
                    "delta": tile["delta"],
                    "tone": tone,
                    "caption": tile["caption"],
                }
            )
        app_ui.render_kpi_row(cards, columns=3)
        _render_accuracy_chart(kpis)
        return

    fallback = load_fallback_kpis(summary_path)
    if fallback:
        app_ui.render_results_band("", fallback[0]["value"])
    app_ui.render_kpi_row(fallback[1:], columns=3)
    st.info("Accuracy against a reference needs a labelled sample; not available for this client.")


def _average_credits_formula(enabled_item_rate: Any, include_address_verification: bool = True) -> str:
    return (
        f"Average credits per policy = 1 geocoding + {0.33 if include_address_verification else 0:.2f} address verification + "
        f"{enabled_item_rate:,.2f} credits for enabled data items."
    )


def _case_panels_html(
    panels: list[tuple[str, str, bool]], *, help_labels: bool = False, on_file: str = ""
) -> str:
    panel_html = "".join(
        '<div class="ai-case-panel" style="min-width:0;box-sizing:border-box;'
        f'padding:14px;border:1px solid #E6E3F0;border-radius:8px;background:{"#F5EFFC" if tinted else "#FFFFFF"};'
        f'{"grid-column:1 / -1;" if not help_labels and len(panels) % 2 and index == len(panels) - 1 else ""}">'
        f'<div style="font-size:12px;color:#5F5A73;margin-bottom:6px">{escape(label)}'
        f'{app_ui.help_icon_html(label) if help_labels else ""}</div>'
        f'<div style="color:#1B1433;overflow-wrap:anywhere;white-space:pre-wrap">'
        f'{highlight_changed_words(value, on_file) if label == "Suggested address" and on_file else escape(value)}</div></div>'
        for index, (label, value, tinted) in enumerate(panels)
    )
    return (
        '<style>.ai-case-panel mark{font-weight:700;color:inherit;background:transparent;'
        'text-decoration:underline;text-decoration-color:#8017E1;text-decoration-thickness:2px}'
        '@media(max-width:600px){.ai-case-panels{grid-template-columns:1fr !important}}</style>'
        f'<div class="ai-case-panels" style="display:grid;'
        f'grid-template-columns:repeat({len(panels) if help_labels else min(2, len(panels))},minmax(0,1fr));gap:12px">{panel_html}</div>'
    )


def _record_worklist_decision(entry: dict[str, Any], log_path: Path | None) -> None:
    if log_path is None:
        st.session_state.setdefault("made_up_worklist_decisions", []).append(entry)
    else:
        decisions_lib.append_log(log_path, entry)


def _render_decision_box(case: dict[str, Any], log_path: Path | None) -> None:
    """Approve or reject a fix that Precisely confirmed, with the unit kept from the file."""
    display = format_sales_worklist_rows([case])[0]
    policy_id = display["Policy ID"]
    confirmed = case.get("precisely_address")
    parts = decisions_lib.corrected_address(confirmed or case.get("suggestion") or {}, case.get("input_address"))
    with st.container(border=True):
        st.markdown("**Analyst decision**")
        st.markdown(f"Address to approve: **{escape(decisions_lib.format_address(parts))}**", unsafe_allow_html=True)
        if confirmed:
            changes = decisions_lib.corrections_by_precisely(case.get("suggestion") or {}, confirmed)
            if changes:
                st.caption("Precisely corrected the AI's suggestion (" + "; ".join(changes) + "). This is Precisely's address.")
            else:
                st.caption("This is the address Precisely confirmed.")
        if decisions_lib.unit_kept(parts):
            st.caption(f"Unit kept from the address on file ({parts['line2']}).")
        st.caption(
            "Approving logs the decision for audit and adds the address to the corrections file. "
            "Nothing changes in a policy record here."
        )
        approve_column, reject_column = st.columns(2)
        decision = None
        if approve_column.button("Approve the fix", key=f"wl_approve_{policy_id}", type="primary", icon=":material/check:"):
            decision = "approved"
        if reject_column.button("Reject: send to research", key=f"wl_reject_{policy_id}", icon=":material/manage_search:"):
            decision = "rejected"
        if decision:
            entry = decisions_lib.decision_entry(
                policy_id=policy_id,
                decision=decision,
                address_on_file=display["Address on file"],
                corrected=parts,
                confirmed_by_precisely=True,
                source="Overnight run",
            )
            _record_worklist_decision(entry, log_path)
            st.session_state["just_decided"] = entry
            st.rerun()


def _render_decision_confirmation(entry: dict[str, Any], log_path: Path | None) -> None:
    policy_id = entry["policy_id"]
    with st.container(border=True):
        if entry["decision"] == "approved":
            st.success(f"{policy_id} approved. It has left the open work list.")
            st.markdown(f"**Corrected address:** {escape(entry['corrected_address'])}", unsafe_allow_html=True)
        else:
            st.info(f"{policy_id}: fix rejected and sent to research. It stays on the work list.")
        when = entry["time"].replace("T", " ").replace("+00:00", "")
        st.caption(
            f"Logged for audit at {when} UTC: who decided, the address before and after, "
            "and Precisely's confirmation."
        )
        if st.button("Undo", key=f"wl_undo_{policy_id}", icon=":material/undo:"):
            _record_worklist_decision(
                decisions_lib.decision_entry(
                    policy_id=policy_id, decision="undone", address_on_file=entry["address_on_file"]
                ),
                log_path,
            )
            st.session_state.pop("just_decided", None)
            st.rerun()


def _render_corrections(latest: dict[str, dict[str, Any]], demo_mode: bool, run_dir: Path) -> None:
    rows = decisions_lib.corrections_rows(latest)
    st.subheader("Approved changes for your systems", anchor=False)
    st.write(
        "Every approved fix, ready to load into the policy system. In the pilot, it's this file. "
        "In production, the same changes go through an API or a scheduled load. Each decision is also in the audit log."
    )
    if not rows:
        st.caption("No approved changes yet. Approve a confirmed fix to add it here.")
        return
    st.dataframe(
        [{key: row[key] for key in ("Policy ID", "Address on file", "Corrected address", "Approved (UTC)")} for row in rows],
        hide_index=True,
        width="stretch",
    )
    if demo_mode:
        try:
            st.download_button(
                "Download corrections file (Excel)",
                data=decisions_lib.made_up_corrections_workbook(rows),
                file_name="made-up-corrections.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                icon=":material/download:",
            )
        except ValueError as error:
            st.error(str(error))
        return
    if st.button("Save corrections file to the client folder", key="save_corrections", icon=":material/save:"):
        try:
            saved = decisions_lib.save_real_corrections(rows, run_dir, ROOT / "data" / "output")
        except ValueError as error:
            st.error(str(error))
        except PermissionError:
            st.warning("The corrections file is open in Excel. Close it, then save again.")
        except OSError:
            st.error("The corrections file could not be saved. Check that the client folder is available.")
        else:
            st.session_state.corrections_path = str(saved)
    saved_path = st.session_state.get("corrections_path")
    if saved_path:
        st.success(f"Corrections file saved to: {Path(saved_path).resolve()}")


def _render_audit_trail(entries: list[dict[str, Any]], demo_mode: bool) -> None:
    rows = decisions_lib.audit_rows(entries)
    with st.expander(f"Audit trail ({len(rows)} {'decision' if len(rows) == 1 else 'decisions'})"):
        st.caption(
            "Every analyst decision, newest first: who, when, the address before and after, and Precisely's confirmation. "
            "Entries are added, never edited or removed. "
            + ("Made-up mode keeps them for this session only." if demo_mode else "Stored in this client's private results folder.")
        )
        if rows:
            st.dataframe(rows, hide_index=True, width="stretch")
        else:
            st.caption("No decisions yet.")


def _render_exception_detail(detail: dict[str, Any]) -> None:
    values = format_exception_table_rows([detail])[0]
    display = format_sales_worklist_rows([detail])[0]
    address = display["Suggested address"]
    verification_details = detail.get("verification")
    verification_available = bool(detail.get("verification_status")) or (
        isinstance(verification_details, dict)
        and any(verification_details.get(key) is True for key in (
            "verified_correct", "verified_incorrect", "not_verified",
        ))
    )
    verification = worklist_verification_text(detail) if verification_available else ""
    status = worklist_case_status(detail, display["Status"])
    status_key = status.casefold()
    if status_key in {"confirmed incorrect", "rejected", "not verified", "with the customer"}:
        color, background = "#B00062", "#FDE8F3"
    elif status_key == "confirmed by precisely":
        color, background = "#39006B", "#F5EFFC"
    else:
        color, background = "#67420C", "#FFF6E5"
    status_html = (
        f'<span class="ai-case-status" style="display:inline-block;border-radius:999px;'
        f'padding:4px 10px;color:{color};background:{background};font-weight:600">'
        f'{escape(status)}</span>' if status else ""
    )

    on_file = display["Address on file"]
    next_action = str(detail.get("next_action") or "").strip()
    next_step = (
        f'<span>Next step: {escape(friendly_label(next_action))}</span>' if next_action else ""
    )
    with st.container(border=True):
        st.markdown(
            '<div style="display:flex;align-items:center;justify-content:space-between;'
            'gap:12px;flex-wrap:wrap">'
            f'<strong>Policy ID: {escape(str(detail.get("policy_id", "")))}</strong>{next_step}{status_html}</div>',
            unsafe_allow_html=True,
        )
        panels = [
            (label, str(value), tinted)
            for label, value, tinted in (
                ("On file", on_file, False),
                ("Suggested address", address, True),
                ("What the AI found", display["What the AI found"], False),
                ("Summary for operations", display["Summary for operations"], False),
            )
            if value and str(value).strip()
        ]
        if panels:
            st.markdown(_case_panels_html(panels, on_file=on_file), unsafe_allow_html=True)

        stages = []
        if address:
            stages.append("AI suggested")
        if (
            str(detail.get("verification_status", "")).strip().casefold() == "verified by precisely"
            and verification not in {"Confirmed incorrect", "Rejected"}
        ) or verification == "Confirmed correct":
            stages.append("Precisely confirmed")
        if detail.get("next_action") == "analyst_review":
            stages.append("Analyst decides")
        if stages:
            chips = ' <span aria-hidden="true">→</span> '.join(
                f'<span style="display:inline-block;padding:4px 10px;border-radius:999px;'
                f'background:{"transparent" if stage == "Analyst decides" else "#F5EFFC"};'
                f'border:1px solid {"#8017E1" if stage == "Analyst decides" else "transparent"};'
                f'color:#1B1433">{escape(stage)}</span>'
                for stage in stages
            )
            st.markdown(
                f'<div class="ai-case-flow" style="display:flex;align-items:center;'
                f'gap:8px;flex-wrap:wrap;color:#1B1433">{chips}</div>',
                unsafe_allow_html=True,
            )

        technical = [
            (label, values[label])
            for label, key in (
                ("Match level", "precision_code"),
                ("AI cause", "ai_cause_set"),
                ("Confidence", "confidence"),
            )
            if detail.get(key) is not None and str(detail.get(key, "")).strip()
        ]
        if technical:
            with st.expander("Technical detail", expanded=False):
                st.markdown(
                    _case_panels_html([(label, value, False) for label, value in technical], help_labels=True),
                    unsafe_allow_html=True,
                )


def _stage_commands(
    profile_path: str,
    ai_flags: dict[str, bool],
    comparison: bool = False,
) -> list[str]:
    py = ".venv\\Scripts\\python"
    stages = ["geocode", "preciselyid", "enrich"] + (["compare"] if comparison else [])
    steps: list[list[str]] = []
    for stage in stages:
        base = f"{py} scripts/run_pipeline.py --stage {stage} --profile {profile_path}"
        steps.append(["# Dry run (no cost)", base])
        steps.append(["# Paid run, only after approval", f"{base} --execute"])
    if ai_flags.get("narratives"):
        base = f"{py} scripts/generate_narratives.py --profile {profile_path}"
        steps.append(["# Dry run (no cost)", f"{base} --dry-run"])
        steps.append(["# Paid run, only after approval", f"{base} --execute"])
        steps.append([f"{py} scripts/evaluate_narratives.py --profile {profile_path}"])
    if ai_flags.get("remediation"):
        steps.append(
            [
                "# Prints 'not available' until a labelled calibration sample exists.",
                f"{py} scripts/diagnose_exceptions.py --profile {profile_path}",
            ]
        )
        steps.append(
            [
                "# Prints 'not available' until a labelled calibration sample exists.",
                f"{py} scripts/remediate_exceptions.py --stage suggest --profile {profile_path}",
            ]
        )
    steps.append([f"{py} scripts/metrics_report.py --profile {profile_path}"])
    steps.append([f"{py} scripts/build_workbook.py --profile {profile_path}"])
    return [
        f"# Step {number}\n" + "\n".join(lines)
        for number, lines in enumerate(steps, start=1)
    ]


st.set_page_config(
    page_title="Address Intelligence",
    page_icon=":material/location_on:",
    layout="wide",
)
if "results_mode" not in st.session_state:
    st.session_state.results_mode = "Made-up examples (safe to share)"
real_mode = st.session_state.results_mode == "Real results on this computer"
app_ui.apply_styles(real_mode=real_mode)
header_placeholder = st.container()

start_tab, onboard_tab, preflight_tab, cost_tab, results_tab = st.tabs(
    [
        ":material/flag: Start here",
        ":material/upload_file: 1 Onboard",
        ":material/fact_check: 2 Pre-flight",
        ":material/request_quote: 3 Cost & plan",
        ":material/insights: 4 Client results",
    ]
)

for key, default in (
    ("uploaded_content", None),
    ("uploaded_name", ""),
    ("sample_mode", False),
):
    if key not in st.session_state:
        st.session_state[key] = default

demo_mode_enabled = os.environ.get("AIP_DEMO_MODE") == "1"
if "demo_session_initialized" not in st.session_state:
    if demo_mode_enabled and not st.session_state.uploaded_content:
        demo_profile = load_client_profile(DEFAULT_PROFILE_PATH)
        _reset_mapping_state()
        st.session_state.pop("excel_sheet", None)
        st.session_state.uploaded_content = demo_profile.input_path.read_bytes()
        st.session_state.uploaded_name = "marova_policy_sample.csv"
        st.session_state.demo_preloaded_mapping = demo_profile.column_mapping
        st.session_state.onboarding_mapping = dict(st.session_state.demo_preloaded_mapping)
        st.session_state.parsed_upload_source = (st.session_state.uploaded_name, None)
        st.session_state.demo_preloaded_fingerprint = _file_fingerprint()
    st.session_state.demo_session_initialized = True

with start_tab:
    app_ui.render_tab_intro(
        "From client file to clean addresses, in four steps.",
        "This tool helps a Sales Engineer assess a prospect's address data, price a clean-up, and show the client what they receive after the run. It runs only on this computer.",
    )
    with st.container(border=True):
        st.subheader("How it works", anchor=False)
        app_ui.render_journey_cards(
            [
                {
                    "title": "1 Onboard",
                    "who": "Sales Engineer.",
                    "provide": "The client's address file (CSV or Excel).",
                    "get": "A settings file that tells the pipeline which column is which.",
                },
                {
                    "title": "2 Pre-flight",
                    "who": "Sales Engineer.",
                    "provide": "Nothing more.",
                    "get": "A free check showing which issues Precisely usually fixes and which need the client.",
                },
                {
                    "title": "3 Cost & plan",
                    "who": "Sales Engineer, then the technical team.",
                    "provide": "The number of policies to price.",
                    "get": "An indicative list price and the run steps.",
                },
                {
                    "title": "4 Client results",
                    "who": "The client (leadership and operations).",
                    "provide": "A completed run.",
                    "get": "A results summary and a work list of addresses that need action.",
                },
            ]
        )
    with st.container(border=True):
        st.subheader("Data safety at a glance", anchor=False)
        for icon, text in (
            (":material/verified_user:", "Only address columns are used; every other column stays on this computer."),
            (":material/phonelink_lock:", "The app contacts the AI and Precisely only when someone clicks Ask AI to investigate and confirms. Paid pipeline runs start only from the command line, after approval."),
            (":material/share:", "Made-up examples are safe to share. Real results are for local review only (Precisely APIs Acceptable Use Policy, sections 4.1–4.2)."),
            (":material/lock:", "Client files and settings are saved in a private project folder that is never uploaded to GitHub."),
        ):
            st.markdown(f"{icon} {text}")
    with st.container(border=True):
        st.subheader("Try it in 60 seconds", anchor=False)
        st.markdown(
            '1. Open tab 1 and click "Use made-up sample client".\n'
            "2. Check the suggested columns.\n"
            "3. Open tab 2 to see the free check.\n"
            "4. Open tab 3 and enter the policy count and expected located share.\n"
            "5. Open tab 4 to see the results and the work list."
        )
    with st.container(border=True):
        st.subheader("Questions", anchor=False)
        for question, answer in (
            (
                "What does 'located automatically' mean?",
                "The address was located at an exact point or on its street. These records are eligible for flood and property data; the rest go to manual review. The share depends on the quality of the client's data and should be updated after a sample run.",
            ),
            (
                "Why is the price a list price?",
                "It uses Precisely's published rate of $0.01 per credit. Enterprise agreements are typically priced by volume, so treat it as a budgeting ceiling, not a quote. It covers the one-time clean-up of the current book.",
            ),
            (
                "Who is the work list for?",
                "Whoever maintains the client's address data, often an operations or data quality team. It lists each address that needs action, who should handle it (contact the customer, or an analyst review), and whether Precisely confirmed an AI suggestion.",
            ),
            (
                "Why is there a 'made-up / real' switch?",
                "Made-up examples let anyone see the app safely, for example in a screen share. Real results come from a completed run on this computer and stay here.",
            ),
            (
                "What never leaves this computer?",
                "The uploaded file, every column not used for addresses, the settings file and all results. The app itself sends nothing anywhere.",
            ),
        ):
            with st.expander(question):
                st.write(answer)

with onboard_tab:
    onboard_title = st.empty()
    with st.container(border=True):
        st.subheader("Client file", anchor=False)
        uploaded_file = st.file_uploader(
            "Upload the client file (CSV or Excel)",
            type=("csv", "xlsx"),
            help="Choose a CSV or Excel (.xlsx) file up to 200 MB. It stays in memory on this computer until you save the client folder.",
            key=(
                f"demo_uploader_{st.session_state.get('demo_uploader_revision', 0)}"
                if demo_mode_enabled else None
            ),
        )
        if uploaded_file is not None:
            uploaded_content = uploaded_file.getvalue()
            if (
                uploaded_file.name != st.session_state.uploaded_name
                or uploaded_content != st.session_state.uploaded_content
            ):
                st.session_state.uploaded_content = uploaded_content
                st.session_state.uploaded_name = uploaded_file.name
                st.session_state.pop("excel_sheet", None)
                _reset_mapping_state()
            st.session_state.sample_mode = False
        if st.button(
            "Use made-up sample client",
            icon=":material/table_view:",
        ):
            if demo_mode_enabled:
                st.session_state.demo_uploader_revision = (
                    st.session_state.get("demo_uploader_revision", 0) + 1
                )
            st.session_state.uploaded_content = SAMPLE_PATH.read_bytes()
            st.session_state.uploaded_name = SAMPLE_PATH.name
            st.session_state.sample_mode = True
            st.session_state.pop("excel_sheet", None)
            _reset_mapping_state()

    content = st.session_state.uploaded_content
    if not content:
        st.info("Upload a client file or choose the made-up sample client to begin.")
        columns, rows = [], []
    else:
        try:
            upload_source = (
                st.session_state.uploaded_name,
                st.session_state.get("excel_sheet"),
            )
            if st.session_state.get("parsed_upload_source") != upload_source:
                _reset_mapping_state()
                st.session_state.parsed_upload_source = upload_source
            columns, rows, sheets = _read_uploaded_file(
                st.session_state.uploaded_name,
                content,
                st.session_state.get("excel_sheet"),
            )
            if len(sheets) > 1:
                st.selectbox("Sheet", sheets, key="excel_sheet")
            elif sheets:
                st.session_state.pop("excel_sheet", None)
        except (UnicodeDecodeError, csv.Error, ValueError, OSError) as error:
            st.error(str(error))
            columns, rows = [], []

    if columns:
        st.caption(f"{len(rows)} records in the uploaded file.")
        st.caption("Preview of the uploaded file (first 3 rows, shown on this computer only)")
        st.dataframe(
            rows[:3],
            width="stretch",
            hide_index=True,
            height=36 * (min(3, len(rows)) + 1) + 4,
        )
        if Path(st.session_state.uploaded_name).suffix.casefold() == ".xlsx":
            st.caption(
                "Excel tip: if ZIP codes were stored as numbers, leading zeros may already be lost; the pre-flight check flags these."
            )
        fingerprint = _file_fingerprint()
        st.subheader("Client", anchor=False)
        with st.container(border=True):
            name_key = f"client_name_{fingerprint}"
            folder_key = f"folder_name_{fingerprint}"
            edited_key = f"folder_edited_{fingerprint}"
            if name_key not in st.session_state:
                st.session_state[name_key] = (
                    "Marova Insurance"
                    if demo_mode_enabled and (
                        st.session_state.sample_mode
                        or st.session_state.get("demo_preloaded_fingerprint") == fingerprint
                    )
                    else "Sample fictional carrier" if st.session_state.sample_mode else ""
                )
            if folder_key not in st.session_state:
                st.session_state[folder_key] = (
                    _slugify(st.session_state[name_key])
                    if st.session_state[name_key].strip()
                    else ""
                )
                st.session_state[edited_key] = False

            def _suggest_folder(name_key=name_key, folder_key=folder_key, edited_key=edited_key):
                if not st.session_state.get(edited_key):
                    typed = st.session_state[name_key]
                    st.session_state[folder_key] = _slugify(typed) if typed.strip() else ""

            def _mark_folder_edited(edited_key=edited_key):
                st.session_state[edited_key] = True

            client_name = st.text_input(
                "Client name",
                key=name_key,
                placeholder="e.g. Acme Insurance",
                on_change=_suggest_folder,
                help="The client's name as it should appear in the settings and reports.",
            )
            slug = st.text_input(
                "Folder name",
                key=folder_key,
                on_change=_mark_folder_edited,
                help="Short ID for this client: lowercase letters, numbers and hyphens, e.g. acme-insurance.",
            )
        st.subheader("Match the file columns", anchor=False)
        st.caption(
            "Each list shows every column in the uploaded file. The app has pre-selected "
            "its best guess; change one only if it is wrong. '— Not mapped —' means the "
            "file has no such column (allowed only for optional fields)."
        )
        with st.container(border=True):
            if st.session_state.get("demo_preloaded_fingerprint") == fingerprint:
                suggestions = st.session_state.demo_preloaded_mapping
            else:
                suggestions = suggest_mapping(columns)
            chosen: dict[str, str | None] = {}
            for canonical in CANONICAL_COLUMNS:
                optional = canonical not in REQUIRED_COLUMNS
                available = [column for column in columns if column not in chosen.values()]
                options = [NO_MAPPING, *available] if optional else [NO_MAPPING, *available]
                suggested = suggestions.get(canonical)
                default_index = options.index(suggested) if suggested in options else 0
                selection = st.selectbox(
                    MAPPING_LABELS[canonical],
                    options,
                    index=default_index,
                    key=f"mapping_{canonical}_{fingerprint}",
                    help=MAPPING_HELP[canonical],
                )
                chosen[canonical] = None if selection == NO_MAPPING else selection
        st.session_state.onboarding_mapping = chosen
        with onboard_title:
            app_ui.render_tab_intro(
                onboard_headline(len(rows), sum(bool(chosen.get(field)) for field in REQUIRED_COLUMNS), len(REQUIRED_COLUMNS)),
                "Prepare the file and choose the services that fit the business need.",
            )

        st.markdown(
            "**Columns ignored: these stay on this computer and are never sent to "
            "Precisely or the AI**"
        )
        unused = unused_columns(columns, chosen)
        app_ui.render_chips(unused)

        st.subheader("Data to add", anchor=False)
        with st.container(border=True):
            enrichment_help = enrichment_help_texts()
            enrichment_labels = {
                "flood_by_location": "Flood zone and risk",
                "distance_to_flood_hazard_by_location": "Distance to flood hazard",
                "property_attributes_v2": "Property attributes",
                "preciselyid_by_address": "PreciselyID (permanent location ID)",
            }
            enrichment = {
                operation: st.checkbox(
                    enrichment_labels[operation],
                    value=True,
                    help=enrichment_help[operation],
                )
                for operation in enrichment_labels
            }
        st.subheader("Optional AI settings", anchor=False)
        with st.container(border=True):
            ai_flags = {
                "narratives": st.checkbox(
                    "Switch on AI risk summaries for this client",
                    value=False,
                    help="Creates plain-language summaries from completed results. Uses AI credits when run.",
                ),
                "remediation": st.checkbox(
                    "Switch on AI suggestions for problem addresses",
                    value=False,
                    help="Suggests possible causes and address fixes for manual review. Uses AI credits when run.",
                ),
            }
        st.caption(
            "Recorded in the client's settings only; the AI runs later from the command "
            "line. Off by default until the client agrees to AI use."
        )
        missing_message = missing_required_message(chosen)
        profile_data = None
        if not client_name.strip():
            st.session_state.pop("onboarding_profile", None)
            st.warning("Enter the client name.")
        elif missing_message:
            st.session_state.pop("onboarding_profile", None)
            st.warning(missing_message)
        else:
            try:
                profile_data = build_profile(client_name, slug, chosen, enrichment, ai_flags)
                st.session_state.onboarding_profile = profile_data
                st.session_state.client_name = client_name
                st.session_state.client_slug = slug
                st.session_state.client_ai_flags = ai_flags
            except ValueError as error:
                st.session_state.pop("onboarding_profile", None)
                st.error(_profile_error_message(error))

        if profile_data is not None:
            st.subheader("Save", anchor=False)
            with st.container(border=True):
                st.download_button(
                    "Download client settings file",
                    data=serialize_profile(profile_data).encode("utf-8"),
                    file_name="client_profile.yaml",
                    mime="application/yaml",
                    icon=":material/download:",
                )
                st.caption(
                    "Saves this client's settings file to this computer only (for your records or a colleague)."
                )
                if st.button(
                    "Save client bundle for a pipeline run",
                    type="primary",
                    icon=":material/save:",
                ):
                    try:
                        save_client_bundle(
                            slug,
                            serialize_input_csv(columns, rows),
                            profile_data,
                            project_root=ROOT,
                        )
                        st.success("Saved this client's file and settings on this computer.")
                        st.session_state.saved_slug = slug
                    except FileExistsError:
                        st.error(
                            "A saved folder with that name already exists. Choose a different folder name."
                        )
                    except ValueError as error:
                        st.error(_profile_error_message(error))
                    except OSError:
                        st.error(
                            "Could not save the client file and settings. Check this computer's project-folder permissions and try again."
                        )
                st.caption(
                    "Saves the client file and settings to a private project folder (never uploaded to GitHub), ready for the run commands in tab 3. "
                    "Only the mapped address columns are saved; other columns are not kept."
                )
    else:
        with onboard_title:
            app_ui.render_tab_intro(
                "Onboard a client",
                "Prepare a client file and choose the services that fit the business need.",
            )

with header_placeholder:
    app_ui.render_header(
        client_name if columns else "",
        len(rows) if columns else None,
    )

with preflight_tab:
    preflight_title = st.empty()
    if not st.session_state.uploaded_content:
        st.info(":material/upload_file: Upload a client file in tab 1 to check its quality.")
    elif not columns:
        st.info("Upload a readable CSV or Excel file in tab 1 to see the free check.")
    elif missing_required_message(
        st.session_state.get("onboarding_mapping") or suggest_mapping(columns)
    ):
        st.info("Map all required columns in tab 1 first.")
    else:
        mapping = st.session_state.get("onboarding_mapping") or suggest_mapping(columns)
        checks = preflight_checks(rows, mapping)
        summaries = preflight_group_summaries(checks)
        summary_by_group = {str(row["group"]): row for row in summaries}
        with preflight_title:
            app_ui.render_tab_intro(
                preflight_headline(int(summary_by_group["No issues found"]["count"]), len(rows)),
                f"A free check on {_client_file(client_name)}, before any credits are spent.",
            )
        with st.container(border=True):
            app_ui.render_share_chart(summaries)
            app_ui.render_kpi_row([
                {
                    "label": group,
                    "value": (
                        f"{summary_by_group[group]['count']} "
                        f"({summary_by_group[group]['percentage']:.0f}%)"
                    ),
                    "tone": tone,
                }
                for group, tone in (
                    ("Needs client follow-up", "pink"),
                    ("Precisely usually fixes", "purple"),
                    ("Review", "yellow"),
                    ("No issues found", "gray"),
                )
            ])
        st.caption(
            "Grouping reflects what Precisely corrected in our reference run: most missing ZIPs were fixed automatically, missing house numbers were not."
        )
        displayed_checks = display_preflight_rows(checks)
        st.caption(
            "A record can have more than one issue, so the table can add up to more than the file. The cards count each record once, in its most serious group."
        )
        st.dataframe(
            displayed_checks,
            width="stretch",
            hide_index=True,
            height=36 * (len(displayed_checks) + 1) + 4,
            column_config={
                "Check": st.column_config.TextColumn("Check", width="medium"),
                "Group": st.column_config.TextColumn("Group", width="medium"),
                "What it means": st.column_config.TextColumn("What it means", width="large"),
                "Records with this issue": st.column_config.NumberColumn("Records with this issue", format="%,d"),
                "Out of": st.column_config.NumberColumn("Out of", format="%,d"),
                "Percent": st.column_config.ProgressColumn(
                    "Share of file", min_value=0, max_value=100, format="%.0f%%"
                ),
            },
        )
    if not columns or missing_required_message(st.session_state.get("onboarding_mapping") or {}):
        with preflight_title:
            app_ui.render_tab_intro("Pre-flight check", "Check the file before any credits are spent.")

with results_tab:
    results_title = st.empty()
    available_profiles = [DEFAULT_PROFILE_PATH]
    available_profiles.extend(
        sorted((ROOT / "data" / "clients").glob("*/client_profile.yaml"))
    )
    profile_options: dict[str, Path] = {}
    for path in available_profiles:
        client_profile = load_client_profile(path)
        label = str(client_profile.data["client"]["name"]) or path.parent.name
        if label in profile_options:
            label = f"{label} ({path.parent.name})"
        profile_options[label] = path
    selected_label = st.selectbox("Client", list(profile_options))
    profile_path = profile_options[selected_label]
    profile = load_client_profile(profile_path)
    summary_path = profile.reports_dir / "before_after_summary.csv"

with cost_tab:
    cost_title = st.empty()
    if not st.session_state.uploaded_content:
        st.info(":material/request_quote: Upload a client file in tab 1 to estimate the full book.")
    elif not columns:
        st.info("Upload a readable CSV or Excel file in tab 1 to estimate the full book.")
    elif missing_required_message(
        st.session_state.get("onboarding_mapping") or suggest_mapping(columns)
    ):
        st.info("Map all required columns in tab 1 first.")
    else:
        mapping = st.session_state.get("onboarding_mapping") or suggest_mapping(columns)
        current_client_name = client_name
        planning_name = current_client_name.strip() or (
            "Marova Insurance" if demo_mode_enabled else "Sample fictional carrier"
        )
        profile_data = st.session_state.get("onboarding_profile")
        slug = (
            st.session_state.get("client_slug", _slugify(planning_name))
            if profile_data is not None
            else _slugify(planning_name)
        )
        if profile_data is None:
            profile_data = build_profile(planning_name, slug, mapping)
        checks = preflight_checks(rows, mapping)
        follow_up = next(
            row for row in preflight_group_summaries(checks)
            if row["group"] == "Needs client follow-up"
        )
        st.metric(
            "This file needs client follow-up (pre-flight)",
            f"{follow_up['percentage']:.0f}%",
        )
        policies_to_price = st.number_input(
            "Policies to price (full book)",
            min_value=1,
            value=max(len(rows), 1),
            step=1000,
            help="Enter the total number of policies in the book you want to clean up.",
        )
        st.caption(
            f"The uploaded file has {len(rows)} records. Prices are for the number of policies entered here (e.g. the client's full book)."
        )
        include_address_verification = st.checkbox(
            "Include Address Verification (mail delivery)",
            value=True,
            help="Checks that mail reaches the customer. Priced for the rollout; this demo run uses Geocoding only.",
        )
        eligible_fraction = _fraction(next(
            (row.get("after", "") for row in read_summary_csv(summary_path) if row.get("metric") == "Eligible"),
            "",
        ))
        share = eligible_fraction[0] / eligible_fraction[1] if eligible_fraction else None
        breakdown = rollout_cost_breakdown(
            profile_data, int(policies_to_price), share if share is not None else 0,
            include_address_verification,
        )
        total_credits = breakdown["total_credits"]
        credit_total = f"{total_credits:,.2f}".rstrip("0").rstrip(".")
        credits_per_policy = breakdown["credits_per_policy"]
        list_price = total_credits / 100
        usd_display = f"${list_price:,.2f}"
        with cost_title:
            app_ui.render_tab_intro(
                cost_headline(total_credits, int(policies_to_price), list_price),
                f"Estimate for {_client_file(current_client_name)}.",
            )
        app_ui.render_kpi_row(
            [
                {"label": "Estimated credits", "value": credit_total},
                {
                    "label": "Average credits per policy",
                    "value": f"{credits_per_policy:,.2f}",
                },
                {"label": "List price", "value": usd_display},
            ],
            columns=3,
        )
        if share is not None:
            st.markdown(
                f"The sample run located {share:.0%} at once. The rest are enriched once corrected."
            )
            app_ui.render_kpi_row([
                {"label": "Day one", "value": f'{breakdown["day_one_credits"]:,.0f}'},
                {"label": "After corrections", "value": f'{breakdown["after_credits"]:,.0f}'},
                {"label": "Total", "value": f'{total_credits:,.0f}'},
            ], columns=3)
        display_rows = format_cost_breakdown_rows(breakdown["rows"])
        st.dataframe(
            display_rows,
            width="stretch",
            hide_index=True,
            height=36 * (len(display_rows) + 1) + 4,
            column_config={
                "Calls": st.column_config.NumberColumn("Calls", format="%,d"),
                "Credits each": st.column_config.NumberColumn("Credits each", format="%.2f"),
                "Credits": st.column_config.NumberColumn("Credits", format="localized"),
            },
        )
        st.markdown(f"**Total: {credit_total} credits**")
        st.caption(_average_credits_formula(breakdown["enabled_item_rate"], include_address_verification))
        st.caption("Calls are rounded to whole records; at full-book volumes the rounding is negligible.")
        st.caption(
            "List price at 1 credit = $0.01 (Precisely published rate). Enterprise agreements are typically priced by volume; risk-use fees, integration and ongoing costs for new and changed policies are not included. This is the one-time clean-up of the current book."
        )

        st.subheader("Next step: run the clean-up (technical team)", anchor=False)
        expected_profile = ROOT / "data" / "clients" / slug / "client_profile.yaml"
        if expected_profile.is_file():
            saved_status = f"✓ Saved in data/clients/{slug}/"
        else:
            saved_status = "Not saved yet"
        st.markdown(f"1. Save the client bundle (tab 1): **{saved_status}**")
        st.markdown("2. The technical team runs a free dry run to confirm the planned calls and credits.")
        st.markdown(
            "3. After approval, they run the clean-up. A budget limit stops any run that would go over."
        )
        st.markdown("4. Results then appear in tab 4.")
        profile_path = f"data/clients/{slug}/client_profile.yaml"
        ai_flags = st.session_state.get("client_ai_flags", {})
        with st.expander("Commands for the technical team"):
            st.code("\n\n".join(_stage_commands(profile_path, ai_flags, bool(profile_data.get("comparison", {}).get("enabled")))), language="powershell")
            st.caption("Copy a command, review the dry-run plan, and get approval before any paid run.")
    if not columns or missing_required_message(st.session_state.get("onboarding_mapping") or {}):
        with cost_title:
            app_ui.render_tab_intro("Cost & plan", "Estimate a one-time clean-up for the full book.")

with results_tab:
    result_kpis = load_summary_kpis(summary_path)
    if result_kpis:
        eligible = next(
            (row.get("after", "") for row in read_summary_csv(summary_path) if row.get("metric") == "Eligible"),
            "",
        )
        result_title = results_headline(
            result_kpis["correct_building"]["before"],
            result_kpis["correct_building"]["after"],
            eligible,
            result_kpis["has_reference"],
        )
    else:
        result_title = "Client results are not available yet."
    with results_title:
        app_ui.render_tab_intro(
            result_title,
            f"From the completed run for {profile.data['client']['name']}.",
        )
    _display_kpis(summary_path)

    work_heading, work_label, work_mode = st.columns([1, 0.13, 1], vertical_alignment="center")
    work_heading.subheader("Work list for your analysts", anchor=False)
    work_label.markdown("Show:")
    st.write(
        "Work list for whoever maintains the client's address data: what to fix, who does it (customer outreach or analyst review), and whether Precisely confirmed an AI fix."
    )
    display_mode = work_mode.radio(
        "Show:",
        ["Made-up examples (safe to share)", "Real results on this computer"],
        horizontal=True,
        key="results_mode",
        label_visibility="collapsed",
    )
    demo_mode = display_mode == "Made-up examples (safe to share)"
    if demo_mode:
        queue, _is_demo = load_exception_queue(
            profile.run_dir,
            demo_path=DEMO_EXCEPTIONS_PATH,
            demo=True,
        )
        st.info("Made-up example data, not Precisely output.")
    else:
        queue, _is_demo = load_exception_queue(profile.run_dir)
        st.warning(
            "Live Precisely results. Private screen share only."
        )
        input_rows = None
        try:
            _, input_rows, _ = _read_uploaded_file(
                profile.input_path.name, profile.input_path.read_bytes()
            )
        except (OSError, UnicodeDecodeError, csv.Error, ValueError):
            st.info("The input file is unavailable, so addresses on file cannot be displayed.")
        if result_kpis:
            if input_rows is None:
                st.info("The input file is unavailable, so the work path cannot be displayed.")
            else:
                app_ui.render_work_path(work_path(len(input_rows), queue))
        if queue and all(row.get("suggestion_status") == "Not available" for row in queue):
            st.info("AI suggestions are off for this client until a labelled calibration sample is available.")
    if not queue:
        st.info(":material/task_alt: No work items are available for this client.")
    else:
        if demo_mode:
            queue = join_sales_worklist_addresses(queue, [], {}, demo=True)
        else:
            try:
                queue = join_sales_worklist_addresses(
                    queue, input_rows or [], profile.column_mapping
                )
            except ValueError as error:
                st.warning(f"Addresses on file cannot be matched: {error}")
                queue = [{**row, "input_address": {}} for row in queue]
        queue = sort_sales_worklist(queue)
        if demo_mode:
            decision_log = None
            decision_entries = st.session_state.setdefault("made_up_worklist_decisions", [])
        else:
            decision_log = decisions_lib.decision_log_path(profile.run_dir)
            decision_entries = decisions_lib.read_log(decision_log)
        latest_decisions = decisions_lib.latest_decisions(decision_entries)
        if not demo_mode:
            confirmed_addresses = decisions_lib.load_precisely_confirmed(profile.run_dir)
            queue = [
                {**row, "precisely_address": confirmed_addresses[str(row.get("policy_id"))]}
                if decisions_lib.is_confirmed_fix(row) and str(row.get("policy_id")) in confirmed_addresses
                else row
                for row in queue
            ]
        queue = decisions_lib.open_cases(queue, latest_decisions)
        has_input_address = not demo_mode or any(
            row["Address on file"] for row in format_sales_worklist_rows(queue)
        )
        action_values = sorted({str(row.get("next_action", "")) for row in queue})
        verification_values = sorted(
            {str(row.get("verification_status", "")) for row in queue}
        )
        action_column, verification_column = st.columns(2)
        with action_column:
            action_filter = st.selectbox(
                "Filter by next action",
                ["All", *action_values],
                format_func=lambda value: "All" if value == "All" else friendly_label(value),
            )
        with verification_column:
            verification_filter = st.selectbox(
                "Filter by verification status",
                ["All", *verification_values],
                format_func=lambda value: "All" if value == "All" else friendly_label(value),
            )
        filtered = [
            row for row in queue
            if (action_filter == "All" or row.get("next_action") == action_filter)
            and (
                verification_filter == "All"
                or row.get("verification_status") == verification_filter
            )
        ]
        formatted_rows = format_sales_worklist_rows(filtered)
        table_rows = [
            {
                "Policy ID": row["Policy ID"],
                **({"Address on file": row["Address on file"]} if has_input_address else {}),
                "Next action": row["Next action"],
                "Why flagged": row["Why flagged"],
                "Status": row["Status"],
            }
            for row in formatted_rows
        ]
        st.caption(sales_worklist_count_line(filtered))
        selected_rows: list[Any] = []
        index = 0
        if filtered:
            table_identity = repr(
                (str(profile_path), display_mode, action_filter, verification_filter, filtered)
            )
            table_key = "exceptions_table_" + hashlib.sha256(table_identity.encode()).hexdigest()[:16]
            st.caption("Click a row to open the case.")
            table_event = st.dataframe(
                table_rows,
                width="stretch",
                hide_index=True,
                height=36 * (min(len(table_rows), 10) + 1) + 4,
                column_config={
                    "Policy ID": st.column_config.TextColumn("Policy ID", width=100),
                    **({"Address on file": st.column_config.TextColumn("Address on file", width=260)} if has_input_address else {}),
                    "Next action": st.column_config.TextColumn("Next action", width=145),
                    "Why flagged": st.column_config.TextColumn("Why flagged", width=250),
                    "Status": st.column_config.TextColumn("Status", width=170),
                },
                key=table_key,
                on_select="rerun",
                selection_mode="single-row",
            )
            selected_rows = list(table_event.selection.rows)
            index = selected_rows[0] if selected_rows else 0
            if not isinstance(index, int) or not 0 <= index < len(filtered):
                index = 0
        else:
            st.info("No work items match these filters.")
        if demo_mode:
            try:
                st.download_button(
                    "Download work list (Excel)",
                    data=made_up_exception_workbook(filtered),
                    file_name="made-up-work-list.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    icon=":material/download:",
                )
            except ValueError as error:
                st.error(str(error))
        else:
            if st.button(
                "Save work list to the client folder",
                key="save_real_work_list",
                type="primary",
                icon=":material/save:",
            ):
                try:
                    saved_path = save_real_exception_workbook(
                        filtered,
                        profile.run_dir,
                        ROOT / "data" / "output",
                    )
                except ValueError as error:
                    st.error(str(error))
                except PermissionError:
                    st.session_state.pop("real_work_list_path", None)
                    st.warning("The work list is open in Excel. Close it, then save again.")
                except OSError:
                    st.session_state.pop("real_work_list_path", None)
                    st.error("The work list could not be saved. Check that the client folder is available.")
                else:
                    st.session_state.real_work_list_path = str(saved_path)
            saved_path = st.session_state.get("real_work_list_path")
            if saved_path:
                full_path = Path(saved_path).resolve()
                st.success(f"Work list saved to: {full_path}")
                st.caption(app_data_real_save_explanation)
                if st.button("Open folder", icon=":material/folder_open:", key="open_work_list_folder"):
                    if not open_folder(full_path.parent):
                        st.info(f"Open this folder on your computer: {full_path.parent}")

        just_decided = st.session_state.get("just_decided")
        if just_decided and selected_rows:
            st.session_state.pop("just_decided", None)
            just_decided = None
        live_enabled = live_panel.live_settings(ROOT)["enabled"]
        if just_decided:
            _render_decision_confirmation(just_decided, decision_log)
        elif filtered:
            selected_case = filtered[index]
            _render_exception_detail(selected_case)
            if decisions_lib.is_confirmed_fix(selected_case):
                _render_decision_box(selected_case, decision_log)
            already_confirmed = (
                str(selected_case.get("verification_status", "")).strip().casefold() == "verified by precisely"
            )
            # Offer a live investigation only where the overnight run left work to do.
            if live_enabled and selected_case.get("next_action") == "analyst_review" and not already_confirmed:
                case_display = format_sales_worklist_rows([selected_case])[0]

                def record_live_decision(decision: str, result: dict[str, Any], case=selected_case, shown=case_display) -> None:
                    if decision != "approved" or not result.get("proposed_fix") or result.get("fix_confirmed") is not True:
                        return
                    entry = decisions_lib.decision_entry(
                        policy_id=shown["Policy ID"],
                        decision="approved",
                        address_on_file=shown["Address on file"],
                        corrected=decisions_lib.corrected_address(result["proposed_fix"], case.get("input_address")),
                        confirmed_by_precisely=True,
                        source="Live investigation",
                    )
                    _record_worklist_decision(entry, decision_log)
                    st.session_state["just_decided"] = entry
                    st.rerun()

                with st.container(border=True):
                    st.markdown("**Investigate this case live**")
                    live_panel.render_investigate(
                        case_key=str(selected_case.get("policy_id", "case")),
                        address=case_display["Address on file"],
                        real_mode=not demo_mode,
                        root=ROOT,
                        run_dir=profile.run_dir,
                        saved_fallback=lambda: st.caption("The saved result for this case is in the case card above."),
                        on_decision=record_live_decision,
                    )
        _render_corrections(latest_decisions, demo_mode, profile.run_dir)
        _render_audit_trail(list(decision_entries), demo_mode)
        if live_enabled:
            with st.expander("Quick address check"):
                st.caption(
                    "Check one address on the spot, for example during a call or a new quote. "
                    "Nothing is saved to the work list."
                )
                live_panel.render_investigate(
                    case_key="typed_address",
                    address="",
                    real_mode=not demo_mode,
                    root=ROOT,
                    run_dir=profile.run_dir,
                    allow_typed=True,
                    record_decisions=False,
                )

app_ui.render_footer()
