# Setup & Architecture

This guide summarizes how to run the app, key components, and where to look in the codebase.

## Project Overview
- FastAPI backend under `app/`
- Async SQLAlchemy + Postgres (`app/core/database.py`)
- Admin (SQLAdmin + custom views) under `app/admin/`
- Dashboard API under `app/api/v1/endpoints/dashboard.py`
- AI services under `app/services/ai/`
- Enums/types under `app/types/`

See also: `docs/architecture.md` for full tree and CLI usage examples.

## Quick Start
```bash
# deps
pip install -r requirements.txt

# run API
uvicorn app.main:app --reload

# docker
docker-compose up --build
```

## Configuration
- Env vars via `.env` (see `README.md` and `docs/architecture.md`).
- Database URL and credentials required.

## Data Model Highlights
- `app/models/`
  - `User`, `Role` with `UserRoleType`
  - `Platform`, `Source` (platform_type via `PlatformType`)
  - `AIAnalytics` (aggregated analysis payloads, evolution fields like `topic_chain_id`)
  - `LLMProvider` (type, models, costs, capabilities)

## Key Enums
- `app/types/enums/`
  - `UserRoleType`, `ActionType`
  - `PlatformType`, `SourceType`, `MonitoringStatus`
  - `ContentType`, `MediaType`
  - `AnalysisType`, `SentimentLabel`, `PeriodType`
  - `LLMProviderType`, `LLMStrategyType`

## High-level Flow
1. Sources (VK/Telegram/etc.) ingested -> `AIAnalytics`
2. AI analyzer uses unified PromptBuilder to analyze text/images/video and produce summary
3. Aggregation endpoints summarize analytics for dashboards
4. Admin UI renders dashboards via HTML templates + JS
