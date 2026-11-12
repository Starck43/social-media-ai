# P12-T12 — Accepted file-workspace implementation increment

**Verdict: ACCEPTED — 2026-11-03.** Accepted PR #327 source `6433ae6a7615f63f2b204636b8538196986d18a9`; merge commit `48caea61d10c5d586d0d97880dc413b215432665`.

This accepts the delivered implementation increment, not the full P12-T12 roadmap. The increment provides a default-off private catalog, bounded cloud text attachment and selected-line preview, scoped consent and encrypted private-result lifecycle, bounded expiry, schema bootstrap guards, and explicit approval for destructive private-result purge during key rotation. Unsupported exact provider-byte versions, native original-open, local-only execution and approved file writes remain future work. Runtime flags stay off; no deployment, paid provider call, live connection or key rotation is evidenced. Live connection verification is owner-run after merge.

## Integrated PRs

- PR #308 security source `9885a1f94148c5a0db8532a6c31f5b47ff4797b8`, merge `37944aba697b2b19f4a3fe9479b22c5b80e4ad9c`.
- PR #311 agreement source `d0e048384f19fa62941cb118a3fb00689fa94924`, merged through PR #308 at `fa7867adfc3c019d6a3aa75a26a768eb7e0c9051`.
- PR #307 plan source `28f7e45638e22ce00e8b01579e174b9fa3151c95`, automatically merged as ancestor of PR #327 at `310adca7990f8a624bba8d32b26fbf7710e574f6`.
- PR #327 implementation source and merge as above. The original #307 plan was reconciled into `docs/FILE_WORKSPACE_ROADMAP.md`; this did not close the broader parent task.

## Evidence and checks

River executed the focused checks through the configured tester. On functional checkpoint `1a464409d632b4302c569cb5d939ed65a0d2de8f`, the focused suite passed **1,428 tests and 18 subtests**, with seven existing warnings; a basename-colliding private-storage test passed separately (**37 passed**), and the guard inventory passed separately (**15 passed**). Dependency export parity passed for five profiles. The catalog browser harness passed **17 scenarios** with 18 snapshots, zero page errors and zero unexpected requests. Eight representative captures were inspected. Exact pytest and browser commands are in the [validation record](../archive/P12_FILE_WORKSPACE_VALIDATION.md). These are synthetic UI/provider fixtures, not live-provider acceptance.

The final CI source was `6433ae6a7615f63f2b204636b8538196986d18a9`. River's `python -m scripts.ci.checks` exited 0: syntax **1,265**, stdlib **4**, migration graph **17 revisions**, single head `0128`, and secret baseline **173 formatting files / 281 secret candidates**. The final Markdown check on that source passed: **336 tracked files, 1,898 local-link candidates, zero errors**.

GitGuardian's hosted check **failed** on four historical occurrences in `.ci/secret-baseline.json` (commits `a88a94f37294f3cecd4e245f36ff7879364c3d79` and `ba6259f9d495661e3d03878a0cd3f622ab4c2d62`, lines 15 and 23), all mapping to incident `39493215`. The independent reviewer verified the detector fingerprint by confirming `SHA1(secret_value)` equals `e4e59eed8060114c3f3cd491c4b7c46eb2623f3b` and the paired placeholder fingerprint equals `c053ecf9ed41df0311b9df13cc6c3b6078d2d3c2`; the source values are documented offline/local examples in the P12-T14 card and archive, and are not credentials. The baseline stores only one-way hashes and rationales, not source values. The incident was adjudicated as a false positive. No scanner suppression or detector change was used. Record this as a reviewed scanner finding, not a green hosted check.

Migration graph `baseline_0118 -> 0128`, canonical schema validation and reference seed passed on the disposable PostgreSQL cluster. Canonical bootstrap installed 89 guard objects matching migration trigger definitions and function bodies after whitespace normalization. The post-test inventory matched the clean baseline across all 73 table row counts, with no temporary schemas or fixture residue.

The owner-authorized working database migrations both completed through Alembic from `0123` to `0128` at the reviewed graph. `test_schema` was applied at source `c7a076b5c91a6133e6745d22312d9f7b0e450707`; `public` at accepted source `6433ae6a7615f63f2b204636b8538196986d18a9`. Both reports confirm pre-existing table row counts preserved, 11 new tables, zero legacy plaintext refresh-metadata rows, verified rollback-backup checksum and no synthetic records. No key was rotated. Protected reports and backups remain in the ignored local directory `backups/20261103-p12-pr-completion`; a full-data restore was not tested.

## Corrections and limits

The integration retained encrypted-refresh migration `0124` and the `p12t14_*` security chain, assigned the file catalog migration a unique revision after that chain, and preserved one Alembic head. Migration tests now restore their starting revision and refuse destructive downgrade while private state exists. Private expiry uses bounded batches, database time, `SKIP LOCKED` and timeouts; worker and web maintenance isolate failures. Rotation refuses implicit purge of private results and keeps purge plus credential update transactional.

Earlier failing test doubles, stale receipt-field references, an HTTP client incompatible with secure cookies, fixture teardown and a multi-DDL AST scanner were corrected; tests were not weakened or skipped. A preparatory attempt against a disposable database selected `public`; the target guard caught it before tests and that owned disposable database was recreated. It did not touch the working public schema and is not evidence for migration success.

## Related work and disposition

**Documents reviewed for this closeout:** `README.md`, `docs/DESIGN.md`, `AGENTS.md`, `docs/COLLABORATION.md` task/state/evidence/acceptance/database/Git sections, `tests/AGENTS.md`, `docs/P12_FILE_WORKSPACE_VALIDATION.md`, `docs/tasks/p12-t12.md`, `docs/tasks/p8-t34.md`, and the P12 phase continuation. The linked implementation roadmap was checked to preserve the remaining task scope.

P12-T13's contract-review scope is separately accepted in [its audit](p12-t13.md). P12-T12 remains **IN_PROGRESS** for the roadmap items above; P8-T34 remains **IN_PROGRESS** for exact provider behavior and live Yandex Disk pilot work. P12-T15 folder policy and separately gated writes also remain open. PR #327 and its original #307 plan branch were deleted remotely after integration was verified. Owned local worktrees/branches `t12`, `guards`, `ci-fix`, `metadata12` and the #341 verification merge were removed after ancestry/tree verification. The owner checkout `/Users/admin/Projects/social-media-ai` on `chore/env-examples-cleanup` and foreign `/Users/admin/Projects/sma-t14-docs` were retained untouched. Protected rollback backups remain at `/Users/admin/Projects/social-media-ai/backups/20261103-p12-pr-completion`. The coordinator will remove the remaining owned `integration12`/`registry` worktrees, disposable PostgreSQL cluster and temporary River sessions after publishing this durable audit; no foreign session or backup is included in that cleanup. The owner will verify live connections after merge.
