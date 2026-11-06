# P18-T03 — assignment brief for Codex

Owner decision: assigned to Codex on 2026-11-06. Migration number `0143` is
reserved for this task and may only be used if a server-side table becomes
genuinely unavoidable — in that case stop and ask the auditor first.

## Read exactly this

1. `docs/tasks/p18-t03.md` — the card.
2. `docs/audit/p18-t01.md` — the accepted connector protocol this task talks to.
3. `docs/phases/p18-local-file-connector.md` — only the T03 section.
4. `tests/AGENTS.md` — before any test work.

Do not load `docs/phases/` parents, `PRODUCT_PLAN` or neighbouring cards.

## Claim

Claim `P18-T03` in `ROADMAP.md`: status `IN_PROGRESS`, owner `codex-p18-t03`,
link your branch in `docs/tasks/p18-t03.md`. Publish the claim on its own to
`origin/dev` before touching code. Do **not** edit `ROADMAP.md` in the task
branch — the claim commit is the only place that file changes.

## Scope, and the hard boundary

- New top-level package `local_connector/` with its own `pyproject`. It is a
  separate distribution: **it must not import `app`**.
- Folder selection with default and user exclusions; path canonicalisation that
  refuses symlinks and junctions leaving a selected root; case-insensitive
  handling on Windows and macOS.
- Index in SQLite with FTS5: files (path hash, size, mtime, content hash, type)
  and chunks with page/sheet anchors.
- `watchdog` watcher plus periodic rescan; incremental by mtime/size/hash;
  deletion removes index entries.
- Outbound session client implementing the P18-T01 protocol; local status page.

**No database work.** P18-T03 adds no model to `app/models` and no table to the
server schema. `register_model_types()` registers every `app.models` table on
every upgrade, so a stray server table breaks the next migration for everyone.
Test for it before you report: `git diff --name-only origin/dev` must list
neither `migrations/` nor `app/models/`. If the design needs a server table,
stop and ask the auditor for a revision number.

## Local server

The local UI binds `127.0.0.1` with a random port and a CSRF token; it is not an
`app/web` route. If you think a nav entry or a shared template must change,
coordinate it as a separate small commit and check open PRs for those files first
(P20-T06 owns navigation).

## Tests

`pytest local_connector/tests -q` with coverage ≥ 80%. Symlink/junction traversal
tests must run on all three OS in their matrix, or you document manual evidence
per OS in the PR. Mock the outbound session; never contact a real server. This
suite does not need PostgreSQL, so a remote agent may run it alone.

## Delivery

Draft PR against `dev`, body carrying: what changed, documents actually read,
exact commands and their results, and the `git diff --name-only` proof above.
Keep it Draft until the auditor reviews. Record branch/PR and checks in
`docs/tasks/p18-t03.md`.
