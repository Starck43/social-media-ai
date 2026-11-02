# Audit: P12-T14 slice — VK refresh concurrency fence (`44543165`)

**Date:** 2026-11-02
**Candidate:** commit `44543165c11b0058068b7709c2e65b6d77d69794` (branch `feat/p12-t14-refresh-security`, PR #314)
**Base for the diff:** `06d3e100` (`dev` at that merge point)
**Auditor:** opencode (independent)
**Task card:** `docs/tasks/p12-t14.md` (P12-T14 stays IN_PROGRESS: this is the first implementation slice)

## What the slice changes

- `app/models/managers/user_credential_manager.py`: new `locked_vk_token(user_id)` context manager — `SET LOCAL lock_timeout = '5s'`, then `SELECT ... FOR UPDATE` on the active `users` row, then `FOR UPDATE` on the newest active `vk`/`user_token` credential ordered by `coalesce(updated_at, created_at) DESC, id DESC`. Yields `(session, row)`; absent/inactive owner raises `PermissionError`, malformed id raises `ValueError`.
- `app/services/social/vk_oauth.py`: refresh rewritten around a durable, committed claim (`meta.refresh_attempt = {id, state, started_at}`) written **before** any HTTP call; success/failure updates re-lock and are fenced by `_owns_refresh_attempt` (row id + attempt id + `state == pending`). Pending claims are never auto-reclaimed; `refresh_failed_at` cooldown preserved; the spent refresh token is never reused as fallback; explicit OAuth login clears the stale claim state. `renew_if_expiring` now delegates to the same path.
- `tests/security/test_p12_t14_refresh_claim.py` (new, 446 lines) and `tests/security/test_p6_t02_vk_oauth_log_privacy.py` (updated).

## Checks performed

Isolated worktree, Python 3.14.0, `requirements.txt` installed, no provider calls, no application DB:

```sh
PYTHONPATH=.:tests python -m pytest tests/security/test_p12_t14_refresh_claim.py \
  tests/security/test_p6_t02_vk_oauth_log_privacy.py \
  --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
```

PASS, exit 0: **43 passed, 5 subtests passed** — matches the PR's reported count.

Delegated test module docstring explicitly states its shared-lock double models interleavings, not PostgreSQL lock semantics. That gap was closed here with a one-shot real-PostgreSQL probe (throwaway schema `p12t14_audit_probe`, real manager, two independent sessions; schema and helper scripts removed afterwards, verified absent):

| Probe | Result |
| --- | --- |
| Second `locked_vk_token` context for the same user waits until the first commits | PASS (`first-enter → first-exit → second-enter`) |
| Concurrent writer is blocked by the row lock (wait observed, not just serialized in-process) | PASS |
| Absent owner raises `PermissionError`; invalid id raises `ValueError` | PASS |
| Exception inside the context rolls back the row (meta unchanged) | PASS |
| `SET LOCAL lock_timeout` does not leak to the session/engine | PASS (`SHOW lock_timeout` = 0 outside) |

## Findings

1. **DB-backed PostgreSQL locking is now evidenced** for the manager primitive itself, on real PostgreSQL, which the PR listed as unrun. The full refresh orchestration (claim → HTTP → fenced finalize) was still only exercised against the in-memory double; a DB-backed run of `refresh_user_token` with a mocked `_post_form` is the remaining gap.
2. **Process-crash residue:** a claim left `pending` (worker killed between commit and finalize) permanently blocks further automatic renewal for that user until an explicit re-login, as documented in the docstring. Intended fail-closed behaviour, but it should be stated in the card's operator notes.
3. **UI status does not surface a pending claim.** `app/services/social/connections.py` (renewal status card) checks `refresh_failed_at` and expiry only; a stuck `refresh_attempt` keeps showing "renewable"/"connected" until the access token expires, then "renewable" forever. Not a regression of this slice, but a follow-up belongs with the status-reader cutover.
4. **Legacy secret representation unchanged** (plaintext refresh token in `meta`) — explicitly deferred to the next slice by the PR; not a finding against this diff, only a reminder that this slice is not the security fix on its own.

## Verdict

**Slice accepted as correct for its stated scope** (durable single-use claim, fenced success/failure, no token-reuse fallback, cross-session lock). Findings 1–3 are follow-ups, not blockers for the slice; P12-T14 stays IN_PROGRESS. No task card, ROADMAP row or phase entry is closed by this audit.

Merging PR #314 is **not** authorized by this audit: it is a draft, its head has moved past `44543165` (`adbf5586`), and the owner's next step is the encryption slice.

## Documents read

`AGENTS.md`, `tests/AGENTS.md`, `.agents/skills/audit/SKILL.md`, `docs/COLLABORATION.md#tasks`, PR #314 body, `docs/tasks/p12-t14.md`, the changed source and test files.
