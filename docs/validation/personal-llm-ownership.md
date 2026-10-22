# Personal LLM ownership implementation evidence

## Source and task allocation

Initial audit baseline: `dev` `ccce2801a640efd7a90704f37874d0bae1ec388a`.
Latest synchronized dev: `92dcb948de24c55ab82dccdba81e792945b5ac48`.
Final tested integrated source: `d9f7c473605c3bf14d6495288abcc1cdb8ddb9a4`
(UI branch; backend `f6674b65977bed4e573b5ecc68aba55b501d7d94` plus UI `eeecde1`).
Later documentation-only commits do not change the tested application/test bytes.
Backend branch: `feat/personal-llm-ownership`, [Draft PR #229](https://github.com/Starck43/social-media-ai/pull/229).
Initial implementation: `e14c3950b8846ea124e08ecbf57e703c8c6506d6`.
Reviewed incoming dev: `92dcb948de24c55ab82dccdba81e792945b5ac48`; its task+links
transaction is preserved, with caller-session support in the task manager.
P3-T93: personal provider/model ownership and atomic bundle/default service.
P3-T94: transactional manager source/scenario bindings and DB definition guards.
P3-T95: chat model numeric FK and shared owner/provider/tier eligibility.
P3-T96: coordinated remote UI session, `ui/llm-provider-settings`.
No task is DONE; required PostgreSQL/ASGI acceptance is BLOCKED pending an approved
isolated target and sequential owner-local validation. Offline UI evidence follows.

## Audit of the four requested boundaries

1. Sources/scenarios/tasks/jobs/digests/analytics use tenant-scoped models. This
   isolates stored records; workspace creation does not automatically provision
   one record of each kind. Generic association writes had missing cross-workspace
   validation: this candidate adds atomic task/source/scenario checks and guards.
2. At baseline ccce280, LLM providers/models were a global catalog without user
   ownership. The candidate changes them to one personal catalog per user, shared
   by that user's workspaces. Migration is defined, not applied.
3. Chat configuration is workspace-specific. Baseline selected a model by name;
   candidate uses numeric FK and shared owner/provider/activity/tier eligibility.
   Different members of a workspace do not gain another member's personal model
   or credentials just because that workspace stores its model ID.
4. Social Platform is still a global system catalog, including across different
   users; UserCredential is personal. Therefore it is shared across one user's
   workspaces, but is NOT a separate per-user Platform catalog. This change does
   not convert Platform into LLMProvider or change that social-platform boundary.

## Implemented contract

- Providers/models have immutable `owner_user_id`; model/provider ownership is
  enforced by a composite FK. Shared among one user's workspaces, not all users.
- Active actor selects the personal catalog; background LLM work resolves stored
  task payer consent (never workspace-owner guessing or an owner ID in payload).
  Pure collection without payer/initiator explicitly suppresses inherited personal
  identity, even when a caller has an active actor/worker catalog context.
- Provider numeric ID is immutable; `name` and owner-unique string `uid` are editable.
  Display model names remain labels, not identity or authority.
- `personal_catalog.py`: `catalog_metadata`, `save_provider_bundle`,
  `set_default_model`, `chat_model_options`, `validate_chat_model`.
  Every public method checks trusted active actor ID. No secret in read DTOs.
  Bundle is one owner-locked transaction; absent fields/models are preserved.
- Defaults are per-user/per-overlapping-capability. Owner row locking serializes
  bundle/default writes. UI distinguishes explicit `is_default`, effective default,
  and workspace-selected `agent_model_id`.
- Model selection and fallbacks stay inside one personal catalog; active provider,
  model, owner membership and current configuration are checked before HTTP.
- Task source replacement validates/locks the parent and every source before
  deleting links. Missing/foreign/task-owned IDs reject the whole replacement.
  Caller-owned sessions are forwarded without separate commit so task row and
  source links remain one transaction (dev 92dcb94 behavior). Scenario bindings
  are checked in the manager and migration-defined DB guard. Source owner guards
  include INSERT/UPDATE; link guards use SHARE to block concurrent non-key edits.
- Raw SQLAdmin LLM create/edit is disabled: use validated personal self-service.
  Platform inspection grants never authorize decrypting another user's key.

## Ownership migration / operational boundary

Migration definition 0109 is NOT applied. Existing providers/models are assigned
only with explicit `-x llm_legacy_owner_id=2`, reflecting the owner's instruction
relayed by the coordinated settings session. User 2 must exist and be active.
No secret duplication, guessed owner or silent cross-tenant data repair.
Provider UIDs start at `legacy-<numeric id>` and can be edited without changing FKs.
Unambiguous authorized legacy chat names become FK IDs; unmatched names need
manual re-selection. Old `agent_model` is retained only as legacy evidence.
Invalid legacy task/source/scenario bindings stop upgrade before DDL.
Downgrade refuses colliding provider display names rather than deleting data.

## Sandbox-executed evidence

- `python -m compileall -q app migrations/versions/0109_personal_llm_ownership.py`:
  completed, exit 0. Syntax only, no application startup or migration execution.
- `/data/social-audit-venv/bin/python tests/ai/test_personal_catalog_unit.py`:
  14 passed, 0 failed, 0 skipped. Real canonical provider/model mappings and actual
  service source, explicit fake storage/crypto/actor dependencies. Fake rollback,
  owner/default/eligibility checks are NOT real SQL transaction/isolation proof.
- `/data/social-audit-venv/bin/python tests/security/test_personal_scope_bindings_unit.py`:
  16 passed, 0 failed, 0 skipped. Actual-source context/path helper, AST-extracted
  task binding/BaseManager methods with local doubles: owner scope/restore,
  explicit denied background scope, mounted API namespace boundaries, foreign/
  missing/task-owned sources before link deletes, and caller-session no-commit.
  Not DB isolation, middleware/HTTP permission or real transaction proof.
- Separate venv; SQLAlchemy 2.1.6, Pydantic 2.14.1, FastAPI 0.159.1,
  HTTPX 0.28.1, cryptography 50.0.2. No app bootstrap, engines, DB, .env or network
  in the suite. Fictitious example.invalid URL and fake-encrypted markers only.

## Final integrated offline evidence (attributed)

[Draft PR #230](https://github.com/Starck43/social-media-ai/pull/230) is stacked on
#229's feature branch. UI owns the 10 UI templates/assets/helper/test files;
backend settings.py/router/middleware remain in #229. Final integrated HEAD
`d9f7c473605c3bf14d6495288abcc1cdb8ddb9a4` includes backend `f6674b6`, UI `eeecde1`
and dev `92dcb94`. The coordinated UI owner reported the following completed
rerun on that exact HEAD, with a clean worktree and all 10 UI/test files hash-verified:

| Completed check | Result | What it does NOT prove |
| --- | --- | --- |
| Isolated web handler/layout pytest | 53 PASS | Real session middleware, HTTP/ASGI authorization or persisted writes |
| Catalog unittest, actual mappings and fake storage/crypto | 14 PASS | PostgreSQL constraints, transaction isolation or rollback |
| Scope/bindings unittest, source-loaded methods and doubles | 16 PASS | Real connection locking, trigger enforcement or DB atomicity |
| Catalog offline browser fixtures | 24 PASS | Real authenticated page GET/POST or database effects |
| Existing settings offline browser fixtures | 104 PASS | Runtime/DB acceptance or the entire project suite |

Total: **83 isolated checks and 128 offline browser cases**. These are distinct
suites, not 211 end-to-end/DB checks. The earlier 9221352/eeecde1 results are
superseded for final offline evidence; integrated revalidation is no longer pending.
Real PostgreSQL/ASGI acceptance is still NOT RUN.

Reported exact-HEAD commands (the runner paths are sandbox-specific, not setup
instructions or a replacement for repository R8/R10 test routing):

```text
python -m pytest tests/unit_web/test_settings_llm.py tests/unit_web/test_settings_layout.py --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
python tests/ai/test_personal_catalog_unit.py
python tests/security/test_personal_scope_bindings_unit.py
node tests/web_settings_llm.cjs /data/llm-ui-visual /data/settings-vendor
node tests/web_settings_layout.cjs /data/llm-ui-regression /data/settings-vendor
```

Browser commands used the runner's preinstalled node modules and local Chromium.
Browser matrix: 320/390/768/1440, light/dark, catalog/empty/no-workspace;
positive synthetic submit, HTTPS/row validation, cleared partial-row required
state, provider prefill/manual UID, numeric chat selection/probe, payload,
unique IDs, overflow and page errors. Screenshots were individually inspected,
including final dark desktop multirow and light mobile chat selection. No key
is projected to JSON: only id/name/uid/base_url/has_api_key; Decimal tariff DTOs
are not serialized wholesale.

The UI owner also reported Black/isort/diff-check PASS and GitGuardian success
for integrated HEAD (run `119913994817`). Reported runner: Python 3.13.14,
FastAPI 0.143.2, Starlette 1.8.0, SQLAlchemy 2.1.4, Jinja2 3.1.6,
pytest 9.1.1, pytest-asyncio 1.4.0, Chromium 153.0.8010.12. This is a separate
environment from the backend unittest venv described above. Complete attributed
commands/environment are retained in PR #230. Backend coordinator independently
reconstructed published backend f6674b6 + UI eeecde1 and reran compileall plus
14+16 unittest checks successfully, without app bootstrap or DB.

Backend mounts the UI router, so #229 alone is not an independently releasable app.
After **required real acceptance**, merge #230 into the feature branch first,
then the complete #229 into dev. Both remain Draft; do not release an intermediate
backend without the mandatory UI module. No merge is performed by this work.

## Required owner-local acceptance (sequential, R8/R10)

1. Read-only target/writer/backup inspection; review 0109 against actual 0108
   catalog, active user2 and retained legacy rows. Do NOT infer migration permission.
2. Reviewed normal upgrade and downgrade refusal/rollback in an approved isolated
   target, including schema-qualified FK/trigger parity and unchanged historical
   provider/model IDs, encrypted keys, analytics and billing evidence.
3. One user/two workspaces and second user: direct/list/update/delete/default,
   foreign IDs, inactive owner/provider/model and membership revocation.
4. Real two-connection bundle/default races, rollback on foreign Nth model,
   duplicate UID; task source replacement rejects all missing/foreign IDs atomically.
5. Web/API/agent/worker dispatch: selected FK, same-owner fallbacks only, scheduled
   stored payer, no inherited actor guessing, CSRF, exact personal route allowlist.
6. Real authenticated UI at the jointly accepted source SHA: default vs selected
   markers, sparse edits and persisted atomic bundle/default changes. Offline
   fixture/browser coverage above is completed, not a substitute for this gate.

## Coordination and inherited work

Preserve social Platform PR #224; it is not LLMProvider. Its offline FK inventory
snapshots need refresh after owner FKs. PR #217 overlaps task argument validation
but has older queue-manager code: no wholesale manager replacement. Existing dev
pending-occurrence invalidation and source-admission behavior are preserved.
No deploy, merge, production/shared-test schema changes, live provider call, or
full-suite acceptance is claimed.
