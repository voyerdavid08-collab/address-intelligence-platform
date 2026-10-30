# Exception cause diagnosis calibration

## Dry-run dataset summary

- Detectable labelled records: 68
- Calibration / test split: 34 / 34
- Missing-unit-only records excluded: 8
- Independent samples per record: 5
- Planned model calls: 340
- Estimated tokens: 163,195
- Conformal alpha: 0.10
- Samples failed: not sampled (dry-run only)
- Records with fewer than K valid samples: not sampled (dry-run only)

The eight excluded records have `missing_unit` as their only ground-truth
defect. Submitted address fields and derived geocoder precision/component
signals do not reveal whether a unit was omitted or mismatched.

No model sampling was executed for this report. Therefore q-hat, test coverage,
mean prediction-set size, singleton share, failed-sample count, and records with
fewer than K valid samples are not yet available. The execution workflow writes
those aggregate metrics here without address-level records or Precisely
response values.
