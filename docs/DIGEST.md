# Digest Delivery

A digest is a hybrid report for a period (`day` or `week`): an algorithmic
brief aggregates the stored analytics, and the LLM turns that brief into a short
narrative. It is rendered to text and pushed to the configured channels — the
same payload for every channel; channels differ only in transport rules.

## Pipeline

`app/services/digest/builder.py::build_and_publish(period, agent_task_id, source_ids, group_by, time_breakdown, scenario_id)`

1. `period_bounds(period)` → inclusive `(start, end)` dates: the last N days
   ending today (N = 1 for `day`, N = 7 for `week`).

2. **Idempotency short-circuit:** if a `digest_runs` row for the same
   `(agent_task_id, period_start, period_end)` already has status `sent`, return
   `{"status": "skipped", "reason": "already_sent"}`.

3. **Hybrid step 1 — the algorithm.** `aggregate()` reads
   `ReportAggregator.generate_digest_brief(period, source_ids, group_by, time_breakdown, scenario_id)`:
   it groups the period's `ai_analytics` rows by the `group_by` axis (and
   optionally `time_breakdown`) and appends a section for each `analysis_types`
   the scenario enables.
   The five core types (`sentiment`, `keywords`, `topics`, `trends`,
   `engagement`) are covered by the standard aggregations; the eight extended
   types use their specialized ones (see the table below).
   No LLM call happens here. Existing managers load ORM rows; Python reads
   stored `summary_data` and groups them. This is not a SQL/JSONB-only pipeline.

   **Current scope/date limitations:** the brief receives source/scenario filters,
   but the top-level stats/sentiment/topics/content mix/engagement/LLM summaries
   in `aggregate()` do not. The shared look-back query uses `today - days` with
   no upper date or DAILY-only predicate, while the displayed inclusive window
   starts at `today - (days - 1)`. Weekly/monthly aggregation also writes
   workspace-wide rollups. These are open correctness/refactoring findings, not
   cross-workspace leakage or repaired behavior.

4. **Hybrid step 2 — the narrative.** `_summarize()` sends that brief to the
   LLM for a 2–4 sentence Russian summary. Model choice:
   `LLMModel.objects.resolve_default_model("text", strategy="quality")`; this
   path does not consult `AGENT_MODEL`. A broken LLM never
   breaks the digest — the summary is simply omitted. The daily cost cap is
   checked *before* the call: past `AGENT_DAILY_COST_LIMIT` (or
   `tenant.daily_cost_limit`) the summary is skipped with the digest still
   rendered. Returned envelopes are journaled before output validation (PR #163);
   the legacy `known_cost_usd` projection is stored on `digest_runs.llm_cost`,
   which is part of the cap's spend metric
   (`tenancy.resolver.daily_cost_today()`).
   The owner-decided target pricing is three-state: provider-reported cost takes
   precedence; an explicit zero from the provider or tariff table is FREE; a
   known non-zero table rate is the reservation basis; no report and no tariff
   is UNDEFINED (NULL, never free/zero, tracked separately). This policy does
   not claim the current projection adapter or atomic admission is implemented.

5. `render.py` renders HTML-safe text (the brief's Markdown is converted to the
   HTML subset the channels accept) with a length budget per channel.

6. **Hybrid step 3 — delivery.** `broadcast_digest()` sends only to active
   `tenant_channels` bindings with `is_digest_target=True` belonging to the one
   active workspace resolved for this run. `channel_filter` applies to every
   bound target. Exact normalized `(transport, chat_id)` destinations are sent
   once per broadcast call.
   - Deprecated env destinations (`TELEGRAM_DIGEST_CHANNEL_ID` / `MAX_CHANNEL_ID`)
     are ignored in every workspace, including bootstrap. `TELEGRAM_ADMIN_CHAT_ID`
     is not used by digest delivery. Configuring env alone cannot authorize a send.
   - Non-operator callers cannot supply a foreign `tenant_id` or run unscoped.
     Trusted unscoped operator runs resolve `DEFAULT_TENANT_SLUG`; the builder
     enters that workspace with bypass disabled **before** aggregation, run
     bookkeeping, LLM summarization and delivery.

## Grouping axes and time slices

The algorithmic brief groups data by a **grouping axis** (`group_by`) and an
optional **time breakdown** (`time_breakdown`). These are two independent
parameters passed through the digest task payload:

| `group_by` | `time_breakdown` | Brief groups by                                                        |
| ---------- | ---------------- | ---------------------------------------------------------------------- |
| `themes`   | `false` (default) | Top themes over the period (count of topic mentions)                  |
| `themes`   | `true`            | Per-day dynamics of themes: "Mon: vacation, Tue: prices, Wed: contest" |
| `sources`  | `false`           | Per source (analyses and posts per source)                             |
| `sources`  | `true`            | Per-source activity broken down by date                                |
| `entities` | `false`           | Top mentioned entities (brands, persons, organizations)                |
| `entities` | `true`            | Chronology of mentions: "01.10 — Coca-Cola, 03.10 — Pepsi"            |
| `intent`   | `false`           | Distribution by user intent                                            |
| `topic_chains` | `false`       | Thematic chains across the period (top chains by entry count)          |
| `topic_chains` | `true`         | Per-day chain activity dynamics                                        |

The digest handler reads `task.payload.group_by` (default `"themes"`) and
`task.payload.time_breakdown` (default `false`). The same `ai_analytics` rows
are grouped differently depending on these parameters — no data duplication.

**Mention grouping (По упоминаниям):** The user-facing heading is «По упоминаниям»;
the technical axis value remains `entities`.

**Entity filtering:** `entities` axis supports an optional `entity_type` filter
(`"person"`, `"brand"`, `"org"`). The old `monitored_users` mode is now
`group_by="entities", entity_type="person"`.

**Example task payload:**
```json
{
  "period": "week",
  "group_by": "entities",
  "time_breakdown": true
}
```

## Specialized sections

For each `analysis_types` the scenario enables, the brief adds one section read
by a dedicated aggregation in `app/services/ai/reporting.py` (all keyed off the
current `JSONSchemaBuilder` contract, with aliases for pre-contract rows —
`tests/test_reporting_contract.py` pins the pairing).

The five core types are handled by the standard aggregations
(`get_sentiment_trends`, `get_top_topics`, `get_engagement_metrics`, etc.).
The eight extended types use specialized sections:

| `analysis_type`   | Aggregation                          |
| ----------------- | ------------------------------------ |
| `toxicity`        | `get_toxicity_summary()`             |
| `hashtag_analysis`| `get_top_hashtags()`                 |
| `brand_mentions`  | `get_brand_mention_stats()`          |
| `viral_detection` | `get_viral_content()`                |
| `influencer`      | `get_influencer_impact()`            |
| `competitor`      | `get_competitor_activity()`          |
| `intent`          | `get_intent_distribution()`          |
| `demographics`    | `get_demographics_breakdown()`       |

## Dynamics against the previous period

The sections above each describe the period in isolation: "12 toxic posts" says
nothing about whether that is worse than usual, which is the part a reader acts
on. So the brief closes with a **Динамика** block comparing the window to the one
before it — the sentiment movement and the toxicity spike or drop.

The previous window is read by explicit date range rather than by re-using the
reporting methods, which take a look-back `days` instead. Re-using them would
compare "the last 7 days" against "the 7 days before now" — an overlapping pair
that dilutes every delta across the shared days and reports no change in a period
that moved a lot. `tests/test_digest_brief.py::test_dynamics_never_compare_overlapping_windows`
pins this: it fails against the overlapping variant.

Two thresholds keep the block from crying wolf. A sentiment move under 0.02 and
a toxicity move under 1 percentage point are reported as "no change" — a brief
that announces movement every day trains the reader to ignore it. And when the
previous window has no rows at all, the block is omitted rather than comparing
against an empty baseline.

## Idempotency

`digest_runs` has a unique index on
`(agent_task_id, period_start, period_end)`, so a scheduled digest is sent once
per period. Two consequences worth knowing:

- Retries reuse the existing row (`DigestRunManager.start_run()`) instead of
  inserting a duplicate — a re-attempt cannot violate the unique index.
- A scheduled attempt must claim that row before it builds or sends: the reset
  to `pending` is conditional on the status and `updated_at` version it
  observed (every claim bumps `updated_at`), and a concurrent insert loses on
  the unique index. A row that is already `pending` belongs to another
  attempt in flight — or one that stopped mid-flight with unknown delivery — so
  the attempt returns `{"status": "failed", "error":
  "digest_run_in_progress_requires_review"}` without summary cost or delivery,
  and the queue does not retry it. Only an explicit `force_refresh` run takes
  such a row over, and only once it has not been written for 30 minutes
  (`FORCED_TAKEOVER_AFTER`, the job queue's stale-lease window); a forced run
  against a younger `pending` row returns the same failure. Of two forced runs
  at once, at most one builds and sends. A takeover is still an operator
  decision: the stopped attempt's delivery stays unknown.
- `agent_task_id IS NULL` rows (manual `digest send-now`) never collide, so
  manual runs are unlimited and each is kept as separate history.

## Delivery failures stop for review

`build_and_publish` raises `DigestDeliveryError` when the digest was built but
no recipient received any part of it. The dispatcher does not treat that
exception as a known, retryable delivery failure: the job is stored as terminal
`failed` with the "Queue outcome unconfirmed; inspect before retry." marker and
is not retried automatically, because this path cannot tell a definitive
rejection from a lost acknowledgement. A new attempt for the same
`(agent_task_id, period)` — the next scheduled run or an operator's job — reuses
the run row and delivers again. A config problem (no channel configured)
returns `status="skipped"` instead: retrying cannot fix missing credentials.

Partial delivery is terminal instead. When at least one recipient received the
digest (or at least one chunk of a split message) and another recipient failed,
the run is stored as `failed` with an error starting with
`partial_delivery_requires_review` and the handler returns
`{"status": "failed", "error": "partial_delivery_requires_review"}`. The queue
does not retry it, because a retry rebuilds the digest and resends it to every
recipient. Later scheduled attempts for the same `(agent_task_id, period)` stop
before aggregation, summary cost and delivery with the same failure. Only an
explicit `force_refresh` run resends; that is an operator decision and can
duplicate the digest for recipients that already received it. Manual runs keep
separate history and are not blocked.

This legacy path still cannot tell a definitive rejection from a lost
acknowledgement when no recipient succeeded, so the next scheduled run or an
operator re-attempt can repeat an external delivery. Per-part receipts and
uncertain-send review are provided by the disabled-by-default checkpoint
delivery path.

### Lease loss mid-run

If the worker loses its job lease while a legacy digest is being built or sent
(heartbeat renewal fails, or the stale-lease reaper takes the row), the handler is
cancelled and the job is stored as terminal `failed` with the unconfirmed-outcome
marker; nothing is requeued. The run row stays `pending` with no `message_id`,
because delivery is unknown. Scheduled re-attempts for that `(agent_task_id,
period)` then stop with `digest_run_in_progress_requires_review` before summary
cost or delivery, and so does a forced resend within `FORCED_TAKEOVER_AFTER`
(30 minutes); after that an explicit `force_refresh` run may take the row over.

The review codes `digest_run_in_progress_requires_review` and
`partial_delivery_requires_review` are kept verbatim as the job error, so the
operator sees why a digest job stopped on `/app/jobs`; other returned failure
text is still stored as `handler_returned_failure`.

## Channel rules

|                | Telegram                             | MAX                                    |
| -------------- | ------------------------------------ | -------------------------------------- |
| API base       | `api.telegram.org`                   | `platform-api2.max.ru`                 |
| Auth           | token in path `/bot<token>/`         | `Authorization: <access_token>` header |
| Target         | `chat_id` in JSON body               | `?chat_id=` query param                |
| Text limit     | 4096                                 | 4000                                   |
| Rate limit     | 429 with `retry_after`               | ~2 messages/sec per chat               |
| Inbound¹       | `getUpdates` + `offset`              | `GET /updates` + `marker`              |

¹ Inbound refers to the agent chat (long-polling for user messages), not to
digest delivery. Digests are sent via `sendMessage` / `POST /messages`.

Long text is split on paragraph boundaries with a fallback to a hard cut at the
limit; `parse_mode`/`format` is sent only with the first chunk, because
splitting mid-markup would make the remainder invalid.

Channels are long-polling clients. No public URL, domain or TLS certificate
is required on the VPS. MAX uses the current `platform-api2.max.ru` host and
requires the Russian Ministry of Digital Development root certificate in the
trust store if the OS does not ship it.

## Configuration

| Variable                       | Purpose                                          |
| ------------------------------ | ------------------------------------------------ |
| `TELEGRAM_BOT_TOKEN`           | Bot token (also used for the agent chat)         |
| `TELEGRAM_DIGEST_CHANNEL_ID`   | Deprecated; ignored by digest delivery (use workspace bindings) |
| `MAX_BOT_TOKEN`                | MAX bot access token                             |
| `MAX_CHANNEL_ID`               | Deprecated; ignored by digest delivery (use workspace bindings) |
| `AGENT_MODEL`                  | Preferred LLM model name for the summary         |

Recipient authorization comes from active, digest-enabled workspace bindings,
not from env. Result keys are consistently `transport:chat_id`, including
bootstrap; consumers of the old `telegram` / `max` keys must update.
Missing transport credentials produce a failed result for that owned target;
no eligible bindings return `{}`. Missing/inactive workspace or an unauthorized
scope override raises `TenantContextError` before sending.

**Compatibility action:** register the intended recipient through the existing
channel-binding/operator workflow and enable its digest flag in workspace
settings. No binding is created automatically and no database migration is
required. Env-only installations must perform this setup before delivery works.
Identifiers are trimmed and `@usernames` compared case-insensitively; a numeric
chat ID and a username alias are not resolved as equivalent without platform
identity verification. Prefer one stable identifier per destination.

This fixes routing and within-call duplicate recipients. Partial delivery stops
for review as described above; concurrent-send idempotency remains separate work,
and an ambiguous transport timeout with no successful recipient can still cause a
repeated external delivery.

## Manual runs
```bash
python -m cli.main digest send-now day
python -m cli.main digest send-now week
```

Manual runs are not idempotent by design — useful for testing channel setup
without waiting for a schedule. An unscoped operator `digest send-now` uses only
the bootstrap workspace, not a cross-workspace aggregate. Explicitly scoped
runs use their selected workspace; the entire build runs without tenant bypass.

Legacy task payloads with group_by=sentiment or content_type fall back to themes.
Sentiment distribution and content mix remain metrics, not grouping axes.


### Narrative versus report preview

Digest narrative resolves the active configured quality/default text model via
`resolve_default_model("text", strategy="quality")`. Daily cost caps are checked
before resolution. Report preview tools use zero-LLM compact aggregates instead
of invoking digest aggregation or weekly rollup writes; the existing agent chat
cycle narrates with cost-efficient routing. Publication still needs confirmation.

### Interactive manual-send authorization

The web manual-send action and the agent `digest_send_now` tool both require
`digestrun.create`: sending creates a new run rather than editing an existing
one. `digestrun.view` or `digestrun.update` alone cannot authorize a send. The
agent still requires an actor-bound, single-use confirmation and rechecks the
current right before dispatch. Pending confirmations recorded under the old
`digestrun.update` tool contract are rejected; request and confirm the action
again. The read-only `report_period` tool retains `digestrun.view`. This does
not change scheduled/operator sends, channel scope, delivery safeguards or
external-write activation policy.

PR #176's current mock results, integration limits and local follow-up are
tracked in [manual-send acceptance](validation/pr176-digest-send-acceptance.md).
