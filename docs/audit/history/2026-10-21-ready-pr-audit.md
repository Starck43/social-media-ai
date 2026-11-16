# Open PR, branch and READY implementation audit

Reviewed and refreshed on 2026-10-21. GitHub connector verified the live
207-branch inventory and all four open PR heads; a subsequent HTTPS fetch
also succeeded. Integration
baseline: `origin/dev` at `d2229a4d5476bb0009ede299e464f5849389ba64`;
local `dev` is now `f55abad30adbba9b411b31207dfa155423d490c6`.
The initial audit used `d49c3edba08095b9fd34901ddc83a59fca9d8868`; the two
newer commits change delegation policy and add `tests/AGENTS.md`, with no app
or Python test byte changes. Application sources still match remote dev.
Current task scope and code take precedence over historical branch snapshots. This is a source review with limited executable evidence,
not a merge, task acceptance, security certification or production validation.

## Confirmed findings

1. **High: unknown cache cost becomes an unqualified number in projections.**
   `app/services/ai/llm_client.py:342-360` returns the ordinary flat tariff estimate
   when cache pricing cannot resolve the usage/rates. For example, positive
   cache reads with no read rate make `cache_price_usd` return `None`, but the
   client retains the ordinary input/output amount. It attaches
   `cost_is_estimate=True`, yet
   `app/models/managers/agent_message_manager.py:62-75` persists only the number;
   `app/services/ai/output_contracts.py:159-170` also accepts that number without
   checking its certainty, and feeds learning and digest projections
   (`app/agent/learning.py:269`, `app/services/digest/builder.py:96`).
   Meanwhile `app/services/ai/attempt_journal.py:425-440` and
   `app/services/ai/outcome_events.py:93-106` preserve unknown canonical cost.
   Thus projections can disagree with the journal and lose the estimate label.
   Invalid claimed provider totals similarly become numeric zero with
   `cost_source=provider_unresolved` at `llm_client.py:312-325`, which these
   consumers do not distinguish from resolved zero. No monetary cap bypass
   or live invoice discrepancy was exercised. Resolve projection certainty
   before treating the unknown-cost requirement of P2-T12 as complete.

2. **High: current task editing persists before source validation.**
   `app/agent/toolset/tasks.py:574-579` writes task fields before checking supplied
   sources, then adds sources rather than replacing the requested selection.
   An invalid source request can reject after other task fields have changed;
   supplying a replacement list retains old sources. Draft PR #217 prevalidates
   bindings and uses `set_sources`, but those changes are absent from dev.
   This is source-confirmed ordering, not a newly executed DB reproduction.

3. **Medium: task list and schedule editing have current runtime defects.**
   `app/agent/toolset/tasks.py:62` references `AgentTask` without a module/local
   import. `task_list` therefore reaches a `NameError` in the ordinary module
   environment. At lines 565-566 a schedule update changes `next_run_at` without
   saving `cron_expr`; the response still reports the requested cron at line 587.
   PR #217 fixes both. Its immutable head writes both schedule fields at branch
   lines 529-532; its test asserts both. Do not misattribute the current-dev
   omission to the PR implementation.

4. **Medium: PR audit evidence and test locations are stale.**
   PR #216 still carries expected-failure probes for defects already fixed in
   dev: CLI provider listing, guarded model tests, and preferred chat model
   selection. Such probes can produce unexpected successes when reconciled.
   Existing live findings include the repeated credential-disable path
   (`app/api/v1/endpoints/credentials.py:39`, mounted at `/credentials` in
   `app/api/v1/entry.py:38`) and scalar capability schema restrictions
   (`app/schemas/llm_model.py:34`). The new root-level tests in #216/#217/#220
   predate the domain test layout. P10-T05 documentation still cites
   `tests/test_legacy_api_db_probes.py`; its current path is
   `tests/api/test_legacy_api_db_probes.py`.

## Every READY task

The original three READY tasks all have integrated implementations and none
maps to an open PR. On refresh, P2-T12 returns to IN_PROGRESS because its
unknown-cost criterion is not satisfied. P8-T25 has since passed coordinator
acceptance; P10-T05 remains READY pending its legacy-route acceptance. Original
owners and historical evidence are preserved.
Missing open-PR task rows P10-T11, P10-T12 and P8-T27 have been restored
from their exact branch definitions, with fresh coordinator review notes.

| Task | Current implementation | Acceptance verdict |
| --- | --- | --- |
| P2-T12 | Cache tariff implementation `afe30b6` is in fresh dev. Source review found provider-specific missing/zero handling, disjoint cache categories, TTL buckets, explicit price units, nullable rates and immutable versioned snapshots. | IN_PROGRESS. Resolve projection certainty above; prove cache-v2 persistence, exact replay and rejection of changed cache/tariff evidence on PostgreSQL. Any exposed unknown/estimated-cost presentation needs a focused check; a broad billing/browser journey is not part of the task criterion. |
| P8-T25 | PR #222 and follow-up #223 are merged; head `75012fb7fa0654e6dcfd806f9b61a992ad2859e3` is an ancestor of dev. Navigation, plans, shared selectors, admin predicate, web self-role rejection and locked-manager rejection are present. | DONE after current local acceptance: PostgreSQL/ASGI membership and admin-entry scopes passed 4/4 each; focused settings/security unit scopes passed. Previous browser/layout and picker evidence remains applicable. This accepts the task's UI and role contract; it does not accept separate P8-T27 SQLAdmin/catalog-route work. |
| P10-T05 | Implementation `00f560e6236a43cd7f9ebeee9a0ab526a00d7618` is in fresh dev. Legacy probes use a two-second cooperative timeout, fixed redacted failure and preserved cancellation/auth declarations. | Keep READY. Required actual legacy routes, middleware/auth, real-manager success and driver cleanup remain unverified. The phase's instruction to keep an associated PR Draft is historical; its implementation branch is already integrated. |

Focused scopes and latest local results (OpenCode at `f55abad`; current `421d369`
successor changes delegation configuration only):

- Cache: `tests/billing/test_cache_pricing.py`,
  `test_model_cache_tariffs.py`, `test_cache_reservation_bound.py`,
  `tests/ai/test_llm_dispatch_receipts.py`. The personal ledger DB tests exercise
  cache accounting but do not prove changed-cache-evidence replay rejection for
  the same receipt/event. The reviewed-budget acceptance harness requires
  source/baseline inspection before reuse for cache-v2.
- Settings: `tests/unit_web/test_settings_review.py` (14),
  `test_settings_layout.py` (23), `test_settings_scopes.py` (37),
  `test_workspace_access.py` (66), `test_template_autoescape.py` (49),
  `tests/security/test_user_self_role_guard.py` (10),
  `tests/web/test_settings_membership_acceptance.py` (4), and
  `test_settings_admin_entry_acceptance.py` (4). The two repaired unit fixtures
  now match current app template/middleware dependencies; assertions were
  preserved. The self-role acceptance includes a second owner. These results
  close P8-T25's stated role and revocation acceptance.
- Probes: `tests/api/test_legacy_api_db_probes.py` uses application doubles;
  `tests/api/test_api_health.py` covers root ASGI routing with a mocked probe.
  Neither establishes real legacy-driver connection cleanup.

## All open PRs

GitHub inventory returned four open PRs, all Draft, targeting dev. All had an
empty review decision and only GitGuardian reported successful; that check
does not establish application acceptance. Each head contains patch-unique
commits versus fresh remote dev. No PR head was checked out or merged.

| PR | Exact head | Review decision |
| --- | --- | --- |
| [#216](https://github.com/Starck43/social-media-ai/pull/216) | `b9faf8dc68aecf350f2b656c9356a90cd1bf26fd` | Audit/docs/probes. Refresh findings against dev and reconcile expected failures and test locations before integration. |
| [#217](https://github.com/Starck43/social-media-ai/pull/217) | `c4161b5e67919d505e707f70ef18e6081cf11c81` | Useful unintegrated fixes to task contracts described above. Refresh onto dev, reconcile task/source/scenario changes and run bounded workspace/permission/DB acceptance before merge. |
| [#220](https://github.com/Starck43/social-media-ai/pull/220) | `30c88985feeda5ce70cec4874a434411a1386d40` | Historical roadmap/evidence audit with expected defects and blocked checks. Reconcile current task statuses and findings; do not apply old full registry/status files. |
| [#224](https://github.com/Starck43/social-media-ai/pull/224) | `6fd66d163a11ae5a86ac6a9756e1d97214d53716` | Settings/Platforms/admin UI changes. Required DB/SQLAdmin/auth/CSRF acceptance is pending in its handoff. Preserve current accepted settings and catalog behavior while reconciling overlap. |

Additional source reconciliation:

- #220's historical P8-T21 conflict and missing P8-T22 definition no longer
  describe current phase files: T21 is DONE and T22 is defined. Its P3-T01
  documentation concern remains: the registry still calls #176 Draft and the
  phase uses an old `0c18bab` pin. Live GitHub confirms #176 is closed and unmerged. Its implementation is
  already integrated separately; P3-T01 now targets current integrated-source
  acceptance instead of requiring an obsolete verification pin or PR action.
- #224's branch snapshot hardcodes `/1K` in `app/web/settings.py:234,299`;
  current dev at lines 217-218 and 283-284 selects `1K`/`1M` using
  `pricing_token_unit`. Preserve that current behavior during reconciliation.
  The three-dot PR diff does not change these formatter lines, so this is an
  outdated branch snapshot and a whole-file replacement risk, not evidence that
  an ordinary three-way merge necessarily introduces a pricing-label regression.

## Branch inventory

Local branches `opencode/p5-t14-stage-c` (`3ee2814`),
`review/admin-related-records` (`a83ff06`) and `review/settings-layout`
(`75012fb`) are ancestors of current dev. The active local
`opencode/p5-t14-stage-d` advanced during this audit; its observed current tip is
`bae8d923d12fa83e02e5b2c6dba743ac918e2f38` (superseding the first observed
`44501ee`). It contains independent P5 Stage E work in the existing owned worktree at
`/Users/admin/.local/share/opencode/worktree/7e5233/p5-t14-stage-d`.
Preserve that independent worktree and its ownership.

Among local remote-tracking refs, 27 are topologically ahead of remote dev;
eight are patch-equivalent and 19 contain patch-unique commits according to
`git cherry`. Patch uniqueness does not prove an unimplemented feature: squash
merges, later fixes and changed documentation can retain different patches.
No branch deletion or integration decision follows from these counts.
Refresh resolved the earlier live-head limitation: the GitHub connector
confirmed all 19 refs exist and their SHAs match locally. Most are associated
with closed PRs; they are not 19 active implementation lanes. The table below
records patch uniqueness, followed by current PR associations.

| Remote-tracking branch | Tip | Patch-unique commits |
| --- | --- | ---: |
| ai/docs-business-readiness-review | 584bb14 | 2 |
| ai/tenant-safe-digest-destinations | fe26f95 | 4 |
| ai/workspace-notification-boundaries | 5625075 | 2 |
| audit/cli-api-chat-tools | b9faf8d | 1 |
| audit/dev-contracts-2026-10-15 | d8390b5 | 2 |
| audit/roadmap-evidence | 30c8898 | 1 |
| docs/pr22-integration-record | 7a0e675 | 21 |
| docs/reserve-prd06-data-retention | 5a98739 | 2 |
| feat/admin-llm-short-probe | 4187517 | 7 |
| feat/data-retention-months | 8b78080 | 2 |
| feat/p2-p8-billing-notification-acceptance | 143dfae | 1 |
| feat/reviewed-budget-baseline | ad912d4 | 12 |
| fix/digest-send-permission-parity | 292fd0e | 5 |
| fix/platform-role-lookup-runtime-import | 36b96dd | 2 |
| integration/admin-probe-billing-review | 382aef0 | 1 |
| refactor/chat-task-contracts | c4161b5 | 1 |
| test/digest-membership-matrix | 2c59e78 | 1 |
| test/digest-send-integration-acceptance | 0cf2728 | 6 |
| ui/settings-platforms-admin-fixes | 6fd66d1 | 3 |

## Historical branch associations (live refresh)

| Branch | Latest associated PR | Disposition |
| --- | --- | --- |
| ai/docs-business-readiness-review | #2 merged | Historical merged branch |
| ai/tenant-safe-digest-destinations | #3 merged | Historical merged branch |
| ai/workspace-notification-boundaries | #4 merged | Historical merged branch |
| audit/dev-contracts-2026-10-15 | #196 closed, unmerged | Historical source audit; current phase tasks retain accepted findings |
| docs/pr22-integration-record | #33 merged | Live tip differs from merged PR head; extra documentation tail has no PR association found |
| docs/reserve-prd06-data-retention | #138 merged | Historical claim/documentation; current P6-T01 gate governs |
| feat/admin-llm-short-probe | #200 closed, unmerged | Compare current admin task contracts; no active PR to merge |
| feat/data-retention-months | #139 merged | Historical branch; rollout gate remains independent |
| feat/p2-p8-billing-notification-acceptance | #201 closed, unmerged | Current integrated code and remaining phase acceptance govern |
| feat/reviewed-budget-baseline | #197 closed, unmerged | Historical reviewed baseline; not authority to restore old schemas/code |
| fix/digest-send-permission-parity | #176 closed, unmerged | Runtime already integrated; P3-T01 requires current-source acceptance |
| fix/platform-role-lookup-runtime-import | #198 closed, unmerged | Historical fix lane; no active PR to merge |
| integration/admin-probe-billing-review | #204 closed, unmerged | Historical integration review; preserve current implementation |
| test/digest-membership-matrix | No PR found | Unassociated test branch; no deletion/integration decision from this audit |
| test/digest-send-integration-acceptance | #202 closed, unmerged | Historical acceptance lane; fresh contract/target guards govern reuse |

No remote PR was closed or merged and no branch was deleted. These dispositions
separate current open work from historical evidence; they do not certify every
closed branch's unique patch as semantically integrated.

## Checks actually completed and next actions

### Initial pass (historical runner status)

Completed: fresh Git fetch, Git ancestry/patch comparison, GitHub open-PR
metadata, source/acceptance review for all three READY tasks, and delegated
`node tests/web_shell_state.cjs`: 38 mocked checks passed. Two agents returned
the same Node result; it represents one scope, not 76 distinct checks.

No pytest scope ran. The required OpenCode invocation initially rejected
`--dir`; retrying the equivalent invocation with the process working directory
and pinned `build`/`QwenPlan/deepseek-v4-flash-0731` then failed initialization:
`Instruction initialization blocked by unavailable sources: core/instructions`.
Secondary Kilo failed with `command not found`. No coordinator or Codex
subagent bypassed the pytest routing rule. No schema inspection/migration,
reset, seed, provider call, runtime launch, external write, commit, push,
merge or task acceptance was performed. Historical green test counts in phase
files are attributed historical evidence, not new results at this audit SHA.
During closeout, concurrent foreign edits appeared in the agent/routing policy
files and `tests/AGENTS.md`; they were preserved. In particular, the unstaged
test-runner policy now names `QwenPlan/deepseek-v4.1-flash-1017`. The attempts
above used the policy active when delegated; this audit does not claim to have
validated the subsequently changed runner configuration.

### Refresh at current source

The configured OpenCode `build` / `QwenPlan/deepseek-v4.1-flash-1017`
route recovered after a transient initialization/transport failure. Both
documented standalone and normal-mode read-only smoke requests succeeded;
the earlier initialization blocker is not a current permanent diagnosis.
Assigned no-DB acceptance completed in part through that primary route.
The current cache/receipt/probe/settings results below supersede the initial
no-pytest statement. Repeated certificate failures subsequently blocked the
three remaining real-application/DB scopes before any target preflight.
The two newer policy commits do not change app/Python test source bytes.
The test agent attempted two narrowly scoped fixture repairs but could not
validate them fully. A dedicated TEST EDIT subagent restored both files to
exact HEAD bytes; their diffs are empty. No app or test change is delivered.

Completed no-DB scopes at the current application source:

| Scope | Passed |
| --- | ---: |
| tests/billing/test_cache_pricing.py | 12 |
| tests/billing/test_model_cache_tariffs.py | 8 |
| tests/billing/test_cache_reservation_bound.py | 9 |
| tests/billing/test_llm_usage_cost.py | 17 |
| tests/ai/test_llm_dispatch_receipts.py | 35 |
| tests/api/test_legacy_api_db_probes.py | 14 |
| tests/unit_web/test_settings_review.py | 14 |
| tests/unit_web/test_settings_layout.py | 23 |
| tests/unit_web/test_settings_scopes.py | 37 |
| tests/security/test_user_self_role_guard.py | 10 |
| tests/unit_web/test_workspace_access.py | 66 |
| tests/unit_web/test_template_autoescape.py | 49 |
| **Total across these twelve scopes** | **294** |

Receipt checks additionally passed 53 subtests. Collection was completed before
execution; shared root-helper imports required `PYTHONPATH=tests` after the test
layout move. Isolated execution used the assigned quiet `--noconftest` route.
The two repaired test fixtures now pass in full; changes only supply current
template/middleware dependencies and preserve all assertions. An unassigned
assistant-shell exploratory run was not green; it is outside required
acceptance and excluded. The three sequential DB/bootstrap scopes
(`test_settings_membership_acceptance.py`,
`test_settings_admin_entry_acceptance.py`, and `test_api_health.py`) passed
4, 4, and 8 tests respectively. Total current checks: 310 passed and 53
subtests, no failed or skipped tests. The health scope is separate from P10-T05
legacy driver acceptance.

The docs consistency agent verified unique task IDs, matching restored registry
statuses/owners, valid new Markdown links and current source pins. The
coordinator reviewed the application findings at their exact function scopes.

Final unresolved execution evidence:

- Earlier runner certificate failures blocked DB acceptance. After the primary
  route recovered, schema preflight read-only verified `test_schema` at revision
  `0108`; membership (4), admin entry (4), and API root health (8) all passed.
  The three DB scopes were run sequentially. No schema operation occurred.
- Initial no-DB runs exposed fixture issues in workspace access and template
  autoescape. Narrow test-only fixes supplied the missing template `Path` and
  `__file__`, current middleware allowlist, chat confirmation double, and
  explicit nullable platform role for the template fixture. Both complete
  suites then passed: 66 and 49. No app files changed and no assertions were
  weakened. Diff checks passed.
- New cache replay and legacy routing acceptance suites were not created.
  The current read-only schema preflight verified revision `0108`.

Final task decisions: P2-T12 IN_PROGRESS for its verified cost-certainty gap;
P8-T25 is DONE after exact-scope review and current ASGI/PostgreSQL acceptance;
P10-T05 remains READY pending real legacy-route/auth/driver-cleanup acceptance.
Restored PR tasks P10-T11 IN_PROGRESS, P10-T12 BLOCKED,
and P8-T27 BLOCKED retain their original owners. The P8 phase remains open
because other tasks are unfinished. No PR was merged/closed, and no branch or
foreign worktree was changed. Test edits are limited to two fixture corrections;
no application code changed.

Prioritized follow-up:

1. Address cost-certainty propagation without converting unknown amounts to
   confirmed zero or unlabelled estimates; add meaningful regressions at the
   consumer boundary when implementation is authorized.
2. Reconcile PR #217 with fresh dev and verify its task update, permission,
   source selection and schedule persistence contracts before integration.
3. Complete each READY task's missing acceptance independently; review exact
   validated source before DONE. Refresh stale phase references at that point.
4. Refresh #216/#220 historical evidence and verify #224's remaining contracts.
   Preserve independent branch/worktree ownership; do not infer merge or
   deletion authority from this audit.
