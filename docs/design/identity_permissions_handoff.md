# Identity and permission boundary: owner handoff

## Current continuation — PR22 merged; general queue is next assigned lane

PR #22 **MERGED into dev**, history-preserving merge commit
`683c49e85918503119af47338200d8f762a52403` (parents dev a983a15 and PRhead
56d810538aa15f9e391c43dfed75e77aada2d2a1), after explicit Owner Ready/merge
approval. GitHub closed/merged verified; Draft removed. Merge tree equals
approved PRhead tree exactly. app/tests/rules/migrations match tested 482eacb;
no new executable delta or automatic post-merge retest requested.

OWNER completed full at exact 482eacbd84ce640bd25cfaf4c75bbe1695621495:
1612 passed, 11 warnings, 64 subtests, 738.25s, saved PYTEST_EXIT_CODE=0;
HEAD unchanged before/after, only untracked perr logs. Owner-local full log not
independently read by agent. GitGuardian Security Checks completed SUCCESS on
approved 56d8105; zero legacy commit statuses. No invented test-CI pass or
independent warning triage. History of red/interrupted runs remains evidence,
not current acceptance: bounded separately approved one-row TEST orphan cleanup
with zero dependencies preceded final green; no future deletion/reset permission.

Integrated bounded safeguards: bound active identity/session, fresh tool/rights
checks, actor/args/tool-bound one-use confirmation, narrow owner/service grants,
preview-only action_send botaction.view/literal dry_run=True, no publication/
PENDING transition. Owner queued/running/outcome UI, source/task escaping,
LLM-default management, test isolation/onboarding correction preserved.
Merged/regression-verified does not establish deployment, live activation, or
business/security acceptance. Broad /app XSS audit, durable approval/CAS and
release/browser/live
acceptance remain OPEN. Do not restart merged dispatcher privacy or typed work.

Next assigned sequence: general queue PRD-05 lane, THEN per-attempt accounting/
budget-reservation DESIGN (schema approval separate). Before implementation
resolve fresh dev/open Drafts/board ownership; choose one bounded independent
queue contract and preserve Owner parallel changes. No new queue code prepared
in this integration-record task. PR29 deferred, PR30/31/32 occupied model-layout
lanes separate/unmodified; do not run/merge them automatically.

Existing .agent R3/R4 and R6–R9 remain binding: main dev checkout off limits,
existing per-PR worktrees, sequential shared local test_schema, no schema create/
reset/drop/stamp/migrate/Alembic without separate command. Agent prepares tests
only; ZERO agent tests/collection/app imports/DB/migrations/browser/live calls.
Owner main checkout was not changed; PR22 worktree/logs/branch retained, no
cleanup or automatic branch deletion. Fetch-only inspection may observe dev;
no instruction to switch/stash/commit Owner main or remove review worktree.

This FOUR-file post-merge documentation record is PR #33; Owner explicitly
approved its integration separately from PR22. Its actual merge state/SHA are
in GitHub PR33. No new docs/design files, code/tests/rules/schema changes or
replay requested. Future continuation uses fresh dev, not frozen session heads.


## Latest raw-log diagnosis and prepared import-isolation follow-up

Attached owner log for `706bfea` was selectively inspected: actual run directory
is the retained PR22 worktree, Python3.12.6, collected1554; final1486 passed,
68 failed,11 warnings,58 subtests,663.02s. Markdown renderer PASSED (line1413)
and is not a failed node. Raw tier traceback includes plan_arrange_user and
scoped inserts; earlier reconstructed traceback/list was inaccurate, not proof
of an old checkout. Focused owner41/41 and139/139 remain separately reported.
This is inspected owner evidence, NOT agent test execution.

Later tracebacks import current_tenant_id/handle_collect from mock_infrastructure
and JobManager() returns the fixture's SimpleNamespace without enqueue. These
specific doubles are declared by returned-job-failure source fixtures, reused
by dispatcher privacy. The dangerous global-import boundary is addressed in
`99c8294`: load actual source with module-local declared from-import doubles,
including late/relative imports; restore temporary private alias; notification
mocks patch only that module's map. No application/rights/UI/schema change,
no fake enqueue added or assertion weakened. Eight new regression methods
PREPARED/NOT RUN; existing outcome25/privacy13 assertions retained. Other source
fixtures are not globally rewritten; do not claim all68 failures fixed.

Early CLI/digest arrange failures occur before this producer group and are a
separate compatibility checkpoint, not dismissed as unrelated/pre-existing.
Next: owner runs8/25/13 standalone scripts and the ordered producer-consumer
pytest group in the handoff. Agent runs ZERO tests/imports/collections/DB/live
calls; AST/whitespace/ref checks only. Full-suite rerun deferred until the short
group is clean and remaining arrange failures are addressed. PR29 deferred;
PR22 Draft/unmerged, no deployment/acceptance claim. Older sections below are
historical checkpoints, not the current continuation.

### Owner verification — short ordered group, not another full suite yet

In the clean retained worktree: git fetch origin; git merge --ff-only
origin/ai/identity-permissions-boundary; git rev-parse HEAD. Stop if dirty or
history diverged. Existing project venv/dependencies; no reset/drop/migration,
concurrent pytest on one schema or live calls. Standalone scripts isolate all
production effects:

```bash
python tests/test_source_import_isolation.py
python tests/test_returned_job_failures.py
python tests/test_dispatcher_log_privacy.py
```

Expected8/25/13 success. Then, with the existing distinct DB_TEST_SCHEMA:

```bash
python -m pytest -q tests/test_source_import_isolation.py tests/test_dispatcher_log_privacy.py tests/test_returned_job_failures.py tests/test_plan_tiers.py tests/test_handle_collect.py tests/test_learn_cost.py tests/test_task_run_now.py::test_saving_and_running_queues_the_job_instead_of_blocking tests/test_web_source_detail.py::test_collect_now_runs_inline_for_that_source_only
```

The explicit order places former producers before consumers in one process.
Expected: no mock_infrastructure/current_tenant_id/handle_collect imports or
SimpleNamespace.enqueue after these fixtures; tier quotas still enforced.
Return exact testedSHA and first complete failure, not broad "pre-existing"
categories. Other remaining arrangement/UI/digest failures need their own
investigation; this patch cannot certify all68 or a full green suite.

## Latest checkpoint — owner results and five arrange fixes

Current code checkpoint `31618a5`; includes dev `6f810a4` via `ef9c417`.
Owner tested `70f58a1`: standalone16/18/19/9 passed; DB/API/web133 passed,
6 failed; full suite reported1554 tests and timed out after10minutes.
These are OWNER-REPORTED, not agent runs. Timeout is incomplete evidence;
"pre-existing before the latest correction" does not waive PR integration failures.

Five plan-tier arrange failures are addressed ONLY in tests/test_plan_tiers.py:
isolated active non-superuser VIEWER actor with role_id, eager rights, ordinary
create-denial assertions and cleanup; explicit owner scope around source
inserts/deactivation and task inserts. Quota checks run after scope restoration.
No autouse blanket privilege, altered quota assertions or production change.
This fixture is PREPARED/NOT RUN. Owner's published UI/markdown test changes
are included from dev rather than reimplemented. PR29 stays separate/deferred.

Retained clean review worktree: git fetch origin; git merge --ff-only
origin/ai/identity-permissions-boundary (stop if dirty/diverged); git rev-parse HEAD.
After confirming the interrupted process is stopped and inspecting isolated
schema state, no automatic reset/drop/migration:

```bash
python -m scripts.setup_test_db --check
python -m pytest -q tests/test_plan_tiers.py tests/test_web_chat.py
```

If those pass, complete the existing DB/API/web group; do not separately repeat
the four unchanged standalone scripts. For full-suite diagnosis, use verbose
progress and pytest's faulthandler diagnostic (not a kill timeout):

```bash
python -m pytest -vv --durations=20 -o faulthandler_timeout=120
```

Normal project .venv/dependencies and distinct DB_TEST_SCHEMA; no concurrent
pytest processes on one schema. Record exact testedSHA, completed summary or
last active test + redacted diagnostic on a stall; don't infer that six known
failures are the only failures in an interrupted run. No ready/merge/deployment
claim. Below is earlier contract/history; latest evidence is this checkpoint.

## Current prepared block — identity, confirmation and action preview

2026-10-09: draft PR #22, branch `ai/identity-permissions-boundary`.
Implementation is PREPARED/PUSHED, NOT MERGED OR FULLY ACCEPTED. Fresh dev
`274cb2c` was integrated into this PR branch by `8f09a83`, preserving #26/#27/#28
and the owner LLM tests (earlier `0234c21` by `f28b70f`). This does not merge PR #22
into dev. Owner-reported focused evidence is recorded below; agent runs no tests.
Code stages: identity `b1e5d5`, confirmation `f4f3fd`, action preview `ccb8905`.
Owner's parallel VIEWER-fixture revision `4b6e450` and merge history are retained;
this follow-up restores its accidentally removed imports, without weakening tests.

### Completed implementation substeps (acceptance remains open)

1. **Bound runtime identity:** active tenant, exact channel/external-user membership,
   active bound User, eager role/permissions/model-type references, active chat
   binding for messenger, exact web subject/chat binding and matching session.
   Invalid IDs, missing/inactive/detached identity or tenant bypass refuse admission.
   Permission scope covers the entire admitted turn, not only identity lookup.
   Runtime owner authority requires an explicit membership SUPERUSER role; legacy
   NULL membership role never elevates the runtime actor. Platform rights still
   come from User permissions, not workspace ownership. No auto-link or router.
2. **Fresh authority + actor-bound consent:** identity reload after session load,
   before LLM iterations/tool dispatch, when confirming and again after pending
   clear before effects. Intents bind tenant/User/membership/channel/chat/external
   actor, session, role IDs, registry contract and canonical arguments, expire
   after one hour. Revoked identities/rights, changed roles/contracts/arguments,
   expired or legacy unbound intents deny. Another group actor cannot confirm,
   cancel, replace or `/stop` a bound pending intent. A staged/stopped batch does
   not run later effects and still accounts for all tool call IDs in history.
   Both registry dispatch APIs require a one-use in-process approval for
   `confirm=True`; approval is consumed before the handler, including child-task
   reuse prevention. Declared permission checks remain mandatory.
3. **Safe action contract:** `action_send` now registers its real handler, not
   the tier helper; action preview and logs require `botaction.view`. Only literal
   `dry_run=True` is accepted; live/non-boolean requests deny before storage.
   Preview validates positive ID, PENDING state, payload, tenant-owned references
   and guards; it stays PENDING and writes no approval/result/attempt changes,
   calls no transport/provider. Runtime preview requires confirmation; direct
   handler calls still enforce the permission. Log limits are bounded.
4. **Compatibility fixtures and documentation:** isolated ADMIN-bound Agent
   fixture, isolated non-superuser VIEWER privacy arrange actor (User.role_id is
   NOT NULL), owner scope only around the arrange insert, stronger PENDING/no
   approval DB assertions. No broad anonymous/operator test workaround or
   per-tenant reference-data reseeding. Board/ledger/runtime/tenancy docs updated.

### Bounded continuation: collection authorization and local round-trip

`collect_now` was the only one of 33 statically inventoried built-in @tool
handlers without a declared permission. `ec69981` adds **source.analyze**, matching
app/web/sources.py's existing collection gate (collection writes rows and can
cost LLM money, not merely view/edit a source). Both registry dispatch paths
already enforce declared rights before the handler. The generic None contract
is unchanged for custom tools; direct handler/raw-manager paths remain outside
this bounded fix. No new confirmation, queue policy, provider action or role
migration is introduced.

Six additional methods are WRITTEN, NOT RUN in the existing
`tests/test_tool_dispatch_permissions.py`: missing identity, view-only and
inactive denials before mocked run_job_inline; allowed result compatibility;
rights removal before the next dispatch; built-in static permission inventory.
The dispatch script now has 16 methods. The source inventory recognizes current
@tool declarations, not arbitrary alias/dynamic registration or end-to-end access.
Source-level AST/whitespace checks and the preserved injection-guard comparison
are completed; no test, application import, DB or external action ran here.

Owner reports setup --check, dispatcher privacy, identity boundary and
permission_scope passed, without a tested SHA/exact logs for that group.
Separately **61 passed** across test_llm_client_factory, test_ai_output_boundaries
and test_learning after integration on dev `274cb2c`; exact invocation/logs not
supplied. Neither is acceptance of new runtime/confirmation/action/collect code.
Do not duplicate unchanged owner checks without an integration reason.

Owner-local dev is dirty in chat.css, chat.html and test_web_chat.py: preserve
all three, no branch switch/stash/reset. Review the PR separately:

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
git fetch origin
git worktree add --detach ../social-media-ai-pr22-review origin/ai/identity-permissions-boundary
cd ../social-media-ai-pr22-review
source "$REPO_ROOT/.venv/bin/activate"
git rev-parse HEAD
python tests/test_tool_dispatch_permissions.py
python tests/test_runtime_identity_authorization.py
python tests/test_runtime_confirmation_authorization.py
python tests/test_action_tool_authorization.py
```

Use an unused worktree path; if it exists, inspect it instead of removing it.
The worktree does not automatically inherit an ignored .env: configure the
existing local environment privately before DB checks. Common POSTGRES_URL with
distinct DB_TEST_SCHEMA; no concurrent pytest on one schema, reset/drop or
migration. Then use the focused DB/API/web group below and a complete full suite
for this broad authorization integration. Report tested SHA + each command/result;
no full-suite pass from timeout. New corrections belong on the PR branch, not
owner's dirty dev; coordinate before committing from a detached worktree.

Next agent step while owner verifies: bounded raw-manager/legacy arrangement
review, not redo merged privacy or the completed runtime/confirmation/action
implementation. Keep this PR Draft pending evidence/review; no automatic merge.

### Current authorization matrix and remaining bypasses

Earlier core/API/CLI matrix below remains valid, with these runtime additions:

| Runtime path | Current contract | Residual limit |
| --- | --- | --- |
| No active bound User/membership/chat/tenant | Refuse before session/tool loop | Existing messenger membership with no User now needs explicit admin reconciliation |
| Workspace owner | Explicit membership SUPERUSER; only core source/task/scenario owner allowlist | Global rights require actual User permissions; legacy resolver/UI NULL-role inference still exists outside runtime |
| Tool dispatch | Fresh bound identity + declared right; confirmed tools also require consumed approval | Tools with no declared right and raw/undecorated manager methods need a separate coverage audit |
| Pending confirmation | Same actor/session/roles/args/contract + fresh right at effect boundary | No database CAS/transaction fence: simultaneous YES or revoke races are not exactly-once guarantees |
| Action preview | botaction.view + validated local PENDING preview only | Other publication paths/private legacy helpers are not globally disabled or redesigned |
| Trusted internal service/operator | Earlier explicit narrowly scoped grants retained | Never substitute these grants for interactive identity or wrap an entire handler/loop |

The original `_check_prompt_injection` guard is preserved byte-for-byte at AST
level. Dispatcher, runtime_process, parallel privacy handlers and digest delivery
are unchanged from fresh dev. No personal router, queue/lease redesign, billing
schema/migration, live sender activation or deployment.

### Prepared checks, not executed

**46 new standalone actual-source methods:** identity 18, confirmation 19,
action tools 9. The earlier 13 policy and 10 dispatch methods remain prepared.
Storage/providers are isolated/mocked in the standalone scripts; normal pytest
still loads DB conftest. Updated DB regressions are also NOT RUN by the agent.
Performed: static AST, changed-file whitespace, remote-content comparison and
preserved-guard/source checks only. No pytest collection, application import,
DB setup/reset/migration or live call. Syntax is not runtime acceptance.

Owner previously reported bootstrap 24/24, focused readiness 33/33 and the narrow
privacy rerun (two cases) passed. Exact tested SHA/logs were not supplied. These
are NOT evidence for this new runtime block or subsequent VIEWER/import edits.
The earlier full-suite run timed out at 120 seconds; no complete pass is claimed.

### Owner / local agent verification

Use project Python 3.12+ .venv with requirements/dev dependencies. Save local
edits before switching/pulling; do not force/reset them. Common POSTGRES_URL is
allowed, but use a DISTINCT DB_TEST_SCHEMA, never working DB_SCHEMA/public.
Do not run concurrent pytest sessions on one schema. No blanket reset/drop is
requested; inspect interrupted test state first. Standalone scripts need no DB,
but pytest uses the existing isolated-schema initialization in conftest.

```bash
git fetch origin
git switch ai/identity-permissions-boundary
git pull --ff-only
python tests/test_runtime_identity_authorization.py
python tests/test_runtime_confirmation_authorization.py
python tests/test_action_tool_authorization.py
python tests/test_tool_dispatch_permissions.py
python tests/test_identity_permissions_boundary.py
python -m scripts.setup_test_db --check
python -m pytest -q tests/test_runtime_identity_authorization.py tests/test_runtime_confirmation_authorization.py tests/test_action_tool_authorization.py tests/test_agent.py tests/test_web_chat.py tests/test_bot_actions.py tests/test_plan_tiers.py tests/test_dispatcher_log_privacy_db.py tests/test_permission_scope.py tests/test_manager_permissions.py tests/test_web_permissions.py tests/test_api_permissions.py tests/test_api_scope.py
python -m pytest -q
```

Expected: all commands finish successfully; refused identity/consent/tool paths
invoke no handler/provider, allowed paths retain contracts, preview stays
PENDING without approval/sending, DB privacy remains tenant-isolated. Record
exact tested SHA/commands/redacted output. Full suite must COMPLETE; a timeout
or unchanged warning count is not acceptance.

### Concrete continuation / gates

This linked implementation block is complete as prepared code, not all PRD-02
or stage-B security acceptance. Next in this lane: owner verifies the new block;
review raw manager/unannotated-tool coverage and remaining legacy arrangement
compatibility. Before merge, re-fetch dev and reconcile parallel edits. Do not
restore anonymous allowances to make regressions pass.
Legacy NULL-role reconciliation/automatic account linking, durable confirmation
CAS/transaction-fenced revocation and broader shared-session policy need explicit
contracts; no migration or exactly-once claim here. Revoke-then-restore does not
have a role epoch; single-process approval is not a distributed receipt.
Only then coordinate package 2 (general queue: stale workers, lease/heartbeat,
Job/AgentTask/scheduler consistency); package 3 is attempt/reservation DESIGN,
with separate schema approval. No automatic PR merge or sender activation.
Use the [current-stage board](README.md) for allocation, not historical next lists.

---

## Historical checkpoints — superseded continuation, retained evidence

The following records describe earlier revisions only. Their UNCHANGED/OPEN/next
statements are historical, not the current runtime/preview contract. Current
implementation and verification commands are above; retain historical evidence
without attributing prior passes to new code.

# Original boundary checkpoint

Status: PREPARED IN `ai/identity-permissions-boundary`, NOT MERGED OR ACCEPTED.
Baseline: dev `57b5612` after parallel PR #20. The first commit records merged
PR #18/#19/#20 without overwriting the parallel work tracker. Digest integration,
owner guard in `app/agent/runtime.py`, queue claims/leases and cost logic are unchanged.

## Bounded authorization matrix

| Caller/context | Source/task/scenario rights | Global LLM fleet, roles/users, queue | Platform role checks |
| --- | --- | --- | --- |
| Missing user, no explicit trusted grant | Denied, even with is_owner=True | Denied | Denied |
| Inactive user | Denied | Denied | Denied |
| Active workspace owner with a bound User | Owner override for source, agenttask, agentscenario only; same subject and tenant at scope entry | Only User.has_perm_for; ownership grants nothing | Actual platform role only |
| Active non-owner | User.has_perm_for | User.has_perm_for | Actual platform role only |
| Internal service grant | One model/action in the captured tenant; no implicit anonymous right outside that block | No service grant for global models | No service grant |
| Trusted operator tenant bypass | Valid model/action allowed; data bypass remains explicit | Valid model/action allowed | Valid role allowed |
| Trusted CLI operator permission scope | Valid rights persist when --tenant narrows data; interactive permission_scope clears this grant | Operator authority | Operator authority |

Malformed codenames/actions/roles are rejected before owner/operator shortcuts.
The workspace-owner allowlist is deliberately small, not all tenant tables:
being tenant-scoped is not enough to authorize external publication or queue control.
WebPerms uses the same owner allowlist; API keeps its existing platform-right
contract and now carries its authenticated, eager-loaded User into manager gates.
An inactive API user is refused before downstream processing.

## Bypass map and disposition

| Entry/path | Previous risk | This package / remaining limit |
| --- | --- | --- |
| core/permissions.has_permission / codename / has_role | user=None meant unrestricted access | Removed implicit anonymous allow; negative sync/async checks before effects |
| permission_scope(is_owner=True) | Unrestricted model override, including global fleet | Three configuration models, captured tenant, current subject; nested scope restoration |
| web/perms.WebPerms.can | Owner override also covered global/unknown models and invalid actions | Same limited owner rule and action validation |
| core/api_scope.ApiScopeMiddleware | Identity resolved but not carried into manager decorators | Authenticated User is ambient; API model dependencies are retained |
| cli/main._run_platform | Tenant narrowing previously relied on anonymous allow for manager writes | Explicit operator permission scope; tenant data remains narrowed |
| tasks/bootstrap | Anonymous default-task creation | Only agenttask.create around the insert |
| tasks/runner and jobs/enqueue | Anonymous one-shot disarming | Only agenttask.update around that write; no lease/scheduler redesign |
| monitoring/ingest, social/tg_client, checkpoint_manager | Anonymous source watermark/params update | Only source.update around the existing gated write, same tenant |
| agent/runtime | Messenger binding may have no User; User loads are not eager rights loads | UNCHANGED. Declared tool rights now deny missing/detached identities; existing guard preserved |
| TenantUser.is_owner / resolver | Legacy NULL role_id still means owner | UNCHANGED; migration/reconciliation requires an explicit owner decision |
| agent/tools call_tool / execute | Declared permission could be skipped by a direct caller | PREPARED: both paths check current registry metadata and scoped user before handler effects; execute returns a static permission_denied result |
| toolset/actions and undeclared tool rights | Actions registration/metadata contract incomplete; required_permission=None still ungated | OPEN. Authorization is not confirmation; no posting/activation added |
| managers/BaseManager raw create/update/delete and bookkeeping | Only selected manager methods carry decorators | OPEN. No claim of complete manager authorization coverage |
| operator HTTP middleware / standalone scripts | Trusted bypass and ambient identity assumptions | Preserve operator boundaries; audit access separately; scripts with no trusted scope may now be refused |

Service grants are trusted code delegation, not a replacement for caller
identity. They authorize the model/action, NOT field-level or row-level changes;
tenant managers still provide row isolation. The existing calls are intentionally
small. Never wrap a whole handler/LLM/tool loop in a service grant or take the
grant's model/action from external input. Capabilities may propagate to child
async tasks under ContextVar semantics; these write sites do not spawn tasks.

## Prepared tests, not executed

- 13 standalone actual-source boundary methods: anonymous/owner/global/inactive
  refusals, malformed rights, explicit operator authority, tenant/subject switches,
  exact service grant, nested scope restoration, sync/async refusal before effects,
  API downstream identity/inactive refusal and web global boundary.
- Existing permission-scope tests replace obsolete anonymous-allow contracts with
  stronger denials. Manager tests deny anonymous task/scenario/source writes;
  existing role allow/deny and explicit bypass assertions are retained.
- Web regression adds global-fleet/role/queue denials for a workspace owner.
- Legacy conftest already represents the operator; its authorization is now
  explicit, separately from the existing data-guard monkeypatch. Permission-scope
  and manager-security modules opt out via tenancy; their negative tests do not
  receive the operator grant. API arrange inserts use an exact test-local grant,
  never authority around the HTTP request.

Actually performed: static AST parsing of changed Python files and git diff --check.
NO test, pytest collection, application import, live provider/transport, DB setup,
reset or migration was executed. Static syntax is not runtime acceptance.

## Owner/local-agent commands

From repo root, use the project Python 3.12+ .venv. Install requirements and the
project's dev dependencies if missing; do not commit local .env/credentials.
Use the common POSTGRES_URL and a DISTINCT DB_TEST_SCHEMA, never working DB_SCHEMA
(default public). Do not run parallel pytest sessions on the same test schema.
The check command only diagnoses; normal pytest conftest initializes the test
schema. No reset/drop command is requested.

```bash
python tests/test_identity_permissions_boundary.py
python -m scripts.setup_test_db --check
python -m pytest -q tests/test_permission_scope.py tests/test_manager_permissions.py tests/test_web_permissions.py tests/test_api_permissions.py tests/test_api_scope.py
python -m pytest -q tests/test_agent.py tests/test_task_activity.py tests/test_task_run_now.py tests/test_cli_task_run.py tests/test_cli_collect.py tests/test_telegram_ingest.py tests/test_telegram_mtproto.py
python -m pytest -q
```

Expected: the standalone script exits zero; unidentified callers/owner-global
operations are refused before effects, real allowed writes and existing CLI/API/
web behavior remain intact, service bookkeeping remains tenant-isolated.
Record exact tested branch SHA, commands and redacted output; do not reuse prior
owner-reported green results as evidence for this package.

## Compatibility gates / no ready-to-merge claim

1. Some existing tenancy test arrangements and standalone scripts explicitly set
   a tenant but no actor before a gated create/update. They depended on the old
   anonymous allowance and may now fail. Inspect each arrange block: add a bound
   authorized actor or a narrowly scoped TEST-LOCAL arrange grant. Do not add a
   blanket grant to tenancy tests or weaken negative assertions/application guards.
   Complete this inventory and run the full suite before marking the PR ready.
2. Messenger-only owners lacking a User lose declared tool writes by design.
   Do not restore None bypass; reconcile identities separately and test it.
   Existing runtime User loads can return detached rights; eager identity loading
   and full admission policy still need a coordinated runtime follow-up.
3. Tools with no declared permission and raw manager paths are NOT made safe by
   changing this predicate. PRD-02/UX-02 remain open. Review dispatch/confirmation,
   stale membership/role revocation and action contract in the next bounded unit.
4. Preserve the owner runtime guard and parallel log/UI work. No personal-chat
   workspace router, new identity schema, queue lease/heartbeat/transaction package,
   budget schema/migration or digest reintegration belongs in this PR.

Next session: fetch fresh dev/open PRs and this branch; inspect the tracker and
owner test evidence; finish compatibility arrangements, then coordinate runtime
identity/dispatch changes. Queue is package 2; attempt-accounting/reservations
are package 3 and require separate schema agreement. Merge is owner's decision.


## Publication/synchronization checkpoint

Draft PR #22 was created with original implementation head `4244b5`. All 19
published payload files matched the prepared files. Runtime and dispatcher
sources were unchanged. Dev then advanced to `a88cd30` through documentation-only
PR #21; its tracker and next_tasks_handoff are preserved and augmented here.
13 tests remain prepared/unrun. Next: owner compatibility checks, not queue work.


## Owner evidence and fixture follow-up

Owner pushed `963bf4f` to this PR branch and reported these local results:

| Command | Owner-reported result |
| --- | --- |
| python tests/test_bootstrap_readiness_unit.py | 24/24 passed |
| python -m scripts.setup_test_db --check | Common PostgreSQL database; working public, isolated test_schema |
| python -m pytest -q tests/test_bootstrap_readiness_unit.py tests/test_api_health.py tests/test_setup_test_db.py | 33/33 passed |
| python -m pytest -q tests/test_dispatcher_log_privacy_db.py | 2/2 passed with the owner's previous fixture |
| python -m pytest -q | Interrupted by a 120-second timeout; NOT a completed pass |

These are OWNER-REPORTED, not rerun here. Exact tested SHA/full logs/environment
versions were not supplied; do not attribute the previous 2/2 result to the
fixture revision below or certify other tests from an unchanged warning count.

Follow-up replaces the arbitrary first-active-user lookup with a test-owned
active User (no role, no superuser flag), eager-loaded for deterministic rights
checks, and deleted in fixture teardown. Owner authority exists ONLY around
AgentTask arrangement; Job arrangement and the mocked handler/dispatcher run
without it. Added assertions verify that the actor's ordinary create right is
absent and the handler receives no ambient user/anonymous create grant. Existing
privacy/outcome/no-live-send assertions are preserved. No per-tenant role seeding
is introduced: model role permissions are platform reference data.

Fresh base is `eb49d1d3b6bb249393f2719e056c77f1a0516d4a` after parallel PR #23/#24/#25. Two conflicts were observed:
implementation tracker and next_tasks_handoff. Shared docs are aligned to the
fresh base before PR-branch synchronization; identity continuation is preserved
here and will be restored alongside the parallel sections afterward. Keep both
records; do not overwrite the new handler privacy code or reimplement it.

New fixture/assertions are PREPARED, NOT RUN. Only static AST and whitespace
checks performed. Run from the project .venv with dependencies and a DISTINCT
DB_TEST_SCHEMA on the common POSTGRES_URL, not working/default public; no parallel
pytest processes on one schema. Do not issue reset/drop without inspecting and
explicitly authorizing the isolated target after the interrupted run.

```bash
python -m scripts.setup_test_db --check
python -m pytest -q tests/test_dispatcher_log_privacy_db.py
python tests/test_identity_permissions_boundary.py
python -m pytest -q tests/test_permission_scope.py tests/test_manager_permissions.py tests/test_web_permissions.py tests/test_api_permissions.py tests/test_api_scope.py
python -m pytest -q
```

Expected: both privacy cases pass even with no pre-existing active account;
no owner scope reaches the handler; audit/notification/log behavior unchanged.
Record new tested SHA and redacted output. Full suite needs a suitable timeout;
an interruption is not acceptance. PR #22 remains draft/open, not merged into dev.


## Completed synchronization and exact continuation

`4735859` contains the new narrow fixture and owner evidence; `5596797` merges
fresh dev `eb49d1d` into the PR BRANCH (not PR #22 into dev). The two shared-doc
conflicts are resolved. Fresh handler privacy changes from PR #23/#25, runtime
and dispatcher are preserved; PR #25's merged status was checked through GitHub.
Tracker/next_tasks now hold both parallel work and the identity continuation.

Owner: git fetch origin; git switch ai/identity-permissions-boundary;
git pull --ff-only after saving local edits. Retest the NEW fixture with
`python -m pytest -q tests/test_dispatcher_log_privacy_db.py`, then the permission
commands above. Prior 2/2 is not this revision's result. Tests remain unrun by the
agent. Full suite must complete; its earlier 120-second timeout remains open.
No sender activation, runtime/router rewrite, schema/migration or dev merge.


## Latest owner confirmation

After the narrowed fixture publication, the owner confirmed that the requested
focused `tests/test_dispatcher_log_privacy_db.py` rerun passed. The handoff command
covers two parameterized privacy cases. This is OWNER-REPORTED success for the
new fixture, not an agent rerun; exact tested SHA/logs were not supplied.

Do not confuse this focused result with the earlier 120-second full-suite timeout.
Full-suite completion and the broader permission/identity compatibility gates
remain open. No approval to merge PR #22 or deploy/activate was given. Next:
collect remaining permission regression/full-suite evidence and continue the
bounded identity compatibility review.


## Next related unit — declared tool-dispatch authorization

Prepared in the SAME rights draft PR #22; not merged or tested. app/agent/tools.py
now checks each non-None required_permission before handler invocation in BOTH
call_tool and execute. The former raises PermissionDeniedError; execute returns
static error/code permission_denied without a routine denial traceback/log.
Unknown-tool, successful result and handler error-dict contracts are preserved.
Invalid declared rights (including an empty string) deny, not treated as None.

Ten actual-source standalone methods in tests/test_tool_dispatch_permissions.py
are WRITTEN, NOT RUN: missing/inactive identity, owner/global boundaries, malformed
rights, refusal before handler effects, no denial traceback/private arguments,
allowed local/explicit global rights, per-call registry/scoped-user recheck, and
unknown/undeclared/error-result compatibility. The script reuses the existing
actual-policy boundary loader; only imports/handlers/toolset are isolated. No real
provider, transport or DB calls. Pytest still uses the normal DB conftest.

This does NOT make undeclared tools safe, enforce confirmation in direct calls,
reload permissions from DB, solve revocation/membership/session freshness or
validate action arguments. Per-call recheck uses the current scoped User; a cached
ORM role/permission snapshot is not proof of fresh DB rights. Runtime/guard is
UNCHANGED. Do not wrap dispatch in a service/operator grant to pass tests.

## Concrete linked task list and continuation

1. Owner/local verification of this declared dispatch gate, the 13 earlier policy
   methods, permission/API/web/manager regressions and the complete suite. The
   narrowed privacy fixture rerun is already owner-confirmed, do not re-open it
   as an unexplained failure. No automatic ready/merge declaration.
2. Runtime identity in the RESOLVED tenant: scope messenger membership lookup to
   resolution.tenant_id, eager-load active User.role.permissions and model-type
   references; refuse missing/inactive/revoked/mismatched identity before tools.
   Preserve owner guard and exclude personal multi-workspace routing/new schema.
3. Confirmation: actor/tenant/current registry right binding and role/membership
   revocation on a later yes; avoid trusting stale pending metadata. Recheck fresh
   identity, not merely an in-memory policy snapshot. No external publication.
4. Action-tool contract: action_send is currently registered on the helper
   _auto_actions_forced_dry_run, not its real argument-taking handler; actions_log
   and action_send lack rights declarations. Audit/register correctly and define
   rights with negative tests, but avoid enabling live sends as an incidental fix.
   Preview/approve/send state semantics require a separate explicit safe contract.
5. Remaining raw manager write paths/bookkeeping and legacy tenancy arrange
   scripts: distinguish trusted internal delegation from interactive authority.
   Keep tenant guards and negative tests intact; no global test bypass.
6. Legacy NULL-role membership ownership: inventory and proposed reconciliation
   policy only; migrations/automatic account linking need separate owner agreement.

Next code unit after this dispatch change: runtime identity eager loading and
resolved-tenant membership binding, with mocked negative tests only. Coordinate
runtime file ownership before editing in parallel. Keep queue package 2 and
attempt-cost/reservation DESIGN package 3 separate; no digest reimplementation.

Owner commands (project .venv/dependencies, isolated DB_TEST_SCHEMA, no concurrent
pytest on one schema, no blanket reset/drop):

```bash
python tests/test_tool_dispatch_permissions.py
python tests/test_identity_permissions_boundary.py
python -m scripts.setup_test_db --check
python -m pytest -q tests/test_tool_dispatch_permissions.py tests/test_permission_scope.py tests/test_manager_permissions.py tests/test_web_permissions.py tests/test_api_permissions.py tests/test_api_scope.py tests/test_agent.py
python -m pytest -q
```

Expected: declared denials invoke no handler; allowed results and error contracts
remain compatible; report exact tested head/commands/redacted output. New tests
remain unrun by the agent; only static AST and whitespace checked. A full-suite
timeout is not acceptance. Final merge requires explicit owner instruction.
