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

The synthetic dataset contains no person names and uses policy IDs only, following data minimization. Only address fields are sent to external APIs; in production, personal data stays inside the carrier and records are linked by policy ID. If re-identification is ever needed, use keyed tokenization (HMAC with a secret key or a token vault), not plain hashing: hashes of names can be reversed by dictionary attack and remain personal data.

## Data licensing

Row-level Precisely API outputs (coordinates, flood zones, property attributes, and PreciselyIDs) are not published here, per the Precisely APIs Acceptable Use Policy (April 2021) §4.1 (no public display) and §4.2 (temporary storage only, maximum 30 days). The repository contains code, synthetic input, aggregate before/after metrics, and an output schema with clearly labelled made-up example rows; row-level output is shared privately with the review team.

Example output schema (MADE-UP EXAMPLE ONLY; not API data):

| policy_id | standardized_address | verification_status | precision_code | flood_zone | built_year |
| --- | --- | --- | --- | --- | --- |
| POL-EXAMPLE | 100 Example Rd, Sample City, TX 00000 | MADE-UP | MADE-UP | MADE-UP | MADE-UP |

## Synthetic data

Synthetic policy records use generated house numbers on real public street anchors that passed address validation, with generated defects kept separate as ground truth. See [data/input/README.md](data/input/README.md) for the seed, distribution, defect rates, outcomes, and limitations.

Faker was considered for addresses but generates fictional streets that cannot be verified or enriched; real street anchors (validated with Geocode Premium) with generated house numbers were used instead. Faker is used for non-address fields.

## Enrichment and AI

Coming soon.

## Outputs

Coming soon.
