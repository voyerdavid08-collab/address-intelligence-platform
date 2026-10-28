# Address Intelligence Platform — Project Plan

## 1. Executive summary and solution vision

Build a laptop-runnable Python prototype that demonstrates, on a controlled
synthetic insurance-policyholder dataset, how address verification,
geocoding, location enrichment, and one focused AI workflow can turn noisy
address records into evidence-backed underwriting context.

The prototype will prioritize a reproducible end-to-end demonstration and
clear customer-facing communication through the required business impact
summary over production-scale infrastructure. A small reusable pipeline will
isolate provider integrations from data processing and insurance-specific
decision rules. Actual Precisely-derived results will be distinguishable from
synthetic fixtures or simulated fallbacks. Metrics will describe the prototype
dataset only; any enterprise-scale financial impact will be explicitly framed
as an assumption or scenario, not as measured savings.

The fictional carrier has 300k+ policyholder records, but the assignment
requires a 200–500-record synthetic demonstration. The prototype will not
claim that a small synthetic sample proves results for the carrier's full
portfolio.

### Classification used in this plan

- **Explicit requirement** — stated in the candidate assignment.
- **Recommended decision** — an implementation choice proposed here to meet
  the assignment with appropriate safety and proportionate complexity.
- **Optional enhancement** — useful if time and access permit, but not needed
  for the core submission.

## 2. Assignment requirements traceability

| ID | Assignment requirement / update | Planned evidence / acceptance evidence |
|---|---|---|
| R1 | Generate 200–500 synthetic policyholder records with realistic address noise: misspellings/transpositions, missing unit numbers, ZIP/city mismatches, and outdated or non-existent addresses. Document how errors were introduced and the intentionally dirty percentage. | Reproducible generator, documented seed and per-error counts/rates, and a CSV with 200–500 rows. Synthetic identities and addresses are not represented as real policyholders. |
| R2 | Use Precisely APIs for address verification/standardization, geocoding, and geocoding with PreciselyID for persistent location linking. | Provider adapter and run artifacts using verified API capabilities; document API access and actual results. Do not assert PreciselyID availability/semantics until confirmed in current documentation and the account. |
| R3 | Produce before/after comparison including match rates, standardization improvements, and coordinate coverage. | Reproducible summary metrics with explicit denominators, missing/failure handling, and before/after outputs. |
| R4 | Append 3–5 relevant location enrichment attributes to validated records using Precisely APIs; explain why selected and what business decision they enable. | Attribute selection supported by verified product, field, and account-access documentation; enriched output and field provenance. |
| R5 | Implement one functional, focused AI step using a free-tier LLM option or local model; depth over breadth. | A tested AI workflow on actual or clearly labelled fixture enrichment data, with structured output and evidence-grounding checks. |
| R6 | Write a one-page VP of Operations business impact summary that communicates the business problem, quantifies two relevant risks in cost or revenue terms, explains how the proposed solution mitigates them, and explains the incremental value of AI beyond enrichment. | `docs/BUSINESS_IMPACT.md` with all financial inputs labelled as measured, sourced, or assumed; optional PDF export. |
| R7 | Everything runs locally on a laptop using free-tier services; no cloud infrastructure required. | Reproducible local setup and run instructions; no AWS, Azure, or GCP dependency. |
| R8 | Precisely free trial is described as 2,500 credits valid for 30 days; plan use and do not exhaust credits during exploration. | Written budget gate, measured/verified credit assumptions before calls, and a conservative execution plan. |
| R9 | Deliver a public GitHub repository containing runnable commented code or notebook, README with setup, `.env` key structure and sample outputs; before/after CSV or Excel with summary stats; and the one-page impact summary. | Repository contents and final checklist. The current repository is private; changing it to public is a publication/visibility change and must be explicitly approved before it is made. |

### Explicit requirements versus scope boundaries

- The fictional carrier's scale and pain points (risk-zone assignment,
  misrouted claims, compliance exposure, and data not ready for AI
  underwriting) are the business scenario, not measured facts about a real
  customer.
- The assignment calls for a functional prototype. It does not require
  production deployment, enterprise-scale processing, automated underwriting
  decisions, a web UI, or a distributed system.
- A public repository is an explicit submission requirement. This repository
  is currently private. Do not change visibility or publish sample data/code
  without the owner's explicit approval.

### Mandatory and optional deliverables

**Mandatory:** a working, locally runnable prototype using 200–500 synthetic
policyholder records; verified Precisely address validation/standardization,
geocoding and PreciselyID mapping where supported and available; 3–5 verified
location enrichment attributes subject to actual product access and
documented API capabilities; one functional, evidence-grounded AI workflow;
before/after data, metrics, and documented results; a README covering setup,
configuration, and sample outputs; and the one-page business impact summary.
These artifacts are to be submitted in a public GitHub repository, subject to
the current private-repository visibility constraint and explicit approval
before changing visibility or publishing.

**Optional enhancements:** only those listed in Section 20; they do not
replace or delay mandatory deliverables.

## 3. Functional and non-functional requirements

### Target geography

**Recommended decision:** United States addresses only. This focuses the
prototype on standardized US hazard data and offers the best chance of
meaningful API matches. Phase 0 must confirm that US products and selected
attributes are enabled for the account before implementation or API use.

### Functional requirements

1. Generate a deterministic synthetic dataset with 200–500 rows and known
   injected defects.
2. Validate input structure and preserve an immutable copy of each raw
   address.
3. Run address verification/standardization and geocoding through Precisely
   APIs after API documentation, account access, and credit budget are
   verified.
4. Persist the provider's PreciselyID when the verified API operation returns
   one, retaining it as a provider-returned identifier rather than inventing
   or deriving one.
5. Apply a configurable enrichment selection to records that meet the
   documented validation/eligibility policy.
6. Produce row-level raw, standardized, geocoded, enriched, and processing
   status data, plus before/after quality metrics.
7. Generate one evidence-grounded, concise risk narrative or equivalent
   focused AI result, with explicit abstention when evidence is missing or
   contradictory.
8. Produce sample artifacts and a non-technical business impact summary.

### Non-functional requirements

- **Laptop-first:** local execution; free-tier services only for the prototype.
- **Reproducible:** pinned/declared dependencies, deterministic synthetic
  generation, documented commands and configuration.
- **Safe:** synthetic data only for external API/LLM calls; secrets kept out of
  source control; no unsupported underwriting or risk assertions.
- **Observable:** structured, actionable progress and errors without logging
  API credentials or unnecessary address/identity data.
- **Resilient:** timeouts, bounded retries for transient errors, explicit
  record-level partial-failure status, and a no-silent-fallback policy.
- **Maintainable:** typed contracts and small modules; provider-specific
  details isolated from reusable orchestration and scenario rules.
- **Testable offline:** unit and fixture-based tests do not require API
  credentials, network access, or LLM calls.
- **Proportionate:** no database, queue, cloud deployment, agent framework,
  or UI unless a later demonstrated need justifies it.

## 4. Proposed architecture and component responsibilities

### Architecture diagram

```mermaid
flowchart LR
    A[Seeded synthetic data generator] --> B[Input validation and canonical records]
    B --> C[Pipeline orchestrator]
    C --> D[Precisely address verification adapter]
    D --> E[Precisely geocoding adapter]
    E --> F[Eligibility and enrichment workflow]
    F --> G[Precisely enrichment adapter]
    E --> H[Canonical result and provenance store]
    G --> H
    H --> I[Quality metrics and before/after report]
    H --> J[Evidence-grounded AI narrative]
    I --> K[CSV or Excel deliverables]
    J --> K
    K --> L[Mandatory executive business impact summary]
    M[Settings and secret provider] --> C
    M --> D
    M --> E
    M --> G
```

### Components

| Component | Responsibility |
|---|---|
| Synthetic data | Generate controlled, reproducible records and defect labels/ground truth for evaluating the demonstration. |
| Canonical data contracts | Define provider-independent input, normalized address, geocode, enrichment, status, and provenance shapes. Keep raw provider payloads out of general business logic. |
| Orchestrator | Execute ordered stages, enforce eligibility and configuration, manage per-record status, and summarize partial failures. |
| Precisely adapters | Own authentication, verified request/response mappings, timeouts, rate-limit behavior, and provider error translation. Exact API details remain gated on official documentation and account verification. |
| Enrichment workflow | Select requested fields and decide which successfully processed records are eligible; do not infer missing values. |
| Business rules | Express scenario-specific review flags or simple evidence-based rules separately from reusable address processing. No automated premium or coverage decisions. |
| AI workflow | Produce a short evidence-grounded explanation from explicitly supplied, available fields; separate model-generated text from authoritative source data. |
| Metrics/reporting | Calculate defined quality measures and export row-level results and aggregate summaries with provenance. |
| Configuration and logging | Load non-secret settings from configuration and secrets from environment; emit useful stage, timing, count, and error information. |

**Decision — deterministic orchestration, not an autonomous agent
(recommended):** the assignment needs a repeatable, auditable pipeline and one
focused AI step. A deterministic orchestrator is easier to budget, test,
explain, and reproduce than an autonomous agent. The trade-off is less
open-ended behavior, which is appropriate for a constrained customer demo.

**Decision — provider adapters and canonical contracts (recommended):**
isolate Precisely-specific request/response details so other scenarios or
providers do not require rewriting core processing. The trade-off is a small
amount of interface/mapping code; avoid a generalized plugin framework until
there is a second concrete implementation.

**Decision — local files, no database (recommended):** CSV/JSON artifacts are
sufficient for a 200–500-row laptop demo and directly meet the assignment's
output needs. The trade-off is that this is not a concurrent, durable,
production data platform.

## 5. Data flow: raw input to analyzed output

1. Generate seeded synthetic identities/records and retain defect labels and
   intended clean reference values separately from the API input.
2. Validate required columns, unique record IDs, address field types, and
   row count. Fail clearly on malformed input; do not silently drop records.
3. Create a canonical input record containing synthetic record ID and raw
   address components. Preserve the raw fields unchanged.
4. Call the verified Precisely address verification/standardization operation.
   Record status and returned standardized components only when present in
   the verified response mapping.
5. Geocode according to the confirmed API workflow. Preserve returned
   coordinates, quality/status metadata if documented, and PreciselyID only
   where actually returned and supported.
6. Apply documented eligibility criteria. Enrich eligible records with the
   selected 3–5 attributes, subject to verified products, access, credit
   limits, and returned-data availability.
7. Record source, stage status, timestamps, and synthetic/API/simulated
   provenance for each output value. Missing and failed values remain missing
   with an explicit reason/status; never substitute invented values.
8. Compute quality metrics from the input, intended synthetic ground truth,
   and actual pipeline results using declared denominators.
9. Run the focused AI workflow only on an allowlisted set of available
   enrichment/geocoding fields. Store generated narrative separately from
   source facts.
10. Export row-level before/after results and aggregate summary; prepare the
    mandatory business impact summary from measured results plus explicitly
    labelled assumptions.

## 6. Proposed repository and module structure

```text
precisely_project/
├── README.md
├── pyproject.toml                 # or requirements.txt; choose after checking repo conventions
├── .env.example                   # names/placeholders only; never credentials
├── .gitignore
├── main.py                        # minimal CLI entry point, if useful
├── src/
│   └── address_intelligence/
│       ├── __init__.py
│       ├── config.py
│       ├── models.py              # canonical typed contracts
│       ├── pipeline.py            # deterministic orchestration
│       ├── metrics.py
│       ├── reporting.py
│       ├── synthetic_data.py
│       ├── providers/
│       │   ├── precisely.py       # provider interface/mapping
│       │   └── llm.py             # focused model client interface
│       ├── workflows/
│       │   ├── enrichment.py
│       │   └── risk_narrative.py
│       └── insurance/
│           └── rules.py           # scenario rules, kept out of generic pipeline
├── data/
│   ├── input/                     # synthetic input only
│   └── output/                    # generated outputs; ignore run-specific files
├── tests/
│   ├── unit/
│   ├── fixtures/
│   └── integration/               # opt-in; requires credentials and budget gate
├── docs/
│   ├── PROJECT_PLAN.md
│   ├── ARCHITECTURE.md
│   └── BUSINESS_IMPACT.md
└── .github/                       # optional later, only if existing CI is appropriate
```

This is a proposed shape, not a requirement to create every file at once.
Prefer a small runnable package over a notebook-only design because reusable
interfaces and repeatable tests matter; optionally add a notebook only if it
materially improves the live demonstration.

## 7. API dependencies and verification steps

Precisely APIs are explicitly required for address validation, geocoding, and
enrichment. The assignment points to `developer.precisely.com` and the
`PreciselyData` GitHub organization. This plan does **not** assert particular
endpoint paths, authentication flows, product names, request/response fields,
SDK support, pricing, or per-operation credit use.

Before implementation or any API call:

1. Confirm access to the Precisely developer account and the stated trial,
   including current expiration date, available credits, and terms.
2. Locate current official documentation for address verification,
   standardization, geocoding, PreciselyID, and candidate enrichment
   products/attributes. Confirm product availability for this account and
   intended geography.
3. Confirm authentication method, credential scopes, base URL/environment,
   request/response examples, batch support, limits, timeout guidance, error
   codes, and rate-limit/`Retry-After` behavior.
4. Confirm how credits are counted for each operation, whether failed,
   repeated, batched, or enrichment requests consume credits, and how to
   monitor remaining credits. Do not infer this from request count.
5. Verify the supported workflow for linking standardization, geocoding, and
   PreciselyID. Map only fields actually documented and returned.
6. Verify attribute definitions, units/categories, data freshness/coverage,
   interpretation constraints, licensing, and whether values are available
   from the granted account.
7. Check whether the APIs permit synthetic test addresses and whether any
   restrictions apply. Do not send real policyholder data.
8. Record links, access outcome, date checked, and unresolved questions in
   implementation documentation; redact credentials and account identifiers.

**Decision — documentation-first integration (recommended):** avoid guessed
endpoints and speculative SDK use. The trade-off is that the provider adapter
cannot be finalized until access is confirmed; this prevents wasted trial
credits and incorrect claims.

### Transparent fallback

If credentials, product access, an attribute, or an API operation is
unavailable, the offline pipeline may use deterministic fixtures for
development and tests. Every fixture/simulated field must carry provenance
such as `synthetic_fixture` or `simulated`, be described in the README and
outputs, and never be presented as an actual Precisely result. The final
submission must disclose any unmet required API capability rather than claim
the end-to-end API requirement has been satisfied.

## 8. Data model and proposed output schemas

The canonical model is internal and provider-independent. Exact provider
fields are mapped only after official schema verification.

### Proposed input columns

- `record_id`: synthetic stable identifier; no real person identifier.
- `raw_address_line_1`, `raw_address_line_2`, `raw_city`,
  `raw_state`, `raw_postal_code`, `raw_country`.
- `injected_error_types`: generator-side evaluation label(s), not sent to
  provider unless a specifically justified experiment requires it.
- `expected_reference_id` or equivalent generator-side clean reference
  linkage, stored separately from provider output.

### Proposed result columns

- Original raw address columns (immutable).
- `standardized_*`: canonical address components actually returned.
- `verification_status`, `geocode_status`, `processing_status`, and
  explicit error category/message safe for output.
- `latitude`, `longitude`, coordinate/reference metadata only if documented
  and returned.
- `precisely_id`: only the provider-returned PreciselyID, if available.
- `enrichment_*`: one column per selected and verified attribute, with
  documented units/categories.
- `ai_narrative`, `ai_status`, and evidence/source references or field list.
- Provenance/status per stage and attribute, e.g. actual API, synthetic
  fixture, simulated, unavailable, or failed.

### Proposed summary outputs

- Row counts and denominators at each stage.
- Verification/match rate as defined from documented API status semantics.
- Standardization improvement measured against controlled injected errors
  and/or an explicit component comparison rule.
- Coordinate coverage and geocode success rate.
- PreciselyID coverage, if the verified API returns it.
- Enrichment completion by selected attribute and eligibility denominator.
- AI completion/abstention/error counts.
- API operation counts and known/verified credit use, where available.

Prefer a CSV with one record per row plus a separate summary CSV/JSON. An Excel
workbook with summary and detail tabs is also compatible with the assignment,
but adds a dependency and should be selected only if it improves the
executive review. Do not write unverified provider raw payloads into
distributable sample output.

## 9. Synthetic dataset strategy and controlled data-quality errors

Generate 200–500 records with deterministic seed and documented construction
rules. Keep a clean reference address and injected-error labels for evaluation
separate from the address submitted to APIs.

**Recommended strategy:** use synthetic person/policy identifiers and
synthetic policy context, while anchoring address examples to a small,
documented set of legitimate, non-residential/publicly usable test locations
or another source approved for API testing. Apply controlled perturbations to
those locations. This is more likely to produce meaningful API matches than
fully fictional addresses while avoiding real policyholder PII. Confirm that
the source addresses are permitted by provider terms. Do not imply those
locations belong to policyholders.

If appropriate test locations cannot be sourced or API terms are unclear,
use generated addresses for offline tests and clearly explain that API
resolution may be unavailable; do not fabricate API success.

Proposed defect categories directly follow the assignment:

- Character misspellings/transpositions in street names or suffixes.
- Missing apartment/suite values for selected multi-unit cases.
- Deliberate city/postal-code inconsistency.
- Outdated or non-existent address cases, labelled as intentionally
unresolvable where known.

Set and document an overall dirty-record percentage and per-category
percentages before generation; choose a reproducible distribution that
includes clean controls. Track overlapping defects explicitly so category
counts do not incorrectly sum to unique dirty records. Report actual generated
counts, not just target percentages.

## 10. Precisely API credit-budget strategy

The assignment states 2,500 trial credits valid for 30 days; actual current
account balance, expiration, and operation costs remain unverified. No
exploratory calls until per-operation credit accounting and product access are
confirmed.

1. Inventory required operations and record verified credit costs, batching
   behavior, and retry/failure billing rules from official docs/account
   visibility.
2. Estimate cost as the sum of operation-specific credits for the planned
   records, accounting for eligibility, retries, and any separate
   validation/geocoding/enrichment operations. Do not assume one request is
   one credit or that batching reduces credits.
3. Set a hard run limit below available credits. **Recommended initial
   reserve:** hold back at least 20% (500 credits of the stated 2,500) for
   unexpected billing, controlled re-runs, and final demonstration; adjust
   only after actual account rules are understood and record the reason.
4. Prefer one small, representative smoke test after budget calculation,
   then inspect result quality and actual balance before the full run.
5. Use deterministic inputs and persist completed stage outputs so a failed
   later stage does not require repeating successful operations. Re-run only
   records/stages that need it where provider semantics permit.
6. Limit enrichment calls to records eligible under documented rules and
   only to selected attributes/products. Confirm whether attributes are
   separately charged. For the raw-versus-verified location comparison,
   budget for roughly double enrichment calls on the comparison subset if
   each location lookup is charged; confirm actual billing and workflow in
   Phase 0, then size the subset.
7. Gate execution on an operator-configured maximum and display a clear
   estimate/confirmation before calls. Stop on unexpected credit accounting
   or rate-limit/error patterns; do not blindly retry.
8. If verified costs cannot fit the 200-record minimum/API scope within the
   budget, pause and disclose the constraint. Do not silently downgrade an
   API-required stage or represent fixtures as actual results.

The reserve percentage is a proposed safety policy, not a Precisely billing
fact. It can be revisited once official costs are known.

## 11. Enrichment selection criteria and access dependencies

Select exactly 3–5 attributes only after confirming current products, access,
coverage, semantics, terms, and credit accounting. Candidate themes from the
assignment include:

- Flood/natural-hazard zone or exposure indicator.
- Property type/building characteristics.
- Distance or proximity to fire station/emergency services.
- Neighborhood demographics.

**Recommended selection principle:** prioritize attributes with a clear
underwriting or claims-service decision, interpretable coverage, reliable
provenance, and demonstrated account availability. Prefer hazard and
property/emergency-response context over neighborhood demographics for this
prototype. Demographic variables can introduce fairness, legal, and
proxy-discrimination concerns in insurance decisions; do not use them for
individual risk scoring or pricing absent explicit business/legal approval
and a defensible governance basis.

For each selected attribute, document: exact verified field/product name,
definition, units/categories, geography and freshness, missingness, stage
eligibility, API source/date, credit impact, intended business question, and
interpretation limitations. Do not infer or fabricate an attribute when it
is missing. If fewer than three suitable attributes are accessible, document
the unmet requirement and ask for a decision on an authorized alternative;
do not rename unrelated data to make the count appear satisfied.

## 12. AI workflow design, inputs, outputs, guardrails, and evaluation

### Recommended workflow: evidence-grounded underwriter risk narrative

This is one focused AI capability, not an autonomous agent. Given a single
synthetic record's verified geocoding and available, allowlisted enrichment
attributes, generate a two- or three-sentence plain-English summary for an
underwriter. It should help a human understand relevant location context and
what evidence is missing; it must not decide price, eligibility, claims, or
coverage.

**Inputs**

- Synthetic record ID (not personal identity).
- Only successfully returned, allowlisted geocoding/enrichment values and
  provenance.
- Explicit null/missing indicators and any deterministic review flags.
- Optional controlled prompt/version metadata.

**Outputs**

- Short narrative, structured status, and evidence field references.
- Abstention/insufficient-evidence result when no supported narrative can be
  formed.
- Model/provider and prompt version for reproducibility; no claim that the
  output is verified fact.

**Guardrails**

- No real policyholder PII or secrets sent to an LLM.
- Never ask the LLM to infer a hazard zone, building attribute, risk score,
  loss probability, premium, or causal relationship absent source evidence.
- Constrain output to supplied facts; distinguish source facts from
  interpretation and state material uncertainty.
- No demographic input for individual underwriting narrative/scoring.
- Validate structured response shape and length; treat malformed output as
  a surfaced failure, not a successful fallback.
- Require human review; label the output as decision support, not a decision.
- Store prompts/results carefully and avoid logging full records.

**Provider choice:** first compare the assignment's listed free-tier options
(Gemini, Groq, Hugging Face Inference API, or local Ollama) for access,
current free-tier constraints, data handling/privacy terms, and laptop
compatibility. Do not claim a free tier, data-retention policy, or model
capability without checking current terms. A local model reduces external
data transfer but may require more laptop resources and may be less reliable;
a hosted free-tier service may be easier to demo but adds account, quota,
privacy, and availability dependencies.

**Evaluation:** use a small hand-reviewed set of actual or clearly labelled
fixture records. Check that every factual clause is supported by an input
field, missing evidence is not invented, the summary is understandable, and
abstention works. Record qualitative review outcomes and failure cases; do
not present a tiny subjective evaluation as model validation.

## 13. Testing and validation strategy

- **Synthetic-data tests:** row count bounds, deterministic seed, unique IDs,
  intended dirty rate, each required defect type, clean controls, and
  correctly counted overlaps.
- **Input/contracts tests:** required fields, types, nulls, invalid
  coordinates, canonical mapping, immutable raw data, and serialization.
- **Provider adapter unit tests:** sanitized recorded fixtures for success,
  partial response, documented errors, timeout, rate limit, and malformed
  response. Fixtures are not evidence of live API access.
- **Pipeline tests:** stage ordering, eligibility, partial failures,
  rerun/resume behavior, no silent substitution, and provenance tracking.
- **Metrics tests:** denominator definitions, zero eligible rows, missing
  values, and before/after comparisons against known synthetic ground truth.
- **AI tests:** supported claims, abstention, missing/contradictory inputs,
  invalid output, and no leakage of non-allowlisted fields.
- **Integration smoke tests (opt-in):** use credentials only after API checks
  and credit approval; very small representative sample, measured balance,
  explicit operator invocation, no API-dependent default test suite.
- **End-to-end acceptance:** run from documented local commands on the
  synthetic data, verify output schema and summary totals, inspect
  representative records, and record actual results/limitations.

Only use test/build/lint tools already present in the repository or selected
with explicit approval. Do not add a framework solely for the plan.

## 14. Security, configuration, logging, and error handling

- Store API/LLM secrets in environment variables or a local ignored `.env`;
  provide only placeholder variable names in `.env.example`.
- Verify `.gitignore` covers `.env`, local output, caches, virtual
  environments, and generated secrets before any credentials are used.
- Use synthetic data for all external calls and demonstrations.
- Do not commit credentials, private account data, or sensitive provider
  responses. Review outputs before repository publication.
- Keep configuration explicit: provider selection, input/output paths,
  timeouts, retry limits, run/credit cap, enrichment selection, and AI model.
- Use structured logs for stage, record counts, elapsed time, and sanitized
  error categories; avoid raw addresses, tokens, full payloads, and prompts
  containing sensitive data.
- Apply finite timeouts. Retry only documented transient conditions with
  bounded backoff and respect server-provided retry guidance when documented.
  Do not retry validation/authentication errors indiscriminately.
- Represent each stage as success, partial, skipped (with reason), or failed;
  surface actionable errors and preserve completed work where safe.
- Do not add broad exception handlers or success-shaped defaults.
- No automated underwriting decisions; preserve a human-review boundary.

## 15. Before/after measurement framework

Define formulas only after confirming API status semantics and output fields.
Use the full input row count as the top-level denominator and report eligible
and attempted counts separately.

- **Verification/match rate:** records meeting the documented success/match
  definition divided by records attempted, with not-attempted/failed rows
  reported separately.
- **Standardization improvement:** compare input defects to the clean
  synthetic reference using an explicitly defined component-level
  normalization/comparison rule. Report both the proportion corrected and
  cases unresolved; do not assume provider response equals ground truth.
- **Coordinate coverage:** records with valid returned coordinate pairs
  divided by total input and by geocoding attempts; identify partial or
  low-quality results only using documented metadata.
- **Raw-versus-verified enrichment comparison:** for the same comparison
  records, compare selected verified enrichment results using raw and
  standardized/geocoded locations. Measure the share whose hazard category
  changes when a verified hazard attribute is available. If no hazard
  attribute is available, report post-verification geocode displacement as a
  clearly labelled proxy, not a hazard measure.
- **Persistent identifier coverage:** records with an actually returned
  PreciselyID divided by geocoding attempts, only if supported.
- **Enrichment completeness:** non-missing values per selected attribute,
  separately over eligible records and all input records.
- **AI usability:** narrative/abstention/error counts and human-reviewed
  evidence-support results.
- **Data integrity:** input rows, output rows, duplicates, dropped records
  (target zero), and explicit stage failures.

Show actual counts beside percentages, version the run, state limitations, and
do not conflate synthetic ground truth, API result status, or model output.

## 16. Business impact methodology and assumptions

The assignment requires two quantified business risks framed in cost/revenue
terms. No carrier cost, loss, premium, claim, or dirty-rate baselines are
provided. Therefore, the prototype must not invent realized savings or claim
causality.

Build the mandatory one-page `docs/BUSINESS_IMPACT.md` summary, with an
optional PDF export. It must state the business problem, quantify the two
risks below, explain how the proposed solution mitigates each, and explain the
incremental value of AI beyond enrichment. Use inputs sourced from the
assignment, measured prototype, or clearly labelled assumptions. Prefer
carrier-provided operational figures if supplied later.

1. **Claims misrouting and rework cost:** annual claims × address-defect share
   observed in the prototype × cost per misrouted claim. The cost per
   misrouted claim is assumed, must be labelled, and must have its source
   stated. Explain how verified locations can reduce misrouting/rework; do not
   claim elimination or realized savings without measurement.
2. **Risk-zone misassignment and revenue exposure:** policies × share whose
   hazard category changes after verification × average premium differential.
   Measure the category-change share by enriching raw versus verified
   locations for the same comparison records. The average premium
   differential is assumed, must be labelled, and must have its source
   stated. Underpricing can create premium leakage/adverse selection;
   overpricing can reduce competitiveness and retention. Do not claim actual
   mispricing or revenue impact without carrier pricing data and validation.

If no verified hazard attribute is available, use geocode displacement after
verification as a clearly labelled proxy for location change; do not present
it as a measured hazard-category change.

Show formulas, source/value/type for each input, a conservative range or
sensitivity table, and the difference between:

- prototype-observed rates on 200–500 synthetic records;
- scenario assumptions for a fictional 300k+ portfolio; and
- business outcomes that would require a controlled pilot and carrier data to
  measure.

AI value statement should describe what it enables—turning several
evidence-backed fields into a concise, reviewable explanation—not imply that
AI itself improves risk accuracy or replaces underwriting judgment.

## 17. Implementation phases, dependencies, and acceptance criteria

All phases are gated by review of this plan. No application implementation
begins until the owner approves the plan and the required API access path is
understood.

| Phase | Work | Dependencies | Acceptance criteria |
|---|---|---|---|
| 0. Plan and access discovery | Approve this plan; inspect official Precisely docs/account, candidate enrichment products, trial credits, LLM terms, and repository visibility constraints. | Owner plan review; account access. | API verification checklist completed or blockers recorded; no speculative API calls; visibility approval remains separate. |
| 1. Minimal foundation | Add project setup, canonical contracts, configuration, safe logging, and reproducible local command. | Phase 0; approved module scope. | Clean local setup; secrets excluded; offline tests run. |
| 2. Synthetic data and baseline | Generate 200–500 seeded records, controls and documented noise; define metrics. | Phase 1. | All required defect categories present; dirty percentage/count documented; tests confirm generator and baseline. |
| 3. Precisely address and geocoding | Implement verified API adapters, bounded safe handling, standardization, geocoding, and PreciselyID mapping only if documented/returned. | Verified US API/products/account access; credit estimate and owner approval. | Small live smoke test passes within budget; output provenance/status correct; full 200-record run is credit-feasible or blocker disclosed; comparison subset credit estimate includes raw-versus-verified enrichment where supported. |
| 4. Enrichment | Select and implement 3–5 verified attributes for eligible records; compare raw versus verified location enrichment on a subset when the selected hazard attribute is available. | US product access, semantics, terms, credits, Phase 3 data, and comparison-subset size set after Phase 0. | Each field has documented definition/source; partial/missing data is represented honestly; eligible-row coverage and raw-versus-verified hazard-category changes (or labelled geocode-displacement proxy) are reported. |
| 5. Focused AI workflow | Implement evidence-grounded narrative with selected free-tier/local option and safety tests. | Enrichment result shape; chosen provider/privacy review. | Tested output and abstention; factual claims tied to input fields; no unsupported decisions. |
| 6. Reporting and impact | Produce before/after detail/summary and the mandatory `docs/BUSINESS_IMPACT.md` summary, with optional PDF export. | Real run artifacts or clearly labelled fallback; metric definitions and transparent financial inputs. | Required metrics and outputs present; two cost/revenue risks quantified; solution mitigation and AI's incremental value explained; assumptions separated from measurements; one-page summary is clear to a non-technical executive. |
| 7. Final validation and submission | Re-run from clean setup; review artifacts/secrets; verify reviewer-facing documents and required outputs. | Phases 1–6. | README/run steps reproduce output; tests and integration results reported accurately; mandatory artifacts included; visibility approval remains separate. |

**Phase 7 submission hygiene:** internal planning documents, including this
file, are not submitted. Reviewer-facing documents are `README.md`,
`docs/ARCHITECTURE.md`, and `docs/BUSINESS_IMPACT.md`. The public repository
must present a clean history; decide the approach in Phase 7, without
rewriting history or publishing before explicit approval.

### Quality objectives

- **Technical quality:** clean modular code, reproducible run, tested
  provider integration, and honest error/coverage handling.
- **Business value:** cost/revenue risk scenarios with transparent
  assumptions and actionable VP summary.
- **Enrichment relevance:** verified location context with a clear
  underwriting/service decision and access-dependent rationale.
- **AI quality:** functioning, grounded workflow, evidence checks,
  abstention, and limitations.
- **Communication:** accessible before/after evidence, README, and mandatory
  one-page business impact summary that makes the problem and proposed
  business value clear to a non-technical executive.

### Schedule

Deadline: Nov 6. Target submission: Nov 4. Nov 5 is reserved as a buffer.

| Date | Planned work |
|---|---|
| Oct 27–28 | Phase 0 and plan cleanup. Phase 0 account/portal access and verification are performed by the owner. |
| Oct 28 | Phase 1: minimal foundation, after the Phase 0 access gate clears. |
| Oct 29 | Phase 2: synthetic data and baseline. |
| Oct 30 | Phase 3: smoke test, only after the owner approves the credit estimate. |
| Oct 31 | Phase 3: full run, within the approved credit budget. |
| Nov 1 | Phase 4: enrichment and raw-versus-verified comparison. |
| Nov 2 | Phase 5: focused AI workflow. |
| Nov 3 | Phase 6: reporting and business impact summary. |
| Nov 4 | Phase 7: final validation and target submission. |
| Nov 5 | Schedule buffer. |
| Nov 6 | Deadline. |

If Phase 0 slips, later dates slip accordingly; do not bypass access or credit
verification to preserve the target dates.

## 18. Risks, mitigations, and unresolved questions

| Risk / question | Mitigation or decision gate |
|---|---|
| Precisely trial access, APIs, products, or attributes are unavailable or differ from assumptions. | Verify official current docs and account access first; implement only verified mappings; disclose unmet requirements. |
| API credit use is higher than expected or differs by operation. | Verify credit accounting; estimate before calls; reserve credits; enforce explicit cap; do not exploratory-call blindly. |
| Synthetic/altered addresses fail to resolve. | Use permitted, non-residential/public test locations as controlled anchors where available; retain clean controls and unresolvable cases; disclose limitations. |
| 200–500 synthetic records are not representative of the fictional 300k+ portfolio. | Report sample evidence only; avoid extrapolating observed rates as carrier results; use labelled scenario assumptions. |
| Enrichment attributes are unavailable, incomplete, or unsuitable for underwriting. | Gate on account/product/coverage verification; select only decision-relevant fields; transparent fallback and limitation disclosure. |
| Demographic data or other proxy variables raise fairness/legal concerns. | Prefer physical hazard/property/service context; exclude demographics from individual risk scoring; require governance/legal review for any later use. |
| Hosted LLM free tier, availability, retention, or privacy terms change. | Verify current terms and quotas; use synthetic data only; keep provider swappable; consider local model if feasible. |
| LLM fabricates or overstates conclusions. | Allowlist evidence, require grounded output, test abstention, human review, and label generated narrative. |
| Network or record-level API failures leave partial outputs. | Explicit statuses, bounded retries, persist stage results, and report incomplete stages without silent substitutions. |
| Public-repository requirement conflicts with current private repository and user publication controls. | Keep repository private until explicit approval to change visibility; review artifacts for secrets and permitted data before publishing. |
| Assignment's public GitHub requirement and current `feature/v1` branch workflow may affect final submission. | Work only on `feature/v1`; never modify `main`; no commit, push, publication, or history changes without explicit approval. |
| The exact financial inputs for the VP summary are absent. | Use a transparent assumption table/ranges or request approved carrier baselines; do not present invented values as fact. |

### Unresolved questions for later implementation

1. Is Precisely trial access active, and what are the confirmed expiration date,
   credit balance, operation costs, limits, and permitted test-data rules?
2. Which current Precisely products/fields are actually enabled for this
   account and geography, including a supported PreciselyID workflow?
3. Which 3–5 attributes are both available and acceptable for the intended
   underwriting demonstration?
4. Which free-tier/local LLM option meets current access, privacy, quota, and
   laptop constraints?
5. Are carrier-approved cost-per-reroute, review-cost, or other financial
   assumptions available, or should the impact summary use explicitly
   illustrative ranges?
6. May the GitHub repository be changed from private to public for final
   submission, and may the final artifacts be pushed/published?

## 19. Final submission checklist

- [ ] Public GitHub repository contains all approved submission artifacts;
  repository visibility changed only with explicit owner approval.
- [ ] README explains purpose, local setup/run, dependencies, `.env` variable
  names, sample outputs, data provenance, API caveats, and limitations.
- [ ] No secrets, real personal data, unapproved API payloads, or sensitive
  account details are committed.
- [ ] Reproducible synthetic CSV contains 200–500 records; defect injection
  types, counts, dirty percentage, and seed are documented.
- [ ] Verified Precisely address verification/standardization and geocoding
  results are included, or unmet live API access is prominently disclosed.
- [ ] PreciselyID is included only if genuinely supported and returned.
- [ ] Three to five verified enrichment attributes are included for eligible
  records, or any inability to meet the requirement is explicitly disclosed.
- [ ] Before/after output and summary metrics include match/standardization
  measures and coordinate coverage with clear denominators.
- [ ] API/fixture/simulated/synthetic provenance is visible and unambiguous.
- [ ] One focused AI capability is functional, tested, and evidence-grounded;
  limitations and human-review boundary are stated.
- [ ] Mandatory one-page VP impact summary communicates the business problem,
  quantifies two cost/revenue risks, explains how the solution mitigates them,
  and states AI's incremental value beyond enrichment; measurements and
  assumptions are clearly distinguished; deliver as `docs/BUSINESS_IMPACT.md`
  with optional PDF export.
- [ ] Setup and offline tests run from documented instructions; actual test
  and live integration results are accurately reported.
- [ ] Submission hygiene: internal planning documents, including
  `docs/PROJECT_PLAN.md`, are not submitted. Reviewer-facing documents are
  `README.md`, `docs/ARCHITECTURE.md`, and `docs/BUSINESS_IMPACT.md`. The
  public repository must present a clean history; decide the approach in
  Phase 7 without rewriting history or publishing before approval.
- [ ] No commit, push, publication, branch deletion, or history change occurs
  without explicit approval.

## 20. Optional enhancements, prioritized by business value and effort

| Priority | Enhancement | Business value | Effort / reason to defer |
|---|---|---|---|
| 1 | Small local demo UI (Streamlit or Gradio) showing selected records, status, and before/after evidence. | Makes the customer story interactive. | Optional in assignment; add only after the pipeline is stable. |
| 2 | Scenario/sensitivity controls for dirty rate, reroute cost, and review cost. | Makes assumptions and potential impact transparent to executives. | Moderate effort; a simple documented spreadsheet/table may suffice. |
| 3 | Alternate provider or fixture adapter. | Demonstrates reusability and supports offline development. | Useful only if it does not distract from the required Precisely integration. |
| 4 | Additional AI evaluation set and reviewer rubric. | Improves confidence in narrative grounding and consistency. | More review effort; not a substitute for production model validation. |
| 5 | Larger-volume performance experiment up to the fictional carrier scale. | Helps frame operational scaling questions. | Not required; synthetic prototype metrics do not establish production throughput. |
| 6 | Automated CI checks. | Repeatable quality gates. | Add only if repository and available tooling justify the setup. |

The core deliverable remains a small, tested local pipeline with verified
external integrations, transparent evidence, and the mandatory, clear
executive business impact summary.
