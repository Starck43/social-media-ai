"""SQL shape checks for permission-filtered home schedule queries."""

import asyncio
from datetime import datetime, timezone


def test_upcoming_query_without_source_view_does_not_reference_source_tables(monkeypatch):
    # Import only when this test runs. The real DB acceptance module must first
    # resolve and validate its approved target during collection.
    from sqlalchemy import select

    from app.models.scheduling.agent_task import AgentTask
    from app.models.managers.agent_task_manager import AgentTaskManager

    captured = {}

    class Query:
        def order_by(self, *expressions):
            return self

        def limit(self, count):
            return self

        def __await__(self):
            async def result():
                return []

            return result().__await__()

    manager = AgentTaskManager()

    def capture_filter(*criteria):
        captured["criteria"] = criteria
        return Query()

    monkeypatch.setattr(manager, "filter", capture_filter)
    asyncio.run(
        manager.upcoming_effective(
            now=datetime.now(timezone.utc),
            include_source_based=False,
        )
    )

    statement = select(AgentTask).where(*captured["criteria"])
    sql = str(statement.compile(compile_kwargs={"literal_binds": True})).lower()
    assert ".sources" not in sql
    assert "agent_task_sources" not in sql
