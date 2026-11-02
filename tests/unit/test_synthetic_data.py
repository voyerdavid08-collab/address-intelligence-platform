import csv
from datetime import date
from pathlib import Path

import pytest

from address_intelligence.synthetic_data import (
    DEFECT_COMBINATIONS,
    DEFECT_TARGETS,
    OUTCOME_SEVERITY,
    POLICY_COLUMNS,
    generate_synthetic,
)


def _read(path):
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def _generate(tmp_path, suffix, *, n=200, seed=20261029):
    return generate_synthetic(
        n,
        seed,
        policyholders_path=tmp_path / f"policyholders-{suffix}.csv",
        ground_truth_path=tmp_path / f"truth-{suffix}.csv",
    )


def test_same_seed_writes_byte_identical_files(tmp_path):
    _generate(tmp_path, "first")
    _generate(tmp_path, "second")

    assert (tmp_path / "policyholders-first.csv").read_bytes() == (
        tmp_path / "policyholders-second.csv"
    ).read_bytes()
    assert (tmp_path / "truth-first.csv").read_bytes() == (
        tmp_path / "truth-second.csv"
    ).read_bytes()


@pytest.mark.parametrize("n", [199, 501])
def test_record_count_outside_supported_range_is_rejected(n, tmp_path):
    with pytest.raises(ValueError, match="200 and 500"):
        _generate(tmp_path, f"invalid-{n}", n=n)


def test_upper_record_count_limit_is_supported(tmp_path):
    summary = _generate(tmp_path, "max", n=500)

    assert summary.record_count == 500
    assert len(_read(tmp_path / "policyholders-max.csv")) == 500


def test_policy_and_truth_outputs_match_defect_plan(tmp_path):
    summary = _generate(tmp_path, "main")
    policies = _read(tmp_path / "policyholders-main.csv")
    truth = _read(tmp_path / "truth-main.csv")
    data_dir = Path(__file__).parents[2] / "data" / "input"
    references = {
        row["street_id"]: row
        for row in _read(data_dir / "reference_streets.csv")
    }
    accepted = {
        row["street_id"]
        for row in _read(data_dir / "reference_streets_validation.csv")
        if row["status"].startswith("accepted")
    }
    truth_by_id = {row["policy_id"]: row for row in truth}

    assert len(policies) == len(truth) == 200
    assert len({row["policy_id"] for row in policies}) == 200
    assert tuple(policies[0]) == POLICY_COLUMNS
    assert all(
        date(2023, 10, 30) <= date.fromisoformat(row["effective_date"]) <= date(2026, 10, 29)
        for row in policies
    )
    assert not any("name" in column.casefold() for column in policies[0])
    assert "defects" not in policies[0]
    assert all(row["street_id"] in accepted for row in policies)
    street_distribution = {}
    for row in policies:
        street_distribution[row["street_id"]] = street_distribution.get(row["street_id"], 0) + 1
    assert max(street_distribution.values()) - min(street_distribution.values()) <= 1
    assert sum(bool(row["clean_address_line2"]) for row in truth) == 30
    assert all(row["line_of_business"] in {"Homeowners", "Commercial Property"} for row in policies)
    homeowner_count = sum(row["line_of_business"] == "Homeowners" for row in policies)
    assert 140 <= homeowner_count <= 180
    assert set(row["expected_outcome"] for row in truth) <= {
        "correctable",
        "manual_review",
        "unresolvable",
        "not_detectable",
        "to_be_measured",
    }

    house_numbers_by_street = {}
    for policy in policies:
        truth_row = truth_by_id[policy["policy_id"]]
        defects = set(filter(None, truth_row["defects"].split(";")))
        if "missing_house_number" in defects:
            assert policy["raw_address_line1"] == references[policy["street_id"]]["street_name"]
        else:
            number = int(policy["raw_address_line1"].split()[0])
            street = references[policy["street_id"]]
            if "nonexistent_address" in defects:
                assert number > int(street["house_number_max"])
            else:
                assert int(street["house_number_min"]) <= number <= int(street["house_number_max"])
            house_numbers_by_street.setdefault(policy["street_id"], []).append(number)
        if "missing_unit" in defects:
            assert truth_row["clean_address_line2"]
            assert not policy["raw_address_line2"]
        if "missing_zip" in defects:
            assert not policy["raw_zip5"]
        if "street_typo" in defects:
            clean_street = truth_row["clean_address_line1"].split(" ", 1)[1]
            raw_street = policy["raw_address_line1"].split(" ", 1)[1]
            assert len(clean_street) == len(raw_street)
            assert sum(a != b for a, b in zip(clean_street, raw_street)) == 1
        if "city_typo" in defects:
            clean_city = truth_row["clean_city"]
            raw_city = policy["raw_city"]
            assert len(clean_city) == len(raw_city)
            assert sum(a != b for a, b in zip(clean_city, raw_city)) == 1
        if "wrong_zip" in defects:
            assert policy["raw_zip5"] != truth_row["clean_zip5"]
            assert any(
                row["zip5"] == policy["raw_zip5"]
                and row["metro"] != policy["metro"]
                for row in references.values()
            )

    assert all(len(numbers) == len(set(numbers)) for numbers in house_numbers_by_street.values())
    actual_counts = {
        defect: sum(defect in row["defects"].split(";") for row in truth)
        for defect in DEFECT_TARGETS
    }
    assert actual_counts == {
        "street_typo": 20,
        "city_typo": 12,
        "missing_zip": 16,
        "wrong_zip": 10,
        "missing_house_number": 10,
        "missing_unit": 10,
        "nonexistent_address": 8,
    }
    assert summary.defect_counts == actual_counts
    assert summary.unique_dirty_count == sum(bool(row["defects"]) for row in truth)
    assert summary.overlap_count == sum(int(row["defect_count"]) > 1 for row in truth)
    assert summary.unique_dirty_count < sum(actual_counts.values())
    assert summary.overlap_count == 10
    assert len({row["street_id"] for row in policies}) == len(accepted)
    observed_combinations = {
        tuple(sorted(row["defects"].split(";"))): 0
        for row in truth
        if int(row["defect_count"]) == 2
    }
    for row in truth:
        if int(row["defect_count"]) == 2:
            combination = tuple(sorted(row["defects"].split(";")))
            observed_combinations[combination] += 1
            outcomes = {
                "street_typo": "correctable",
                "city_typo": "correctable",
                "missing_zip": "correctable",
                "wrong_zip": "to_be_measured",
                "missing_house_number": "manual_review",
                "missing_unit": "not_detectable",
                "nonexistent_address": "unresolvable",
            }
            expected = max(
                (outcomes[defect] for defect in combination),
                key=OUTCOME_SEVERITY.__getitem__,
            )
            assert row["expected_outcome"] == expected

    expected_combinations = {
        tuple(sorted(combination)): count
        for combination, count in DEFECT_COMBINATIONS.items()
    }
    assert observed_combinations == expected_combinations
