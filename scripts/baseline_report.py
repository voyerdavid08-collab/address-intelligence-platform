"""Build aggregate-only pre-API baseline reports."""

from address_intelligence.baseline import build_baseline_report


def main() -> int:
    metrics = build_baseline_report()
    print("Wrote reports/baseline_summary.csv and reports/baseline_summary.md.")
    for metric, value in metrics.items():
        print(f"{metric}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
