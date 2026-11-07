import base64

from app import ui


def _contrast(first, second):
    def luminance(color):
        values = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [
            value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
            for value in values
        ]
        return sum(weight * value for weight, value in zip((0.2126, 0.7152, 0.0722), linear))

    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def test_brand_colors_and_contrast():
    assert ui.COLORS["brand_dark"] == "#39006B"
    assert ui.COLORS["brand"] == "#8017E1"
    assert "navy" not in ui.COLORS
    assert "teal" not in ui.COLORS
    assert _contrast(ui.COLORS["white"], ui.COLORS["brand"]) >= 4.5
    assert _contrast(ui.COLORS["white"], ui.COLORS["brand_dark"]) >= 4.5
    assert _contrast("#E9DBF5", ui.COLORS["brand_dark"]) >= 4.5
    assert _contrast("#F0E5FA", ui.COLORS["brand_dark"]) >= 4.5
    assert "color: var(--ai-white) !important" in ui._GLOBAL_CSS


def test_header_embeds_optional_logo(monkeypatch, tmp_path):
    calls = []
    logo = b"example webp bytes"
    logo_path = tmp_path / "assets" / "brand" / "precisely-logo.webp"
    logo_path.parent.mkdir(parents=True)
    logo_path.write_bytes(logo)
    monkeypatch.setattr(ui, "__file__", str(tmp_path / "ui.py"))
    monkeypatch.setattr(ui.st, "markdown", lambda body, **kwargs: calls.append(body))

    ui.render_header()

    expected = base64.b64encode(logo).decode("ascii")
    assert f'src="data:image/webp;base64,{expected}"' in calls[0]
    assert calls[0].index("<img") < calls[0].index("Address Intelligence")
    assert 'alt="Precisely"' in calls[0]
    assert "height:28px" in calls[1]
    assert "filter:brightness(0) invert(1)" in calls[1]


def test_header_without_logo(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(ui, "__file__", str(tmp_path / "ui.py"))
    monkeypatch.setattr(ui.st, "markdown", lambda body, **kwargs: calls.append(body))

    ui.render_header()

    assert "Address Intelligence" in calls[0]
    assert '<div class="ai-mode">Runs on this computer</div>' in calls[0]
    assert "<img" not in calls[0]
