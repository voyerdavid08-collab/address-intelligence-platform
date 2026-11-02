import csv

from address_intelligence.baseline import compute_baseline_metrics, write_baseline_report


def _write(path, columns, rows):
    with path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def test_baseline_metrics_use_input_and_separate_ground_truth(tmp_path):
    policies = tmp_path / "policies.csv"
    truth = tmp_path / "truth.csv"
    references = tmp_path / "references.csv"
    policy_columns = (
        "policy_id", "raw_address_line1", "raw_address_line2", "raw_city",
        "raw_state", "raw_zip5",
    )
    policy_rows = [
        {
            "policy_id": "POL-000001",
            "raw_address_line1": "2099 Westheimer Rd",
            "raw_address_line2": "",
            "raw_city": "Houston",
            "raw_state": "TX",
            "raw_zip5": "77098",
        },
        {
            "policy_id": "POL-000002",
            "raw_address_line1": "Westheimer Rd",
            "raw_address_line2": "",
            "raw_city": "Houston",
            "raw_state": "TX",
            "raw_zip5": "",
        },
        {
            "policy_id": "POL-000003",
            "raw_address_line1": "2099 Westheimer Rd",
            "raw_address_line2": "",
            "raw_city": "Houston",
            "raw_state": "TX",
            "raw_zip5": "77098",
        },
    ]
    _write(policies, policy_columns, policy_rows)
    _write(
        references,
        ("street_id", "street_name", "city", "state", "zip5"),
        [{
            "street_id": "HOU-01",
            "street_name": "Westheimer Rd",
            "city": "Houston",
            "state": "TX",
            "zip5": "77098",
        }],
    )
    _write(
        truth,
        ("policy_id", "defects"),
        [
            {"policy_id": "POL-000001", "defects": "street_typo"},
            {"policy_id": "POL-000002", "defects": "street_typo;city_typo"},
            {"policy_id": "POL-000003", "defects": ""},
        ],
    )

    metrics = compute_baseline_metrics(policies, truth, references)

    assert metrics["Input rows"] == "3"
    assert metrics["Missing ZIP"] == "1 (33.3%)"
    assert metrics["Missing house number"] == "1 (33.3%)"
    assert metrics["Unit where expected unknown"] == "not measurable pre-API"
    assert metrics["ZIP format valid"] == "2 (66.7%)"
    assert metrics["State format valid"] == "3 (100.0%)"
    assert metrics["City-state-ZIP matches reference"] == "2 (66.7%)"
    assert metrics["Duplicate address records beyond first"] == "1 (33.3%)"
    assert metrics["street_typo actual (target 10%)"] == "2 (66.7%)"
    assert metrics["Unique dirty records"] == "2 (66.7%)"
    assert metrics["Overlapping records (2+ defects)"] == "1 (33.3%)"

    csv_report = tmp_path / "reports" / "summary.csv"
    md_report = tmp_path / "reports" / "summary.md"
    write_baseline_report(metrics, csv_report, md_report)
    assert "2099 Westheimer Rd" not in csv_report.read_text(encoding="utf-8")
    assert "Ground-truth defect injection" in md_report.read_text(encoding="utf-8")
    assert "Defect combinations" in md_report.read_text(encoding="utf-8")
    with csv_report.open(newline="", encoding="utf-8") as source:
        report_rows = list(csv.DictReader(source))
    assert {
        row["combination"]: row["count"]
        for row in report_rows
        if row["section"] == "Defect combinations"
    }["street_typo+city_typo"] == "1"
