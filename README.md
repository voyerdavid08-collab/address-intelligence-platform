# Address Intelligence Platform

A reusable Python pipeline and local app that verifies, standardises and enriches insurance policy addresses with Precisely APIs, then adds grounded AI summaries and AI-assisted exception handling. It is demonstrated on 200 synthetic policy records for a fictional national insurance carrier.

The pipeline runs in separate stages, each dry-run first, so every paid call is planned and approved before it is made. A per-client profile controls the input file, column mapping, enrichment items and AI switches, so a new client needs a new profile and their file, not new code.

## Business impact summary

- [One-page summary (PDF)](docs/BUSINESS_IMPACT.pdf)
- [Text version (Markdown)](docs/BUSINESS_IMPACT.md)
- [Source (HTML)](docs/BUSINESS_IMPACT.html)

## Results at a glance

Aggregates only, from the 200-record synthetic run. Full tables: [reports/before_after_summary.md](reports/before_after_summary.md) and [reports/before_after_workbook.xlsx](reports/before_after_workbook.xlsx).

| Measure | Result |
| --- | --- |
| Correct building | 138/200 before, 169/200 after |
| Flawed records fully corrected (including unit) | 6/76 before, 37/76 after |
| Flawed records with the right building | 14/76 before, 45/76 after (+1 where Precisely's ZIP differs from the synthetic reference) |
| Verified to building level and enriched | 170/200; 30 routed to manual review |
| Coarse vs verified location changes the flood-risk tier | 14/39 (6 understated, 8 overstated) |
| AI exception handling: top-answer accuracy | 62/68 (91.2%) |
| AI exception handling: conformal test coverage | 32/34 (94.1%) at a 90% target |
| AI exception handling: customer outreach routing | 14/14 correct |
| AI fixes confirmed by Precisely | 6/6 correct, 0 incorrect |
| AI narratives | 169 generated, 1 rejected by guardrail, 30 templates |
| Human review of 10 narratives | evidence-supported 10/10 |

## How it works

- **Client profile**: one YAML file defines the input, column mapping and switches.
- **Verification**: Geocode Premium verifies and standardises each address.
- **Enrichment**: flood, property and PreciselyID data are requested only for records verified at precision S5, S7 or S8.
- **Comparison**: a coarse location is compared with the verified one to show where it changes the flood-risk tier.
- **AI narratives**: a short underwriting-support summary built from allowlisted derived fields only.
- **AI exception handling**: diagnoses why a record failed, then follows "AI proposes, Precisely verifies": a suggested fix is applied only if Precisely confirms it.
- **Reports**: aggregate reports and a workbook.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design.

## Setup

1. Requires Python 3.11 or later.
2. Create and activate a virtual environment: `python -m venv .venv`, then `.venv\Scripts\Activate.ps1` in PowerShell.
3. Install the project with development and app dependencies: `pip install -e ".[dev,app]"`.
4. Copy `.env.example` to `.env` and add your own values. Never commit `.env`.
5. Check the configuration without any network call: `.venv\Scripts\python main.py --check-config` (prints masked settings and local credit usage).

| Key | Purpose | Required for |
| --- | --- | --- |
| `PRECISELY_API_KEY` | Precisely API key | Any paid pipeline stage |
| `PRECISELY_API_SECRET` | Precisely API secret | Any paid pipeline stage |
| `PRECISELY_BASE_URL` | Precisely API base URL | Any paid pipeline stage |
| `GROQ_API_KEY` | Groq API key | Narratives and exception diagnosis |
| `GROQ_MODEL` | Model for narratives | Narratives |
| `REMEDIATION_MODEL` | Model for exception cause diagnosis | Exception diagnosis |
| `GROQ_DAILY_TOKEN_BUDGET` | Maximum Groq tokens per UTC day | Narratives and exception diagnosis |
| `REMEDIATION_SAMPLES` | Optional; classifications sampled per record. Overrides the profile when set | Exception diagnosis |
| `CONFORMAL_ALPHA` | Optional; miscoverage target for conformal calibration. Overrides the profile when set | Exception diagnosis |
| `CREDIT_HARD_CAP` | Maximum cumulative credit spend across runs | Any paid pipeline stage |
| `TRIAL_CREDITS` | Total credits available in the account | Credit reporting and budget checks |
| `LOG_LEVEL` | Standard-library logging level | Optional |

## Configuration: client profiles

`config/client_profile.yaml` controls the input file and column mapping, eligibility codes, enrichment items, comparison, AI switches, output folders and data handling. Every script accepts `--profile <path>`. Secrets are rejected in profiles; keep them in `.env`. Prices live in `credits.py` (a verified rate table).

Onboarding a new client means a new profile plus their file. The optional "metro" column may be omitted.

- Real client files and generated profiles go in `data/clients/<folder>/` (git-ignored). Never put them in `data/input/`, which is committed and synthetic only.
- Each client writes to its own `data/output/<folder>/` (always git-ignored).
- An environment variable (`REMEDIATION_SAMPLES`, `CONFORMAL_ALPHA`) overrides the profile setting.

## Run order

All commands use `.venv\Scripts\python` and accept `--profile <path>`.

1. `scripts/run_pipeline.py --stage geocode`
2. `scripts/run_pipeline.py --stage preciselyid`
3. `scripts/run_pipeline.py --stage enrich`
4. `scripts/run_pipeline.py --stage compare`
5. `scripts/generate_narratives.py` (`--dry-run` first, then `--execute`)
6. `scripts/evaluate_narratives.py`
7. `scripts/diagnose_exceptions.py`
8. `scripts/remediate_exceptions.py`
9. `scripts/metrics_report.py` (`--dashboard-credits-left <value>` prints the dashboard figure)
10. `scripts/build_workbook.py` (`--public-only` works on a fresh clone without results)
11. `scripts/estimate_cost.py`

The pipeline stages (1-4) are a dry run by default: they print the planned calls and credits. Add `--execute` and type YES to spend. A budget gate and a hard cap stop overspend.

## Cost estimate

`scripts/estimate_cost.py` prices a book of policies. Average credits per policy = 1 + verified share x 8.01 with all enrichment items on. For 300,000 policies at 100% verified this is about 2,703,000 credits, or about $27,030 at the published $0.01 per credit. This is an indicative list price: enterprise pricing, risk-use fees and integration are excluded.

## Local app

```
.venv\Scripts\streamlit run app/streamlit_app.py
```

Open http://localhost:8501. The app is local only and must not be deployed.

- **Start here**: purpose, data safety and a 60-second walkthrough.
- **1 Onboard**: upload a CSV or Excel file, match columns, save the settings file.
- **2 Pre-flight**: free checks showing which issues Precisely usually fixes, which need client follow-up, and which need review.
- **3 Cost & plan**: the estimate and the run commands.
- **4 Client results**: a summary for leadership and a work list for operations.

Made-up examples are safe to share; real results stay on this computer. The app never calls Precisely or the AI. Paid runs stay on the command line behind `--execute` for three reasons: budget control, human approval for anything that spends money, and keeping API keys out of web pages.

## Outputs

`reports/` (aggregates, committed):

- `before_after_summary.md` and `.csv`
- `before_after_workbook.xlsx`: the public version, with the full synthetic input and summary, and made-up example rows in Validated and Enriched
- `ai_narrative_summary.*`, `remediation_calibration.*`, `remediation_actions.*`, `baseline_summary.*`

`data/output/<client>/` (git-ignored):

- `results_rowlevel.csv`
- `before_after_workbook_PRIVATE.xlsx`: full records, shared privately
- narratives and caches

## Testing

Run `pytest`. The last run passed 345 tests. Unit tests use temporary folders and a temporary credit ledger.

### Live end-to-end test of a new client

A 6-record Excel file with different column names was onboarded in the app and run live:

- 5 records verified to the building, including one missing ZIP that Precisely filled in; 1 routed to manual review.
- PreciselyID returned for the 2 records matched to an exact address point (S8).
- Flood data for 5 of 5.
- AI narratives: 5 generated plus 1 template.
- The AI exception steps reported "not available" cleanly, because there is no labelled sample.
- 42.02 credits used. The test data was deleted afterwards.

## Privacy by design

The synthetic dataset contains no person names and uses policy IDs only, following data minimization. Only the needed address fields are sent to Precisely; only the derived fields in the AI allowlist are sent to Groq. In production, personal data stays inside the carrier and records are linked by policy ID. If re-identification is ever needed, use keyed tokenization (HMAC with a secret key or a token vault), not plain hashing: hashes of names can be reversed by dictionary attack and remain personal data.

## Data licensing

Row-level Precisely API outputs (coordinates, flood zones, property attributes, and PreciselyIDs) are not published here, per the Precisely APIs Acceptable Use Policy (April 2021) §4.1 (no public display) and §4.2 (temporary storage only, maximum 30 days). The repository contains code, synthetic input, aggregate before/after metrics, and an output schema with clearly labelled made-up example rows; row-level output is shared privately with the review team.

Example output schema (MADE-UP EXAMPLE ONLY; not API data):

| policy_id | standardized_address | verification_status | precision_code | flood_zone | built_year |
| --- | --- | --- | --- | --- | --- |
| POL-EXAMPLE | 100 Example Rd, Sample City, TX 00000 | MADE-UP | MADE-UP | MADE-UP | MADE-UP |

## Synthetic data

Synthetic policy records use generated house numbers on real public street anchors that passed address validation, with generated defects kept separate as ground truth. See [data/input/README.md](data/input/README.md) for the seed, distribution, defect rates, outcomes, and limitations.

Faker was considered for addresses but generates fictional streets that cannot be verified or enriched; real street anchors (validated with Geocode Premium) with generated house numbers were used instead. Faker is used for non-address fields.

## AI narrative

The optional narrative stage turns a small, allowlisted set of derived location and policy fields into a short underwriting-support summary. It is not a decision engine: a human underwriter remains responsible for review, and the output makes no pricing, premium, eligibility, coverage, or claims decisions and does not predict future flooding. Manual-review records receive a deterministic remediation template without a model call; records without both flood-zone and flood-risk evidence abstain. Model responses are checked for valid JSON, supported evidence-field references, grounded numbers, length, and prohibited decision language. Aggregate status and guardrail counts are evaluated in `reports/ai_narrative_summary.md`; a seeded local review sheet is written under ignored `data/output/run/`.

## AI exception handling

- **Layer 1, diagnosis**: each exception is classified 5 times by the model. Split-conformal prediction, calibrated on labelled examples, turns these into a set of likely causes and routes the record to customer outreach or analyst review.
- **Layer 2, "AI proposes, Precisely verifies"**: a suggested fix is applied only if Geocode Premium confirms it. House numbers are never changed.

For a new client, Layer 1 needs a labelled calibration sample of the client's own exceptions (about 30-70; the reference run used 68). Until then it reports "not available" and exceptions go to the analyst work list. Layer 2's safety comes from Precisely verification, not from labels. Aggregates: [reports/remediation_calibration.md](reports/remediation_calibration.md) and [reports/remediation_actions.md](reports/remediation_actions.md).

## AI provider choice and data handling

Groq was selected for the brief's free-tier requirement (free plan, $0). Its Services Agreement §4.2 prohibits training on inputs and outputs, and organization-wide Zero Data Retention is enabled in the Groq console; without that setting, Groq states inputs and outputs may be stored for up to 30 days. The selected model, `openai/gpt-oss-120b`, is open-weight and hosted by Groq; requests are sent to Groq, not OpenAI. Only the derived allowlisted fields needed for the narrative are sent—never addresses, coordinates, ZIP codes, PreciselyIDs, water-body names, or policy IDs. Row-level Precisely outputs remain private and temporary, consistent with the Precisely APIs Acceptable Use Policy §4.1–4.2. Gemini API's free tier was excluded because prompts may be used to improve products, human review is involved, and its terms advise against confidential data. Hugging Face was excluded because its approximately $0.10/month credit and provider-dependent terms do not meet the use case; running a local model is impractical on the available hardware. Production use would require an enterprise agreement with a data processing addendum.

## Credit ledger

`data/state/credit_ledger.json` (git-ignored) records every billed or zero-credit call. It is account-wide across clients. From 1 November 2026, successful calls that return no result are recorded as "no_result" (not billed). Older zero-credit "failure" rows include such empty results and are kept unchanged for audit integrity.

## Limitations

- Synthetic data with a 38% defect rate by design; 200 records, one run.
- The comparison stage always lists one pending call for a record whose coarse geocode returned no point; it is not re-run.
- Conformal calibration needs labels per client.
- Parallel runs for several clients are not tested.
- The credit ledger is account-wide, not per client.

## Roadmap

### Now (built)

- Verification and standardisation.
- Enrichment: flood zone, risk, base flood elevation, distance to flood hazard, year built, PreciselyID.
- Coarse-vs-verified risk comparison.
- AI risk narratives with guardrails.
- AI exception diagnosis with conformal prediction and "AI proposes, Precisely verifies".
- Client profiles and per-client folders.
- The local onboarding app.
- A live end-to-end test of a new client.

### Next

- Verified AI fix suggestions for new clients before calibration (safe because Precisely re-verifies each one; all routed to an analyst).
- A client labelling step to switch on calibrated confidence and automatic routing.
- Per-client credit attribution in the ledger.

### Later

- A "Run" button in the app behind role-based approval and a budget check, with keys in a vault.
- Deployment in the client's own environment.
- Performance testing at full-book volume.
- An enterprise agreement and data processing addendum with the AI provider.

## Deployment note

The onboarding, pre-flight and cost tabs are pre-sales tools for a Solutions Architect. In production, the Client results view (summary for leadership, work list for operations) and the paid pipeline run in the client's own environment, behind approval, with API keys in a secrets vault.
