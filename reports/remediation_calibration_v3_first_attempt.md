# Remediation calibration - v3 first attempt

Historical aggregate-only results from the v3 taxonomy and prompt. These are
preserved separately from the v4 report. No addresses, policy identifiers, or
Precisely response values are included.

- Detectable labelled records: 68
- Calibration / test split: 34 / 34
- q-hat: 1.0 (uninformative prediction sets)
- Top-answer accuracy: 52 / 68
- Failed samples: 25

## Top-answer accuracy by v3 true cause

| True cause | Correct | Total | Accuracy |
|---|---:|---:|---:|
| street_name_issue | 12 | 20 | 60.0% |
| city_issue | 12 | 12 | 100.0% |
| zip_issue | 21 | 26 | 80.8% |
| missing_house_number | 6 | 10 | 60.0% |
| address_not_found | 1 | 8 | 12.5% |

`missing_house_number` and `address_not_found` are shown separately here to
preserve the first attempt's taxonomy. In v4 they are consolidated as
`house_number_issue` because the synthetic nonexistent-address defect is an
out-of-range house number.
