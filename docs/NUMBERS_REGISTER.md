# Numbers register

**Status: draft for approval (Gate 2).** No figure enters the deck or the one-pager unless it is listed here.

**Types:** *Measured* = from this project's runs. *Sourced* = public source, linked. *Assumed* = planning assumption, to be replaced by carrier data in the pilot. *Illustrative* = example value only. *By design* = set when the synthetic data was built.

Row-level results are not published here. All measured figures are aggregates.

## 1. The sample

| ID | Claim | Value | Calculation | Source | Type |
|---|---|---:|---|---|---|
| S1 | Records in the sample | 200 | Count | `data/input/README.md` | By design |
| S2 | Records with an error | 76 (38%) | 76 / 200 | `data/input/README.md` | By design |
| S3 | Homeowners / Commercial Property | 170 / 30 (85% / 15%) | Count of `line_of_business` | `data/input/policyholders_synthetic.csv` | By design |
| S4 | Located automatically | 170 of 200 (85%) | Precision S5, S7 or S8 | `data/output/analysis/breakdown.md` | Measured |
| S5 | Pinpointed to the exact address | 39 of 170 (23%) | S8, eligible records | `data/output/analysis/breakdown.md` | Measured |
| S6 | Located on the street (street segment) | 131 of 170 (77%) | S5, eligible records | `data/output/analysis/breakdown.md` | Measured |
| S7 | Sent for follow-up | 30 of 200 (15%) | Not eligible | `reports/remediation_actions.md` | Measured |
| S8 | Free file check: no visible issue / visible issue | 174 / 26 of 200 (87% / 13%) | App pre-flight groups on the input file | App tab 2, Nov 8, 2026 | Measured |
| S9 | File check "needs the customer" that went to a person | 10 of 10 | Pre-flight group joined to run eligibility by policy ID, totals only | Local count, Nov 8, 2026 | Measured |
| S10 | File check "Precisely usually fixes": fixed automatically / went to a person | 14 / 2 of 16 | Same join | Local count, Nov 8, 2026 | Measured |
| S11 | No visible issue in the file, but went to a person | 18 of 174 | Same join; 10 + 2 + 18 = 30 = S7 | Local count, Nov 8, 2026 | Measured |

S5 records are never described as verified to the building.

## 2. What was fixed, by error type

Flawed records with the right street address and ZIP, before and after. Records with several errors count in each row.

| ID | Error type | Records | Before | After | Source | Type |
|---|---|---:|---:|---:|---|---|
| F1 | Missing ZIP | 16 | 0 | 14 | `data/output/analysis/breakdown.md` | Measured |
| F2 | Wrong ZIP | 10 | 0 | 9 | same | Measured |
| F3 | City typo | 12 | 6 | 10 | same | Measured |
| F4 | Street typo | 20 | 0 | 10 | same | Measured |
| F5 | Missing unit | 10 | 8 | 8 | same | Measured |
| F6 | Missing house number | 10 | 0 | 0 | same | Measured |
| F7 | Non-existent address | 8 | 0 | 0 | same | Measured |
| F8 | All flawed records | 76 | 14 | 45 | same | Measured |
| F9 | Fixed, of those wrong before | 62 | — | 31 (50%) | same | Measured |
| F10 | ZIP-only fixes among records fixed | 32 | — | 22 | same | Measured |
| F11 | Fully correct including unit | 76 | 6 | 37 | same | Measured |

F8 excludes one record where only the ZIP differs from the synthetic reference, which is why 32 were fixed but the net gain is 31.

## 3. Address Verification capability check

Real business and public-building addresses, no private homes. This is a capability check, not the carrier's results.

| ID | Claim | Value | Calculation | Source | Type |
|---|---|---:|---|---|---|
| A1 | Inputs tested | 16 | 10 + 6 typo inputs | `data/output/av_check/` | Measured |
| A2 | Street typos corrected by Address Verification | 6 of 7 | All 7 sent with fields mapped separately on Nov 5, 2026: 1 not found, 6 corrected. The October single-call test is excluded | `data/output/av_check/` | Measured |
| A3 | Street typos corrected by Geocode Premium | 7 of 7 | Same inputs | `data/output/av_check/` | Measured |
| A4 | Missing suite flagged where the geocoder gave no warning | Shown on multi-tenant buildings | Status description "Missing Apt/Suite Number" | `data/output/av_check/` | Measured |
| A5 | Suite supplied when the firm name is known | 1 of 1 | Precisely HQ | `data/output/av_check/` | Measured |
| A6 | USCAN price | 0.33 credit per address | Portal drop matched the ledger | Portal, Nov 5, 2026 | Measured |
| A7 | USCAN across the book, list price | ≈$990 | 300,000 × 0.33 × $0.01 | Calculated | Assumed |

## 4. Flood finding

"Centre point" means the flood tier at the ZIP's centre point, compared with the tier at the property's own location. These metros were chosen for flood exposure, so none of this is a national rate. A tier change is not proof of mispricing.

| ID | Claim | Value | Calculation | Source | Type |
|---|---|---:|---|---|---|
| L1 | Exact addresses in a different tier | 15 of 39 (38%) | Tier at centre vs. own location | `data/output/zip_comparison/summary.md` | Measured |
| L2 | Of those, centre shows lower risk / higher risk | 6 / 9 | Same | same | Measured |
| L3 | All located records in a different tier | 65 of 170 (38%) | Same | same | Measured |
| L4 | Of those, lower risk / higher risk | 31 / 34 | Same | same | Measured |
| L5 | Crossed in or out of a high-risk zone, all records | 56 of 170 (33%) | A or V zone at one point only | `data/output/zip_comparison/results.csv` | Measured |
| L6 | Crossed in or out of a high-risk zone, exact addresses | 10 of 39 (26%) | 6 out, 4 in | same | Measured |
| L7 | Truly high-risk exact addresses missed by the centre point | 6 of 8 | Very small sample, backup only | same | Measured |
| L11 | Exact addresses high risk at the property, minimal at the ZIP's centre | 2 of 39 | Plus 12 street-level records | `data/output/zip_comparison/results.csv` | Measured |
| L8 | Clean vs. flawed records changing tier | 46 of 124 vs. 19 of 46 | Similar rates: a location-precision finding | same, with `data/input/ground_truth.csv` | Measured |
| L9 | ZIPs with any change | 14 of 26 | One ZIP holds 13 of 65 | same | Measured |
| L10 | Original city-level comparison | 14 of 39 (36%; 6 lower, 8 higher) | Reproduced with the same tier logic | `results_rowlevel.csv` | Measured |

## 5. Rules and AI

Errors were injected by design, so rules look better here than they would on real data.

| ID | Claim | Value | Calculation | Source | Type |
|---|---|---:|---|---|---|
| R1 | AI alone, correct cause | 62 of 68 (91%) | Top answer | `reports/remediation_calibration.md` | Measured |
| R2 | Rules first, AI on the rest, correct cause | 65 of 68 (96%) | 52 + 13 | `data/output/analysis/rules_vs_ai.md` | Measured |
| R3 | Rules decided | 52 of 68, all correct | Fixed rules | same | Measured |
| R4 | Ambiguous cases left for the AI | 16, of which 13 correct | Same | same | Measured |
| R5 | Customer outreach: rule | 17 of 18, 0 sent by mistake | On 30 exceptions | same | Measured |
| R6 | Customer outreach: AI | 14 of 18, 0 sent by mistake | On 30 exceptions | same | Measured |
| R7 | AI suggestions confirmed by Precisely | 6 of 9 | 0 confirmed but wrong; 3 not confirmed | `reports/remediation_actions.md` | Measured |
| R9 | Follow-up cases with a clear cause from Precisely's codes | 19 of 30 | Cause clear, not fixed; they still go to outreach or an analyst | `data/output/analysis/rules_vs_ai.md` | Measured |
| R10 | Harder cases left for AI | 11 of 30 | 30 − 19 | same | Measured |
| R11 | Follow-up cases sent to customer outreach | 14 of 30 | Actions in the run | `reports/remediation_actions.md` | Measured |
| R12 | Follow-up cases for analysts | 16 of 30 | 30 − 14 | same | Measured |
| R13 | Analyst cases with a confirmed fix to approve | 6 of 16 | Confirmed by Precisely | same | Measured |
| R14 | Cases left for research | 10 of 200 | 16 − 6 | same | Measured |
| R8 | Calibration | Pending client data | Only 6 ambiguous exceptions in the calibration split | same | Measured |

Precisely confirms that a suggested address exists, not that it is the policyholder's. Every suggestion goes to an analyst for review.

## 6. Inputs to the business case

| ID | Input | Value | Basis | Source | Type |
|---|---|---:|---|---|---|
| B1 | Book size | 300,000 policies | Brief | Assignment document | Sourced |
| B2 | Flawed addresses | 15% = 45,000 | Planning assumption; the pilot replaces it | Planning estimate | Assumed |
| B3 | Staff time per flawed address | 17.5 min | Midpoint of 15–20 | Planning estimate | Assumed |
| B4 | Loaded staff cost | $40 per hour | Planning estimate | Planning estimate | Assumed |
| B5 | Staff cost per flawed address | $11.67 | B3 × B4 | Calculated | Assumed |
| B6 | Staff time avoided | 60% | 410–575 of 1,050–1,400 minutes remain on the 70 records needing work | `reports/`, `breakdown.md` | Assumed, from measured counts |
| B7 | Mailings per policy per year | 4 | Renewal, billing, notices | Planning estimate | Assumed |
| B8 | Undeliverable mail rate | 4.3% | Share of USPS volume, FY2014 (6.6 billion pieces); also 4.3% in FY2013 | [USPS OIG MS-MA-15-006](https://www.oversight.gov/sites/default/files/oig-reports/MS-MA-15-006.pdf) | Sourced |
| B9 | Cost per undeliverable piece | $2 | Within a $1–3 trade-press estimate from unnamed industry experts | [Mailing Systems Technology](https://mailingsystemstechnology.com/print-article-4191-permanent.html) | Assumed |
| B10 | Undeliverable mail attributed to business mailers | 35% | Report p. 12: "business mailers (35 percent)"; "Much of the 35 percent" is insufficient addressing. Moves are the largest cause (public 40%) and verification does not fix moves | [USPS OIG MS-MA-15-006](https://www.oversight.gov/sites/default/files/oig-reports/MS-MA-15-006.pdf) | Sourced |
| B11 | Homes filing a claim each year | 5% | Rounded down from 5.3% in 2023 | [Triple-I](https://www.iii.org/fact-statistic/facts-statistics-homeowners-and-renters-insurance) | Sourced |
| B12 | Cost to re-route a claim | $50 | Midpoint of $40–60, no named source | Planning estimate | Assumed |
| B13 | Claims re-routing avoided | 31 of 76 (41%) | Flawed records fully corrected by processing; excludes the 6 already correct | `breakdown.md` | Assumed, from a measured share |
| B14 | Proxy for the yearly flow of new and changed addresses (carrier confirms) | 11.8% = 35,400 | US movers, 2024 | [Census ACS 1-year](https://www.census.gov/topics/population/migration/guidance/acs-1yr.html) | Sourced |
| B15 | Flawed addresses needing work each year without entry checks | ≈4,891 | 35,400 × 15% × 70/76 | Calculated | Assumed |
| B16 | Credits per policy | 9.33 | Geocode 1, flood 5, water 2, year built 1, USCAN 0.33. Enrichment applied to all 300,000 (conservative). Separate ID lookup dropped: PB_KEY came back on the same 39 records | `src/address_intelligence/credits.py`, `breakdown.md` | Measured prices |
| B17 | Credit value | $0.01 | API list price, illustrative; enterprise subscription quoted in the proposal; risk-use fees excluded | `docs/API_DISCOVERY.md` | Sourced |
| B18 | Developer cost | $3,700 a week | $135,980 ÷ 0.70 ÷ 52 | Wage: [BLS Occupational Outlook Handbook](https://www.bls.gov/ooh/computer-and-information-technology/software-developers.htm); benefits 30%: [BLS ECEC](https://www.bls.gov/news.release/ecec.nr0.htm) | Sourced |
| B19 | Integration effort | 20 person-weeks | Batch 3, entry checks 14, work list 3 | Planning estimate | Assumed |
| B20 | Annual maintenance | 10% of integration | Planning estimate | Planning estimate | Assumed |

## 7. Risk 1: operations cost

| ID | Claim | Value | Calculation | Type |
|---|---|---:|---|---|
| C1 | Cost to clear the flawed backlog by hand | ≈$483,553 one-time | 45,000 × 70/76 × $11.67 | Assumed |
| C2 | Undeliverable mail | $103,200 a year | 300,000 × 4 × 4.3% × $2 | Assumed |
| C3 | Re-routed claims | $112,500 a year | 45,000 × 5% × $50 | Assumed |
| C4 | New flawed addresses, staff time | ≈$57,059 a year | ≈4,891 × $11.67 | Assumed |
| C5 | **Ongoing cost** | **≈$272,759 a year** | C2 + C3 + C4 | Assumed |

## 8. Cost and return

Run-rate savings start at go-live. Flood value and the backlog cleanup are excluded from the headline.

| ID | Claim | Value | Calculation |
|---|---|---:|---|
| V1 | Staff time saved on new addresses | ≈$34,236 a year | C4 × 60% |
| V2 | Mail saved | $36,120 a year | C2 × 35% |
| V3 | Claims handling saved | ≈$45,888 a year | C3 × 31/76 |
| V4 | **Total saved** | **≈$116,244 a year** | V1 + V2 + V3 |
| V5 | Data, API list price, illustrative | $27,990 to start; ≈$3,303 a year | 300,000 × 9.33 × $0.01; 35,400 × 9.33 × $0.01 |
| V6 | Integration; maintenance | $74,000; $7,400 a year | 20 × $3,700; 10% |
| V7 | **Cost to go-live** | **≈$101,990** | Data + integration |
| V8 | **Running cost** | **≈$10,703 a year** | Data + maintenance |
| V9 | **Payback** | **≈12 months after go-live** | V7 ÷ ((V4 − V8) ÷ 12) = 11.6 |
| V10 | **Three-year net** | **≈$214,633** | 3 × (V4 − V8) − V7, years counted from go-live |
| V11 | **Annual return once running** | **≈11×** | V4 ÷ V8; 7.7× on mail and claims alone |

**Backlog option, shown separately:** clearing the backlog by hand costs ≈$483,553. With Precisely, ≈$193,421 of work remains. That ≈$290,132 difference is value only if the carrier would otherwise do this work.

## 9. Risk 2: flood scenario

Conditional and illustrative. It applies only if policies are placed at a ZIP's centre, and only to flood-covered policies.

| ID | Claim | Value | Calculation | Type |
|---|---|---:|---|---|
| D1 | Per 1,000 flood-covered policies, sitting in a lower tier than their real location | ≈150 | 6 / 39; range 70–300 | Measured rate, flood-exposed sample |
| D2 | Per 1,000, sitting in a higher tier | ≈230 | 9 / 39; range 130–380 | Measured rate, flood-exposed sample |
| D3 | Example premium gap between tiers | $1,000 a year | Carrier replaces it | Illustrative |
| D4 | Example uncollected premium per 1,000 policies | $150,000 a year | D1 × D3 | Illustrative |
| D5 | Private share of flood policies | Commercial 23% → 48%; residential 6% → 11%, 2018 to 2025 | By policy count | Sourced: [GAO-27-108012](https://www.gao.gov/products/gao-27-108012) |
| D6 | NFIP premiums no longer use flood zones | Risk Rating 2.0 | Property-specific rating | Sourced: [CRS IN11777](https://www.everycrsreport.com/reports/IN11777.html) |

## 10. Pilot

| ID | Claim | Value | Basis | Type |
|---|---|---:|---|---|
| P1 | Pilot scope | 25,000–50,000 policies, 13 weeks | Proposed scope, agreed with the carrier | Assumed |
| P2 | Pilot data cost | ≈$2,300–$4,700 | 25,000–50,000 × 9.33 × $0.01, API list price | Assumed |
| P3 | Analyst time | 2–3 analysts, about a day a week, 8 weeks: ≈130–190 hours, ≈1,100–1,650 cases | 7 minutes a case = 17.5 × 40% | Assumed |
| P4 | Every case in the region | ≈3,450–6,900 cases, ≈400–800 hours | Policies × 15% × 70/76 × 7 minutes | Assumed |
| P5 | IT time | One contact, about 2 days a week for 13 weeks, ≈26 days | Slice of B19 | Assumed |

## 11. Retired figures

Not to be used: "6 in 10 fixed", "3 in 4 resolved", "≈25× return", "≈3 months payback", "≈11 months payback", "≈$125K saved", "≈$242K net", "$0.6–0.8M", "$1.1–1.3M", "$2–7M", "1 in 3 mispriced", "≈9,450 over-charged", "verified to the building" for S5 records, "applied", "one-click approval", "no incorrect change reached a policy record".
