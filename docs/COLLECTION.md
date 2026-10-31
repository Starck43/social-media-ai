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
over what a source returns — see [task selection](COLLECTION.md#task-selection-contract).
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

### VK OAuth refresh log privacy

VK OAuth refresh failures and failures to persist `refresh_failed_at` log only
fixed event text, a positive integer `user_id` (or `None` for invalid input),
and the shared bounded `credential_exception_status` category. Neither path
formats the exception or logs a traceback, request URL, provider payload,
access token, refresh token, PKCE verifier or device identifier. Unknown errors
use `error: integration_error`; raw exception text is not a diagnostic code.

Refresh failures still attempt to record the failure and return `None`;
bookkeeping failure remains best-effort and does not mask the original failure.
Successful refresh rotation and cancellation propagation are unchanged. This
contract is limited to these two refresh error paths, not a certification of
all OAuth/login/provider logging. Regression coverage:
`tests/security/test_p6_t02_vk_oauth_log_privacy.py` (isolated mocked checks,
no live tokens or real DB; exact revision/commands in the task PR).

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
full contract is in [collection plan](audit/collection-plan-history.md).

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

### Explicit task connections (P8-T38)

A version-1 task plan may pin `vk_access: {"mode": "api"}` or
`vk_access: {"mode": "user", "credential_id": 7}`. The latter selects that
exact personal vault row, owned by the authenticated initiator (the stored payer
for scheduled runs). The owner must remain an active workspace member with
source-analysis and task-execution rights. Ordinary edits retain a pin; per-run
overrides cannot replace it. Deliberate task edits may select a different
connection. Source/scenario/plan bindings and product entitlements remain separate.

The web task editor shows the connection selector, local state, numeric account,
OAuth scopes, expiry, explicit method grants and method/mode matrix. Read-only
catalogs share `vk_connections.connection_catalog`:

- API: `GET /api/v1/tasks/vk-connections` in an authenticated workspace.
- CLI: `task vk-connections --tenant <workspace> --user <member>`;
  `task plan '<JSON>' --tenant <workspace> --user <member>` adds a safe preview.
- Admin: `/admin/agent-task/vk-connections?tenant_id=<workspace>` returns only the
  signed-in operator's own member connections. Plan JSON uses the same selector.
- Agent: `task_vk_connections` lists choices before selection; `task_plan` and
  `search_plan` show the same safe connection preview;
  pass `vk_access` to select a connection. A preview never reveals a token.

Capability metadata is conservative. OAuth `meta.scope` records returned scopes,
not requested scopes; OAuth login does not imply a social API grant. Personal
rows use explicit trusted `meta.vk_method_grants` (known method names only)
and `meta.vk_method_scopes` (method-to-required-scope arrays). Service access uses
`VK_SERVICE_METHOD_GRANTS` / `VK_SERVICE_GRANTED_SCOPES` JSON arrays and the
`VK_SERVICE_METHOD_SCOPES` JSON object, empty by default. These are operator
assertions from separately reviewed application-profile evidence, never
user-authored plan fields or product entitlements. Missing or unknown grants
or method scope requirements fail closed; an explicitly reviewed empty scope
array is distinct from an absent method declaration. This implementation does not turn Q-5 into a live availability
claim. No method-to-scope relationship is inferred from a token type. The
policy unions the trusted method requirements with any additional caller scopes
and checks them against the actual granted scopes at every selected request.

Admission checks fresh facts before queueing; execution reloads them before
population/collection and before every VK HTTP request, including each page,
lookup, comment and candidate lane. The request boundary replaces any cached or
legacy token with the exact selected credential, and emits fixed errors without
provider text or token-bearing URLs. Selected requests send tokens in POST
bodies so standard HTTP request logs do not include them. Sanitized HTTP 429 and
VK 6/9/29 responses retain the existing bounded retry/defer behavior. Only
identity lookups normalize not-found codes to empty. Unknown profile errors such as 1051 cannot
switch credentials. Existing unselected source `auto` semantics remain as above.

For explicit tasks, an expired row is denied until renewed through the existing
OAuth path or reauthorized. It is never silently renewed by selecting the newest
row. Revoking/disabling the selected row stops later requests; a refreshable row
is shown as requiring renewal, not as an execution permit. Grant metadata must
be reviewed again after reauthorization replaces it. Catalogs never return
refresh tokens, device IDs, arbitrary provider metadata or encrypted secrets.

### VK search-result categories (P8-T33)

`app/services/collection/search_categories.py` declares the VK search-result
categories as an explicit, evidence-gated registry: each category records its
official API method, result kind, status (`enabled`, `mocked`, `deferred`,
`unsupported`) and a bounded reason. Only `people` (official `users.search`)
is selected in the T33 slice; it dispatches through the collection adapter's
existing `search_people`, returns typed people results (never Sources) stamped
with category provenance, and stays `mocked` — contract-tested only — while
Q-5 owns live method/profile evidence. Video, apps/games, owner-scoped market
items and music are `deferred` with their own task cards; clips and channels
are `unsupported` with explicit reasons. Only `enabled` categories may
execute: `resolve_categories` aggregates only enabled categories for `all`,
returns the rest as unavailable with their reasons and fails closed on unknown
ids; a refused or unavailable category raises a bounded error instead of
returning an empty page. `search_category` always re-resolves the canonical
registry record, so a caller-supplied category object cannot bypass the gate.

Read-only catalogs share `search_categories.category_catalog`:

- API: `GET /api/v1/tasks/vk-search-categories` in an authenticated workspace.
- Web: `GET /app/tasks/vk-search-categories`; the task editor renders the
  catalog read-only with each category's status.
- CLI: `task vk-search-categories`.
- Admin: `/admin/agent-task/vk-search-categories` for an authenticated operator.
- Agent: `task_vk_search_categories` returns the same catalog in chat.

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
([task selection](COLLECTION.md#task-selection-contract)). The removal migration strips the
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
See [collection plan](audit/collection-plan-history.md).

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

## Universal task boundary (P8-T29)

The [universal task contract](AGENT_TASKS_PLAN.md#explicit-task-plans) reuses these collectors. First delivery covers VK post search, community discovery and wall collection. Source-level platform/connection identity remains independent from task-owned queries, dates and methodology. Existing Telegram behavior is preserved. Search availability depends on Q-5; a successful login or wall read is not proof of global search access.


## Task selection contract

How a task decides *which collected items are analysed*. Selection is a
property of the **task**; a scenario still answers only "how to analyse" and a
source only "which wall/channel we may read".

> **P5-T14 stages C and E implemented.** Deterministic selection now runs
> before staging: `collected_items` stores only `selected`/`needs_review` rows
> with a status/reason, rejected text is not stored, and the analyse path does
> not re-filter new rows. A comment-enabled batch is filtered at thread level
> (post + comments). Collection types now come from the task's `collection`
> block; scenario `content_types` is supported media only. Task-scoped query
> lanes are hidden from the global source list and excluded from the
> empty-set/"all active" fallback. See
> [collection plan](audit/collection-plan-history.md).

### Why the old model is replaced

Until this refactor a task could carry `payload.monitored_users`. It did not
filter anything: for every source of the task the collector replaced the wall
with the personal walls of the listed users, and a listed user only worked when
a separate `Source` row of type `USER` already existed. That produced duplicate
sources for one person (one to serve as the wall registry, one to be tracked),
silently analysed nothing from the task's own sources, and stored the same
setting in two places (`Source.params["monitored_users"]` and the task payload)
with different effects. The setting is removed, not deprecated: there is no
legacy read path.

### The three layers of a task scope

| Layer | Where | Meaning |
| --- | --- | --- |
| **Sources** | `agent_task_sources` | Walls/channels the task may read. Empty set = every active workspace source except task-owned query lanes (`owner_task_id`); only an explicit link activates a lane. |
| **Tracked users** | `AgentTask.payload["tracked_users"]` | Authors whose items are eligible for analysis. An item is eligible when its author is in the list. |
| **Text scope** | `AgentTask.payload["keywords_list"]`, `payload["topics_list"]` | Phrases an eligible item must contain (see matching rules). |

The layers are applied in order: an item must come from a task source, carry an
eligible author, and pass the text scope. The same order is reported in run
diagnostics, so an operator sees *why* an item was left out.

Stage C keeps these layers but changes two boundaries: selection runs at
collection, and a comment-enabled batch applies the text scope to the post
**and its comments together** (thread level). Which posts are expanded for
comments is decided by the author layer only: a post whose own text does not
match can still carry the matching comment that made the task comment-enabled.
A task-scoped query source is an explicit lane, never part of the "all active
sources" shortcut; that fallback exclusion is still pending. Legacy rows staged
before stage C keep a NULL
status and still pass through the old analysis-time selection once.

#### Tracked users

`tracked_users` is a **filter on the author**, never a collection target. It does
not read anyone's personal wall, and it never adds a source to the task.

Stored as a list of flat strings, `<platform>:<external_id>`:

```json
{"tracked_users": ["vk:123456", "telegram:@durov"]}
```

- `external_id` is what the platform client already resolves for a source —
  a numeric id for VK users, a username/chat id for Telegram. Accepting
  `vk:@name` or `vk:name` in the UI is a convenience that must be resolved to
  the numeric id through the platform client before it is stored; the stored
  value is the resolved one so a rename cannot silently retarget the filter.
- An empty list means "no author filter": the task analyses the source's own
  items in the types its task-owned `collection` block selects, not "nobody"
  (see below).
- Identity is platform-scoped; `vk:123` and `telegram:123` are different people.

An item's author is read from the collected snapshot
(`collected_items.author`: the next `Author/Timestamp` pair, i.e. whose
"next post" a wall repost carries), not from `Source.params`, so a wall that
reposts other people is filterable by the person it carries.

**Without `tracked_users` the task analyses the source's own content, as the
task's collection block says.** The task's `collection.content_types` (`posts`,
`comments`) decides what is collected and analysed; scenario `content_types` is
only the supported media capability and never changes collection. The selection
layers never change it:

- no `tracked_users` → the items are those of the source itself
  (`collection.content_types` applied to the wall/channel);
- `tracked_users` given → **only** those authors' items of the same
  `collection.content_types` are analysed. The task does not widen the content
  types and does not fall back to the source's own items when a tracked author
  has nothing.

A task whose `tracked_users` are all unknown to the collector reports an empty
selection with a named reason (`unknown_tracked_user`), never a silent "nothing
found".

#### Text scope

`keywords_list` (and `topics_list` as a synonym of the same scope) are whole
phrases matched against the item text plus its author name and permalink, within
the content types the scenario already selected:

- matching is case-insensitive, word-bounded, `ё` = `е`; a phrase matches as a
  contiguous sequence of words, and Russian inflections are folded to a common
  stem (`utils/ru_stem.py`) so «Италия» matches «Италию»/«Италии». Stemming is a
  matching key, not a synonym: «дизайн» still does not match «дизайнер»;
- `payload["keywords_match"]` is `"any"` (default) or `"all"`;
- the same list is *also* offered to the model as analysis context
  (`{payload_instruction}`), but the deterministic scope is what decides whether
  an item is paid for. A prompt is not a filter.
- An empty scope means every eligible item is analysed.

#### Region and city

There is no region column and no platform filter for it: VK does not expose a
post's city, and a profile city is self-declared. A city is expressed as
ordinary text-scope phrases ("Киров", "Kirov") plus the sources whose audience
is that city. Since P5-T14 stage D4 a task's `collection.city` additionally
compares the author's profile-declared city, carrying `city_source` and a
confidence; an unknown city goes to `needs_review` instead of being dropped.
The UI must label it as a declared preference rather than implying a
geographic filter.

### What is not selection

- **Reaction/action triggers** (`KEYWORD_MATCH`, `USER_MENTION`,
  `SENTIMENT_THRESHOLD`, `ACTIVITY_SPIKE`) decide whether a stored analysis
  produces an action. They never decide whether an item is analysed; a keyword
  for an alert and a keyword for a selection are different intentions and live
  in different fields.
- **Analytics filters** (`/app/analytics`) narrow the report over rows that were
  already analysed. They never reduce spend and never change a task.
- **A scenario's `scope`** holds methodology only. Concrete people, brands and
  phrases there are rejected by `param_registry` rules.

### Migration history

The [original removal/validation plan](audit/task-selection-migration-history.md)
is retained as historical rationale. Current migrations follow the database policy.
