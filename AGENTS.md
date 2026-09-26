# AGENTS.md — Social Media AI Analytics

AI-powered social media monitoring platform (VK, Telegram, MAX): AI-анализ
контента, автокомментирование ботом, ежедневные отчёты, персональный агент в
личном чате мессенджера.

Currently being reshaped into a personal agent runtime: cron-driven collection,
scheduled digests delivered to a Telegram/MAX channel, and chat with the agent
in a private chat. Celery/Redis/RBAC are frozen, not deleted — the runtime no
longer depends on them.

## Stack
- Python 3.12+, FastAPI, SQLAlchemy 2.0 + Alembic, PostgreSQL, Docker Compose.
- Runtime: DB-backed cron scheduler + `jobs` table queue (no broker), long
  polling for Telegram/MAX bots (no public port needed).
- Legacy/frozen (not exercised by the runtime): Celery + Redis, Streamlit
  frontend, FastAPI API + sqladmin admin (optional).

## Layout
- `app/` — backend: `api/v1`, `models`, `schemas`, `services` (ai/monitoring/notifications/social/user/digest), `core`, `templates`, `static`
- `app/runtime.py` — entrypoint: scheduler loop + worker loop + channels listener in one process
- `app/worker.py` — worker-only entrypoint (for a separate container/unit)
- `app/scheduler/` — cron parsing, due-schedule tick, default schedules
- `app/jobs/` — job dispatcher (claim/retry/backoff) and handlers
- `app/channels/` — Telegram/MAX bot channels (send + long polling); the listener also feeds channel posts into collection
- `app/services/social/` — platform API clients; `credentials.py` resolves tokens from the per-tenant vault (env = legacy fallback), `tg_session.py` manages the Telegram MTProto (L2) user session. Collection layer is chosen per source via `Source.params["mode"]` — see `docs/COLLECTION.md`
- `app/services/monitoring/` — `collector.py` (pull: VK/API) and `ingest.py` (push: Telegram Bot API updates)
- `app/services/digest/` — digest aggregation, LLM summary, rendering
- `app/services/llm/` — universal LLM clients (OpenAI-compatible + Anthropic)
- `app/agent/` — agent runtime: session, tools, prompts, memory
- `app/web/` — client UI under `/app` (Jinja2 + HTMX/Alpine/Tailwind CDN): auth,
  base layout; `TenantUIMiddleware` resolves web memberships → tenant scope
  (`PlatformScopeMiddleware` bypasses everything except `/app/*`)
- `app/celery/` — frozen legacy, do not extend
- `cli/` — Typer CLI (`cli.main:app`): `schedule`, `digest`, `roles`, `credentials`
- `migrations/` — Alembic; table creation is Alembic-only, run `alembic upgrade head`
- `scripts/` — seed-скрипты и утилиты
- `tests/` — pytest
- `docs/` — project documentation (committed); index: `docs/DOCS_INDEX.md`, scheduler: `docs/AGENT_TASKS.md`, digest: `docs/DIGEST.md`, agent: `docs/AGENT.md`, tenancy: `docs/TENANCY.md`, collection/credentials: `docs/COLLECTION.md`

## Conventions
- Formatting: black + isort, line-length 120 (config in `pyproject.toml`)
- No plan/milestone numbers (M6.5, M7, …) in code comments, docstrings, DB column
  comments, admin labels/titles or file names; milestone context lives in docs and
  `.agent/` only.
- The pre-existing code is tab-indented; new and migrated files are
  black-formatted. Do not run a repo-wide reformat.
- Tests: `pytest` (asyncio_mode = auto, coverage on `app/` by default via addopts)
- Migrations: Alembic (`alembic.ini`); keep `alembic check` clean
- Config via env vars / `.env` — never commit `.env*`
- Optional integrations (VK, Telegram, MAX, Redis) must stay startable when
  unconfigured — the app has to boot with none of them set
- Commit messages, code, and committed docs in English; user-facing agent
  replies in Russian

## LLM Providers — universal approach

LLM configuration lives in the database, NOT in code. Two API formats are
supported; adding a new provider is a row in the DB, not a code change.

### Data model

| Table | Scope | Purpose |
|-------|-------|---------|
| `llm_providers` | global (optionally per-tenant override) | Connection: `name`, `api_format` (`openai` \| `anthropic`), `base_url`, `auth_header`, `encrypted_api_key`, `is_active`, `is_default` |
| `llm_models` | global | Model definition: `provider_id`, `name` (human), `model_id` (API model string), `model_type` (`text` \| `image` \| `embedding`), `input_cost_per_1k`, `output_cost_per_1k`, `max_tokens`, `default_temperature`, `is_active` |
| `tenant_credentials` | per-tenant | Optional tenant-specific provider overrides, encrypted with Fernet under `CREDENTIALS_KEY` |

`LLMProvider` and `LLMModel` are **global** (see `TENANCY.md`) because the same
provider fleet serves all workspaces. Tenants may override via
`tenant_credentials` if they bring their own keys.

### Two API formats, one interface

`app/services/llm/client.py` exposes `LLMClient.chat(messages, model, **kwargs)`
with two concrete backends:

- **`OpenAICompatibleClient`** — `POST {base_url}/chat/completions`,
  `Authorization: Bearer {key}`. Covers OpenAI, DeepSeek, Ollama, vLLM,
  Together AI, Groq, OpenRouter, any vendor with an OpenAI-shaped API.
- **`AnthropicClient`** — `POST {base_url}/messages`,
  `x-api-key: {key}`, `anthropic-version: 2023-06-01`. Covers Claude family.

Adding a new provider = adding an `llm_providers` row with the matching
`api_format`. No code change.

### Resolution order

When the agent/analyzer asks for a model:

1. Explicit model name passed by caller → find by `llm_models.name`.
2. `AGENT_MODEL` env var set → use that model.
3. Otherwise: first active `text`-capable model ordered by
   `llm_providers.is_default DESC, llm_models.id ASC`.

### Fallback

A broken LLM must never break the digest, agent reply, or collection analysis.
If a call raises a non-4xx error (timeout, 5xx, network), the client walks the
fallback chain: active providers ordered by `is_default`, same model type, and
retries on the next one. A 4xx (auth, quota) is raised as-is so config problems
surface instead of silently routing to a fallback.

### Encryption at rest

API keys are stored in `llm_providers.encrypted_api_key` encrypted with Fernet
using the same `CREDENTIALS_KEY` as `tenant_credentials`. The master key comes
from the env (`CREDENTIALS_KEY`) and is **never** committed. Admin UI shows the
key masked; the edit form accepts a new plaintext and re-encrypts on save.
Reading the column through ORM returns the ciphertext; use
`LLMProvider.decrypted_key()` helper when making a request.

### Admin UI

`sqladmin` views for `LLMProviderAdmin` and `LLMModelAdmin` live in
`app/admin/llm.py`. Each provider has a **"Test connection"** action that
sends a 10-token ping to the configured model and reports latency or the
underlying error.

### Cost tracking

Every LLM call stores `request_tokens`, `response_tokens`, `estimated_cost`
(USD cents) and `provider_type` on the `ai_analytics` row. `ReportAggregator`
rolls this up per provider/model for the digest and the dashboard. See
`docs/ANALYTICS_AGGREGATION_SYSTEM.md`.

## Commands
- Install: `pip install -r requirements.txt`
- Migrate: `alembic upgrade head`
- Run runtime: `python -m app.runtime` (from repo root — `app.main` uses cwd-relative paths)
- Run worker only: `python -m app.worker`
- Run API (optional): `uvicorn app.main:app`
- Schedules: `python -m cli.main schedule list|add|remove|pause`
- Digest now: `python -m cli.main digest send-now day|week`
- Credentials: `python -m cli.main credentials set|list|test|disable`
- Tests: `pytest` (from project root)
