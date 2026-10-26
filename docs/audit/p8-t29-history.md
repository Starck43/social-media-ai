# P8-T29 — Implementation history and unresolved diagnostics

This is a historical evidence archive for the active [P8-T29 task](../tasks/p8-t29.md),
not an acceptance audit. The active card and ROADMAP own current status. Old test
results belong to their original commits and do not certify the integrated `dev`
revision or the remaining user journeys.

Registry: [ROADMAP](../../ROADMAP.md#p8); status/owner live there.
Parent: [P8](../phases/p8-first-value-ui.md). Contract: [Universal tasks](../AGENT_TASKS.md#explicit-task-plans).

## Owner decisions and acceptance

2026-10-25: replace the original confirm-every-paraphrase plan with the shared
social task contract. Preserve design and standalone sources/scenarios/tasks/
schedules/digests. Ship web/API/CLI/admin/agent parity. Automatic one-off research
within personal limits; explicit confirmation for schedules and external sends.
Allow temporary discovered sources within the user's requested scope.

Stages: documentation and future cards; shared task contract and compatibility;
all five surfaces; VK search/discovery/walls feeding staging and analysis;
focused validation and bounded live VK availability evidence. Fixed/rolling
windows, explicit scope, payer, fresh rights, duplicate admission and honest
coverage are acceptance requirements. No READY claim with missing evidence.

Dependencies: reuse P5-T14; Q-5 is real VK profile search availability. P10-T07
and P3-T01 are already accepted in the integrated base; preserve their digest
scope and permission contracts.
At the time of the 2026-10-25 original task plan, future work was listed as
P8-T33/P8-T34. P8-T10/P8-T11 had existing worktrees. No
redesign, general refactor, push, deployment or public migration.

## Ownership and execution

Claim: codex-p8-t29, 2026-10-25, owner's "Implement the plan" in this dialogue.
Branch: `feat/p8-t29-universal-social-tasks`.
Worktree: `/private/tmp/social-p8-t29`.
Base SHA: `0997534a65a55a0d5677a275e014100a72ad48e8`.
Owned scope: shared task/collection services, related web/API/CLI/admin/tools,
focused checks and linked documentation. Parent Codex owns registry/docs;
record exact child file ownership before delegating writes. Preserve foreign work.

## Documentation actually consulted

- README/AGENTS/task skill and COLLABORATION Tasks, Evidence, Delegation, Tests,
  Database, Git and cleanup: workflow, ownership and validation.
- PRODUCT_PLAN Extension contracts; DOCS_INDEX/PROJECT_MAP: architecture.
- TENANCY How it works/Plans and quotas: workspace and credential limits.
- COLLECTION VK/OAuth; design/collection-plan; Q-5: collection and restrictions.
- AGENT Tools/Confirmation flow; AGENT_TASKS Adding a task: task compatibility.
- API Tasks; CLI Task; ADMIN Custom actions/Run task now: parity.
- DIGEST Pipeline/Configuration/Manual runs: generation versus sending.
- design/assistant-shell Page inventory/State and authorization: keep design.
- P10-T01/T03/T04/T06/T07, P7-T01, P8-T13: parallel scope/dependencies.
- test-runner skill and tests/AGENTS: exact focused test evidence.

## Implementation and validation evidence

Implemented on `feat/p8-t29-universal-social-tasks` in `/private/tmp/social-p8-t29`.
The task branch now includes the four-worktree slices and a final fixture-only
cleanup. All four work branches were refreshed from `origin/dev` at
`e8501686dba9fcc6f22f03d7c909851ecba581fe` before PR preparation. They preserve
the `MAX_WORKSPACE_MEMBERS` ceiling and current Q-11 resolved / Q-12 deferred
decisions. The shared universal-task design file has the same content hash in
all four branches. At this point-in-time handoff, no solution push, deployment
or public migration had occurred.

The implementation stores one versioned plan across web, API, CLI, admin and
agent tools; existing task/source/scenario/job paths execute it. It adds explicit
selected/discovered/all scope, VK/TG platform filters, bounded queries and
source IDs, fixed/rolling windows and timezone handling. One-off launches use a
stable request key, current actor and membership rights, existing personal
payer consent, quota checks and a durable task receipt. Scheduled runs and
external sends retain their existing confirmation paths. Results expose
permission-filtered summaries and bounded progress. Legacy routes and task
semantics remain supported. UI design was not changed.

The focused run at source SHA
`f399ac5479e8aab30d81e6dcd27644f5ddcfba03` passed all 11 assigned paths:
**136 passed, 28 warnings, no failures**. Nine source-only suites had already
passed 60 tests at `d6f25cb7d0ca1436f5486784b6106bf641b32cc3` (2 warnings); after
adding the missing field to the isolated SQLAlchemy `Source` fake, the API
interface suite passed 11/11 and source-job admission passed 36/36 at f399.
Warnings were dependency deprecations and two pytest warnings for synchronous
tests inheriting an asyncio mark. Successful launch cases double only the
existing payer-consent facade and assert it was called for the new task IDs,
avoiding append-only consent fixtures. Real DB membership, task/job admission
and replay checks still ran; this does not prove billing settlement or calls to
VK. Each DB-backed file was collected and run in a fresh Python process,
serially, with the ordinary root conftest. Environment:
`PYTHONPATH=/private/tmp/social-p8-t29 DB_TEST_SCHEMA=test_schema DEBUG=false`
and interpreter `/Users/admin/Projects/social-media-ai/.venv/bin/python`.
The command forms were:

```sh
<python> -m pytest -o addopts= --collect-only -q --no-header <file>
<python> -m pytest -o addopts= -q --tb=short --no-header <file>
```

The source-isolated `tests/collection/test_source_job_admission.py` used
`PYTHONPATH=/private/tmp/social-p8-t29/tests:/private/tmp/social-p8-t29` and
`--noconftest`, as its module contract explicitly forbids importing the app,
engine, credentials or database. All other suites used root conftest.

| Focused path at `f399ac5` | Passed |
| --- | ---: |
| `tests/api/test_p8_t29_task_plan_interfaces.py` | 11 |
| `tests/collection/test_source_job_admission.py` (documented isolated no-DB mode) | 36 |
| `tests/api/test_api_task_collection.py` | 4 |
| `tests/web/test_web_task_collection.py` | 6 |
| `tests/cli/test_cli_task_run.py` | 13 |
| `tests/jobs/test_task_pending_occurrence.py` | 33 |
| `tests/jobs/test_p8_t29_social_launch.py` | 9 |
| `tests/api/test_p8_t29_task_plan_persistence.py` | 2 |
| `tests/admin/test_admin_task_form.py` | 15 |
| `tests/agent/test_agent_task_collection_tools.py` | 4 |
| `tests/web/test_web_analysis_repeat_policy.py` | 3 |

After improving the CLI fixture to clean its own notification rows, the affected
CLI file was rerun at `7f09763f4933125e7363a96927464cbefd8195b8`: 13 passed, one
dependency deprecation warning and zero fixture residue. This commit changes
only that test fixture; application code matches the f399 candidate.

### Earlier test-schema migration investigation

Before the later owner request to update `public` through `0112`, migration
`0112` added three nullable launch-receipt columns and a unique
constraint after `0111`. During local validation, `test_schema` reported
revision `0112` while those columns were absent; root conftest stopped before
test bodies or fixture writes. With an exact schema/table/column guard, the
existing 0112 Alembic upgrade operations restored the missing objects without
rewriting version history, preserving all 14 task rows. After testing, the
standard Alembic downgrade returned only the shared local test schema to
revision `0111`; comparisons found every other table's row count and primary
keys unchanged, all 30 pre-existing billing accounts preserved, consent
triggers enabled, and no owned fixture residue. At that earlier point, the
public schema had not been migrated.

A previous compatibility run's exact residues were cleaned by identity,
timestamp and relationship using existing ORM managers. It removed only 14
run-created tasks, six workspaces, four sources, five jobs and four
notifications. Final snapshots confirmed no remaining owned data. The temporary
test-schema HOLD was released at
`2026-10-26T07:19:31Z`.

The current bounded VK probe at `2026-10-26` made two read-only requests using
the configured service credential and API 5.199: `newsfeed.search` and
`wall.get`, each with an 8-second timeout. Both returned `ConnectTimeout`. This
is inconclusive about method permissions or availability from the deployment
network; it neither confirms nor disproves the historical profile error 1051.
Q-5 remains open. Required live VK availability evidence is still missing, so
this task remains **BLOCKED on that acceptance gate**, not accepted/READY/DONE.
No browser, token refresh or data write was used.

Future scope is now sequenced across P8-T33 and P8-T38–P8-T41; P8-T34
(non-social connectors) is deferred beyond the social milestone. P10-T08 (home
component permission gates) and P10-T22
(dispatch-identity/tariff receipt contract) are independent queue candidates;
P10-T09 depends on P10-T08. P8-T10/P8-T11 have existing worktrees and are not
available parallel lanes. Refresh ROADMAP before claiming any task.

### Documentation consulted for this continuation

- `README.md`, `AGENTS.md`, `.agents/skills/task/SKILL.md`: project hierarchy,
  branch ownership and task evidence.
- `docs/tasks/p8-t29.md`, `docs/AGENT_TASKS.md#explicit-task-plans`: goal, shared contract
  and acceptance boundary.
- `docs/COLLABORATION.md` sections Tests, Database and Git and cleanup;
  `tests/AGENTS.md`; `.agents/skills/test-runner/SKILL.md`: runner isolation,
  serialized schema work, fixture cleanup and handback.
- `docs/tasks/q-5.md` and the bounded VK probe result: distinguish historical
  profile results from the current connectivity result.
- `ROADMAP.md` P8/P10/Questions rows: refresh status and identify open,
  independent parallel candidates.

### Migration 0112 handoff review (2026-10-26)

After the owner asked to “актуализируй все до 0112, включая public” (2026-10-26),
the standard Alembic command `DB_SCHEMA=<schema> .venv/bin/python -m alembic
upgrade 0112` exited 0 for `public`, `test_schema`, and `test_schema_p10t19`.
The executed order was `public` first, then the two test schemas. Therefore this
does **not** establish that the migration was first validated on a test schema;
the public migration preceded those upgrades. The authorization was explicit,
but it does not supply the missing prerequisite evidence required by current
[Database rules](../COLLABORATION.md#database).

Read-only post-migration inspection found the three schemas at 0112 with
`launch_key`, `launch_fingerprint`, `launch_job_id` and `UNIQUE (tenant_id,
launch_key)`. Task row counts remained `public=2`, `test_schema=0`, and
`test_schema_p10t19=0`; immutable payer-consent triggers remained enabled where
the table exists. No synthetic rows were added or removed. Three unrelated
schemas without Alembic version state (`test_analysis_receipts`,
`test_schema_backup_0108_20261021`, `test_schema_p5t14`) were not changed.
The shared DB HOLD was released at revision 0112. These are schema-state
observations, not application-test or migration-prerequisite evidence.

The owner said “вливай и я буду сам тестировать ошибку” before the integration
to local `dev`; the aggregate was fast-forwarded to
`597b5e78d6a0c6bd41bd82ba3312b1aef287d3c2` (31 commits ahead of
`origin/dev=e8501686dba9fcc6f22f03d7c909851ecba581fe` at that point). No tests
were run after integration, per the owner's plan to test the VK error. The
bounded VK probe remains inconclusive: both methods timed out, so Q-5 remains
open and this task remains BLOCKED on the live VK availability acceptance gate.

The dialogue's URL/session ID is not available in this checkout; ROADMAP keeps
the dialogue field unknown rather than using the Q-5 dependency link as a
dialogue link. The relevant decisions and outcomes are preserved here. Documents
read for this continuation: `README.md`, `docs/DESIGN.md`, `AGENTS.md`,
`ROADMAP.md`, `docs/COLLABORATION.md#tasks`, this card, `docs/tasks/q-5.md`, and
`docs/phases/p8-first-value-ui.md` for hierarchy, dialogue/evidence ownership,
task status and parent-plan responsibilities. No tests were run for this
documentation-only update.
