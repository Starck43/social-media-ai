# Configuration Reference

Application-level configuration comes from environment variables. Load it from
a `.env` file or pass it directly to the process. Personal credentials and
workspace recipients belong in the database as described below.
**Never commit `.env` files.**

The application reads config from `app/core/config.py` (Pydantic `Settings`).

## Application credentials and recipient boundaries

| Scope | Storage | Purpose |
|---|---|---|
| VK application | env: `VK_APP_ID`, `VK_SERVICE_KEY`, `VK_CLIENT_ACCESS_KEY`, `VK_REDIRECT_URI` | Application identity, L1 service access and registered OAuth callback; a client secret is not a user's access token. |
| Shared Telegram bot | env: `TELEGRAM_BOT_TOKEN` | Bot API transport; one bot may serve multiple workspaces. |
| Personal integrations | encrypted `user_credentials` | User VK access/refresh tokens and Telegram MTProto sessions; existing collection-owner resolution selects the user. |
| Workspace digest recipients | `tenant_channels`, active and `is_digest_target=True` | Owned destination IDs for this workspace only; use existing binding workflow, then Settings → Channels to toggle digests. |
| Operator notifications | optional env: `TELEGRAM_ADMIN_CHAT_ID` | Fixed-template operator-alert destination; never a workspace notification fallback. |
| Agent access bootstrap | `TELEGRAM_OWNER_IDS` | Telegram **user IDs**, not chat/channel IDs; distinct from the notification destination and workspace memberships. |

Keep infrastructure settings (`POSTGRES_URL`, `SECRET_KEY`, `CREDENTIALS_KEY`)
in env/your deployment secret store. Preserve and back up `CREDENTIALS_KEY`
securely: replacing it without re-encryption makes existing vault/provider keys
unreadable. No actual secret values belong in documentation or source control.

`TELEGRAM_DIGEST_CHANNEL_ID` and `MAX_CHANNEL_ID` remain accepted by Settings
for compatibility, but digest delivery ignores both. Do not add them to new
installations. `TELEGRAM_ADMIN_CHAT_ID` is never consulted for digest delivery.
An installation with only env destinations must explicitly register/enable its
workspace recipient; no automatic binding or schema migration is performed.
Broadcast result keys are now uniformly `transport:chat_id`, including bootstrap.

**Notification isolation:** workspace notifications require an explicit active
owned recipient. Only `send_operator_alert(event_code)` consults the admin chat,
and its text comes from a fixed catalog. The legacy `send_critical_alert` shim
discards all free-form title/message/error details. Collection errors retain a
generic source-owned DB notification and emit only a generic operator event.
Missing admin configuration does not redirect messages to a client workspace.
See [notification boundaries and remaining limits](NOTIFICATIONS.md).

## Workspace subscription provider (P2-T16 foundation)

`SUBSCRIPTION_PAYMENT_PROVIDER=yookassa` selects YooKassa API v3 by default.
`SUBSCRIPTION_PAYMENT_MODE=test` is the only accepted mode in this increment;
`live` and unknown providers are rejected by configuration validation.
`SUBSCRIPTION_CHECKOUT_ENABLED=false` is the safe default. This increment adds
configuration only: no checkout/webhook routes, purchase persistence or entitlement
activation are implemented by these settings. Setting the flag does not supply
those missing paths or authorize provider calls.

| Setting | Default | Meaning |
| --- | --- | --- |
| `SUBSCRIPTION_PAYMENT_PROVIDER` | `yookassa` | Selected provider, not a payment authorization |
| `SUBSCRIPTION_PAYMENT_MODE` | `test` | Test-only contract; not merchant verification |
| `SUBSCRIPTION_CHECKOUT_ENABLED` | `false` | Reserved opt-in gate; does not enable live payments |
| `YOOKASSA_SHOP_ID` | empty | Test merchant's ASCII numeric shop ID |
| `YOOKASSA_SECRET_KEY` | empty | Test merchant key, redacted/excluded from settings serialization |
| `YOOKASSA_RETURN_URL` | empty | Server-configured HTTPS browser return, never payment proof |
| `YOOKASSA_HTTP_TIMEOUT` | `15` | Future adapter timeout, 1–30 seconds |

Do not put credentials in Notion, Git or PR comments. Use the deployment secret
store/private `.env`. Empty credentials preserve existing deployments without
activating payment behavior. A return URL, when supplied, must be HTTPS on port
443/8443 (or implicit 443), without embedded credentials, fragment or whitespace.
It is not the notification URL. Configure Basic Auth notifications in the
YooKassa Merchant Profile only after a webhook endpoint is actually implemented
and its HTTPS URL has been approved; no webhook signature secret is invented.

YooKassa test stores use the same API host as live stores. A `test` configuration
label is not proof that credentials belong to a test store. The future adapter
must verify retrieved payment `test=true`, merchant, amount/currency and purchase
binding before accepting evidence; fake callbacks and browser redirects never
grant access. The official contract and remaining work live in
[SUBSCRIPTIONS](SUBSCRIPTIONS.md).

**Migration dependency:** P2-T15 first introduced `tenants.plan_expires_at` and
`entitlement_usage` in revision `0117`; Q-14 later incorporated that schema into
frozen `baseline_0118`. The checked-in migration chain now has follow-up `0119`
for workspace offboarding; `0118` is not a free revision number. Do not recreate
the entitlement fields or replay the removed numbered migrations. Fresh-schema
and legacy-schema transition rules are in [DEPLOYMENT](DEPLOYMENT.md). Before
any separately approved subscription-schema work, inspect the actual integrated
graph and reserve a distinct revision on its current head; never infer it from an
old planning snapshot. The #246 → #252 → #253 integration sequence is historical
and complete.

## Optional same-host runtime observation

`RUNTIME_STATE_DIR` is unset by default. An API alone does not prove that
`python -m app.runtime` is running. Set an absolute path to one trusted local
POSIX directory **in both API and runtime environments** to enable `/runtimez`.
Example configuration only: `RUNTIME_STATE_DIR=/var/lib/social-media-ai/runtime`.
The operator must provision ownership/access for the service user; the runtime
creates the directory with mode 0700 and its files with mode 0600. Do not point
this at a customer-controlled directory, symlink, FIFO or a network filesystem.

The same OS user, host/kernel lock namespace and directory must be shared.
Containers need an explicitly reviewed local shared mount and matching UID;
this change does not edit Compose or deploy a volume. Independent paths/hosts
are independent observations, not global runtime uniqueness. Do not enable a
new profile during an incident as a supposedly read-only probe.

With the option enabled, runtime takes a nonblocking exclusive local owner lock
before default-task bootstrap. A duplicate owner or inaccessible directory
aborts before bootstrap. Keep `owner.lock` in place; never unlink/replace it to
force a start. The lock is not a DB/distributed lease, job claim or authorization
mechanism. Unset configuration preserves the existing startup path and reports
`unconfigured`, never green, from `/runtimez`.

A random runtime ID changes per start. Runtime publishes `starting`, then
`running` after bootstrap, every 15 seconds on its event loop, and `stopped` at
shutdown; ownership stays held until component cleanup finishes. Heartbeat or
component failure cancels/drains the supervised tasks before releasing ownership.
No scheduler/worker/listener progress is inferred; a responsive event loop can
coexist with failing jobs, broken credentials or disconnected channels.

## .env.example

```bash
# ─── Application ───────────────────────────────────────────────
ENVIRONMENT=development        # development | production
DEBUG=false
HOST=0.0.0.0
PORT=8000
SECRET_KEY=change-me-in-production   # Pydantic: required, generate with:
                                     # python -c "from secrets import token_urlsafe; print(token_urlsafe(64))"
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:8000

# ─── Database ──────────────────────────────────────────────────
POSTGRES_URL=postgresql://user:pass@localhost:5432/social_media
DB_SCHEMA=public

# ─── Redis (active admin rate-limit storage; Celery remains optional) ─
REDIS_URL=redis://localhost:6379/0

# ─── JWT / Auth ───────────────────────────────────────────────
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=43200    # 30 days in dev, overridden to 60 in prod
REFRESH_TOKEN_EXPIRE_DAYS=30
LOGIN_TIMEOUT_MINUTES=15             # counting window and lockout duration, shared by all login surfaces
MAX_LOGIN_ATTEMPTS=5                 # failed attempts per account before the lockout
LOGIN_IP_MAX_ATTEMPTS=20             # failed attempts per client address, across accounts
MAX_WORKSPACE_MEMBERS=2              # people one workspace may hold (owner + invited), all tiers

# ─── Password Policy ──────────────────────────────────────────
PASSWORD_MIN_LENGTH=8
PASSWORD_REQUIRE_UPPERCASE=true
PASSWORD_REQUIRE_LOWERCASE=true
PASSWORD_REQUIRE_NUMBERS=true
PASSWORD_REQUIRE_SPECIAL=false

# ─── Rate Limiting ────────────────────────────────────────────
CHANGE_PASSWORD_RATE_LIMIT=5/minute

# ─── CSRF ─────────────────────────────────────────────────────
SCRF_TOKEN_EXPIRY_MINUTES=15
SCRF_TOKEN_LENGTH=32

# ─── Admin Panel ──────────────────────────────────────────────
ADMIN_ENABLED=true

# ─── External Platforms (optional — app boots without them) ───
# VK
VK_APP_ID=
VK_SERVICE_KEY=             # VK "Сервисный ключ доступа" (L1)
VK_CLIENT_ACCESS_KEY=       # VK "Защищённый ключ" (OAuth client secret)
VK_REDIRECT_URI=https://your-domain/api/v1/social/callback

# Telegram (Bot API)
TELEGRAM_BOT_TOKEN=
TELEGRAM_API_ID=          # L2 MTProto
TELEGRAM_API_HASH=        # L2 MTProto
TELEGRAM_SESSION=         # L2 MTProto (StringSession, never printed)
TELEGRAM_ADMIN_CHAT_ID=   # fixed-template operator alerts only

# Telegram (Channels)
TELEGRAM_OWNER_IDS=       # comma-separated Telegram user ids for agent chat
# Digest recipients: workspace channel bindings in DB, not env

# MAX
MAX_BOT_TOKEN=
MAX_API_URL=https://platform-api2.max.ru
MAX_OWNER_ID=             # MAX user id for agent chat
# MAX digest recipients also come from workspace channel bindings

# ─── Tenancy ──────────────────────────────────────────────────
CREDENTIALS_KEY=          # Fernet key — generate with:
                          # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
DEFAULT_TENANT_SLUG=owner

# ─── Scheduler ────────────────────────────────────────────────
SCHEDULER_ENABLED=true
SCHEDULER_POLL_SECONDS=30
SCHEDULER_TIMEZONE=Europe/Moscow

# ─── Agent ────────────────────────────────────────────────────
AGENT_MODEL=              # explicit LLMModel name inside the acting personal catalog; empty = auto-resolve
AGENT_HISTORY_LIMIT=20    # messages kept in agent context per session
AGENT_MAX_ITERATIONS=6    # max tool-call rounds per user message
AGENT_DAILY_COST_LIMIT=5.0  # USD cap (agent + digests combined) per UTC day
AGENT_MAX_TOKENS=1024     # reply size cap for chat
AGENT_TEMPERATURE=0.4

# ─── Background Jobs ──────────────────────────────────────────
JOB_MAX_ATTEMPTS=3
JOB_RETRY_BACKOFF_SECONDS=300

# ─── Data retention ───────────────────────────────────────────
DATA_RETENTION_MONTHS=1        # calendar months; minimum 1; deletion is irreversible

# ─── Collection freshness ─────────────────────────────────────
SOURCE_STALE_HOURS=24          # hours; an active source unchecked longer is reported stale

# ─── LLM ──────────────────────────────────────────────────────
LLM_REQUEST_DELAY=3000         # ms between requests (rate limit)
LLM_DEFAULT_TEMPERATURE=0.3    # for analysis tasks
LLM_DEFAULT_MAX_TOKENS=400     # for social media annotations
LLM_DEFAULT_STREAM=false       # complete responses for processing
LLM_DEFAULT_TIMEOUT=90.0       # seconds

# ─── Logging ──────────────────────────────────────────────────
LOG_LEVEL=INFO
```

---

## Variable Reference

### Application

| Variable | Default | Description |
|---|---|---|
| `ENVIRONMENT` | `development` | `development` or `production` (affects token expiry, password policy) |
| `DEBUG` | `false` | Enable debug mode |
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `8000` | Bind port |
| `SECRET_KEY` | *(required)* | Pydantic JWT signing key |
| `ALLOWED_ORIGINS` | `http://localhost:3000,http://localhost:8000,http://localhost:8501` | Compatibility setting; not read by the active CORS middleware |
| `BACKEND_CORS_ORIGINS` | `["http://localhost:3000", "http://localhost:8000"]` | Active CORS allowlist; set as a JSON list in the environment |

### Database

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_URL` | *(required)* | Async PostgreSQL connection string (`postgresql+asyncpg://user:pass@host:port/db`) |
| `DB_SCHEMA` | `public` | PostgreSQL schema name |
| `DB_POOL` | `pool` | Async engine connection reuse: `pool` reuses connections, `null` opens one per session. A pooled asyncpg connection belongs to the event loop that opened it, so a process that runs parts of its work in another loop (the test suite runs several unittest-style cases in a loop of their own) must use `null`. |

### Redis

The active admin SlowAPI limiter constructs Redis storage during ASGI import,
so the Redis Python client is part of the core package. Celery remains a frozen,
optional compatibility profile.

| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | Active admin SlowAPI rate-limit storage and frozen Celery compatibility. The Redis Python dependency remains core; this does not authorize starting Redis/Celery. |

### JWT / Authentication

| Variable | Default | Description |
|---|---|---|
| `ALGORITHM` | `HS256` | JWT signing algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `43200` (30 days) | Overridden to `60` (1 hour) in production |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `30` | Refresh token lifetime |
| `LOGIN_TIMEOUT_MINUTES` | `15` | Counting window and lockout duration for the shared login budget |
| `MAX_LOGIN_ATTEMPTS` | `5` | Failed attempts per account before the shared lockout |
| `LOGIN_IP_MAX_ATTEMPTS` | `20` | Failed attempts per client address, across accounts |
| `MAX_WORKSPACE_MEMBERS` | `2` | People one workspace may hold across all tiers; the tier's `max_team_members` still applies, the tighter wins. Deployment-wide for the pilot — per-workspace settings are a later task |

The console, `/app/login` and `POST /api/v1/auth/login` share one budget stored in
the `login_attempts` table, so it survives a cleared cookie and applies to every
process. The table is created by revision `0111`; deploying the code without that
revision breaks login. These settings are not a substitute for a password policy
or for provider-side rate limiting.

### Password Policy

| Variable | Default | Description |
|---|---|---|
| `PASSWORD_MIN_LENGTH` | `8` (6 in dev) | Minimum password length |
| `PASSWORD_REQUIRE_UPPERCASE` | `true` | Require uppercase letter |
| `PASSWORD_REQUIRE_LOWERCASE` | `true` | Require lowercase letter |
| `PASSWORD_REQUIRE_NUMBERS` | `true` | Require digit |
| `PASSWORD_REQUIRE_SPECIAL` | `false` | Require special character |

### Rate Limiting

| Variable | Default | Description |
|---|---|---|
| `CHANGE_PASSWORD_RATE_LIMIT` | `5/minute` | Rate limit for the operator password change/reset endpoint |

### CSRF

| Variable | Default | Description |
|---|---|---|
| `SCRF_TOKEN_EXPIRY_MINUTES` | `15` | CSRF token lifetime |
| `SCRF_TOKEN_LENGTH` | `32` | CSRF token length in bytes |

### Admin

| Variable | Default | Description |
|---|---|---|
| `ADMIN_ENABLED` | `true` | Enable/disable sqladmin panel |

### External Platforms

| Variable | Default | Description |
|---|---|---|
| `VK_APP_ID` | `None` | VK application ID |
| `VK_SERVICE_KEY` | `None` | VK service token (L1) — console "Сервисный ключ доступа" |
| `VK_CLIENT_ACCESS_KEY` | `None` | VK OAuth client secret — console "Защищённый ключ" |
| `VK_REDIRECT_URI` | `http://localhost/api/v1/social/callback` | OAuth callback; set the registered public HTTPS URL in production |
| `TELEGRAM_BOT_TOKEN` | `None` | Telegram bot token |
| `TELEGRAM_API_ID` | `None` | Telegram API ID (L2 MTProto) |
| `TELEGRAM_API_HASH` | `None` | Telegram API hash (L2 MTProto) |
| `TELEGRAM_SESSION` | `None` | Telegram StringSession (L2 MTProto) |
| `TELEGRAM_ADMIN_CHAT_ID` | `None` | Fixed operator-alert catalog only; no workspace/digest fallback |
| `TELEGRAM_OWNER_IDS` | `""` | Comma-separated Telegram user IDs allowed to chat with the agent |
| `TELEGRAM_DIGEST_CHANNEL_ID` | `""` | Deprecated; ignored by digest delivery (use workspace bindings) |
| `MAX_BOT_TOKEN` | `None` | MAX bot access token |
| `MAX_API_URL` | `https://platform-api2.max.ru` | MAX API base URL |
| `MAX_OWNER_ID` | `""` | MAX user ID allowed to chat with the agent |
| `MAX_CHANNEL_ID` | `""` | Deprecated; ignored by digest delivery (use workspace bindings) |

### Tenancy

| Variable | Default | Description |
|---|---|---|
| `CREDENTIALS_KEY` | `None` | Fernet master key for encrypting personal (`user_credentials`) secrets. **Never commit.** |
| `DEFAULT_TENANT_SLUG` | `owner` | Slug of the bootstrap (owner) workspace |

### Scheduler

| Variable | Default | Description |
|---|---|---|
| `SCHEDULER_ENABLED` | `true` | Enable/disable the scheduler loop |
| `SCHEDULER_POLL_SECONDS` | `30` | How often the scheduler checks for due schedules |
| `SCHEDULER_TIMEZONE` | `Europe/Moscow` | Fallback timezone for cron expressions (a workspace's own timezone wins) |

### Agent

| Variable | Default | Description |
|---|---|---|
| `AGENT_MODEL` | `None` | Explicit LLMModel name, resolved inside the acting user's personal catalog: the row must be text-capable, active, on an active provider and owned by that user. A foreign or unknown name falls back to auto-resolution in the same catalog, never to another owner's model. Empty = auto-resolve. |
| `AGENT_HISTORY_LIMIT` | `20` | Number of messages kept in agent context per session |
| `AGENT_MAX_ITERATIONS` | `6` | Max tool-call rounds per user message |
| `AGENT_DAILY_COST_LIMIT` | `5.0` | USD cap for agent + digests combined per UTC day |
| `AGENT_MAX_TOKENS` | `1024` | Reply size cap for agent chat |
| `AGENT_TEMPERATURE` | `0.4` | Temperature for agent chat completions |

### Background Jobs

| Variable | Default | Description |
|---|---|---|
| `JOB_MAX_ATTEMPTS` | `3` | Max retry attempts for a job |
| `JOB_RETRY_BACKOFF_SECONDS` | `300` | Base backoff in seconds; actual = `backoff * 2^(attempts-1)` |

### Data retention

| Variable | Default | Description |
|---|---|---|
| `DATA_RETENTION_MONTHS` | `1` | Calendar months to keep analysis results (`ai_analytics`) and unanalysed raw content (`collected_items`). The daily `prune` task deletes older rows in each workspace, measured by last write, not publication date. Minimum `1`; startup fails on `0` or a non-integer. |

### Collection freshness

| Variable | Default | Description |
|---|---|---|
| `SOURCE_STALE_HOURS` | `24` | Hours after which an **active** source whose `last_checked` is older (or absent) is reported as stale on `/app/sources`: a banner counts them and each row is marked «устарел» or «не проверялся». Read-only — it never triggers a collection or changes a source. The same window is used by `SourceManager.get_sources_for_monitoring`. Minimum `1`; startup fails on a non-positive value. A source that was never checked is counted separately, so "older than N hours" is never claimed for it. |

Kept regardless of age: analyses referenced by a bot action, the LLM attempt
journal (`llm_attempts`, accounting evidence), and job rows with an operational
budget stop or unconfirmed outcome. Finished job history keeps its own `days`
budget (default 7). Deleted rows cannot be restored from the application; there
is no export or offboarding flow yet. Lowering the value takes effect at the next
`prune` run, so back up first if older history matters.

### LLM

| Variable | Default | Description |
|---|---|---|
| `LLM_REQUEST_DELAY` | `3000` | Delay in ms between LLM requests (rate limit) |
| `LLM_DEFAULT_TEMPERATURE` | `0.3` | Temperature for analysis tasks |
| `LLM_DEFAULT_MAX_TOKENS` | `400` | Max tokens for social media annotations |
| `LLM_DEFAULT_STREAM` | `false` | Use streaming for LLM responses |
| `LLM_DEFAULT_TIMEOUT` | `90.0` | Request timeout in seconds |

### Logging

| Variable | Default | Description |
|---|---|---|
| `LOG_LEVEL` | `INFO` | Python logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) |
| `LOG_DIR` | `~/.social-media-ai/logs` | Writable directory for rotating `app.log`; absolute path required (`~` is expanded) |

File logging remains enabled. Importing the application logger creates `LOG_DIR`
and opens `app.log` (10 MiB rotation, five backups), but never creates `./logs`
or writes into the installed package. The service user's home directory is used
by default on every platform. Containers/services that previously collected
`./logs` must set `LOG_DIR` to an absolute writable mounted directory and update
their log collection configuration. An unwritable directory fails explicitly;
there is no silent fallback to CWD. Static files and both template roots resolve
relative to the installed Python modules, not the process working directory.

---

## Key Generation

### SECRET_KEY

```bash
python -c "from secrets import token_urlsafe; print(token_urlsafe(64))"
```

### CREDENTIALS_KEY (Fernet)

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## Configuration Precedence

1. Environment variables (highest priority)
2. `.env` file
3. Default values in `Settings` class

Extra variables in `.env` are ignored (`extra="ignore"`).

Programmatic constructor values retain Pydantic's precedence above environment
variables. The import-time `load_dotenv()` compatibility behavior is unchanged;
no nested env prefixes or renamed keys are introduced.

`Settings.database`, `.security`, `.collection` and `.llm` return fresh frozen
subsystem snapshots. Their fields are projections of already-resolved flat
settings, not new env/default declarations. Secret-bearing view fields use
`SecretStr` and require explicit `.get_secret_value()` at an execution boundary.
Views intentionally exclude tenant plans, member ceilings and billing policy.

For logs/diagnostics use `settings.safe_dict()`, not `model_dump()` or the raw
flat secret attributes. Sensitive fields are hidden from Settings repr; legacy
attribute types and `model_dump()` semantics remain compatible (and therefore
are **not** a safe logging API). The existing payment secret remains excluded
from dumps. Do not persist or expose unwrapped secret values.


---

## Platform credentials

Two kinds of secret, resolved by `app/services/social/credentials.py`:

- **Personal L2 secrets** (VK `user_token`, Telegram MTProto `api_id` /
  `api_hash` / `session`) live in the `user_credentials` table, keyed by
  `users.id`, encrypted with Fernet under `CREDENTIALS_KEY`. Manage via CLI:
  ```bash
  python -m cli.main credentials login telegram --user <id>
  python -m cli.main credentials oauth vk --user <id> --tenant owner
  python -m cli.main credentials list --user <id>
  python -m cli.main credentials test --user <id>
  ```
- **Application / infrastructure config** (VK `app_id`, `client_secret`,
  service token; Telegram/MAX bot tokens) is one-per-deployment and read from
  the **environment** (`.env`), not from the DB.

See also: [COLLECTION.md](COLLECTION.md) — Credential vault.


## Notification recipient boundaries

`TELEGRAM_ADMIN_CHAT_ID` is used only by the fixed operator-alert catalog, not
as a fallback for workspace notifications. Keep it in deployment configuration
for an operator-only chat. A workspace messenger send needs an explicit active
owned binding and workspace context (or an explicitly selected trusted operator
workspace). Report/trend helpers without a recipient fail closed.

The legacy `send_critical_alert` signature discards title/message/error details
and emits a generic operator event. No new env variable or schema migration is
needed. These rules apply to the notification service, not to the independently
reviewed digest transport. See [notification contract and setup impact](NOTIFICATIONS.md).


## Shared platform configuration

`Platform.params` is the only runtime source for VK/Telegram API configuration.
An example VK record contains:

```json
{"api_base_url": "https://api.vk.com/method", "api_version": "5.199", "auth_type": "oauth"}
```

VK also stores `oauth_base_url`, `oauth_token_url` and `redirect_uri` in this JSON;
OAuth authorize/exchange/refresh consume it, with no hard-coded token endpoint
or callback override. Telegram uses `api_base_url` and `auth_type`. Manage the shared record through
SQLAdmin under the existing platform action grants, not `/app/settings`.
`base_url` remains public-link metadata, distinct from the API endpoint.
`platform_type` is globally unique. Defaults are materialized on creation, not
reapplied when reading a persisted record. Inactive, missing or malformed config
blocks provider requests instead of falling back to a process-wide value.
Deployment transport timeouts, application identity and credentials keep their
existing settings/vault ownership; `params` must not contain new credentials.

### Migration 0120 and rollback

Before applying to a working schema, back up the database and validate 0120 on
`test_schema`; production/public application still needs separate authorization.
Current schema has no Platform user/tenant ownership column. Legacy per-user-like
variants are multiple names with the same type; migration groups them by type,
retains the lowest ID and remaps source foreign keys without changing source IDs.
Non-conflicting JSON objects are merged recursively, preserving custom values.
A duplicate's explicit inactive status keeps the shared record inactive.

Conflicting values (including nested values), differing public URLs, unknown
foreign-key references or colliding `(tenant, platform, external_id)` source
identities abort before writes. Reconcile explicitly and retry; no silent
last-user-wins merge, secret printing or automatic source deletion occurs.
The original records and source mapping are retained as inert reserved
`params._migration_0120` rollback evidence, never consulted by runtime clients.
Do not edit this reserved key. Downgrade restores exact records/references only
when config and source identities still match the migration snapshot; otherwise
it refuses destructive rollback and requires a reviewed export/reconciliation or
backup restore. Empty-catalog rollback also requires a backup.

`VK_API_BASE_URL`, `VK_API_VERSION`, `VK_OAUTH_BASE_URL`, `VK_REDIRECT_URI` and
`TELEGRAM_API_BASE_URL` remain accepted only
as legacy **one-time migration inputs** for missing JSON keys. Existing JSON wins.
They do not override records or act as runtime fallbacks after migration; change
the shared JSON to reconfigure an existing platform. No provider calls occur in
migration. Newly created records use the application's bootstrap defaults.
