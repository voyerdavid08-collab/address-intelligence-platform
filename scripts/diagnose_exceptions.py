"""Dry-run-first CLI for exception cause diagnosis."""

from address_intelligence.remediation import run_diagnosis_cli


if __name__ == "__main__":
    raise SystemExit(run_diagnosis_cli())
