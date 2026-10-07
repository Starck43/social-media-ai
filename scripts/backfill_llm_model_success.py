#!/usr/bin/env python3
"""Backfill last_success_at from ai_analytics for LLM models.

Usage: python scripts/backfill_llm_model_success.py
"""
import asyncio
from datetime import datetime, timezone

from app.core.database import async_session_maker
from app.models import LLMModel, AIAnalytics
from sqlalchemy import select, func


async def backfill():
    async with async_session_maker() as s:
        # Get max created_at per llm_model name from ai_analytics
        stmt = select(
            AIAnalytics.llm_model,
            func.max(AIAnalytics.created_at).label("last_success")
        ).where(
            AIAnalytics.llm_model.isnot(None)
        ).group_by(AIAnalytics.llm_model)

        result = await s.execute(stmt)
        rows = result.all()

        updated = 0
        for model_name, last_success in rows:
            model = await LLMModel.objects.get(name=model_name)
            if model and model.last_success_at is None:
                # ai_analytics.created_at is tz-aware; llm_models columns are
                # plain DateTime — normalise to naive UTC before writing.
                if last_success is not None and last_success.tzinfo is not None:
                    last_success = last_success.astimezone(timezone.utc).replace(tzinfo=None)
                await LLMModel.objects.update_by_id(model.id, last_success_at=last_success)
                updated += 1
                print(f"Updated {model_name}: last_success_at = {last_success}")

        print(f"Backfill complete. Updated {updated} models.")


if __name__ == "__main__":
    asyncio.run(backfill())