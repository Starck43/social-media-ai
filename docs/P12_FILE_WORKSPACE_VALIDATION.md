# File-workspace integration validation — 2026-11-03

## Scope and authority

The owner authorized completing the four related PRs, independent review and
`dev` integration, and test/public migrations. Live connections will be checked
by the owner after merge and are waived as a repository-acceptance prerequisite.
They remain UNRUN. Runtime activation and deployment are separate.

The increment delivers the private catalog, bounded cloud text attachment and
selected-line preview, scoped consent and encrypted-result lifecycle, automatic
bounded expiry, schema bootstrap fences and explicit rotation purge approval.
The [reconciled original plan](FILE_WORKSPACE_ROADMAP.md) retains the broader
P12-T12 roadmap and P12-T15 responsibilities; unsupported immutable provider-byte
versions, native original-open, local-only execution and approved writes are not
reported as implemented.

## Focused checks

All pytest/browser/CI executions were delegated through OpenCode `build` to its
configured V2 River `tester`. Python 3.14.0, exclusive disposable PostgreSQL 15.14
at `127.0.0.1:55439`, database `sma_complete_combined`, schema `test_schema`.
Root conftest redirected from the distinct empty `sma_complete_working` target;
`DEBUG=false`, `ENVIRONMENT=test`, synthetic keys and provider doubles only.

Exact functional checkpoint `1a464409d632b4302c569cb5d939ed65a0d2de8f`:

```sh
python -m pytest -q -o addopts='' --tb=short --no-header -p no:cacheprovider \
  tests/files/test_p12_t12_catalog.py \
  tests/files/test_p12_t12_catalog_refresh.py \
  tests/files/test_p12_t12_catalog_scan.py \
  tests/files/test_p12_t12_catalog_web.py \
  tests/files/test_p12_t12_migration.py \
  tests/files/test_p12_t12_original_open.py \
  tests/files/test_p12_t12_recursive_scan.py \
  tests/integrations/test_p12_t12_private_storage.py \
  tests/unit_services/test_p12_t12_catalog_traversal.py \
  tests/unit_services/test_p12_t12_file_actions.py \
  tests/unit_services/test_p12_t12_insight_candidates.py \
  tests/unit_services/test_p12_t12_line_evidence.py \
  tests/unit_services/test_p12_t12_original_target.py \
  tests/unit_services/test_p12_t12_plain_text.py \
  tests/unit_services/test_p12_t12_plain_text_read.py \
  tests/unit_services/test_p12_t12_private_admission.py \
  tests/unit_services/test_p12_t12_private_maintenance.py \
  tests/unit_services/test_p12_t12_private_payload.py \
  tests/unit_services/test_p12_t12_private_projection.py \
  tests/unit_services/test_p12_t12_retention_quota.py \
  tests/unit_services/test_p12_t12_retention_time.py \
  tests/unit_web/test_p12_t12_file_catalog_layout.py \
  tests/collection/test_telegram_durable_admission.py \
  tests/security/test_p12_t14_login_maintenance_unit.py \
  tests/tooling/test_p11_t24_schema_guards.py
```

Exit 0: **1428 passed, 18 subtests passed**, seven existing warnings (109.50s),
no failures, skips or errors. Separately, normal-conftest
`tests/unit_services/test_p12_t12_private_storage.py`: **37 passed**, exit 0,
one existing warning. Its basename collides with the integration test under
pytest prepend mode, so it intentionally runs in a separate process.
The guard inventory alone also passed **15 tests** on the same code checkpoint.

Fresh actual Alembic `baseline_0118 -> 0128`, canonical model/schema validation
and reference seed passed without metadata/stamp shortcuts. The graph retains
encrypted-refresh `0124`, then `p12t14_*`, unique `p12t12_catalog` and 0125-0128.
Canonical schema bootstrap installs the same 89 guard objects: function bodies
match migration bodies after whitespace normalization and trigger definitions
match exactly. Post-test complete schema inventory and all 73 table row counts
matched the clean seeded baseline; no temporary schemas or test residue remained.

## Browser and static checks

With Playwright, an installed Chromium and the pinned public JS versions named
in the checked-in harness (all requests intercepted):

```sh
node tests/shared/file_catalog_browser.cjs "$ASSETS_DIR" "$OUTPUT_DIR"
```

Exit 0: **17 catalog layout/browser checks passed**, with 18 snapshots and no page errors, unexpected
requests, overlap or horizontal overflow. Eight representative list/detail/
long-name/notification/light/dark/mobile/desktop captures were visually inspected.
These are synthetic templates with local assets, not live-provider acceptance.

Dependency export parity: `python scripts/packaging/dependencies.py check`
passed with **5 profiles**. The general CI first failed on formatting and secret
candidates after its earlier stdlib-boundary blocker was fixed. The 21 formatting
paths were corrected with normalized AST equivalence review; 50 exact synthetic/
placeholder/identifier/runtime-reference findings were individually reviewed and
added as hash-only exceptions, without disabling detectors or allowing patterns.
Two further keyword matches in the concurrently merged planning prose were
reviewed separately as non-secret data-model descriptions and given exact hash
exceptions. The final general-CI result is recorded in PR #327 and its audit.

## Corrected failures and cleanup

The original migration tests left the schema at 0127, dropping private tables;
they now share one Alembic connection, restore their starting head in `finally`,
and refuse destructive downgrade while private state exists. Private GC uses DB
time, SKIP LOCKED, bounded batches and timeouts; worker/web loops isolate failures.
Rotation refuses implicit private-result loss and tests verify rollback after a
later credential failure as well as approved purge and required new consent.

Earlier failing runs exposed incomplete test doubles, stale receipt-field names,
secure-cookie clients using HTTP, an unowned provider teardown and an AST scanner
that read only the first function in multi-DDL SQL. These were corrected without
skipping cases or weakening security assertions. Failed-run synthetic residue was
inventoried before recreating only the exclusively owned disposable database;
the final clean run verified zero row-count deltas.

One preparatory migration attempt selected `public` in a disposable test database;
the target guard caught it before tests, and that owned database was recreated.
It was not the working/public database and is not counted as validation evidence.
Working-schema apply uses reviewed backups and the validated final migration
graph separately; it creates no synthetic public records and rotates no live key.
