# Personal LLM ownership implementation evidence

## Source and task allocation

Base synchronized: `dev` `ccce2801a640efd7a90704f37874d0bae1ec388a`.
Backend branch: `feat/personal-llm-ownership`, Draft PR #229.
P3-T93: personal provider/model ownership and atomic bundle/default service.
P3-T94: transactional manager source/scenario bindings and DB definition guards.
P3-T95: chat model numeric FK and shared owner/provider/tier eligibility.
P3-T96: coordinated remote UI session, `ui/llm-provider-settings`.
No task is DONE; required PostgreSQL/ASGI and integrated UI evidence remains pending.

## Implemented contract

- Providers/models have immutable `owner_user_id`; model/provider ownership is
  enforced by a composite FK. Shared among one user's workspaces, not all users.
- Active actor selects the personal catalog; background LLM work resolves stored
  task payer consent (never workspace-owner guessing or an owner ID in payload).
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
  Scenario bindings are checked in the manager and migration-defined DB guard.
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
- Separate venv; SQLAlchemy 2.1.6, Pydantic 2.14.1, FastAPI 0.159.1,
  HTTPX 0.28.1, cryptography 50.0.2. No app bootstrap, engines, DB, .env or network
  in the suite. Fictitious example.invalid URL and fake-encrypted markers only.

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
6. Integrated UI at backend+UI exact SHAs: default vs selected markers, sparse
   edits, special characters, desktop/mobile light/dark and no secret HTML/JSON.

## Coordination and inherited work

Preserve social Platform PR #224; it is not LLMProvider. Its offline FK inventory
snapshots need refresh after owner FKs. PR #217 overlaps task argument validation
but has older queue-manager code: no wholesale manager replacement. Existing dev
pending-occurrence invalidation and source-admission behavior are preserved.
No deploy, merge, production/shared-test schema changes, live provider call, or
full-suite acceptance is claimed.
