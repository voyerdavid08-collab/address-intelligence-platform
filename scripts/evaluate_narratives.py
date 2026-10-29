"""Create aggregate narrative evaluation metrics and a local review sheet."""

import csv
import json
from pathlib import Path
import random

from address_intelligence.narrative import (
    NARRATIVES_PATH,
    POLICIES_PATH,
    PROJECT_ROOT,
    RESULTS_PATH,
    build_evidence,
)


REPORTS_ROOT = PROJECT_ROOT / "reports"
SUMMARY_CSV_PATH = REPORTS_ROOT / "ai_narrative_summary.csv"
SUMMARY_MD_PATH = REPORTS_ROOT / "ai_narrative_summary.md"
REVIEW_SHEET_PATH = PROJECT_ROOT / "data" / "output" / "run" / "narrative_review_sheet.csv"
REVIEW_SEED = 20261029


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        return list(csv.DictReader(source))


def evaluate_narratives() -> dict[tuple[str, str], int]:
    if not NARRATIVES_PATH.exists():
        narratives = []
    else:
        with NARRATIVES_PATH.open(encoding="utf-8") as source:
            narratives = [json.loads(line) for line in source if line.strip()]
    counts: dict[tuple[str, str], int] = {}
    for row in narratives:
        key = (row.get("status", ""), row.get("guardrail_reason", ""))
        counts[key] = counts.get(key, 0) + 1

    REPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    with SUMMARY_CSV_PATH.open("w", newline="", encoding="utf-8") as destination:
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
    SUMMARY_MD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _write_review_sheet(narratives)
    return counts


def _write_review_sheet(narratives: list[dict[str, object]]) -> None:
    generated = [row for row in narratives if row.get("status") == "generated"]
    generated.sort(key=lambda row: str(row.get("policy_id", "")))
    sample = random.Random(REVIEW_SEED).sample(
        generated, min(10, len(generated))
    )
    policies = {row["policy_id"]: row for row in _read_csv(POLICIES_PATH)}
    results = {row["policy_id"]: row for row in _read_csv(RESULTS_PATH)}
    REVIEW_SHEET_PATH.parent.mkdir(parents=True, exist_ok=True)
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
    with REVIEW_SHEET_PATH.open("w", newline="", encoding="utf-8") as destination:
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
    evaluate_narratives()
