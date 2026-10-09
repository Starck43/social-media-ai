# Identity and permission boundary: owner handoff

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
| agent/tools direct dispatch and toolset/actions | Direct dispatch lacks a central gate; actions metadata/binding contract incomplete | OPEN. Confirmation is not authorization; no posting/activation added |
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
