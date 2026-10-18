# CLI/API and embedded-chat contract audit

## Scope and baseline

- Source: `dev` **e79583e719e97ba16945b31db20267d40d297b0d**, inspected on
  2026-10-18. Fresh clone in a new thematic branch; no pre-existing sandbox or
  owner worktree was modified. Open PR listing was empty before edits.
- Publication synchronization: `bd123b76e4e90829e2b935c05f6419ca36bd44be`.
  `dev` advanced during branch creation with a foreign `docs/MODELS.md` receipt
  documentation update. It was fast-forwarded intact; runtime source and owned
  files did not change. Immutable findings retain their original source SHA.
- Owner request: audit CLI/API commands and the web chat's built-in agent tools
  after model migrations. This PR changes documentation and adds offline probes;
  **it does not repair runtime defects or authorize their activation**.
- Review covered mounted v1 routes, CLI registration, every built-in tool's
  declaration/signature, and focused LLM/task/web-chat implementations. It is not
  full handler, security, DB, browser, provider or production acceptance.
- Migration files were read, never executed. Deployment state and actual catalog
  rows are unknown. Current ORM is authoritative for this source audit.
- Existing P10-T01–T10 and P5-T04 owners/status/evidence are preserved. New
  findings below are recommendations, not separately accepted implementation tasks.

## Post-migration contract

| Change | Current source contract | Interface impact |
| --- | --- | --- |
| 0091 | Provider `is_default` removed; model defaults remain | CLI provider list still reads the deleted field |
| 0092–0095 | Attempt/dispatch/outcome journals and budget reservations | A mock is not accounting or live-provider evidence; probes must preserve admission/accounting |
| 0096 | Platform roles separated from workspace roles; canonical resource keys | API `/llm` is an operator route; workspace chat cannot acquire global catalog rights |
| 0097/0099/0100/0102/0103 | Personal billing, attribution and validation foundation | Do not claim paid test equivalence merely because a legacy helper journals a response |
| 0101 | Manual job initiator recorded | Enqueued/manual jobs need their existing initiator and permission contract |
| 0104 | Durable context-scoped analysis success receipts | Scheduled jobs ignore legacy repeat flags; direct CLI override is not a receipt bypass |

`LLMModel.model_type` is a comma-separated capability string in the ORM
(`app/models/llm/llm_model.py::_model_type_to_capabilities`); default resolution
uses capabilities and active providers. No new provider/model names are hardcoded
by this audit. Import shims under `app/models/` still exist: an old import path
alone is not a missing-model defect.

## Verified findings and prioritized fix plan

Priorities are recommendations, not changes to other task priorities. Source links
below pin the audited revision. Offline probes have expected failures deliberately:
those are unresolved contracts, **not passing runtime behavior**.

### High — task edits can silently change the wrong configuration

[Source at audited SHA](https://github.com/Starck43/social-media-ai/blob/e79583e719e97ba16945b31db20267d40d297b0d/app/agent/toolset/tasks.py#L372-L480).

- `task_update` always writes `agent_scenario_id=scenario_id`, whose omitted
  default is `None`. A request changing only `period` unbinds scenario 42 in the
  mocked source-slice reproduction. It computes a new `next_run_at` for `cron`
  and returns `new_cron`, but never includes `cron_expr` in the manager update.
  Thus the acknowledgment can disagree with persisted scheduling.
- Fix first in a bounded task lane: distinguish omission from explicit clear,
  persist the cron expression and next time together, validate sources/scenario
  before mutation. Preserve targets and workspace ownership.
- Acceptance: no-op/period-only update keeps scenario and cron; cron-only update
  writes both fields; invalid source/scenario refuses all changes. Real DB
  transaction/tenant checks remain owner-local, sequential.
- Probes: `test_unrelated_update_preserves_scenario_binding`,
  `test_cron_change_persists_actual_expression` (expected failures), and
  `test_partial_payload_preserves_existing_targets` (pass).

### Medium — task listing cannot resolve its model

[Source at audited SHA](https://github.com/Starck43/social-media-ai/blob/e79583e719e97ba16945b31db20267d40d297b0d/app/agent/toolset/tasks.py#L1-L48).

`task_list` uses `AgentTask` without a top-level or local import in
`app/agent/toolset/tasks.py`. Other handlers import that symbol locally; it is
not a module global. Tool registration succeeds but the list handler reaches a
`NameError`. Add the normal model import in a later runtime fix and exercise the
handler with a mocked manager. Static missing-symbol probe is an expected failure;
this audit did not invoke a real manager.

### Medium — LLM CLI is stale after lifecycle changes

[Source at audited SHA](https://github.com/Starck43/social-media-ai/blob/e79583e719e97ba16945b31db20267d40d297b0d/cli/commands/llm.py#L27-L305).

- `llm provider-list` reads `p.is_default`, absent from current LLMProvider and
  removed by 0091. Exact function + current-shape provider double raises
  `AttributeError`; empty provider lists can hide it.
- `llm model-test` instantiates abstract `LLMClient()` before checking `--mock`.
  Both modes are broken before dispatch. Its real branch additionally calls
  `LLMModelActions.test_model_real`, which no longer exists. The replacement
  admin flow uses `submit_test` and `model_probe`, not that deleted helper.
- Fix: remove provider-default output; keep mock truly dispatch-free; reuse an
  approved probe service for an explicitly requested real check, with proper
  payer/admission/error handling and nonzero CLI exit on returned failure.
  `--mock` still looks up catalog data; it is **not** a DB-free command.
- Probes: current-provider listing, abstract-client mock, missing real-probe target
  (three expected failures). No CLI command was run against application settings.

### Medium — LLM HTTP and tool schemas disagree with capability storage

[Source at audited SHA](https://github.com/Starck43/social-media-ai/blob/e79583e719e97ba16945b31db20267d40d297b0d/app/schemas/llm_model.py#L11-L61).

`LLMModelBase` / `LLMModelUpdate` allow only `text|image|embedding|decision`;
`LLMModelResponse` inherits the same restriction. Thus current-shape `video` or
`text,image` rows cannot validate in model detail/list responses; writes cannot
express them either. Tool list/add/update schemas carry the same narrow enum,
while CLI forwards a free-form `--type` and manager/default resolution understand
capability sets. API/tool `model_type` filters use exact equality, not capability
membership. No actual deployed row values were read.

Fix with one shared capability validation/serialization contract, not another
independent enum. Include video, valid combinations, invalid/duplicate/empty
values and exact-filter vs capability-filter semantics. Preserve default overlap
uniqueness and existing clients. The regex and tool-enum probes are expected
failures; they are not an executed HTTP/OpenAPI test.

### Medium — saved agent model choice is ignored by chat selection

[Source at audited SHA](https://github.com/Starck43/social-media-ai/blob/e79583e719e97ba16945b31db20267d40d297b0d/app/agent/runtime.py#L865-L908).

Settings persist `Tenant.agent_model` (a model name). Runtime `_chat` reads the
tenant's token/temperature overrides but always requests the fleet default via
`resolve_default_model("text")`, then passes that as `preferred_model` to fallback.
The offline source-slice probe supplies a distinct saved choice and observes the
fleet default instead (expected failure). Decide the supported override contract
before a fix: resolve only active/allowed models/providers, retain fallback,
usage accounting and deterministic auto-selection. Model IDs vs names and stale
settings need explicit handling. No provider was contacted.

### Medium — credential disable path differs from the documented client URL

[Source at audited SHA](https://github.com/Starck43/social-media-ai/blob/e79583e719e97ba16945b31db20267d40d297b0d/app/api/v1/endpoints/credentials.py#L39-L53).

The credentials router is mounted at `/api/v1/credentials`, but its disable
endpoint declares `/credentials/{credential_id}/disable`. The actual path is
`PATCH /api/v1/credentials/credentials/{credential_id}/disable`; the single
segment path previously shown in API.md is not registered. The AST composition
probe is an expected failure against that intended single-segment contract.
This PR corrects the reference to report reality; a later runtime fix must
choose whether to add a compatibility alias or migrate clients. No credential
row or token was loaded, tested or disabled.

### Policy/latent risk — global model tools are registered, not workspace capabilities

[Source at audited SHA](https://github.com/Starck43/social-media-ai/blob/e79583e719e97ba16945b31db20267d40d297b0d/app/core/permissions.py#L95-L150).

All six `llm_*` tools require global `llm.model.*`. Current `member_decision`
refuses non-workspace resources, even for a workspace owner or a platform admin
acting in workspace chat. `tool_specs_for` therefore hides these tools from
normal authenticated web-chat users; dispatch rechecks rights. This is the intended
platform/workspace separation, **not permission to restore access or add bypasses**.
Use `/admin`, the operator API or trusted CLI for the global catalog.

Before any future operator-chat exposure, separately review:
- `llm_providers_list` declares `llm.model.view`, not `llm.provider.view`.
- `llm_model_test` is unconfirmed and view-gated but can issue a paid request;
  it checks provider activity, not model activity, returns raw exception strings,
  and is not the new admin `probe_real_capped` flow. It does call
  `journal_chat_attempt`; that alone does not prove equivalent admission,
  payer binding or safe diagnostics. Error-envelope work overlaps P10-T06.
- Registry dispatch checks permissions/confirmation but does not enforce the
  declared JSON Schema (types/enums/ranges). Runtime checks dictionary/string keys;
  confirmation canonicalizes/binds arguments but does not validate the schema.
  Handler-specific checks exist; do not claim global validation or a demonstrated
  authorization bypass from this observation. Coordinate one selected boundary
  with P3-T02/P5-T01 rather than changing every tool in this audit.

## Web-chat boundaries verified by source inspection

- `/app/chat` delegates to `handle_web_message`, then the shared runtime; it is
  not a REST `/api/v1/chat` endpoint. Sessions are separate per workspace, channel
  and web user, not the same transcript as Telegram/MAX.
- `TenantUIMiddleware` resolves membership; web chat has its own access gate and
  CSRF on mutations. Runtime refreshes identity and declared rights before effects.
- Current confirmation authorization is **version 3**, with actor/session,
  workspace-role and chat-enabled binding; old versions fail closed. Existing
  atomic-consumption evidence is not rerun or broadened here.
- `memory_set` is an immediate personal-memory write; `collect_now` may collect,
  analyze and incur cost inline without confirmation. `scenario_suggest_prompt`
  can also invoke an LLM under scenario-view permission. Therefore “no confirmation”
  does not mean read-only, free or side-effect-free. `digest_send_now` is confirmed
  delivery; `action_send` remains confirmed preview-only with literal dry-run.
- P5-T04 remains owned by codex-analysis-receipts. Stale force flags in task-tool
  schemas are documentation/UX debt, not evidence of a durable receipt bypass.

## Inventory (source declarations, not successful executions)

### CLI — 37 registered command paths

Top-level groups: `roles`, `credentials`, `llm`, `chains`, `scenarios`, `task`,
`digest`; direct entry commands: `collect`, `analyze`, `prune`, `learn`, `reflect`.
The six `task <job_type>` paths are registered by the factory loop.

| Command | Declaration |
| --- | --- |
| `analyze` | `cli/main.py:26` |
| `chains groups` | `cli/commands/chains.py:45` |
| `chains merge` | `cli/commands/chains.py:28` |
| `collect` | `cli/main.py:23` |
| `credentials disable` | `cli/commands/credentials.py:99` |
| `credentials list` | `cli/commands/credentials.py:68` |
| `credentials login` | `cli/commands/credentials.py:124` |
| `credentials oauth` | `cli/commands/credentials.py:177` |
| `credentials test` | `cli/commands/credentials.py:277` |
| `digest run` | `cli/main.py:707` |
| `digest send-now` | `cli/main.py:670` |
| `learn` | `cli/main.py:28` |
| `llm model-add` | `cli/commands/llm.py:128` |
| `llm model-delete` | `cli/commands/llm.py:222` |
| `llm model-list` | `cli/commands/llm.py:69` |
| `llm model-test` | `cli/commands/llm.py:247` |
| `llm model-update` | `cli/commands/llm.py:171` |
| `llm provider-list` | `cli/commands/llm.py:28` |
| `prune` | `cli/main.py:27` |
| `reflect` | `cli/main.py:29` |
| `roles list` | `cli/commands/roles.py:33` |
| `roles preset` | `cli/commands/roles.py:84` |
| `roles show` | `cli/commands/roles.py:58` |
| `roles update` | `cli/commands/roles.py:96` |
| `scenarios list` | `cli/commands/scenarios.py:102` |
| `scenarios seed-default` | `cli/commands/scenarios.py:48` |
| `task add` | `cli/main.py:220` |
| `task analyze` | `cli/main.py:660` |
| `task collect` | `cli/main.py:660` |
| `task digest` | `cli/main.py:660` |
| `task learn` | `cli/main.py:660` |
| `task list` | `cli/main.py:101` |
| `task pause` | `cli/main.py:340` |
| `task prune` | `cli/main.py:660` |
| `task reflect` | `cli/main.py:660` |
| `task remove` | `cli/main.py:333` |
| `task update` | `cli/main.py:363` |

### HTTP API — 81 mounted v1 method/path declarations

Includes legacy hidden probes. Route declaration inventory only: authentication,
request validation and DB effects were not executed. `/health/*`, admin and
non-chat web routes are outside this REST inventory.

| Method | Path | Declaration |
| --- | --- | --- |
| GET | `/api/v1/ai/scenarios` | `app/api/v1/endpoints/scenarios.py:72` |
| POST | `/api/v1/ai/scenarios` | `app/api/v1/endpoints/scenarios.py:27` |
| DELETE | `/api/v1/ai/scenarios/{scenario_id}` | `app/api/v1/endpoints/scenarios.py:199` |
| GET | `/api/v1/ai/scenarios/{scenario_id}` | `app/api/v1/endpoints/scenarios.py:108` |
| PUT | `/api/v1/ai/scenarios/{scenario_id}` | `app/api/v1/endpoints/scenarios.py:138` |
| POST | `/api/v1/auth/change-password` | `app/api/v1/endpoints/auth.py:154` |
| POST | `/api/v1/auth/login` | `app/api/v1/endpoints/auth.py:18` |
| POST | `/api/v1/auth/refresh-token` | `app/api/v1/endpoints/auth.py:63` |
| POST | `/api/v1/auth/register` | `app/api/v1/endpoints/auth.py:92` |
| GET | `/api/v1/credentials` | `app/api/v1/endpoints/credentials.py:16` |
| PATCH | `/api/v1/credentials/credentials/{credential_id}/disable` | `app/api/v1/endpoints/credentials.py:40` |
| POST | `/api/v1/credentials/login` | `app/api/v1/endpoints/credentials.py:56` |
| POST | `/api/v1/credentials/oauth` | `app/api/v1/endpoints/credentials.py:107` |
| GET | `/api/v1/credentials/test` | `app/api/v1/endpoints/credentials.py:131` |
| GET | `/api/v1/dashboard/analytics` | `app/api/v1/endpoints/dashboard.py:142` |
| GET | `/api/v1/dashboard/analytics/aggregate/content-mix` | `app/api/v1/endpoints/dashboard.py:684` |
| GET | `/api/v1/dashboard/analytics/aggregate/engagement` | `app/api/v1/endpoints/dashboard.py:700` |
| GET | `/api/v1/dashboard/analytics/aggregate/grouped` | `app/api/v1/endpoints/dashboard.py:716` |
| GET | `/api/v1/dashboard/analytics/aggregate/llm-stats` | `app/api/v1/endpoints/dashboard.py:668` |
| GET | `/api/v1/dashboard/analytics/aggregate/sentiment-trends` | `app/api/v1/endpoints/dashboard.py:624` |
| GET | `/api/v1/dashboard/analytics/aggregate/top-topics` | `app/api/v1/endpoints/dashboard.py:646` |
| GET | `/api/v1/dashboard/debug/analytics` | `app/api/v1/endpoints/dashboard.py:578` |
| GET | `/api/v1/dashboard/debug/analytics/{analytics_id}` | `app/api/v1/endpoints/dashboard.py:602` |
| GET | `/api/v1/dashboard/debug/topic-chain/{chain_id}` | `app/api/v1/endpoints/dashboard.py:548` |
| GET | `/api/v1/dashboard/notifications/recent` | `app/api/v1/endpoints/dashboard.py:236` |
| GET | `/api/v1/dashboard/sources` | `app/api/v1/endpoints/dashboard.py:94` |
| GET | `/api/v1/dashboard/stats` | `app/api/v1/endpoints/dashboard.py:26` |
| GET | `/api/v1/dashboard/topic-chains` | `app/api/v1/endpoints/dashboard.py:252` |
| GET | `/api/v1/dashboard/topic-chains/{chain_id}` | `app/api/v1/endpoints/dashboard.py:424` |
| GET | `/api/v1/dashboard/topic-chains/{chain_id}/evolution` | `app/api/v1/endpoints/dashboard.py:461` |
| GET | `/api/v1/dashboard/trends/{source_id}` | `app/api/v1/endpoints/dashboard.py:183` |
| GET | `/api/v1/health` | `app/api/v1/entry.py:59` |
| GET | `/api/v1/llm/llm-models/` | `app/api/v1/endpoints/llm_models.py:60` |
| POST | `/api/v1/llm/llm-models/` | `app/api/v1/endpoints/llm_models.py:43` |
| DELETE | `/api/v1/llm/llm-models/{model_id}` | `app/api/v1/endpoints/llm_models.py:130` |
| GET | `/api/v1/llm/llm-models/{model_id}` | `app/api/v1/endpoints/llm_models.py:84` |
| PATCH | `/api/v1/llm/llm-models/{model_id}` | `app/api/v1/endpoints/llm_models.py:102` |
| GET | `/api/v1/llm/llm-providers/` | `app/api/v1/endpoints/llm_providers.py:44` |
| POST | `/api/v1/llm/llm-providers/` | `app/api/v1/endpoints/llm_providers.py:28` |
| DELETE | `/api/v1/llm/llm-providers/{provider_id}` | `app/api/v1/endpoints/llm_providers.py:101` |
| GET | `/api/v1/llm/llm-providers/{provider_id}` | `app/api/v1/endpoints/llm_providers.py:58` |
| PATCH | `/api/v1/llm/llm-providers/{provider_id}` | `app/api/v1/endpoints/llm_providers.py:76` |
| GET | `/api/v1/monitoring/analytics/source/{source_id}` | `app/api/v1/endpoints/monitoring.py:92` |
| POST | `/api/v1/monitoring/collect/monitored` | `app/api/v1/endpoints/monitoring.py:67` |
| POST | `/api/v1/monitoring/collect/platform` | `app/api/v1/endpoints/monitoring.py:41` |
| POST | `/api/v1/monitoring/collect/source` | `app/api/v1/endpoints/monitoring.py:15` |
| GET | `/api/v1/notifications/notifications` | `app/api/v1/endpoints/notifications.py:24` |
| POST | `/api/v1/notifications/notifications` | `app/api/v1/endpoints/notifications.py:120` |
| POST | `/api/v1/notifications/notifications/cleanup` | `app/api/v1/endpoints/notifications.py:207` |
| POST | `/api/v1/notifications/notifications/mark-all-read` | `app/api/v1/endpoints/notifications.py:172` |
| GET | `/api/v1/notifications/notifications/stats` | `app/api/v1/endpoints/notifications.py:71` |
| DELETE | `/api/v1/notifications/notifications/{notification_id}` | `app/api/v1/endpoints/notifications.py:189` |
| GET | `/api/v1/notifications/notifications/{notification_id}` | `app/api/v1/endpoints/notifications.py:94` |
| POST | `/api/v1/notifications/notifications/{notification_id}/mark-read` | `app/api/v1/endpoints/notifications.py:151` |
| GET | `/api/v1/social/callback` | `app/api/v1/endpoints/social.py:55` |
| GET | `/api/v1/social/start` | `app/api/v1/endpoints/social.py:36` |
| GET | `/api/v1/sources` | `app/api/v1/endpoints/sources.py:14` |
| POST | `/api/v1/sources` | `app/api/v1/endpoints/sources.py:63` |
| DELETE | `/api/v1/sources/{source_id}` | `app/api/v1/endpoints/sources.py:132` |
| GET | `/api/v1/sources/{source_id}` | `app/api/v1/endpoints/sources.py:38` |
| PATCH | `/api/v1/sources/{source_id}` | `app/api/v1/endpoints/sources.py:97` |
| GET | `/api/v1/tasks` | `app/api/v1/endpoints/tasks.py:44` |
| POST | `/api/v1/tasks` | `app/api/v1/endpoints/tasks.py:65` |
| POST | `/api/v1/tasks/run` | `app/api/v1/endpoints/tasks.py:227` |
| DELETE | `/api/v1/tasks/{task_id}` | `app/api/v1/endpoints/tasks.py:163` |
| GET | `/api/v1/tasks/{task_id}` | `app/api/v1/endpoints/tasks.py:53` |
| PATCH | `/api/v1/tasks/{task_id}` | `app/api/v1/endpoints/tasks.py:113` |
| PATCH | `/api/v1/tasks/{task_id}/pause` | `app/api/v1/endpoints/tasks.py:176` |
| POST | `/api/v1/tasks/{task_id}/run` | `app/api/v1/endpoints/tasks.py:192` |
| GET | `/api/v1/test-auth` | `app/api/v1/entry.py:54` |
| GET | `/api/v1/test-db` | `app/api/v1/entry.py:42` |
| GET | `/api/v1/users` | `app/api/v1/endpoints/user.py:20` |
| GET | `/api/v1/users/me` | `app/api/v1/endpoints/user.py:30` |
| PUT | `/api/v1/users/me` | `app/api/v1/endpoints/user.py:35` |
| GET | `/api/v1/users/roles/` | `app/api/v1/endpoints/roles.py:21` |
| GET | `/api/v1/users/roles/{role_name}` | `app/api/v1/endpoints/roles.py:30` |
| PUT | `/api/v1/users/roles/{role_name}/permissions` | `app/api/v1/endpoints/roles.py:42` |
| DELETE | `/api/v1/users/{user_id}` | `app/api/v1/endpoints/user.py:82` |
| PUT | `/api/v1/users/{user_id}` | `app/api/v1/endpoints/user.py:50` |
| POST | `/api/v1/users/{user_id}/change-password` | `app/api/v1/endpoints/user.py:91` |
| PUT | `/api/v1/users/{user_id}/platform-role` | `app/api/v1/endpoints/user.py:66` |

### Shared chat — 34 built-in tools

Registered tools are filtered per caller; this is not a claim that all tools are
available to a web user. `Confirm=no` is not a side-effect classification.

| Tool | Declared permission | Confirm | Declaration |
| --- | --- | --- | --- |
| `actions_log` | `scheduling.action.view` | no | `app/agent/toolset/actions.py:24` |
| `action_send` | `scheduling.action.view` | yes | `app/agent/toolset/actions.py:91` |
| `collect_now` | `collection.source.analyze` | no | `app/agent/toolset/collect.py:24` |
| `llm_providers_list` | `llm.model.view` | no | `app/agent/toolset/llm.py:34` |
| `llm_models_list` | `llm.model.view` | no | `app/agent/toolset/llm.py:67` |
| `llm_model_test` | `llm.model.view` | no | `app/agent/toolset/llm.py:120` |
| `llm_model_add` | `llm.model.create` | yes | `app/agent/toolset/llm.py:180` |
| `llm_model_update` | `llm.model.update` | yes | `app/agent/toolset/llm.py:252` |
| `llm_model_delete` | `llm.model.delete` | yes | `app/agent/toolset/llm.py:326` |
| `report_period` | `notifications.digest_run.view` | no | `app/agent/toolset/reports.py:59` |
| `digest_send_now` | `notifications.digest_run.create` | yes | `app/agent/toolset/reports.py:126` |
| `analytics_chains` | `analysis.result.view` | no | `app/agent/toolset/reports.py:151` |
| `analytics_chain_detail` | `analysis.result.view` | no | `app/agent/toolset/reports.py:178` |
| `scenario_list` | `analysis.scenario.view` | no | `app/agent/toolset/scenarios.py:174` |
| `scenario_get` | `analysis.scenario.view` | no | `app/agent/toolset/scenarios.py:195` |
| `scenario_templates` | `analysis.scenario.view` | no | `app/agent/toolset/scenarios.py:214` |
| `scenario_suggest_prompt` | `analysis.scenario.view` | no | `app/agent/toolset/scenarios.py:235` |
| `scenario_validate_prompt` | `analysis.scenario.view` | no | `app/agent/toolset/scenarios.py:261` |
| `scenario_create` | `analysis.scenario.create` | yes | `app/agent/toolset/scenarios.py:318` |
| `scenario_update` | `analysis.scenario.update` | yes | `app/agent/toolset/scenarios.py:403` |
| `scenario_clone` | `analysis.scenario.create` | yes | `app/agent/toolset/scenarios.py:493` |
| `scenario_delete` | `analysis.scenario.delete` | yes | `app/agent/toolset/scenarios.py:562` |
| `sources_list` | `collection.source.view` | no | `app/agent/toolset/sources.py:47` |
| `source_add` | `collection.source.create` | yes | `app/agent/toolset/sources.py:85` |
| `source_disable` | `collection.source.update` | yes | `app/agent/toolset/sources.py:124` |
| `source_enable` | `collection.source.update` | yes | `app/agent/toolset/sources.py:145` |
| `system_status` | `scheduling.job.view` | no | `app/agent/toolset/system.py:16` |
| `memory_set` | `agent.memory.update` | no | `app/agent/toolset/system.py:49` |
| `memory_get` | `agent.memory.view` | no | `app/agent/toolset/system.py:67` |
| `task_list` | `scheduling.task.view` | no | `app/agent/toolset/tasks.py:22` |
| `task_add` | `scheduling.task.create` | yes | `app/agent/toolset/tasks.py:139` |
| `task_remove` | `scheduling.task.delete` | yes | `app/agent/toolset/tasks.py:278` |
| `task_update` | `scheduling.task.update` | yes | `app/agent/toolset/tasks.py:372` |
| `task_pause` | `scheduling.task.update` | yes | `app/agent/toolset/tasks.py:499` |

## Sandbox evidence and remaining acceptance

Audited runtime SHA: `e79583e719e97ba16945b31db20267d40d297b0d`; the audit probe
file is added by this PR, runtime files are unchanged. The PR commit pins the
exact probe version. Environment: Linux, separate `/data/interface-audit-venv`,
Final sandbox source HEAD: `bd123b76e4e90829e2b935c05f6419ca36bd44be`.
Python 3.13.14, pip 24.2; no third-party test dependencies required or installed
(stdlib `unittest`, `ast`, mocks only). This does not replace the owner's `.venv`.

Reproduce from a checkout without importing the application:

```sh
python -m venv /tmp/interface-audit-venv
/tmp/interface-audit-venv/bin/python tests/test_interface_audit_probes.py -v
git diff --check
```

Sandbox-executed result: **14 probes: 4 passed, 10 expected failures, 0 unexpected
failures, 0 skipped; exit 0**. The ten expected failures document OPEN defects;
the exit code means the audit harness reproduced the expected state, **not** that
these commands work. The class-schema probe covers three rejected capability
values within one test. Positive coverage checks: no app imports, tool names and
explicit schema argument names, removed provider field, preservation of task
payload targets. This is not JSON-Schema, permission or persistence acceptance.

No pytest/conftest, app/runtime/server, .env, real DB, credentials, provider,
external send, migration/reset or deployment was used. Fresh `dev`/PR heads and
shared-document IDs/links must be rechecked before publication; the compact PR
handoff records that final check. Required runtime acceptance for later fixes:
actual HTTP serialization, DB task persistence/atomicity/tenant isolation,
role-filtered browser tool visibility, revocation/confirmation, selected-model
fallback, and accounting/admission under the owner's sequential local routes.
Do not rerun paid probes simply to validate this report. The audit may be reviewed
without those runtime checks; no runtime fix or feature is accepted here.
