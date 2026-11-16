from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert

from app.core.database import async_session_maker
from app.core.tenant_context import TenantContextError, current_tenant_id

from .base_manager import BaseManager

if TYPE_CHECKING:
    from app.services.ai.output_contracts import LearnedFacts

    from ..agent.agent_memory import AgentMemory


class AgentMemoryManager(BaseManager["AgentMemory"]):
    """Personal agent facts with a separate owner-only legacy bucket."""

    def __init__(self):
        from ..agent.agent_memory import AgentMemory

        super().__init__(AgentMemory)

    @staticmethod
    def _actor() -> tuple[int, bool, int]:
        from app.agent.identity import current_memory_actor

        tenant_id = current_tenant_id()
        actor = current_memory_actor()
        if (
            tenant_id is None
            or actor is None
            or type(actor[0]) is not int
            or actor[0] <= 0
            or type(actor[1]) is not bool
        ):
            raise TenantContextError("Agent memory requires an authenticated actor in a tenant scope")
        return actor[0], actor[1], tenant_id

    @staticmethod
    def _unique_target(personal: bool) -> dict[str, object]:
        if personal:
            return {
                "index_elements": ["tenant_id", "user_id", "scope", "key"],
                "index_where": text("user_id IS NOT NULL"),
            }
        return {
            "index_elements": ["tenant_id", "scope", "key"],
            "index_where": text("user_id IS NULL"),
        }

    async def read(self, key: str, scope: str = "global") -> Optional[str]:
        """Read this actor's value; owners may fall back to old legacy facts."""
        from ..agent.agent_memory import AgentMemory

        user_id, is_owner, tenant_id = self._actor()
        row = await self.filter(
            AgentMemory.tenant_id == tenant_id,
            AgentMemory.user_id == user_id,
            AgentMemory.scope == scope,
            AgentMemory.key == key,
        ).first()
        if row is None and is_owner:
            row = await self.filter(
                AgentMemory.tenant_id == tenant_id,
                AgentMemory.user_id.is_(None),
                AgentMemory.scope == scope,
                AgentMemory.key == key,
            ).first()
        return row.value if row else None

    async def write(
        self,
        key: str,
        value: Optional[str],
        scope: str = "global",
        *,
        source: str = "manual",
        confidence: float = 1.0,
        evidence_message_id: Optional[int] = None,
    ) -> None:
        """Upsert this actor's fact; writes never mutate legacy/shared rows."""
        from ..agent.agent_memory import AgentMemory

        user_id, _, tenant_id = self._actor()
        row = await self.filter(
            AgentMemory.tenant_id == tenant_id,
            AgentMemory.user_id == user_id,
            AgentMemory.scope == scope,
            AgentMemory.key == key,
        ).first()
        if row is None:
            if value is not None:
                await self.create(
                    user_id=user_id,
                    scope=scope,
                    key=key,
                    value=value,
                    source=source,
                    confidence=confidence,
                    evidence_message_id=evidence_message_id,
                )
            return
        if value is None:
            await self.delete_by_id(row.id)
            return
        await self.update_by_id(
            row.id,
            value=value,
            source=source,
            confidence=confidence,
            evidence_message_id=evidence_message_id,
        )

    async def apply_learn_batch(
        self,
        batch: "LearnedFacts",
        *,
        expected_watermark: int,
        new_watermark: int,
        authorized_session_ids: set[int],
    ) -> bool:
        """Atomically commit one actor's validated facts and learning cursor."""
        from ..agent.agent_message import AgentMessage

        user_id, _, tenant_id = self._actor()
        if not authorized_session_ids or any(type(value) is not int or value <= 0 for value in authorized_session_ids):
            raise ValueError("Learning requires an explicit set of actor-owned sessions")
        if (
            type(expected_watermark) is not int
            or type(new_watermark) is not int
            or expected_watermark < 0
            or new_watermark <= expected_watermark
        ):
            raise ValueError("Learning watermark must advance from a nonnegative integer")

        memory = self.model
        identity = [
            memory.tenant_id == tenant_id,
            memory.user_id == user_id,
            memory.scope == "meta",
            memory.key == "learn_msg_wm",
        ]
        unique = self._unique_target(personal=True)
        async with async_session_maker() as session:
            try:
                await session.execute(
                    insert(memory)
                    .values(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        scope="meta",
                        key="learn_msg_wm",
                        value="0",
                        source="learn",
                    )
                    .on_conflict_do_nothing(**unique)
                )
                raw = (await session.execute(select(memory.value).where(*identity).with_for_update())).scalar_one()
                try:
                    watermark = int(raw or 0)
                except ValueError:
                    watermark = 0
                if watermark != expected_watermark:
                    await session.rollback()
                    return False

                evidence_ids = {fact.evidence_id for fact in batch.facts}
                if evidence_ids:
                    owned_ids = set(
                        (
                            await session.execute(
                                select(AgentMessage.id).where(
                                    AgentMessage.tenant_id == tenant_id,
                                    AgentMessage.role == "user",
                                    AgentMessage.session_id.in_(authorized_session_ids),
                                    AgentMessage.id.in_(evidence_ids),
                                )
                            )
                        )
                        .scalars()
                        .all()
                    )
                    if owned_ids != evidence_ids:
                        raise ValueError("Learning evidence is no longer an owned user message")
                for fact in batch.facts:
                    statement = insert(memory).values(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        scope="global",
                        key=fact.key,
                        value=fact.value,
                        source="learn",
                        confidence=fact.confidence,
                        evidence_message_id=fact.evidence_id,
                    )
                    await session.execute(
                        statement.on_conflict_do_update(
                            **unique,
                            set_={
                                "value": statement.excluded.value,
                                "source": "learn",
                                "confidence": statement.excluded.confidence,
                                "evidence_message_id": statement.excluded.evidence_message_id,
                                "updated_at": func.now(),
                            },
                        )
                    )
                await session.execute(
                    update(memory)
                    .where(*identity)
                    .values(value=str(new_watermark), source="learn", updated_at=func.now())
                )
                await session.commit()
            except BaseException:
                await session.rollback()
                raise
        return True

    async def as_dict(self, scope: str = "global") -> dict[str, str]:
        """Return personal facts; owners also receive the legacy fact bucket."""
        rows = await self.snapshot(scope=scope, limit=1000)
        values: dict[str, str] = {}
        for row in reversed(rows):
            if row.value is not None:
                values[row.key] = row.value
        return values

    async def snapshot(
        self,
        limit: int = 20,
        *,
        scope: str | None = None,
        include_legacy: bool = True,
    ) -> list["AgentMemory"]:
        """Bounded actor snapshot; legacy NULL rows are visible only to owners."""
        from ..agent.agent_memory import AgentMemory

        user_id, is_owner, tenant_id = self._actor()
        clauses = [
            AgentMemory.tenant_id == tenant_id,
            AgentMemory.scope != "meta",
            AgentMemory.user_id == user_id,
        ]
        if scope is not None:
            clauses.append(AgentMemory.scope == scope)
        personal = list(await self.filter(*clauses).order_by(AgentMemory.confidence.desc()).limit(limit))
        if not include_legacy or not is_owner or len(personal) >= limit:
            return personal
        personal_keys = {row.key for row in personal}
        legacy_clauses = [
            AgentMemory.tenant_id == tenant_id,
            AgentMemory.user_id.is_(None),
            AgentMemory.scope != "meta",
        ]
        if scope is not None:
            legacy_clauses.append(AgentMemory.scope == scope)
        if personal_keys:
            legacy_clauses.append(~AgentMemory.key.in_(personal_keys))
        legacy = list(
            await self.filter(*legacy_clauses).order_by(AgentMemory.confidence.desc()).limit(limit - len(personal))
        )
        return personal + legacy

    async def legacy_snapshot(self, limit: int = 20, *, scope: str | None = None) -> list["AgentMemory"]:
        """Read legacy shared facts only for the current workspace owner."""
        from ..agent.agent_memory import AgentMemory

        _, is_owner, tenant_id = self._actor()
        if not is_owner:
            return []
        clauses = [
            AgentMemory.tenant_id == tenant_id,
            AgentMemory.user_id.is_(None),
            AgentMemory.scope != "meta",
        ]
        if scope is not None:
            clauses.append(AgentMemory.scope == scope)
        rows = await self.filter(*clauses).order_by(AgentMemory.confidence.desc()).limit(limit)
        return list(rows)

    async def delete_fact_by_id(self, row_id: int) -> bool:
        """Delete only a fact visible to this actor, including owner's legacy."""
        from ..agent.agent_memory import AgentMemory

        user_id, is_owner, tenant_id = self._actor()
        criteria = [AgentMemory.id == row_id, AgentMemory.tenant_id == tenant_id]
        criteria.append(
            ((AgentMemory.user_id == user_id) | AgentMemory.user_id.is_(None))
            if is_owner
            else AgentMemory.user_id == user_id
        )
        row = await self.filter(*criteria).first()
        if row is None:
            return False
        await self.delete_by_id(row.id)
        return True

    async def update_fact_by_id(self, row_id: int, **values) -> bool:
        """Update only a fact visible to this actor."""
        from ..agent.agent_memory import AgentMemory

        user_id, is_owner, tenant_id = self._actor()
        criteria = [AgentMemory.id == row_id, AgentMemory.tenant_id == tenant_id]
        criteria.append(
            ((AgentMemory.user_id == user_id) | AgentMemory.user_id.is_(None))
            if is_owner
            else AgentMemory.user_id == user_id
        )
        row = await self.filter(*criteria).first()
        if row is None:
            return False
        await self.update_by_id(row.id, **values)
        return True

    async def clear_facts(self) -> int:
        """Clear this actor's facts; only owners may clear the legacy bucket."""
        from ..agent.agent_memory import AgentMemory

        user_id, is_owner, tenant_id = self._actor()
        criteria = [
            AgentMemory.tenant_id == tenant_id,
            AgentMemory.scope != "meta",
            AgentMemory.user_id == user_id,
        ]
        if is_owner:
            criteria[-1] = (AgentMemory.user_id == user_id) | AgentMemory.user_id.is_(None)
        result = await self.filter(*criteria).delete()
        return int(result or 0)


agent_memory = AgentMemoryManager()
