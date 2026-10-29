# Phase 0 API Discovery Worksheet

Purpose: record verified Precisely trial, API, US coverage, enrichment, and
LLM details before implementation or API calls. Fill this worksheet from
official documentation and the trial account.

**Status:** IN PROGRESS – pricing and docs verified; smoke test pending
**Date checked:** 2026-10-28

Every filled value must include a source link or the note
`observed in account on <date>`. Do not infer undocumented behavior.

## 1. Account and trial

| Item | Value | Source / observation |
|---|---|---|
| Platform | Precisely APIs (`developer.precisely.com`, `api.precisely.com`) | Account, observed 2026-10-28 |
| Trial start date | TBD | TBD |
| Trial expiry date | TBD-owner to record | TBD |
| Credit balance at start | 2,500 credits; approximately 30-day trial | Account, observed 2026-10-28 |
| Credit value | 1 credit = $0.01 | Account, observed 2026-10-28 |
| Credit charging rule | Credits charged only when a call has no errors and returns results | Account, observed 2026-10-28 |
| Where the balance is visible | Portal Credit Usage dashboard; per-API calls, errors, and usage | Account, observed 2026-10-28 |
| Permitted test-data rules | TBD | TBD |
| US coverage confirmed (Y/N/TBD) | TBD; account product/attribute access confirmation pending | TBD |
| Production licensing note | Pricing page footnote: applications making determinations of risk may carry additional fees; commercial licensing note for production, not a trial blocker | Account, observed 2026-10-28 |

## 2. Authentication

| Item | Value | Source |
|---|---|---|
| Method | OAuth2 `client_credentials` (GettingStarted/generating_an_oauth_token.html) | Docs base: https://docs.precisely.com/docs/sftw/precisely-apis/main/en-us/webhelp/apis/ |
| Token request | POST `https://api.precisely.com/oauth/token`; header `Authorization: Basic base64(API_KEY:SECRET)`, `Content-Type: application/x-www-form-urlencoded`; body `grant_type=client_credentials` (GettingStarted/generating_an_oauth_token.html) | Docs base: https://docs.precisely.com/docs/sftw/precisely-apis/main/en-us/webhelp/apis/ |
| Credential type / location | API key and secret from portal “API Keys” (GettingStarted/generating_an_oauth_token.html) | Docs base: https://docs.precisely.com/docs/sftw/precisely-apis/main/en-us/webhelp/apis/ |
| Token lifetime | `expiresIn` approximately 35,999 seconds (GettingStarted/generating_an_oauth_token.html) | Docs base: https://docs.precisely.com/docs/sftw/precisely-apis/main/en-us/webhelp/apis/ |
| Base URL | `https://api.precisely.com` | Account, observed 2026-10-28 |

Encode `API_KEY:SECRET` locally in code; never use an online encoder. Secrets
belong only in `.env`, never in this file.

## 3. Operations and documentation

| Operation | Official doc link | Endpoint (as documented) | Enabled on my account? (Y/N/TBD) | Credits per call (as documented) | Batch supported? | Key response fields | US coverage | Notes | Source |
|---|---|---|---|---|---|---|---|---|---|
| Address verification/standardization: ValidateMailingAddress / Pro / Premium | TBD | TBD | TBD | 1 credit per US address for each; 0.33 per USCAN address for each | TBD | Premium adds parsed elements, per-element match codes, lat/long | US pricing listed; account access TBD | Address Verification evaluated once in smoke test only | Account, observed 2026-10-28 |
| Geocoding forward: Premium | Docs mapping appendix | `/geocode/v1/premium/geocode` | TBD | Premium: 1 credit; Advanced: 0.5; Basic: 0.1. POST is per address. | TBD | `candidates[].precisionCode`; `candidates[].precisionLevel`; suffix `HPNTSCZA` | TBD | GET; Advanced endpoint TBD | Account, observed 2026-10-28 |
| Geocoding forward: Advanced / Basic | TBD | Advanced: TBD; Basic: TBD | TBD | Advanced: 0.5 credit; Basic: 0.1 credit. POST is per address. | TBD | `candidates[].precisionCode`; `candidates[].precisionLevel`; suffix `HPNTSCZA` | TBD | GET | Account, observed 2026-10-28 |
| PreciselyID by address | Geocode/PreciselyID/byPreciselyID/resp_desc.html | `/geocode/v1/key/byaddress` | TBD | 0.01 credit | TBD | `key`, `matchedAddress` | TBD |  | Account, observed 2026-10-28 |
| Key lookup | TBD | TBD | TBD | 0.01 credit | TBD | TBD | TBD |  | Account, observed 2026-10-28 |
| Flood by location: flood zone code, flood risk level, base flood elevation | TBD | `/risks/v1/flood/bylocation?latitude&longitude&includeZoneDesc&includeGeometry` | TBD | 5 credits per call | TBD | `floodZone.code`, `floodZone.areaType`, `floodZone.riskLevel`, `floodZone.primaryZone.code/description`, `floodZone.baseFloodElevation.unit/value`, `floodZone.additionalInfo`, `community.number`, `community.status.code/description` | US only | One flood call supplies the listed candidate attributes | Account, observed 2026-10-28 |
| Distance to flood hazard by location | TBD | TBD | TBD | 2 credits per call | TBD | TBD | US only |  | Account, observed 2026-10-28 |
| Property attributes by address: one named attribute | TBD | `/property/v1/all/attributes/byaddress` | TBD | 1 credit per attribute + 0.01 credit for PBKey | TBD | TBD | TBD | Request named attributes only; verify charging on 1 record before any batch | Account, observed 2026-10-28 |
| Fire station | TBD | TBD | TBD | 6 credits per call | TBD | TBD | TBD | Recorded; not selected | Account, observed 2026-10-28 |
| Fire risk | TBD | TBD | TBD | 9 credits per call | TBD | TBD | TBD | Recorded; not selected | Account, observed 2026-10-28 |
| Crime | TBD | TBD | TBD | 2 credits per call | TBD | TBD | TBD | Recorded; excluded on fairness | Account, observed 2026-10-28 |
| Earthquake | TBD | TBD | TBD | 2 credits per call | TBD | TBD | TBD | Recorded; not selected | Account, observed 2026-10-28 |

### Geocoder behavior on defective input

| Question | Finding | Source |
|---|---|---|
| Does geocoding auto-correct or standardize raw input? | TBD | TBD |
| `candidates[].precisionCode` categories (LI_Geo_responsedesc.html) | S8 house point; S7 interpolated between points; S5 street address; S4 street centroid; SC projected house-level; SX intersection; S0 no coordinates; Z1/Z2/Z3 postal centroid; G1–G4 area centroid; B1/B2 PO box | Docs base: https://docs.precisely.com/docs/sftw/precisely-apis/main/en-us/webhelp/apis/ |
| `candidates[].precisionLevel` (LI_Geo_responsedesc.html) | 0–18; 16 = address point; 3 = postal code centroid; 0 = no coordinates | Docs base: https://docs.precisely.com/docs/sftw/precisely-apis/main/en-us/webhelp/apis/ |
| Match modes (LI_GGM_Geo_Matching.html) | Exact, Standard (default), Relaxed, Custom; USA also Interactive and CASS | Docs base: https://docs.precisely.com/docs/sftw/precisely-apis/main/en-us/webhelp/apis/ |
| `HPNTSCZA` suffix (LI_Geo_responsedesc.html) | Shows which components matched | Docs base: https://docs.precisely.com/docs/sftw/precisely-apis/main/en-us/webhelp/apis/ |
| How is unresolved input reported? (LI_Geo_responsedesc.html) | S0 / `precisionLevel` 0 or no candidates | Docs base: https://docs.precisely.com/docs/sftw/precisely-apis/main/en-us/webhelp/apis/ |
| DPV fields (LI_Geo_responsedesc.html) | Restricted to CASS Certified products; do not rely on them | Docs base: https://docs.precisely.com/docs/sftw/precisely-apis/main/en-us/webhelp/apis/ |

This determines how the raw-vs-verified comparison (PROJECT_PLAN.md Sections
15–16, Risk 2) is defined.

## Phase 0 decisions

- **Owner-approved 2026-10-28** — United States addresses only; `N = 200`
  records.
- **Owner-approved 2026-10-28** — Geocode Premium is the verification/
  standardization and geocoding step for the full run. Address Verification
  is evaluated once in the smoke test only.
- **Owner-approved 2026-10-28** — Enrichment eligibility is precisionCode
  S5, S7, or S8 only.
- **Owner-approved 2026-10-28** — Selected enrichment: flood zone code,
  flood risk level, and base flood elevation from one flood call; distance to
  flood hazard; and one property attribute (TBD after smoke test).
- **Owner-approved 2026-10-28** — Risk APIs are called by location using
  pipeline coordinates.
- **Owner-approved 2026-10-28** — Fire station and fire risk excluded on
  cost/relevance grounds; crime and demographics excluded on fairness grounds.
- **Owner-approved 2026-10-28** — Risk 2 comparison subset is approximately
  40 records. Exact counterfactual method is TBD after smoke test. Budget
  comparison calls at 6 credits per record.
- **Owner-approved 2026-10-28** — Hard credit cap 2,150 (reserve floor ≈14%,
  350 credits) instead of 20%, because failed calls are not charged and
  successful results will be cached. Ledger must be reconciled with the portal
  dashboard before each live run.

## Smoke test plan

Maximum: 5 records. Owner approval is required before any call.

- Compare Premium, Advanced, and Basic `precisionCode` results.
- Compare Address Verification Premium and USCAN output.
- Request ONE named property attribute on ONE record, then check the Credit
  Usage dashboard before continuing.
- Test flood and distance-to-flood-hazard by location.

## 5. Credit budget calculator

Approved budget inputs are below. Use the same unit (credits) throughout.
For selected enrichment, `C_E` is one full enrichment pass per eligible
record. The comparison subset adds a raw-location flood pass in addition to
the normal verified-location pass; the comparison term is
`S × (C_G + C_E_flood)`.

| Symbol | Input | Value |
|---|---|---|
| `B` | Starting available credit balance | 2,500 |
| `C_AV` | Credits per record for Address Verification in full run | 0 |
| `C_G` | Credits per record for Geocode Premium | 1 |
| `C_PID` | Credits per record for PreciselyID retrieval | 0.01 |
| `C_E` | Credits per eligible record for selected enrichment: flood 5 + distance 2 + one property attribute 1 + PBKey 0.01 | 8.01 |
| `C_E_flood` | Credits for flood call in raw-vs-verified comparison | 5 |
| `S` | Raw-vs-verified enrichment comparison subset size | 40 |
| `C_SMOKE` | Smoke-test allowance in credits | 100 |
| `C_RERUN` | Re-run allowance in credits | 0 (cached; failed calls free) |
| `C_RESERVE` | Reserve floor: `B − C_AVAILABLE` | 350 |
| `C_AVAILABLE` | Approved hard credit cap / spendable limit; reserve floor is `B − C_AVAILABLE` | 2,150 |

**Estimated total formula**

For eligibility rate `E`:

`N × (C_AV + C_G + C_PID) + (N × E × C_E) + S × (C_G + C_E_flood) + C_SMOKE + C_RERUN`

| Case | Calculation | Total | Spendable balance | Remaining balance | Resulting reserve (% of `B`) | Within spendable balance? |
|---|---|---:|---:|---:|---:|---|
| Best case: 200 records, 85% eligible (`E = 0.85`) | `200 × (0 + 1 + 0.01) + (200 × 0.85 × 8.01) + 40 × (1 + 5) + 100 + 0` | 1,903.70 | 2,150 | 596.30 | 23.85% | Yes; 246.30 credits below hard cap |
| Worst case: 200 records, 100% eligible (`E = 1.00`) | `200 × (0 + 1 + 0.01) + (200 × 1.00 × 8.01) + 40 × (1 + 5) + 100 + 0` | 2,144.00 | 2,150 | 356.00 | 14.24% | Yes; 6.00 credits below hard cap |

The reserve percentage is remaining balance divided by starting balance.
The approximate reserve floor is 350 credits (14% of the starting balance);
the worst case leaves 356 credits. Reconcile the ledger with the portal
dashboard before each live run.

## 6. Enrichment candidate scoring

Record only verified attributes and documented definitions. Use candidate
themes from `PROJECT_PLAN.md` as a starting point; do not treat them as
available products or fields until confirmed.

| Attribute | Business decision it supports | Supports Risk 1 or Risk 2? | Available? | Definition/units | Fairness concern (Y/N) | Selected? |
|---|---|---|---|---|---|---|
| Flood zone code | Compare hazard category at raw versus verified location for Risk 2 | Risk 2 | TBD | TBD | TBD | Yes |
| Flood risk level | Support location-risk review for Risk 2 | Risk 2 | TBD | TBD | TBD | Yes |
| Base flood elevation | Support location-risk review for Risk 2 | Risk 2 | TBD | TBD | TBD | Yes |
| Distance to flood hazard | Support location-risk review for Risk 2 | Risk 2 | TBD | TBD | TBD | Yes |
| One property attribute | TBD | TBD | TBD | TBD | TBD | TBD after smoke test |

## 7. LLM option check

| Option | Free-tier access | Data-retention terms link | Laptop feasibility | Decision |
|---|---|---|---|---|
| Gemini | TBD | TBD | TBD | TBD |
| Groq | TBD | TBD | TBD | TBD |
| Hugging Face | TBD | TBD | TBD | TBD |
| Ollama | TBD | TBD | TBD | TBD |

## 8. Open questions and blockers

| Question or blocker | Owner / next action | Status | Source / notes |
|---|---|---|---|
| TBD | TBD | TBD | TBD |
| TBD | TBD | TBD | TBD |
| TBD | TBD | TBD | TBD |

## 9. Phase 0 exit criteria

- [ ] API verification checklist completed or blockers recorded.
- [ ] No speculative API calls.
- [ ] Visibility approval remains separate.
