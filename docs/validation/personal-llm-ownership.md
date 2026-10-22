# Personal LLM ownership implementation evidence

## Source and task allocation

Base synchronized: `dev` `ccce2801a640efd7a90704f37874d0bae1ec388a`.
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

## Coordinated UI evidence (attributed, offline only)

[Draft PR #230](https://github.com/Starck43/social-media-ai/pull/230) is stacked on
#229's feature branch. UI session owns the 10 UI templates/assets/helper/test files;
backend settings.py/router/middleware remain in #229. At integrated snapshot
`9221352990988869657e847dbd3d0914fed79056` (UI source `6dedebe` plus backend `e14c395`),
the UI owner reported exact published bytes and: 53 isolated handler/layout pytest
PASS, 24 new catalog browser cases PASS, 104 existing settings browser cases PASS,
and 14 catalog unittest PASS. Browser matrix: 320/390/768/1440, light/dark,
catalog/empty/no-workspace, with individual screenshot inspection. These counts
are separate reported suites, NOT 195 end-to-end/DB tests. No keys projected into
JSON; only id/name/uid/base_url/has_api_key. Decimal tariff DTOs are not serialized
wholesale. Follow-up UI HEAD `eeecde1156d81eb1d6518ccdf9e917078184b62a`
changes only settings-llm.js and its browser test: cleared partial rows no longer
retain sticky required validation. UI owner reported exact-byte rerun on eeecde1:
53 isolated pytest PASS, 14 catalog unittest PASS, 24 catalog browser cases PASS
(including positive submit/HTTPS/clear-required/payload). The 104 settings regression
cases were run at 9221352, not rerun at eeecde1. Backend hardening after e14c395
needs integrated revalidation; no earlier-SHA result certifies later commits.

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
5. Web/API/agent/worker dispatch: selected FK, same-owner fallbacks only for text,
   image, video and audio, scheduled stored payer, no inherited actor guessing,
   CSRF, exact personal route allowlist.
6. Integrated UI at backend+UI exact SHAs: default vs selected markers, sparse
   edits, special characters, desktop/mobile light/dark and no secret HTML/JSON.

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
