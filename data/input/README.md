# Synthetic input and ground truth

The default dataset has 200 records and seed `20261029`. Generation uses the fixed effective-date reference date `2026-10-29`, so the same record count and seed produce byte-identical files. Policy IDs are synthetic; no person names are generated. Records are distributed as evenly as possible across reference streets whose validation status starts with `accepted`. Each street's house numbers are generated uniquely within its approved range. About 15% have a synthetic unit; policy line of business is approximately 80% Homeowners and 20% Commercial Property.

`policyholders_synthetic.csv` is the API input and contains no defect labels. `ground_truth.csv` contains the separate clean address, defect labels, counts, and expected outcome. Defect targets apply to all records: street typo 10%, city typo 6%, missing ZIP 8%, wrong ZIP from a different metro 5%, missing house number 5%, missing unit 5% (selected only among records with a clean unit), and nonexistent address 4%. Exactly 10 records have two defects: two each of street typo + missing ZIP, city typo + wrong ZIP, street typo + city typo, city typo + missing ZIP, and street typo + missing unit. Overlap counts are reported separately and defect-category percentages must not be summed to infer unique dirty records.

Expected outcomes follow observed smoke-test behavior: street typo, city typo, and missing ZIP are `correctable`; missing house number is `manual_review`; nonexistent address is `unresolvable`; missing unit is `not_detectable`; and wrong ZIP is `to_be_measured`. For records with multiple defects, ground truth applies the most severe outcome in this order: `unresolvable` > `manual_review` > `to_be_measured` > `not_detectable` > `correctable`.

Default generation actual counts (seed `20261029`, n=200):

| Defect / measure | Target | Actual count | Actual % |
| --- | ---: | ---: | ---: |
| street_typo | 10% | 20 | 10% |
| city_typo | 6% | 12 | 6% |
| missing_zip | 8% | 16 | 8% |
| wrong_zip | 5% | 10 | 5% |
| missing_house_number | 5% | 10 | 5% |
| missing_unit | 5% | 10 | 5% |
| nonexistent_address | 4% | 8 | 4% |
| Unique dirty records | about 38% | 76 | 38% |
| Overlapping records (2+ defects) | about 5% | 10 | 5% |

This is a synthetic evaluation dataset, not a representation of real policyholders or real property addresses. It uses real public street names as anchors and generated house numbers; only validation-accepted streets are used. Unit defects are not detectable without unit-level or Delivery Point Validation (DPV) data. `nonexistent_address` approximates an outdated address; it is not evidence that an address is actually outdated. Defect rates are controlled targets rather than claims about real-world frequency.
