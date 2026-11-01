"""Local-only client onboarding and results viewer. Never run this in production."""

import csv
import hashlib
from io import StringIO
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

from address_intelligence.app_data import (
    defect_chart_data,
    defect_chart_rows,
    format_cost_breakdown_rows,
    format_exception_table_rows,
    load_exception_queue,
    load_fallback_kpis,
    load_summary_kpis,
    made_up_exception_workbook,
    open_folder,
    plain_flag_reason,
    REAL_SAVE_EXPLANATION as app_data_real_save_explanation,
    read_summary_csv,
    save_real_exception_workbook,
    selected_policy_id,
    zero_category_note,
)
from address_intelligence.client_profile import DEFAULT_PROFILE_PATH, load_client_profile
from address_intelligence.onboarding import (
    CANONICAL_COLUMNS,
    REQUIRED_COLUMNS,
    build_profile,
    cost_breakdown,
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
    displayed, _denominator = fraction.split("/", 1)
    return fraction, int(displayed) if displayed.isdigit() else 0


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
            "label": "Coarse location changes flood-risk tier",
            "value": flood_value,
            "delta": (
                f"{metric_count('Coarse location understated verified risk')} under-, "
                f"{metric_count('Coarse location overstated verified risk')} over-priced"
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
    st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)
    st.altair_chart(
        app_ui.build_results_chart(rows, defect_chart_data(kpis)),
        width="stretch",
    )
    note = zero_category_note(rows)
    if note:
        st.caption(note)


def _display_kpis(summary_path: Path) -> None:
    if not summary_path.is_file():
        st.info(":material/insights: No completed run yet. Run the clean-up from tab 3, step 4.")
        return
    kpis = load_summary_kpis(summary_path)
    if kpis is None:
        st.info(":material/insights: No completed run yet. Run the clean-up from tab 3, step 4.")
        return
    if kpis["has_reference"]:
        cards = []
        for tile in _build_kpi_tile_values(summary_path):
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
        app_ui.render_kpi_row(cards)
        _render_accuracy_chart(kpis)
        return

    app_ui.render_kpi_row(load_fallback_kpis(summary_path))
    st.info("Accuracy against a reference needs a labelled sample; not available for this client.")


def _average_credits_formula(enabled_item_rate: Any) -> str:
    return (
        "Average credits per policy = 1 + verified share × "
        f"{enabled_item_rate:,.2f} credits for enabled data items."
    )


def _format_address(suggestion: Any) -> str:
    if not isinstance(suggestion, dict) or not suggestion:
        return "No suggested address."
    line = str(suggestion.get("line1", "")).strip()
    city_zip = " ".join(
        str(part).strip()
        for part in (suggestion.get("city", ""), suggestion.get("zip5", ""))
        if part
    )
    address = ", ".join(part for part in (line, city_zip) if part)
    return address or "No suggested address."


def _verification_text(row: dict[str, Any]) -> str:
    verification = row.get("verification", {})
    if row.get("suggestion_status") == "rejected":
        return "Rejected"
    if isinstance(verification, dict) and verification.get("verified_correct") is True:
        return "Confirmed correct"
    if isinstance(verification, dict) and verification.get("verified_incorrect") is True:
        return "Confirmed incorrect"
    if (
        row.get("verification_status") == "did not verify"
        or isinstance(verification, dict) and verification.get("not_verified") is True
    ):
        return "Not verified"
    return friendly_label(str(row.get("verification_status", "not_started")))


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
app_ui.render_header(real_mode=real_mode)

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

with start_tab:
    app_ui.render_tab_intro(
        "Start here",
        "This tool helps a Solutions Architect assess a prospect's address data, price a clean-up, and show the client what they receive after the run. It runs only on this computer.",
    )
    st.caption(
        "Default figures come from a reference run on 200 synthetic records; replace them with the client's own results after a sample run."
    )
    with st.container(border=True):
        st.subheader("How it works", anchor=False)
        app_ui.render_journey_cards(
            [
                {
                    "title": "1 Onboard",
                    "who": "Solutions Architect.",
                    "provide": "The client's address file (CSV or Excel).",
                    "get": "A settings file that tells the pipeline which column is which.",
                },
                {
                    "title": "2 Pre-flight",
                    "who": "Solutions Architect.",
                    "provide": "Nothing more.",
                    "get": "A free check showing which issues Precisely usually fixes and which need the client.",
                },
                {
                    "title": "3 Cost & plan",
                    "who": "Solutions Architect, then the technical team.",
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
            (":material/phonelink_lock:", "This app never contacts Precisely or the AI. Paid runs start only from the command line, after approval."),
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
            "4. Open tab 3, enter 300,000 policies and set the verified share to 100% (≈$27,030 at list price).\n"
            "5. Open tab 4 to see the results and the work list."
        )
    with st.container(border=True):
        st.subheader("Questions", anchor=False)
        for question, answer in (
            (
                "What does 'verified to the building' mean?",
                "Precisely confirms the address down to the exact building. Only those addresses get flood and property data; the rest go to manual review. The share depends on the quality of the client's data: tab 3 starts at 85%, the rate seen in our reference run, and should be updated once a sample of the client's own file has been run.",
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
    app_ui.render_tab_intro(
        "Onboard a client",
        "Prepare a client file and choose the services that fit the business need.",
    )
    with st.container(border=True):
        st.subheader("Client file", anchor=False)
        uploaded_file = st.file_uploader(
            "Upload the client file (CSV or Excel)",
            type=("csv", "xlsx"),
            help="Choose a CSV or Excel (.xlsx) file up to 200 MB. It stays in memory on this computer until you save the client folder.",
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
                    "Sample fictional carrier" if st.session_state.sample_mode else ""
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

with preflight_tab:
    app_ui.render_tab_intro(
        "Pre-flight check",
        "See which file issues are usually corrected automatically and which need attention.",
    )
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
        st.caption("Free check on the submitted file; no credits used.")
        summaries = preflight_group_summaries(checks)
        summary_by_group = {str(row["group"]): row for row in summaries}
        app_ui.render_kpi_row(
            [
                {
                    "label": group,
                    "value": (
                        f"{summary_by_group[group]['count']} "
                        f"({summary_by_group[group]['percentage']:.0f}%)"
                    ),
                    "tone": tone,
                }
                for group, tone in (
                    ("Needs client follow-up", "danger"),
                    ("Precisely usually fixes", "positive"),
                    ("Review", "warning"),
                    ("No issues found", "neutral"),
                )
            ]
        )
        app_ui.render_share_chart(summaries)
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

with cost_tab:
    app_ui.render_tab_intro(
        "Cost & plan",
        "Estimate a one-time clean-up for the full book and prepare the next steps.",
    )
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
        client_name = st.session_state.get("client_name", "Sample fictional carrier")
        slug = st.session_state.get(
            "client_slug", _slugify(client_name)
        )
        profile_data = st.session_state.get("onboarding_profile")
        if profile_data is None:
            profile_data = build_profile(client_name, slug, mapping)
        checks = preflight_checks(rows, mapping)
        follow_up = next(
            row for row in preflight_group_summaries(checks)
            if row["group"] == "Needs client follow-up"
        )
        slider, follow_up_column = st.columns([2, 1])
        eligible_percent = slider.slider(
            "Expected share verified to the exact building",
            min_value=0,
            max_value=100,
            value=85,
            step=1,
            format="%d%%",
            help=(
                "Flood and property data are only bought for addresses Precisely verifies to the building. "
                "Starts at 85% (reference run); replace with the client's own rate after a sample run."
            ),
        )
        follow_up_column.metric(
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
        share = eligible_percent / 100
        breakdown = cost_breakdown(profile_data, int(policies_to_price), share)
        total_credits = breakdown["total_credits"]
        credit_total = f"{total_credits:,.0f}"
        credits_per_policy = breakdown["credits_per_policy"]
        list_price = total_credits / 100
        usd_display = f"${list_price:,.0f}" if list_price >= 1000 else f"${list_price:,.2f}"
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
        st.dataframe(
            format_cost_breakdown_rows(breakdown["rows"]),
            width="stretch",
            hide_index=True,
            height=36 * (len(breakdown["rows"]) + 1) + 4,
            column_config={
                "Calls": st.column_config.NumberColumn("Calls", format="%,d"),
                "Credits each": st.column_config.NumberColumn("Credits each", format="%.2f"),
                "Credits": st.column_config.NumberColumn("Credits", format="localized"),
            },
        )
        st.caption(_average_credits_formula(breakdown["enabled_item_rate"]))
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
        st.markdown(f"1. Save the client bundle (tab 1) — **{saved_status}**")
        st.markdown("2. Dry run each stage (no cost) to see planned calls and credits.")
        st.markdown(
            "3. After approval, run each stage with `--execute`. The budget gate stops any run that exceeds the remaining credits."
        )
        st.markdown("4. Build the reports; results then appear in tab 4.")
        profile_path = f"data/clients/{slug}/client_profile.yaml"
        ai_flags = st.session_state.get("client_ai_flags", {})
        with st.expander("Commands for the technical team"):
            st.code("\n\n".join(_stage_commands(profile_path, ai_flags, bool(profile_data.get("comparison", {}).get("enabled")))), language="powershell")
            st.caption("Copy a command, review the dry-run plan, and get approval before any paid run.")

with results_tab:
    app_ui.render_tab_intro(
        "Client results",
        "What the client receives after the run: a summary for leadership and a work list for operations."
    )
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
    st.caption(f"From the completed run for {profile.data['client']['name']} · aggregates only")
    summary_path = profile.reports_dir / "before_after_summary.csv"
    _display_kpis(summary_path)

    st.subheader("Exceptions", anchor=False)
    st.write(
        "Work list for the client's operations team: what to fix, who does it (customer outreach or analyst review), and whether Precisely confirmed an AI fix."
    )
    display_mode = st.radio(
        "Show:",
        ["Made-up examples (safe to share)", "Real results on this computer"],
        horizontal=True,
        key="results_mode",
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
            "Real Precisely results on this computer. Fine for local review; do not share screenshots or exports of individual records outside Precisely (Acceptable Use Policy, sections 4.1–4.2)."
        )
        if queue and all(row.get("suggestion_status") == "Not available" for row in queue):
            st.info("AI suggestions are off for this client until a labelled calibration sample is available.")
    if not queue:
        st.info(":material/task_alt: No work items are available for this client.")
    else:
        action_values = sorted({str(row.get("next_action", "")) for row in queue})
        verification_values = sorted(
            {str(row.get("verification_status", "")) for row in queue}
        )
        action_filter = st.selectbox(
            "Filter by next action",
            ["All", *action_values],
            format_func=lambda value: "All" if value == "All" else friendly_label(value),
        )
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
        table_rows = format_exception_table_rows(filtered)
        table_event = st.dataframe(
            table_rows,
            width="stretch",
            hide_index=True,
            height=36 * (len(table_rows) + 1) + 4,
            column_config={
                "Confidence": (
                    None
                    if all(
                        str(row.get("confidence", "")).strip() in ("", "Not available")
                        for row in filtered
                    )
                    else st.column_config.TextColumn("Confidence", width="small")
                ),
                "Policy ID": st.column_config.TextColumn("Policy ID", width="medium"),
            },
            key="exceptions_table",
            on_select="rerun",
            selection_mode="single-row",
        )
        filtered_ids = [str(row.get("policy_id", "")) for row in filtered]
        selected_rows = list(table_event.selection.rows)
        selected_from_table = selected_policy_id(filtered, selected_rows)
        if selected_from_table in filtered_ids:
            st.session_state.exception_detail_id = selected_from_table
        current_id = st.session_state.get("exception_detail_id", "")
        default_index = filtered_ids.index(current_id) if current_id in filtered_ids else 0
        selected_id = st.selectbox(
            "Exception detail",
            filtered_ids,
            index=default_index,
            key="exception_detail_id",
        )
        if demo_mode:
            try:
                st.download_button(
                    "Save work list (Excel)",
                    data=made_up_exception_workbook(filtered),
                    file_name="made-up-work-list.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    icon=":material/download:",
                )
            except ValueError as error:
                st.error(str(error))
        else:
            if st.button(
                "Save work list (Excel)",
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

        detail = next(row for row in filtered if str(row.get("policy_id", "")) == selected_id)
        left, right = st.columns(2)
        left.markdown("**Suggested address**")
        left.write(_format_address(detail.get("suggestion")))
        right.markdown("**Why it was flagged**")
        right.write(detail.get("rationale") or plain_flag_reason(detail.get("manual_review_reason", "")))
        with st.container(border=True):
            st.markdown("**Precisely verification**")
            st.write(_verification_text(detail))
            st.markdown("**Summary for operations**")
            st.write(detail.get("narrative", ""))
        st.info(
            "AI proposes, Precisely verifies: no AI change is applied unless Precisely confirms it."
        )

app_ui.render_footer()
