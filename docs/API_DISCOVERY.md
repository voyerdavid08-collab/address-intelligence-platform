# Phase 0 API Discovery Worksheet

Purpose: record verified Precisely trial, API, US coverage, enrichment, and
LLM details before implementation or API calls. Fill this worksheet from
official documentation and the trial account.

**Status:** Phase 0 COMPLETE (smoke test 2026-10-28)
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
| Credit charging rule | True errors are free; a not-found answer returned as Status F is a result and was charged | Account and smoke test, observed 2026-10-28 |
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
| Address verification/standardization: ValidateMailingAddress / Pro / Premium | TBD | TBD | TBD | 1 credit per US address for each; 0.33 per USCAN address for each | TBD | Premium adds parsed elements, per-element match codes, lat/long | US pricing listed; account access TBD | Premium returned Status F “Address Not Found” for defective input but charged 1 credit; not used in full run | Account and smoke test, observed 2026-10-28 |
| Geocoding forward: Premium | Docs mapping appendix | `/geocode/v1/premium/geocode` | TBD | Premium: 1 credit; Advanced: 0.5; Basic: 0.1. POST is per address. | TBD | `candidates[].precisionCode`; `candidates[].precisionLevel`; suffix `HPNTSCZA` | TBD | GET; Advanced endpoint TBD | Account, observed 2026-10-28 |
| Geocoding forward: Advanced / Basic | TBD | Advanced: TBD; Basic: TBD | TBD | Advanced: 0.5 credit; Basic: 0.1 credit. POST is per address. | TBD | `candidates[].precisionCode`; `candidates[].precisionLevel`; suffix `HPNTSCZA` | TBD | GET | Account, observed 2026-10-28 |
| PreciselyID by address | Geocode/PreciselyID/byPreciselyID/resp_desc.html | `/geocode/v1/key/byaddress` | TBD | 0.01 credit | TBD | `key`, `matchedAddress` | TBD |  | Account, observed 2026-10-28 |
| Key lookup | TBD | TBD | TBD | 0.01 credit | TBD | TBD | TBD |  | Account, observed 2026-10-28 |
| Flood by location: flood zone code, flood risk level, base flood elevation | TBD | `/risks/v1/flood/bylocation?latitude&longitude&includeZoneDesc&includeGeometry` | TBD | 5 credits per call | TBD | `floodZone.code`, `floodZone.areaType`, `floodZone.riskLevel`, `floodZone.primaryZone.code/description`, `floodZone.baseFloodElevation.unit/value`, `floodZone.additionalInfo`, `community.number`, `community.status.code/description` | US only | One flood call supplies the listed candidate attributes | Account, observed 2026-10-28 |
| Distance to flood hazard by location | TBD | TBD | TBD | 2 credits per call | TBD | TBD | US only |  | Account, observed 2026-10-28 |
| Property attributes by address: one named attribute | [Property Attributes v2 sample URL](https://docs.precisely.com/docs/sftw/precisely-apis/main/en-us/webhelp/apis/PropertyInformation/ByAddressv2/sample_url.html); [query parameters](https://docs.precisely.com/docs/sftw/precisely-apis/main/en-us/webhelp/apis/PropertyInformation/ByAddressv2/query_param.html) | `GET /property/v2/attributes/byaddress?address=...&attributes=...` | TBD | 1 credit per requested attribute + 0.01 credit for PBKey; `attributes=all` charges 1 credit per attribute (approximately 50) | TBD | TBD | TBD | Request named attributes only; verify charging on 1 record before any batch. The v1 `/property/v1/all/...` endpoint is deprecated. | Account, observed 2026-10-28 |
| Fire station | TBD | TBD | TBD | 6 credits per call | TBD | TBD | TBD | Recorded; not selected | Account, observed 2026-10-28 |
| Fire risk | TBD | TBD | TBD | 9 credits per call | TBD | TBD | TBD | Recorded; not selected | Account, observed 2026-10-28 |
| Crime | TBD | TBD | TBD | 2 credits per call | TBD | TBD | TBD | Recorded; excluded on fairness | Account, observed 2026-10-28 |
| Earthquake | TBD | TBD | TBD | 2 credits per call | TBD | TBD | TBD | Recorded; not selected | Account, observed 2026-10-28 |

### Geocoder behavior on defective input

| Question | Finding | Source |
|---|---|---|
| Does geocoding auto-correct or standardize raw input? | Premium returned corrected street/city and ZIP+4 for clean and misspelled/no-ZIP input; Basic and Advanced results differed as recorded below | Smoke test, observed 2026-10-28 |
| `candidates[].precisionCode` categories (LI_Geo_responsedesc.html) | S8 house point; S7 interpolated between points; S5 street address; S4 street centroid; SC projected house-level; SX intersection; S0 no coordinates; Z1/Z2/Z3 postal centroid; G1–G4 area centroid; B1/B2 PO box | Docs base: https://docs.precisely.com/docs/sftw/precisely-apis/main/en-us/webhelp/apis/ |
| `candidates[].precisionLevel` (LI_Geo_responsedesc.html) | 0–18; 16 = address point; 3 = postal code centroid; 0 = no coordinates | Docs base: https://docs.precisely.com/docs/sftw/precisely-apis/main/en-us/webhelp/apis/ |
| Match modes (LI_GGM_Geo_Matching.html) | Exact, Standard (default), Relaxed, Custom; USA also Interactive and CASS | Docs base: https://docs.precisely.com/docs/sftw/precisely-apis/main/en-us/webhelp/apis/ |
| `HPNTSCZA` suffix (LI_Geo_responsedesc.html) | Shows which components matched | Docs base: https://docs.precisely.com/docs/sftw/precisely-apis/main/en-us/webhelp/apis/ |
| How is unresolved input reported? (LI_Geo_responsedesc.html) | S0 / `precisionLevel` 0 or no candidates | Docs base: https://docs.precisely.com/docs/sftw/precisely-apis/main/en-us/webhelp/apis/ |
| DPV fields (LI_Geo_responsedesc.html) | Restricted to CASS Certified products; do not rely on them | Docs base: https://docs.precisely.com/docs/sftw/precisely-apis/main/en-us/webhelp/apis/ |

The observed auto-correction determines why Risk 2 uses the coarse-vs-verified
comparison (PROJECT_PLAN.md Sections 15–16).

## Smoke test results (observed 2026-10-28)

- Stage A used 12.81 credits and Stage B used 1 credit (13.81 total). The
  ledger matched the portal Credit Usage dashboard.
- Address Verification Premium returned Status F, “Address Not Found,” for
  the defective input and was charged 1 credit. A not-found answer counts as
  results returned; only true errors are free.
- Geocode Premium returned S8 (house point) for both the clean and
  misspelled/no-ZIP inputs, with corrected street/city and ZIP+4. Match string
  `S8HPN-S--A` shows corrected components; dashes indicate street type, city,
  and ZIP. The input without a house number returned S4 (street centroid).
- Geocode Advanced returned S8 but assigned the misspelled input the wrong ZIP
  (80303 rather than 80301-2532).
- Geocode Basic returned city-level G3 only. The misspelled input resolved to
  Walnut, Los Angeles County, California (wrong state).
- Premium returned `precisionLevel` 19, outside the documented 0–18 list;
  rely on `precisionCode`, not `precisionLevel`.
- Flood by location returned `floodZone.code` SHX and `riskLevel` Moderate,
  with no BFE value. Distance to flood hazard returned 0.069 MILES and a water
  body name.
- Property v2 charged 1 credit for `attributes=builtYear`; the value is at
  `propertyAttributes.builtYear`. The response also includes its own
  `matchedAddress` because the API geocodes internally.
- PreciselyID was returned for the clean input. The identifier value is not
  recorded here.

## Phase 0 decisions

- **Owner-approved 2026-10-28** — United States addresses only; `N = 200`
  records.
- **Owner-approved 2026-10-28** — Geocode Premium is the sole verification,
  standardization, and geocoding step. Address Verification is dropped from
  the full run because it returned “Address Not Found” for defective input and
  still charged 1 credit.
- **Owner-approved 2026-10-28** — Enrichment eligibility is precisionCode
  S5, S7, or S8 only.
- **Owner-approved 2026-10-28** — Any record with precisionCode other than
  S5/S7/S8 (for example S4, Z*, G*, or S0) is not enriched and is routed to a
  “manual review” category with the reason recorded.
- **Owner-approved 2026-10-28** — Selected enrichment: flood zone code,
  flood risk level, and base flood elevation from one flood call; distance to
  flood hazard; and one property attribute, `builtYear`.
- **Owner-approved 2026-10-28** — Risk APIs are called by location using
  pipeline coordinates.
- **Owner-approved 2026-10-28** — Property v2 requests use the Premium
  standardized address, not the raw input.
- **Owner-approved 2026-10-28** — Fire station and fire risk excluded on
  cost/relevance grounds; crime and demographics excluded on fairness grounds.
- **Owner-approved 2026-10-28** — Risk 2 comparison is revised because
  Premium auto-corrects defective input, so comparing raw and Premium-verified
  locations would show no difference. For an approximately 40-record subset,
  compare a coarse location from Geocode Basic with the verified building
  location from Premium S8/S5/S7, measuring flood-zone differences at both
  points. Basic is a proxy for low-precision location assignment, not a claim
  about any specific carrier's process. Comparison cost is 5.1 credits per
  record (Basic 0.1 + flood 5).
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
record. The comparison subset uses Geocode Basic as the coarse-location proxy
and one flood call there, then compares it with flood enrichment at the normal
verified Premium location; the comparison term is
`S × (C_G_basic + C_E_flood)`.

| Symbol | Input | Value |
|---|---|---|
| `B` | Starting available credit balance | 2,500 |
| `C_AV` | Credits per record for Address Verification in full run | 0 |
| `C_G` | Credits per record for Geocode Premium | 1 |
| `C_G_basic` | Credits per comparison record for Geocode Basic coarse-location proxy | 0.1 |
| `C_PID` | Credits per record for PreciselyID retrieval | 0.01 |
| `C_E` | Credits per eligible record for selected enrichment: flood 5 + distance 2 + one property attribute 1 + PBKey 0.01 | 8.01 |
| `C_E_flood` | Credits for flood call at the coarse comparison location | 5 |
| `S` | Coarse-vs-verified enrichment comparison subset size | 40 |
| `C_SMOKE` | Actual Stage A + Stage B smoke-test credits | 13.81 |
| `C_RERUN` | Re-run allowance in credits | 0 (cached; failed calls free) |
| `C_RESERVE` | Reserve floor: `B − C_AVAILABLE` | 350 |
| `C_AVAILABLE` | Approved hard credit cap / spendable limit; reserve floor is `B − C_AVAILABLE` | 2,150 |

**Estimated total formula**

For eligibility rate `E`:

`N × (C_AV + C_G + C_PID) + (N × E × C_E) + S × (C_G_basic + C_E_flood) + C_SMOKE + C_RERUN`

| Case | Calculation | Total | Spendable balance | Remaining balance | Resulting reserve (% of `B`) | Within spendable balance? |
|---|---|---:|---:|---:|---:|---|
| Best case: 200 records, 85% eligible (`E = 0.85`) | `200 × (0 + 1 + 0.01) + (200 × 0.85 × 8.01) + 40 × (0.1 + 5) + 13.81 + 0` | 1,781.51 | 2,150 | 718.49 | 28.74% | Yes; 368.49 credits below hard cap |
| Worst case: 200 records, 100% eligible (`E = 1.00`) | `200 × (0 + 1 + 0.01) + (200 × 1.00 × 8.01) + 40 × (0.1 + 5) + 13.81 + 0` | 2,021.81 | 2,150 | 478.19 | 19.13% | Yes; 128.19 credits below hard cap |

The reserve percentage is remaining balance divided by starting balance.
The reserve floor is 350 credits (14% of the starting balance); the worst case
leaves 478.19 credits. Reconcile the ledger with the portal dashboard before
each live run.

## 6. Enrichment candidate scoring

Record only verified attributes and documented definitions. Use candidate
themes from `PROJECT_PLAN.md` as a starting point; do not treat them as
available products or fields until confirmed.

| Attribute | Business decision it supports | Supports Risk 1 or Risk 2? | Available? | Definition/units | Fairness concern (Y/N) | Selected? |
|---|---|---|---|---|---|---|
| Flood zone code | Compare hazard category at coarse versus verified location for Risk 2 | Risk 2 | TBD | TBD | TBD | Yes |
| Flood risk level | Support location-risk review for Risk 2 | Risk 2 | TBD | TBD | TBD | Yes |
| Base flood elevation | Support location-risk review for Risk 2 | Risk 2 | TBD | TBD | TBD | Yes |
| Distance to flood hazard | Support location-risk review for Risk 2 | Risk 2 | TBD | TBD | TBD | Yes |
| `builtYear` | TBD | TBD | TBD | TBD | TBD | Yes |

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
