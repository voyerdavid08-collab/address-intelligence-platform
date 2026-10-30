# Exception cause diagnosis calibration - v4 dry run

No v4 model calls were made. These are run-planning counts only; q-hat,
accuracy, confusion counts, and coverage remain pending. No address-level
records or Precisely response values are included.

- Taxonomy: `street_name_issue`, `city_issue`, `zip_issue`, and
  `house_number_issue`
- Detectable labelled records: 68
- Diagnosis model: `openai/gpt-oss-20b`
- Planned calibration / test split: 34 / 34
- Missing-unit-only records excluded: 8
- Samples per record: 5
- Planned calls: 340 (0 cached)
- Estimated tokens: 184,760
- Daily budget remaining for `openai/gpt-oss-20b`: 180,000
- Calls fit within this model's daily budget: No (184,760 estimated tokens)

The diagnosis taxonomy groups missing house numbers and the synthetic
nonexistent-address defect as `house_number_issue`: nonexistent addresses in
this dataset use out-of-range house numbers, so both indicate a house number
not found on the street.

The eight missing-unit-only records remain excluded because submitted address
fields and geocoder precision/component signals do not reveal whether a unit
was omitted or mismatched. Historical v3 results are preserved in
`remediation_calibration_v3_first_attempt.md`.
