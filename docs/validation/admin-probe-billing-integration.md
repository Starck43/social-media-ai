# Admin probe and billing integration candidate

## Scope and authorization

This is a documentation-only integration preparation lane, not a combined code
implementation or accepted feature. The owner authorized a new branch and Draft
PR only. Source branches must remain unchanged. Schema owner and both source PR
owners must approve before integration code review proceeds. Those approvals
have not been supplied in this lane; do not infer them from PR authorship or from
the authorization to open this Draft.

No database connection, collection, schema operation, application/runtime start,
provider call, paid call or deployment is permitted in this lane. Merge into dev
requires explicit owner authorization, regardless of general routine-merge rules.
The separate digest acceptance/maintenance lane is not permission for these
admin/billing actions.

## Pinned inputs

Verified from fresh Git refs and source PR descriptions:

| Input | Branch | Commit |
| --- | --- | --- |
| dev | dev | 92d6e2e4372212b940e4fad953998fe33ee2fe5a |
| PR #200 | feat/admin-llm-short-probe | 41875179261341ae823b1d37f7b0b61e3d260b76 |
| PR #201 | feat/p2-p8-billing-notification-acceptance | 143dfae7bb52974f129818394b63a625fd799da4 |

- https://github.com/Starck43/social-media-ai/pull/200
- https://github.com/Starck43/social-media-ai/pull/201

Recheck all heads after approval. An unexpected head change requires a new
compatibility assessment; never relabel historical evidence as candidate evidence.
The task registry remains ROADMAP.md plus docs/phases, not this document. Source
PR descriptions and published dev have differing task/status statements; this
preparation lane does not adjudicate those statuses or mark any task DONE.

## Approval and source gates

Before opening source diffs or combining implementation, obtain explicit code-
review approval from the schema owner, PR #200 owner and PR #201 owner. Record
actual approval references; no approval reference is currently available here.
Do not send person-directed comments or infer reviewer identities.

The user referred to tests/test_admin_model_probe_local_acceptance.py as provided
above, but its contents were not available in the active or searched session
history. PR #200 describes an owner-local untracked five-test file. Do not invent,
replace or claim to have reviewed it. Obtain the exact file contents or a
published branch/commit/blob reference before reviewing its coverage.

## Compatibility questions to review only after approval

PR #200 describes prompt-free local short checks, distinct mock/explicit capped
real modes, GET render-only behavior and POST CONFIGURE/CSRF/explicit-consent
boundaries. Unknown usage must not become zero and an error must not cause an
automatic paid retry. Preserve escaping and provider-failure semantics.

PR #201 describes personal billing, fixed task-payer consent, guarded admin
probes, reservations, immutable ledger settlement, attribution migrations and
notification preferences. Its PR description reports local migrations through
0103; this is source-reported local history, not authorization to apply or replay
migrations here.

Source PR descriptions identify overlapping admin actions/endpoints/model_probe
and a schema baseline mismatch. The reported 13 live tables absent from #200's
metadata are attributed to #201's billing/preferences additions. This is not
independent verification of a current database, permission to delete objects, or
proof that a combined candidate will pass.

After approval, review those overlaps as one bounded compatibility problem:
resolve payer/consent/accounting semantics without weakening authorization,
retaining obsolete policy, copying unrelated features solely to satisfy preflight
or changing required checks. Review schema qualification and migration ordering
statically only. Missing owner decisions remain explicit blockers.

## Evidence boundaries

PR #200 reports 74 remote unit PASS, 56 offline browser cases and an owner-relayed
74 local PASS at its pinned revision. Its local real acceptance was blocked at
setup by unknown tables, with zero behavioral scenarios executed.

PR #201 reports 15 DB acceptance PASS and earlier 77 no-DB, 17 preference-client
and 8 dashboard-browser checks. These are source-reported results at that PR's
own revision/scope. They are neither candidate evidence nor a combined-suite PASS.

No tests, browser fixtures, collection or application code imports were executed
for this preparation commit. Do not repeat passed source checks without a
specific changed risk. Any later execution scope requires a separate explicit
assignment consistent with the no-DB/no-runtime/no-paid-call restriction; actual
DB/runtime/paid execution needs new owner permission.

## Draft handoff

Current deliverable: pinned-input/approval handoff only. No implementation from
#200 or #201 has been merged or cherry-picked into this branch. No acceptance
file has been added because its source is missing. Keep Draft; do not request
Ready, change source branches, merge to dev or declare compatibility/acceptance.

Next required inputs: the three owner approvals for code review and the exact
owner-local acceptance file (or its published immutable reference).
