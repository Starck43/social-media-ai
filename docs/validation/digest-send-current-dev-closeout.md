# Digest send: current-dev acceptance closeout

## Authority and exact evidence

Selected task: P3-T01; PR #176 remains Draft. Read current AGENTS.md,
docs/RULES.md, docs/COLLABORATION.md, .agents/skills/remote/SKILL.md,
ROADMAP.md, docs/phases/p3-identity-access.md and docs/ACCESS_MIGRATION.md.
ROADMAP.md + docs/phases/ are the registry, not docs/design/README.md.
This assignment supersedes the executable instructions in
access-acceptance-reconciliation.md, pr176-local-evidence-intake.md and the old
0c18bab verification plan. Those files retain historical evidence only.
Do not restore old .agent procedures, role catalogs, fixtures or plugins.

Fresh remote heads checked 2026-10-15:

- dev: a3a32625152e824a56ecf9ab9c82e20c4a2a4c47.
- PR #176 implementation/test snapshot: 0968157007a1888595d62d91c8ef0ef614f62b5d.
- PR #198: 36b96dd70e59b32e2a2b1643b5da349b965e2dc8.

Recheck all heads before local work. Current dev wins over superseded assistant
changes. Do not relabel a tested dirty tree as a subsequently committed SHA.

The current dev-to-PR176 merge-base diff contains seven test/documentation
files, not an outstanding reports.py runtime fix. The original permission fix
is already integrated. The PR still has a stale docs/design/README.md delta;
do not import it into the new registry. GitHub reports #176 mergeability dirty;
this assignment does not authorize an automatic conflict resolution or merge.
Keep authoritative dev behavior and prepare a minimal integration proposal.

Accepted local evidence is exactly the historical ledger at
`git show edbe0fc:ROADMAP.md`: 29 distinct PostgreSQL/ASGI cases in sequential
batches 21+4+4, attributed to caec98e plus the reviewed access working diff.
They cover API/workspace boundaries, explicit operator grants, ordinary-user
admin refusal, queue/digest-history isolation, last-owner race, real memory_set
dispatch with AI/membership revocation, and personal memory isolation. This is
owner/local OpenCode evidence, not remotely replayed evidence. It explicitly
excludes a real-DB pending-confirmation E2E run. Do not rerun the old nine
failures, the 21-case batch or the 29 cases merely for a new acceptance label.

### New sandbox evidence, not PR-head or DB acceptance

The shell worked in a fresh /data sandbox; no previous checkout was found
under /data, /tmp or /home in the bounded initial inventory. This does not
establish cleanliness or disappearance of owner-local/previous sandbox copies.
A fresh clone was initially clean. Separate detached worktrees were used.

Tested digest tree: dev a3a32625152e824a56ecf9ab9c82e20c4a2a4c47 PLUS only
these untracked test additions copied unchanged from PR176 0968157:

- tests/digest_permission_support.py SHA256
  8a592c8a5b3e2602ea6afaa406c8b367500c47a2b3ca987d5be36559b6ca701d
- tests/test_digest_send_current_boundaries.py SHA256
  4c046cdd523bf902c4ab03fb088c3d73ab48961e1870caaab5689ca4f9b1c3aa

Command from /data/pr176-current:
`/data/digest-env/bin/python -m unittest discover -s tests -p 'test_digest_send*.py' -v`
Result: 47 tests, OK, exit 0, 0.597 seconds; no skipped cases reported.
Python 3.13.14; separate venv: itsdangerous 2.2.0 (manifest exact pin),
Jinja2 3.1.6 and MarkupSafe 3.0.4 (web-template import dependencies; not a full
repository dependency installation). Selected import/fixture graph was reviewed:
no root conftest, application bootstrap, real ORM, database or provider. Storage,
HTTP plumbing, publisher and LLM are doubles; policy and source predicates are
real. These results are not the old 47 PASS at 1a95916, nor a run on unchanged
PR176 HEAD. No DB/ASGI or preservation inference follows.

In a separate disposable source copy, changing only the canonical send-tool
CREATE declaration to UPDATE caused three selected runtime regressions to fail
(3 failures, 0 errors, exit 1). Positive worktrees were untouched. Selected
methods in DigestRuntimeBoundaryTests: test_membership_view_and_update_cannot_stage_or_dispatch,
test_create_stages_then_confirms_once_through_real_runtime_and_registry,
test_revoke_create_after_stage_refuses_yes. This is sensitivity evidence, not
three additional passing tests.

PR198 tests were separately overlaid on authoritative dev without its runtime
patch: tests/test_platform_role_lookup_isolated.py SHA256
9bbd8e0a3b178d65a63a8bd9236c150716ad2685dc846fc3fcbfa72a52680eac.
Command from /data/pr198-current:
`/data/digest-env/bin/python -m unittest discover -s tests -p 'test_platform_role_lookup_isolated.py' -v`
Result: 6 tests, OK, exit 0, 0.016 seconds; no skipped cases reported.
Not an unchanged PR198-head run. Dev's module-level PlatformRoleType import
was preserved. No alternative method-local import was installed.

## Local execution assignment

Coordinator owns current-tree review, registry reconciliation and acceptance;
OpenCode owns every local pytest invocation and local commit. No DB tests run
remotely. Do not repeat the completed sandbox mocks without a changed risk.

### 1. Read-only intake and registry reconciliation

- At /Users/admin/Projects/social-media-ai, inspect fresh heads, git status and
  git worktree list. Reuse an appropriate PR-review lane if safe; never verify
  two PRs in one worktree. Preserve dirty files, private candidates and backups;
  never reset, stash, prune, repin or commit unrelated work automatically.
- Claim P3-T01 IN_PROGRESS in current ROADMAP.md and the phase file using the
  current local workflow. Replace the historical 0c18bab acceptance requirement
  with current-dev-based remaining digest acceptance at an explicitly recorded
  HEAD plus test-diff hashes. Preserve P3-T90/T91/T92 DONE and their attribution.
  Do not remove P3-T01 until its actual acceptance is reviewed. No direct dev push.
- Read .codex/agents/test-runner.toml and .agents/skills/test-runner/SKILL.md.
  Run the exact preflight `pgrep -fl pytest` (no operators); exit 1 means no match.
  Confirm the current shared-DB lane is available; no competing OpenCode/Kilo,
  pytest or other known test writer. A historical pause is not a perpetual lock
  or evidence of today's exclusive window. Do not kill foreign processes.
- Verify the existing resolved localhost:5432/social_manager test_schema target
  without printing secrets. It is already approved for real tests, not schema
  changes. Current conftest validates the existing schema/catalog read-only;
  do not restore the obsolete bootstrap-free plugin or old matrix. Verify current
  ORM completeness and read-only preflight before fixtures write. Keep real
  tenancy guards enabled (`pytest.mark.tenancy` on security tests); bypass may
  be used only for narrowly scoped fixture setup/restoration/owned cleanup.
- Reconcile current safety with the new ACCESS_MIGRATION evidence: test_schema
  0096 operations, 3 platform roles, 5 workspace roles, 31 resources, retained
  five LLM triggers and three functions. Do not require an Alembic version table
  in the deliberately unversioned test schema or replay any migration.
- For the historical 931/932 discrepancy, perform one consistent read-only
  snapshot comparing raw user count, summed platform-role groups (including
  unresolved), NULL/missing role references and actual manager scope. Return
  counts only. Different historical snapshots are not proven corruption.
  New migration/29-case evidence supports current functionality and documented
  preservation within its scope, but does not prove old run-attributed cleanup.
  Do not delete presumed leftovers or claim old preservation from a new baseline.
  If current evidence exposes a concrete unsafe/interrupted state, stop under
  R4 with that specific risk. If historical attribution alone remains unavailable,
  state that limit; do not impose an obsolete failure-only blocker forever.
  Obtain a bounded owner decision only if unresolved state actually prevents a
  safe new run. Never request the already granted target approval again.

### 2. Review and implement only the missing digest acceptance

Start from current maintained fixtures/test patterns, especially
 tests/test_access_postgres_acceptance.py,
 tests/test_web_digests_jobs.py,
 tests/test_runtime_confirmation_authorization.py,
 tests/test_confirmation_consumption_db.py and
 tests/test_session_state_key_patch_db.py.
The latter two are standalone DB scripts with import-time target guards and
argv handling: inspect them, do not broadly pytest-discover or blindly invoke
them. Previously reported generic CAS checks are not new digest E2E evidence;
do not rerun their five cases without a changed risk.

Use narrowly owned new modules, proposed paths
`tests/test_digest_send_postgres_acceptance.py` and
`tests/test_digest_send_asgi_acceptance.py`, only if current tests cannot express
the missing scope. Review the final diff and exact collected node IDs before
execution; do not promise an invented test count. Minimal required behaviors:

A. Real DB identity/session/runtime confirmation:
- Real fixed workspace presets: owner/admin/analyst permit CREATE;
  manager/viewer do not. Ordinary non-owner platform users must be exercised;
  non-owner agent users explicitly opt into AI, owner's admission is implicit.
  Platform-admin plus viewer membership must not bypass digest rights.
- Stage through actual _dispatch_or_stage; inspect persisted version-3 actor/
  workspace-role/AI-bound intent; zero publisher calls before confirmation.
  Confirm through actual runtime with real session manager and fresh identity;
  exactly one mocked publisher call with expected arguments, durable intent
  consumption, unrelated state keys preserved, replay denied.
- Persist role change (including CREATE-capable replacement role), AI revocation
  and inactive/removed membership between staging and yes, and separately after
  real consume returns but before dispatch refresh. A wrapper may await the REAL
  consume then commit a real membership mutation before returning; do not replace
  identity, grant resolution, authorization or CAS with doubles. Assert intended
  refusal and zero sends; distinguish consumed and retained-intent semantics.
- Actual identity/tenant mismatch, expired/old-version/old-UPDATE contract and
  replaced observed intent must fail closed. Reuse existing assertions rather
  than restore stale model expectations. Do not consume a different actor's intent.
- Two concurrent yes turns with independent DB sessions must invoke the mocked
  publisher at most once; demonstrate real consume commit/locking, not merely
  an asyncio in-memory lock. Synthetic acknowledgement loss after REAL commit
  must grant no dispatch authority; verify durable state and retry behavior.
  No assertion of exactly-once external delivery follows from this CAS.

B. Real ASGI/middleware/CSRF send-now:
- Real login/session and current DB membership; owner/admin/analyst success and
  manager/viewer denial. Capture publisher arguments and effective tenant scope;
  AI disabled must not deny an otherwise authorized web digest action.
- With valid CREATE, missing/invalid/tampered CSRF must refuse with zero sends.
  With valid CSRF, platform-admin/viewer and revoked/inactive/missing membership
  must refuse after actual middleware refresh (including changes after GET).
- Posted foreign tenant_id must not widen the caller's workspace. Assert exact
  refusal/redirect or retained-own-tenant behavior, not just a generic non-200.
  Existing history isolation and unrelated queue mutation do not need rerunning.

Never mutate shared seeded role/grant catalogs to manufacture VIEW-only or
UPDATE-only subjects; those synthetic distinctions already have mock coverage.
Use the real fixed presets for DB tests. Mock publisher, providers, transports
and LLM continuation only; do not call builder/provider/runtime owners to get
an application running. No live sends, paid calls or deployment.

### 3. Sequential OpenCode collection and execution

Create a short task attachment under .agents/tmp/ with absolute worktree,
branch, expected HEAD, effective test hashes, exact positional test paths,
behavior and a pointer to AGENTS.md/docs/RULES.md. The attachment is private
human-audit material; do not read full test-report.md into main context.

Primary launch (through the local coordinator's configured test-runner route):

```sh
opencode run --dir <absolute-worktree> -m QwenPlan/deepseek-v4-flash-0731 \
  --auto "Выполни тестовое задание из прикреплённого файла и верни итоговый блок TEST RUN." \
  --file <absolute-worktree>/.agents/tmp/test-task-digest.md
```

Verify the attachment is readable. OpenCode runs `.venv/bin/python -m pytest`:
first collection-only, then the exact reviewed positional modules/node IDs,
with `DB_TEST_SCHEMA=test_schema`, `-o addopts= -q --tb=short --no-header`.
For these real ASGI/DB modules keep current conftest ENABLED intentionally:
it supplies read-only schema validation, vault handling and the reviewed fixture
contract. The default isolated `--noconftest` flags are not a reason to invent
another plugin; if the local route cannot accept this reviewed integration
scope, return that precise blocker rather than silently changing flags.
No broad discovery. No direct coordinator pytest/unittest fallback. Kilo only
if OpenCode is BLOCKED, never concurrently against the shared target.

Serialize collection, execution, owned cleanup and postflight. No automatic
reset/drop/create/stamp/migrate/reseed/constraint repair or missing-table bootstrap.
On interruption, inspect processes and DB state read-only before another run.

### 4. Evidence and closeout

Before/after: record count-only/catalog and private deterministic fingerprints
for retained receipts/journal/outcomes, reference grants and relevant guards;
record generated fixture IDs privately and clean up ONLY those owned rows in
finally/fixture teardown. Preserve unrelated business data and secrets. Report
sequence advances separately; do not infer record preservation solely from counts.
Do not load full .agents/tmp reports into coordinator context.

Return a compact TEST RUN block: branch/HEAD, effective dirty paths/hashes,
actual interpreter/root, actual positional collection/execution commands,
nonsecret target marker, both exits, collected/passed/failed/skipped, exact
failed nodes if any, publisher assertions, cleanup and pre/post comparison,
remaining uncertainty and OpenCode route/model. Evidence is not accepted merely
because an executor says DONE; coordinator inspects diff and exact tested tree.

PR198 disposition: dev's module-level runtime import supersedes its patch.
Its six tests add isolated enum/string/alias normalization, miss propagation,
compatibility lookup and fixed-catalog creation refusal; existing integration
calls exercise enum lookup but are not equivalent direct no-DB coverage.
Retain this test value in a separately owned tests-only lane if desired; never
merge the obsolete local import or add it to dev. Recommend closing #198 as
superseded after the tests-only disposition is recorded. Do not mix its review
worktree with #176 or make those tests a new digest DB acceptance prerequisite.
No automatic close/merge is performed by this assignment.

After actual acceptance, reconcile #176 against fresh dev: retain useful test
additions/current DIGEST contract, discard the stale design-board delta and
obsolete procedural instructions, preserve owner code. Resolve any GitHub
merge conflict through explicit local review, not by restoring old code.
Update phase status/evidence and PR handoff; remove ROADMAP row only on accepted
completion. Keep Draft while required DB/ASGI evidence or conflicts remain.
Full-suite, browser, production and Alembic drift acceptance remain out of scope.
