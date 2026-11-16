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
from app.services.social.platform_config import PlatformConfigurationError, load_platform_config

logger = logging.getLogger(__name__)

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

    def __init__(self, token: Optional[str] = None, *, expected_api_base: str | None = None):
        self.token = token or settings.TELEGRAM_BOT_TOKEN
        self._offset: int = 0
        self._expected_api_base = expected_api_base

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    async def _api(self, method: str) -> str:
        config = await load_platform_config("telegram")
        api_base = config["api_base_url"].rstrip("/")
        if self._expected_api_base is not None and api_base != self._expected_api_base:
            raise PlatformConfigurationError("platform_endpoint_changed")
        return f"{api_base}/bot{self.token}/{method}"

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
                    resp = await client.post(await self._api("sendMessage"), json=payload)
                    data = resp.json()
                    if resp.status_code == 429:
                        retry_after = int(data.get("parameters", {}).get("retry_after", 1))
                        logger.warning(f"Telegram 429, sleeping {retry_after}s")
                        await asyncio.sleep(retry_after)
                        resp = await client.post(await self._api("sendMessage"), json=payload)
                        data = resp.json()
                    if resp.status_code != 200 or not data.get("ok"):
                        results["success"] = False
                        results["error"] = data.get("description", f"HTTP {resp.status_code}")
                        logger.error(f"Telegram send failed: {results['error']}")
                        break
                    results["message_ids"].append(data["result"]["message_id"])
                    await asyncio.sleep(0.05)  # stay under global rate limit
                except PlatformConfigurationError:
                    results["success"] = False
                    results["error"] = "Platform configuration unavailable"
                    break
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
            api_url = await self._api("sendMessage")
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(api_url, json=payload)
            data = response.json()
        except httpx.HTTPError:
            return part_result("uncertain", error_code="transport_error")
        except PlatformConfigurationError:
            return part_result("blocked", error_code="platform_configuration_unavailable")
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
                        await self._api("getUpdates"),
                        params={"offset": self._offset, "timeout": 25, "allowed_updates": '["message","channel_post"]'},
                    )
                    data = resp.json()
                    if not data.get("ok"):
                        logger.error(f"Telegram getUpdates error: {data}")
                        await asyncio.sleep(3)
                        continue
                    for upd in data.get("result", []):
                        next_offset = max(self._offset, upd["update_id"] + 1)
                        msg = upd.get("message") or upd.get("channel_post")
                        if not msg or ("channel_post" not in upd and not msg.get("text")):
                            self._offset = next_offset  # intentional non-content skip
                            continue
                        yield Inbound(
                            channel="telegram",
                            chat_id=str(msg["chat"]["id"]),
                            user_id=str(msg.get("from", {}).get("id", msg["chat"]["id"])),
                            text=msg.get("text") or msg.get("caption") or "",
                            is_channel_post="channel_post" in upd,
                            raw=upd,
                        )
                        # Resume means the consumer completed durable ingest.
                        # Closing/cancelling at yield keeps this update unacked.
                        self._offset = next_offset
                except PlatformConfigurationError:
                    logger.warning("Telegram platform configuration unavailable")
                    return
                except asyncio.CancelledError:
                    return
                except httpx.HTTPError as e:
                    logger.error(f"Telegram poll error: {e}")
                    await asyncio.sleep(3)
