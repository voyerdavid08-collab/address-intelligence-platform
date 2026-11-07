"""Reusable local-app presentation tokens and components."""

from __future__ import annotations

import base64
from html import escape
from pathlib import Path
from typing import Any

import altair as alt
import streamlit as st

FONT_STACK = '"Segoe UI", -apple-system, "Helvetica Neue", Arial, sans-serif'
COLORS = {
    "brand_dark": "#39006B",
    "brand": "#8017E1",
    "text": "#1F2A37",
    "muted": "#5B6B7B",
    "border": "#E3E8EF",
    "surface": "#F6F8FA",
    "white": "#FFFFFF",
    "success": "#1E7F4F",
    "success_tint": "#EAF5EF",
    "warning": "#B7791F",
    "warning_tint": "#FFF6E5",
    "danger": "#B42318",
    "danger_tint": "#FDEDEC",
    "before": "#9AA5B1",
}

_GLOBAL_CSS = f"""
<style>
:root {{
  --ai-brand-dark: {COLORS["brand_dark"]}; --ai-brand: {COLORS["brand"]};
  --ai-text: {COLORS["text"]}; --ai-muted: {COLORS["muted"]};
  --ai-border: {COLORS["border"]}; --ai-surface: {COLORS["surface"]};
  --ai-white: {COLORS["white"]}; --ai-radius: 8px;
}}
.stApp {{ background: var(--ai-white); color: var(--ai-text); }}
[data-testid="stMainBlockContainer"] {{
  max-width: 1180px; margin: 0 auto; padding-top: 16px; padding-right: 32px;
  padding-bottom: 32px; padding-left: 32px;
}}
header[data-testid="stHeader"] {{ display:none; height:0; }}
h1, h2, h3, h4, [data-testid="stHeadingWithActionElements"] {{
  color: var(--ai-brand-dark) !important; letter-spacing: -0.02em;
}}
[data-testid="stHeadingWithActionElements"] button {{ display: none; }}
[data-testid="stTabs"] [role="tab"] {{
  color: var(--ai-muted); font-weight: 600; padding: 14px 18px;
}}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] {{
  color: var(--ai-brand-dark); border-bottom-color: var(--ai-brand);
}}
[data-testid="stTabs"] [role="tab"]:focus-visible,
button:focus-visible, input:focus-visible, textarea:focus-visible,
[role="combobox"]:focus-visible {{
  outline: 3px solid var(--ai-brand) !important; outline-offset: 2px;
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


def render_header() -> None:
    logo_path = Path(__file__).parent / "assets" / "brand" / "precisely-logo.webp"
    try:
        logo_data = base64.b64encode(logo_path.read_bytes()).decode("ascii")
    except FileNotFoundError:
        logo_html = ""
    else:
        logo_html = (
            f'<img class="ai-brand-logo" src="data:image/webp;base64,{logo_data}" '
            'alt="Precisely">'
        )
    st.markdown(
        f"""
        <div class="ai-header">
          <div>
            <div class="ai-brand">{logo_html}Address Intelligence</div>
            <div class="ai-subtitle">Client onboarding and assessment</div>
          </div>
          <div class="ai-mode">{escape("Local only")}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        f"""
        <style>
        .ai-header {{
          display:flex; align-items:center; justify-content:space-between; gap:24px;
          margin:0 0 24px; padding:24px 28px; border-radius:8px;
          background:{COLORS["brand_dark"]}; color:#fff;
        }}
        .ai-brand {{ display:flex; align-items:center; flex-wrap:wrap; gap:12px; font-size:26px; font-weight:700; letter-spacing:-.02em; }}
        .ai-brand-logo {{ height:28px; width:auto; max-width:100%; filter:brightness(0) invert(1); }}
        .ai-subtitle {{ margin-top:4px; color:#E9DBF5; font-size:14px; }}
        .ai-mode {{
          border-radius:999px; padding:8px 13px; font-weight:600; font-size:13px;
          white-space:nowrap; color:{COLORS["brand_dark"]}; background:#F0E5FA;
        }}
        .ai-tab-title {{ margin:8px 0 4px; font-size:24px; color:{COLORS["brand_dark"]}; font-weight:700; }}
        .ai-purpose {{ margin:0 0 18px; color:{COLORS["muted"]}; font-size:14px; }}
        .ai-journey-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }}
        .ai-journey-card {{
          min-width:0; padding:16px; border:1px solid {COLORS["border"]};
          border-radius:8px; background:{COLORS["white"]};
          box-shadow:0 1px 2px rgba(57,0,107,.06);
        }}
        .ai-journey-title {{ margin-bottom:12px; color:{COLORS["brand_dark"]}; font-size:16px; font-weight:700; }}
        .ai-journey-field {{ margin-top:10px; }}
        .ai-journey-label {{ display:block; color:{COLORS["muted"]}; font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.05em; }}
        .ai-journey-value {{ display:block; margin-top:3px; color:{COLORS["text"]}; font-size:13px; line-height:1.45; }}
        .ai-share-bar {{ display:flex; width:100%; height:40px; overflow:hidden; border-radius:8px; }}
        .ai-share-segment {{ display:flex; flex:0 0 auto; align-items:center; justify-content:center; min-width:0; }}
        .ai-share-segment-label {{ overflow:hidden; padding:0 3px; font-size:12px; font-weight:700; white-space:nowrap; text-overflow:clip; }}
        .ai-kpi-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); grid-auto-rows:1fr; gap:12px; }}
        .ai-kpi {{
          min-height:144px; height:100%; box-sizing:border-box; display:flex; flex-direction:column; padding:17px 18px;
          border:1px solid {COLORS["border"]}; border-radius:8px; background:#fff;
          box-shadow:0 1px 2px rgba(57,0,107,.06);
        }}
        .ai-kpi-label {{ color:{COLORS["muted"]}; font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.06em; }}
        .ai-kpi-value {{ margin-top:10px; color:{COLORS["brand_dark"]}; font-size:27px; font-weight:700; font-variant-numeric:tabular-nums; }}
        .ai-kpi-bottom {{ margin-top:auto; padding-top:10px; display:flex; flex-direction:column; align-items:flex-start; gap:7px; }}
        .ai-kpi-delta {{ display:inline-block; border-radius:999px; padding:4px 8px; font-size:12px; font-weight:600; color:{COLORS["muted"]}; background:{COLORS["surface"]}; }}
        .ai-kpi-delta.positive {{ color:{COLORS["success"]}; background:{COLORS["success_tint"]}; }}
        .ai-kpi-delta.warning {{ color:#67420C; background:{COLORS["warning_tint"]}; }}
        .ai-kpi-delta.danger {{ color:{COLORS["danger"]}; background:{COLORS["danger_tint"]}; }}
        .ai-kpi-caption {{ color:{COLORS["muted"]}; font-size:12px; }}
        .ai-kpi-caption:empty::after {{ content:"\\00a0"; }}
        .ai-column-chips {{ display:flex; flex-wrap:wrap; gap:6px; align-items:center; }}
        .ai-column-chip {{ display:inline-flex; padding:4px 9px; border:1px solid {COLORS["border"]}; border-radius:999px; background:{COLORS["surface"]}; color:{COLORS["muted"]}; font-size:12px; }}
        .ai-share-legend {{ display:flex; flex-wrap:nowrap; justify-content:center; gap:18px; margin-top:8px; color:{COLORS["muted"]}; font-size:12px; }}
        .ai-share-legend-item {{ display:inline-flex; align-items:center; gap:6px; white-space:nowrap; }}
        .ai-share-swatch {{ width:10px; height:10px; border-radius:2px; }}
        .ai-footer {{ margin-top:30px; padding:12px 0; border-top:1px solid {COLORS["border"]}; color:{COLORS["muted"]}; font-size:11px; }}
        @media(max-width:800px) {{
          .ai-kpi-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
          .ai-header {{ align-items:flex-start; flex-direction:column; }}
          .ai-journey-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
        }}
        @media(max-width:600px) {{
          .ai-journey-grid {{ grid-template-columns:1fr; }}
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_tab_intro(title: str, purpose: str) -> None:
    st.markdown(
        f'<div class="ai-tab-title">{escape(title)}</div>'
        f'<div class="ai-purpose">{escape(purpose)}</div>',
        unsafe_allow_html=True,
    )


def render_kpi_row(cards: list[dict[str, Any]], *, columns: int = 4) -> None:
    html_cards = []
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
            '<div class="ai-kpi">'
            f'<div class="ai-kpi-label">{escape(str(card["label"]))}</div>'
            f'<div class="ai-kpi-value">{escape(str(card["value"]))}</div>'
            f'<div class="ai-kpi-bottom"><div>{delta_html}</div>{caption_html}</div>'
            "</div>"
        )
    st.markdown(
        f'<div class="ai-kpi-grid" style="grid-template-columns:repeat({columns},minmax(0,1fr))">'
        + "".join(html_cards)
        + "</div>",
        unsafe_allow_html=True,
    )


def share_bar_html(shares: list[dict[str, Any]]) -> str:
    colors = {
        "Needs client follow-up": COLORS["danger"],
        "Precisely usually fixes": COLORS["brand"],
        "Review": COLORS["warning"],
        "No issues found": "#D8E0E8",
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
    for (group, row), width in zip(visible, widths):
        color = colors[group]
        text_color = (
            COLORS["white"]
            if group in ("Needs client follow-up", "Precisely usually fixes")
            else COLORS["brand_dark"]
        )
        percentage = float(row["percentage"])
        label = f'{int(row["count"]):,} · {percentage:.0f}%'
        label_html = (
            f'<span class="ai-share-segment-label" style="color:{text_color}">'
            f"{escape(label)}</span>"
            if width >= 12
            else ""
        )
        segments.append(
            f'<div class="ai-share-segment" role="img" '
            f'aria-label="{escape(group)}: {escape(label)}" '
            f'style="width:{width:g}%;flex-basis:{width:g}%;background:{color}">'
            f"{label_html}</div>"
        )
    legend = "".join(
        '<span class="ai-share-legend-item">'
        f'<span class="ai-share-swatch" style="background:{colors[group]}"></span>'
        f'{escape(group)}</span>'
        for group in ordered_groups
    )
    return (
        '<div class="ai-share-bar" aria-label="Pre-flight record shares">'
        + "".join(segments)
        + '</div><div class="ai-share-legend">'
        + legend
        + "</div>"
    )


def render_share_chart(shares: list[dict[str, Any]]) -> None:
    st.markdown(share_bar_html(shares), unsafe_allow_html=True)


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
        '<div class="ai-footer">Local prototype · do not deploy · data handling: '
        "Precisely APIs Acceptable Use Policy, sections 4.1–4.2</div>",
        unsafe_allow_html=True,
    )
