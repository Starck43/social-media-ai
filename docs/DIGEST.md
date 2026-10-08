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
   No LLM call happens here — the brief is pure SQL + JSONB aggregation
   over `summary_data`.

4. **Hybrid step 2 — the narrative.** `_summarize()` sends that brief to the
   LLM for a 2–4 sentence Russian summary. Model choice: `AGENT_MODEL` by name
   if set, otherwise the first active text-capable model. A broken LLM never
   breaks the digest — the summary is simply omitted. The daily cost cap is
   checked *before* the call: past `AGENT_DAILY_COST_LIMIT` (or
   `tenant.daily_cost_limit`) the summary is skipped with the digest still
   rendered. A successful call prices its usage from `llm_models` tariffs and
   stores it on `digest_runs.llm_cost`, which is part of the cap's spend metric
   (`tenancy.resolver.daily_cost_today()`).

5. `render.py` renders HTML-safe text (the brief's Markdown is converted to the
   HTML subset the channels accept) with a length budget per channel.

6. **Hybrid step 3 — delivery.** `broadcast_digest()` sends to every target and
   records the result. Targets are additive:
   - env-configured channels (`TELEGRAM_DIGEST_CHANNEL_ID` / `MAX_CHANNEL_ID`);
   - every tenant channel with `is_digest_target=True` in the workspace the job
     runs for (resolved from the ambient tenant scope).

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
| `sentiment`| `false`           | Distribution by sentiment score                                        |
| `content_type` | `false`       | Distribution by media type (text, image, video)                        |
| `intent`   | `false`           | Distribution by user intent                                            |
| `topic_chains` | `false`       | Thematic chains across the period (top chains by entry count)          |
| `topic_chains` | `true`         | Per-day chain activity dynamics                                        |

The digest handler reads `task.payload.group_by` (default `"themes"`) and
`task.payload.time_breakdown` (default `false`). The same `ai_analytics` rows
are grouped differently depending on these parameters — no data duplication.

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
- `agent_task_id IS NULL` rows (manual `digest send-now`) never collide, so
  manual runs are unlimited and each is kept as separate history.

## Delivery failures are retryable

`build_and_publish` raises `DigestDeliveryError` when the digest was built but
at least one channel refused it. The job queue then retries with backoff. A
config problem (no channel configured) returns `status="skipped"` instead:
retrying cannot fix missing credentials.

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
| `TELEGRAM_DIGEST_CHANNEL_ID`   | Legacy env target for digests (`@name` or numeric id) |
| `MAX_BOT_TOKEN`                | MAX bot access token                             |
| `MAX_CHANNEL_ID`               | Legacy env target for digests                    |
| `AGENT_MODEL`                  | Preferred LLM model name for the summary         |

Delivery targets are additive: the env vars above (legacy, global) and the
tenant channels flagged `is_digest_target=True` in the digest job's workspace
(see the web console's workspace settings). Only channels with both token and
target set receive anything; `broadcast_digest()` returns `{}` when none is
configured.

## Manual runs
```bash
python -m cli.main digest send-now day
python -m cli.main digest send-now week
```

Manual runs are not idempotent by design — useful for testing channel setup
without waiting for a schedule.
