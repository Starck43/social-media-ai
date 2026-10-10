"""Standalone regression tests for whitespace-only inbound texts.

Run: python tests/test_agent_runtime_whitespace.py. No DB or HTTP acceptance.

A whitespace-only messenger message used to pass `handle_inbound`'s truthiness
guard, lose everything to `.strip()` inside `_handle_authorized_turn`, and then
crash on `text.split()[0]` with IndexError after the identity was already
admitted. The web ingress already rejected empty text; these tests pin both
ingresses and the turn guard.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # noqa: E402

from app.agent import runtime as agent_runtime  # noqa: E402
from app.channels.base import Inbound  # noqa: E402


def _inbound(text: str, **overrides) -> Inbound:
    params = {"channel": "telegram", "chat_id": "7", "user_id": "7", "text": text}
    params.update(overrides)
    return Inbound(**params)


class WhitespaceInboundTests(unittest.IsolatedAsyncioTestCase):
    async def test_whitespace_only_inbound_is_ignored_before_resolution(self):
        resolve = AsyncMock(return_value=None)
        with patch.object(agent_runtime, "resolve_inbound", resolve):
            result = await agent_runtime.handle_inbound(_inbound("   \t\n  "))
        self.assertIsNone(result)
        resolve.assert_not_awaited()

    async def test_empty_inbound_still_ignored(self):
        resolve = AsyncMock(return_value=None)
        with patch.object(agent_runtime, "resolve_inbound", resolve):
            result = await agent_runtime.handle_inbound(_inbound(""))
        self.assertIsNone(result)
        resolve.assert_not_awaited()

    async def test_non_whitespace_inbound_still_reaches_resolution(self):
        resolve = AsyncMock(return_value=None)
        with patch.object(agent_runtime, "resolve_inbound", resolve):
            result = await agent_runtime.handle_inbound(_inbound("привет"))
        self.assertIsNone(result)
        resolve.assert_awaited_once()

    async def test_authorized_turn_refuses_whitespace_before_command_parsing(self):
        resolution = SimpleNamespace(tenant_id=1, onboarded=False)
        identity = SimpleNamespace()
        result = await agent_runtime._handle_authorized_turn(_inbound("  \t "), resolution, identity)
        self.assertIsNone(result)

    async def test_web_message_whitespace_still_ignored(self):
        result = await agent_runtime.handle_web_message("   ", tenant_id=1, user_id=7)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
