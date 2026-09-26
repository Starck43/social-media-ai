from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from .base_manager import BaseManager

if TYPE_CHECKING:
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
