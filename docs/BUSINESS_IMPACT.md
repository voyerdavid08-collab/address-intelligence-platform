# Address Intelligence: Solution Brief

**For:** VP of Operations · **Prepared by:** David Voyer, Solutions Architect · Precisely SE assignment · Nov 2026

> The formatted one-page version is [BUSINESS_IMPACT.pdf](BUSINESS_IMPACT.pdf) (source: [BUSINESS_IMPACT.html](BUSINESS_IMPACT.html)). This page carries the same content in text form.

## A ≈$27K data investment recovers ≈$0.7M a year in operating cost and closes up to $7M a year of unpriced flood risk

In a 200-policy sample reflecting the carrier's address issues, 38% of addresses contained an error. Sized conservatively at 15%, that is about 45,000 of 300,000 policies the operations team cannot fully trust. Precisely address verification and location data fix most of them automatically and put each property in the right risk zone; a focused AI layer tells the team what to do with the rest.

| ≈25× year-one return | $1.1–1.3M / yr | $2–7M / yr |
|---|---|---|
| ≈$27K to verify and enrich all 300,000 policies, against ≈$0.7M a year saved | spent chasing flawed addresses and rerouting claims | of flood risk not reflected in premium, plus ≈9,450 customers over-charged |

## Two business risks

| | Risk 1 · Cost: Misrouted claims and manual follow-up | Risk 2 · Revenue: 1 in 3 policies rated on a rough location is mispriced |
|---|---|---|
| **Exposure** | **$1.1–1.3M a year.** 45,000 flawed addresses *(assumed)* × $22–25 in staff time *(assumed)* and returned mail *(sourced)* + 2,250 claims *(sourced)* × $40–60 to re-route *(vendor estimate)* | **$2–7M of unpriced flood risk a year.** 45,000 policies rated on a rough location *(assumed)* × 15% under-priced *(measured)* × ≈$1,000 high- vs. low-risk price gap *(sourced)*; ≈$2.3M if a third are affected; plus ≈9,450 over-charged *(21% measured)* |
| **Evidence** | One flawed address typically costs 15–20 minutes of staff time: investigate (≈5 min), contact the policyholder (≈5–10 min), correct and re-issue (≈5 min). Plus returned mail (≈$3 a piece) and claims sent to the wrong service area. | Rough vs. confirmed location on 39 sample properties: right zone 64%, riskier than priced 15%, safer than priced 21%. Every under-priced case was high-risk. The carrier keeps the risks it under-prices and loses the customers it over-prices. |
| **With Precisely** | Addresses are corrected and confirmed as they enter; in the sample, 6 in 10 flawed addresses were fixed to the right building automatically, and 3 in 4 were resolved without manual research once the AI layer was added. | Every confirmed policy is rated on the building's location and flood zone; underwriting is alerted when a location cannot be confirmed. |
| **Outcome** | ≈$0.6–0.8M a year back in staff time, postage and claims handling | Prices match real risk: flood losses covered, good customers kept |

## What the AI layer adds

**Location data shows where a property is; the AI layer tells the team what to do when an address is wrong, so two in three problem addresses leave the queue with a correct next step.**

Process: **Verify** (Precisely confirms the address) → **Enrich** (flood zone, water distance, year built where available) → **Diagnose** (AI names the likely cause, with confidence) → **Confirm** (Precisely re-checks every AI fix).

**Where the AI sent the 30 addresses that needed follow-up** (30 of the 200 policies in a sample representative of the carrier's address issues: typos, missing units, ZIP/city mismatches, outdated entries):

| Outcome | Count | Detail |
|---|---:|---|
| Customer requests | 14 | Ask the customer for the missing house number; cause identified correctly 14 of 14 |
| Fixes confirmed | 6 | Confirmed fix, one-click approval; 6 of 6 correct |
| Analyst review | 10 | Analyst works from a short list of likely causes |

**Every AI fix is verified by Precisely before it is applied:** no incorrect change reached a policy record.

## Also addressed

- **Compliance:** a verified location for every confirmed policy, with a permanent location ID wherever Precisely holds the address point.
- **Automation-ready:** consistent location data that underwriting rules and models can rely on.

## Decision requested

**Approve a 90-day pilot on one region's live policies**, measured on staff hours, returned mail and re-rated policies against the estimates on this page.

---

**Basis.** Estimates for a 300,000-policy book. **Measured** on a 200-policy sample modeled on the carrier's address issues (38% with an error): 6 in 10 flawed addresses fixed automatically; 30 not confirmable by verification alone, of which the AI layer resolved 6 with a confirmed fix (Precisely rejected 3 AI suggestions that did not verify) and routed 14 to the customer; risk tiers on 39 properties vs. city-level location (upper bound). **Sourced:** returned mail ≈$3/piece (USPS OIG); ≈5% of homes claim each year (Insurance Research Council); high- vs. low-risk flood price gap ≈$1,000/yr (indicative); $40–60 per manual claim (vendor estimates). **Assumed:** 15% of addresses flawed and as many rated on a rough location; 15–20 min per flawed address at $40/hr loaded; 4 mailings a year; every claim on a flawed address re-routed (worst case). **Savings:** research avoided on 75% of flawed addresses; mail and re-routing avoided on the 49% fully fixed. **Data cost:** ≈9 credits/policy (verification 1, ID 0.01, flood 5, water 2, year built 1) at $0.01 list; excludes enterprise pricing, risk-use fees, integration.
