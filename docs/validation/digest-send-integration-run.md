# Digest send: checked-in integration scope

## Current state

Prepared against dev `19ce6e1117fd085f08a1fe905335c1337b07b801`.
P3-T01 remains IN_PROGRESS; original PR #176 stays Draft. This bounded follow-up
publishes the previously attachment-only test candidates on a current-dev branch
without merging #176's obsolete procedure/design-board delta or changing app code.
No automatic Ready/merge/closure of #176 or #198 is implied.

The owner relayed 37 mock PASS at 19ce6e1 and an unspecified collection/setup
failure for attempted DB tests. Those are distinct reports. No exact failing
command/first traceback was supplied; do not label a guessed cause as proven.
Published dev and #176 at initial inspection did not contain the two integration
modules. Current runner Skill also mandated --noconftest for every scope: this
conflicts with DB tests' root-conftest requirement. The follow-up resolves the
published delivery and mode-contract gaps, not an unobserved local DB defect.

Read current AGENTS.md, docs/RULES.md, docs/COLLABORATION.md and test-runner Skill.
No full .agents/tmp human-audit reports in the coordinator context.

## Files and modes

- tests/test_digest_send_postgres_acceptance.py: expected 30 parameterized cases
  by AST only; real DB identity/runtime/persisted approvals/CAS/commit faults.
- tests/test_digest_send_asgi_acceptance.py: expected 13 cases by AST only;
  real HTTP login/cookies/middleware/CSRF/tenant binding, publisher mocked.
- tests/test_digest_acceptance_bootstrap_contract.py: independent no-DB tests
  of extracted safety guards and source/routing structure, not app behavior.

Keep both integration files together in tests/. ASGI imports fixture helpers from
its guarded sibling; use --import-mode=prepend. Both opt out of legacy bypass
with tenancy markers. Root conftest must be enabled for their schema redirect,
read-only existing-schema/catalog preflight and vault fixture. No old plugin.
The tests reject a missing root conftest, wrong endpoint/schema, and an app engine
or model that retained a working target before redirection. Fault-injection
wrappers delegate to real CAS and commits. Race checks require both backends to
be blocked by the fixture lock holder, with no publisher call before release.
Only generated workspace/user rows are cleaned up; shared catalogs are untouched.
Publisher and LLM continuation are mocked. No lifespan/runtime owner is started.

## Local assignment: review, collect, execute sequentially

Preserve all local dirty work and previously installed candidates. Compare files
before placing/replacing them. Fetch this follow-up branch, verify its exact HEAD
and inspect the diff against current dev; no repin of old 0c18bab. Never assume
19ce6e1 alone describes an effective dirty test tree. Record test-file hashes.

Before another attempt, reconcile the existing failed attempt using compact
runner output: exact command/root/interpreter, conftest mode, nonsecret target,
collection/setup stage, first exception and exits, whether fixtures wrote rows
and whether cleanup completed. If interrupted state is uncertain, stop under R4;
no blind retry, process killing, presumed-leftover cleanup or schema repair.

The existing localhost:5432/social_manager/test_schema target is already approved
for tests, not schema maintenance. Verify current runner/writer state, including
exact `pgrep -fl pytest`; serialize the shared lane through cleanup/postflight.
Keep accepted access evidence at its original attribution: 29 PostgreSQL/ASGI
cases (21+4+4), exact scope in `git show edbe0fc:ROADMAP.md`. Do not repeat those,
the old nine-failure batch, 37 mocks or generic standalone CAS scripts merely for
new labels. Existing preservation/931-vs-932 reconciliation remains scoped to
current consistent-snapshot evidence; neither declare it solved by these tests
nor impose obsolete blockers unrelated to a present R4 risk.

Local coordinator attaches a bounded task to OpenCode test-runner:
absolute worktree, branch, expected HEAD, owned paths and dirty hashes; explicit
mode REAL DB/ASGI, root conftest enabled. Primary exact model is
QwenPlan/deepseek-v4-flash-0731. Coordinator never executes local pytest directly;
Kilo is secondary only if primary is BLOCKED, not concurrent.

OpenCode collects ONLY the two exact modules:

```sh
DB_TEST_SCHEMA=test_schema .venv/bin/python -m pytest --collect-only \
  -o addopts= -q --no-header --import-mode=prepend \
  tests/test_digest_send_postgres_acceptance.py \
  tests/test_digest_send_asgi_acceptance.py
```

After successful collection and review of actual IDs, OpenCode executes the
modules one at a time (current root conftest enabled; NO --noconftest):

```sh
DB_TEST_SCHEMA=test_schema .venv/bin/python -m pytest \
  -o addopts= -q --tb=short --no-header --import-mode=prepend \
  tests/test_digest_send_postgres_acceptance.py

DB_TEST_SCHEMA=test_schema .venv/bin/python -m pytest \
  -o addopts= -q --tb=short --no-header --import-mode=prepend \
  tests/test_digest_send_asgi_acceptance.py
```

No broad discovery or standalone-script imports. Missing schema/reference rows
are a precise BLOCKED result, never an invitation to setup/reset/create/drop/
stamp/migrate/reseed/repair. No live sends/providers, paid calls or deploy.

Return compact TEST RUN: actual tree/dirty hashes, route/model, commands and
collection/execution exits/counts, first failed node and short error, owned cleanup,
pre/post comparison and remaining uncertainty. Preserve full evidence privately.
Only the coordinating local reviewer may accept READY as DONE/remove ROADMAP row.

## Remote evidence and limitations

Remote no-DB command, separate sandbox venv:
`python tests/test_digest_acceptance_bootstrap_contract.py -v`.
8 tests PASS, exit 0 (0.078 seconds before publication); Python 3.13.14.
This extracts guard AST into synthetic metadata; never imports app, conftest,
engines or integration modules. It is not DB collection or acceptance.
AST syntax, Black/isort checks and TOML parsing are separate static checks.
Full integration collection, DB/ASGI execution and preservation audit remain
NOT RUN remotely. Test candidates must not be promoted to acceptance from these
static/no-DB results. Application policy, role catalogs and transactions unchanged.
