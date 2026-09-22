# Digest Delivery

A digest is an aggregated analytics summary for a period (`day` or `week`),
rendered to text and pushed to the configured channels. It is the same payload
for every channel; channels differ only in transport rules.

## Pipeline

`app/services/digest/builder.py::build_and_publish(period, schedule_id)`

1. `period_bounds(period)` → inclusive `(start, end)` dates ending today.
2. Idempotency short-circuit: if a `digest_runs` row for the same
   `(schedule_id, period_start, period_end)` already has status `sent`, return
   `{"status": "skipped", "reason": "already_sent"}`.
3. `aggregate()` extracts sentiment distribution, top topics, content mix,
   engagement and LLM cost from `ReportAggregator`.
4. `_summarize()` asks the LLM for a 2-4 sentence Russian summary. Model choice:
   `AGENT_MODEL` by name if set, otherwise the first active text-capable model.
   A broken LLM never breaks the digest — the summary is simply omitted.
5. `render.py` renders HTML-safe text with a length budget per channel.
6. `broadcast_digest()` sends to each configured target and records the result.

## Idempotency

`digest_runs` has a unique index on
`(schedule_id, period_start, period_end)`, so a scheduled digest is sent **once
per period**. Two consequences worth knowing:

- Retries reuse the existing row (`DigestRunManager.start_run()`) instead of
  inserting a duplicate — a re-attempt cannot violate the unique index.
- `schedule_id IS NULL` rows (manual `digest send-now`) never collide, so manual
  runs are unlimited and each is kept as separate history.

## Delivery failures are retryable

`build_and_publish` raises `DigestDeliveryError` when the digest was built but
at least one channel refused it. The job queue then retries with backoff. A
*config* problem (no channel configured) returns `status="skipped"` instead:
retrying cannot fix missing credentials.

## Channel rules

| | Telegram | MAX |
| --- | --- | --- |
| API base | `api.telegram.org` | `platform-api2.max.ru` |
| Auth | token in path `/bot<token>/` | `Authorization: <access_token>` header |
| Target | `chat_id` in JSON body | `?chat_id=` query param |
| Text limit | 4096 | 4000 |
| Rate limit | 429 with `retry_after` | ~2 messages/sec per chat |
| Inbound | `getUpdates` + offset | `GET /updates` + marker |

Long text is split on paragraph boundaries with a fallback to a hard cut at the
limit; `parse_mode`/`format` is sent only with the first chunk, because
splitting mid-markup would make the remainder invalid.

Channels are **long-polling** clients. No public URL, domain or TLS certificate
is required on the VPS. MAX uses the current `platform-api2.max.ru` host and
requires the Russian Ministry of Digital Development root certificate in the
trust store if the OS does not ship it.

## Configuration

| Variable | Purpose |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Bot token (also used for the agent chat) |
| `TELEGRAM_DIGEST_CHANNEL_ID` | Target channel/chat for digests (`@name` or numeric id) |
| `MAX_BOT_TOKEN` | MAX bot access token |
| `MAX_CHANNEL_ID` | Target MAX chat for digests |
| `AGENT_MODEL` | Preferred LLM model name for the summary |

Only channels with both token and target set receive anything;
`broadcast_digest()` returns `{}` when none is configured.

## Manual runs

```bash
python -m cli.main digest send-now day
python -m cli.main digest send-now week
```

Manual runs are not idempotent by design — useful for testing channel setup
without waiting for a schedule.
