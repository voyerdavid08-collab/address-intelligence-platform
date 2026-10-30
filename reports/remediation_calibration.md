# Exception cause diagnosis calibration

Aggregate split-conformal evaluation; no address-level records or Precisely response values are included.

The taxonomy maps both missing house numbers and synthetic nonexistent addresses to `house_number_issue`: the synthetic nonexistent case is an out-of-range house number.

- Labelled records: 68
- Calibration / test: 34 / 34
- Missing-unit-only records excluded: 8
- Samples per record: 5
- Samples failed: 6
- Records with fewer than K valid samples: 5
- Top-answer accuracy: 62/68 (0.912)

## Top-answer accuracy by true cause

| True cause | Correct | Total | Accuracy |
|---|---:|---:|---:|
| street_name_issue | 14 | 20 | 0.700 |
| city_issue | 12 | 12 | 1.000 |
| zip_issue | 22 | 26 | 0.846 |
| house_number_issue | 14 | 18 | 0.778 |

## Confusion table (true cause vs most frequent prediction)

| True cause | Predicted cause | Count |
|---|---|---:|
| city_issue | city_issue | 12 |
| house_number_issue | house_number_issue | 14 |
| house_number_issue | street_name_issue | 4 |
| street_name_issue | city_issue | 3 |
| street_name_issue | street_name_issue | 14 |
| street_name_issue | zip_issue | 3 |
| zip_issue | city_issue | 4 |
| zip_issue | zip_issue | 22 |
- Alpha: 0.1
- q-hat: 0.8
- Test set contains all true causes: 0.941
- Test set contains at least one true cause: 1.000
- Mean prediction-set size: 1.382
- Singleton prediction-set share: 0.794
- Uninformative q-hat warning: False

Missing-unit-only records are excluded because the submitted address and geocoder precision/match signals do not reveal whether a unit was omitted or mismatched.
