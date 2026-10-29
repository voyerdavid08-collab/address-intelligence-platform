"""Persistent credit accounting for budgeted provider operations."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]


# Source: docs/API_DISCOVERY.md, Account pricing observed 2026-10-28.
PRICE_TABLE: dict[str, Decimal] = {
    "geocode_premium": Decimal("1"),
    "geocode_advanced": Decimal("0.5"),
    "geocode_basic": Decimal("0.1"),
    "preciselyid_by_address": Decimal("0.01"),
    "flood_by_location": Decimal("5"),
    "distance_to_flood_hazard_by_location": Decimal("2"),
    "property_attributes_v2": Decimal("1"),
    "property_pbkey": Decimal("0.01"),
    "address_verification_us": Decimal("1"),
    "address_verification_uscan": Decimal("0.33"),
}

LOGGER = logging.getLogger(__name__)


class UnknownOperationError(ValueError):
    """Raised when an operation is not present in the verified price table."""


class CreditLedgerError(ValueError):
    """Raised when the persisted credit ledger cannot be read safely."""


class CreditCapExceeded(RuntimeError):
    """Raised when a proposed call would exceed the cumulative credit cap."""


class CreditLedger:
    """Track successful credit charges across runs in a local JSON file."""

    def __init__(
        self,
        hard_cap: Decimal,
        path: str | Path | None = None,
    ) -> None:
        cap = Decimal(hard_cap)
        if not cap.is_finite() or cap <= 0:
            raise ValueError("hard_cap must be a positive number.")
        self.hard_cap = cap
        self.path = (
            Path(path)
            if path is not None
            else PROJECT_ROOT / "data" / "state" / "credit_ledger.json"
        )

    @property
    def cumulative_spent(self) -> Decimal:
        return self._read_state()["spent"]

    @property
    def remaining(self) -> Decimal:
        return self.hard_cap - self.cumulative_spent

    def check(self, operation: str, quantity: int) -> Decimal:
        """Estimate a call and reject it before execution if the cap would be exceeded."""
        cost = self._cost(operation, quantity)
        spent = self.cumulative_spent
        if spent + cost > self.hard_cap:
            raise CreditCapExceeded(
                f"Credit cap exceeded: spent {spent}, estimate {cost}, "
                f"cap {self.hard_cap}."
            )
        return cost

    def record_success(self, operation: str, quantity: int) -> Decimal:
        """Persist the charge for a successful call that returned results."""
        cost = self._cost(operation, quantity)
        state = self._read_state()
        state["spent"] += cost
        state["entries"].append(self._entry(operation, quantity, cost, "success"))
        self._write_state(state)
        return cost

    def record_failure(self, operation: str) -> None:
        """Log a failed call and persist zero credits, per trial charging rules."""
        self._price(operation)
        LOGGER.warning("Provider operation failed; zero credits recorded: %s", operation)
        state = self._read_state()
        state["entries"].append(self._entry(operation, 0, Decimal("0"), "failure"))
        self._write_state(state)

    def record_external(self, credits: Decimal, note: str) -> None:
        """Record external portal spend without logging potentially sensitive notes."""
        try:
            amount = Decimal(credits)
        except (InvalidOperation, TypeError, ValueError):
            raise ValueError("credits must be a positive finite number.") from None
        if not amount.is_finite() or amount <= 0:
            raise ValueError("credits must be a positive finite number.")
        if not isinstance(note, str) or not note.strip() or len(note.strip()) > 200:
            raise ValueError("note must be non-empty text of at most 200 characters.")

        state = self._read_state()
        state["spent"] += amount
        entry = self._entry("external", 1, amount, "external")
        entry["note"] = note.strip()
        state["entries"].append(entry)
        self._write_state(state)

    def _cost(self, operation: str, quantity: int) -> Decimal:
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
            raise ValueError("quantity must be a positive integer.")
        return self._price(operation) * quantity

    @staticmethod
    def _price(operation: str) -> Decimal:
        try:
            return PRICE_TABLE[operation]
        except KeyError:
            raise UnknownOperationError(f"Unknown credit operation: {operation}.") from None

    @staticmethod
    def _entry(
        operation: str, quantity: int, credits: Decimal, outcome: str
    ) -> dict[str, Any]:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "operation": operation,
            "quantity": quantity,
            "credits": str(credits),
            "outcome": outcome,
        }

    def _read_state(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"spent": Decimal("0"), "entries": []}
        try:
            raw_state = json.loads(self.path.read_text(encoding="utf-8"))
            spent = Decimal(raw_state["cumulative_spent"])
            entries = raw_state["entries"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError, InvalidOperation):
            raise CreditLedgerError(
                f"Unable to read credit ledger at {self.path}."
            ) from None
        if not spent.is_finite() or spent < 0 or not isinstance(entries, list):
            raise CreditLedgerError(f"Invalid credit ledger at {self.path}.")
        return {"spent": spent, "entries": entries}

    def _write_state(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        serialized = {
            "cumulative_spent": str(state["spent"]),
            "entries": state["entries"],
        }
        self.path.write_text(
            json.dumps(serialized, indent=2) + "\n",
            encoding="utf-8",
        )
