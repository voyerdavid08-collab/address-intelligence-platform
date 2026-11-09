"""Reusable local-app presentation tokens and components."""

from __future__ import annotations

import base64
import re
import uuid
from decimal import Decimal, DecimalException
from html import escape
from pathlib import Path
from typing import Any

import altair as alt
import streamlit as st

FONT_STACK = '"Segoe UI", -apple-system, "Helvetica Neue", Arial, sans-serif'
COLORS = {
    "brand_dark": "#39006B",
    "brand": "#8017E1",
    "pink": "#E5007E",
    "yellow": "#FAB512",
    "grey": "#D9DBEB",
    "text": "#1B1433",
    "muted": "#5F5A73",
    "border": "#E6E3F0",
    "surface": "#F7F6FB",
    "tint": "#F5EFFC",
    "pink_tint": "#FDE8F3",
    "white": "#FFFFFF",
    "success": "#8017E1",
    "success_tint": "#F5EFFC",
    "warning": "#B7791F",
    "warning_tint": "#FFF6E5",
    "danger": "#B42318",
    "danger_tint": "#FDEDEC",
    "before": "#B9B2CC",
}

HELP_TEXT = {
    "Day one": "Address checks for every policy, plus flood and property data for the policies located at once.",
    "After corrections": "Flood and property data for the remaining policies, once the team or the customer corrects them.",
    "Needs client follow-up": "Information only the customer can give, such as a missing house number.",
    "Precisely usually fixes": "Issues such as a missing ZIP, which were usually corrected automatically in our reference run.",
    "Review": "Issues a person should look at.",
    "No issues found": "No problem visible in the file. The full run can find issues a file check cannot see.",
    "Right address": "Share of records that point to the correct building, measured against the reference answers.",
    "Flawed records fully corrected": "Records that started with a known error and now match the reference answer in full, including the unit.",
    "City-level location changes flood-risk tier": "Properties with an exact location that land in a different flood tier when placed at city level. Under-priced means the tier was too low. Over-priced means it was too high.",
    "AI fixes verified": "AI suggestions that Precisely checked and confirmed.",
    "Match level": "How closely the address was placed: building, street, or ZIP and city.",
    "AI cause": "What the AI found as the likely cause of the problem.",
    "Confidence": "How often the AI reached the same cause across repeat checks. It does not measure whether the address is right.",
}

_HELP_ALIASES = {"Needs customer follow-up": "Needs client follow-up"}


def help_icon_html(label: str) -> str:
    """Return an accessible help icon for a recognized label, otherwise an empty string."""
    text = HELP_TEXT.get(_HELP_ALIASES.get(label, label))
    if text is None:
        return ""
    tooltip_id = f"ai-help-{uuid.uuid4().hex}"
    return (
        '<span class="ai-help">'
        f'<button type="button" class="ai-help-button" aria-label="Help: {escape(label, quote=True)}" '
        f'aria-describedby="{tooltip_id}">'
        '<svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" fill="currentColor">'
        '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 18a8 8 0 1 1 0-16 8 8 0 0 1 0 16zm-.9-3h1.8v-1.8h-1.8V17zm.9-10a3.1 3.1 0 0 0-3.1 3h1.8a1.3 1.3 0 1 1 2.2 1c-.9.8-1.8 1.3-1.8 2.9h1.8c0-1 .4-1.3 1.2-2a3.1 3.1 0 0 0-2.1-4.9z"/>'
        '</svg></button>'
        f'<span id="{tooltip_id}" class="ai-help-tooltip" role="tooltip">{escape(text)}</span>'
        '</span>'
    )


_GLOBAL_CSS = f"""
<style>
:root {{
  --ai-brand-dark: {COLORS["brand_dark"]}; --ai-brand: {COLORS["brand"]};
  --ai-text: {COLORS["text"]}; --ai-muted: {COLORS["muted"]};
  --ai-border: {COLORS["border"]}; --ai-surface: {COLORS["surface"]};
  --ai-white: {COLORS["white"]}; --ai-radius: 12px; --ai-section-spacing: 20px;
}}
.stApp {{ background: var(--ai-surface); color: var(--ai-text); }}
[data-testid="stMainBlockContainer"] {{
  max-width: 1240px; margin: 0 auto; padding-top: 16px; padding-right: 32px;
  padding-bottom: 32px; padding-left: 32px;
}}
header[data-testid="stHeader"] {{ display:none; height:0; }}
h1, h2, h3, h4, [data-testid="stHeadingWithActionElements"] {{
  color: var(--ai-brand-dark) !important; letter-spacing: -0.02em;
}}
[data-testid="stHeadingWithActionElements"] button {{ display: none; }}
[data-testid="stTabs"] [role="tab"] {{
  color: var(--ai-muted); font-weight: 600; padding: 14px 16px;
  white-space: nowrap; border-bottom: 3px solid transparent;
}}
[data-testid="stTabs"] [role="tab"] [role="img"] {{
  display: inline-grid !important; place-items: center; vertical-align: middle !important;
  width: 24px; height: 24px; margin-right: 6px; border-radius: 50%;
  font-size: 15px; color: var(--ai-brand-dark); background: {COLORS["grey"]};
}}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] {{
  color: var(--ai-brand-dark); border-bottom-color: {COLORS["pink"]};
}}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] [role="img"] {{
  color: var(--ai-white); background: var(--ai-brand);
}}
[data-testid="stTabs"] [role="tablist"] {{ overflow-x: auto; background: var(--ai-white); }}
[data-testid="stTabs"] [role="tabpanel"] [data-testid="stVerticalBlock"] {{
  gap: var(--ai-section-spacing) !important;
}}
[data-testid="stTabs"] [data-baseweb="tab-highlight"] {{ background-color: {COLORS["pink"]}; }}
[data-testid="stTabs"] .react-aria-SelectionIndicator {{ background-color: {COLORS["pink"]}; }}
[data-testid="stTabs"] [role="tab"]:focus-visible,
button:focus-visible, input:focus-visible, textarea:focus-visible,
[role="combobox"]:focus-visible {{
  outline: 3px solid var(--ai-brand) !important; outline-offset: 2px;
}}
.ai-help {{ position:relative; display:inline-block; vertical-align:middle; margin-left:4px; line-height:1; }}
.ai-help-button {{
  display:inline-flex; align-items:center; justify-content:center; padding:0; border:0;
  background:transparent; color:inherit; opacity:.75; cursor:help; vertical-align:middle;
}}
.ai-help-button:hover, .ai-help-button:focus-visible {{ opacity:1; }}
.ai-help-tooltip {{
  display:none; position:absolute; z-index:9999; bottom:calc(100% + 6px); left:50%;
  transform:translateX(-50%); box-sizing:border-box; width:max-content;
  max-width:min(320px, calc(100vw - 32px)); max-height:calc(100vh - 32px);
  overflow:auto; padding:6px 12px; border:0; border-radius:10px; background:#F7F6FB;
  color:#1B1433; font-size:14px; font-weight:400; line-height:1.6;
  text-align:left; white-space:normal; box-shadow:0 1px 4px rgba(0,0,0,.16);
}}
.ai-help:hover .ai-help-tooltip, .ai-help:focus-within .ai-help-tooltip {{ display:block; }}
.ai-kpi:first-child .ai-help-tooltip, .ai-case-panel:first-child .ai-help-tooltip,
.ai-share-legend-item:first-child .ai-help-tooltip, .ai-results-band .ai-help-tooltip {{
  left:0; right:auto; transform:none;
}}
.ai-kpi:last-child:not(:first-child) .ai-help-tooltip,
.ai-case-panel:last-child:not(:first-child) .ai-help-tooltip,
.ai-share-legend-item:last-child .ai-help-tooltip {{
  left:auto; right:0; transform:none;
}}
.ai-kpi, .ai-case-panel, .ai-results-band, .ai-share-legend-item {{ overflow:visible; }}
.ai-kpi:has(.ai-help:hover), .ai-kpi:has(.ai-help:focus-within),
.ai-case-panel:has(.ai-help:hover), .ai-case-panel:has(.ai-help:focus-within),
[data-testid="stElementContainer"]:has(.ai-help:hover),
[data-testid="stElementContainer"]:has(.ai-help:focus-within) {{
  position:relative; z-index:10;
}}
[data-testid="stVerticalBlockBorderWrapper"] {{
  border-color: var(--ai-border); border-radius: var(--ai-radius);
  box-shadow: 0 1px 2px rgba(57,0,107,.06); background: var(--ai-white);
}}
[data-testid="stMetricValue"] {{
  color: var(--ai-brand-dark); font-variant-numeric: tabular-nums;
}}
[data-testid="stBaseButton-primary"] {{
  color: var(--ai-white) !important;
}}
[data-testid="stDataFrame"] {{ border: 1px solid var(--ai-border); border-radius: 8px; }}
[data-testid="stAlert"] {{ border-radius: 10px; border: 1px solid var(--ai-border); }}
[data-testid="stAlertContainer"]:has(> [data-testid="stAlertContentInfo"]),
[data-testid="stAlertContainer"]:has(> [data-testid="stAlertContentSuccess"]) {{
  background: {COLORS["tint"]} !important; color: {COLORS["brand_dark"]} !important;
}}
[data-testid="stAlertContainer"]:has(> [data-testid="stAlertContentWarning"]) {{
  background: #FFF4DA !important; color: #7A5300 !important;
}}
[data-testid="stSelectbox"] [data-baseweb="select"] > div {{
  background: var(--ai-white); border-color: var(--ai-border); border-radius: 8px;
}}
@media(max-width:860px) {{
  [data-testid="stMainBlockContainer"] {{ padding-left:16px; padding-right:16px; }}
}}
footer {{ visibility: hidden; }}
</style>
"""

_REAL_MODE_CSS = """
<style>
[data-testid="stElementToolbar"] {
  display: none !important; visibility: hidden !important; pointer-events: none !important;
}
</style>
"""


def real_mode_compliance_css(real_mode: bool) -> str:
    return _REAL_MODE_CSS if real_mode else ""


def apply_styles(*, real_mode: bool = False) -> None:
    """Apply the shared visual system, adding stricter table controls in real mode."""
    st.markdown(_GLOBAL_CSS, unsafe_allow_html=True)
    compliance_css = real_mode_compliance_css(real_mode) or "<style></style>"
    st.markdown(compliance_css, unsafe_allow_html=True)


def render_header(client_name: str = "", record_count: int | None = None) -> None:
    logo_path = Path(__file__).parent / "assets" / "brand" / "precisely-logo.webp"
    try:
        logo_data = base64.b64encode(logo_path.read_bytes()).decode("ascii")
    except FileNotFoundError:
        logo_html = ""
    else:
        logo_html = (
            f'<img class="ai-brand-logo" src="data:image/webp;base64,{logo_data}" '
            'alt="Precisely"><span class="ai-brand-separator" aria-hidden="true"></span>'
        )
    client_html = f'<strong>{escape(client_name)}</strong>' if client_name else ""
    count_html = (
        f'<span class="ai-header-pill">{record_count:,} records</span>'
        if record_count is not None else ""
    )
    st.markdown(
        f"""
        <div class="ai-header">
          <div class="ai-brand">{logo_html}Address Intelligence</div>
          <div class="ai-header-meta">{client_html}{count_html}
            <div class="ai-mode">Runs on this computer</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""
        <style>
        .ai-header {{
          display:flex; align-items:center; justify-content:space-between; gap:16px;
          margin:0 0 12px; padding:14px 24px; border-radius:10px;
          background:{COLORS["brand_dark"]}; color:#fff;
        }}
        .ai-brand {{ display:flex; align-items:center; flex-wrap:wrap; gap:16px; font-size:17px; font-weight:600; }}
        .ai-brand-logo {{ height:28px; width:auto; max-width:100%; filter:brightness(0) invert(1); }}
        .ai-brand-separator {{ width:1px; height:22px; background:rgba(255,255,255,.35); }}
        .ai-header-meta {{ display:flex; align-items:center; justify-content:flex-end; flex-wrap:wrap; gap:12px; font-size:14px; }}
        .ai-mode, .ai-header-pill {{
          border:1px solid rgba(255,255,255,.4); border-radius:999px;
          padding:3px 11px; font-size:12px; white-space:nowrap; color:#fff;
        }}
        .ai-tab-title {{ margin:8px 0 6px; font-size:32px; line-height:1.15; letter-spacing:-.02em; color:{COLORS["brand_dark"]}; font-weight:700; }}
        .ai-purpose {{ margin:0 0 var(--ai-section-spacing); color:{COLORS["muted"]}; font-size:16px; }}
        .ai-journey-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:var(--ai-section-spacing) 12px; }}
        .ai-journey-card {{
          min-width:0; padding:16px; border:1px solid {COLORS["border"]};
          border-radius:12px; background:{COLORS["white"]};
          box-shadow:0 1px 2px rgba(57,0,107,.06);
        }}
        .ai-journey-title {{ margin-bottom:12px; color:{COLORS["brand_dark"]}; font-size:16px; font-weight:700; }}
        .ai-journey-field {{ margin-top:10px; }}
        .ai-journey-label {{ display:block; color:{COLORS["muted"]}; font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.05em; }}
        .ai-journey-value {{ display:block; margin-top:3px; color:{COLORS["text"]}; font-size:13px; line-height:1.45; }}
        .ai-share-bar {{ display:flex; width:100%; height:54px; overflow:hidden; border-radius:10px; margin-bottom:var(--ai-section-spacing); }}
        .ai-share-segment {{ display:flex; flex:0 0 auto; align-items:center; justify-content:center; min-width:0; container:ai-share / inline-size; }}
        .ai-share-segment-label, .ai-share-count {{ display:none; padding:0 3px; font-size:12px; font-weight:700; white-space:nowrap; }}
        .ai-kpi-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); grid-auto-rows:1fr; gap:var(--ai-section-spacing) 12px; margin-bottom:var(--ai-section-spacing); }}
        .ai-kpi {{
          min-height:144px; height:100%; box-sizing:border-box; display:flex; flex-direction:column; padding:17px 18px;
          border:1px solid {COLORS["border"]}; border-top:4px solid var(--ai-card-color,{COLORS["brand"]}); border-radius:10px; background:#fff;
          box-shadow:0 1px 2px rgba(57,0,107,.06);
        }}
        .ai-kpi-label {{ color:{COLORS["muted"]}; font-size:13px; font-weight:600; line-height:1.3; }}
        .ai-kpi-value {{ margin-top:10px; color:{COLORS["brand_dark"]}; font-size:34px; font-weight:700; font-variant-numeric:tabular-nums; }}
        .ai-kpi-bottom {{ margin-top:auto; padding-top:10px; display:flex; flex-direction:column; align-items:flex-start; gap:7px; }}
        .ai-kpi-delta {{ display:inline-block; border-radius:999px; padding:4px 8px; font-size:12px; font-weight:600; color:{COLORS["muted"]}; background:{COLORS["surface"]}; }}
        .ai-kpi-delta.positive {{ color:{COLORS["success"]}; background:{COLORS["success_tint"]}; }}
        .ai-kpi-delta.warning {{ color:#67420C; background:{COLORS["warning_tint"]}; }}
        .ai-kpi-delta.danger {{ color:#B00062; background:{COLORS["pink_tint"]}; }}
        .ai-kpi-caption {{ color:{COLORS["muted"]}; font-size:12px; }}
        .ai-kpi-caption:empty::after {{ content:"\\00a0"; }}
        .ai-column-chips {{ display:flex; flex-wrap:wrap; gap:6px; align-items:center; }}
        .ai-column-chip {{ display:inline-flex; padding:4px 9px; border:1px solid {COLORS["border"]}; border-radius:999px; background:{COLORS["surface"]}; color:{COLORS["muted"]}; font-size:12px; }}
        .ai-share-legend {{ display:flex; flex-wrap:wrap; justify-content:flex-start; gap:12px 18px; margin-bottom:var(--ai-section-spacing); color:{COLORS["muted"]}; font-size:12px; }}
        .ai-share-legend-item {{ display:inline-flex; align-items:center; gap:6px; white-space:nowrap; }}
        .ai-share-swatch {{ width:10px; height:10px; border-radius:2px; }}
        .ai-footer {{ margin-top:30px; padding:12px 0; border-top:1px solid {COLORS["border"]}; color:{COLORS["muted"]}; font-size:11px; }}
        .ai-results-band {{ background:{COLORS["brand_dark"]}; color:#fff; border-left:8px solid {COLORS["pink"]}; border-radius:12px; padding:24px 28px; display:grid; grid-template-columns:1fr 1.1fr; gap:32px; align-items:center; margin-bottom:var(--ai-section-spacing); }}
        .ai-results-big {{ font-size:48px; font-weight:700; line-height:1.1; }}
        .ai-results-sub {{ color:#E9DBF5; margin-top:8px; }}
        .ai-results-progress {{ display:grid; gap:8px; }}
        .ai-results-track {{ position:relative; height:14px; background:#EFEDF6; border-radius:999px; overflow:hidden; }}
        .ai-results-track.unavailable {{ border:1px dashed #E9DBF5; background:transparent; }}
        .ai-results-track span {{ position:absolute; inset:0 auto 0 0; border-radius:999px; }}
        .ai-results-track .before {{ background:{COLORS["before"]}; }}
        .ai-results-track .after {{ background:{COLORS["pink"]}; }}
        .ai-results-line {{ display:grid; grid-template-columns:140px minmax(0,1fr) auto; align-items:center; gap:8px; font-size:13px; }}
        .ai-defect-rows {{ display:grid; gap:10px; padding:20px 22px; background:#fff; border:1px solid {COLORS["border"]}; border-radius:12px; }}
        .ai-defect-heading {{ color:{COLORS["brand_dark"]}; font-size:16px; font-weight:700; }}
        .ai-defect-row {{ display:grid; grid-template-columns:170px minmax(0,1fr) 150px; gap:12px; align-items:center; font-size:14px; }}
        .ai-defect-track {{ position:relative; display:flex; align-items:stretch; height:24px; background:#F1EFF7; border-radius:999px; overflow:hidden; }}
        .ai-defect-track.unavailable {{ border:1px dashed {COLORS["muted"]}; background:transparent; }}
        .ai-defect-track .before {{ background:#D9C2F5; }}
        .ai-defect-track .gained {{ background:{COLORS["brand"]}; }}
        .ai-defect-row.customer .ai-defect-track {{ background:{COLORS["pink_tint"]}; }}
        .ai-defect-row.customer .ai-defect-track .before {{ background:{COLORS["pink_tint"]}; }}
        .ai-defect-customer-label {{ position:absolute; inset:0; padding:2px 10px; color:{COLORS["text"]}; font-size:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
        .ai-defect-value {{ text-align:right; color:{COLORS["muted"]}; font-variant-numeric:tabular-nums; font-size:12px; }}
        .ai-defect-key {{ display:flex; flex-wrap:wrap; gap:8px 18px; color:{COLORS["muted"]}; font-size:12px; }}
        .ai-defect-key-item {{ display:inline-flex; align-items:center; gap:6px; }}
        .ai-defect-swatch {{ width:10px; height:10px; border-radius:2px; }}
        .ai-defect-swatch.before {{ background:#D9C2F5; }}
        .ai-defect-swatch.gained {{ background:{COLORS["brand"]}; }}
        .ai-defect-swatch.customer {{ background:{COLORS["pink_tint"]}; }}
        .ai-work-path {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:8px; }}
        .ai-work-step {{ background:{COLORS["tint"]}; border-radius:8px; padding:9px 12px; font-size:13px; color:{COLORS["muted"]}; }}
        .ai-work-step strong {{ display:block; color:{COLORS["brand_dark"]}; font-size:15px; }}
        @media(max-width:860px) {{
          .ai-kpi-grid {{ grid-template-columns:1fr !important; }}
          .ai-header {{ align-items:flex-start; flex-direction:column; }}
          .ai-header-meta {{ justify-content:flex-start; }}
          .ai-journey-grid, .ai-results-band {{ grid-template-columns:1fr; }}
          .ai-tab-title {{ font-size:26px; }}
          .ai-defect-row {{ grid-template-columns:110px minmax(0,1fr) 130px; }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_tab_intro(title: str, purpose: str) -> None:
    st.markdown(
        f'<h1 class="ai-tab-title">{escape(title)}</h1>'
        f'<div class="ai-purpose">{escape(purpose)}</div>',
        unsafe_allow_html=True,
    )


def render_kpi_row(cards: list[dict[str, Any]], *, columns: int | None = None) -> None:
    html_cards = []
    accents = {
        "Needs client follow-up": COLORS["pink"],
        "Needs customer follow-up": COLORS["pink"],
        "Precisely usually fixes": COLORS["brand"],
        "Review": COLORS["yellow"],
        "No issues found": COLORS["grey"],
    }
    for card in cards:
        delta = card.get("delta", "")
        tone = card.get("tone", "neutral")
        delta_html = (
            f'<span class="ai-kpi-delta {escape(str(tone))}">{escape(str(delta))}</span>'
            if delta
            else ""
        )
        caption = card.get("caption", "")
        caption_html = f'<span class="ai-kpi-caption">{escape(str(caption))}</span>'
        html_cards.append(
            f'<div class="ai-kpi" style="--ai-card-color:{accents.get(str(card["label"]), COLORS["brand"])}">'
            f'<div class="ai-kpi-label">{escape(str(card["label"]))}{help_icon_html(str(card["label"]))}</div>'
            f'<div class="ai-kpi-value">{escape(str(card["value"]))}</div>'
            f'<div class="ai-kpi-bottom"><div>{delta_html}</div>{caption_html}</div>'
            "</div>"
        )
    column_count = max(1, min(len(cards), max(1, int(columns))) if columns is not None else len(cards))
    rows = [html_cards[index:index + column_count] for index in range(0, len(html_cards), column_count)] or [[]]
    grids = [
        f'<div class="ai-kpi-grid" style="grid-template-columns:repeat({max(1, len(row))},minmax(0,1fr))">'
        + "".join(row) + "</div>"
        for row in rows
    ]
    st.markdown(
        '<div style="display:grid">'
        + "".join(grids) + "</div>",
        unsafe_allow_html=True,
    )


def share_bar_html(shares: list[dict[str, Any]]) -> str:
    colors = {
        "Needs client follow-up": COLORS["pink"],
        "Precisely usually fixes": COLORS["brand"],
        "Review": COLORS["yellow"],
        "No issues found": COLORS["grey"],
    }
    ordered_groups = (
        "Needs client follow-up",
        "Precisely usually fixes",
        "Review",
        "No issues found",
    )
    by_group = {str(row["group"]): row for row in shares}
    visible = [
        (group, by_group[group])
        for group in ordered_groups
        if group in by_group and float(by_group[group]["percentage"]) > 0
    ]
    total = sum(float(row["percentage"]) for _, row in visible)
    widths: list[float] = []
    accumulated = 0.0
    for index, (_, row) in enumerate(visible):
        if index == len(visible) - 1:
            width = 100.0 - accumulated
        else:
            width = round(float(row["percentage"]) / total * 100, 4)
            accumulated += width
        widths.append(width)
    segments = []
    count_rules = set()
    label_rules = set()
    for (group, row), width in zip(visible, widths):
        color = colors[group]
        text_color = (
            COLORS["white"]
            if group in ("Needs client follow-up", "Precisely usually fixes")
            else COLORS["brand_dark"]
        )
        percentage = float(row["percentage"])
        count = f'{int(row["count"]):,}'
        label = f'{count} · {percentage:.0f}%'
        count_class = f"ai-share-count-{len(count)}-{len(label) if width >= 12 else 'only'}"
        count_min_width = len(count) * 8 + 6
        count_rules.add(
            f'@container ai-share (min-width:{count_min_width}px)'
            f'{{.{count_class}{{display:inline}}}}'
        )
        label_html = (
            f'<span class="ai-share-segment-label ai-share-full-{len(label)}" aria-hidden="true" style="color:{text_color}">'
            f"{escape(label)}</span>"
            if width >= 12
            else ""
        )
        if label_html:
            label_min_width = len(label) * 8 + 6
            label_rules.add(
                f'@container ai-share (min-width:{label_min_width}px)'
                f'{{.{count_class}{{display:none}}'
                f'.ai-share-full-{len(label)}{{display:inline}}}}'
            )
        segments.append(
            f'<div class="ai-share-segment" role="img" '
            f'aria-label="{escape(group)}: {escape(label)}" '
            f'style="width:{width:g}%;flex-basis:{width:g}%;background:{color}">'
            f'<span class="ai-share-count {count_class}" aria-hidden="true" '
            f'style="color:{text_color}">{escape(count)}</span>{label_html}</div>'
        )
    legend = "".join(
        '<span class="ai-share-legend-item">'
        f'<span class="ai-share-swatch" style="background:{colors[group]}"></span>'
        f'{escape(group)}{help_icon_html(group)}</span>'
        for group in ordered_groups
    )
    return (
        "<style>" + "".join(sorted(count_rules)) + "".join(sorted(label_rules)) + "</style>"
        + '<div class="ai-share-legend">'
        + legend
        + '</div><div class="ai-share-bar" aria-label="Pre-flight record shares">'
        + "".join(segments)
        + "</div>"
    )


def render_share_chart(shares: list[dict[str, Any]]) -> None:
    st.markdown(share_bar_html(shares), unsafe_allow_html=True)


_COUNT_PATTERN = r"[+-]?(?:[\d,]+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"


def _count_value(value: str) -> Decimal | None:
    if not re.fullmatch(rf"\s*{_COUNT_PATTERN}\s*", value):
        return None
    try:
        number = Decimal(value.strip().replace(",", ""))
    except DecimalException:
        return None
    return number if number.is_finite() and number >= 0 else None


def _fraction_share(value: str) -> float | None:
    """Return a share for a count fraction, or None when unavailable."""
    match = re.fullmatch(
        rf"\s*({_COUNT_PATTERN})\s*/\s*({_COUNT_PATTERN})(?:\s*\(\s*[\d.]+%\s*\))?\s*",
        value,
    )
    if not match:
        return None
    numerator, denominator = (_count_value(part) for part in match.groups())
    if numerator is None or denominator is None or denominator == 0 or numerator > denominator:
        return None
    try:
        return float(numerator / denominator)
    except (DecimalException, OverflowError):
        return None


def _share_width(share: float | None) -> str:
    return f"{min(1.0, max(0.0, share or 0.0)) * 100:.2f}"


def render_results_band(before: str, after: str) -> None:
    """Show the supplied before and after count fractions and their shares."""
    before_share = _fraction_share(before)
    after_share = _fraction_share(after)
    headline = f"{after_share * 100:.1f}%" if after_share is not None else "Not available"
    has_before = bool(before.strip())
    lines = []
    for label, value, share, css_class in (
        ("Before", before, before_share, "before"),
        ("After" if has_before else "Located automatically", after, after_share, "after"),
    ):
        if label == "Before" and not value.strip():
            continue
        unavailable = " unavailable" if share is None else ""
        display_value = escape(value) if value else "Not available"
        if share is None and value:
            display_value += " (share unavailable)"
        lines.append(
            '<div class="ai-results-line">'
            f'<span>{label}</span><div class="ai-results-track{unavailable}" aria-hidden="true">'
            f'<span class="{css_class}" style="width:{_share_width(share)}%"></span></div>'
            f'<span>{display_value}</span></div>'
        )
    st.markdown(
        '<div class="ai-results-band"><div>'
        f'<div class="ai-results-big">{headline}</div>'
        f'<div class="ai-results-sub">{"right address" + help_icon_html("Right address") if has_before else "Located automatically"}</div></div>'
        '<div class="ai-results-progress">' + "".join(lines) + "</div></div>",
        unsafe_allow_html=True,
    )


def render_defect_rows(rows: list[dict[str, Any]]) -> None:
    """Render defects sorted by after share, using Total as the count denominator."""
    def fraction(row: dict[str, Any], key: str) -> str:
        value = str(row[key])
        if "/" in value:
            return value
        total = row.get("Total")
        return f"{value}/{total}" if total is not None else value

    def sort_key(row: dict[str, Any]) -> float:
        share = _fraction_share(fraction(row, "After"))
        return share if share is not None else -1.0

    def is_zero(value: Any) -> bool:
        return _count_value(str(value).split("/", 1)[0]) == Decimal(0)

    html_rows = []
    has_customer = False
    for row in sorted(rows, key=sort_key, reverse=True):
        name = escape(str(row["Defect"]))
        before = fraction(row, "Before")
        after = fraction(row, "After")
        before_share = _fraction_share(before)
        after_share = _fraction_share(after)
        before_count, before_sep, before_total = before.partition("/")
        after_count, after_sep, after_total = after.partition("/")
        before_total = before_total.split("(", 1)[0].strip()
        after_total = after_total.split("(", 1)[0].strip()
        same_total = (
            bool(before_sep and after_sep)
            and _count_value(before_total) is not None
            and _count_value(before_total) == _count_value(after_total)
        )
        if same_total:
            value = f"{before_count.strip()} → {after_count.strip()} of {after_total}"
        else:
            value = f"{before} → {after}"
        available = before_share is not None and after_share is not None and same_total
        if not available:
            value += " (share unavailable)"
        customer_row = is_zero(row["After"])
        has_customer = has_customer or customer_row
        customer = " customer" if customer_row else ""
        unavailable = " unavailable" if not available else ""
        gain = max(0.0, after_share - before_share) if available else None
        segments = (
            f'<span class="before" style="width:{_share_width(before_share)}%"></span>'
            if available else ""
        )
        if available:
            segments += f'<span class="gained" style="width:{_share_width(gain)}%"></span>'
        if customer_row:
            segments += '<span class="ai-defect-customer-label">Needs the customer</span>'
        html_rows.append(
            f'<div class="ai-defect-row{customer}"><span>{name}</span>'
            f'<div class="ai-defect-track{unavailable}" role="img" aria-label="{name}: '
            f'before {escape(before)}, after {escape(after)}">'
            f'{segments}</div>'
            f'<span class="ai-defect-value">{escape(value)}</span></div>'
        )
    legend = (
        '<span class="ai-defect-key-item"><span class="ai-defect-swatch before"></span>Already right</span>'
        '<span class="ai-defect-key-item"><span class="ai-defect-swatch gained"></span>Fixed by the run</span>'
    )
    if has_customer:
        legend += '<span class="ai-defect-key-item"><span class="ai-defect-swatch customer"></span>Needs the customer</span>'
    st.markdown(
        '<div class="ai-defect-rows"><div class="ai-defect-heading">Right address, by issue type</div>'
        + "".join(html_rows) + f'<div class="ai-defect-key">{legend}</div></div>',
        unsafe_allow_html=True,
    )


def render_work_path(path: dict[str, Any]) -> None:
    """Render work_path counts or ordered label-to-display-value pairs."""
    if {"total", "queue", "analyst", "remaining"} <= path.keys():
        path = {
            "Automated checks": f'{path["total"]} → {path["queue"]}',
            f'AI finds the cause; {path["queue"] - path["analyst"]} go to the customer': f'{path["queue"]} → {path["analyst"]}',
            f'Precisely checks; {path["analyst"] - path["remaining"]} fixes ready to approve': f'{path["analyst"]} → {path["remaining"]}',
            "Need research. Analysts approve every change": path["remaining"],
        }
    steps = "".join(
        f'<div class="ai-work-step"><strong>{escape(str(value))}</strong>'
        f'{escape(str(label))}</div>'
        for label, value in path.items()
    )
    st.markdown(f'<div class="ai-work-path">{steps}</div>', unsafe_allow_html=True)


def render_journey_cards(cards: list[dict[str, str]]) -> None:
    card_html = []
    for card in cards:
        fields = "".join(
            '<div class="ai-journey-field">'
            f'<span class="ai-journey-label">{escape(label)}</span>'
            f'<span class="ai-journey-value">{escape(card[key])}</span>'
            "</div>"
            for key, label in (
                ("who", "Who"),
                ("provide", "You provide"),
                ("get", "You get"),
            )
        )
        card_html.append(
            '<article class="ai-journey-card">'
            f'<div class="ai-journey-title">{escape(card["title"])}</div>'
            f"{fields}</article>"
        )
    st.markdown(
        '<div class="ai-journey-grid">' + "".join(card_html) + "</div>",
        unsafe_allow_html=True,
    )


def render_chips(values: list[str]) -> None:
    chips = "".join(
        f'<span class="ai-column-chip">{escape(value)}</span>'
        for value in values
    )
    st.markdown(
        f'<div class="ai-column-chips">{chips or "None"}</div>',
        unsafe_allow_html=True,
    )


def build_results_chart(
    rows: list[dict[str, Any]],
    chart_data: list[dict[str, Any]],
) -> alt.TopLevelMixin:
    defects = [str(row["Defect"]) for row in rows]
    max_count = max((int(row["Count"]) for row in chart_data), default=0)
    base = alt.Chart(alt.InlineData(values=chart_data)).encode(
        y=alt.Y(
            "Defect:N",
            sort=defects,
            scale=alt.Scale(
                domain=defects,
                paddingInner=0.38,
                paddingOuter=0.18,
            ),
            axis=alt.Axis(title=None, labelLimit=0, labelFontSize=12),
        ),
        yOffset=alt.YOffset(
            "Series:N",
            scale=alt.Scale(domain=["Before", "After"], paddingInner=0.2),
        ),
        color=alt.Color(
            "Series:N",
            scale=alt.Scale(
                domain=["Before", "After"],
                range=[COLORS["before"], COLORS["brand"]],
            ),
            legend=alt.Legend(
                title=None,
                orient="top-right",
                direction="horizontal",
                labelFontSize=12,
                labelColor=COLORS["muted"],
                labelFont="Segoe UI",
                offset=4,
            ),
        ),
        tooltip=["Defect:N", "Series:N", "Count:Q"],
    )
    bars = base.mark_bar(size=16).encode(
        x=alt.X(
            "Count:Q",
            title=None,
            scale=alt.Scale(domain=[0, max_count + 2], nice=False),
            axis=alt.Axis(
                format="d",
                tickMinStep=1,
                tickCount=max_count + 1,
                grid=True,
                gridColor=COLORS["border"],
            ),
        )
    )
    labels = base.transform_filter("datum.Count > 0").mark_text(
        align="left",
        dx=6,
    ).encode(
        x=alt.X("Count:Q"),
        text=alt.Text("Label:N"),
        yOffset=alt.YOffset(
            "Series:N",
            scale=alt.Scale(domain=["Before", "After"], paddingInner=0.2),
        ),
    )
    chart = (bars + labels).properties(
        height=max(190, 38 * len(rows)),
        title=alt.TitleParams(
            text="Records with the right address, before and after, by issue type",
            anchor="start",
            color=COLORS["brand_dark"],
            fontSize=16,
            offset=16,
        ),
    )
    return theme_chart(chart)


def theme_chart(chart: alt.TopLevelMixin) -> alt.TopLevelMixin:
    return (
        chart.configure(background=COLORS["white"], font="Segoe UI")
        .configure_view(stroke=None)
        .configure_axis(
            labelColor=COLORS["muted"],
            titleColor=COLORS["brand_dark"],
            gridColor=COLORS["border"],
            domainColor=COLORS["border"],
            labelFont="Segoe UI",
            titleFont="Segoe UI",
        )
        .configure_axisY(grid=False)
    )


def render_footer() -> None:
    st.markdown(
        '<div class="ai-footer">Local demo · data handling: '
        "Precisely APIs Acceptable Use Policy, sections 4.1–4.2</div>",
        unsafe_allow_html=True,
    )
