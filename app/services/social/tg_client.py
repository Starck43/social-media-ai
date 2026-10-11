"""
Telegram collection client: L1 push contract + L2 MTProto pull (Telethon).

The Bot API (L1) cannot read history, so pull collection is only meaningful in
`user` mode through an authorized MTProto session (Telethon). Sessions are
resolved from the personal vault (`app.services.social.tg_session`, keyed by the
source's owner user) and telethon is imported lazily — the app boots without it
installed.

Official documentation: https://docs.telethon.dev/
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.config import settings
from app.models import Source
from app.services.social.base import BaseClient
from app.types import SourceType

logger = logging.getLogger(__name__)

# Source.params["mode"] values (see docs/COLLECTION.md).
MODE_API = "api"  # L1: Bot API push via listener -> ingest; pull is a no-op
MODE_USER = "user"  # L2: MTProto user session pull (Telethon)
MODE_AUTO = "auto"  # default: L2 pull when an MTProto session exists, else L1 push


class TelegramClient(BaseClient):
	"""
	Client for Telegram: content collection and bot-side sending.

	Collection is hybrid, decided per source by `Source.params["mode"]`:
	- `api` (L1): no pull — the Bot API delivers only new updates to
	  the listener, which feeds `services/monitoring/ingest.py`; pulling here
	  would re-analyze pushes and burn LLM tokens, so it returns no items.
	- `user` (L2): MTProto pull of everything newer than `Source.last_item_id`
	  (watermark shared with the push path); `force_refresh` + `cli_dates`
	  widen the window for a one-off backfill.
	- `auto` (default): L2 pull whenever an MTProto session is configured,
	  otherwise fall back to L1 (push-based, returns no items).

	`send_message` writes through the Bot API and is unrelated to the layer.
	"""

	async def collect_data(self, source: Source, content_type: str = "posts") -> list[Any] | list[dict | None]:
		"""Route the source to its collection layer (L1 no-op, L2 MTProto pull)."""
		mode = (source.params or {}).get("mode", MODE_AUTO)
		if mode == MODE_USER:
			return await self._collect_mtproto(source)

		if mode == MODE_AUTO:
			from app.services.social.tg_session import load_session

			if await load_session() is not None:
				logger.info(f"Telegram source {source.id} in auto mode - L2 session available, pulling via MTProto")
				return await self._collect_mtproto(source)
			logger.info(f"Telegram source {source.id} in auto mode - no L2 session, falling back to L1 (push)")

		if mode not in (MODE_API, MODE_AUTO):
			logger.warning(f"Unknown Telegram mode {mode!r} for source {source.id} - treating as api")
		logger.info(f"Telegram source {source.id} is push-based (L1) - pull collection skipped")
		return []

	async def _collect_mtproto(self, source: Source) -> list[Any] | list[dict | None]:
		"""L2 pull: fetch messages newer than the last durably admitted watermark.

		A missing session raises `AuthorizationRequired`: the job reports the
		source as failed, the rest of the run continues, and the operator gets
		told why — as opposed to an empty list, which reads as "nothing new".
		"""
		from app.services.social.credentials import AuthorizationRequired
		from app.services.social.owner import resolve_source_owner
		from app.services.social.tg_session import build_client, load_session

		owner_user_id = await resolve_source_owner(source)
		session = await load_session(user_id=owner_user_id)
		if session is None:
			raise AuthorizationRequired(
				f"Нужна Telegram-сессия для источника {source.name}",
				platform="telegram",
				hint="Подключите Telegram в настройках, чтобы читать закрытые чаты и историю.",
			)

		params = source.params or {}
		collection = params.get("collection") or {}
		limit = int(collection.get("limit", params.get("limit", 100)))
		force = bool(params.get("force_refresh"))

		# The watermark is the primary cursor; dates only matter for backfills
		# (first run or force_refresh), so a normal run costs nothing new.
		min_id = 0 if force else self._watermark_id(source)
		min_date = None if min_id else self._pull_start_date(source)

		try:
			client = build_client(session)
		except ImportError:
			logger.error("Telethon is not installed - L2 Telegram collection unavailable")
			return []

		messages: list[Any] = []
		try:
			await client.connect()
			if not await client.is_user_authorized():
				raise AuthorizationRequired(
					f"Telegram-сессия источника {source.name} истекла или отозвана",
					platform="telegram",
					hint="Подключите Telegram заново в настройках.",
				)
			entity = await self._resolve_entity(client, source)
			if entity is None:
				return []
			async for message in client.iter_messages(entity, limit=limit, min_id=min_id, offset_date=min_date, reverse=True):
				messages.append(message)
		except AuthorizationRequired:
			# Raised above for an unauthorized session. It must survive the broad
			# catch below: it is a fact the operator has to act on, not a glitch
			# to log and turn into an empty pull.
			raise
		except Exception as e:  # noqa: BLE001 - one source must not sink the job
			logger.error(f"Telegram L2 collection failed for source {source.id}: {e}", exc_info=True)
			return []
		finally:
			try:
				await client.disconnect()
			except Exception:  # noqa: BLE001
				pass

		items = [item for item in (self._normalize_message(m, source) for m in messages) if item]
		if not items:
			logger.info(f"Telegram L2 source {source.id}: no new messages")
			return []

		# The collector advances the cursor only in its staging transaction.
		logger.info(f"Telegram L2 source {source.id}: pulled {len(items)} messages")
		return items

	@staticmethod
	def _watermark_id(source: Source) -> int:
		try:
			return int((source.params or {}).get("telegram_l2_last_item_id", getattr(source, "last_item_id", None)) or 0)
		except (TypeError, ValueError):
			return 0

	def _pull_start_date(self, source: Source) -> Optional[datetime]:
		"""Lower date bound for a first/forced L2 pull (no watermark yet)."""
		params = source.params or {}
		cli_dates = params.get("cli_dates") or {}
		start = cli_dates.get("start_date") if cli_dates else None
		if not start:
			start = getattr(source, "last_checked", None) or getattr(source, "date_from", None)
		if not start:
			return None
		try:
			return self._convert_to_datetime(start)
		except Exception as e:  # noqa: BLE001
			logger.warning(f"Cannot parse Telegram pull start date {start!r}: {e}")
			return None

	@staticmethod
	def _entity_ref(external_id: str) -> Any:
		"""Map a Telegram `external_id` to something Telethon can resolve.

		Bot API chat ids and MTProto peer ids differ: channels are stored as
		`-100<channel_id>` (or the bare id), basic groups as negative ints,
		public chats as @username or t.me links.
		"""
		ref = external_id.strip()
		if ref.startswith("-100") and ref[4:].isdigit():
			from telethon.tl.types import PeerChannel

			return PeerChannel(int(ref[4:]))
		if ref.lstrip("-").isdigit():
			number = int(ref)
			if number < 0:
				from telethon.tl.types import PeerChat

				return PeerChat(-number)
			from telethon.tl.types import PeerUser

			return PeerUser(number)
		if "t.me/" in ref:
			ref = ref.split("t.me/", 1)[1].strip("/")
		if ref and not ref.startswith("@"):
			ref = "@" + ref
		return ref

	async def _resolve_entity(self, client: Any, source: Source) -> Any:
		ref = self._entity_ref(source.external_id or "")
		try:
			return await client.get_entity(ref)
		except (ValueError, TypeError) as e:
			logger.error(
				f"Telegram entity {source.external_id!r} not resolvable for source {source.id}: {e} - "
				"is the session account a member of that chat?"
			)
			return None

	@staticmethod
	def _media_placeholders(media: Any) -> list[dict[str, Any]]:
		"""Recognize direct MTProto photos/videos without resolving file references.

		A caption is not proof that its media was analyzed. Missing references
		must reach staging as placeholders, never as token-bearing Bot API URLs.
		Unsupported documents/web previews remain outside this bounded contract.
		"""
		kind = media.get("_") if type(media) is dict else type(media).__name__
		if kind == "MessageMediaPhoto":
			return [{"type": "image", "url": None}]
		if kind != "MessageMediaDocument":
			return []
		document = media.get("document") if type(media) is dict else getattr(media, "document", None)
		mime = document.get("mime_type") if type(document) is dict else getattr(document, "mime_type", None)
		attributes = document.get("attributes") if type(document) is dict else getattr(document, "attributes", None)
		video_attribute = any(
			(attribute.get("_") if type(attribute) is dict else type(attribute).__name__) == "DocumentAttributeVideo"
			for attribute in attributes if attribute is not None
		) if type(attributes) is list else False
		if (type(mime) is str and mime.lower().startswith("video/")) or video_attribute:
			return [{"type": "video", "url": None}]
		return []

	def _normalize_message(self, message: Any, source: Source) -> Optional[dict]:
		"""Telethon Message -> the shared normalized content-item contract.

		Keys match `ingest.normalize_channel_post` and `_normalize_response` so
		the analyzer cannot tell L1 and L2 items apart. The `external_id` is
		built from `Source.external_id` (the Bot API chat id the push path also
		uses), because `dedup.item_hash` hashes it: the same post arriving via
		push and later pulled via L2 must produce the same hash, so overlap
		never pays twice.
		"""
		text = getattr(message, "text", None) or getattr(message, "message", None)
		attachments = self._media_placeholders(getattr(message, "media", None))
		if not text and not attachments:
			return None  # service/unsupported media: no eligible content
		text = text or ""

		peer = getattr(message, "peer_id", None)
		chat_id = (
			getattr(message, "chat_id", None) or getattr(peer, "channel_id", None) or getattr(peer, "chat_id", None) or ""
		)

		reactions_count = 0
		reactions = getattr(message, "reactions", None)
		for result in getattr(reactions, "results", None) or []:
			reactions_count += getattr(result, "count", 0) or 0

		replies = getattr(message, "replies", None)
		source_type = source.source_type
		item = {
			"id": str(message.id),
			"external_id": f"{source.external_id or chat_id}_{message.id}",
			"text": text,
			"date": getattr(message, "date", None) or datetime.now(timezone.utc),
			"views": getattr(message, "views", 0) or 0,
			"forwards": getattr(message, "forwards", 0) or 0,
			"reactions": reactions_count,
			"comments": getattr(replies, "replies", 0) if replies else 0,
			"metric_availability": {
				"reactions": reactions is not None,
				"comments": replies is not None,
				"views": getattr(message, "views", None) is not None,
			},
			"source_type": getattr(source_type, "value", source_type) or "channel",
			"platform": "telegram",
			"message_type": "post" if getattr(message, "is_channel", False) else "message",
			"from_id": getattr(message, "sender_id", None),
			"peer_id": chat_id,
			"is_pinned": bool(getattr(message, "pinned", False)),
			"edit_date": getattr(message, "edit_date", None),
		}
		if getattr(message, "media", None):
			item["has_media"] = True
			item["media_type"] = type(message.media).__name__
		if attachments:
			item["attachments"] = attachments
		return item

	def _extract_items_from_response(self, response: dict) -> list:
		if not response:
			return []
		return response.get("messages", []) if isinstance(response, dict) else list(response)

	def _should_stop_pagination(self, response: dict, items: list, page_size: int, total_collected: int) -> bool:
		return len(items) < page_size

	def _build_paginated_response(self, all_items: list) -> dict:
		return {"messages": all_items}

	def _get_api_method(self, source_type: SourceType, content_type: str) -> str:
		"""Telegram has no REST method map: L2 pull is `iter_messages`, L1 is push.

		Present only to satisfy `BaseClient`; `collect_data` is overridden and
		never enters the shared httpx collection path.
		"""
		return "iter_messages"

	def _build_params(self, source: Source, method: str) -> dict:
		"""Collection knobs for the L2 pull, read from `Source.params`.

		Same abstract-method note as `_get_api_method`. Date cursors live in
		`_pull_start_date`, the message-id cursor in `Source.last_item_id`.
		"""
		collection = (source.params or {}).get("collection") or {}
		return {"entity": source.external_id, "limit": int(collection.get("limit", 100))}

	def _normalize_response(self, raw_data: dict, source_type: SourceType) -> list[dict[str, Any]]:
		"""
		Normalize Telegram API response to unified format.

		Converts Telethon message objects to common format:
		- id: Message ID
		- text: Message text content
		- date: Publication datetime
		- reactions: Reaction count
		- views: View count
		- forwards: Forward count

		Args:
						raw_data: Raw response from Telethon (list of messages)
						source_type: Type of source

		Returns:
						List of normalized content items
		"""
		# Telethon returns list of Message objects or dict representation
		messages = raw_data.get("messages", []) if isinstance(raw_data, dict) else raw_data

		if not messages:
			logger.info("No messages in Telegram response")
			return []

		normalized = []
		for msg in messages:
			try:
				# Handle both dict and object responses
				if hasattr(msg, "to_dict"):
					msg = msg.to_dict()

				# Extract message content
				text = msg.get("message", "") or msg.get("text", "")

				# Extract engagement metrics
				views = msg.get("views", 0) or 0
				forwards = msg.get("forwards", 0) or 0

				# Reactions (if available)
				reactions_data = msg.get("reactions", {})
				reactions_count = 0
				if reactions_data and isinstance(reactions_data, dict):
					results = reactions_data.get("results", [])
					reactions_count = sum(r.get("count", 0) for r in results)

				# Build normalized item
				normalized_item = {
					"id": str(msg.get("id", "")),
					"external_id": f"{msg.get('peer_id', {})}_{msg.get('id', '')}",
					"text": text,
					"date": (
						msg.get("date")
						if isinstance(msg.get("date"), datetime)
						else datetime.fromtimestamp(msg.get("date", 0))
					),
					# Engagement metrics
					"views": views,
					"forwards": forwards,
					"reactions": reactions_count,
					"comments": msg.get("replies", {}).get("replies", 0) if msg.get("replies") else 0,
					"metric_availability": {
						"reactions": isinstance(msg.get("reactions"), dict),
						"comments": isinstance(msg.get("replies"), dict),
						"views": msg.get("views") is not None,
					},
					"replies": msg.get("replies", {}).get("replies", 0) if msg.get("replies") else 0,
					# Metadata
					"source_type": source_type.value if source_type else "unknown",
					"platform": "telegram",
					"message_type": "post" if msg.get("post") else "message",
					# Additional Telegram-specific fields
					"from_id": msg.get("from_id"),
					"peer_id": msg.get("peer_id"),
					"is_pinned": msg.get("pinned", False),
					"edit_date": msg.get("edit_date"),
				}

				# Include media info if present
				if msg.get("media"):
					normalized_item["has_media"] = True
					media = msg["media"]
					if hasattr(media, "__class__"):
						normalized_item["media_type"] = media.__class__.__name__
					elif isinstance(media, dict):
						normalized_item["media_type"] = media.get("_", "unknown")
					attachments = self._media_placeholders(media)
					if attachments:
						normalized_item["attachments"] = attachments

				normalized.append(normalized_item)

			except Exception as e:
				logger.error(f"Error normalizing Telegram message: {e}", exc_info=True)
				continue

		logger.info(f"Normalized {len(normalized)} Telegram messages from {len(messages)} raw messages")
		return normalized

	async def send_message(
			self,
			chat_id: int | str,
			text: str,
			dry_run: bool = True,
	) -> dict:
		"""Send a message to a Telegram chat.

		Args:
				chat_id: Chat ID or username
				text: Message text
				dry_run: If True, return payload without sending (default True)

		Returns:
				Dict with 'success', 'payload', and optionally 'message_id'
		"""
		payload = {
			"chat_id": chat_id,
			"text": text,
		}

		if dry_run:
			logger.info(f"[DRY RUN] Telegram send_message: chat={chat_id}")
			return {"success": True, "dry_run": True, "payload": payload}

		try:
			from app.services.social.credentials import resolve_token

			token = await resolve_token("telegram", kind="bot_token")
			if not token:
				return {"success": False, "error": "No Telegram bot token available"}

			import httpx

			api_base = settings.TELEGRAM_API_BASE_URL.rstrip("/")
			timeout = settings.TELEGRAM_REQUEST_TIMEOUT
			async with httpx.AsyncClient() as client:
				resp = await client.post(
					f"{api_base}/bot{token}/sendMessage",
					json=payload,
					timeout=timeout,
				)
				data = resp.json()

			if not data.get("ok"):
				return {"success": False, "error": data.get("description", "Unknown error")}

			message_id = data.get("result", {}).get("message_id")
			return {"success": True, "dry_run": False, "message_id": message_id, "payload": payload}

		except Exception as e:
			logger.error(f"Telegram send_message failed: {e}", exc_info=True)
			return {"success": False, "error": str(e)}
