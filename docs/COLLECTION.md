# Collection and platform credentials

How content gets out of VK/Telegram/MAX and into `ai_analytics`. Two questions
are answered separately: **who authorizes us** (the credential vault) and **how
we read** (one of three collection layers).

## Collection layers

| Layer | Mechanism | Status | Reaches |
| --- | --- | --- | --- |
| **L1 official API** | VK `wall.get`/`wall.getComments` with a service token; `newsfeed.search` for a query source; Telegram Bot API where the bot is a member/admin | VK done, Telegram push-only (below) | public communities; chats and channels the bot joined; VK-wide posts when search is enabled on the app |
| **L2 user session** | VK user token (sees what the owner's account sees); Telegram MTProto user session (Telethon) | done | closed communities, walls, historical depth |
| **L3 browser** | headless browser scraping | planned, last resort | everything without an API |

The layer is picked per source (`Source.params["mode"]`), because most sources
only need L1 while a few need L2.

**A source is a wall or a channel, not a person to follow.** Whose items are
eligible for analysis is decided by the task's `tracked_users` filter applied
over what a source returns — see [task selection](design/task-selection.md).
Collection never switches a task's sources for the personal walls of listed
people.

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
renewed from its stored `refresh_token` *before* it expires (`_RENEW_MARGIN`, see
below) and never returned stale (so L2 collection never fails just because the
token aged out); a decryption failure is logged and treated as absent; the
plaintext is never logged, never put into an LLM prompt and never echoed into a
chat. Agent tools see `credential_status()` only.

### Connection states — `app/services/social/connections.py`

One module answers "is this person authorized, and does it matter?", so no page
re-derives "expired?" from a timestamp. `connection_status(platform, user_id)`
returns a `ConnectionStatus` whose `needs_action` means *a human must press a
button*:

| State | Condition | `needs_action` |
| --- | --- | --- |
| `connected` | row present, not expired | no |
| `renewable` | expired, but a `refresh_token` renews it silently | **no** |
| `reauth` | no refresh token, or the last renewal failed | yes |
| `missing` | never connected | yes |
| `unconfigured` | the deployment has no app credentials | no |

The distinction that matters is `renewable` vs `reauth`. VK access tokens live
**1 hour** and are renewed automatically; showing «войдите заново» for that
every hour is what pushed people to log out and re-authorize for no reason. A
renewal failure is recorded as `meta.refresh_failed_at` and treated as `reauth`
for `REFRESH_FAILURE_COOLDOWN` (6 h) — the UI then asks for a re-login instead of
retrying a refresh token that is known to be spent.

Consumers: the navbar (`TenantUIMiddleware` → `connection_alerts`, rendered only
when non-empty), `/app/settings?tab=connections` (one card per platform),
`/app/sources` (a per-row «Доступ» badge, plus a banner per blocked platform when
one is actually blocked on this page), and `sources.py::_readiness` (a missing L2
token is a named reason on the source page). All of them render through the
`connection_badge` macro, so the tone→colour mapping lives in one place.
`source_connection_status(source)` answers for the *owner*, not the caller, since
a source may collect with a teammate's token; `source_connection_statuses()`
answers for a whole page and memoises both the owner lookup and the status, so a
list of sources sharing one token costs two queries rather than two per row.

Two properties templates get for free instead of re-deriving them:
`ConnectionStatus.fix_href` is empty unless the state needs action *and* the
platform can be fixed, so a healthy or self-renewing badge is inert, and it points
at the settings tab for a `manual` platform that has no authorize endpoint at all.

Adding a platform is one `ConnectionSpec` row plus, for OAuth, a branch in
`app/web/connections.py::connection_authorize`.

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

### Credential diagnostic error privacy

The API `credentials/test` error statuses and the shared MTProto `check_session`
error verdicts use a bounded vocabulary: `authorization_error`, `rate_limited`,
`provider_unavailable`, `provider_error`, `timeout`, `connection_error`,
`integration_unavailable`, `invalid_response` or `integration_error`, prefixed
with `error: `. The existing explicit MTProto expired/revoked verdict is retained.
Provider error descriptions, echoed request parameters, exception text and
token-bearing request URLs are never included in these error verdicts.
Only allowlisted numeric provider codes or exception types select a category.

Success identity labels, `missing`, `no_token`, `skipped` and `not_configured`
remain unchanged. MTProto disconnect cleanup remains in `finally`, and task
cancellation propagates rather than becoming an error or successful result.
These changes do not alter authorization, token/session storage, resolution or
revocation. They do not certify all credential logging or other API/CLI errors;
the independent CLI L1 diagnostic path and login/OAuth errors remain separate
privacy review surfaces. The isolated regression script is
`tests/test_credential_diagnostic_privacy.py`; use the PR handoff for its exact
revision and execution evidence. No live credential test is part of verification.

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

### Search sources

`SourceType.SEARCH` is a phrase, not a wall. The source's `params["query"]`
holds the text (with `external_id` as the stable slug) and the collector calls
`newsfeed.search` with `extended=1`, `start_time`/`end_time` and the same
1000-result ceiling VK applies. `params["owner_ids"]` is an optional list of
wall ids (`-123` for a community, `456` for a person): results outside it are
dropped during normalisation, because `newsfeed.search` itself has no owner
filter. The `profiles`/`groups` arrays from the extended response are used to
fill each item's author and name.

VK only serves the first 1000 search results by `offset`; the collection limit
below is the same number, so the two ceilings agree.

### Collection plan (P5-T14; stages A–E implemented)

The current contract stores a search phrase as an ordinary `SEARCH` source.
The accepted target moves collection scope into the task and keeps the source
row only as a technical, task-scoped lane. A task declares content types
(posts/comments/...), mention queries, discovery by city, engagement types and
an explicit limit; `CollectionPlanner` expands that intent into ordered lanes
and calls several platform methods. Selected rows land in `collected_items`;
discovered people/communities land in `collection_candidates` and require
confirmation; comments are a dependent lane on posts; likes/reposts yield
candidates, not text rows. Every lane reports `limit_reached` instead of
silently truncating. Stages A–D delivered the parser/schema, adapters, staging,
selection and comment/candidate/profile lanes; stage E delivered the task
editor, scenario-media boundary, admin surfaces and one shared writer for web,
CLI, API and chat. The runtime resolves each source through the parsed task
intent. The source-level primitives below still apply inside that context. The
full contract is in [collection plan](design/collection-plan.md).

### Walls and dates

`wall.get` does **not** honour `start_time`/`end_time` — VK answers with the
newest page regardless. The lower/upper bound is therefore applied in the
client after each page: items outside `cli_dates` are dropped, and a page that
is entirely older than the window stops pagination. `groups.getById` (not
`utils.resolveScreenName`, which answers error 1051 for this app profile type)
resolves a community screen name.

### Search API availability

`newsfeed.search`, `groups.search`, `users.search` and `database.getCities`
answer `1051 "Method is not available for this profile type"` on deployments
whose VK app type is not allowed to call them. `wall.get`, `users.get` and
`groups.getById` keep working with the same token. This is an application
configuration limit, not something the client can bypass; the agent
`sources_search` tool reports it as an actionable error.

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
  repeat run costs nothing until new posts arrive. A direct CLI `collect
  --force-refresh` can re-fetch a date window, but it does not bypass analysis
  receipts. Set `cli_dates` +
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

Auto-refresh: VK access tokens age out in **1 hour** and cannot be extended, but
each authorization also yields a **refresh token valid for 180 days** that is
renewed silently. `resolve_token("vk", ...)` calls `renew_if_expiring()`, which
renews ~5 minutes *before* the expiry rather than after it — a run that starts at
10:59 would otherwise carry a token that dies mid-request.

Two VK ID rules shape `refresh_user_token()`:

- **the refresh token rotates.** The response carries a new one and the old value
  stops working; VK invalidates *every* token in the session if a spent refresh
  token is replayed. The rotated value therefore replaces the old one outright,
  and a response without one stores `""` rather than keeping the spent token.
- **a renewal failure is not retried silently.** It records
  `meta.refresh_failed_at`, after which the connection reads as `reauth` for
  `REFRESH_FAILURE_COOLDOWN` and the UI asks for a login. Re-posting a known-bad
  refresh token is exactly what loses a working session.

A missing `app_id` leaves the flow unchanged — the row is skipped with a warning,
as before.

Consequence for the product: **re-authorization is a ~6-monthly event, not a
routine chore.** Anything in the UI that asks for a login more often than that is
reporting a bug, not a fact.

## Source parameters

`Source.params` holds collection settings (a JSON column, no schema migration):

| Key | Meaning |
| --- | --- |
| `mode` | `api` (L1, default) \| `user` (L2, MTProto) \| `auto` (L1 with automatic L2 fallback) \| `browser` (L3, planned) |
| `query` | SEARCH sources: the phrase passed to `newsfeed.search` |
| `owner_ids` | SEARCH sources: keep only posts from these walls (community ids negative) |
| `incremental_mode` | `true` → only fetch what is newer than `last_checked` |
| `collection.count` / `limit` | page size / max items per run (default `SOCIAL_COLLECT_LIMIT`, 1000; never above the hard ceiling) |
| `collection.filter` | VK wall filter (`all`, `owner`, `others`, `suggests`) |
| `force_refresh`, `cli_dates` | direct CLI collection date/refetch overrides; web jobs clear legacy force flags |

`Source.params["monitored_users"]` is **removed**, not deprecated. It was stored
on a source but only read as a fallback for the task list, and a listed user
could be collected only when a separate `USER` source already existed — which is
what produced two sources for one person
(`docs/audit/2026-10-18-vk-raw-content.md`). Whose items are analysed is now
the task's `tracked_users`; nothing on a source selects an author
([task selection](design/task-selection.md)). The removal migration strips the
key from stored `params`.

`Source.params["monitored_users"]` is **removed**, not deprecated. It was stored
on a source but only ever read as a fallback for the task list, and a listed user
could only be collected when a separate `USER` source already existed — which is
what created two sources for one person (`docs/audit/2026-10-18-vk-raw-content.md`).
Whose items are analysed is now the task's `tracked_users`; nothing on a source
selects an author. The removal migration strips the key from stored `params`.

> `force_refresh` is a direct collection diagnostic and may re-fetch the
> requested API window. Web task/source force controls have been removed;
> legacy stored force flags are ignored at web task admission. Re-fetching does
> not bypass durable analysis receipts or trigger a paid re-analysis.

## Platform rate limits

`app/services/social/rate_limit.py`. A platform that refuses with "too many
requests" has neither failed nor returned an empty source, so collection treats
it as a third outcome:

- **Short limit → bounded pause and retry.** VK code 6 (too many requests per
  second) and HTTP / Bot API 429 with a small `retry_after` are retried on the
  collection read path (`BaseClient._fetch_page`, both full and incremental mode)
  up to `SOCIAL_RATE_LIMIT_RETRIES` times (default 2), pausing 1 s, 2 s, …
  or the platform's hint, never more than `SOCIAL_RATE_LIMIT_MAX_WAIT` seconds
  (default 10) per pause. Telethon waits out a flood limit itself; its
  `flood_sleep_threshold` is set to the same cap for L2 pulls.
- **Limit that does not clear → deferred.** VK code 9 (flood control) and 29
  (method quota), a hint above the cap, an exhausted retry budget, or a
  Telegram `FloodWaitError` longer than the cap raise `SourceRateLimited`. The
  collect job records the source with `outcome: "rate_limited"` (and
  `retry_after` when known), counts it in `rate_limited` /
  `rate_limited_sources`, not in `error` or `empty`, and sends no failure alert.
- **Nothing is acknowledged on deferral.** Items read before the limit are not
  staged and the cursor/watermark does not advance, so the next scheduled run
  reads the same window. The run summary reports «выполнено частично» and the
  source history shows «отложено».

`SourceRateLimited` is not a `RuntimeError`, so VK L1→L2 fallback and token
refresh do not switch credentials into the same limit. External writes (for
example `wall.createComment`) are never retried by this policy. The fixed 0.1 s
pacing between pages is unchanged; there is no shared per-platform budget across
sources or workers yet.

## Next steps (later milestones)

1. **L3 browser fallback** — Playwright client for sources without an API.
2. **Shared rate budget** — honour `Platform.rate_limit_remaining`/reset across
   sources and workers instead of per request.

## Deduplication and analysis contexts

`item_hash(item)` in `app/services/ai/dedup.py` hashes platform, external ID and
text; engagement counters and dates do not affect the content hash. Analysis
deduplication uses a durable success receipt keyed by workspace, source, content
hash and a versioned fingerprint of the actual scenario and semantic analysis
configuration. A repeated item in the same context is skipped even after its
physical `AIAnalytics` result is deleted or pruned. Receipts have no 30-day
cutoff and contain hashes and timestamps, not content. A different scenario,
target set, prompt/configuration or other semantic context has a different
fingerprint and can analyze the same source content independently.

Receipt lookup fails closed: if the receipt store cannot be checked, analysis
stops before calling a provider. Receipts are written only for successfully
covered hashes in the same transaction as their analytics result. Normal web
job admission already serializes a source's ordinary runs; this does not add a
global claim/lock for simultaneous direct CLI invocations.

Migration `0104` makes daily analytics uniqueness and generated weekly/monthly
rollups context-scoped by `analysis_fingerprint`, so distinct analyses for the
same source and period coexist. Legacy analytics are marked as legacy context.
Historical merged hashes cannot safely be assigned to a particular scenario,
and deleted legacy rows cannot be reconstructed, so there is no unsafe
historical receipt backfill. A previously analyzed legacy item without a
surviving receipt can therefore be analyzed once under the new policy.

## Raw content (`collected_items`)

Every collection writes its fetched batch here **before** anything is analysed,
and the rows are deleted only once an analysis has actually stored the matching
content. The table is therefore a write-ahead copy, not an archive — see
`docs/PRODUCT_PLAN.md` and the lifecycle gate in `docs/BUSINESS_PRODUCTION_READINESS.md` for why raw text is not kept forever.

| Stage | Table state |
| --- | --- |
| batch fetched, analysis not yet run | rows written (`run_id` = the job, NULL outside one) |
| analysis saved the content | rows for the hashes it covered are deleted |
| analysis failed / returned nothing | every row stays, and the next `analyze` run retries them |

Consequences worth knowing before changing this code:

- **Write before analyse.** The rows exist for the whole LLM call, so a crashed
  worker, a timeout or an outage leaves the content on disk. An inline analysis
  no longer keeps its batch in a local variable.
- **Retire by successfully covered hash, not by job.** Only hashes covered and
  persisted by the current analysis context are eligible for deletion; a
  receipt-only skip also counts as already processed. Partial analysis leaves
  uncovered rows staged. Rows needed by another active, scheduled or queued
  analysis context are retained while they are inside that context's date
  window. Once normal cleanup removes raw text, a newly introduced context
  needs collection again: a hash receipt cannot restore the text.
- **Retention is `handle_prune`.** It sweeps rows older than `staged_days`
  (defaults to the job-history `days`), which is the ceiling on a permanently
  failing source. `staged_days` is also capped by `DATA_RETENTION_MONTHS`, which
  separately expires analysis results (see `docs/CONFIGURATION.md`).

`analyze_inline` defaults to **false** for a `collect` job — the raw batch stays
staged for the separate `analyze` task, which is the workspace's shipped shape
(`daily-collect` parks, `daily-analyze` drains). Set `analyze_inline: true` to
collect and analyse in one pass; the rows the analysis actually stored are then
retired immediately. The direct CLI analyze command supports `--keep-raw` for
diagnostic runs that should preserve staged text even after successful
analysis; this option does not bypass receipts or enable paid calls by default.

### Selection at collection (P5-T14 stage C)

The collector applies the task's deterministic selection before staging:
`collected_items` contains only `selected` and `needs_review` rows, with
`content_type`, `parent_external_id` (comments), `lane_id` and the selection
status/reason. Rejected text is not stored; `collection_lane_runs` keeps counts
and reasons, and `collection_seen` keeps dedupe/new-item identity, including
rejected identities so a filter change can re-evaluate them. A comment-enabled
batch is filtered at thread level, so a post whose own text lacks the phrase is
still selected when its comments match. New selected rows are not re-filtered
during analysis; legacy NULL-status rows keep the old analysis-time pass once.
See [collection plan](design/collection-plan.md).

### Task collection context (P5-T14 stage D1)

Each source in a collect run now gets a parsed `CollectionIntent` and that
provider's capabilities. The handler fails a source with
`collection_intent_invalid` when a known collection value is malformed, rather
than collecting a guessed population. Requested operations the provider cannot
perform (for example comments, likes, city lookup or profiles on Telegram)
appear as bounded warnings in the per-source result; they are not an empty
result. Older tasks without a `collection` block keep the post-only behaviour.

### Comment lane (P5-T14 stage D2)

A task whose `collection.content_types` includes `comments` expands that lane
after the post batch is fetched and before selection. Only posts that pass the
task's **author** filter are expanded: the text scope is deliberately not
applied there, because a post whose own text lacks the phrase is exactly the
case a comment lane exists for. Comments are read through the provider adapter
(`fetch_comments`) in pages bounded by `collection.comments.max_per_post`, and
the run stops at `collection.comments.total` across all parents. Hitting either
cap sets `limit_reached` and names it (`comments_per_post_limit`,
`comments_total_limit`) in the per-source `comments` block; a failed post is
counted in `errors`/`error_codes` and never fails the post batch. Comment rows
carry `content_type="comment"` and `parent_external_id`, and the thread is
selected as a unit, so a matching comment keeps its parent post. A platform
without comment support keeps its `comments_unsupported` warning and fetches
nothing. Profile/city enrichment is a separate lane (stage D4).

### Engagement candidates (P5-T14 stage D3)

A task whose `collection.engagement_types` lists `likes` and/or `reposts` reads
those lanes for the author-eligible posts and stores the result as candidates,
never as `collected_items`: a candidate answers "who reacted", not "what was
said", so it does not inflate `content_count` and never reaches the analysis.
One task-wide `collection.candidates.limit` bounds every lane together, and an
identity seen in both lanes (or in a re-run) is stored once. `confirm` decides
the stored status: `manual` (default) keeps candidates `new` for an operator,
`auto` marks them `confirmed` within the same cap and still creates no source.
A re-collection keeps the first evidence and never resets an existing
decision. The per-source `candidates` block reports `fetched`, `by_lane`,
`duplicates`, `invalid`, `stored`, `limit_reached`
(`candidates_limit_reached`) and `errors`/`error_codes`; a rehearsal reports
the same counts with `stored: 0` and `dry_run: true`.

### Profile and city enrichment (P5-T14 stage D4)

A city is not a post attribute: VK post search cannot filter by it, and wall
items carry none. With `collection.city` configured, the collector reads the
profiles of the batch's post authors through the provider adapter
(`fetch_profiles`) before selection, in bounded identity batches, and attaches
the self-declared city to the author together with `city_source: "profile"` and
`city_confidence`. The same enrichment is applied to discovered candidates
before they are stored, so a candidate's `city`/`city_source`/`city_confidence`
columns carry a declared value rather than a guess. Comments are not enriched:
a commenter's city says nothing about whether the thread belongs to the task, so
a thread is decided by its post.

The city layer runs after the author and text layers, and only for an item that
those already admitted:

- `prefer` (default) and `review`: a declared match selects the item; a known
  mismatch stays selected with reason `city_mismatch`; an unknown city becomes
  `needs_review` with reason `city_unknown`.
- `require`: a known mismatch is rejected (`city_mismatch`), an unknown city is
  still `needs_review` (`city_unknown`) — an unenriched author is never
  silently dropped.
- For a thread the decision uses the post's author, not a matching commenter.
- `needs_review` rows are staged but never analysed until an operator decides:
  the analysis path reads only `selected` rows.

A platform without profile support (Telegram) skips the gate instead of sending
the whole batch to review: the per-source `profiles` block reports
`supported: false` and `error_code: "profiles_unsupported"`. The same block
reports `requested`/`resolved`/`unknown` identities (not rows), `errors`/
`error_codes` for a failed batch, and the city name and mode; the `selection`
block reports the city counters `matched`/`unknown`/`mismatch`. A rehearsal
reports the same preview without storing rows.

## Rehearsal (dry run)

`CLI --dry-on` and the task's `dry_run` toggle fetch the platform, count what is
new and return a bounded preview, but write no `collected_items`, call no model
and do not advance `last_checked`. A rehearsal that moved the cursor would make
the following real run skip the exact window it rehearsed. A comment-enabled
task rehearses its comment lane too: the fetch is read-only, so the preview
reports the same cap evidence a real run would store. An engagement lane is
previewed the same way, with `stored: 0` and `dry_run: true` in its
`candidates` block. In `analyze` the
same flag stops at the deterministic task selection and reports
`eligible`/`rejected` counts. The web toggle is shown to workspace
administrators and owners only; a non-admin submission is ignored with a
warning, never silently applied.

## Tests

`tests/test_credentials.py` — vault/env precedence, kind preference, expiry,
`CredentialMissing`, and that a secret never reaches the logs.
`tests/test_telegram_ingest.py` — content-item contract, watermark replay,
newer posts, unknown chats and private messages (analyzer stubbed, no network).
`tests/test_telegram_mtproto.py` — L1/L2 mode switching, missing/expired
sessions, entity resolution, cross-layer `external_id` for dedup, watermark
advance, and tenant fail-closure (Telethon network client faked).
`tests/test_dedup.py` — hash stability/order-independence, batch and item
filtering, and analyzer integration. Durable receipt and context behavior is
covered separately by `tests/test_analysis_receipts.py`.

