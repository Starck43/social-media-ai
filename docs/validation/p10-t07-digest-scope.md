# P10-T07 — digest scope validation

PR: https://github.com/Starck43/social-media-ai/pull/235

## Revision and ownership

- Branch: `fix/p10-t07-digest-scope-consistency`.
- Dev base: `07853fa7284fc244bfb11e83a383d48b60e53df8`; initial claim commit: `5c7f5dd1ab23c8a411554700e7d2533a0d1c9d4a` (claim originally recorded cf36bd4 before dev advanced).
- Exact tested implementation: `63484139b9552af47662868c1592a430102cb166`, clean detached checkout. Later evidence/registry commit is docs-only. No rebase or integration-branch pushes during this implementation.
- Owned implementation: `app/services/ai/reporting.py`, `app/services/digest/builder.py` (aggregate), `app/models/managers/ai_analytics_manager.py` (build_period_rollups).
- Tests: `tests/notifications/test_p10_t07_digest_scope.py` (new), `tests/notifications/test_digest_data_completeness.py` (scope-interface test double only; assertions preserved).
- Registry/card/evidence: only P10-T07's ROADMAP row, its task card and this note. Parent phase and other task rows untouched.

## Behavior / acceptance map

Immutable `ReportScope` carries tenant, inclusive start/end, source set, scenario and DAY base-row kind. Existing ambient ORM tenant guards remain authoritative; explicit tenant is narrow-only. Current rows are materialized once and reused by all five top-level aggregates, grouping/chains and specialized sections. Standalone brief generation uses the same scope. A nonempty report materializes one previous window, shared by both dynamics sections; empty current rows never trigger per-section fallback reads. Previous windows remain adjacent, equal-length and non-overlapping.

Regressions cover two sources/two scenarios, tenant narrowing, first/last-day inclusion, yesterday/future exclusion for a daily digest, DAY plus WEEK/MONTH saved rows, section-row identity, specialized methods, scope/header alignment, previous-window reuse and chain/sentiment movement. No rollup double-counting: only DAY rows enter report sums.

Rollup writes remain owned by aggregate's week/month step, constrained to selected sources in the existing ambient tenant. Storage still uses `(source, analysis_fingerprint)` context. Scenario is not a new rollup dimension; source rollups retain their historical all-context meaning. DAY-only report reads exclude these stored rollups. No schema/migration or delivery/checkpoint/force-resend change.

Coverage is calculated by its existing independent job query (it measures collection/analysis jobs, not analytics rows); grouping and deterministic caveats are preserved.

## Exact executed checks

Python 3.13. Native executor, no child sessions. All tests below are inspected no-DB subsets; paid APIs/transports are mocked or not called. Commands executed from the clean tested checkout. Synthetic environment values below configure imports only, not an accessed database.

| Exact command | Exit | Passed | Failed/errors | Skipped |
| --- | --- | --- | --- | --- |
| `python3 -m unittest tests.notifications.test_p10_t07_digest_scope -v` | 0 | 14 | 0 | 0 |
| `PYTHONPATH=tests:. python3 -m unittest tests.notifications.test_digest_data_completeness -v` | 0 | 24 | 0 | 0 |
| `python3 -m unittest tests.security.test_digest_send_permissions -v` | 0 | 10 | 0 | 0 |
| `POSTGRES_URL=postgresql+asyncpg://test:test@localhost:5432/test_db SECRET_KEY=p10-test PYTHONPATH=tests:. python3 -m pytest --noconftest -q tests/notifications/test_digest_render.py tests/notifications/test_digest_html_parts.py` | 0 | 62 | 0 | 0 |
| `git diff --check` | 0 | n/a | 0 | n/a |

Final output summaries: `Ran 14 tests ... OK`; `Ran 24 tests ... OK`; `Ran 10 tests ... OK`; `62 passed in 7.88s`. Failed test names: none in the final run. Total: 110 passing tests. `--noconftest` is confined to pure rendering/checkpoint functions without DB fixtures. No full suite run or claim of full-suite PASS.

## Deferred local checks

Owner explicitly permits heavy/DB tests to be left to a local agent. Real PostgreSQL/ORM integration remains unverified; mocked reads are not integration proof. Helper request: https://github.com/Starck43/social-media-ai/pull/235#issuecomment-6401398603

On the implementation SHA or a verified docs-only descendant, validate the authorized isolated test schema and run sequentially with the normal root conftest:

```bash
python3 -m pytest -q tests/notifications/test_digest_brief.py tests/notifications/test_digest_builder.py tests/ai/test_reporting_brief.py tests/ai/test_reporting_contract.py
```

The helper must report SHA, command, exit/counts, failures by name and synthetic-record cleanup in the PR. No public/production schema, migrations, real messages or paid APIs are authorized by this handoff. No send authorization changes; additional DB/ASGI send acceptance may be performed locally if desired.

## Documents actually consulted

- `AGENTS.md`; `.agents/skills/remote-task/SKILL.md` and `references/local-validation.md`: execution, PR/helper routing and no-DB limits.
- `.agents/skills/task/SKILL.md`; P10-T07 ROADMAP row and `docs/tasks/p10-t07.md`: scope, ownership and READY handoff.
- `.agents/skills/test-runner/SKILL.md`; `tests/AGENTS.md`; `docs/COLLABORATION.md#tasks`, `#evidence`, `#tests`, `#git-and-cleanup`: focused test/evidence and cleanup contracts.
- PR #196's recovered `docs/audits/2026-10-15-dev.md`, AUD-07 section: immutable scope, boundaries/rollups, reused reads and exclusive file lanes.

## Cleanup / limits

No database was accessed and no synthetic records were created. No provider/transport writes, secrets, migrations or generated artifacts are committed. No subagents created. Local generated caches and the owned checkout are removed only after verified merge. Remote branch deletion is separate housekeeping and is reported if unsupported. READY is executor handoff, not owner acceptance: the ROADMAP row is retained and parent phase closeout is left to the custodian.
