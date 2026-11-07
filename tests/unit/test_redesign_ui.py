import pytest

from app import ui


def _capture(monkeypatch):
    calls = []
    monkeypatch.setattr(ui.st, "markdown", lambda body, **kwargs: calls.append(body))
    return calls


def test_header_metadata_is_escaped_and_count_is_optional(monkeypatch):
    calls = _capture(monkeypatch)
    monkeypatch.setattr(ui.Path, "read_bytes", lambda self: b"logo")
    ui.render_header("<client>", 1200)
    assert "<strong>&lt;client&gt;</strong>" in calls[0]
    assert "Loaded file:" not in calls[0]
    assert "1,200 records" in calls[0]
    assert 'alt="Precisely"' in calls[0]
    assert calls[0].index('class="ai-brand-separator"') < calls[0].index("Address Intelligence")
    calls.clear()
    ui.render_header()
    assert "records" not in calls[0]


def test_header_supports_missing_logo(monkeypatch):
    calls = _capture(monkeypatch)

    def missing(self):
        raise FileNotFoundError

    monkeypatch.setattr(ui.Path, "read_bytes", missing)
    ui.render_header()
    assert "<img" not in calls[0]
    assert "ai-brand-separator" not in calls[0]
    assert '<div class="ai-mode">Runs on this computer</div>' in calls[0]


def test_footer_uses_required_policy_notice(monkeypatch):
    calls = _capture(monkeypatch)
    ui.render_footer()
    assert (
        "Local demo · data handling: Precisely APIs Acceptable Use Policy, sections 4.1–4.2"
        in calls[0]
    )


def test_results_fractions_and_unavailable_denominator(monkeypatch):
    calls = _capture(monkeypatch)
    ui.render_results_band("2/4", "3/4")
    assert "75.0%" in calls[0]
    assert '<div class="ai-results-sub">right address</div>' in calls[0]
    assert "After share" not in calls[0]
    assert "width:50.00%" in calls[0]
    assert "width:75.00%" in calls[0]
    ui.render_results_band("0/0", "0/0")
    assert "Not available" in calls[1]
    ui.render_results_band("1/4 (25.0%)", "3/4 (75.0%)")
    assert "75.0%" in calls[2] and "width:25.00%" in calls[2]
    assert "width:75.00%" in calls[2]
    ui.render_results_band("1/1", "5/4")
    assert "Not available" in calls[3]
    assert "125.0%" not in calls[3]
    ui.render_results_band("1e+06/2e+06 (50.0%)", "1.5e+06/2e+06 (75.0%)")
    assert "75.0%" in calls[4]
    assert "width:50.00%" in calls[4] and "width:75.00%" in calls[4]
    assert ui._fraction_share("nan/2") is None
    assert ui._fraction_share("3e+06/2e+06") is None


def test_results_without_reference_omits_before(monkeypatch):
    calls = _capture(monkeypatch)
    ui.render_results_band("", "3/4")
    assert "Before" not in calls[0]
    assert "After" not in calls[0]
    assert "Located automatically" in calls[0]
    assert "75.0%" in calls[0] and "3/4" in calls[0]
    assert calls[0].count('class="ai-results-line"') == 1


def test_defects_sort_by_after_fraction_and_escape(monkeypatch):
    calls = _capture(monkeypatch)
    ui.render_defect_rows([
        {"Defect": "<lower>", "Before": "1/10", "After": "9/10"},
        {"Defect": "higher", "Before": "1/2", "After": "1/1", "customer_follow_up": True},
        {"Defect": "zero after", "Before": "1/2", "After": "0/2"},
        {"Defect": "unknown", "Before": "0/0", "After": "0/0"},
    ])
    html = calls[0]
    assert html.index("higher") < html.index("&lt;lower&gt;") < html.index("unknown")
    assert '<div class="ai-defect-row customer"><span>unknown</span>' in html
    assert '<div class="ai-defect-row customer"><span>zero after</span>' in html
    assert '<div class="ai-defect-row"><span>higher</span>' in html
    assert "Needs the customer" in html
    assert 'class="gained" style="width:80.00%"' in html
    assert "&lt;lower&gt;" in html


def test_defect_counts_use_total_without_inventing_missing_shares(monkeypatch):
    calls = _capture(monkeypatch)
    ui.render_defect_rows([
        {"Defect": "unknown", "Before": 2, "After": 9, "Total": None},
        {"Defect": "lower", "Before": 1, "After": 2, "Total": 10},
        {"Defect": "higher", "Before": 1, "After": 4, "Total": 5},
        {"Defect": "zero", "Before": 0, "After": 0, "Total": 0},
    ])
    html = calls[0]
    assert html.index("higher") < html.index("lower") < html.index("unknown")
    assert 'class="gained" style="width:60.00%"' in html
    assert 'class="gained" style="width:10.00%"' in html
    assert "after 9" in html and "after 9/" not in html
    assert "after 0/0" in html
    assert "2 → 9 (share unavailable)" in html
    assert 'class="ai-defect-track unavailable"' in html
    assert "1 → 4 of 5" in html
    assert '<div class="ai-defect-row customer"><span>zero</span>' in html


def test_scientific_notation_zero_defects(monkeypatch):
    calls = _capture(monkeypatch)
    ui.render_defect_rows([
        {"Defect": "zero", "Before": "0e+00", "After": "0.0e+00", "Total": "1e+06"},
    ])
    assert '<div class="ai-defect-row customer"><span>zero</span>' in calls[0]
    assert "Needs the customer" in calls[0]


@pytest.mark.parametrize(
    ("before", "after", "total", "before_width", "gain_width"),
    [(6, 10, 12, "50.00", "33.33"), (0, 9, 10, "0.00", "90.00"),
     (8, 8, 10, "80.00", "0.00"), (9, 8, 10, "90.00", "0.00")],
)
def test_defect_segments_are_before_and_nonnegative_gain(
    monkeypatch, before, after, total, before_width, gain_width
):
    calls = _capture(monkeypatch)
    ui.render_defect_rows([{"Defect": "issue", "Before": before, "After": after, "Total": total}])
    html = calls[0]
    assert f'class="before" style="width:{before_width}%"' in html
    assert f'class="gained" style="width:{gain_width}%"' in html
    assert f"{before} → {after} of {total}" in html
    assert "Right address, by issue type" in html
    assert "Already right" in html and "Fixed by the run" in html
    assert "Needs the customer" not in html


def test_zero_after_has_pink_track_and_conditional_legend(monkeypatch):
    calls = _capture(monkeypatch)
    ui.render_defect_rows([{"Defect": "issue", "Before": 0, "After": 0, "Total": 8}])
    html = calls[0]
    assert 'class="ai-defect-row customer"' in html
    assert '<span class="ai-defect-customer-label">Needs the customer</span>' in html
    assert "0 → 0 of 8" in html
    assert 'class="ai-defect-swatch customer"></span>Needs the customer' in html
    assert 'class="before" style="width:0.00%"' in html
    assert 'class="gained" style="width:0.00%"' in html


def test_defect_styles_align_tracks_and_use_segment_colors(monkeypatch):
    calls = _capture(monkeypatch)
    ui.render_header()
    css = calls[1]
    assert "grid-template-columns:170px minmax(0,1fr) 150px" in css
    assert "grid-template-columns:110px minmax(0,1fr) 130px" in css
    assert ".ai-defect-track .before { background:#D9C2F5; }" in css
    assert f'.ai-defect-track .gained {{ background:{ui.COLORS["brand"]}; }}' in css
    assert "background:#F1EFF7" in css
    assert f'.ai-defect-row.customer .ai-defect-track {{ background:{ui.COLORS["pink_tint"]}; }}' in css
    assert f'.ai-defect-row.customer .ai-defect-track .before {{ background:{ui.COLORS["pink_tint"]}; }}' in css


def test_defect_missing_before_or_mismatched_totals_do_not_invent_gain(monkeypatch):
    calls = _capture(monkeypatch)
    ui.render_defect_rows([
        {"Defect": "missing", "Before": None, "After": 4, "Total": 10},
        {"Defect": "different", "Before": "1/5", "After": "4/10"},
    ])
    html = calls[0]
    assert html.count('class="ai-defect-track unavailable"') == 2
    assert 'class="before" style=' not in html
    assert 'class="gained" style=' not in html
    assert "share unavailable" in html
    assert "1/5 → 4/10" in html


def test_work_path_and_kpis_escape_inputs(monkeypatch):
    calls = _capture(monkeypatch)
    ui.render_work_path({"<step>": "<value>"})
    assert "&lt;step&gt;" in calls[0] and "&lt;value&gt;" in calls[0]
    ui.render_kpi_row([{"label": "<label>", "value": "<value>", "delta": "change", "tone": 'x"'}])
    assert "&lt;label&gt;" in calls[1] and "&lt;value&gt;" in calls[1]
    assert 'x&quot;' in calls[1]


def test_kpi_columns_fill_available_width(monkeypatch):
    calls = _capture(monkeypatch)
    for count in (1, 2, 3, 4, 5):
        ui.render_kpi_row([{"label": str(i), "value": i} for i in range(count)])
        assert f"repeat({count},minmax(0,1fr))" in calls[-1]
    ui.render_kpi_row([{"label": str(i), "value": i} for i in range(3)], columns=4)
    assert "repeat(3,minmax(0,1fr))" in calls[-1]
    ui.render_kpi_row([{"label": str(i), "value": i} for i in range(3)], columns=2)
    assert "repeat(2,minmax(0,1fr))" in calls[-1]
    assert "repeat(1,minmax(0,1fr))" in calls[-1]
    ui.render_kpi_row([{"label": str(i), "value": i} for i in range(5)], columns=3)
    assert "repeat(3,minmax(0,1fr))" in calls[-1]
    assert "repeat(2,minmax(0,1fr))" in calls[-1]
    ui.render_kpi_row([])
    assert "repeat(1,minmax(0,1fr))" in calls[-1]


def test_shared_tab_vertical_spacing():
    assert "--ai-section-spacing: 20px" in ui._GLOBAL_CSS
    assert '[role="tabpanel"] [data-testid="stVerticalBlock"]' in ui._GLOBAL_CSS
    assert "gap: var(--ai-section-spacing) !important" in ui._GLOBAL_CSS


def test_html_blocks_have_section_spacing_without_double_grid_gap(monkeypatch):
    calls = _capture(monkeypatch)
    ui.render_header()
    for selector in (".ai-results-band", ".ai-share-bar", ".ai-kpi-grid"):
        rule = calls[1].split(f"{selector} {{", 1)[1].split("}", 1)[0]
        assert "margin-bottom:var(--ai-section-spacing)" in rule
    ui.render_kpi_row([{"label": str(i), "value": i} for i in range(5)], columns=3)
    assert '<div style="display:grid">' in calls[-1]
    assert 'display:grid;gap:var(--ai-section-spacing)' not in calls[-1]


def test_work_path_uses_data_helper_counts(monkeypatch):
    calls = _capture(monkeypatch)
    ui.render_work_path({"total": 100, "queue": 20, "analyst": 7, "remaining": 3})
    assert "<strong>100 → 20</strong>Automated checks" in calls[0]
    assert "<strong>20 → 7</strong>AI finds the cause" in calls[0]
    assert "<strong>7 → 3</strong>Precisely checks" in calls[0]
    assert "<strong>3</strong>Analysts decide" in calls[0]


def test_share_chart_uses_redesign_colors():
    html = ui.share_bar_html([
        {"group": "Needs client follow-up", "count": 1, "percentage": 25},
        {"group": "No issues found", "count": 3, "percentage": 75},
    ])
    assert ui.COLORS["pink"] in html
    assert ui.COLORS["grey"] in html
    assert "width:25%" in html and "width:75%" in html


def test_share_chart_legend_precedes_bar_and_counts_fit_responsive_segments(monkeypatch):
    shares = [
        {"group": "Needs client follow-up", "count": 1, "percentage": 5},
        {"group": "No issues found", "count": 19, "percentage": 95},
    ]
    html = ui.share_bar_html(shares)
    assert html.index('class="ai-share-legend"') < html.index('class="ai-share-bar"')
    assert 'class="ai-share-count ai-share-count-1-only"' in html
    assert '@container ai-share (min-width:14px){.ai-share-count-1-only{display:inline}}' in html
    assert 'aria-label="Needs client follow-up: 1 · 5%"' in html
    assert chr(0x2014) not in html
    calls = _capture(monkeypatch)
    ui.render_header()
    assert "margin-bottom:var(--ai-section-spacing)" in calls[1]
    assert "justify-content:flex-start" in calls[1].split(".ai-share-legend {", 1)[1].split("}", 1)[0]
    assert "container:ai-share / inline-size" in calls[1]


def test_notification_and_input_theme_keeps_errors_independent():
    config = (ui.Path(ui.__file__).parent.parent / ".streamlit" / "config.toml").read_text(
        encoding="utf-8"
    )
    assert 'greenColor = "#8017E1"' in config
    assert 'blueColor = "#8017E1"' in config
    assert 'greenBackgroundColor = "#F5EFFC"' in config
    assert 'yellowColor = "#FAB512"' in config
    assert "redColor =" not in config
    assert '[data-testid="stSelectbox"] [data-baseweb="select"]' in ui._GLOBAL_CSS
    for kind in ("Info", "Success", "Warning"):
        assert (
            f'[data-testid="stAlertContainer"]:has(> [data-testid="stAlertContent{kind}"])'
            in ui._GLOBAL_CSS
        )
    assert '[data-testid="stAlertContentError"]' not in ui._GLOBAL_CSS
    assert ui.COLORS["success"] == ui.COLORS["brand"]


def test_tab_step_styling_does_not_add_numbers():
    assert '[role="tab"] [role="img"]' in ui._GLOBAL_CSS
    assert "react-aria-SelectionIndicator" in ui._GLOBAL_CSS
    assert "counter-increment" not in ui._GLOBAL_CSS
