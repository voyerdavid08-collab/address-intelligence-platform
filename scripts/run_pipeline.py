"""Dry-run-first CLI for separately invoked pipeline stages."""

from address_intelligence.pipeline import run_pipeline


if __name__ == "__main__":
    raise SystemExit(run_pipeline())
