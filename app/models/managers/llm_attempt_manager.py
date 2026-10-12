"""Insert-only manager for the LLM attempt journal."""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError

from .base_manager import BaseManager


class LLMAttemptManager(BaseManager):
    """Journal access: duplicate-safe, create-only.

    The journal is evidence, not state, so there is deliberately no update or
    delete flow. `record_attempt` is idempotent through the unique
    `attempt_key`: a retried persistence (worker retry, repeated save, late
    notification) cannot duplicate or silently rewrite a paid attempt.
    """

    def __init__(self):
        from ..analysis.llm_attempt import LLMAttempt

        super().__init__(LLMAttempt)

    async def record_attempt(self, record: dict[str, Any]) -> bool:
        """Record one attempt; False when the ledger already holds it."""
        existing = await self.get(attempt_key=record["attempt_key"])
        if existing is not None:
            return False
        try:
            await self.create(**record)
        except IntegrityError:
            # A concurrent writer stored the same attempt first; the ledger is
            # already correct, so the duplicate is not a failure.
            return False
        return True
