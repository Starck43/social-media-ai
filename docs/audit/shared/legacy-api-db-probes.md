# Legacy API DB probes: isolated verification and handoff

## Source and ownership

- Task: P10-T05 only; branch `fix/legacy-api-db-probes`, PR targets `dev`.
- Synchronized dev: `959a461410aa220cd1059c4e3ee59b55d5f79eaa`.
- Reservation: `cf0ca8ae198b7b728b8f8ef87821bdb5ed8831de`.
- Runtime implementation: `f20a18bffca0d64054b4c428897b48dc0ff5a9dd`.
- Final implementation/test source: `00f560e6236a43cd7f9ebeee9a0ab526a00d7618`.
  Subsequent handoff commits change documentation only.
- Rechecked active heads: #216 `b9faf8dc68aecf350f2b656c9356a90cd1bf26fd`,
  #217 `c4161b5e67919d505e707f70ef18e6081cf11c81`,
  #220 `30c88985feeda5ce70cec4874a434411a1386d40`.
  No probe runtime/test overlap; shared docs were edited only in this task's
  registry/phase row and API health section. Historical audit evidence and
  foreign owners/statuses were not imported, rewritten or accepted.
- P10-T10 is untouched and unclaimed by this lane. No dependency/packaging
  changes, migrations, root probe changes or unrelated runtime refactoring.

## Behavior and limits

Both legacy handlers use one manager-layer `Permission.objects.count()` probe
with `asyncio.wait_for(..., timeout=2.0)`. Successful bodies remain unchanged;
failures and timeouts retain legacy HTTP 500 and fixed detail
`Database connection error`. Raw exceptions are neither returned nor logged by
this helper. `CancelledError` is not caught. The hidden `/test-db` and
`/test-auth` authentication and schema visibility remain unchanged; legacy
`/api/v1/health` remains public under existing middleware policy. Root
`/health`, `/readyz` and `/livez` are outside this change.

This is a cooperative async timeout, not forced preemption of a blocking or
cancellation-suppressing driver. No worker/scheduler health guarantee is added.

## Sandbox evidence (self-executed, not delegated)

Linux, Python 3.13.14, pip 24.2; separate `/data/legacy-probe-venv`.
Selected dependencies are pinned to the repository's `requirements.txt`;
no owner `.venv`, `.env`, real configuration, DB URL or credentials were used.
The initial FastAPI install selected Starlette 1.9.0 transitively; it was pinned
to the manifest's 1.8.0 before any test execution. No dependency installation
blocker occurred. Installed versions are listed below for reproducibility.

Import/fixture graph inspected before execution: the actual `entry.py` is loaded
with `tests/source_import_isolation.py`; all three application imports (models,
auth and endpoint modules) are explicit module-local doubles. Real FastAPI
constructs routes, but no application factory, settings, ORM or provider module
is imported. Other imports are stdlib/pytest/FastAPI and their dependencies.
Root conftest, cache and plugin autoload are disabled; only pytest-asyncio is
explicitly enabled. The existing `tests/test_api_health.py` imports `app.main`
and was intentionally not run in this isolated lane.

Preparation commands (the selected manifest pins, consolidated):

```sh
python3 -m venv /data/legacy-probe-venv
/data/legacy-probe-venv/bin/python -m pip install fastapi==0.143.2 starlette==1.8.0 pytest==9.1.1 pytest-asyncio==1.4.0 black==26.10.1 isort==9.0.2
/data/legacy-probe-venv/bin/python -m pip check
/data/legacy-probe-venv/bin/python -m pip freeze
```

Exact final checks, from `/data/legacy-probe-checks`, a clean detached worktree
at `00f560e6236a43cd7f9ebeee9a0ab526a00d7618`:

```sh
git rev-parse HEAD
git status --short
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /data/legacy-probe-venv/bin/python -m pytest tests/test_legacy_api_db_probes.py -p pytest_asyncio.plugin --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header --collect-only
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 /data/legacy-probe-venv/bin/python -m pytest tests/test_legacy_api_db_probes.py -p pytest_asyncio.plugin --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
/data/legacy-probe-venv/bin/python -m black --check tests/test_legacy_api_db_probes.py
/data/legacy-probe-venv/bin/python -m isort --check-only tests/test_legacy_api_db_probes.py
git diff --check 959a461410aa220cd1059c4e3ee59b55d5f79eaa HEAD
git diff --exit-code 959a461410aa220cd1059c4e3ee59b55d5f79eaa HEAD -- app/core/health.py
```

Results: clean worktree; collection 14, exit 0; **14 passed**, 0 failed,
0 skipped/xfail, exit 0 (0.36s). Formatter checks, diff checks and unchanged-root
comparison: exit 0. `pip check`: exit 0, no broken requirements.
Cases include zero/nonzero counts; async and immediate exceptions with a fake
DSN/secret sentinel; an actual blocked coroutine cancelled by the probe timeout;
caller cancellation and probe-originated cancellation; actual FastAPI dependency
and schema declarations (including child routers); unchanged test-auth success.
Direct calls do not execute authentication or middleware.

An earlier run at `f20a18bffca0d64054b4c428897b48dc0ff5a9dd` had 13 passed /
1 failed, exit 1: route introspection assumed eager `.path` objects. The test
was corrected to use FastAPI's effective route contexts (no assertion removed),
then strengthened with a baseline-safe timeout override. Runtime code did not
change after that earlier run.

Baseline: the final test file was copied unchanged into an isolated worktree at
`959a461410aa220cd1059c4e3ee59b55d5f79eaa`. The same pytest command above,
from `/data/legacy-probe-baseline`, gives **8 failed / 6 passed**, exit 1.
Failures detect missing timeout configuration, raw error disclosure, and blocked
probes reaching the outer one-second test guard rather than a legacy failure.
The final implementation passes without weakening those checks.

### Installed versions

```text
annotated-doc==0.0.5
annotated-types==0.8.0
anyio==4.15.1
black==26.10.1
click==8.5.0
fastapi==0.143.2
idna==3.20
iniconfig==2.3.1
isort==9.0.2
mypy_extensions==1.1.0
opentelemetry-api==1.45.1
packaging==26.3
pathspec==1.1.1
platformdirs==4.13.5
pluggy==1.7.0
pydantic==2.14.1
pydantic_core==2.50.1
Pygments==2.21.0
pytest==9.1.1
pytest-asyncio==1.4.0
pytokens==0.4.1
starlette==1.8.0
typing-inspection==0.4.4
typing_extensions==4.16.0
```

## Required acceptance before Ready/merge

READY in the task board means the remote implementation is ready for review,
not accepted DONE or merge authorization. Keep the PR Draft while required real
environment evidence is missing. The coordinator must review the exact PR head
and evidence and decide acceptance. Only the coordinator may mark DONE.

Use the owner's sequential R8/R10 local route after inspecting current HEAD,
fixtures and existing target state; do not reset/migrate/reseed to obtain PASS.
At the final PR SHA, verify the actual registered `/api/v1/health`, `/test-db`
and `/test-auth` paths and prefixes through middleware/auth. Check unauthenticated
`/test-db` refusal without its probe; authorized success with the real manager;
unchanged public health success; generic failure/timeout serialization without
synthetic sentinels; cancellation/connection cleanup under the actual driver.
Confirm root probe routing/statuses remain unchanged. The existing root routing
scope is `tests/test_api_health.py`; any added DB/ASGI coverage must be inspected
and assigned by the coordinator before execution under local rules.

No real DB/ASGI, permission/tenant isolation, driver cleanup, owner-local full
suite or staging acceptance was performed remotely. No app/runtime/Compose
startup, schema action, provider request, external send, deployment, force push,
direct dev push or merge occurred. Existing dirty worktrees were preserved.
