# Admin model / personal billing acceptance

## Provider cost and compact diagnostic controls follow-up (P8-T20)

Remote tests verify the administrator's OpenRouter-shaped 470 input / 8 output /
478 total, empty-content, `length`, explicit-zero-cost response with the actual
client/probe/action source over HTTP/storage doubles. The pure journal parser and
pricing pipeline pass exact tokens and zero/free USD to a manager double when
scoped, and refuse a missing payer. This is not PostgreSQL persistence evidence.
API price is now shown separately from the configured-tariff estimate; the latter
retains the `llm_models.last_request_cost` contract. Supplier upstream costs and
reasoning detail counts do not replace retail cost or add duplicate output usage.
Model telemetry is not token history: an unscoped single-owner probe still has no
promised durable `llm_attempts` row. Full history in that mode is separate work.

Prepared local acceptance adds an empty-content/zero-price owner-mode case:
exactly one mocked HTTP, no personal dispatch/account prerequisite, zero API
price on screen, model counter plus tariff-estimate DB readback. Run the existing
suite sequentially through normal R8/R10 with root conftest; never run it with the
remote no-DB flags. This added case has not run remotely. No schema/config change,
real provider, retry or migration. Delivery to dev follows the owner's existing
explicit merge exception/local-validation deferral, not a full acceptance claim.

Offline browser coverage checks top-right SVG coordinates, icon/check feedback,
no visible copy-success paragraph, clipboard-denied selection fallback, keyboard,
no-JS, semantic status colors, contrast, mobile/desktop and dark/light themes.

## Single-owner testing follow-up (P8-T18)

This revision supersedes the historical disabled-billing refusal below. A real
admin test works in the existing single-owner mode without personal account,
workspace, cutover or bound setup. Same client, model telemetry, usage/estimate,
JSON diagnostics, permissions, CSRF, caps and no-retry behavior. When personal
accounting is enabled, the initiating administrator remains the payer and the
existing workspace/admission/reserve/settlement contracts are unchanged.

Prepared local acceptance now includes a flag-off, no-personal-account real test:
one mocked provider HTTP, no personal dispatches, normal model counter/cost
readback, no workspace selector or activation warning. Existing flag-on ledger
and denial tests remain. Remote coverage additionally exercises the ACTUAL client
flag-off admission branch and both-mode authority/CSRF with explicit offline
storage/HTTP doubles; it is not PostgreSQL acceptance. Browser fixtures verify
blank/custom owner-mode submit without a workspace field, as well as personal
workspace validation, JSON copy, no-JS and busy safeguards. Local acceptance is
updated, not remotely executed; normal R8/R10 follow-up remains deferred under
the owner's existing delivery agreement. No config activation or migration.

## Admin response diagnostics follow-up (P8-T17)

The explicit CONFIGURE/CSRF-protected real POST is the administrator's paid
operation. No extra confirmation field is required; no billing gate is removed.
Prepared local settlement tests now send **no confirm_real field**, require the
formatted provider JSON/status panel, and preserve the existing durable payer,
reserve/settlement, unknown usage and no-replay assertions. Missing workspace and
disabled billing remain original-status HTML refusals with no provider dispatch.

Remote focused coverage additionally includes `tests/test_admin_model_diagnostics.py`:
actual OpenAI client source over MockTransport with explicit admission/storage
and journal doubles; HTTP errors, transport timeout, missing account/cutover/model
bound, budget denial, empty text, provider JSON errors, safe copies and preserved
answers after accounting failures. This is **not** real ledger/DB acceptance.
Browser fixtures additionally check no redundant checkbox, actual form fields,
readable text/JSON/statuses, auto-focus and copy/manual-selection fallback.

The user's explicit implementation-and-dev instruction follows their in-session
merge exception/local-validation deferral. Required owner-local execution remains
unperformed, not a passing result. No real provider, schema/config changes or
runtime activation. Run the existing local acceptance sequentially under R8/R10
with normal root conftest and all guards below; exact remote evidence is in the PR.

## Admin request-mode follow-up (P8-T15)

The current candidate changes the admin short mode to authenticated metadata
`GET /models` (no generation) and forwards the real question, or the tiny default
when blank. This supersedes the historical fixed-prompt/admin-offline contracts
below; previously accepted results do not validate this follow-up.

Prepared local acceptance remains `tests/test_admin_model_billing_acceptance.py`:
- Metadata requests use a separate recorded list; explicit counts verify admin
  short dispatches exactly one GET, while web settings remain offline and no
  personal dispatch exists. CSRF and revoked/disabled authority still refuse HTTP.
- Real settlement checks are parametrized for blank (8 output tokens) and a
  supplied prompt (512, bounded by the model). The transport asserts exact prompt,
  limit, 20-second timeout and durable reservation before responding. Initiator
  payer, immutable settlement, unknown usage and no-retry cases remain intact.
- This changed PostgreSQL/ASGI suite has **not run remotely**. Execute sequentially
  through the existing R8/R10 local runner, with normal root conftest and all
  retained-fixture/network guards. No reset/migration or real provider call.
- Remote source/mocked coverage: `tests/test_admin_model_probe.py`,
  `tests/test_admin_model_billing_integration.py`,
  `tests/test_admin_model_permission_policy.py`,
  `tests/test_admin_model_request_modes.py`; browser fixture:
  `tests/admin_model_test_layout.cjs`. Exact revision/results belong in the PR.

## Historical status and scope

This revision is a **prepared, locally unexecuted acceptance suite**, not a
passing full integration report. Earlier owner-relayed runs are attributed
to their original revisions below. It adds tests to the combined review candidate, not to the
source branches. Implementation snapshot before these test-only changes:
`ace7c536300ae3ec1d5f9abaaa430bec4cdb74ad`.

Entry point: `tests/test_admin_model_billing_acceptance.py`. It intentionally
has a different name from the owner's unpublished
`tests/test_admin_model_probe_local_acceptance.py`; do not replace or import
that file, nor delete `test-results/`.

The earlier revision declared 19 test functions / 41 parametrized cases. This count is
from static AST enumeration, **not pytest collection**. Local collection may
fail before any test, and actual execution is still required. It is not the
entire repository suite or a complete billing subsystem audit.

## Safety boundary

- Do not execute this suite remotely. Owner relay is not schema maintenance
  approval. Respect any narrower current owner execution restriction.
- Use the normal current root conftest. It redirects before application imports
  and checks the existing schema/catalog read-only. The module independently
  refuses missing conftest, a schema other than `test_schema`, or a target other
  than `localhost`/`127.0.0.1:5432/social_manager`, before importing application
  models. Do not use `--noconftest` for this suite.
- Any unknown/missing table, constraint drift, missing reference catalog, unknown
  writer or interrupted-run uncertainty is a STOP. Do not create a new target,
  reset, drop, migrate, stamp, seed, repair columns or edit reference grants.
- Execute sequentially under R8/R10, using the existing configured environment
  privately. No environment values, JWTs, cookies, keys or full request headers
  belong in reports. Do not change `.env`, runtime config or source branches.
- This is **not a read-only test** after successful preflight: it creates
  synthetic users, workspace memberships, workspaces, provider/model rows and,
  in billing cases, synthetic personal accounts, cutover/bound-policy fixtures,
  dispatches, reservations, outcomes and ledger entries. These are bounded
  per-test fixture DML, not shared-catalog seeding or schema work. Never use
  an existing real payer/account/provider/model as a fixture.
- All generated fixture rows are retained, including financial evidence and
  referenced dependencies. Do not cascade-delete or rewrite immutable evidence
  to tidy a test run. `RETAINED_SYNTHETIC_FIXTURES`, CRUD-model and foreign-workspace
  output identify each unique run marker and owned IDs. Record them in the
  local human-audit test report; maintenance requires a separate owner decision.
- Authentication, password verification, CSRF, authorization, ORM, personal
  admission and settlement are real. `fixture_scope()` is only setup/readback;
  no ASGI request is wrapped in trusted bypass and `tenancy` disables the legacy
  conftest permission bypass. Do not mock these boundaries to get green results.
- Only external transport is replaced. HTTPX network clients use MockTransport
  with a synthetic `.invalid` provider/key; unexpected HTTP targets and sync HTTP
  are refused. A socket guard additionally permits only approved loopback
  PostgreSQL TCP; forbidden connection attempts are reported as test errors even
  if an application error handler catches them. No provider request reaches the
  network. Never substitute actual keys or allow real HTTP passthrough.
- Application construction and HTTP requests are in-process ASGITransport only.
  Do not run lifespan/startup, uvicorn, `app.runtime`, worker, listener, migration,
  sender or a second application process. The suite temporarily selects admin
  and billing feature flags in the test process only and restores them afterward.

## Declared contracts and interpretation

| Test function suffix | Cases | Assertions / limitations |
| --- | ---: | --- |
| `admin_list_details_and_forms_are_real` | 1 | Authenticated real list/detail/create/edit HTML; provider, capabilities, rates, max tokens and editable-field allowlist. |
| `admin_create_and_edit_roundtrip` | 1 | Actual SQLAdmin POST and ORM readback of precision, capabilities, booleans and limits; never selects global default. |
| `short_empty_question_and_shared_web_service` | 1 | Admin and workspace-owner web short checks with no question, same local success text and zero HTTP/dispatch. Shared service wiring also remains covered by source review/isolated contracts. |
| `mock_empty_and_escaped_question` | 2 | Empty-question error; valid mock and script-like question escaped, no HTTP. |
| `admin_csrf_actual_verifier` | 3 | Missing/tampered/actually expired signed tokens refused. Expiry uses an old signing timestamp, not sleep or a mocked verifier. |
| `non_console_identities_cannot_execute` | 3 | Anonymous, authenticated workspace owner and viewer cannot execute global admin actions. |
| `existing_operator_profile_matches_console_enforcement` | 1 | Real stored operator grants; list/detail/custom GET/unified POST must match actual grants. No grants are altered; controlled VIEW-only policy is tested separately below. |
| `fresh_authority_after_page_load` | 2 | Disable or revoke role of this test's own administrator after opening page; next POST denied using the existing session. |
| `unknown_or_multiple_selection_is_refused` | 5 | Missing in-range model (404); composite, zero, malformed and overflowing IDs (422 before SQL). |
| `paid_preconditions_before_dispatch` | 4 | Missing consent/workspace, inactive workspace, disabled billing feature; zero HTTP/dispatch. |
| `real_admission_cannot_bypass_personal_safeguards` | 4 | Missing personal account/cutover/reviewed bound and insufficient cap fail before HTTP, using the real admission manager. |
| `real_fixed_probe_initiator_payer_durable_settlement` | 1 | Admission/immutable reserve must exist **inside the transport handler before response**. Trusted initiating admin, not submitted payer or workspace owner; fixed prompt, 8 output tokens, 20-second client timeout, escaped answer, exact tariff settlement and telemetry; one HTTP. |
| `unknown_usage_keeps_reserved_liability_not_free` | 1 | Missing provider usage remains unknown in UI and retains reserved liability; no false free/settled/released state. |
| `transport_failure_never_replays` | 2 | Synthetic timeout / 503: one HTTP, one dispatch, no automatic retry. This asserts timeout configuration and exception handling, not a 20-second wall-clock delay. |
| `estimate_save_failure_preserves_answer_without_second_http` | 1 | SQLAlchemy event fails actual estimate UPDATE; settled ledger and escaped response remain; warning and exactly one HTTP. No manager replacement. |
| `legacy_paid_route_is_inert` | 1 | Authenticated CSRF-valid old paid endpoint returns 410, no dispatch. |
| `web_settings_csrf_and_workspace_boundary` | 3 | Invalid CSRF/viewer cannot run short check; foreign posted workspace ID cannot switch active owner context. Short check itself does not mutate workspace configuration. |
| `short_and_mock_do_not_require_billing_or_workspace_selection` | 1 | Both local modes still work with billing off and invalid unused workspace field. |
| `invalid_model_configuration_cannot_dispatch` | 4 | Inactive model/provider, absent key, non-OpenAI format cannot dispatch. |

### VIEW/CONFIGURE verification layers — revised, not a policy change

The old test required the single shared operator profile to be VIEW-only for
LLMModel. Fixed/unique platform role codenames mean this is not an owned fixture
we can provision without changing other users' grants. A failed precondition
was not evidence of an authorization bypass. Do not edit shared grants.

Verification is now explicitly split:

1. `test_existing_operator_profile_matches_console_enforcement` uses real login,
   fresh DB role/permission reads and real application requests. It verifies
   list/detail and custom GET/unified POST against this existing profile,
   including denial when CONFIGURE is absent. No role/permission/storage/auth
   mock is introduced into the DB acceptance module. Actual observed actions
   are printed in the private evidence for this owned test user.
2. `tests/test_admin_model_permission_policy.py` executes unchanged User
   permission method bodies, actual enum/resource catalog, actual SQLAdmin
   authorization backend and the actual unified POST body on controlled
   in-memory permission rows. It explicitly asserts VIEW-only allows reads but
   denies configuration, CONFIGURE allows testing, UPDATE does not configure,
   other-resource grants do not apply, inactive/workspace identities cannot
   inherit access, revocation is fresh, storage failure denies and CSRF remains
   required. Identity/storage, CSRF and final dispatch are declared doubles.
   SQLAlchemy mapped field construction is omitted; no app/settings/engine or
   ASGI application is started. This is **isolated policy evidence, not actual
   VIEW-only DB-profile or browser/ASGI integration evidence**.

The original security contract is unchanged. The verification plan replaces
an unavailable shared-catalog prerequisite with separately attributable policy
and real-profile checks; it does not claim a VIEW-only role was persisted or
used through real DB-backed login. The owner must review this evidence split
and the new real-profile test still needs local execution before closeout.
No assertion is replaced by a skip/xfail, and no production authorization code
or grants are changed. The 41-case DB suite no longer stops solely because the
seeded profile differs from an artificial VIEW-only fixture.

## Relayed execution and model-ID correction

The owner relayed collection/execution of 40 cases at
`4f290aee981977ec5c950740bedd17a38d0b22a6`, with two failures: the explicit
VIEW-only operator-profile precondition and missing-model 500 versus 404.
Exact numeric command exit codes, complete counts and a database exception trace
were not supplied; do not reconstruct them or call the suite accepted.

The missing-model fixture supplied `2**63 - 1`, but `LLMModel.id` is PostgreSQL
INTEGER, not BIGINT. The asyncpg SQL dialect binds that predicate as INTEGER;
the out-of-range value is consistent with the reported 500 rather than an
ordinary absent-row result. The exact local exception remains unverified.
The test now distinguishes an absent in-range ID (`2**31 - 1`, expected 404)
from an overflowing ID (expected 422). The application rejects positive IDs
above INTEGER maximum before querying; this is not exception swallowing or a
change of an ordinary missing model from 404 to 422.

The VIEW-only failure establishes an unavailable test precondition, **not a
proven authorization bypass or product defect**. Neither the existing role's
precise grant set nor a bypass was demonstrated by that assertion. No shared
grants were changed. That old fixture prerequisite was left blocked; the revised two-layer route
above makes the precise evidence boundary explicit. It is not retroactive
acceptance of the earlier failed test.

## Owner-relayed narrow model-ID verification

At `2673c395a7f838a6203f0fe4f273e950bc4095d9`, the owner explicitly approved
and relayed a clean-worktree run of only
`-k unknown_or_multiple_selection_is_refused`: collection exit 0, execution
exit 0, **5 passed, 0 failed, 0 skipped, 36 deselected**. The compact report
called the deselected cases skipped; they were not executed. No schema/grant/
.env/runtime changes or real API calls were reported. The human-audit report
and retained fixture IDs were not independently inspected. This closes only
the assigned model-ID scope, not this revised permission test or full suite.

## Local execution assignment

Pin the exact fresh PR head from the handoff, preserve all other worktrees,
and read `AGENTS.md`, `docs/COLLABORATION.md`, `docs/COLLABORATION.md` and
`.codex/agents/test-runner.toml`. Check `pgrep -fl pytest` separately before
collection and execution: exit 1 means no matching process; an unknown process
blocks DB-backed checks. Do not overlap execution routes.

Through primary OpenCode test-runner (`build`, model
`QwenPlan/deepseek-v4-flash-0731`), use the existing project `.venv` and private
child-process dotenv wrapper if the review worktree has no private environment:

```python
# run_private semantics; no environment dump, no parent-shell export.
import subprocess
import sys
from dotenv import load_dotenv

load_dotenv("/Users/admin/Projects/social-media-ai/.env", override=False)
raise SystemExit(subprocess.call([sys.executable, *sys.argv[1:]]))
```

From the pinned review worktree, these are the **only DB pytest scopes**:

```sh
run_private -m pytest -p no:cacheprovider -o addopts='' tests/test_admin_model_billing_acceptance.py --collect-only -q --tb=short --no-header
run_private -m pytest -p no:cacheprovider -o addopts='' tests/test_admin_model_billing_acceptance.py -q --tb=short --no-header -rP
```

`run_private` is a private wrapper defined in the relay task, not a repository
command. `-rP` preserves owned-row manifests for passed tests in human evidence;
do not publish raw application logs. No full suite, unpublished acceptance file
or layout fixture is implicitly included. Prior isolated 94-test / 56-layout
results belong to `ace7c536300ae3ec1d5f9abaaa430bec4cdb74ad`, not to a new
ASGI/DB execution. Run or reuse them only with accurate revision attribution.

For the isolated policy file, use a separate no-DB command with `--noconftest`
and the same `-p no:cacheprovider -o addopts='' -q --tb=short --no-header`
flags. Never apply `--noconftest` to the DB acceptance file.

If primary OpenCode is unavailable, report BLOCKED. A secondary route may be
used only per the current R10 fallback contract, not by silently executing
pytest in the coordinator. The test agent has no authority to change application
behavior, fixtures, schema, grants, source branches or Git history to resolve a
failure; return the exact failure/precondition and a proposed correction instead.

## Evidence and completion

Write human-audit evidence only to `.agents/tmp/test-report.md`: tested full SHA,
clean/dirty paths, exact collection/execution commands and numeric exit codes,
actual collected/passed/failed/skipped/error counts, per-contract outcomes,
owned fixture markers/IDs, blockers and remaining scenarios. Do not call a
collection pass an execution pass. Report setup failures separately from failing
assertions; the actual stored-profile outcome and the isolated controlled-profile policy
results must be attributed separately.

Return the R10 compact `TEST RUN` block directly. The coordinator does not open
the runner's evidence file. The owner may relay the human report, without secrets.
No Ready, merge, dev push, source-branch change, deployment or schema operation
is authorized by this document. Code review approval from schema and source-lane
owners remains distinct from local execution evidence and explicit merge approval.
