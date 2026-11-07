from decimal import Decimal

import pytest

from address_intelligence.app_data import (
    cost_headline,
    defect_chart_rows,
    onboard_headline,
    preflight_headline,
    results_headline,
    work_path,
)


@pytest.mark.parametrize(
    ("clean", "total", "expected"),
    [
        (174, 200, "174 of 200 addresses look clean. 26 show an issue."),
        (5, 8, "5 of 8 addresses look clean. 3 show an issue."),
        (4, 4, "All 4 addresses look clean."),
        (3, 4, "3 of 4 addresses look clean. 1 shows an issue."),
        (1, 1, "All 1 address looks clean."),
        (0, 1, "0 of 1 address looks clean. 1 shows an issue."),
        (0, 0, "No addresses to check."),
        (None, 4, "No pre-flight data available."),
        (5, 4, "No pre-flight data available."),
    ],
)
def test_preflight_headline(clean, total, expected):
    assert preflight_headline(clean, total) == expected


@pytest.mark.parametrize(
    ("before", "after", "eligible", "reference", "expected"),
    [
        ("138/200 (69.0%)", "169/200 (84.5%)", None, True, "169 of 200 addresses are right, up from 138."),
        ("1/5 (20.0%)", "4/5 (80.0%)", None, True, "4 of 5 addresses are right, up from 1."),
        ("4/5", "4/5", None, True, "4 of 5 addresses are right."),
        ("4/5", "2/5", None, True, "2 of 5 addresses are right."),
        ("1/2", "2/5", None, True, "2 of 5 addresses are right."),
        (None, "0/1", None, True, "0 of 1 address is right."),
        ("0/1", "1/1", None, True, "1 of 1 address is right, up from 0."),
        ("1/1", "1/1", "0/1", False, "0 of 1 address was located automatically."),
        (None, None, "8/10 (80.0%)", False, "8 of 10 addresses were located automatically."),
        (None, "0/0 (0.0%)", None, True, "No results data available."),
        (None, "n/a", None, True, "No results data available."),
        ("1/1", "1/1", None, False, "No results data available."),
        (None, "2/1", None, True, "No results data available."),
    ],
)
def test_results_headline(before, after, eligible, reference, expected):
    assert results_headline(before, after, eligible, reference) == expected


@pytest.mark.parametrize(
    ("before", "after", "eligible", "reference", "expected"),
    [
        ("5e+05/2e+06 (25.0%)", "1e+06/2e+06 (50.0%)", None, True,
         "1,000,000 of 2,000,000 addresses are right, up from 500,000."),
        ("1,000,000/2,000,000", "1.5E+06/2,000,000 (75.0%)", None, True,
         "1,500,000 of 2,000,000 addresses are right, up from 1,000,000."),
        (None, None, "1e+06/2e+06 (50.0%)", False,
         "1,000,000 of 2,000,000 addresses were located automatically."),
        (None, "1.2/2", None, True, "No results data available."),
        (None, "1,00/2,000", None, True, "No results data available."),
        (None, "1e9999999999999999999/2e9999999999999999999", None, True,
         "No results data available."),
        (None, "0/0e+06", None, True, "No results data available."),
    ],
)
def test_results_headline_with_large_or_invalid_fractions(before, after, eligible, reference, expected):
    assert results_headline(before, after, eligible, reference) == expected


def test_defect_chart_rows_supports_scientific_notation_and_grouping():
    kpis = {"defects": [
        {"defect": "street_typo", "before": "5e+05/2e+06 (25.0%)", "after": "1e+06/2e+06 (50.0%)"},
        {"defect": "city_typo", "before": "1,000/2,000 (50.0%)", "after": "1.5e+03/2e+03 (75.0%)"},
        {"defect": "missing_zip", "before": "invalid", "after": "1/2"},
        {"defect": "wrong_zip", "before": "0/0", "after": "0/0"},
    ]}
    assert defect_chart_rows(kpis) == [
        {"Defect": "City typo", "Before": 1000, "After": 1500},
        {"Defect": "Street typo", "Before": 500000, "After": 1000000},
        {"Defect": "Wrong ZIP", "Before": 0, "After": 0},
    ]


@pytest.mark.parametrize(
    ("credits", "policies", "price", "expected"),
    [
        (Decimal("2703000"), 300000, Decimal("27030"), "About 2,703,000 credits for 300,000 policies: $27,030 at list price."),
        (Decimal("40.4"), 7, Decimal("0.404"), "About 40 credits for 7 policies: $0.40 at list price."),
        (1, 1, 0.01, "About 1 credit for 1 policy: $0.01 at list price."),
        (0.6, 1, 0.006, "About 1 credit for 1 policy: $0.01 at list price."),
        (0, 0, 0, "About 0 credits for 0 policies: $0.00 at list price."),
        (None, 3, 0.03, "No cost estimate available."),
        (2, None, 0.02, "No cost estimate available."),
        (2, 1, None, "No cost estimate available."),
        (float("nan"), 1, 1, "No cost estimate available."),
    ],
)
def test_cost_headline(credits, policies, price, expected):
    assert cost_headline(credits, policies, price) == expected


@pytest.mark.parametrize(
    ("records", "matched", "required", "expected"),
    [
        (200, 5, 5, "200 records loaded. 5 of 5 required columns matched."),
        (12, 2, 4, "12 records loaded. 2 of 4 required columns matched."),
        (1, 1, 1, "1 record loaded. 1 of 1 required column matched."),
        (0, 0, 1, "0 records loaded. 0 of 1 required column matched."),
        (None, 0, 4, "No file data available."),
        (5, None, 4, "No file data available."),
        (5, 2, None, "No file data available."),
    ],
)
def test_onboard_headline(records, matched, required, expected):
    assert onboard_headline(records, matched, required) == expected


def test_work_path_counts_only_verified_analyst_rows():
    queue = (
        [{"next_action": "analyst_review", "verification_status": "verified by Precisely"}] * 6
        + [{"next_action": "analyst_review", "verification_status": "did not verify"}] * 10
        + [{"next_action": "customer_outreach", "verification_status": "verified by Precisely"}] * 14
    )
    assert work_path(200, queue) == {"total": 200, "queue": 30, "analyst": 16, "remaining": 10}


def test_work_path_for_other_dataset_and_empty_queue():
    queue = [
        {"next_action": "analyst_review", "verification_status": " VERIFIED BY PRECISELY "},
        {"next_action": "analyst_review", "verification_status": "not_started"},
        {"next_action": "customer_outreach"},
        {},
    ]
    assert work_path(12, queue) == {"total": 12, "queue": 4, "analyst": 2, "remaining": 1}
    assert work_path(0, []) == {"total": 0, "queue": 0, "analyst": 0, "remaining": 0}
