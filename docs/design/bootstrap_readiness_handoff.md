# Test-schema safety and API readiness: owner handoff

Status: MERGED IN PR #19 AS `f0a4510`; DEPLOYMENT/ACCEPTANCE NOT CERTIFIED.
Original branch `ai/bootstrap-and-readiness`.
Baseline dev `7175e47ee42548dd19f493cc6c65607f0208025c` (2026-10-09).
Owner UI icon-centering and runtime guard are preserved. Four requested small
work items are merged; no broader production gate is certified. No fresh
owner test results for this package were supplied after that merge.

## Test target and diagnostics

- Common POSTGRES_URL with separate DB_TEST_SCHEMA remains the chosen strategy.
  TEST_POSTGRES_URL is an existing optional override, not a required second DB.
- An unset working DB_SCHEMA now resolves to the application default public for
  the guard. Explicit DB_TEST_SCHEMA=public on the common DB is rejected BEFORE
  environment redirect, app import or bootstrap writes. Blank working schema
  is treated conservatively as public; configured equal schemas are also refused.
- Default test_schema is checked too: a working schema named test_schema cannot
  accidentally use the default test target. Different custom schemas still work.
- --check loads dotenv before reporting the working target and removes userinfo,
  query and fragment from URL displays. Invalid URLs produce a static label.
  It does not redirect, import app, connect, provision, stamp, seed or reset.
- Missing URL guidance now names common POSTGRES_URL + separate DB_TEST_SCHEMA;
  it no longer asks users to create an extra database.

The existing bootstrap/seed/missing-column/reset operations are otherwise kept.
No database/schema was created, reset, truncated or migrated here. No reset/drop
command is needed for this handoff. Schema isolation is not a security sandbox
against privileged roles, cross-schema SQL or CASCADE dependencies. Database-name
comparison remains conservative (may refuse separate hosts with matching DB names);
no live endpoint/alias identity verification or complete destructive-reset hardening
is claimed. Use canonical uppercase configuration variables; working default is
kept aligned with Settings without importing application engines before redirect.

## API health contract

Registered inside create_application, so both the singleton app and factory
instances have these routes:

| Route | Purpose | DB read | Status |
|---|---|---|---|
| /livez | HTTP process liveness | None | 200 when responding |
| /readyz | API readiness for global-model DB access | Awaited Permission count | 200 connected; 503 failed/timeout |
| /health | Existing path, alias of readiness | Same probe | Same 200/503 semantics |

The response preserves status/database/timestamp for /health (UTC timestamp now),
adds Cache-Control: no-store and never includes raw exceptions, DSNs, schemas,
customer content or query counts. Readiness has a 2-second asyncio timeout;
request/shutdown cancellation propagates rather than becoming a false ready result.
Timeout cancellation assumes a cooperative async driver, not an absolute bound
on an uncooperative driver, middleware or startup.

**Compatibility change:** /health previously returned 200/status=ok even when
DB access failed. It now returns 503/status=error. Use /livez for process-only
restart checks and /readyz for traffic admission. Adjust external probes after
review; no Compose/proxy/deployment setting is silently changed here.

This checks API-process responsiveness and one real ORM read only. It does NOT
prove scheduler/worker/listener freshness, migrations, backlog, provider health,
application startup recovery, restore correctness or overall production readiness.
Startup still uses the existing init_db lifespan behavior.

## Verification and evidence

**24 actual-source/mocked-infrastructure tests PASSED locally** with:

```bash
python tests/test_bootstrap_readiness_unit.py
```

They cover effective schema defaults, refusal before redirect, common/optional
URLs, redaction/invalid URL/IPv6, dotenv diagnostic ordering, check-only no DB
work, successful/failed/timed-out/cancelled probes, no-DB liveness, no-cache UTC
responses and singleton/factory router registration. Infrastructure imports
are mocked; these are not actual FastAPI/SQLAlchemy/PostgreSQL acceptance tests.
Compilation, AST and whitespace checks passed. No black/isort run is claimed.

**8 real ASGI cases PREPARED, NOT EXECUTED** in tests/test_api_health.py:
singleton/factory failure status, both ready aliases, success, liveness and timeout.
Requests mock the ORM probe; normal pytest conftest still initializes the isolated
PostgreSQL test target. Pytest/FastAPI/httpx/SQLAlchemy/PostgreSQL are unavailable
in this authoring sandbox. Existing/full-suite acceptance for this new package
remains pending; no live HTTP, LLM, provider or DB calls ran.

The owner reported all tests pass on 2026-10-09 for prior work. That report is
recorded as OWNER-REPORTED with no invented count, command set or tested SHA.
It is not attributed to this later package or the subsequent UI change. PR #18
is merged as 4f01edb; handoffs/tracker/readiness docs no longer call it unmerged.

## Owner sequence

Use the existing common POSTGRES_URL and a distinct DB_TEST_SCHEMA. Do not run
parallel pytest processes sharing that schema. No second database is required.

```bash
python tests/test_bootstrap_readiness_unit.py
python -m scripts.setup_test_db --check
python -m pytest -q tests/test_bootstrap_readiness_unit.py tests/test_api_health.py tests/test_setup_test_db.py
python -m pytest -q
```

In staging, separately verify real healthy and unavailable DB responses and
probe timeout behavior; use process-only /livez for liveness. Do not deliberately
stop the owner's working DB to run a test. Record commands, tested head and logs,
redacting credentials. The new package is not accepted by the earlier test report.

## Parallel work and unchanged limits

Small package scope: test bootstrap, new API health router, main registration,
focused tests and documentation. No permission/identity rules, owner prompt guard,
UI templates, queue claims, memory write protocol, billing ledger, models or
migrations changed. No checkpoint sender or external action activated.

Important complex work remains OPEN and separately reviewable:

1. Fail-closed interactive identity and tenant-owner vs platform rights.
2. General queue claim/lease fencing, atomic task/job outcomes and scheduler ticks.
3. Per-attempt spend ledger and concurrent budget admission (design/schema approval first).

All commits include English Owner handoff notes. Merge/live activation need
separate approval; passing unit tests or a documentation checkbox is not release
sign-off.
