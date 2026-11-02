"""Dry-run-first CLI for evidence-limited underwriting narratives."""

import argparse
import os
import sys

from address_intelligence.client_profile import load_client_profile
from address_intelligence.narrative import run_narrative_cli


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--profile")
    options, remaining = parser.parse_known_args(arguments)
    profile = load_client_profile(options.profile)
    if not profile.ai_enabled("narratives"):
        print(
            f"Narratives are disabled by client profile '{profile.data['client']['name']}'; "
            "no narrative work was run."
        )
        return 0
    if options.profile is not None:
        os.environ["CLIENT_PROFILE"] = str(profile.path)
    return run_narrative_cli(remaining)


if __name__ == "__main__":
    raise SystemExit(main())
