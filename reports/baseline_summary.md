# Pre-API baseline summary

Aggregate metrics from synthetic input and its separate ground truth; no API data.

## Input quality

| Metric | Value |
| --- | ---: |
| Input rows | 200 |
| Missing ZIP | 16 (8.0%) |
| Missing house number | 10 (5.0%) |
| Unit where expected unknown | not measurable pre-API |
| ZIP format valid | 184 (92.0%) |
| State format valid | 200 (100.0%) |
| City-state-ZIP matches reference | 166 (83.0%) |
| Duplicate address records beyond first | 0 (0.0%) |

## Ground-truth defect injection

| Metric | Value |
| --- | ---: |
| street_typo actual (target 10%) | 20 (10.0%) |
| city_typo actual (target 6%) | 12 (6.0%) |
| missing_zip actual (target 8%) | 16 (8.0%) |
| wrong_zip actual (target 5%) | 10 (5.0%) |
| missing_house_number actual (target 5%) | 10 (5.0%) |
| missing_unit actual (target 5%) | 10 (5.0%) |
| nonexistent_address actual (target 4%) | 8 (4.0%) |
| Unique dirty records | 76 (38.0%) |
| Overlapping records (2+ defects) | 10 (5.0%) |

## Defect combinations

| Combination | Count |
| --- | ---: |
| street_typo+missing_zip | 2 |
| city_typo+wrong_zip | 2 |
| street_typo+city_typo | 2 |
| city_typo+missing_zip | 2 |
| street_typo+missing_unit | 2 |
