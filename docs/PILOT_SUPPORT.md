# Managed pilot support and measurement contract

**Revision:** P8-T13 draft v1. **Approval state:** DRAFT — not owner-signed.
**Proposed accountable owner:** s_shabalin (repository/project owner).
This nomination and every service target below require the owner's explicit
approval of an exact document commit. Assignment to write the draft is not a
signature, pilot launch, operational SLA or permission to perform external actions.

Task record: [P8-T13 audit](audit/p8-t13.md). Parent: [P8](phases/p8-first-value-ui.md).
This is a manual operating contract over existing signals, not new telemetry,
a support integration, a delivery feature or production-readiness acceptance.

## 1. Scope and operating prerequisites

- One managed pilot workspace and the existing, explicitly selected task/run
  cohort, named by the owner in a private pilot log before measurement begins.
  Unlisted workspaces/tasks are outside this contract, not silently included.
- Use existing supported collection/analysis/digest paths and existing permissions.
  An operationally unverified VK search method does not become supported through
  this document. [Q-5](tasks/q-5.md) and release gates remain independent.
- Proposed service hours: Monday–Friday, 09:00–18:00 **Europe/Kirov** (UTC+03:00),
  without an implied holiday or 24/7 rota. Outside those hours, record the event;
  the response clock resumes at the next service opening. Safety restrictions
  apply at all times. No staffed backup or automatic paging is promised.
- The owner records the cohort, observation dates, authorized observer and signed
  revision before activating these proposed targets. They are activation facts,
  not missing code dependencies. No measurements or operating approvals were
  obtained while drafting this document.

### Runtime freshness is opt-in, not an assumed green signal

`RUNTIME_STATE_DIR` defaults to **unset**. `/runtimez` then returns
`state=unconfigured` and HTTP 503. Runtime freshness is **not measurable** until
an operator explicitly configures API and the sole runtime with the same trusted
local POSIX directory, OS user/permissions and host/kernel lock namespace.
Enabling this is an operator change under the existing deployment procedure,
not an incident-time read-only diagnostic and not authorized by this document.
An unset value is a measurement prerequisite, **not a blocker to drafting T13**.

The implementation writes a heartbeat every **15 seconds**, classifies age
**greater than 60 seconds** as stale, and returns HTTP 200 only for `fresh`
(running record plus held owner lock). At age 60 seconds the running record can
still be fresh. `scheduler_enabled` is configuration, not scheduler progress.
A fresh heartbeat proves event-loop presence only: not queue, worker, listener,
provider, source-data freshness, analysis quality or delivery success. Never
substitute `/livez`, `/readyz`, API uptime or an old runtime id for this signal.

Sources: [runtime configuration](CONFIGURATION.md#optional-same-host-runtime-observation),
[HTTP probes](DEPLOYMENT.md#2-interpret-the-http-probes-correctly),
[runtime signal](../app/core/runtime_presence.py), [HTTP route](../app/core/health.py).
P8-T10 code `d7213c6b` and P8-T11 integration `83c1f79a` are included in this
contract's source base. T10's remaining registry closure belongs to the owner;
it does not block this draft or certify production operation.

## 2. Named ownership, channels and escalation

| Responsibility | Proposed named owner / channel | Limit |
| --- | --- | --- |
| Accountable pilot owner, first triage and escalation decision | s_shabalin | Proposed; no separate support team or backup is invented. |
| Primary support and durable decision record | The P8-T13 contract PR conversation in Starck43/social-media-ai | One redacted comment per case; link follow-up engineering PRs. Signing the contract pins this exact conversation URL. |
| Urgent manual owner ping | The existing owner–agent Notion dialogue used to assign P8-T13 | Link the redacted PR case; no promise of automated notifications, paging or message delivery. |
| Fix executor | The agent/developer explicitly assigned by s_shabalin in that case | An assignment, not standing production/DB/provider authority. |
| Final acceptance / pilot suspend-or-resume choice | s_shabalin | A review result or merge is not this choice. |

No new external support service, webhook, bot, analytics collector or recipient
is introduced. Use existing GitHub/Notion access controls. Treat repository PR
comments as potentially public: keep raw workspace/customer details, customer
content and private receipt snapshots in the owner's existing private workspace.
Publish only redacted case references, safe categories, timings and decisions.
If the primary channel is inaccessible, record locally in the existing private
workspace, use the existing dialogue when available, and backfill the PR later;
channel unavailability is a measurement gap, not an acknowledged incident.

### Case intake and response targets (proposed, not agreed SLA)

Support latency starts at the first recorded observation/report, **not** the
receipt's creation time, bell `latest_at`, a successful ping or a hypothetical
notification time. An acknowledgement is an explicit owner comment naming the
case and next action; a read marker or agent reply is not owner acknowledgement.

| Priority | Observable trigger | Owner acknowledgement target | Triage / next update target |
| --- | --- | --- | --- |
| P1 — safety / broad loss | Observed `unconfirmed`/`quarantined` outcome needing replay review; suspected privacy/cross-workspace effect; or enabled runtime non-fresh on two observations at least 60 seconds apart with due pilot work | 30 service minutes from first report | Initial safe action/decision within 60 service minutes; update each 60 service minutes while unresolved. Do not delay a safety hold to wait for the second runtime observation. |
| P2 — degraded first value | One retained pilot run exceeds the latency target; partial/blocked/failed delivery evidence; runtime measurement becomes unavailable without confirmed broad loss | 4 service hours | Triage within 1 service day; update each service day. |
| P3 — usefulness / guidance | Non-urgent usefulness shortfall, supported-scope question or contract improvement | 1 service day | Decision or next action within 3 service days; update on that cadence. |

A service day is nine service hours; elapsed service time is the intersection
of the interval with the hours above. Record both UTC timestamps and local
service-time calculation. Escalate by manually pinging s_shabalin with the case
link when the acknowledgement/update target expires. With the same person as
primary and escalation owner, record **unacknowledged/overdue**, not a fictional
handoff; no guaranteed resolution time is promised. The owner may nominate a
backup only through an explicit contract amendment.

At intake: record safe case id, priority, observation time, signal type,
permission scope, affected cohort reference, next update deadline and owner.
Before closure: record evidence of the particular problem's resolution and the
owner's decision. A later successful but unrelated job does not close a case.

## 3. Measurement protocol and evidence limits

Use a **weekly, workspace-scoped** review (Monday 00:00 through next Monday
00:00 Europe/Kirov); collect observations during the week, not by reconstructing
missing rows afterward. Store timestamps in UTC; retain the local review window.
Each record carries contract revision, cohort reference, observer, observation
time, permission scope, safe job/run/task reference and known/unknown coverage.
No raw token, provider payload, prompt, destination/chat id or customer content
belongs in the support PR. Use existing authorized views only; this contract
adds neither a privileged export nor an access bypass.

Ordinary terminal Job rows are automatically pruned after **24 hours** based on
`finished_at`. Record permitted receipt metadata during the observation session
and within 12 hours of observing completion; do not rely on a seven-day Job
history. Retained evidence is not permission to change retention or dump raw
results. If a row disappears before evidence capture, report `unavailable` and
coverage loss, never reconstruct a successful result from task `last_status`.
See [job retention](AGENT_TASKS_QUEUE.md#job-history-and-one-time-runs).

All targets below are **proposed v1 thresholds**, not measured baselines or
promises that current code already achieves them. Report numerator, denominator,
missing/excluded counts, sample scope and observation window alongside any rate.
For zero denominators report `no_sample`; insufficient coverage is not PASS.
No automated metric dashboard, feedback persistence or monitoring cadence is
claimed to exist: the authorized operator keeps this manual evidence log.

### M1 — observed runtime freshness

- Prerequisite: explicit operator enablement above. Without it, report
  `not_measurable: runtime_signal_unconfigured`, not 0%, 100% or a code blocker.
- At the first service observation session of each service day, record **15
  scheduled observations at one-minute spacing** (t=0 through t=14 minutes),
  using the already-deployed `/runtimez`. This is a sampled 15-point window,
  not whole-day or 24/7 availability. No new sampler/service is commissioned.
- Good sample: HTTP 200 **and** `state=fresh` and numeric `age_seconds <= 60`.
  Record observation time, state, age, runtime id and scheduler flag only.
  Other HTTP/state results or request failures are non-good observations.
  Missed observations remain in the planned denominator and reduce coverage.
- `fresh_rate = good / planned`; `coverage = received_responses / planned`.
  Proposed target: **fresh_rate >= 99%**, coverage **100%** of scheduled samples,
  with **at least 75 planned samples** in a full five-service-day review.
  Shorter/partial weeks remain descriptive, `insufficient_sample` for the target.
- Any `unconfigured` response makes enabled measurement invalid: report the
  configuration gap and escalate P2. Do not reclassify it as healthy or silently
  remove it. Other missing/invalid/unavailable/starting/stopped/stale states are
  non-good with distinct diagnostic labels, not proof of a particular component
  failure. An enabled profile returning no response is not `unconfigured`.

### M2 — observed time to a terminal run receipt

- Use **all accepted run receipts** in the nominated cohort during the week;
  duplicate launch receipts for the same job id count once. Rejected admissions
  are separate diagnostics, not accepted jobs. Scheduled work not yet represented
  by a job is outside this metric and must not be claimed as timely execution.
- Observer records `t_accept` when an existing launch/admission receipt is seen
  and polls the existing authorized job/task result view at **one-minute**
  spacing for up to **30 minutes**, recording the first terminal receipt.
  For a deferred job start the elapsed clock at `max(t_accept, run_at)` when the
  scheduled time is visible; if it is not visible, classify the latency sample
  as unavailable rather than guess. No new launch is authorized just to measure.
- `observed_terminal_latency = t_terminal_seen - t_clock_start` in seconds.
  This includes observation delay; it is **not** provider round-trip time or
  exact handler duration. Terminal means `done` or `failed`, not successful
  delivery, usable analysis or paid-use acceptance. Record the outcome separately.
- Proposed responsiveness target: **>=95% of all accepted cohort jobs have an
  observed terminal receipt within 15 minutes**, with at least **20 jobs/week**.
  No terminal receipt by 15 minutes counts as a miss, even if it appears later;
  still pending at 30 minutes is right-censored and escalated, never dropped.
  Missing/inaccessible/pruned evidence counts as not demonstrated within target
  and reduces coverage. Do not label a high failure-rate cohort successful merely
  because it terminates quickly; M3 and M4 are separate gates.
- Report `within_15m / accepted`, terminal outcome counts and unavailable counts.
  Report p95 only over known terminal latencies, using nearest rank
  `sorted_latencies[ceil(0.95*n)-1]`; label that subset and its coverage, never
  use its percentile to conceal unfinished jobs. n=0 gives `no_sample`.

Source fields/semantics: [Job](../app/models/scheduling/job.py),
[permission-aware task results](../app/services/task_results.py),
[task run receipts](AGENT_TASKS_PLAN.md#explicit-task-plans).
`AgentTask.last_status=queued` is historical admission evidence, not completion.
Database `started_at`/`finished_at` are not claimed to be exposed by every UI/API;
this manual metric uses receipt observation times, not a new timestamp endpoint.

### M3 — incomplete-delivery attention and honest outcome accounting

- Inspect the **existing personal bell aggregate** at each service opening,
  after each observed pilot digest outcome and before the weekly review. Record
  inspection time, `scope`, four bucket counts, `truncated`, `jobs_included` and
  the permitted redacted evidence references. An unavailable bell is not empty.
- Existing implementation: seven-day window, up to 200 fetched rows **per
  source kind** and up to 20 displayed samples **per kind**. Owner/admin can
  see authorized workspace-wide evidence; manager/viewer see only their own
  initiated digest jobs. A reduced scope is never a whole-workspace denominator.
- Buckets are `undelivered`, `unconfirmed`, `retrying`, `quarantined`. Job and
  DigestRun evidence can describe the same logical event: **do not sum bell
  counts into unique deliveries or compute a delivery success rate from them**.
  Use `run:<id>` / `job:<id>` case keys and correlate only with actual retained
  evidence. No inferred association, automatic retry or implicit resolution.
- For each observed problem entry, proposed target: **100% have a redacted case
  and priority recorded by that inspection's end**; apply P1/P2 response clocks
  above. A receipt with uncertain external effect stays review-required;
  unknown is not sent, and unrelated later success does not erase the case.
- Use existing validated delivery outcomes (`status`, `sent_parts`,
  `total_parts`, `uncertain_parts`, `blocked_parts`, `pending_parts`,
  `rejected_parts`) to report each nominated digest run separately. `sent`
  means the existing ledger's completed send state, not read-by-human or useful.
  Missing/invalid ledger = unknown, never zero parts or successful delivery.
- If `truncated`, authorization is insufficient, `jobs_included=false`, evidence
  is unavailable or references cannot be correlated, label the review **partial**.
  No claim of complete cohort delivery coverage or clean PASS from that view.

Sources: [bell aggregate](../app/services/notifications/delivery_signal.py),
[delivery outcomes](../app/services/digest/delivery_outcomes.py),
[P8-T11 audit](audit/p8-t11.md). This contract consumes existing delivery
semantics; it does not create sends, retries, acknowledgements or recovery rules.

### M4 — owner-rated usefulness tied to existing outcomes

- Machine receipts/counters cannot establish semantic usefulness. At weekly
  review the owner manually rates each analysis/digest outcome (including failed
  or missing outcomes) in the
  nominated cohort, referencing its existing permitted task/job/run outcome and,
  when available, analysis/citation references. No new feedback service/table.
- `useful`: addresses the agreed pilot question, has actionable relevant output
  and traceable evidence, with material coverage/uncertainty stated. `not_useful`:
  irrelevant, unsupported conclusion, missing material evidence or no usable
  output. `not_rated`: owner has not assessed it or cannot access retained output.
  No new useful result from a collect-only run is expected; such runs are outside
  the agreed usefulness cohort and recorded separately before measurement.
- Proposed target: **useful / rated >=80%**, at least **10 rated results/week**,
  and **rated / eligible_results >=90%**. Zero/too few rated results or missing
  cohort coverage gives `no_sample`/`insufficient_sample`, never successful quality.
  Failed analysis/digest outcomes with no usable result count `not_useful`;
  unknown/missing outcomes remain `not_rated` and lower coverage, not silently
  excluded. Keep technical empty/failed/partial outcomes distinct in the report.
- Record the rubric reason as a short redacted category in the PR and keep any
  sensitive examples in the existing private pilot workspace. Changes to the
  rubric or selected cohort apply prospectively with a new signed revision.

### Calculation examples — synthetic, not pilot measurements

- M1: 75 good / 75 planned = 100%; 74/75 = 98.67%, below 99%.
  A successful 15/15 short window is descriptive but below the weekly minimum.
  Unconfigured runtime is `not_measurable`, not any of those rates.
- M2: 19 of 20 accepted jobs observed terminal within 900 seconds = 95%.
  A twentieth pending job is still a denominator member and a target miss;
  it must not disappear from the report. For 20 known latencies comprising
  nineteen 600-second values and one 1200-second value, nearest-rank p95 is
  600 seconds; the 1200-second outlier and outcome remain explicitly reported.
- M3: two job entries plus one run entry are three evidence entries, not proof
  of three unique deliveries. Record each or link only proven correlations.
- M4: 8 useful + 2 not_useful out of 11 eligible = 80% usefulness and 90.91%
  rating coverage, satisfying both proposed rate targets and the 10-rating
  minimum. The same ten ratings out of 12 eligible gives 83.33% coverage and
  cannot pass the coverage gate. Zero ratings is `no_sample`, not 0% quality.

## 4. Review, safety and suspend/resume decisions

At weekly review, publish a redacted summary: signed contract revision,
observation dates/cohort reference, M1–M4 numerators/denominators/coverage and
outcomes, open/overdue cases, limitations and the owner's next action. First
week without minimum samples is a baseline, not proof that targets are met.

Any credible cross-workspace/privacy effect or unconfirmed external outcome
requires immediate conservative review: do not repeat an external action to
make a metric green. Pause a relevant pilot action only through existing,
authorized controls and explicit owner/operator decision. No new global kill
switch, deployment, DB repair, token renewal, runtime restart or provider probe
is authorized by an incident classification or this document. Do not unlink a
runtime owner lock. Respect existing confirmation, permission and delivery gates.

Owner records one explicit weekly decision: continue within the signed scope;
change the draft/targets/cohort prospectively; or pause and name resume evidence.
A metric breach is an investigation trigger, not an automatic replay, service
credit, legal SLA, project acceptance or release authorization. Non-observable
provider/source content freshness remains outside this runtime-presence contract.

## 5. Owner signature and revision control

**Current signature: PENDING.** No approval date, agreeing owner identity or
operational start has been asserted. All owner roles and numbers are proposed.

To approve, s_shabalin posts an explicit comment in the contract PR:

> Approve P8-T13 pilot support contract at `FULL_DOCUMENT_COMMIT_SHA`, including
> the named ownership/channels, service hours, M1–M4 definitions/thresholds and
> runtime enablement prerequisite. Signed by s_shabalin on `ISO_TIMESTAMP`.

The owner fills the real full SHA/time; this template is **not** a signature.
Record the actual comment permalink, exact approved document commit and effective
start in the task handoff/audit. The PR becomes the named primary support channel.
Do not fabricate acceptance from a merge, emoji, assignment or generic permission
to draft. A substantive edit to scope/ownership/channels/targets/measurement after
approval requires a new explicit signature; earlier approval covers only its
pinned revision. Final T13 acceptance remains **written and owner-signed**.
