# Admin model / personal billing acceptance

## Status and scope

This is a **prepared, unexecuted owner-local acceptance suite**, not a passing
integration report. It adds tests to the combined review candidate, not to the
source branches. Implementation snapshot before these test-only changes:
`ace7c536300ae3ec1d5f9abaaa430bec4cdb74ad`.

Entry point: `tests/test_admin_model_billing_acceptance.py`. It intentionally
has a different name from the owner's unpublished
`tests/test_admin_model_probe_local_acceptance.py`; do not replace or import
that file, nor delete `test-results/`.

The module declares 19 test functions / 40 parametrized cases. This count is
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
| `view_does_not_grant_configure_existing_operator_profile` | 1 | **Conditional environment gate**, below. Existing VIEW-only operator can list but cannot GET/POST test action. |
| `fresh_authority_after_page_load` | 2 | Disable or revoke role of this test's own administrator after opening page; next POST denied using the existing session. |
| `unknown_or_multiple_selection_is_refused` | 4 | Missing model, composite selection, zero and malformed ID. |
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

### VIEW/CONFIGURE limitation — do not hide it

Platform roles are fixed to `user`, `operator`, `admin`, with a unique codename.
A second custom operator-role fixture cannot be inserted safely. Do not change
shared `operator` grant links just for this test.

The VIEW-only test reads the existing operator profile. If it does not grant
LLMModel VIEW without CONFIGURE, it **fails with a BLOCKED precondition** rather
than skipping, changing grants, substituting an auth double, or pretending to
have checked the boundary. Other independent cases may still run sequentially;
the resulting report is partial, not full acceptance. The schema/catalog owner
must provide an approved verification route for that remaining boundary. Do not
weaken the assertion into "whatever this profile allows is correct".

## Local execution assignment

Pin the exact fresh PR head from the handoff, preserve all other worktrees,
and read `AGENTS.md`, `docs/RULES.md`, `docs/COLLABORATION.md` and
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
assertions; a BLOCKED operator precondition leaves that contract unverified.

Return the R10 compact `TEST RUN` block directly. The coordinator does not open
the runner's evidence file. The owner may relay the human report, without secrets.
No Ready, merge, dev push, source-branch change, deployment or schema operation
is authorized by this document. Code review approval from schema and source-lane
owners remains distinct from local execution evidence and explicit merge approval.
