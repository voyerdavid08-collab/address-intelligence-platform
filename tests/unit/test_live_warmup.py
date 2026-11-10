"""The warm-up's settings check reports names only, never values."""

import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "live_warmup", Path(__file__).resolve().parents[2] / "scripts" / "live_warmup.py"
)
warmup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(warmup)


def test_settings_check_flags_missing_and_wrong_values_without_showing_them():
    rows = warmup.check_settings({"PRECISELY_MCP_APP_TOKEN": "secret-token", "GROQ_API_KEY": "", "AIP_LIVE_AI": "0"})
    assert rows == [
        ("Setting PRECISELY_MCP_APP_TOKEN", True, "set"),
        ("Setting GROQ_API_KEY", False, "missing"),
        ("Setting AIP_LIVE_AI", False, "must be 1"),
    ]
    assert "secret-token" not in repr(rows)


def test_main_reports_not_ready_when_settings_are_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(warmup, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    assert warmup.main([]) == 1
    assert "Not ready" in capsys.readouterr().out
