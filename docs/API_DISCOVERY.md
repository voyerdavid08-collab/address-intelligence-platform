# Phase 0 API Discovery Worksheet

Purpose: record verified Precisely trial, API, US coverage, enrichment, and
LLM details before implementation or API calls. Fill this worksheet from
official documentation and the trial account.

**Status:** NOT STARTED  
**Date checked:** TBD

Every filled value must include a source link or the note
`observed in account on <date>`. Do not infer undocumented behavior.

## 1. Account and trial

| Item | Value | Source / observation |
|---|---|---|
| Trial start date | TBD | TBD |
| Trial expiry date | TBD | TBD |
| Credit balance at start | TBD | TBD |
| Where the balance is visible | TBD | TBD |
| Permitted test-data rules | TBD | TBD |
| US coverage confirmed (Y/N/TBD) | TBD | TBD |

## 2. Authentication

| Item | Value | Source |
|---|---|---|
| Method | TBD | TBD |
| Credential type | TBD | TBD |
| Token lifetime | TBD | TBD |
| Base URL | TBD | TBD |

Secrets never go in this file. Record documentation links and credential
metadata only; do not record credentials, tokens, or other secret values.

## 3. Operations and documentation

| Operation | Official doc link | Endpoint (as documented) | Enabled on my account? (Y/N/TBD) | Credits per call (as documented) | Batch supported? | Key response fields | US coverage | Notes |
|---|---|---|---|---|---|---|---|---|
| Address verification/standardization | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| Geocoding | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| PreciselyID retrieval | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| Candidate enrichment attribute 1 | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| Candidate enrichment attribute 2 | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| Candidate enrichment attribute 3 | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| Candidate enrichment attribute 4 (if needed) | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| Candidate enrichment attribute 5 (if needed) | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |

### Geocoder behavior on defective input

| Question | Finding | Source |
|---|---|---|
| Does geocoding auto-correct or standardize raw input? | TBD | TBD |
| What precision/match levels are returned? Record the names exactly as documented. | TBD | TBD |
| How is unresolved input reported? | TBD | TBD |

This determines how the raw-vs-verified comparison (PROJECT_PLAN.md Sections
15–16, Risk 2) is defined.

## 4. Credit budget calculator

Enter documented/account-observed inputs only. Use the same unit (credits)
throughout. For selected enrichment, `C_E` is the sum of credits for one
enrichment pass per record; the comparison subset adds a raw-location pass
alongside the normal verified-location pass. If billing differs, revise the
formula to match documented billing before any call.

| Symbol | Input | Value |
|---|---|---|
| `B` | Starting available credit balance | |
| `C_AV` | Credits per record for address verification/standardization | |
| `C_G` | Credits per record for geocoding | |
| `C_PID` | Additional credits per record for PreciselyID retrieval, if separately charged | |
| `C_E` | Credits per record for one selected enrichment pass | |
| `S` | Raw-vs-verified enrichment comparison subset size | |
| `C_SMOKE` | Smoke-test allowance in credits | |
| `C_RERUN` | Re-run allowance in credits | |
| `C_RESERVE` | Reserve: `0.20 × B` | |
| `C_AVAILABLE` | Spendable balance: `B − C_RESERVE` | |

**Estimated total formula**

`N × (C_AV + C_G + C_PID + C_E) + S × C_E + C_SMOKE + C_RERUN`

Proceed only if the estimated total is no greater than `C_AVAILABLE` and the
owner has approved the estimate.

| Planned records (`N`) | Estimated total | Spendable balance | Within budget? |
|---|---|---|---|
| 200 | `200 × (C_AV + C_G + C_PID + C_E) + S × C_E + C_SMOKE + C_RERUN` | `B − C_RESERVE` | TBD |
| 300 | `300 × (C_AV + C_G + C_PID + C_E) + S × C_E + C_SMOKE + C_RERUN` | `B − C_RESERVE` | TBD |

Confirm whether PreciselyID retrieval is a separate billed operation and
whether enrichment billing is per request, record, attribute, or another
documented unit. If unsupported or included in another operation, record the
documented treatment rather than assuming a zero cost.

## 5. Enrichment candidate scoring

Record only verified attributes and documented definitions. Use candidate
themes from `PROJECT_PLAN.md` as a starting point; do not treat them as
available products or fields until confirmed.

| Attribute | Business decision it supports | Supports Risk 1 or Risk 2? | Available? | Definition/units | Fairness concern (Y/N) | Selected? |
|---|---|---|---|---|---|---|
| TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| TBD | TBD | TBD | TBD | TBD | TBD | TBD |

## 6. LLM option check

| Option | Free-tier access | Data-retention terms link | Laptop feasibility | Decision |
|---|---|---|---|---|
| Gemini | TBD | TBD | TBD | TBD |
| Groq | TBD | TBD | TBD | TBD |
| Hugging Face | TBD | TBD | TBD | TBD |
| Ollama | TBD | TBD | TBD | TBD |

## 7. Open questions and blockers

| Question or blocker | Owner / next action | Status | Source / notes |
|---|---|---|---|
| TBD | TBD | TBD | TBD |
| TBD | TBD | TBD | TBD |
| TBD | TBD | TBD | TBD |

## 8. Phase 0 exit criteria

- [ ] API verification checklist completed or blockers recorded.
- [ ] No speculative API calls.
- [ ] Visibility approval remains separate.
