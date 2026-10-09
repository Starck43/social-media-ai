"""Telegram Bot API channel: sendMessage + getUpdates long polling.

Docs: https://core.telegram.org/bots/api
Limits: text up to 4096 chars; ~30 messages/sec globally; 429 → retry_after.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Optional

import httpx

from app.channels.base import Inbound
from app.channels.delivery_parts import REJECTED_HTTP_STATUSES, part_result, valid_part
from app.core.config import settings

logger = logging.getLogger(__name__)

API_BASE = settings.TELEGRAM_API_BASE_URL.rstrip("/")
MAX_TEXT_LEN = 4096


def split_message(text: str, limit: int = MAX_TEXT_LEN) -> list[str]:
    """Split long text into chunks <= limit, preferring paragraph boundaries."""
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


class TelegramChannel:
    name = "telegram"

    def __init__(self, token: Optional[str] = None):
        self.token = token or settings.TELEGRAM_BOT_TOKEN
        self._offset: int = 0

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _api(self, method: str) -> str:
        return f"{API_BASE}/bot{self.token}/{method}"

    async def send(
        self,
        chat_id: str,
        text: str,
        parse_mode: str | None = "HTML",
    ) -> dict[str, Any]:
        """Send text to a chat/channel, splitting long messages. Rate-limits aware."""
        if not self.enabled:
            return {"success": False, "error": "Telegram token not configured"}

        results: dict[str, Any] = {"success": True, "message_ids": []}
        async with httpx.AsyncClient(timeout=30.0) as client:
            for i, chunk in enumerate(split_message(text)):
                payload: dict[str, Any] = {
                    "chat_id": chat_id,
                    "text": chunk,
                    "disable_web_page_preview": True,
                }
                if parse_mode and i == 0:
                    payload["parse_mode"] = parse_mode
                try:
                    resp = await client.post(self._api("sendMessage"), json=payload)
                    data = resp.json()
                    if resp.status_code == 429:
                        retry_after = int(data.get("parameters", {}).get("retry_after", 1))
                        logger.warning(f"Telegram 429, sleeping {retry_after}s")
                        await asyncio.sleep(retry_after)
                        resp = await client.post(self._api("sendMessage"), json=payload)
                        data = resp.json()
                    if resp.status_code != 200 or not data.get("ok"):
                        results["success"] = False
                        results["error"] = data.get("description", f"HTTP {resp.status_code}")
                        logger.error(f"Telegram send failed: {results['error']}")
                        break
                    results["message_ids"].append(data["result"]["message_id"])
                    await asyncio.sleep(0.05)  # stay under global rate limit
                except httpx.HTTPError as e:
                    results["success"] = False
                    results["error"] = str(e)
                    logger.error(f"Telegram send error: {e}")
                    break
        if results["message_ids"]:
            results["message_id"] = results["message_ids"][-1]
        return results

    async def send_part(self, chat_id: str, text: str, parse_mode: str | None = "HTML") -> dict[str, Any]:
        """Send exactly one frozen part once; no split, truncation or HTTP retry.

        Future checkpoint caller owns authorization, intent/receipt persistence,
        serialization and pacing. Cancellation leaves its persisted intent in-flight.
        """
        if not self.enabled:
            return part_result("blocked", error_code="transport_not_configured")
        if not valid_part(chat_id, text, MAX_TEXT_LEN) or parse_mode not in (None, "HTML"):
            return part_result("blocked", error_code="invalid_part")
        payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(self._api("sendMessage"), json=payload)
            data = response.json()
        except httpx.HTTPError:
            return part_result("uncertain", error_code="transport_error")
        except ValueError:
            return part_result("uncertain", error_code="invalid_response")
        if not isinstance(data, dict):
            return part_result("uncertain", error_code="invalid_response")
        result = data.get("result")
        message_id = result.get("message_id") if isinstance(result, dict) else None
        if response.status_code == 200 and data.get("ok") is True and type(message_id) is int and message_id > 0:
            return part_result("sent", message_id=str(message_id))
        if (
            response.status_code in REJECTED_HTTP_STATUSES
            and data.get("ok") is False
            and type(data.get("error_code")) is int
            and data["error_code"] == response.status_code
        ):
            return part_result("rejected", error_code=f"http_{response.status_code}")
        return part_result("uncertain", error_code="unconfirmed_response")

    async def poll(self) -> AsyncIterator[Inbound]:
        """Long-poll updates (messages, channel posts, edited messages ignored)."""
        if not self.enabled:
            return
        async with httpx.AsyncClient(timeout=40.0) as client:
            while True:
                try:
                    resp = await client.get(
                        self._api("getUpdates"),
                        params={"offset": self._offset, "timeout": 25, "allowed_updates": '["message","channel_post"]'},
                    )
                    data = resp.json()
                    if not data.get("ok"):
                        logger.error(f"Telegram getUpdates error: {data}")
                        await asyncio.sleep(3)
                        continue
                    for upd in data.get("result", []):
                        self._offset = max(self._offset, upd["update_id"] + 1)
                        msg = upd.get("message") or upd.get("channel_post")
                        if not msg or not msg.get("text"):
                            continue
                        yield Inbound(
                            channel="telegram",
                            chat_id=str(msg["chat"]["id"]),
                            user_id=str(msg.get("from", {}).get("id", msg["chat"]["id"])),
                            text=msg["text"],
                            is_channel_post="channel_post" in upd,
                            raw=upd,
                        )
                except asyncio.CancelledError:
                    return
                except httpx.HTTPError as e:
                    logger.error(f"Telegram poll error: {e}")
                    await asyncio.sleep(3)
