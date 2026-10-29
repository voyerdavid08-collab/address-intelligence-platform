# Address Intelligence Platform

## Purpose

This project is a lightweight, reusable Python foundation for processing address data into decision-ready location context. The initial prototype is designed for a fictional insurance use case and will use synthetic records.

## Setup

1. Create and activate a virtual environment: `python -m venv .venv` then `.venv\Scripts\Activate.ps1` in PowerShell.
2. Install the project and development test dependency: `pip install -e ".[dev]"`.
3. Copy `.env.example` to `.env` and add credentials only if needed for later API work.

## Run tests

Run `pytest`.

## Check config

Run `python main.py --check-config`. This prints masked settings and local credit usage without making network calls.

## API integration

Coming soon.

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

## AI provider choice and data handling

Groq was selected for the brief's free-tier requirement (free plan, $0). Its Services Agreement §4.2 prohibits training on inputs and outputs, and organization-wide Zero Data Retention is enabled in the Groq console; without that setting, Groq states inputs and outputs may be stored for up to 30 days. The selected model, `openai/gpt-oss-120b`, is open-weight and hosted by Groq; requests are sent to Groq, not OpenAI. Only the derived allowlisted fields needed for the narrative are sent—never addresses, coordinates, ZIP codes, PreciselyIDs, water-body names, or policy IDs. Row-level Precisely outputs remain private and temporary, consistent with the Precisely APIs Acceptable Use Policy §4.1–4.2. Gemini API's free tier was excluded because prompts may be used to improve products, human review is involved, and its terms advise against confidential data. Hugging Face was excluded because its approximately $0.10/month credit and provider-dependent terms do not meet the use case; running a local model is impractical on the available hardware. Production use would require an enterprise agreement with a data processing addendum.

## Outputs

Coming soon.
