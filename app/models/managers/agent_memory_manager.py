from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.database import async_session_maker
from app.core.tenant_context import TenantContextError, current_tenant_id

from .base_manager import BaseManager

if TYPE_CHECKING:
    from app.services.ai.output_contracts import LearnedFacts

    from ..agent_memory import AgentMemory


class AgentMemoryManager(BaseManager["AgentMemory"]):
    """Durable key/value facts the agent keeps about itself and its owner."""

    def __init__(self):
        from ..agent_memory import AgentMemory

        super().__init__(AgentMemory)

    async def read(self, key: str, scope: str = "global") -> Optional[str]:
        """Value for `key`, or None when never set."""
        row = await self.get(scope=scope, key=key)
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
        """Upsert a fact (value=None deletes it), stamping its provenance."""
        row = await self.get(scope=scope, key=key)
        if row is None:
            if value is not None:
                await self.create(
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

    async def apply_learn_batch(self, batch: "LearnedFacts", *, expected_watermark: int, new_watermark: int) -> bool:
        """Commit validated facts and an advancing cursor in one tenant transaction.

        The meta row serializes concurrent learners, including its first insert.
        False means a stale watermark and no committed writes. Database errors
        propagate; a lost commit acknowledgement is not proof of rollback.
        Manual writes/clear and reflection are not serialized by this method.
        """
        tenant_id = current_tenant_id()
        if tenant_id is None:
            raise TenantContextError("Learning persistence requires a concrete tenant")
        if (
            type(expected_watermark) is not int
            or type(new_watermark) is not int
            or expected_watermark < 0
            or new_watermark <= expected_watermark
        ):
            raise ValueError("Learning watermark must advance from a nonnegative integer")

        from ..agent_message import AgentMessage

        memory = self.model
        identity = [memory.tenant_id == tenant_id, memory.scope == "meta", memory.key == "learn_msg_wm"]
        unique_key = ["tenant_id", "scope", "key"]
        async with async_session_maker() as session:
            try:
                # The existing unique key also serializes learners with no meta row yet.
                await session.execute(
                    insert(memory)
                    .values(tenant_id=tenant_id, scope="meta", key="learn_msg_wm", value="0", source="learn")
                    .on_conflict_do_nothing(index_elements=unique_key)
                )
                raw = (await session.execute(select(memory.value).where(*identity).with_for_update())).scalar_one()
                try:
                    watermark = int(raw or 0)
                except ValueError:
                    watermark = 0  # Match the existing get_watermark compatibility rule.
                if watermark != expected_watermark:
                    await session.rollback()  # Also undo a just-created meta row.
                    return False

                evidence_ids = {fact.evidence_id for fact in batch.facts}
                if evidence_ids:
                    owned_ids = set(
                        (
                            await session.execute(
                                select(AgentMessage.id).where(
                                    AgentMessage.tenant_id == tenant_id,
                                    AgentMessage.role == "user",
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
                        scope="global",
                        key=fact.key,
                        value=fact.value,
                        source="learn",
                        confidence=fact.confidence,
                        evidence_message_id=fact.evidence_id,
                    )
                    await session.execute(
                        statement.on_conflict_do_update(
                            index_elements=unique_key,
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
        """All facts in a scope — rendered into the system prompt."""
        rows = await self.filter(scope=scope)
        return {row.key: row.value for row in rows if row.value is not None}

    async def snapshot(self, limit: int = 20) -> list["AgentMemory"]:
        """Most trustworthy facts for the system prompt (everything but internal meta)."""
        from ..agent_memory import AgentMemory

        rows = await self.filter(AgentMemory.scope != "meta").order_by(AgentMemory.confidence.desc()).limit(limit)
        return list(rows)

    async def clear_facts(self) -> int:
        """Drop all learned/stored facts but keep meta (watermarks). Returns deleted count."""
        rows = await self.filter(scope="global")
        for row in rows:
            await self.delete_by_id(row.id)
        return len(rows)


agent_memory = AgentMemoryManager()
