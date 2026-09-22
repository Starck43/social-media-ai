# AGENTS.md — Social Media AI Analytics

AI-powered social media monitoring platform (VK, Telegram): AI-анализ контента (DeepSeek), автокомментирование ботом, ежедневные отчёты.

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
- `app/runtime.py` — entrypoint: scheduler loop + worker loop in one process
- `app/worker.py` — worker-only entrypoint (for a separate container/unit)
- `app/scheduler/` — cron parsing, due-schedule tick, default schedules
- `app/jobs/` — job dispatcher (claim/retry/backoff) and handlers
- `app/channels/` — Telegram/MAX bot channels (send + long polling)
- `app/services/digest/` — digest aggregation, LLM summary, rendering
- `app/celery/` — frozen legacy, do not extend
- `cli/` — Typer CLI (`cli.main:app`): `schedule`, `digest`, `roles`
- `migrations/` — Alembic; table creation is Alembic-only, run `alembic upgrade head`
- `scripts/` — seed-скрипты и утилиты
- `tests/` — pytest
- `docs/` — project documentation (committed); index: `docs/DOCS_INDEX.md`, architecture: `docs/architecture.md`, scheduler: `docs/SCHEDULER.md`, digest: `docs/DIGEST.md`

## Conventions
- Formatting: black + isort, line-length 120 (config in `pyproject.toml`)
- The pre-existing code is tab-indented; new and migrated files are
  black-formatted. Do not run a repo-wide reformat.
- Tests: `pytest` (asyncio_mode = auto, coverage on `app/` by default via addopts)
- Migrations: Alembic (`alembic.ini`); keep `alembic check` clean
- Config via env vars / `.env` — never commit `.env*`; secrets of platforms live in env, not DB
- Optional integrations (VK, Telegram, MAX, Redis) must stay startable when
  unconfigured — the app has to boot with none of them set
- Commit messages, code, and committed docs in English; user-facing agent
  replies in Russian

## Commands
- Install: `pip install -r requirements.txt`
- Migrate: `alembic upgrade head`
- Run runtime: `python -m app.runtime` (from repo root — `app.main` uses cwd-relative paths)
- Run worker only: `python -m app.worker`
- Run API (optional): `uvicorn app.main:app`
- Schedules: `python -m cli.main schedule list|add|remove|pause`
- Digest now: `python -m cli.main digest send-now day|week`
- Tests: `pytest` (from project root)
