import csv
import json

from scripts import presentation_breakdown as breakdown


def _write_csv(path, columns, rows):
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(
            destination, fieldnames=columns, lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def _fixtures(tmp_path):
    input_path = tmp_path / "input.csv"
    ground_truth_path = tmp_path / "ground_truth.csv"
    results_path = tmp_path / "results.csv"
    cache_dir = tmp_path / "cache"
    ids = ("FIX-1", "FIX-2", "FIX-3", "FIX-4", "FIX-5", "FIX-6")
    _write_csv(
        input_path,
        ("policy_id", "raw_address_line1", "raw_address_line2", "raw_zip5"),
        [
            {"policy_id": ids[0], "raw_address_line1": "999 Wrong Rd", "raw_address_line2": "", "raw_zip5": "11111"},
            {"policy_id": ids[1], "raw_address_line1": "2 Main St", "raw_address_line2": "Apt 2", "raw_zip5": ""},
            {"policy_id": ids[2], "raw_address_line1": "3 Main St", "raw_address_line2": "", "raw_zip5": "33333"},
            {"policy_id": ids[3], "raw_address_line1": "4 Main St", "raw_address_line2": "", "raw_zip5": "99999"},
            {"policy_id": ids[4], "raw_address_line1": "999 Other Rd", "raw_address_line2": "", "raw_zip5": "55555"},
            {"policy_id": ids[5], "raw_address_line1": "6 Main St", "raw_address_line2": "", "raw_zip5": "66666"},
        ],
    )
    truth = [
        ("2 Main St", "Apt 1", "22222", "street_typo"),
        ("2 Main St", "Apt 2", "22222", "missing_zip"),
        ("3 Main St", "", "33333", ""),
        ("4 Main St", "", "44444", "wrong_zip"),
        ("5 Main St", "", "55555", "city_typo;nonexistent_address"),
        ("6 Main St", "", "66666", ""),
    ]
    _write_csv(
        ground_truth_path,
        (
            "policy_id",
            "clean_address_line1",
            "clean_address_line2",
            "clean_zip5",
            "defects",
        ),
        [
            {
                "policy_id": policy_id,
                "clean_address_line1": line1,
                "clean_address_line2": line2,
                "clean_zip5": zip5,
                "defects": defects,
            }
            for policy_id, (line1, line2, zip5, defects) in zip(ids, truth)
        ],
    )
    result_data = [
        ("S8", "eligible", "2 Main St", "Apt 1", "22222", "PID_PRIVATE_1"),
        ("S5", "eligible", "2 Main St", "Apt 2", "22222", ""),
        ("S7", "eligible", "3 Main St", "", "33333", "PID_PRIVATE_3"),
        ("S4", "manual_review", "4 Main St", "", "44444", "PID_PRIVATE_4"),
        ("Z1", "manual_review", "999 Other Rd", "", "55555", ""),
        ("G3", "eligible", "6 Main St", "", "66666", "PID_PRIVATE_6"),
    ]
    _write_csv(
        results_path,
        (
            "policy_id",
            "precision_code",
            "eligibility",
            "standardized_address_line1",
            "standardized_address_line2",
            "standardized_zip5",
            "preciselyid",
        ),
        [
            {
                "policy_id": policy_id,
                "precision_code": precision,
                "eligibility": eligibility,
                "standardized_address_line1": line1,
                "standardized_address_line2": line2,
                "standardized_zip5": zip5,
                "preciselyid": preciselyid,
            }
            for policy_id, (precision, eligibility, line1, line2, zip5, preciselyid)
            in zip(ids, result_data)
        ],
    )
    cache_values = (
        [{"outer": {"PB_KEY": "PB_PRIVATE_1"}}],
        [{"inner": {"PB_KEY": "PB_PRIVATE_2"}}],
        [{}, {"PB_KEY": "PB_PRIVATE_WRONG"}],
        [{"nested": [{"PB_KEY": "PB_PRIVATE_4"}]}],
        [{}],
        [{"custom": {"PB_KEY": "PB_PRIVATE_6"}}],
    )
    for policy_id, candidate in zip(ids, cache_values):
        path = cache_dir / f"{policy_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "status_code": 200,
                    "data": {"candidates": candidate},
                }
            ),
            encoding="utf-8",
        )
    return results_path, input_path, ground_truth_path, cache_dir, ids


def test_main_reports_expected_aggregate_counts_without_row_values(tmp_path):
    results_path, input_path, truth_path, cache_dir, ids = _fixtures(tmp_path)
    output_path = tmp_path / "analysis" / "breakdown.md"

    assert breakdown.main(
        results_path,
        input_path,
        truth_path,
        cache_dir,
        output_path,
    ) == 0

    report = output_path.read_text(encoding="utf-8")
    assert "| All (6) | 1 | 1 | 1 | 1 | 1 | 1 | 0 |" in report
    assert "| Clean (2) | 0 | 0 | 1 | 0 | 0 | 1 | 0 |" in report
    assert "| Flawed (4) | 1 | 1 | 0 | 1 | 1 | 0 | 0 |" in report
    assert "| Eligible (4) | 1 | 1 | 1 | 0 | 0 | 1 | 0 |" in report
    assert "| Eligible clean (2) | 0 | 0 | 1 | 0 | 0 | 1 | 0 |" in report
    assert "| Eligible flawed (2) | 1 | 1 | 0 | 0 | 0 | 0 | 0 |" in report
    assert "| street_typo | 1 | 0 | 1 | 1 |" in report
    assert "| missing_zip | 1 | 0 | 1 | 1 |" in report
    assert "| wrong_zip | 1 | 0 | 1 | 1 |" in report
    assert "| city_typo | 1 | 0 | 0 | 0 |" in report
    assert "| nonexistent_address | 1 | 0 | 0 | 0 |" in report
    assert "| all flawed | 4 | 0 | 3 | 3 |" in report
    assert "| ZIP-only gain (raw line already matched) | 2 |" in report
    assert "| Street or number gain | 1 |" in report
    assert "| Flawed before | 4 | 0 |" in report
    assert "| Flawed after | 4 | 3 |" in report
    assert "| Gain | 4 | 3 |" in report
    assert "- Non-blank PreciselyID in results: 3" in report
    assert "- Non-blank PB_KEY in geocode cache: 3" in report
    assert "- Records with both: 2" in report
    assert "| address point | 1 | 1 | 1 |" in report
    assert "| street address | 1 | 1 | 0 |" in report
    assert "| street centroid | 0 | 0 | 0 |" in report
    assert "| postal or city level | 1 | 1 | 1 |" in report
    private_values = (
        *ids,
        "999 Wrong Rd",
        "2 Main St",
        "3 Main St",
        "4 Main St",
        "999 Other Rd",
        "6 Main St",
        "Apt 1",
        "Apt 2",
        "11111",
        "22222",
        "33333",
        "44444",
        "55555",
        "66666",
        "PID_PRIVATE_1",
        "PID_PRIVATE_3",
        "PID_PRIVATE_4",
        "PID_PRIVATE_6",
        "PB_PRIVATE_1",
        "PB_PRIVATE_2",
        "PB_PRIVATE_WRONG",
        "PB_PRIVATE_4",
        "PB_PRIVATE_6",
    )
    for private_value in private_values:
        assert private_value not in report
