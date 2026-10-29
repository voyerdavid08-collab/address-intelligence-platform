"""Generate synthetic policy input and separate ground truth."""

import argparse

from address_intelligence.synthetic_data import (
    DEFECT_TARGETS,
    generate_synthetic,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate synthetic policy records")
    parser.add_argument("--n", type=int, default=200, help="record count (200-500)")
    parser.add_argument("--seed", type=int, default=20261029, help="random seed")
    args = parser.parse_args(argv)
    if not 200 <= args.n <= 500:
        parser.error("--n must be between 200 and 500")
    summary = generate_synthetic(args.n, args.seed)
    print(f"Generated records: {summary.record_count}")
    print(f"Seed: {summary.seed}")
    print("Actual defect counts (% of all records):")
    for defect, target in DEFECT_TARGETS.items():
        count = summary.defect_counts[defect]
        print(f"- {defect}: {count} ({count / summary.record_count:.1%}; target {target:.0%})")
    print(
        f"Unique dirty records: {summary.unique_dirty_count} "
        f"({summary.unique_dirty_count / summary.record_count:.1%})"
    )
    print(
        f"Overlapping records (2+ defects): {summary.overlap_count} "
        f"({summary.overlap_count / summary.record_count:.1%})"
    )
    print("Wrote data/input/policyholders_synthetic.csv and data/input/ground_truth.csv.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
