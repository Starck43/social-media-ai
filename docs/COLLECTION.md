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

Two sources, one reader (`app/services/social/credentials.py`):

- **personal vault** — `user_credentials`, encrypted with Fernet under
  `CREDENTIALS_KEY`, keyed by `users.id` and shared across the workspaces that
  user belongs to. Holds the L2 (user) secrets: VK `user_token` and the Telegram
  MTProto parts (`api_id`, `api_hash`, `session`). Read only when the caller
  passes an `owner_user_id`.
- **environment** — application/infrastructure config, one-per-deployment:
  `vk/app_id`, `vk/client_secret`, `vk/service_token` (service/L1 token),
  `telegram/bot_token`, `max/bot_token` (the shared bots) and the Telegram
  MTProto env fallback. Always the last resort, so an unconfigured install
  still boots.

```
resolve_token("vk", owner_user_id=uid)   # personal vault first, then env
resolve_token("vk", required=True)       # raises CredentialMissing instead of None
credential_status(owner_user_id=uid)     # {"vk": "user-vault" | "env" | "missing"} — no secrets
```

Resolution order per platform (first hit wins):

| Platform | Kinds, in order | Env fallback |
| --- | --- | --- |
| `vk` | `user_token`, `service_token` | `VK_APP_ID` + `VK_CLIENT_ACCESS_KEY` (OAuth app), `VK_SERVICE_KEY` (service/L1) |
| `telegram` | `bot_token` | `TELEGRAM_BOT_TOKEN`; L2 parts `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` / `TELEGRAM_SESSION` |
| `max` | `bot_token` | `MAX_BOT_TOKEN` |

`vk/user_token` is a **personal** secret: it is consulted only when the caller
supplies an owner user. Collection takes that owner from
`app.services.social.owner.resolve_source_owner(source)` — an explicit
`Source.params["token_owner"]` (accepted only when the user is an active member
of the source's workspace) or, by default, the workspace owner. The refresh
token is stored in the `meta` JSON of the `vk/user_token` row (see
[VK L2 via OAuth](#vk-l2-via-oauth-pkce)).

Rules: a vault row always beats the env variable; an expired row (`expires_at`
in the past) is skipped with a warning — except a **VK user token**, which is
transparently refreshed from its stored `refresh_token` before it is skipped
(so L2 collection never fails just because the token aged out); a decryption
failure is logged and treated as absent; the plaintext is never logged, never
put into an LLM prompt and never echoed into a chat. Agent tools see
`credential_status()` only.

### Admin

`UserCredentialAdmin` (sqladmin → «Личные креды»): the form takes the secret in
plaintext and encrypts it before saving (`gAAAAA...` values are left alone, so
re-saving without editing does not double-encrypt). Application secrets
(app_id, bot tokens) are **not** in the DB — set them in `.env`.

### CLI

```bash
python -m cli.main credentials list --user <id> [--tenant owner]     # personal vault rows
python -m cli.main credentials test --user <id>                      # resolve + ping the platform
python -m cli.main credentials disable vk user_token --user <id>
python -m cli.main credentials login telegram --user <id>            # MTProto L2: code + optional 2FA
python -m cli.main credentials oauth vk --user <id> [--tenant owner] # VK L2 via PKCE OAuth (below)
```

`test` performs a real-but-cheap call (`account.getProfileInfo` for VK, `getMe`
for Telegram, `/me` for MAX) and reports `user-vault|env -> ok|error`. It also prints
an L2 line (`telegram L2 (MTProto): ok|error|not configured`), which pings the
authorized user session.

`login telegram` walks the Telegram authorization flow (phone, code, optional
2FA) and stores `api_id`/`api_hash`/`session` in the owner user's personal vault
— the session string is a full-access credential and is **never** printed.
Re-running logs in again and replaces the stored set.

## VK

`VKClient.collect_data` resolves the token once per run (`_token()`), then the
shared `BaseClient` paginates. Incremental collection uses `Source.last_checked`
as `start_time`; `params["force_refresh"]` + `params["cli_dates"]` override it.
Without any credential the client logs one error and returns no items — no
request is spent on a call that would answer error 5.

Collection layer per `Source.params["mode"]`:

| Mode | Behaviour |
| --- | --- |
| `api` (L1) | service token only; a layer-1 error is re-raised. |
| `user` (L2) | user token only. |
| `auto` (default) | try L1 (service token) first; if VK answers with a layer-1 error (codes **15** hidden wall, **5** auth failed) and a user token is configured, retry with L2. If L2 yields nothing, the original L1 error is re-raised so the source is recorded as failed instead of silently returning empty. |

A service token reads only public communities; a user token sees what the
owning account sees (closed communities the account joined, walls it can view).
A wall hidden even from the account itself is not reachable by any token —
layer 2 is the ceiling, not a way around VK privacy.

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

- Three personal-vault kinds make an L2 login: `api_id`, `api_hash` (from
  my.telegram.org) and `session` (a Telethon `StringSession`). Set them with
  `credentials login telegram --user <id>` — the CLI walks the code/2FA flow and
  stores all three for that web user; the session string is never shown. At
  collection the owner is resolved per source (see [Credential vault](#credential-vault)).
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

Layer selection per `Source.params["mode"]`:

| Mode | Behaviour |
| --- | --- |
| `api` (L1) | push-based only; pull returns no items. |
| `user` (L2) | MTProto pull. |
| `auto` (default) | L2 pull whenever an MTProto session is configured, otherwise fall back to L1 (push). |

## VK L2 via OAuth (PKCE)

The VK L2 (user) token is obtained with OAuth 2.0 + PKCE (RFC 7636, S256) in
`app/services/social/vk_oauth.py`, instead of pasting a long-lived token by hand.
The exchange is browser-driven: **you** authorize the app in VK once; the app
stores the access token plus a `refresh_token` in the vault and silently refreshes
it afterwards.

Setup (one-time, deployment-wide app; per-user authorize):

```bash
# App credentials are env config (one VK app per deployment):
#   VK_APP_ID, VK_CLIENT_ACCESS_KEY in .env
python -m cli.main credentials oauth vk --user <id> [--tenant owner]   # runs the flow
```

`credentials oauth vk` builds the authorize URL, opens it in the browser, and
waits (up to `--wait` seconds, default 180) for the callback to write the token
to the **personal vault of `--user`** (default: the workspace owner). On VK's
side the user consents and VK redirects to the **public**
callback `VK_REDIRECT_URI` (default `http://localhost/api/v1/social/callback`,
`GET /api/v1/social/callback`).

Why the callback is public: VK redirects the user's browser there, so it cannot
require our bearer token. Security comes from a **signed `state`** — the state is
`"{tenant_id}.{user_id}.{verifier}.{hmac}"` over `SECRET_KEY`, so the callback recovers the
workspace and the PKCE verifier without any server-side session, and a forged or
tampered state is rejected before any code is exchanged.

Auto-refresh: VK access tokens age out, so when `resolve_token("vk", ...)` meets
an expired `vk/user_token` it reads `meta.refresh_token` and calls VK's
`access_token?grant_type=refresh_token`, then rewrites the vault row (access +
rotated refresh + new `expires_at`). The L2 collector (`VKClient`,
`Source.params["mode"] = "user"` or `auto`) never sees a stale token. A missing
`app_id`/`client_secret` or refresh token just leaves the flow unchanged — the
row is skipped with a warning, as before.

## Source parameters

`Source.params` holds collection settings (a JSON column, no schema migration):

| Key | Meaning |
| --- | --- |
| `mode` | `api` (L1, default) \| `user` (L2, MTProto) \| `auto` (L1 with automatic L2 fallback) \| `browser` (L3, planned) |
| `incremental_mode` | `true` → only fetch what is newer than `last_checked` |
| `collection.count` / `limit` | page size / max items per run |
| `collection.filter` | VK wall filter (`all`, `owner`, `others`, `suggests`) |
| `force_refresh`, `cli_dates` | one-off/CLI date overrides used by the VK client |

> **`force_refresh` has two flavours.** `collect --force-refresh` re-fetches the
> full window from the API but analysis stays deduped (tokens saved). A
> `task collect --force-refresh` additionally re-analyzes the whole window
> (bypasses dedup) and overwrites `ai_analytics` rows by `(source, date)` — see
> `docs/CLI.md`.

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

