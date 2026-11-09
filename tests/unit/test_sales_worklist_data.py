import csv
from copy import deepcopy

import pytest

from address_intelligence.app_data import (
    clean_worklist_text,
    highlight_changed_words,
    join_sales_worklist_addresses,
    load_exception_queue,
    sales_worklist_count_line,
    sales_worklist_counts,
    sort_sales_worklist,
)


MAPPING = {
    "policy_id": "Policy",
    "address_line1": "Street",
    "address_line2": "Unit",
    "city": "Town",
    "state": "ST",
    "zip5": "Postal",
}


def test_sort_groups_and_counts_preserve_input_and_policy_ids():
    rows = [
        {"policy_id": "001", "next_action": "customer_outreach", "verification_status": "verified by Precisely"},
        {"policy_id": "010", "next_action": "analyst_review", "verification_status": "not_started"},
        {"policy_id": "003", "next_action": "analyst_review", "verification_status": " VERIFIED BY PRECISELY "},
        {"policy_id": "002", "next_action": "analyst_review", "verification_status": "verified correct"},
        {"policy_id": "009", "next_action": "analyst_review", "verification_status": "verified by Precisely", "verification": {"verified_incorrect": True}},
        {"policy_id": "008", "next_action": "analyst_review", "verification_status": "verified by Precisely", "suggestion_status": "rejected"},
        {"policy_id": "004", "next_action": None},
        {"policy_id": "000", "next_action": " CUSTOMER_OUTREACH "},
        {"policy_id": "005", "next_action": "analyst_review", "verification": {"verified_correct": True}},
    ]
    before = deepcopy(rows)
    ordered = sort_sales_worklist(iter(rows))
    assert [row["policy_id"] for row in ordered] == [
        "002", "003", "005", "008", "009", "010", "000", "001", "004",
    ]
    assert rows == before
    assert ordered[0] is not rows[3]
    assert sales_worklist_counts(iter(rows)) == {"total": 9, "analyst": 6, "outreach": 2, "unclassified": 1}
    assert sales_worklist_count_line(rows) == "9 addresses need a person: 6 for your analysts, 2 for customer outreach, 1 with no recognised next step."
    assert sales_worklist_counts([]) == {"total": 0, "analyst": 0, "outreach": 0, "unclassified": 0}
    assert sales_worklist_count_line([]) == "0 addresses need a person: 0 for your analysts, 0 for customer outreach."


def test_sort_policy_id_text_and_contradictory_status():
    rows = [
        {"policy_id": "b", "next_action": "analyst_review", "verification": {"verified_correct": True}, "verification_status": "did not verify"},
        {"policy_id": "a", "next_action": "analyst_review", "verification_status": "confirmed"},
        {"policy_id": "A", "next_action": "analyst_review", "verification_status": "confirmed"},
        {"policy_id": "01", "next_action": "analyst_review", "verification_status": "confirmed"},
    ]
    assert [r["policy_id"] for r in sort_sales_worklist(rows)] == ["01", "A", "a", "b"]


def test_join_mapped_csv_text_preserves_spaces_and_leading_zeroes(tmp_path):
    source = tmp_path / "fake.csv"
    source.write_text(
        "Policy,Street,Unit,Town,ST,Postal\n"
        '0007,"  1141 Napoheon Ave  ",Apt 03,NEW Orleans,LA,00123\n'
        "7,Other,,Town,TX,00001\n",
        encoding="utf-8-sig",
    )
    with source.open(encoding="utf-8-sig", newline="") as stream:
        input_rows = list(csv.DictReader(stream))
    queue = [{"policy_id": "0007"}, {"policy_id": "7"}, {"policy_id": "missing"}, {"policy_id": ""}]
    before = deepcopy((queue, input_rows))
    joined = join_sales_worklist_addresses(queue, input_rows, MAPPING)
    assert joined[0]["input_address"] == {
        "address_line1": "  1141 Napoheon Ave  ",
        "address_line2": "Apt 03",
        "city": "NEW Orleans",
        "state": "LA",
        "zip5": "00123",
    }
    assert joined[1]["input_address"]["zip5"] == "00001"
    assert all(not value for value in joined[2]["input_address"].values())
    assert all(not value for value in joined[3]["input_address"].values())
    assert (queue, input_rows) == before


def test_join_null_mapping_missing_values_duplicates_and_empty_ids():
    mapping = {**MAPPING, "address_line2": None}
    result = join_sales_worklist_addresses(
        [{"policy_id": "1"}, {"policy_id": ""}, {}],
        [{"Policy": "", "Street": "not a match"}, {"Policy": "1", "Street": None}],
        mapping,
    )
    assert all(not value for row in result for value in row["input_address"].values())
    with pytest.raises(ValueError, match="Duplicate"):
        join_sales_worklist_addresses([], [{"Policy": "1"}, {"Policy": "1"}], mapping)
    with pytest.raises(ValueError, match="policy_id"):
        join_sales_worklist_addresses([], [], {})


def test_demo_never_accesses_client_input_and_supports_fake_fields():
    def forbidden():
        raise AssertionError("demo must not consume input rows")
        yield

    queue = [
        {"policy_id": "EXAMPLE-1", "address_line1": "Fake St", "raw_city": "Sample", "zip5": "00200"},
        {"policy_id": "EXAMPLE-2"},
    ]
    joined = join_sales_worklist_addresses(queue, forbidden(), {}, demo=True)
    assert joined[0]["input_address"] == {
        "address_line1": "Fake St", "address_line2": "", "city": "Sample",
        "state": "", "zip5": "00200",
    }
    assert all(not value for value in joined[1]["input_address"].values())


def test_load_demo_then_join_does_not_read_real_run(tmp_path, monkeypatch):
    demo = tmp_path / "examples.json"
    demo.write_text('[{"policy_id":"EXAMPLE-1","city":"Imaginary"}]', encoding="utf-8")
    from address_intelligence import app_data

    def forbidden(*args, **kwargs):
        raise AssertionError("real run data accessed")

    monkeypatch.setattr(app_data, "_read_csv", forbidden)
    monkeypatch.setattr(app_data, "_read_jsonl", forbidden)
    queue, is_demo = load_exception_queue(tmp_path / "not-a-run", demo=True, demo_path=demo)
    assert is_demo
    assert join_sales_worklist_addresses(queue, [], {}, demo=True)[0]["input_address"]["city"] == "Imaginary"


def test_highlight_word_difference_preserves_text_and_escapes_html():
    assert highlight_changed_words("1141 Napoleon Ave", "1141 Napoheon Ave") == "1141 <mark>Napoleon</mark> Ave"
    assert highlight_changed_words("AVE, 1141 napoleon!", "1141 NAPOLEON Ave") == "AVE, 1141 napoleon!"
    assert highlight_changed_words("<script> & 'x'", "") == (
        "&lt;<mark>script</mark>&gt; &amp; &#x27;<mark>x</mark>&#x27;"
    )
    assert highlight_changed_words("A A", "a") == "A <mark>A</mark>"
    assert highlight_changed_words("", "anything") == ""


def test_clean_text_exact_phrase_replacements_and_snake_case():
    replacements = {"street-level only": "Street match", "street-level": "short"}
    assert clean_worklist_text("street-level only; house_number_issue; Other", replacements) == (
        "Street match; house number issue; Other"
    )
    assert clean_worklist_text("Street-level only, street-level", replacements) == "Street-level only, short"
    assert clean_worklist_text("one_two THREE_FOUR mixedCase") == "one two THREE FOUR mixedCase"
    assert clean_worklist_text("x", {"x": "y", "y": "z"}) == "y"
    with pytest.raises(ValueError, match="empty"):
        clean_worklist_text("x", {"": "bad"})
