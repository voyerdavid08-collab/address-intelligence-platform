aggregates only; row-level Precisely outputs are not published (AUP §4.1–4.2)
Before/after counts use the input population as the top-level denominator.

## Data integrity

| Metric | Before | After | Numerator | Denominator | Source |
| --- | ---: | ---: | ---: | ---: | --- |
| Input rows | n/a | 200/200 (100.0%) | 200 | 200 | input |
| Output rows | n/a | 200/200 (100.0%) | 200 | 200 | precisely_api |
| Dropped rows | n/a | 0/200 (0.0%) | 0 | 200 | precisely_api |
| Duplicate identifiers | n/a | 0/200 (0.0%) | 0 | 200 | input |
| compare failures | n/a | 1/40 (2.5%) | 1 | 40 | precisely_api |
| enrich failures | n/a | 0/170 (0.0%) | 0 | 170 | precisely_api |
| geocode failures | n/a | 0/200 (0.0%) | 0 | 200 | precisely_api |
| PreciselyID stage failures | n/a | 0/170 (0.0%) | 0 | 170 | precisely_api |

## Verification

| Metric | Before | After | Numerator | Denominator | Source |
| --- | ---: | ---: | ---: | ---: | --- |
| Eligible | n/a | 170/200 (85.0%) | 170 | 200 | precisely_api |
| Manual review | n/a | 30/200 (15.0%) | 30 | 200 | precisely_api |
| Manual review: location conflict (state mismatch) | n/a | 1/200 (0.5%) | 1 | 200 | precisely_api |
| Manual review: postal or city level | n/a | 11/200 (5.5%) | 11 | 200 | precisely_api |
| Manual review: street-level only | n/a | 18/200 (9.0%) | 18 | 200 | precisely_api |

## Standardization vs ground truth

| Metric | Before | After | Numerator | Denominator | Source |
| --- | ---: | ---: | ---: | ---: | --- |
| Correct building: all records | 138/200 (69.0%) | 169/200 (84.5%) | 169 | 200 | ground_truth |
| Fully correct (incl. unit): all records | 130/200 (65.0%) | 161/200 (80.5%) | 161 | 200 | ground_truth |
| Correct building: clean records | 124/124 (100.0%) | 124/124 (100.0%) | 124 | 124 | ground_truth |
| Fully correct (incl. unit): clean records | 124/124 (100.0%) | 124/124 (100.0%) | 124 | 124 | ground_truth |
| Correct building: dirty records | 14/76 (18.4%) | 45/76 (59.2%) | 45 | 76 | ground_truth |
| Fully correct (incl. unit): dirty records | 6/76 (7.9%) | 37/76 (48.7%) | 37 | 76 | ground_truth |
| Correct building: defect: city_typo | 6/12 (50.0%) | 10/12 (83.3%) | 10 | 12 | ground_truth |
| Fully correct (incl. unit): defect: city_typo | 6/12 (50.0%) | 10/12 (83.3%) | 10 | 12 | ground_truth |
| Correct building: defect: missing_house_number | 0/10 (0.0%) | 0/10 (0.0%) | 0 | 10 | ground_truth |
| Fully correct (incl. unit): defect: missing_house_number | 0/10 (0.0%) | 0/10 (0.0%) | 0 | 10 | ground_truth |
| Correct building: defect: missing_unit | 8/10 (80.0%) | 8/10 (80.0%) | 8 | 10 | ground_truth |
| Fully correct (incl. unit): defect: missing_unit | 0/10 (0.0%) | 0/10 (0.0%) | 0 | 10 | ground_truth |
| Correct building: defect: missing_zip | 0/16 (0.0%) | 14/16 (87.5%) | 14 | 16 | ground_truth |
| Fully correct (incl. unit): defect: missing_zip | 0/16 (0.0%) | 14/16 (87.5%) | 14 | 16 | ground_truth |
| Correct building: defect: nonexistent_address | 0/8 (0.0%) | 0/8 (0.0%) | 0 | 8 | ground_truth |
| Fully correct (incl. unit): defect: nonexistent_address | 0/8 (0.0%) | 0/8 (0.0%) | 0 | 8 | ground_truth |
| Correct building: defect: street_typo | 0/20 (0.0%) | 10/20 (50.0%) | 10 | 20 | ground_truth |
| Fully correct (incl. unit): defect: street_typo | 0/20 (0.0%) | 9/20 (45.0%) | 9 | 20 | ground_truth |
| Correct building: defect: wrong_zip | 0/10 (0.0%) | 9/10 (90.0%) | 9 | 10 | ground_truth |
| Fully correct (incl. unit): defect: wrong_zip | 0/10 (0.0%) | 9/10 (90.0%) | 9 | 10 | ground_truth |
| City name matches reference: all records | 188/200 (94.0%) | 195/200 (97.5%) | 195 | 200 | ground_truth |
| City name matches reference: clean records | 124/124 (100.0%) | 122/124 (98.4%) | 122 | 124 | ground_truth |
| City name matches reference: dirty records | 64/76 (84.2%) | 73/76 (96.1%) | 73 | 76 | ground_truth |
| City name matches reference: defect: city_typo | 0/12 (0.0%) | 10/12 (83.3%) | 10 | 12 | ground_truth |
| Same street address, ZIP differs from reference: all records | 20/200 (10.0%) | 1/200 (0.5%) | 1 | 200 | ground_truth |
| Same street address, ZIP differs from reference: clean records | 0/124 (0.0%) | 0/124 (0.0%) | 0 | 124 | ground_truth |
| Same street address, ZIP differs from reference: dirty records | 20/76 (26.3%) | 1/76 (1.3%) | 1 | 76 | ground_truth |

City is reported separately, not as part of the building test: street + ZIP identify the building, and official place names vary (for 2 clean records Precisely returns the municipality, Coral Gables, where the reference uses the mailing city, Miami).

## Coordinate coverage

| Metric | Before | After | Numerator | Denominator | Source |
| --- | ---: | ---: | ---: | ---: | --- |
| Building-level coordinates (S5/S7/S8; includes 1 record held for state conflict) | n/a | 171/200 (85.5%) | 171 | 200 | precisely_api |
| Street-level coordinates only | n/a | 18/200 (9.0%) | 18 | 200 | precisely_api |
| City/postal-level coordinates only | n/a | 11/200 (5.5%) | 11 | 200 | precisely_api |

## PreciselyID coverage over attempted

| Metric | Before | After | Numerator | Denominator | Source |
| --- | ---: | ---: | ---: | ---: | --- |
| PreciselyID over attempted | n/a | 39/170 (22.9%) | 39 | 170 | precisely_api |

## Enrichment completeness

| Metric | Before | After | Numerator | Denominator | Source |
| --- | ---: | ---: | ---: | ---: | --- |
| flood_zone_code over eligible | n/a | 170/170 (100.0%) | 170 | 170 | precisely_api |
| flood_zone_code over all input | n/a | 170/200 (85.0%) | 170 | 200 | precisely_api |
| flood_risk_level over eligible | n/a | 170/170 (100.0%) | 170 | 170 | precisely_api |
| flood_risk_level over all input | n/a | 170/200 (85.0%) | 170 | 200 | precisely_api |
| bfe over eligible | n/a | 56/170 (32.9%) | 56 | 170 | precisely_api |
| bfe over all input | n/a | 56/200 (28.0%) | 56 | 200 | precisely_api |
| distance_to_water over eligible | n/a | 170/170 (100.0%) | 170 | 170 | precisely_api |
| distance_to_water over all input | n/a | 170/200 (85.0%) | 170 | 200 | precisely_api |
| built_year over eligible | n/a | 13/170 (7.6%) | 13 | 170 | precisely_api |
| built_year over all input | n/a | 13/200 (6.5%) | 13 | 200 | precisely_api |

## Coarse vs verified location (Risk 2)

| Metric | Before | After | Numerator | Denominator | Source |
| --- | ---: | ---: | ---: | ---: | --- |
| Records compared | n/a | 39/200 (19.5%) | 39 | 200 | precisely_api |
| Flood-zone base changed | n/a | 14/39 (35.9%) | 14 | 39 | precisely_api |
| Flood-risk tier changed | n/a | 14/39 (35.9%) | 14 | 39 | precisely_api |
| Coarse location understated verified risk | n/a | 6/39 (15.4%) | 6 | 39 | precisely_api |
| Coarse location overstated verified risk | n/a | 8/39 (20.5%) | 8 | 39 | precisely_api |
| Median comparison distance km | n/a | 1.408 | 1.408 | 39 | precisely_api |

## AI narratives

| Metric | Before | After | Numerator | Denominator | Source |
| --- | ---: | ---: | ---: | ---: | --- |
| generated | n/a | 169/200 (84.5%) | 169 | 200 | ai_report |
| rejected_by_guardrail | n/a | 1/200 (0.5%) | 1 | 200 | ai_report |
| template | n/a | 30/200 (15.0%) | 30 | 200 | ai_report |

## Remediation

| Metric | Before | After | Numerator | Denominator | Source |
| --- | ---: | ---: | ---: | ---: | --- |
| Labelled records | n/a | 68/200 (34.0%) | 68 | 200 | ai_report |
| Test records | n/a | 34/68 (50.0%) | 34 | 68 | ai_report |
| Top-answer accuracy | n/a | 62/68 (91.2%) | 62 | 68 | ai_report |
| Test coverage (all true labels in set; target 90%) | n/a | 32/34 (94.1%) | 32 | 34 | ai_report |
| Mean prediction-set size | n/a | 1.38 | 1.38 | 34 | ai_report |
| Singleton share | n/a | 27/34 (79.4%) | 27 | 34 | ai_report |
| Exceptions | n/a | 30/200 (15.0%) | 30 | 200 | ai_report |
| Customer outreach | n/a | 14/30 (46.7%) | 14 | 30 | ai_report |
| Customer outreach correct | n/a | 14/14 (100.0%) | 14 | 14 | ai_report |
| Analyst review | n/a | 16/30 (53.3%) | 16 | 30 | ai_report |
| Suggestions made | n/a | 10/16 (62.5%) | 10 | 16 | ai_report |
| Suggestions valid | n/a | 9/10 (90.0%) | 9 | 10 | ai_report |
| Precisely-verified | n/a | 6/9 (66.7%) | 6 | 9 | ai_report |
| Verified correct | n/a | 6/6 (100.0%) | 6 | 6 | ai_report |
| Verified incorrect | n/a | 0/6 (0.0%) | 0 | 6 | ai_report |
| Not verified | n/a | 3/9 (33.3%) | 3 | 9 | ai_report |
| No suggestion | n/a | 5/16 (31.2%) | 5 | 16 | ai_report |
| q-hat | n/a | 0.80 | 0.8 |  | ai_report |
| Additional per-cause and confusion metrics: see reports/remediation_calibration.md | n/a | See source report |  |  | ai_report |

## Credits

| Metric | Before | After | Numerator | Denominator | Source |
| --- | ---: | ---: | ---: | ---: | --- |
| Ledger credits used | n/a | 1680.10 | 1680.10 | 2,500 | ledger |
| Ledger credits left | n/a | 819.90 | 819.90 | 2,500 | ledger |
| Dashboard credits left | n/a | 832.50 | 832.5 | 2,500 | ledger |

## Limitations

- Synthetic data; the 38% defect rate is by design.
- Results cover 200 records and one run.
