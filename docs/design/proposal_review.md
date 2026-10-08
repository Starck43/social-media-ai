# Proposal review: research versus the current product

## Scope and evidence

Reviewed on 2026-10-08 against **dev `f11acefba8fbc2f47e3a17f670a97587208f1dd3`**. This is a point-in-time review, not a production certification. Later dev commits must be checked before implementation.

Inputs: [best experiences research](analysis_and_best_experiences.md), [competitive analysis](competitive_analysis.md), and the [archived integrated roadmap](archive/roadmap_integrated_legacy.md). Competitor names are inspiration, not verified benchmarks: the supplied research does not provide a reproducible source list. No competitor performance or adoption claim was independently verified here.

Method: read the three inputs; inspect implementation, registered tools/handlers, models, migrations, deployment artifacts, relevant tests and current documentation. Code wins over historical prose. Tests being present does not mean they were executed in this review.

Status vocabulary:
- **Present**: a relevant implementation was inspected, not a guarantee that all edge cases are solved.
- **Partial**: foundation exists but the proposed user outcome is incomplete.
- **Not found**: no implementation found in the inspected subsystem/registries; not an exhaustive absence proof.
- **Reframe**: keep the goal but change the proposed mechanism.
- **Defer / reject**: postpone with a trigger, or reject the mechanism under the current architecture.

Routes forward: [local experience](../LOCAL_EXPERIENCE_PLAN.md), [production gates](../BUSINESS_PRODUCTION_READINESS.md), [future scale](future_scale_strategy.md), [execution order](../ROADMAP_INTEGRATED.md).

## Executive decision

Keep the deterministic pipeline and PostgreSQL queue. Complete a safe, observable, repeatable **managed business pilot** before adding embeddings or payment automation. Multi-tenancy is a useful foundation, not proof of secure self-service SaaS. “One VPS” is an initial deployment shape, not a permanent ban on growth or useful exports.

The old roadmap's first four items are substantially implemented. More urgent launch work is missing from that roadmap: tenant-safe delivery, fail-closed authorization, complete spend accounting, a runtime-aware deployment, honest outcome reporting and verified recovery.

## Proposal disposition matrix

IDs below identify decisions in this review; original competitive matrix numbers are retained for traceability. Effort is not estimated as a fixed S/M/L: scope and acceptance must be agreed on fresh dev first.

| ID / original proposal | State and evidence | Decision / next destination |
|---|---|---|
| R01 / CA-01 structured analysis outputs | Present: `json_schema_builder.py::validate_with_pydantic`, three client parsing paths use strict local validation; analyzer revalidates. Provider-native strict protocols are not implemented. | Do not rebuild. Extend typed contracts to digest/learn/reflect separately; PRD-07. |
| R02 / CA-02 injection defense | Present: `prompt_sanitizer.py::frame_untrusted_text` is used in default and custom text prompts; system-owned text cannot be replaced by task payload/scope. | Retain; test additional languages, multimodal metadata, narrative and memory boundaries. Regex cannot guarantee prevention; PRD-07. |
| R03 / CA-03 reproducibility | Present: analyzer writes detached configuration/methodology and per-stage rendered prompt hashes into `response_payload.request` without DEBUG. No historical backfill; no latency field in this snapshot. | Add a safe audit viewer first, timing/correlation second; UX-05 / PRD-08. Hashes are not a raw-data replay archive. |
| R04 / CA-04 strategy/default enforcement | Present: active-provider/capability-aware `resolve_default_model`; tariff-first economical routing, fleet-default quality routing. Chat uses economical preference; digest uses quality defaults. | Preserve one resolver. No new selector/writer role column or compulsory extra LLM stage. Measure quality before changing routing. |
| R05 / CA-05 probes and model health | Partial: reactive fallback, usage/failure counters and manual/model-test tool exist; no probe handler in HANDLERS. | Bounded operator probes and degradation notifications after complete accounting. Prefer cooldown/circuit breaker over globally disabling a shared model on one failure; PRD-08 / FUT-04. |
| R06 / CA-06 semantic dedup | Exact hashes exist; embedding service not found in AI services. | Defer; benchmark false suppression and economics first. Do not erase distinct opinions/events merely because they are similar; FUT-01. |
| R07 / CA-06 semantic chain matching | Present deterministic topic_hint lookup/relinking, normalized labels and genuine token-set overlap; not merely “PARTIAL/not visible”. | Preserve/test current resolver. Optional embedding comparison only after a labeled chain benchmark; FUT-01. |
| R08 / CA-06 semantic memory | KV/provenance/snapshots exist; semantic retrieval not found in tool registry. | Defer additive retrieval; one shared embedding adapter, explicit tenant scope and retention; FUT-01. |
| R09 / CA-07 memory decay/eviction | Reflect already updates/deletes global-scope facts. Access/TTL telemetry is not in AgentMemory. | Reframe: transparent memory control first, measured access semantics later. Reuse reflect, no separate nightly decay job; UX-08 / FUT-02. |
| R10 / CA-08 automatic error learning | `/bad`, persisted tool messages and reflect advice exist; automated failure-to-correction flow not found. | Capture minimal, redacted evidence; reviewable advice, not self-authorizing instruction changes; FUT-02. |
| R11 / CA-09 anomaly notifications | Job-result notifications and digest deltas exist; aggregate anomaly handler not found. | Add opt-in alerts only after source freshness and scoped delivery. Minimum sample/baseline, cooldown, evidence, quiet hours; FUT-03. |
| R12 / CA-10 AI source suggestions | Existing scenario presets, prompt suggestion and onboarding route; source-discovery tool not found. | Improve current onboarding first. Suggestions must have verified URLs/access, not hallucinated LLM channel lists; UX-01 / FUT-05. |
| R13 / CA-11 bilingual output | `agent_style.language` already exists. Digest template/titles are Russian. | Propagate existing preference; no duplicate Tenant.language field; UX-04. |
| R14 / CA-12 per-task/scenario costs | Different cost fields exist, but no complete per-call/task attribution. Analysis costs are cents, chat/digest/jobs use USD; analysis rows are overwritten on update. | Reframe: truthful attributed/unknown costs first; complete budget ledger is PRD-03. Existing field aggregation alone is not billing-grade. |
| R15 / CA-13 subscriptions/Stars | Plan tiers exist; payment handler/ledger not found in inspected registries. | Product/legal decision and separate payment design after production gates. A managed B2B pilot can use an agreed manual commercial process; FUT-06. |
| R16 / CA-14 knowledge graph | Not needed for current source/theme workflows; entities/chains already exist. | Reject as baseline requirement; reopen only for demonstrated multi-hop customer questions. No invented “80% value” estimate. |
| R17 / CA-15 plugins / skill_steps on scenario | Tool registry exists; scenario is methodology, task owns execution/reaction. | Reject skill_steps on scenarios. A future extension API needs a security model, not renamed scenario JSON; FUT-07. |
| R18 / CA-16 Redis/materialized views | PostgreSQL queue and SQL/JSON aggregations exist. | Defer on measurements, not ideological rejection. Index/query optimization first; cache is not a reason to replace the queue; FUT-08. |
| R19 / CA-16 WebSocket | Existing HTMX/polling-style web flow does not require it. | Defer until a measured live-update need; never a launch blocker; FUT-08. |
| R20 / CA-16 PDF/Excel export | Independent customer deliverable, not inherently contrary to one VPS. | Reconsider as business value. Scoped, redacted, bounded on-demand export may fit locally without new services; UX-10. |
| R21 / algorithmic pre-filter | `handle_analyze` runs the analyzer before `should_analyze`; current triggers decide reactions after the LLM work. | Separate collection/analysis eligibility from action triggers. Opt-in, preview skipped content/reasons, preserve recoverability; UX-09. No asserted 70% saving. |
| R22 / hybrid digest / cheap selector + writer | Algorithmic brief already does selection/aggregation without LLM; one narrative step. | Keep. An extra model stage is an experiment, not automatic improvement; benchmark quality and total cost. |
| R23 / prompt_advice_apply | Reflect produces advice; no dedicated apply tool found. | Offer a targeted diff and use existing scenario_update confirmation; no silent rewrite or ambiguous target. UX-07. |
| R24 / trigger_calibrate | Rule descriptions/evaluation exist; calibration tool not found. | Read-only suggestion over enough observations, scoped dates and rollbackable task_update; UX-07 / FUT-03. |
| R25 / recommend grouping | Compact grouped reporting and analytics_chains tools exist. | Explain/recommend from existing aggregates; no automatic task changes or new LLM stage. UX-03. |
| R26 / hash-guided reanalysis | Audit hash exists; raw items retire after successful analysis. | Reframe as eligibility preview: verify retrievable raw content or lawful source refetch, estimate budget and confirm. A hash alone cannot recreate deleted text; UX-05. |
| R27 / warning at 80% spend | Partial spend metric exists, threshold notification not found. | Only after PRD-03. Deduplicate once per threshold/UTC day and show excluded/unknown costs until fixed; UX-06. |
| R28 / suggest blacklist/whitelist edits | Guards and task lists exist; automatic suggestion workflow not found. | Optional reviewable suggestions, evidence and confirmation; never infer permanent bans solely from one model label; FUT-02. |
| R29 / JSON instruction detection | `_ensure_json_instruction` uses JSON-structure regex, not just the words “json/format”. | Old criticism is stale. Prefer an explicit output contract eventually, but do not add another heuristic. |
| R30 / force flags | Force collection, reanalysis and digest resend have distinct meanings and multiple paths. | Human-readable preview of window, refetch, overwrite, cost and delivery; contract tests across web/CLI/chat. UX-02. |
| R31 / raw payload retention | collected_items is write-ahead staging; retirement/failed attempts/pruning exist. DEBUG can retain fuller traces. | Reject permanent raw retention by default. Any opt-in needs purpose, lawful basis, TTL, access/deletion and secret redaction; business tier alone is not consent. PRD-06 / FUT-09. |
| R32 / optional Langfuse tracing | Audit/tool transcripts and health counters exist; no Langfuse integration found. | Existing in-DB audit/timing first; external tracing optional and redacted, never copying full prompts by default. PRD-08 / FUT-04. |
| R33 / blanket Pydantic for all LLM calls | Main analysis is validated; learn/reflect still use tolerant extract_json; digest reads an unchecked summary shape. | Add typed contracts at write/side-effect boundaries, bounded failure behavior and retention safety; PRD-07. “No regex anywhere” is not itself a customer outcome. |
| R34 / prompt sanitization of owner chat | The material high-risk path is third-party content. Owner chat also needs tool authorization and bounded arguments. | Do not censor legitimate owner requests by keyword alone. Treat external content/memory as data; enforce permissions outside LLM; PRD-02/07. |
| R35 / template onboarding | Five scenario presets and `/app/onboarding` exist; checklist currently treats any task as completion. | Complete first successful collect → analyze → preview → delivery, using existing writes; no parallel wizard engine. UX-01. |
| R36 / rebranding | Research checklist adds repo/package/image renaming without a user requirement. | Not a readiness gate. Defer until product identity and compatibility/deployment consequences are decided. |
| R37 / RU/EN, <200 ms recall, ≥30% savings | Goals in research; not measurements from this code review. | Define representative datasets, workload, p95 and quality/recall trade-offs. Do not publish them as achieved performance. |
| R38 / “VK + Telegram + MAX monitoring complete” | MAX transport for chat/delivery exists; social client factory maps only VK/Telegram. | Publish a capability matrix, not a three-platform parity claim. MAX collection is separate scope; UX-01 / FUT-05. |
| R39 / “reactive only” and “manual test only” | Existing scheduler, job notifications, llm_model_test tool and learning already provide automation. | Identify missing aggregate anomaly detection/probes specifically; do not rebuild existing automation. |
| R40 / hosted SaaS out of scope forever | Historical personal-agent framing conflicts with the new business objective. | Adopt progressive scope: managed pilot → repeatable B2B service → self-service after billing/support/security gates. FUT-06/08. |

## Newly identified launch risks

These supersede the old “embeddings next” sequence. Details and acceptance are in the production plan.

| ID | Inspected evidence | Why it matters |
|---|---|---|
| PRD-01 | `channels/registry.py::broadcast_digest` always sends to env targets before tenant targets. `builder.py::build_and_publish` retries an entire broadcast after partial failure. | A configured global target receives other tenants' digests; a retry can duplicate previously successful deliveries. No live disclosure was tested or claimed. |
| PRD-02 | `core/permissions.py` allows None users and workspace-owner bypass; chat resolves some messenger identities to None. LLM models/providers are global but their tools use the same owner-permission scope. Action tools lack explicit permission declarations. | Confirmation is not authorization. Separate tenant-owner powers from platform-global administration and migrate legacy identities deliberately. |
| PRD-03 | `daily_cost_today` sums messages + digests + jobs, not `AIAnalytics.estimated_cost`; `_save_analysis` replaces cost on daily-row updates. | The apparent daily cap is not a complete spend ceiling. Simply summing current analytics rows loses previous attempts and can double-count rollups. |
| PRD-04 | `docker/docker-compose.yml` runs db + API only, migrations inside API startup and publishes 5432. `/health` returns status ok even on DB failure. | Existing artifacts are not a tested runtime deployment; process failure and DB failure can be misreported. |
| PRD-05 | Dispatcher marks returned dicts done; handlers can return status failed or source-error counters. Reaper uses locked_at age without heartbeat. | A green job can conceal complete failure; long healthy work can become stale and be requeued. Verify concurrent scheduler enqueue atomicity as well. |
| PRD-06 | retention_days appears in plan/model/UI; prune uses explicit/default payload days for jobs and staged items. | Advertised data-retention tiers are not a complete deletion policy covering analytics, chats, memory, notifications and backups. |
| UX-02 | Dependency-free isolated registry check: action_send is registered to `_auto_actions_forced_dry_run()` instead of `action_send(action_id, dry_run)`. | Tool invocation with action_id raises TypeError before any DB or platform call. Fix separately; do not enable real publishing as a side effect. |

## Evidence map

All paths below refer to the pinned baseline above; later code may differ.

- Analysis contracts/defense/audit: [analyzer](../../app/services/ai/analyzer.py), [schema validator](../../app/services/ai/json_schema_builder.py), [client](../../app/services/ai/llm_client.py), [prompts](../../app/services/ai/prompts.py), [sanitizer](../../app/services/ai/prompt_sanitizer.py), [reliability tests](../../tests/test_llm_reliability.py).
- Routing and chains: [model manager](../../app/models/managers/llm_model_manager.py), [chain resolver](../../app/services/ai/chain_resolver.py), [dedup](../../app/services/ai/dedup.py), [grouping](../../app/services/ai/grouping.py), [chain tests](../../tests/test_chain_resolver.py).
- Agent boundaries: [runtime](../../app/agent/runtime.py), [permissions](../../app/core/permissions.py), [tools registry](../../app/agent/tools.py), [action tools](../../app/agent/toolset/actions.py), [LLM tools](../../app/agent/toolset/llm.py), [learning](../../app/agent/learning.py), [memory model](../../app/models/agent_memory.py).
- Delivery/cost/jobs: [broadcast](../../app/channels/registry.py), [digest builder](../../app/services/digest/builder.py), [budget resolver](../../app/services/tenancy/resolver.py), [handlers](../../app/jobs/handlers.py), [dispatcher](../../app/jobs/dispatcher.py), [job manager](../../app/models/managers/job_manager.py), [tenant model](../../app/models/tenant.py).
- UX/operations: [onboarding](../../app/web/onboarding.py), [analytics](../../app/web/analytics.py), [source readiness](../../app/web/sources.py), [settings](../../app/web/settings.py), [Compose](../../docker/docker-compose.yml), [Dockerfile](../../docker/Dockerfile), [HTTP health/session](../../app/main.py).
- Recent work already landed: [reliability review](reliability_review_2026_10_08.md), [analytics filters](analytics_filters_review.md), [drilldown](analytics_drilldown_review.md), [chain navigation](chain_navigation_review.md). Their test counts are historical run reports, not a test result from this review.

## Documentation corrections to schedule

`AI_PIPELINE_AND_PROMPTS.md` and vision still mention old per-media fields; vision still places reactions on scenarios and calls RBAC frozen despite active permission checks. TENANCY still reports head 0083 and 255 passing tests; repository revision graph has head 0086. DEPLOYMENT says artifacts do not exist even though docker files are tracked, and examples mix API health with runtime-only startup. Current DIGEST/API/MODELS and the recent review documents are substantially newer; do not blindly repeat the old Phase 0 checklist.

Correct each contract alongside the relevant implementation PR. This documentation-only change does not rewrite every reference manual, claim deployed migration state, or remove useful historical research.

## Verification limits

Performed: static code/contract inspection; isolated dependency-free execution of the tool decorator/dispatch reproducing the action_send TypeError; AST derivation of repository migration head 0086; Markdown local-link checks and `git diff --check` for this documentation delivery.

Not performed: full pytest, Alembic against a DB, real provider/platform calls, browser UX tests, load tests, payment/legal review, deployment, restore or production data access. No “production-ready” or “all tests green” conclusion is justified by this review alone.
