# Future scale and product strategy

## Architectural direction

The personal-agent foundation can become a business product without immediately replacing PostgreSQL, adding mandatory Redis/Celery or building a knowledge graph. Preserve scenario = methodology, task = schedule/reaction/targets, grouping = read-time projection; authorization and budget admission stay outside LLM decisions.

Use progressive delivery: **managed pilot → repeatable B2B service → self-service SaaS**. A single VPS is a starting topology, not a promise of availability or an eternal infrastructure constraint. Availability requirements, measured bottlenecks and customer value decide the next architecture.

Baseline and evidence: [proposal review](proposal_review.md), dev `f11acefba8fbc2f47e3a17f670a97587208f1dd3`. These are deferred proposals; nothing below is claimed implemented.

## Deferred initiatives with entry criteria

| ID | Initiative | Entry criterion | Design / exit criterion |
|---|---|---|---|
| FUT-01 | One embedding adapter for dedup, chains and optional memory | Pilot data demonstrates costly near-duplicates, inaccurate lexical chains or failed KV recall. PRD-03/06 are complete. | Benchmark each consumer separately. Preserve exact hash first, measure false merges/suppression and negations/dates/names; tenant-isolated retrieval. Compare bounded SQL/application similarity with pgvector at actual volume. Decide extension, dimensions/model versions, retention/backfill and migrations. Embedding spend counted; re-embedding plan exists. No <200 ms claim without p95 measurements. |
| FUT-02 | Data-driven memory and correction proposals | Owners use memory control and repeated failures justify better recall/hygiene. | Define what counts as access (not every automatic snapshot), confidence/TTL and protected facts. Extend existing reflect, review destructive ops, no new decay daemon. Failures captured redacted and tenant-scoped; corrections/blacklist suggestions never become self-authorizing instructions. Schema additions require approval. |
| FUT-03 | Statistical alerts and threshold calibration | Source freshness and safe scoped delivery are proven; customers want timely alerts beyond scheduled reports. | Baseline window, min sample, seasonality/no-data handling, rate/cooldown, quiet hours and explanation. Digest's 0.02 delta threshold is not automatically a z-score threshold. Reuse existing notification types where adequate; any new enum/migration is deliberate. Measure precision/usefulness and alert burden. |
| FUT-04 | Model probes, circuit breaker and optional tracing | Real failure history warrants probes; in-DB metrics cannot answer a defined support question. | Bounded operator-owned probes, hysteresis/recovery and explicit 4xx/429 policy. Shared fleet changes require platform rights. Optional tracing redacted with retention/data-transfer review; unavailable tracer never breaks runtime. No mandatory vendor/service. |
| FUT-05 | Verified source discovery and collection expansion | Supported first-value workflow works; actual customers need additional sources/history/platforms. | source_suggest candidates validated against platform APIs/access; no guessed links. MAX collection is separate from MAX messenger transport and needs a client/ingest contract, dedup, credentials/rate limits and tests. Browser L3 remains last resort with platform/legal and operational review. |
| FUT-06 | Payments and self-service subscriptions | Repeatable paid B2B economics/support exist and self-service demand is demonstrated. | Choose B2B contracts/manual invoicing versus Telegram Stars/web provider based on buyer/channel/legal constraints. Payment/order ledger, trusted callback/update verification, idempotency, refund/cancel/expiry, entitlements and reconciliation. Telegram payments may arrive through existing long polling; do not mandate a webhook just because research says so. Plan rows alone are not billing. |
| FUT-07 | Extension/plugin surface | More than one real integration cannot fit current tools cleanly. | Explicit permissions, tenancy, credential isolation, tool schemas, execution budgets, upgrade policy and signed/approved code. Do not put procedural skill_steps on AgentScenario or let arbitrary customer code run in the shared process. |
| FUT-08 | Capacity/availability evolution | Load testing shows a specific bottleneck or the SLA requires resilience beyond one host. | Optimize SQL/indexes/pagination first. Separate API and bounded workers with one scheduler/listener owner; test DB connection budgets and fair tenant scheduling. Add standby/managed DB for recovery goals. Introduce cache/materialized views only with measured query demand and tenant-safe invalidation; use a broker only for demonstrated queue limits. WebSocket/SSE only for measured live-update UX. No wholesale microservice rewrite. |
| FUT-09 | Optional raw retention/reanalysis | A customer has an explicit audit/reparse need that cannot use lawful refetch and accepts the processing policy. | Purpose-bound opt-in, time limit, access/deletion/encryption, storage budget and backup expiry. Price and document the limitation. Audit hashes are not replayable text; paying for Business is not sufficient permission to retain personal data indefinitely. |

## Not launch blockers; not permanently forbidden

PDF/Excel export can be a bounded local feature with no new infrastructure. Caching and materialized views are engineering options, not contradictions by themselves. Rebranding is a product decision, not reliability work. New messenger channels/mobile apps should wait for demand rather than dilute supported collection quality.

Knowledge graphs and an autonomous LLM scheduler are rejected as baseline mechanisms: current business workflows do not demonstrate a need that outweighs complexity/risk. Reopen only through an explicit architecture decision with alternatives and evidence.

## Capacity and economics experiment

Define tenant counts, sources per tenant, posts/day, media mix, history windows, report frequency, peak concurrent chats and provider latency. Measure total billed attempts (including failure/fallback), DB growth, pool saturation, oldest-job age, delivery lag and p95 UI latency. Include one noisy tenant and provider outage; evaluate fair scheduling and recovery.

Do not size the product solely by number of users or introduce “unlimited Business” without abuse/resource controls. Publish explicit fair-use limits and supported workload. Exact availability promises and tariff margins require these measurements, not analogy with competitors.

## Decisions that need the owner's input before implementation

1. First buyers/use cases and minimum platform/history coverage; is MAX collection essential to sell the pilot?
2. Managed deployments versus shared hosted service; data geography and operator access requirements.
3. Business budget and margin policy, especially whether Business is capped or truly unlimited and who pays for diagnostics.
4. Reliability/freshness/recovery objectives and willingness to fund resilient DB/hosting.
5. Whether any live commenting is required for the first release; otherwise keep it disabled.
6. Commercial channel, legal/privacy terms and support ownership before payment automation.

Do not block the safe documentation/security triage on these answers. Select optional product/scale work after the first pilot evidence.
