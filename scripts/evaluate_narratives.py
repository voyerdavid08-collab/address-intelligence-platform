"""Create aggregate narrative evaluation metrics and a local review sheet."""

import csv
import json
from pathlib import Path
import random
import argparse

from address_intelligence.client_profile import (
    DEFAULT_PROFILE_PATH,
    ClientProfile,
    load_client_profile,
    read_client_policies,
)
from address_intelligence.narrative import (
    NARRATIVES_PATH,
    POLICIES_PATH,
    PROJECT_ROOT,
    RESULTS_PATH,
    build_evidence,
)


_DEFAULT_PROFILE = load_client_profile(DEFAULT_PROFILE_PATH)
REPORTS_ROOT = _DEFAULT_PROFILE.reports_dir
SUMMARY_CSV_PATH = REPORTS_ROOT / "ai_narrative_summary.csv"
SUMMARY_MD_PATH = REPORTS_ROOT / "ai_narrative_summary.md"
REVIEW_SHEET_PATH = _DEFAULT_PROFILE.run_dir / "narrative_review_sheet.csv"
REVIEW_SEED = 20261029


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        return list(csv.DictReader(source))


def evaluate_narratives(
    profile: ClientProfile | None = None,
) -> dict[tuple[str, str], int]:
    narratives_path = (
        profile.run_dir / "narratives.jsonl" if profile else NARRATIVES_PATH
    )
    policies_path = profile.input_path if profile else POLICIES_PATH
    results_path = (
        profile.run_dir / "results_rowlevel.csv" if profile else RESULTS_PATH
    )
    reports_root = profile.reports_dir if profile else REPORTS_ROOT
    summary_csv_path = reports_root / "ai_narrative_summary.csv"
    summary_md_path = reports_root / "ai_narrative_summary.md"
    review_sheet_path = (
        profile.run_dir / "narrative_review_sheet.csv"
        if profile
        else REVIEW_SHEET_PATH
    )
    if not narratives_path.exists():
        narratives = []
    else:
        with narratives_path.open(encoding="utf-8") as source:
            narratives = [json.loads(line) for line in source if line.strip()]
    counts: dict[tuple[str, str], int] = {}
    for row in narratives:
        key = (row.get("status", ""), row.get("guardrail_reason", ""))
        counts[key] = counts.get(key, 0) + 1

    reports_root.mkdir(parents=True, exist_ok=True)
    with summary_csv_path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(
            destination,
            fieldnames=("status", "guardrail_reason", "count"),
        )
        writer.writeheader()
        for (status, reason), count in sorted(counts.items()):
            writer.writerow(
                {"status": status, "guardrail_reason": reason, "count": count}
            )

    lines = [
        "# AI Narrative Evaluation Summary",
        "",
        "Aggregate counts only; no row-level narratives or evidence are included.",
        "",
        "| Status | Guardrail reason | Count |",
        "| --- | --- | ---: |",
    ]
    if counts:
        lines.extend(
            f"| {status or '(none)'} | {reason or '(none)'} | {count} |"
            for (status, reason), count in sorted(counts.items())
        )
    else:
        lines.append("| (none) | (none) | 0 |")
    summary_md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _write_review_sheet(narratives, profile, policies_path, results_path, review_sheet_path)
    return counts


def _write_review_sheet(
    narratives: list[dict[str, object]],
    profile: ClientProfile | None = None,
    policies_path: Path = POLICIES_PATH,
    results_path: Path = RESULTS_PATH,
    review_sheet_path: Path = REVIEW_SHEET_PATH,
) -> None:
    generated = [row for row in narratives if row.get("status") == "generated"]
    generated.sort(key=lambda row: str(row.get("policy_id", "")))
    sample = random.Random(REVIEW_SEED).sample(
        generated, min(10, len(generated))
    )
    policies = {
        row["policy_id"]: row
        for row in (
            read_client_policies(profile) if profile is not None else _read_csv(policies_path)
        )
    }
    results = {row["policy_id"]: row for row in _read_csv(results_path)}
    review_sheet_path.parent.mkdir(parents=True, exist_ok=True)
    columns = (
        "policy_id",
        "narrative",
        "evidence",
        "evidence_fields_used",
        "missing_evidence",
        "all_claims_supported Y/N",
        "missing_evidence_stated Y/N",
        "clear_to_underwriter 1–5",
        "notes",
    )
    with review_sheet_path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=columns)
        writer.writeheader()
        for row in sample:
            policy_id = str(row["policy_id"])
            evidence = build_evidence(results[policy_id], policies[policy_id])
            writer.writerow(
                {
                    "policy_id": policy_id,
                    "narrative": row.get("narrative", ""),
                    "evidence": json.dumps(evidence, sort_keys=True),
                    "evidence_fields_used": json.dumps(
                        row.get("evidence_fields_used", [])
                    ),
                    "missing_evidence": json.dumps(row.get("missing_evidence", [])),
                    "all_claims_supported Y/N": "",
                    "missing_evidence_stated Y/N": "",
                    "clear_to_underwriter 1–5": "",
                    "notes": "",
                }
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate locally generated narratives.")
    parser.add_argument("--profile", type=Path, default=None)
    args = parser.parse_args()
    evaluate_narratives(load_client_profile(args.profile))
