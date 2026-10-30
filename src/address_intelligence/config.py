"""Environment-backed, secret-safe application settings."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import logging
import os
from pathlib import Path

from dotenv import dotenv_values


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(ValueError):
    """Raised when application configuration is invalid or incomplete."""


@dataclass(frozen=True, repr=False)
class Settings:
    api_key: str | None
    api_secret: str | None
    base_url: str
    credit_hard_cap: Decimal
    trial_credits: Decimal
    log_level: str
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-120b"
    groq_daily_token_budget: int = 180000
    remediation_samples: int = 5
    conformal_alpha: float = 0.10

    @classmethod
    def from_env(cls, *, dotenv_path: str | Path | None = None) -> "Settings":
        """Load settings from process environment and an optional local .env."""
        path = Path(dotenv_path) if dotenv_path is not None else PROJECT_ROOT / ".env"
        dotenv_settings = dotenv_values(path)

        def value(key: str, default: str) -> str:
            process_value = os.environ.get(key)
            if process_value:
                return process_value
            dotenv_value = dotenv_settings.get(key)
            return dotenv_value if dotenv_value else default

        cap_value = value("CREDIT_HARD_CAP", "2150")
        try:
            credit_hard_cap = Decimal(cap_value)
        except InvalidOperation:
            raise ConfigError("CREDIT_HARD_CAP must be a positive number.") from None
        if not credit_hard_cap.is_finite() or credit_hard_cap <= 0:
            raise ConfigError("CREDIT_HARD_CAP must be a positive number.")

        trial_value = value("TRIAL_CREDITS", "2500")
        try:
            trial_credits = Decimal(trial_value)
        except InvalidOperation:
            raise ConfigError("TRIAL_CREDITS must be a positive number.") from None
        if not trial_credits.is_finite() or trial_credits <= 0:
            raise ConfigError("TRIAL_CREDITS must be a positive number.")

        log_level = value("LOG_LEVEL", "INFO").upper()
        if log_level not in logging.getLevelNamesMapping():
            raise ConfigError("LOG_LEVEL must be a valid standard-library logging level.")

        token_budget_value = value("GROQ_DAILY_TOKEN_BUDGET", "180000")
        try:
            groq_daily_token_budget = int(token_budget_value)
        except ValueError:
            raise ConfigError(
                "GROQ_DAILY_TOKEN_BUDGET must be a positive integer."
            ) from None
        if groq_daily_token_budget <= 0:
            raise ConfigError(
                "GROQ_DAILY_TOKEN_BUDGET must be a positive integer."
            )

        samples_value = value("REMEDIATION_SAMPLES", "5")
        try:
            remediation_samples = int(samples_value)
        except ValueError:
            raise ConfigError(
                "REMEDIATION_SAMPLES must be a positive integer."
            ) from None
        if remediation_samples <= 0:
            raise ConfigError("REMEDIATION_SAMPLES must be a positive integer.")

        alpha_value = value("CONFORMAL_ALPHA", "0.10")
        try:
            conformal_alpha = float(alpha_value)
        except ValueError:
            raise ConfigError("CONFORMAL_ALPHA must be between 0 and 1.") from None
        if not 0 < conformal_alpha < 1:
            raise ConfigError("CONFORMAL_ALPHA must be between 0 and 1.")

        return cls(
            api_key=value("PRECISELY_API_KEY", "") or None,
            api_secret=value("PRECISELY_API_SECRET", "") or None,
            base_url=value("PRECISELY_BASE_URL", "https://api.precisely.com"),
            credit_hard_cap=credit_hard_cap,
            trial_credits=trial_credits,
            log_level=log_level,
            groq_api_key=value("GROQ_API_KEY", "") or None,
            groq_model=value("GROQ_MODEL", "openai/gpt-oss-120b"),
            groq_daily_token_budget=groq_daily_token_budget,
            remediation_samples=remediation_samples,
            conformal_alpha=conformal_alpha,
        )

    def require_api_credentials(self) -> tuple[str, str]:
        """Return credentials or raise without including secret values."""
        if not self.api_key or not self.api_secret:
            missing = []
            if not self.api_key:
                missing.append("PRECISELY_API_KEY")
            if not self.api_secret:
                missing.append("PRECISELY_API_SECRET")
            raise ConfigError(f"Missing required API credential(s): {', '.join(missing)}.")
        return self.api_key, self.api_secret

    def __repr__(self) -> str:
        api_key = "****" if self.api_key else None
        api_secret = "****" if self.api_secret else None
        return (
            "Settings("
            f"api_key={api_key!r}, api_secret={api_secret!r}, "
            f"base_url={self.base_url!r}, "
            f"credit_hard_cap={self.credit_hard_cap!r}, "
            f"trial_credits={self.trial_credits!r}, "
            f"groq_api_key={'****' if self.groq_api_key else None!r}, "
            f"groq_model={self.groq_model!r}, "
            f"groq_daily_token_budget={self.groq_daily_token_budget!r}, "
            f"remediation_samples={self.remediation_samples!r}, "
            f"conformal_alpha={self.conformal_alpha!r}, "
            f"log_level={self.log_level!r})"
        )

    def __str__(self) -> str:
        return self.__repr__()
