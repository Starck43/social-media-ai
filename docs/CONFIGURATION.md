# Configuration Reference

All configuration is done via environment variables. Load them from a `.env`
file or pass directly to the process. **Never commit `.env` files.**

The application reads config from `app/core/config.py` (Pydantic `Settings`).

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
POSTGRES_URL=postgresql+asyncpg://user:pass@localhost:5432/social_media
DB_SCHEMA=public

# ─── Redis (optional — only for frozen Celery / sqladmin storage) ─
REDIS_URL=redis://localhost:6379/0

# ─── JWT / Auth ───────────────────────────────────────────────
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=43200    # 30 days in dev, overridden to 60 in prod
REFRESH_TOKEN_EXPIRE_DAYS=30
LOGIN_TIMEOUT_MINUTES=15             # lockout duration after max attempts
MAX_LOGIN_ATTEMPTS=5

# ─── Password Policy ──────────────────────────────────────────
PASSWORD_MIN_LENGTH=8
PASSWORD_REQUIRE_UPPERCASE=true
PASSWORD_REQUIRE_LOWERCASE=true
PASSWORD_REQUIRE_NUMBERS=true
PASSWORD_REQUIRE_SPECIAL=false

# ─── Rate Limiting ────────────────────────────────────────────
RESET_PASSWORD_RATE_LIMIT=3/hour
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

# Telegram (Bot API)
TELEGRAM_BOT_TOKEN=
TELEGRAM_API_ID=          # L2 MTProto
TELEGRAM_API_HASH=        # L2 MTProto
TELEGRAM_SESSION=         # L2 MTProto (StringSession, never printed)
TELEGRAM_ADMIN_CHAT_ID=   # legacy: default chat for admin notifications

# Telegram (Channels)
TELEGRAM_OWNER_IDS=       # comma-separated Telegram user ids for agent chat
TELEGRAM_DIGEST_CHANNEL_ID=  # target channel/chat for scheduled digests

# MAX
MAX_BOT_TOKEN=
MAX_API_URL=https://platform-api2.max.ru
MAX_OWNER_ID=             # MAX user id for agent chat
MAX_CHANNEL_ID=           # target channel/chat for scheduled digests

# ─── Tenancy ──────────────────────────────────────────────────
CREDENTIALS_KEY=          # Fernet key — generate with:
                          # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
DEFAULT_TENANT_SLUG=owner

# ─── Scheduler ────────────────────────────────────────────────
SCHEDULER_ENABLED=true
SCHEDULER_POLL_SECONDS=30
SCHEDULER_TIMEZONE=Europe/Moscow

# ─── Agent ────────────────────────────────────────────────────
AGENT_MODEL=              # explicit LLMModel name; empty = auto-resolve first active text model
AGENT_HISTORY_LIMIT=20    # messages kept in agent context per session
AGENT_MAX_ITERATIONS=6    # max tool-call rounds per user message
AGENT_DAILY_COST_LIMIT=5.0  # USD cap (agent + digests combined) per UTC day
AGENT_MAX_TOKENS=1024     # reply size cap for chat
AGENT_TEMPERATURE=0.4

# ─── Background Jobs ──────────────────────────────────────────
JOB_MAX_ATTEMPTS=3
JOB_RETRY_BACKOFF_SECONDS=300

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
| `ALLOWED_ORIGINS` | `http://localhost:3000,http://localhost:8000` | CORS allowed origins (comma-separated) |

### Database

| Variable | Default | Description |
|---|---|---|
| `POSTGRES_URL` | *(required)* | Async PostgreSQL connection string (`postgresql+asyncpg://user:pass@host:port/db`) |
| `DB_SCHEMA` | `public` | PostgreSQL schema name |

### Redis

| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379/0` | Only needed by frozen Celery / sqladmin storage. Optional. |

### JWT / Authentication

| Variable | Default | Description |
|---|---|---|
| `ALGORITHM` | `HS256` | JWT signing algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `43200` (30 days) | Overridden to `60` (1 hour) in production |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `30` | Refresh token lifetime |
| `LOGIN_TIMEOUT_MINUTES` | `15` | Lockout duration after max failed attempts |
| `MAX_LOGIN_ATTEMPTS` | `5` | Max failed login attempts before lockout |

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
| `RESET_PASSWORD_RATE_LIMIT` | `3/hour` | Rate limit for password reset endpoint |
| `CHANGE_PASSWORD_RATE_LIMIT` | `5/minute` | Rate limit for change password endpoint |

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
| `TELEGRAM_BOT_TOKEN` | `None` | Telegram bot token |
| `TELEGRAM_API_ID` | `None` | Telegram API ID (L2 MTProto) |
| `TELEGRAM_API_HASH` | `None` | Telegram API hash (L2 MTProto) |
| `TELEGRAM_SESSION` | `None` | Telegram StringSession (L2 MTProto) |
| `TELEGRAM_ADMIN_CHAT_ID` | `None` | Legacy: default chat for admin notifications |
| `TELEGRAM_OWNER_IDS` | `""` | Comma-separated Telegram user IDs allowed to chat with the agent |
| `TELEGRAM_DIGEST_CHANNEL_ID` | `""` | Target channel/chat for scheduled digests |
| `MAX_BOT_TOKEN` | `None` | MAX bot access token |
| `MAX_API_BASE` | `https://platform-api2.max.ru` | MAX API base URL |
| `MAX_OWNER_ID` | `""` | MAX user ID allowed to chat with the agent |
| `MAX_CHANNEL_ID` | `""` | Target channel/chat for scheduled digests |

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
| `AGENT_MODEL` | `None` | Explicit LLMModel name. Empty = auto-resolve first active text-capable model. |
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

See also: [COLLECTION.md](./COLLECTION.md) — Credential vault.
