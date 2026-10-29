"""Command-line entry point for local project checks."""

import argparse
from decimal import Decimal, InvalidOperation
import sys

from address_intelligence.config import Settings
from address_intelligence.credits import CreditLedger
from address_intelligence.logging_setup import configure_logging


def main() -> int:
    parser = argparse.ArgumentParser(description="Address Intelligence Platform")
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="print masked settings and local credit ledger status without network access",
    )
    parser.add_argument(
        "--record-external-spend",
        metavar="CREDITS",
        help="record already-incurred external credit spend",
    )
    parser.add_argument("--note", help="short note for external spend reconciliation")
    args = parser.parse_args()
    if not args.check_config and args.record_external_spend is None:
        parser.print_help()
        return 0

    settings = Settings.from_env()
    configure_logging(settings)
    ledger = CreditLedger(settings.credit_hard_cap)
    if args.record_external_spend is not None:
        if args.check_config:
            parser.error("--check-config and --record-external-spend cannot be combined")
        if args.note is None:
            parser.error("--note is required with --record-external-spend")
        try:
            external_credits = Decimal(args.record_external_spend)
        except InvalidOperation:
            parser.error("--record-external-spend must be a positive finite number")
        try:
            ledger.record_external(external_credits, args.note)
        except ValueError as exc:
            parser.error(str(exc))
    elif args.note is not None:
        parser.error("--note requires --record-external-spend")

    print(settings)
    print(
        f"Credits spent: {ledger.cumulative_spent} / {ledger.hard_cap}; "
        f"remaining: {ledger.remaining}"
    )
    if not args.check_config and args.record_external_spend is None:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())