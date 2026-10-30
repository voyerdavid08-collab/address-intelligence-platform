"""Dry-run-first CLI for triaging and verifying exception-remediation actions."""

from address_intelligence.remediation_actions import run_remediation_cli


if __name__ == "__main__":
    raise SystemExit(run_remediation_cli())
