# P20-T03 — resume note for the reviewer agent

Written 2026-11-10 by the auditor after verifying the uncommitted work left in
`/Users/admin/Projects/social-media-ai-p20-t03-audit`. Owner decision: the same
agent (`codex-review`) finishes the corrections itself. This note is evidence of
what was measured, not a claim about what is fixed.

## State at the time of this note

- Branch `audit/p20-t03` @ `e202f923` (PR [#450](https://github.com/Starck43/social-media-ai/pull/450)).
- **Nothing was committed.** 8 modified/untracked files sit in the worktree
  (`app/services/commands/approval_inbox.py`, `app/services/workspace/today.py`,
  `app/web/{__init__,chat_workspace,commands,deps}.py`,
  `tests/actions/test_p20_t03_approval_inbox.py`, untracked
  `tests/actions/test_p20_t03_approval_inbox_db.py`).
- The previous run ended with `usage_limit_exceeded` (`last_agent_message: null`).
  Context was the cause of the stall: 47.3M input tokens against a 475k window.
  Commit each green step and keep the tail of the conversation short; do not
  re-read the whole diff on every step.

## Measured results

Head `e202f923` **without** the uncommitted edits, isolated schema, `dev` merged in:
`tests/actions/test_p20_t03_approval_inbox.py` + `tests/calendar` — **61 passed**.
So the committed PR head itself is not broken; the regressions below come from the
uncommitted correction work.

With the uncommitted edits applied:

| Check | Result |
|---|---|
| `tests/actions/test_p20_t03_approval_inbox.py` | **5 failed, 6 passed** |
| `tests/actions/test_p20_t03_approval_inbox_db.py` | 1 passed, **1 error** at teardown |
| `tests/unit_web -k "p20_t03 or today or commands or chat"` | **1 failed** (`test_p20_t07_today_home`), run stopped by `-x` |

Failures and their literal causes:

1. `test_stale_payload_expired_ttl_revoked_approval_and_prohibited_options` —
   `CommandConflict: Command payload changed since preview; reload before review`
   from `approval_inbox.py:1179` (`_authorize_mutation`, called by `execute_item`).
   The test passes `created2["payload_hash"]` and still mismatches: the preview and
   the execution do not hash the same payload shape.
2. `test_web_commands_csrf_accessibility_and_unified_surfaces` —
   `'data-pending-approvals-badge' not in html_list`: the badge attribute is not
   rendered by the list template/context.
3. `test_unsupported_capability_omitted_and_blocked_through_get_and_mutations` —
   `NameError: name 'inbox_module' is not defined` (test line 818).
4. `test_authorization_without_caller_session_fails_closed` — same
   `NameError` (test line 906).
5. `test_badge_counts_every_authorized_page_and_never_leaks_to_anonymous` — same
   `NameError` (test line 1032).
   Items 3–5 share one cause: the rewritten test module never binds `inbox_module`
   for `monkeypatch.setattr`.
6. `test_p20_t03_approval_inbox_db.py::test_session_none_authorization_and_two_session_approve_lock`
   — the test body passes, teardown raises
   `IntegrityError: update or delete on table "tenants" violates foreign key constraint
   "action_audit_events_tenant_id_fkey"`. The fixture must clear the
   `action_audit_events` rows its scenario writes before `personal_world` deletes tenants.
7. `test_p20_t07_today_home.py::test_stale_and_unavailable_markers_and_authoritative_links`
   — `Extra items in the right set: '/app/commands'`. The edit to
   `app/services/workspace/today.py` replaced the `/app/commands` decision link with
   `decision["href"]`; the accepted P20-T07 contract expects `/app/commands` in the
   authoritative-link set. Keep the accepted link contract or the acceptance of
   P20-T07 no longer holds.

## Database state that was repaired

The earlier run created schema `test_p20_t03_audit` without a transaction: tables
existed in `pg_class` but not in `information_schema`, no `alembic_version`, and
`.agents/tmp/p20-t03/db-before.json` recorded an empty baseline, so its own data
comparison could not detect anything. A connection (pid 75263) was left
`idle in transaction` for ~9 hours holding advisory lock `1370093`.

The auditor terminated that backend, dropped the broken schema and the assistant's
scratch worktree, and confirmed no other connections remained. **Re-seed with
`scripts/setup_test_db.py DB_SCHEMA=public DB_TEST_SCHEMA=<fresh name>` in one go
before re-capturing any baseline.** Do not reuse a schema whose tables were
created outside a transaction.

Downgrade note: the branch predates `dev`'s `calendar_events`, `calendar_choices`,
`document_approvals`, `outbox_reply_evidence` and `source_objects` models, so
`setup_test_db.py` on the unmerged branch refuses a schema it already finds those
tables in. Merge `origin/dev` (or verify on a schema seeded from the merged tree)
rather than dropping those tables.

## What remains before this can be audited

- Commit the corrections in small green steps and push **each** step; the PR head
  must match what was tested.
- All five findings above plus F1–F7 from the review comment must be green in one run.
- Keep `tests/calendar` passing — their DB fixture asserts the literal schema name
  `test_schema`, so run those on `test_schema` or accept they refuse other schemas.
