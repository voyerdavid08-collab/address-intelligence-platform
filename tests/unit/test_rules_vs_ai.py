import csv
import json

from address_intelligence.remediation import CAUSE_LABELS
from scripts import rules_vs_ai


def _write_csv(path, columns, rows):
    with path.open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _precision(false_components=(), suffix=None):
    chars = list(suffix or "AAAAAAAA")
    for name in false_components:
        chars[{"house_number": 0, "street_name": 2, "city": 5, "zip": 6}[name]] = "-"
    return "S8" + "".join(chars)


def _fixture(tmp_path):
    ids = ("FIX-1", "FIX-2", "FIX-3", "FIX-4", "FIX-5", "FIX-6", "FIX-7", "FIX-8")
    policy_rows = [
        ("12 Oak St", "11111"),
        ("Main St", "22222"),
        ("14 Oak St", ""),
        ("15 Oak St", "44444"),
        ("16 Oak St", "55555"),
        ("17 Oak St", "66666"),
        ("18 Oak St", "77777"),
        ("19 Oak St", "88888"),
    ]
    _write_csv(
        tmp_path / "policies.csv",
        ("policy_id", "raw_address_line1", "raw_address_line2", "raw_city", "raw_state", "raw_zip5"),
        [
            {
                "policy_id": policy_id,
                "raw_address_line1": address,
                "raw_address_line2": "",
                "raw_city": "Sample City",
                "raw_state": "ZZ",
                "raw_zip5": zip5,
            }
            for policy_id, (address, zip5) in zip(ids, policy_rows)
        ],
    )
    precisions = (
        _precision(("house_number",)),
        _precision(("street_name",)),
        _precision(("house_number", "zip")),
        _precision(("street_name",)),
        _precision(),
        "S8",
        _precision(("city",)),
        _precision(("zip",)),
    )
    eligibility = ("eligible",) * len(ids)
    _write_csv(
        tmp_path / "results.csv",
        ("policy_id", "precision_code", "eligibility"),
        [
            {"policy_id": policy_id, "precision_code": precision, "eligibility": eligible}
            for policy_id, precision, eligible in zip(ids, precisions, eligibility)
        ],
    )
    defects = (
        "missing_house_number",
        "street_typo",
        "wrong_zip",
        "missing_house_number",
        "missing_house_number",
        "city_typo",
        "missing_house_number",
        "wrong_zip",
    )
    _write_csv(
        tmp_path / "truth.csv",
        ("policy_id", "defects"),
        [
            {"policy_id": policy_id, "defects": defect}
            for policy_id, defect in zip(ids, defects)
        ],
    )
    diagnoses = [
        {
            "policy_id": policy_id,
            "frequencies": frequencies,
            "prediction_set": [],
            "split": split,
        }
        for policy_id, frequencies, split in zip(
            ids,
            (
                {"zip_issue": 4, "house_number_issue": 4},
                {"street_name_issue": 3},
                {"zip_issue": 3},
                {"house_number_issue": 2},
                {"house_number_issue": 2},
                {"city_issue": 3, "street_name_issue": 3},
                {"house_number_issue": 2},
                {"zip_issue": 2},
            ),
            ("calibration", "test", "calibration", "test", "calibration", "test", "calibration", "test"),
        )
    ]
    diagnosis_path = tmp_path / "diagnosis.jsonl"
    diagnosis_path.write_text(
        "".join(json.dumps(record) + "\n" for record in diagnoses),
        encoding="utf-8",
    )
    actions = [
        {"policy_id": ids[0], "action": "customer_outreach"},
        {"policy_id": ids[1], "action": "analyst_review"},
        {"policy_id": ids[2], "action": "analyst_review"},
        {"policy_id": ids[3], "action": "customer_outreach"},
        {"policy_id": ids[4], "action": "analyst_review"},
    ]
    actions_path = tmp_path / "actions.jsonl"
    actions_path.write_text(
        "".join(json.dumps(record) + "\n" for record in actions),
        encoding="utf-8",
    )
    return (
        diagnosis_path,
        actions_path,
        tmp_path / "results.csv",
        tmp_path / "policies.csv",
        tmp_path / "truth.csv",
        tmp_path / "report.md",
        ids,
    )


def test_fixed_rule_order_covers_all_rule_branches_and_unknown_matches():
    cases = (
        ({"house_number_present": False, "zip_present": True}, ("street_name",), "house_number_issue"),
        ({"house_number_present": True, "zip_present": False}, ("house_number",), "zip_issue"),
        ({"house_number_present": True, "zip_present": True}, ("house_number",), "house_number_issue"),
        ({"house_number_present": True, "zip_present": True}, ("street_name",), "street_name_issue"),
        ({"house_number_present": True, "zip_present": True}, ("city",), "city_issue"),
        ({"house_number_present": True, "zip_present": True}, ("zip",), "zip_issue"),
        ({"house_number_present": True, "zip_present": True}, ("city", "zip"), "ambiguous"),
        ({"house_number_present": True, "zip_present": True}, (), "ambiguous"),
    )
    for features, false_components, expected in cases:
        model_input = {
            "submitted_features": features,
            "match_signals": {
                "component_matches": {
                    **{name: True for name in ("house_number", "street_name", "city", "zip")},
                    **{name: False for name in false_components},
                }
            },
        }
        if not false_components and expected == "ambiguous":
            model_input["match_signals"]["component_matches"] = {
                name: "unknown" for name in ("house_number", "street_name", "city", "zip")
            }
        assert rules_vs_ai._rule_cause(model_input) == expected


def test_ai_tie_break_uses_cause_label_order():
    assert rules_vs_ai._ai_top_answer(
        {"city_issue": 4, "street_name_issue": 4}
    ) == CAUSE_LABELS[0]


def test_report_counts_routing_and_excludes_fixture_identifiers(tmp_path):
    (
        diagnosis_path,
        actions_path,
        results_path,
        input_path,
        truth_path,
        output_path,
        ids,
    ) = _fixture(tmp_path)

    assert rules_vs_ai.main(
        diagnosis_path,
        actions_path,
        results_path,
        input_path,
        truth_path,
        output_path,
    ) == 0

    report = output_path.read_text(encoding="utf-8")
    assert "| All diagnosed records | 8 | 6/8 (75.0%) |" in report
    assert "| Exceptions | 5 | 4/5 (80.0%) |" in report
    assert "| Other diagnosed records | 3 | 2/3 (66.7%) |" in report
    assert "| Rule A | 1 | 1 | 2 |" in report
    assert "| Rule B | 1 | 2 | 2 |" in report
    assert "| AI action | 2 | 0 | 1 |" in report
    assert "| calibration | 1 |" in report
    assert "| test | 0 |" in report
    for policy_id in ids:
        assert policy_id not in report
    assert "Sample City" not in report
    assert "12 Oak St" not in report
