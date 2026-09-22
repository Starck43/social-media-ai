"""MAX Bot API channel: /messages + /updates long polling.

Docs: https://dev.max.ru (use platform-api2.max.ru).
Limits: text up to 4000 chars; max 2 messages/sec per chat; auth via
`Authorization: <access_token>` header.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Optional

import httpx

from app.channels.base import Inbound
from app.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://platform-api2.max.ru"
MAX_TEXT_LEN = 4000
# MAX allows 2 messages/sec per chat — keep a safe interval between chunks
SEND_INTERVAL_SECONDS = 0.6


def split_message(text: str, limit: int = MAX_TEXT_LEN) -> list[str]:
    """Split long text into chunks <= limit on paragraph/line boundaries."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n\n", 0, limit)
        if cut == -1:
            cut = text.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return [c for c in chunks if c.strip()]


class MaxChannel:
    name = "max"

    def __init__(self, token: Optional[str] = None, api_base: Optional[str] = None):
        self.token = token or settings.MAX_BOT_TOKEN
        self.api_base = (api_base or settings.MAX_API_BASE or DEFAULT_API_BASE).rstrip("/")

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self.token or "", "Content-Type": "application/json"}

    async def send(
        self,
        chat_id: str,
        text: str,
        parse_mode: str | None = "html",
    ) -> dict[str, Any]:
        """Send a message/post to a chat or channel, splitting long texts."""
        if not self.enabled:
            return {"success": False, "error": "MAX token not configured"}

        results: dict[str, Any] = {"success": True, "message_ids": []}
        async with httpx.AsyncClient(timeout=30.0) as client:
            for i, chunk in enumerate(split_message(text)):
                payload: dict[str, Any] = {"text": chunk[:MAX_TEXT_LEN], "notify": True}
                if parse_mode and i == 0:
                    payload["format"] = "html" if parse_mode.lower() == "html" else "markdown"
                try:
                    resp = await client.post(
                        f"{self.api_base}/messages?chat_id={chat_id}",
                        json=payload,
                        headers=self._headers(),
                    )
                    if resp.status_code == 429:
                        await asyncio.sleep(1.0)
                        resp = await client.post(
                            f"{self.api_base}/messages?chat_id={chat_id}",
                            json=payload,
                            headers=self._headers(),
                        )
                    if resp.status_code != 200:
                        results["success"] = False
                        results["error"] = f"HTTP {resp.status_code}: {resp.text[:300]}"
                        logger.error(f"MAX send failed: {results['error']}")
                        break
                    body = resp.json() or {}
                    msg = body.get("message") or body
                    results["message_ids"].append(msg.get("body", {}).get("mid") or msg.get("mid"))
                    await asyncio.sleep(SEND_INTERVAL_SECONDS)
                except httpx.HTTPError as e:
                    results["success"] = False
                    results["error"] = str(e)
                    logger.error(f"MAX send error: {e}")
                    break
        if results["message_ids"]:
            results["message_id"] = results["message_ids"][-1]
        return results

    async def poll(self) -> AsyncIterator[Inbound]:
        """Long-poll updates via GET /updates (marker-based)."""
        if not self.enabled:
            return
        marker: Optional[str] = None
        async with httpx.AsyncClient(timeout=45.0) as client:
            while True:
                try:
                    params: dict[str, Any] = {"timeout": 30, "types": "message_created,message_edited"}
                    if marker:
                        params["marker"] = marker
                    resp = await client.get(f"{self.api_base}/updates", params=params, headers=self._headers())
                    if resp.status_code != 200:
                        logger.error(f"MAX updates error: HTTP {resp.status_code}: {resp.text[:200]}")
                        await asyncio.sleep(3)
                        continue
                    data = resp.json() or {}
                    marker = data.get("marker")
                    for upd in data.get("updates", []):
                        if upd.get("update_type") != "message_created":
                            continue
                        msg = upd.get("message", {})
                        sender = msg.get("sender", {})
                        chat = msg.get("recipient", {})
                        text = (msg.get("body") or {}).get("text", "")
                        if not text:
                            continue
                        yield Inbound(
                            channel="max",
                            chat_id=str(chat.get("chat_id", "")),
                            user_id=str(sender.get("user_id", "")),
                            text=text,
                            is_channel_post=str(chat.get("chat_type", "")) == "channel",
                            raw=upd,
                        )
                except asyncio.CancelledError:
                    return
                except httpx.HTTPError as e:
                    logger.error(f"MAX poll error: {e}")
                    await asyncio.sleep(3)
