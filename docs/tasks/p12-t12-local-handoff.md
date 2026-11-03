# P12-T12 — local completion, verification and audit handoff

Entry: [task card](p12-t12.md) · [PR #327](https://github.com/Starck43/social-media-ai/pull/327) · [implementation details](../FILE_WORKSPACE_PRIVATE_IMPLEMENTATION.md). The PR body is the single source for remote test evidence; this page lists what is **not yet done**.

## 1. Establish the exact worktree and scope

- Fetch `origin/dev` and `origin/feat/p12-t12-file-workspace`; inspect PR #327 head, current status and dirty/staged files. Use a separate worktree if necessary; do not reset, overwrite foreign work, force-push or merge implicitly. Registry and task-card changes can conflict between the diverged branches.
- Record the exact PR HEAD, parent and test environment. The remote code tested at `ae177107673bdb272a00eefea48cb2f3e7511156`; later documentation-only commits do not add code. Retest any later code changes. Read `AGENTS.md`, `tests/AGENTS.md`, `.agents/skills/test-runner/SKILL.md` and the test/DB and acceptance sections of `docs/COLLABORATION.md`.
- Review the PR diff and behavior, not just this summary: `app/integrations/file_workspace/`, the file catalog/content/insight managers and services, `app/web/file_workspace.py`, its template/CSS, migrations 0124–0128, and P12-T12 tests. Use the implementation guide for architecture and trust boundaries.

## 2. PostgreSQL and failure/race coverage — NOT RUN

- Use the local OpenCode/test-runner route and an exclusive **authorized test schema**. Follow DB lock, target identification, complete before/after schema inventory and cleanup rules. No public-schema or production mutation. Apply migrations only through Alembic; verify 0127→0128→0127→0128 and preservation of pre-existing data and schema.
- Run the authored integration file **with root conftest**, not the remote exception:

```sh
python -m pytest tests/integrations/test_p12_t12_private_storage.py -q -o addopts=''
```

- Inspect coverage and author/run focused additional local tests where absent: current owner/tenant/source.view and root isolation; independent consent and regrant purge; connection/secret/policy/member/role revoke-and-restore; object forget vs in-flight save and no resurrection; concurrent last-slot and last-byte quota across object/connection/owner/tenant; retries and interrupted commits; expiry/GC idempotency, abandoned ledgers, backward clocks; corruption, encryption/key rotation and backup/restore; legacy catalog behavior and trigger paths. Verify no synthetic residue. Existing mocks are not evidence of PostgreSQL locking, migrations or concurrency.
- Record commands, exact code SHA, interpreter/dependencies, DB target (without secrets), inventories/cleanup, counts, failures and unrun scenarios. Do not run the full suite without owner request.

## 3. Provider, UI and operational gates — NOT RUN

- With explicit authorization and dedicated scoped test credentials only, validate the real provider read, allowed download host behavior, no credential/URL/content logs, DNS/TLS/redirect limits, authorization/consent changes during reads and sanitized failures. Provider OAuth may be wider than the app-enforced root. No live call or secret was used remotely.
- Establish documented provider-backed immutable exact-byte version semantics and a reviewed authority before enabling durable Yandex result creation. Metadata equality, MD5, mtime and LIST are **not** such proof; do not fabricate a version. Native browser-open is a separate unresolved P8-T34 capability, not this attachment endpoint.
- Perform authenticated browser E2E, accessibility and visual inspection on desktop and ~390px, including empty/long-name/preview/historical/error states, consent and delete. Screenshot generation ran remotely, but image inspection was unavailable; visual QA has **not** passed.
- Complete P12-T13 independent contract review and obtain real user consent where needed. Resolve P12-T15 approved actions, local-only execution and any remaining task acceptance scope. Review policy, security/privacy, resource limits, key lifecycle, backup/restore, retention and GC scheduling before deployment. All feature flags remain off until explicit approval.

## 4. Handoff to independent reviewer / auditor

- Correct defects and missing implementation, rerun affected checks on the resulting SHA, and update PR evidence and the short dev card. Do not treat a failing/unrun check as passing, and do not self-accept. An owner waiver must be explicit and recorded.
- Keep P12-T12 and P8-T34 IN_PROGRESS and PR #327 Draft until all applicable acceptance criteria and required checks pass. Once the executor records evidence and transitions to READY per repository rules, an **independent auditor** reviews the exact head, evidence, dependencies and remaining risks using `.agents/skills/audit/SKILL.md` and `docs/COLLABORATION.md#acceptance`. Auditor records the verdict and only then performs authorized closeout/merge under those rules. P12-T13 READY likewise awaits independent review.
