"""Generate deterministic synthetic policy records on validated street anchors."""

import csv
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
import random

from faker import Faker


PROJECT_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_PATH = PROJECT_ROOT / "data" / "input" / "reference_streets.csv"
VALIDATION_PATH = PROJECT_ROOT / "data" / "input" / "reference_streets_validation.csv"
POLICYHOLDERS_PATH = PROJECT_ROOT / "data" / "input" / "policyholders_synthetic.csv"
GROUND_TRUTH_PATH = PROJECT_ROOT / "data" / "input" / "ground_truth.csv"
EFFECTIVE_DATE_REFERENCE = date(2026, 10, 29)

DEFECT_TARGETS = {
    "street_typo": Decimal("0.10"),
    "city_typo": Decimal("0.06"),
    "missing_zip": Decimal("0.08"),
    "wrong_zip": Decimal("0.05"),
    "missing_house_number": Decimal("0.05"),
    "missing_unit": Decimal("0.05"),
    "nonexistent_address": Decimal("0.04"),
}
DEFECT_COMBINATIONS = {
    ("street_typo", "missing_zip"): 2,
    ("city_typo", "wrong_zip"): 2,
    ("street_typo", "city_typo"): 2,
    ("city_typo", "missing_zip"): 2,
    ("street_typo", "missing_unit"): 2,
}
OUTCOME_SEVERITY = {
    "correctable": 0,
    "not_detectable": 1,
    "to_be_measured": 2,
    "manual_review": 3,
    "unresolvable": 4,
}
POLICY_COLUMNS = (
    "policy_id",
    "line_of_business",
    "effective_date",
    "insured_value_band",
    "metro",
    "street_id",
    "raw_address_line1",
    "raw_address_line2",
    "raw_city",
    "raw_state",
    "raw_zip5",
)
GROUND_TRUTH_COLUMNS = (
    "policy_id",
    "clean_address_line1",
    "clean_address_line2",
    "clean_city",
    "clean_state",
    "clean_zip5",
    "street_id",
    "defects",
    "defect_count",
    "expected_outcome",
)


@dataclass(frozen=True)
class GenerationSummary:
    record_count: int
    seed: int
    defect_counts: dict[str, int]
    unique_dirty_count: int
    overlap_count: int


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as source:
        return list(csv.DictReader(source))


def _target_count(n: int, proportion: Decimal) -> int:
    return int(Decimal(n) * proportion + Decimal("0.5"))


def _mutate_one_character(value: str, rng: random.Random) -> str:
    positions = [index for index, character in enumerate(value) if character.isalpha()]
    if not positions:
        raise ValueError(f"Cannot inject a typo into {value!r}.")
    position = rng.choice(positions)
    original = value[position]
    alphabet = "abcdefghijklmnopqrstuvwxyz"
    replacement = rng.choice([letter for letter in alphabet if letter != original.lower()])
    if original.isupper():
        replacement = replacement.upper()
    return value[:position] + replacement + value[position + 1 :]


def _expected_outcome(defects: list[str]) -> str:
    outcomes = {
        "street_typo": "correctable",
        "city_typo": "correctable",
        "missing_zip": "correctable",
        "wrong_zip": "to_be_measured",
        "missing_house_number": "manual_review",
        "missing_unit": "not_detectable",
        "nonexistent_address": "unresolvable",
    }
    return max(
        (outcomes[defect] for defect in defects),
        key=OUTCOME_SEVERITY.__getitem__,
        default="correctable",
    )


def generate_synthetic(
    n: int = 200,
    seed: int = 20261029,
    *,
    reference_path: Path = REFERENCE_PATH,
    validation_path: Path = VALIDATION_PATH,
    policyholders_path: Path = POLICYHOLDERS_PATH,
    ground_truth_path: Path = GROUND_TRUTH_PATH,
) -> GenerationSummary:
    """Write a synthetic input and separate ground-truth CSV."""
    if not 200 <= n <= 500:
        raise ValueError("n must be between 200 and 500.")

    references = _read_csv(reference_path)
    validations = _read_csv(validation_path)
    accepted_ids = {
        row["street_id"]
        for row in validations
        if row["status"].startswith("accepted")
    }
    streets = [row for row in references if row["street_id"] in accepted_ids]
    if not streets:
        raise ValueError("No accepted reference streets are available.")

    rng = random.Random(seed)
    faker = Faker("en_US")
    faker.seed_instance(seed)
    today = EFFECTIVE_DATE_REFERENCE

    street_assignments = [streets[index % len(streets)] for index in range(n)]
    rng.shuffle(street_assignments)
    grouped_indices: dict[str, list[int]] = {}
    for index, street in enumerate(street_assignments):
        grouped_indices.setdefault(street["street_id"], []).append(index)
    house_numbers: dict[int, int] = {}
    for street in streets:
        indices = grouped_indices.get(street["street_id"], [])
        low = int(street["house_number_min"])
        high = int(street["house_number_max"])
        if low <= 0 or high < low or len(indices) > high - low + 1:
            raise ValueError(f"Invalid or insufficient house-number range for {street['street_id']}.")
        for index, number in zip(indices, rng.sample(range(low, high + 1), len(indices))):
            house_numbers[index] = number

    unit_count = _target_count(n, Decimal("0.15"))
    unit_indices = set(rng.sample(range(n), unit_count))
    clean_units = {
        index: rng.choice(("Apt 2B", "Unit 4", "Apt 3", "Unit 2A"))
        for index in unit_indices
    }
    targets = {
        defect: _target_count(n, proportion)
        for defect, proportion in DEFECT_TARGETS.items()
    }

    selected: dict[str, set[int]] = {defect: set() for defect in DEFECT_TARGETS}
    pair_rows: dict[tuple[str, str], set[int]] = {}
    missing_unit_pair = ("street_typo", "missing_unit")
    pair_rows[missing_unit_pair] = set(
        rng.sample(sorted(unit_indices), DEFECT_COMBINATIONS[missing_unit_pair])
    )
    remaining = set(range(n)) - pair_rows[missing_unit_pair]
    for pair, count in DEFECT_COMBINATIONS.items():
        if pair == missing_unit_pair:
            continue
        chosen = set(rng.sample(sorted(remaining), count))
        pair_rows[pair] = chosen
        remaining -= chosen

    for pair, indices in pair_rows.items():
        for defect in pair:
            selected[defect].update(indices)

    for defect, target in targets.items():
        required_singles = target - len(selected[defect])
        if defect == "missing_unit":
            eligible = sorted(unit_indices & remaining)
        else:
            eligible = sorted(remaining)
        if required_singles > len(eligible):
            raise ValueError(f"Unable to allocate target count for {defect}.")
        chosen = set(rng.sample(eligible, required_singles))
        selected[defect].update(chosen)
        remaining.difference_update(chosen)

    defect_membership: dict[int, list[str]] = {}
    for defect, indices in selected.items():
        for index in indices:
            defect_membership.setdefault(index, []).append(defect)
    if any(len(defects) > 2 for defects in defect_membership.values()):
        raise ValueError("Defect allocation exceeded two defects for a record.")

    other_metro_zips = {
        street["metro"]: sorted(
            {
                candidate["zip5"]
                for candidate in streets
                if candidate["metro"] != street["metro"]
                and candidate["zip5"] != street["zip5"]
            }
        )
        for street in streets
    }
    nonexistent_numbers: dict[int, int] = {}
    for street in streets:
        indices = sorted(
            index
            for index in selected["nonexistent_address"]
            if street_assignments[index]["street_id"] == street["street_id"]
        )
        for offset, index in enumerate(indices):
            nonexistent_numbers[index] = int(street["house_number_max"]) + 50000 + offset

    policy_rows: list[dict[str, str]] = []
    truth_rows: list[dict[str, str]] = []
    for index, street in enumerate(street_assignments):
        number = house_numbers[index]
        clean_line1 = f"{number} {street['street_name']}"
        clean_line2 = clean_units.get(index, "")
        raw_line1 = clean_line1
        raw_line2 = clean_line2
        raw_city = street["city"]
        raw_zip = street["zip5"]
        defects = [
            defect for defect, indices in selected.items() if index in indices
        ]
        if "street_typo" in defects:
            raw_line1 = f"{number} {_mutate_one_character(street['street_name'], rng)}"
        if "city_typo" in defects:
            raw_city = _mutate_one_character(street["city"], rng)
        if "missing_zip" in defects:
            raw_zip = ""
        if "wrong_zip" in defects:
            choices = other_metro_zips[street["metro"]]
            if not choices:
                raise ValueError(f"No alternate-metro ZIP is available for {street['metro']}.")
            raw_zip = rng.choice(choices)
        if "missing_house_number" in defects:
            raw_line1 = street["street_name"]
        if "missing_unit" in defects:
            raw_line2 = ""
        if "nonexistent_address" in defects:
            raw_line1 = f"{nonexistent_numbers[index]} {street['street_name']}"

        policy_id = f"POL-{index + 1:06d}"
        effective_date = faker.date_between_dates(
            date_start=today - timedelta(days=365 * 3),
            date_end=today,
        ).isoformat()
        policy_rows.append(
            {
                "policy_id": policy_id,
                "line_of_business": rng.choices(
                    ("Homeowners", "Commercial Property"), weights=(80, 20), k=1
                )[0],
                "effective_date": effective_date,
                "insured_value_band": rng.choice(
                    ("<250k", "250-500k", "500k-1M", ">1M")
                ),
                "metro": street["metro"],
                "street_id": street["street_id"],
                "raw_address_line1": raw_line1,
                "raw_address_line2": raw_line2,
                "raw_city": raw_city,
                "raw_state": street["state"],
                "raw_zip5": raw_zip,
            }
        )
        truth_rows.append(
            {
                "policy_id": policy_id,
                "clean_address_line1": clean_line1,
                "clean_address_line2": clean_line2,
                "clean_city": street["city"],
                "clean_state": street["state"],
                "clean_zip5": street["zip5"],
                "street_id": street["street_id"],
                "defects": ";".join(defects),
                "defect_count": str(len(defects)),
                "expected_outcome": _expected_outcome(defects),
            }
        )

    _write_csv(policyholders_path, POLICY_COLUMNS, policy_rows)
    _write_csv(ground_truth_path, GROUND_TRUTH_COLUMNS, truth_rows)
    defect_counts = {
        defect: sum(defect in row["defects"].split(";") for row in truth_rows)
        for defect in DEFECT_TARGETS
    }
    unique_dirty_count = sum(bool(row["defects"]) for row in truth_rows)
    actual_overlap_count = sum(int(row["defect_count"]) > 1 for row in truth_rows)
    return GenerationSummary(
        n,
        seed,
        defect_counts,
        unique_dirty_count,
        actual_overlap_count,
    )


def _write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
