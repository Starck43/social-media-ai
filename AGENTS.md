# AGENTS.md — Social Media AI Analytics

AI-powered social media monitoring platform (VK, Telegram): AI-анализ контента (DeepSeek), автокомментирование ботом, ежедневные отчёты.

## Stack
- Python 3.10+, FastAPI, SQLAlchemy 2.0 + Alembic, PostgreSQL, Redis, Celery, Streamlit dashboard, Docker Compose.

## Layout
- `app/` — backend: `api/v1`, `models`, `schemas`, `services` (ai/monitoring/notifications/social/user), `core`, `frontend` (Streamlit), `templates`, `static`
- `cli/` — Typer CLI (`cli.main:app`), `cli/scheduler.py`
- `migrations/` — Alembic
- `scripts/` — seed-скрипты и утилиты
- `tests/` — pytest
- `docs/` — project documentation (committed); index: `docs/DOCS_INDEX.md`, architecture: `docs/architecture.md`

## Conventions
- Formatting: black + isort, line-length 120 (config in `pyproject.toml`)
- Tests: `pytest` (asyncio_mode = auto, coverage on `app/` by default via addopts)
- Migrations: Alembic (`alembic.ini`)
- Config via env vars / `.env` — never commit `.env*`; secrets of platforms live in env, not DB

## Commands
- Install: `pip install -r requirements.txt`
- Run all: `docker-compose up --build`
- Tests: `pytest` (from project root)
