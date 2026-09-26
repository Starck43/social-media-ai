# Collection and platform credentials

How content gets out of VK/Telegram/MAX and into `ai_analytics`. Two questions
are answered separately: **who authorizes us** (the credential vault) and **how
we read** (one of three collection layers).

## Collection layers

| Layer | Mechanism | Status | Reaches |
| --- | --- | --- | --- |
| **L1 official API** | VK `wall.get`/`wall.getComments` with a service token; Telegram Bot API where the bot is a member/admin | VK done, Telegram push-only (below) | public communities; chats and channels the bot joined |
| **L2 user session** | VK user token (sees what the owner's account sees); Telegram MTProto user session (Telethon) | done | closed communities, walls, historical depth |
| **L3 browser** | headless browser scraping | planned, last resort | everything without an API |

The layer is picked per source (`Source.params["mode"]`), because most sources
only need L1 while a few need L2.

## Credential vault

Secrets live in `tenant_credentials`, encrypted with Fernet under
`CREDENTIALS_KEY`. `app/services/social/credentials.py` is the only reader:

```
resolve_token("vk")            # vault first, then env
resolve_token("vk", required=True)   # raises CredentialMissing instead of None
credential_status()            # {"vk": "vault" | "env" | "missing"} — no secrets
```

Resolution order per platform (first hit wins):

| Platform | Kinds, in order | Env fallback |
| --- | --- | --- |
| `vk` | `user_token`, `service_token` | `VK_USER_ACCESS_TOKEN`, `VK_SERVICE_ACCESS_TOKEN` |
| `telegram` | `bot_token` | `TELEGRAM_BOT_TOKEN` |
| `max` | `bot_token` | `MAX_BOT_TOKEN` |

Rules: a vault row always beats the env variable; an expired row (`expires_at`
in the past) is skipped with a warning; a decryption failure is logged and
treated as absent; the plaintext is never logged, never put into an LLM prompt
and never echoed into a chat. Agent tools see `credential_status()` only.

### Admin

`TenantCredentialAdmin` (sqladmin → «Креды платформ»): the form takes the secret
in plaintext and encrypts it before saving (`gAAAAA...` values are left alone, so
re-saving without editing does not double-encrypt).

### CLI

```bash
python -m cli.main credentials set vk user_token --tenant owner     # prompts for the secret (hidden)
python -m cli.main credentials list [--tenant owner]
python -m cli.main credentials test vk                              # resolve + ping the platform
python -m cli.main credentials disable vk user_token
python -m cli.main credentials login telegram                       # MTProto L2: code + optional 2FA
```

`test` performs a real-but-cheap call (`account.getProfileInfo` for VK, `getMe`
for Telegram, `/me` for MAX) and reports `vault|env -> ok|error`. It also prints
an L2 line (`telegram L2 (MTProto): ok|error|not configured`), which pings the
authorized user session.

`login telegram` walks the Telegram authorization flow (phone, code, optional
2FA) and stores `api_id`/`api_hash`/`session` in the vault — the session string
is a full-access credential and is **never** printed. Re-running logs in again
and replaces the stored set.

## VK

`VKClient.collect_data` resolves the token once per run (`_token()`), then the
shared `BaseClient` paginates. Incremental collection uses `Source.last_checked`
as `start_time`; `params["force_refresh"]` + `params["cli_dates"]` override it.
Without any credential the client logs one error and returns no items — no
request is spent on a call that would answer error 5.

## Telegram

The Bot API **cannot read a channel's or a chat's history**: a bot only receives
what arrives through `getUpdates` while it is running. There is no backfill and
no way around it from L1 — historical depth needs L2 (MTProto user session).

So Telegram collection is push-based and lives in the listener path:

```
channels/listener.py  ->  services/monitoring/ingest.py  ->  AIAnalyzer.base_analyze_content
```

- The listener already consumes `getUpdates`, so a second poller would fight it
  over the offset — instead every inbound is offered to `ingest_channel_post()`.
- Ingest resolves the owning workspace itself: a channel post carries no chat
  binding, so the source is looked up under `bypass` and the analysis then runs
  inside `tenant_scope(source.tenant_id)` (same shape as the job dispatcher).
- Ingest is synchronous with the poll iteration on purpose: the `getUpdates`
  offset only advances after the iteration completes, which gives at-least-once
  delivery.
- `Source.last_item_id` is the high-water mark per source: a replayed update
  (restart, network retry) is skipped instead of being analyzed twice.
- Only posts of chats with a registered source (platform `telegram`,
  `external_id` = chat id, `is_active`) are analyzed; every other update is
  ignored, so being an admin of unrelated channels costs nothing.
- Requirement for the owner: the bot must be an admin of the channel, otherwise
  it never receives `channel_post` updates.

### L2: MTProto user session (Telethon)

For historical depth and channels the bot cannot join, set the source's
`Source.params["mode"] = "user"`. The collect job then pulls messages over
MTProto with an authorized user session instead of waiting for push:

- Three vault kinds make an L2 login: `api_id`, `api_hash` (from
  my.telegram.org) and `session` (a Telethon `StringSession`). Set them with
  `credentials login telegram` — the CLI walks the code/2FA flow and stores all
  three; the session string is never shown.
- Collection is incremental by message id: `iter_messages` starts at
  `Source.last_item_id` (the same watermark the push path advances), so a
  repeat run costs nothing until new posts arrive. Set `force_refresh` +
  `cli_dates.start_date` for a one-off backfill into the past.
- `external_id` of a pulled item is built from the source's own Bot API chat id,
  so a post that first arrived by push and is later pulled by L2 hashes to the
  same `content_hash` — `app/services/ai/dedup.py` makes sure overlap is never
  paid for twice.
- A missing, expired or revoked session is an error to log, not to raise: the
  job reports the source as failed and the rest of the run continues. Telethon
  is imported lazily, so the app boots even when it is not installed.

## Source parameters

`Source.params` holds collection settings (a JSON column, no schema migration):

| Key | Meaning |
| --- | --- |
| `mode` | `api` (L1, default) \| `user` (L2, MTProto) \| `browser` (L3, planned) |
| `incremental_mode` | `true` → only fetch what is newer than `last_checked` |
| `collection.count` / `limit` | page size / max items per run |
| `collection.filter` | VK wall filter (`all`, `owner`, `others`, `suggests`) |
| `force_refresh`, `cli_dates` | one-off/CLI date overrides used by the VK client |

## Next steps (later milestones)

1. **L3 browser fallback** — Playwright client for sources without an API.
2. **Rate limits** — honour `Platform.rate_limit_remaining`/reset with backoff
   per platform instead of per request.

## Deduplication

A repeated item is never analyzed (and paid for) twice —
`app/services/ai/dedup.py`, wired into `AIAnalyzer.base_analyze_content`:

- `item_hash(item)` — sha256 over platform + external_id + text only;
  engagement counters and dates are ignored, so refetched items still match.
- `batch_hash(items)` — sha256 of the sorted item hashes (order-independent).
- Batch level: `ai_analytics.content_hash` (migration `0049`); an identical
  batch returns the existing analysis without touching an LLM.
- Item level: every analyzed item's hash goes to
  `summary_data["content_hashes"]` (merged, never replaced, when several
  batches land on the same daily row); overlapping batches only pay for the
  genuinely new items.
- Fail-open: any lookup/matching error analyzes the full batch — a dedup bug
  degrades to "pay twice", never to "analysis silently skipped".
- Lookback: last 30 days, capped at 200 rows per source.

## Tests

`tests/test_credentials.py` — vault/env precedence, kind preference, expiry,
`CredentialMissing`, and that a secret never reaches the logs.
`tests/test_telegram_ingest.py` — content-item contract, watermark replay,
newer posts, unknown chats and private messages (analyzer stubbed, no network).
`tests/test_telegram_mtproto.py` — L1/L2 mode switching, missing/expired
sessions, entity resolution, cross-layer `external_id` for dedup, watermark
advance, and tenant fail-closure (Telethon network client faked).
`tests/test_dedup.py` — hash stability/order-independence, batch and item
filtering, fail-open, and analyzer integration (replay costs one LLM call;
partial overlap pays only for new items).

