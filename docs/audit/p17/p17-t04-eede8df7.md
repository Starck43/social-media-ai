# P17-T04 — review of eede8df7

- Date: 2026-11-14.
- Verdict: not accepted; correctable findings, return to IN_PROGRESS.
- Candidate: `eede8df77ba8fabe390092700827ed97ec5193ef`,
  [PR #482](https://github.com/Starck43/social-media-ai/pull/482), target `dev`.
- Review baseline: fresh `origin/dev` at `21070ab3`.
- No implementation changes, merge, migration or provider writes performed.

## Findings

### R1 — high: execution ignores revoked field permissions

`app/services/onec/commands.py:1434-1453` loads the current connection allowlist
but discards `_allowed_fields` and `_expected_sha256`. Only entity-set membership
is rechecked. Preview applies field permissions at lines 1055 and 1069-1075 /
1171-1175; execution subsequently sends the stored `fields` or `changes` without
applying the current field allowlist (1491-1502, 1562-1577).

Reproduction path from code: preview and approve a catalog Description change;
remove Description from the connection's allowed fields while keeping the entity
allowed and its remote DataVersion unchanged; execute. The PATCH still contains
Description. The same issue affects document creation, including its injected
idempotency field. Approval is not authority to ignore a later permission revocation.

Required correction: fail closed before any mutation when any outgoing field is
no longer allowed; compare the approved metadata contract with the current
connection contract and invalidate/re-preview on incompatible changes. Apply the
current entity allowlist to reconciliation reads too (1690-1708 currently discard it).
Required focused regression: revoke a field after approval and assert no POST/PATCH,
an explicit refusal, and durable failure/audit evidence.

### R2 — high: ambiguous post-send transport failures become terminal failed

`send_odata_command_request` sends bytes at lines 677-678. Only TimeoutError is
preserved as an unknown write outcome (728-731); OSError and h11.ProtocolError
are converted to `OneCCommandError("connection_failed")` (732-733). Thus a connection
reset / truncated response after 1C commits takes the conflict-error branch at
1616-1629 and persists `failed`. A malformed/oversized successful response has
the same problem. Reconciliation refuses that row because it requires `unknown`
(1685-1686). The local result falsely claims failure when the remote write may exist.

Required correction: track the send/confirmation boundary and preserve ambiguous
mutation outcomes as unknown until provider reconciliation. Distinguish definite
pre-send failures / authoritative remote refusal from post-send loss of confirmation.
Required focused regression: simulate POST/PATCH applied remotely followed by
connection reset or truncated response; assert persisted unknown, unknown audit,
reachable reconcile, and no second mutation.

### R3 — medium: catalog reconciliation compares serialized values, not typed values

`_format_single_value` stores monetary/decimal changes as fixed-scale strings
(389-410). The remote JSON parser returns numbers as int or Decimal (564), while
catalog reconciliation compares `str(live_item.get(k)) == str(v)` (1765).
An approved monetary value `"100.00"` and a valid remote JSON number `100` compare
unequal. A successful timed-out PATCH is therefore settled as failed solely due
to JSON representation. Datetime/GUID normalization can similarly differ.

Required correction: compare using metadata-driven normalization, not string
conversion; retain unknown if evidence cannot establish a result. Required focused
regressions: equivalent decimal scales / integer JSON spelling of money and a
genuinely different value; assert correct settlement and no duplicate PATCH.

## Checks and evidence

- Inspected PR metadata/body, previous audit and executor follow-up comments,
  candidate task card, exact candidate source, the changes from `200bd70d` to
  `eede8df7`, and shared wiring diff against the PR merge base.
- Previous B1/B2/M1/L1 corrections are present in the candidate source. This does
  not certify the new scenarios above.
- Executor reports 25 focused passes, package coverage PASS and isolated-schema
  cleanup at this head in the PR body / follow-up. These are executor evidence,
  not a new auditor run. No pytest, DB access or coverage rerun was performed in
  this review: the new findings follow directly from reachable source paths and
  must receive focused regression coverage with the corrections.
- Required after correction: focused P17-T04 unit/API/PostgreSQL scenarios,
  package coverage gate from the task card, formatting and reference checks.
  Record exact tested SHA, exit/counts, Alembic baseline and DB inventory/cleanup.
  The reference-check failure was already fixed on dev (`4035e98c`); use a current
  baseline rather than carrying the stale claim that dev still fails it.
- Diagnostic publication checks: `git diff --check` and
  `python3 scripts/check_registry.py` exited 0 (no registry warnings).
- Documents read: `README.md`, `docs/product/DESIGN.md`, supplied `AGENTS.md`,
  `ROADMAP.md` (P17-T04 row), candidate `docs/tasks/p17-t04.md`,
  `docs/process/COLLABORATION.md#tasks`, `#state-machine`, `#evidence`, `#acceptance`,
  `#tests`, `#database`, `#git-and-cleanup`, `tests/AGENTS.md`, `docs/audit/README.md`, and the P17 plan's
  matching task/acceptance lines.

## Resume and cleanup

Preserve `feat/p17-t04-onec-write-commands` / PR #482 at the candidate revision.
The task card and parent phase remain active; no acceptance or integration is
claimed. The clean candidate review worktree is removed at closeout; its unmerged
branch `audit/p17-t04-review` remains at the candidate SHA as a review resume point.
The registry-only `audit/p17-t04-verdict` worktree/branch is removed after verified
publication to dev. Existing foreign worktrees and the owner's
untracked `tasks_plan.md` are not part of this audit and must remain untouched.
