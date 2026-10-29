from decimal import Decimal

import pytest

from address_intelligence.config import ConfigError, Settings
from address_intelligence.credits import CreditLedger


def test_settings_repr_masks_secrets(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PRECISELY_API_KEY", "key-secret-value")
    monkeypatch.setenv("PRECISELY_API_SECRET", "api-secret-value")

    settings = Settings.from_env()

    assert "****" in repr(settings)
    assert "key-secret-value" not in repr(settings)
    assert "api-secret-value" not in repr(settings)
    assert "key-secret-value" not in str(settings)
    assert "api-secret-value" not in str(settings)


def test_credentials_are_optional_until_required(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PRECISELY_API_KEY", raising=False)
    monkeypatch.delenv("PRECISELY_API_SECRET", raising=False)

    settings = Settings.from_env()

    assert settings.api_key is None
    assert settings.api_secret is None
    with pytest.raises(ConfigError, match="PRECISELY_API_KEY.*PRECISELY_API_SECRET"):
        settings.require_api_credentials()


@pytest.mark.parametrize("value", ["0", "-1", "invalid", "NaN", "Infinity"])
def test_invalid_credit_hard_cap_is_rejected(monkeypatch, tmp_path, value):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CREDIT_HARD_CAP", value)

    with pytest.raises(ConfigError, match="CREDIT_HARD_CAP"):
        Settings.from_env()


def test_credit_hard_cap_is_decimal(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CREDIT_HARD_CAP", "2150")

    settings = Settings.from_env()

    assert settings.credit_hard_cap == Decimal("2150")


def test_default_ledger_path_is_independent_of_current_directory(monkeypatch, tmp_path):
    first = CreditLedger(Decimal("2150")).path

    monkeypatch.chdir(tmp_path)
    second = CreditLedger(Decimal("2150")).path

    assert first == second
    assert first.name == "credit_ledger.json"
    assert first.parts[-3:] == ("data", "state", "credit_ledger.json")
