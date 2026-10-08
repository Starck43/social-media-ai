# Reliability review — 2026-10-08

## Basis and checklist corrections

Reviewed master `1df34e762a2971b464e3a474c822025c171e9a1c` against code,
migrations, tests and the supplied residual-work checklist. Code and migrations
win over historical design prose. The branch originally called dev was behind
master by 29 commits and had no unique commits; a later fetch showed both refs
at `1df34e7`. No master changes are part of this delivery.

The following proposed work was already present and was not reimplemented:

- Scenario prompt fields, tenant/invite role_id, LLM API CRUD/permissions and
  most aggregation architecture documentation.
- Digest CLI help and DIGEST.md grouping table already include topic_chains.
- LLM chat add/update/delete/test tools already register permissions and write
  confirmations; permission recheck lives in the existing runtime.
- Local Pydantic validation and a prompt sanitizer already existed. However,
  validation failed open, discarded extra fields and could insert unset nulls;
  the sanitizer did not protect the default prompt path.

Migration head is **0086**, not 0085. No new database migration was required.

## File-by-file changes

| File | Change |
|---|---|
| `.agent/rules.md` | Add the five shared development/testing/doc-sync rules. |
| `.gitignore` | Keep local agent scratch files ignored, but track rules.md. |
| `app/api/v1/endpoints/dashboard.py` | Include topic_chains in parameter help and invalid-value error. Existing days rejection retained. |
| `app/services/ai/analyzer.py` | Remove analyze_by; return models rather than providers from strategy resolution; check explicit model capability/provider state; strict save validation; detached audit snapshots including unified-summary prompt hash. Remove obsolete provider-type helper. |
| `app/services/monitoring/collector.py` | Remove analyze_by from signature, analyzer call and result metadata. |
| `app/models/managers/llm_model_manager.py` | Share active-provider capability/default resolution; cost/multimodal strategy tie-breaks; handle custom endpoint creation; maintain defaults on type changes and partial updates; exclude deleted/inactive-provider models from replacement candidates. |
| `app/api/v1/endpoints/llm_models.py` | Return custom_endpoint_path instead of silently losing it. |
| `app/agent/toolset/llm.py` | Expose decision/custom endpoint fields; safe default replacement preview; no spurious non-default deletion warning. Existing permission/confirmation gates retained. |
| `app/services/ai/json_schema_builder.py` | Validate supported nested schema types, nullability, enum values and bounds; retain common relevance fields; preserve permitted extra/unset fields; strict runtime validation option. |
| `app/services/ai/llm_client.py` | Reject invalid structured analysis on all three formats, retain usage and mark failure; JSON parser returns dict-shaped data to analytics. |
| `app/services/ai/prompt_sanitizer.py` | Add explicit untrusted-data boundaries and escape boundary tags; clarify that sanitization is heuristic. |
| `app/services/ai/prompts.py` | Frame both custom and default text; prevent task/scope override of system-owned text. |
| `docs/MODELS.md` | Correct real column types, missing hashes/chain labels, removed source dates, LLM endpoint/cost fields, head 0086 and resolution semantics. |
| `docs/API.md` | Correct grouped API axes/default/days rejection and permission matrix; document custom endpoint/decision fields. |
| `docs/ANALYTICS_AGGREGATION_SYSTEM.md` | Correct specialized section names/API axes/head/cost example; document validation, defense boundaries and audit semantics. |
| `docs/AGENT.md` | Remove duplicated permission/confirmation documentation and document current LLM tool behavior. |
| `docs/design/competitive_analysis.md` | Add verified CA-01–CA-04 status above historical research. |
| `tests/test_llm_reliability.py` | Cover model strategy/default/provider behavior, audit hash/detachment/save without DEBUG, nested validation and invalid outputs across three formats, data framing and existing tool gates. |
| `tests/test_llm_models_manager.py` | Cover endpoint round-trip, default type changes/partial updates, deletion preview/execution agreement and disabled providers. |
| `tests/test_analyzer_modes.py` | Replace obsolete analyze_by dispatch assertions with the fixed write-path contract. |
| `tests/test_ai_analytics_periods.py` | Use the cleaned analyzer signature. |
| `tests/test_handle_collect.py` | Update collector stand-ins to the cleaned signature. |
| `tests/test_scenario_prompt_system.py` | Assert framed content rather than verbatim untrusted-text substitution. |
| `tests/conftest.py` | Prepare test DB before role seeding; use an ephemeral test-only vault key, restoring the previous setting afterward. |
| `tests/test_web_onboarding.py` | Supply real Request context for static url_for, assert current light/dark shared control classes, avoid cross-workspace bypass-dependent schedule assertion. |
| `docs/design/reliability_review_2026_10_08.md` | Record reviewed scope, file changes, verification and limitations. |

## Verification

Environment: Python 3.13.14, PostgreSQL 15.18, pinned requirements.txt dependencies.
All databases/schemas were local and isolated; no deployment database or live
provider credentials were used. Provider response tests use httpx mocks.

- Clean unmodified master: **852 passed, 10 failed, 1 skipped**. The ten failures
  reproduced separately: six stale onboarding/template assertions and four
  missing-vault-key setup failures. They were not introduced by the LLM changes.
- Focused final LLM/analyzer/schema tests: **113 passed**.
- Web onboarding/settings/sources checks: **30 passed**.
- Automatic bootstrap of a previously absent test schema: **17 passed**.
- `alembic heads`: **0086 (head)**.
- `compileall`, targeted Black checks and `git diff --check`: passed.

- Final clean full suite: **891 passed, 1 skipped, 10 warnings** in 172.87 seconds.
  Total collected: **892 tests**, 29 more than the reviewed master.
  No failures or collection errors. Remaining warnings are deprecations.

## Deliberate limits

- Quality selection uses explicit fleet defaults/stable order, not price as a
  proxy for model quality. Provider/model default ranks precede strategy-specific
  tariff/capability tie-breaks. Explicit active scenario models take precedence.
- Structured output is locally validated using the supported JSON Schema subset;
  provider-native strict-schema protocols and arbitrary JSON Schema features
  such as references/combinators were not implemented. The independent learning
  extract_json helper remains unchanged.
- Prompt framing and control-pattern stripping are defense in depth, not a
  guarantee against prompt injection. No regex-based filter can provide that.
- Audit hashes/configuration are written for new analyses; historical rows are
  not backfilled. Arbitrary task payload keys are not copied into the snapshot.
- CA-05 onward, MAX L1, embeddings, anomaly alerts, language propagation and
  billing are unchanged. No infrastructure/service addition or production
  migration execution is included.
