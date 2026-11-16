# Test-suite failures and their causes (2026-10-20)

Scope: the failing tests of the `dev` branch after the VK search work, their
real cause, and what was changed. Every fix is either a product defect or a
test that encoded a contract the product has deliberately left behind.

## 1. The whole suite could not even be collected

`tests/test_pr197_offline_gates.py` started with two stray lines

```python
# UNIQUE_STRING_12345
print("FILE LOADED: test_pr197_offline_gates.py")
```

before its `from __future__ import annotations`. Python rejects a future import
after any statement, so the file raised `SyntaxError` at import time — and
pytest aborted the **entire** run with an INTERNALERROR, which hid every other
failure in the repository. The two lines were debris (nothing in the repo
references either marker) and are removed.

## 2. The PR197 launcher trusted the import order

`scripts/pr197_normal_migration_launcher.py` promises that "the process
environment is the only source" for the target DSN: it suppresses both dotenv
mechanisms and then compares `settings.POSTGRES_URL` with the requested DSN.
It read the *module-level* `settings` object, which is created when
`app.core.config` is first imported. When the launcher runs in a process that
already imported the app (the offline test suite does exactly that), the cached
object still carries the old DSN and the gate stops with
`effective-dsn-drift` — a false alarm about a target that was never wrong.

The launcher now builds a **fresh** settings instance from the environment
after the dotenv mechanisms are suppressed, so the gate means the same thing
in a fresh process and in an embedded one.

## 3. The launcher's catalog gate was weaker than its own header

The launcher documents "catalog deltas against explicit expectations; any drift
fails the run", but the readback only demanded that the *expected* objects
exist: a step that created something nobody reviewed passed silently. It now
also stops on an unexpected delta (`unexpected-catalog-delta`), and the
expectation comment says what it means.

## 4. The PR197 test file had drifted from the launcher

The file was committed with expectations that the launcher never had:

- it asserted `alembic.command.upgrade` was called with a **range**
  (`"0096:0097"`) while its own name and docstring say "a single revision
  step, not a range" — the launcher calls `upgrade(cfg, "0097")`, which is the
  correct Alembic form;
- the fixture for the `0096→0097` step did not contain the objects that step
  creates (`llm_budget_baselines` and its three triggers and two functions),
  so every 0097 run stopped as `expected-object-missing` before it could test
  what it meant to test;
- the manifest test set attributes on a `dict`;
- the probe test instantiated the *test* class instead of the harness class,
  did not import `asyncio`, and expected no exception where the harness
  deliberately raises `Blocked` when a must-fail probe succeeds.

All of these were test-side defects; the harness behaviour they claimed to
check is the one the code implements.

## 5. A pooled connection cannot cross an event loop

Several DB tests are `unittest.IsolatedAsyncioTestCase` classes, and each of
their test methods runs in an event loop **of its own**. The application's
async engine pools connections, and an asyncpg connection belongs to the loop
that opened it — so a reused connection arrived in the next test's loop and
failed with `got Future attached to a different loop`.

`DB_POOL` is now a setting: `pool` (default) reuses connections, `null` opens
one per session. The test suite sets `DB_POOL=null` before the first `app`
import (the engine is built at import time), which is the only correct
setting for a process that runs parts of its work in another loop.

## 6. A tenant-isolation test was not isolating anything

`tests/test_job_task_atomicity_db.py` creates two workspaces and proves that a
Job cannot write the summary of a task that lives in another one. The suite's
autouse fixture patches `is_bypass` to `True` for legacy tests, so under it
*every* row — the "foreign" task, the job, the claim — landed in the bootstrap
workspace. The foreign reference stopped being foreign, the select found the
row, and the expected `RuntimeError` never happened: the test passed for the
wrong reason before and failed for the right reason only once the class was
marked `@pytest.mark.tenancy` (the real guard).

The same class' ACK-loss test injected the lost ACK into the *first* commit of
the dispatch path (the claim renewal) instead of the coupled outcome commit,
so it failed with `JobClaimRenewalError` before the handler ran. The injection
now targets the second commit — the one that writes the job row and the task
summary together.

## 7. Two stale expectation sets

- `tests/test_collection_model_layout.py` asserted `_app_label == "social"`
  after the collection domain was renamed to `collection`, and pinned a
  schema fingerprint of `CollectedItem` from before migration 0089
  (nullable staged attachment snapshots, commit `e67f06f`). Both were updated
  to the current, migration-backed reality.
- `tests/test_vk_direct_photo_snapshots.py` expected a photo with a rejected
  URL to be classified as an image with no media URL. The classifier
  deliberately does not bucket a URL-less attachment as media: that would ask
  for a media analysis of nothing and withhold the parent post from text
  coverage for ever. The tests now assert the current contract — the post
  stays covered as text and is not classified as media.

## 8. The web task form and the current contract

`tests/test_task_activity.py` still wrote the old contract: a `collect` task
created through the web form without a scenario. Since the unified social
analysis task, a web-created analysis task needs a methodology — the selected
scenario or the workspace default — and the form refuses to create it without
one. The tests now create an active scenario, and the one that asserts
`force_refresh` was rewritten: the web surface has no force control any more
(a forced re-read is a CLI diagnostic), so it stores the default repeat
policy.

One product asymmetry was fixed along the way: creating a task with a
*deactivated* scenario was refused, while editing one was allowed and started
it inactive. Creation now allows it too — the operator may pick a scenario
that is temporarily off, and the task starts inactive with the reason.
