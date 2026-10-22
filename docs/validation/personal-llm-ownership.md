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
- Model selection and fallbacks stay inside one personal catalog **for every
  capability** (text, image, video, audio): `resolve_default_model`,
  `capability_candidates` (the analyzer failover queue) and
  `get_model_for_capability` share one ownership predicate, so a fallback cannot
  decrypt another owner's key. A scenario that pins a model id — the analyzer's
  first priority — passes the same gate, so a pin left over from the legacy
  catalog falls back instead of reaching a foreign key. `AGENT_MODEL` is a name
  lookup through the same gate — an ownership bypass it is not. Active provider,
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

## Owner-local PostgreSQL/ASGI acceptance (2026-10-22)

Run on the owner's machine against an **isolated clone** of the local 0108
database (`social_manager` → `social_ai_llm_accept`), never the working schema
and never `test_schema`. A `pg_dump -Fc` backup of the clone was taken before the
first upgrade; the clone is dropped after the run. PostgreSQL 15.14. Head:
backend `5f7aaa2`, with the #230 UI source (`d9f7c47`) merged locally for the
web settings routes — the merge is not pushed.

### Preflight (read-only, actual 0108 catalog)

- Revision `0108`; 5 providers, 7 models, 4 active users, 73 workspaces.
- Reviewed owner user 2 exists and is active; web memberships in workspaces 1 and 695.
- Legacy `tenants.agent_model`: only workspace 695 carries
  `'Gemini 3.1 Flash-Lite'`, which has no catalog row — it must stay unmapped.

### Migration (22/22 checks)

- Upgrade without `-x llm_legacy_owner_id=2` is refused before DDL; revision stays 0108.
- An inactive owner 2 is refused before DDL.
- A pre-existing cross-workspace task/source link is refused before DDL and no
  owner column is added.
- Upgrade with the reviewed owner reaches `0109`; providers get `legacy-<id>`,
  every provider and model row is owned by user 2.
- Provider/model rows, encrypted keys, tariffs and numeric IDs are identical to
  0108; only `owner_user_id`, `uid` and `agent_model_id` are added.
- An authorized unique legacy name maps to its row; a tenant without membership
  and an ambiguous name stay NULL.
- Downgrade refuses colliding provider display names; after cleanup it restores
  the 0108 structure **exactly** (columns, constraints, indexes, triggers and
  functions compared as a fingerprint), keeping rows, keys and rates.
- A round trip re-applies 0109 with one owner and no unowned model.

### Guards on the upgraded schema (12/12)

Task/source/scenario bindings: cross-workspace link, source `owner_task_id`, task
`tenant_id`, task→scenario and task-owned source links are refused; same-workspace
writes succeed. Personal catalog: owner and model `provider_id` are immutable,
`uid` and display name are editable, duplicate `uid` within one owner is refused,
duplicate display names are allowed, a model cannot point at another owner's
provider (composite FK), and deleting an owner with a catalog is RESTRICTed.

### ASGI, permissions, CSRF and dispatch (27/27)

Two registered users in separate workspaces over real ASGI: atomic provider+model
bundle save; the key is stored encrypted and never echoed; a save without CSRF is
403 and creates no row; one default per owner and capability; another owner's
catalog is not rendered and a foreign provider edit is refused unchanged; chat
`agent_model_id` accepts the owner's model and refuses a foreign id; the bearer
API lists only the owner's providers and refuses a foreign id; two concurrent
bundle writes leave exactly one default; dispatch accepts the owner's model in the
owner's workspace and refuses a foreign owner, an inactive provider and a revoked
membership; image resolution skips another owner's model; tariff lookup stays in
the acting catalog by row identity, and a foreign row id cannot price the attempt.

### Not executed here / remaining

- The #230 browser suite was not run on this machine (`playwright` is not
  installed here); the UI owner's sandbox evidence (24 catalog + 104 layout
  cases) stands.
- `alembic check` still reports the known repo-wide model/schema naming drift
  (schema-qualified FKs, `ix_public_*` indexes, enum and comment differences) on
  unrelated tables. It predates 0109 and is unchanged by it.
- No production or staging target, no working-schema migration, no deploy, no
  live provider call.

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
5. Web/API/agent/worker dispatch: selected FK, same-owner fallbacks only for text,
   image, video and audio, scheduled stored payer, no inherited actor guessing,
   CSRF, exact personal route allowlist.
6. Real authenticated UI at the jointly accepted source SHA: default vs selected
   markers, sparse edits and persisted atomic bundle/default changes. Offline
   fixture/browser coverage above is completed, not a substitute for this gate.

## Review fixes (2026-10-22, on `f6674b6`)

A requested review of the draft found three gaps between the stated contract and
the source. All are fixed here; none is claimed as PostgreSQL acceptance.

- **Media parity.** `resolve_default_model` owner-filtered `text` only, while the
  analyzer resolves image/video through `get_model_for_capability` and failover
  through `capability_candidates` — both built a fleet-wide candidate list, so a
  media stage or a fallback could select a model owned by another user and
  decrypt that provider's key. One predicate
  (`personal_catalog.eligible_owned_model`) now gates all three paths for every
  capability, with the tier filter applied per capability.
- **`AGENT_MODEL`.** The env lookup returned the first row with that name from
  any owner and skipped provider activity; it now passes the same
  ownership/activity gate and only bypasses the workspace tier. A foreign or
  unknown name falls back to auto-resolution inside the acting catalog.
- **Tariff identity.** `AIAnalyzer._price_usage` priced a provider-reported model
  name with `filter(model_id=..., is_active=True).first()` — across every owner,
  so with personal catalogs a workspace could inherit another user's rates. It
  now resolves the exact row the attempt used (`request.llm_model_id`, written
  before the HTTP call); a legacy name-only envelope is scoped to the acting
  catalog and stays unknown when the name is ambiguous. `attempt_journal`'s
  unique provider/model rule is unchanged and already refuses to price an
  ambiguous identity.
- **Documentation.** Remaining global-catalog statements were corrected in
  `MODELS.md`, `TENANCY.md`, `API.md`, `ADMIN.md`, `CONFIGURATION.md`,
  `AGENT.md`, `DIGEST.md` and `.env.example`. The digest table had claimed
  `AGENT_MODEL` was consulted although that path never reads it.
- **Offline suites.** `tests/ai/test_llm_text_eligibility.py` was already red on
  this branch (its AST scope predates the catalog gate and the cache-price
  helper); the doubles now cover those imports and the suite passes again. New
  coverage: `eligible_owned_model` for image/video/audio ownership, tier and
  activity in `tests/ai/test_personal_catalog_unit.py`, plus a source-level guard
  for the three resolution entry points and the env gate in
  `tests/security/test_personal_scope_bindings_unit.py`.

Evidence: `pytest tests/ai/test_llm_text_eligibility.py
tests/ai/test_personal_catalog_unit.py
tests/security/test_personal_scope_bindings_unit.py --noconftest -q` —
**48 passed, 38 subtests passed**, no database, engine, `.env`, network or
provider call. This does not replace the required real acceptance below.

## Coordination and inherited work

Preserve social Platform PR #224; it is not LLMProvider. Its offline FK inventory
snapshots need refresh after owner FKs. PR #217 overlaps task argument validation
but has older queue-manager code: no wholesale manager replacement. Existing dev
pending-occurrence invalidation and source-admission behavior are preserved.
No deploy, merge, production/shared-test schema changes, live provider call, or
full-suite acceptance is claimed.
