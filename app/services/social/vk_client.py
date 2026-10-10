"""
VK API client for collecting posts, comments, and other content.

An official documentation: https://dev.vk.com/ru/reference
"""

import logging
from datetime import datetime
from typing import Any

from app.core.config import settings
from app.models import Source
from app.services.social.base import BaseClient
from app.services.social.credentials import AuthorizationRequired, resolve_token
from app.services.social.owner import resolve_source_owner
from app.types import SourceType
from app.utils.date_parsing import to_unix_timestamp, universal_date_parser
from app.utils.enum_helpers import get_enum_value

logger = logging.getLogger(__name__)


class VKClient(BaseClient):
	"""
	Client for VK API integration.

	Supports:
	— Wall posts collection (wall.get)
	— Comments collection (wall.getComments)
	— User/Group information (users.get, groups.getById)
	— Messages for authorized groups (messages.getHistory)

	Credentials come from the personal vault (`app.services.social.credentials`):
	`vk/user_token` (the workspace owner's, or the token_owner recorded on the
	source) first, then the env `VK_SERVICE_KEY` L1 token. The token is resolved
	once per run in `collect_data`.
	"""

	# Filled by collect_data() before any request parameters are built.
	_access_token: str | None = None
	# Resolved numeric external_id (screen_name -> id) for the current run.
	_resolved_external_id: str | None = None
	# Web user (users.id) whose L2 token this source collects with, from
	# Source.params["token_owner"]; None falls back to the workspace vault/env.
	_token_owner: int | None = None

	# Source.params["mode"] values (see docs/COLLECTION.md).
	MODE_API = "api"  # L1: service token only
	MODE_USER = "user"  # L2: user token only
	MODE_AUTO = "auto"  # default: L1, falling back to L2 on a layer-1 error

	# VK error codes that mean "the L1 (service) token cannot reach this source":
	# 15 — access denied (hidden wall/community), 5 — auth failed. These trigger
	# the automatic L1->L2 (service->user token) fallback in `auto` mode.
	L1_UNAVAILABLE_CODES = {15, 5}

	async def collect_data(self, source: Source, content_type: str = "posts") -> list[dict]:
		"""
		Resolve the tenant's VK token and the source's external_id, then
		delegate to the shared collector.

		Collection layer is chosen per source via `Source.params["mode"]`:
		- `api` (L1): service token only.
		- `user` (L2): user token only.
		- `auto` (default): try L1 (service token) first; if VK answers with a
		  layer-1 error (codes 15/5) the request is retried with the L2 (user)
		  token when one is configured. A screen_name is resolved once per run
		  and kept for both attempts.

		If no layer produces items and the L1 error is not recoverable, the L1
		error is re-raised so the caller can record the source as failed rather
		than silently returning an empty result.
		"""
		mode = (source.params or {}).get("mode", self.MODE_AUTO)
		if mode not in (self.MODE_API, self.MODE_USER, self.MODE_AUTO):
			logger.warning(f"Unknown VK mode {mode!r} for source {source.id} - treating as {self.MODE_AUTO}")

		# Which web user's L2 (user) token this source collects with.
		# `resolve_source_owner` honours an explicit `Source.params["token_owner"]`
		# (validated as a member of this workspace) and otherwise defaults to the
		# workspace owner; None falls back to the environment (L1 service token).
		self._token_owner = None
		if mode in (self.MODE_USER, self.MODE_AUTO):
			self._token_owner = await resolve_source_owner(source)

		self._resolved_external_id = await self._resolve_external_id(source.external_id)

		if mode == self.MODE_USER:
			return await self._collect_with_kinds(source, content_type, ("user_token",))

		l1_token = await resolve_token("vk", kinds=("service_token",))
		if not l1_token:
			if mode == self.MODE_API:
				logger.error(f"No VK service credential available for source {source.id} - collection skipped")
				return []
			# auto: no service token, fall straight to L2
			return await self._collect_with_kinds(source, content_type, ("user_token",))

		self._access_token = l1_token
		try:
			logger.info(f"VK credential ready for source {source.id} (L1 service token)")
			return await super().collect_data(source, content_type)
		except RuntimeError as e:
			if mode == self.MODE_API or not self._is_layer1_unavailable(e):
				raise
			logger.info(f"VK L1 failed for source {source.id}: {e}; retrying with L2 (user token)")
			try:
				items = await self._collect_with_kinds(source, content_type, ("user_token",))
			except AuthorizationRequired as auth_error:
				# Both layers are unusable: L1 cannot see this source *and* nobody
				# has authorized L2. That is one fact for the user, not two —
				# the L1 error is kept in the message so the cause is not lost.
				raise AuthorizationRequired(
					f"{auth_error} ({e})",
					platform=auth_error.platform,
					hint=auth_error.hint,
				) from e
			if items:
				return items
			# L2 yielded nothing (e.g. no user token configured) - surface the
			# real L1 error rather than pretending the source is empty.
			raise

	async def _collect_with_kinds(self, source: Source, content_type: str, kinds: tuple[str, ...]) -> list[dict]:
		"""Run the shared collector with a specific token kind (L1/L2).

		An L2 (user) token can go stale *before* it expires when the deployment's
		IP changes: VK ID binds the access token to the IP it was issued on, so a
		run from another address gets error 5 "another ip address" even though
		the token is far from its expiry. Without a retry the source fails until
		the next hourly renewal. So on a code-5 (IP binding) rejection of a user
		token we force a refresh and retry exactly once — the new token is bound
		to the current IP, which is what the failing request was missing.
		"""
		token = await resolve_token("vk", kinds=kinds, owner_user_id=self._token_owner)
		if not token:
			if kinds == ("user_token",):
				# L2 was requested and there is nobody authorized to provide it.
				# Returning [] here made the source look "collected, just empty",
				# so a misconfigured owner stayed invisible; this is the one case
				# where the operator must be told rather than the run.
				raise AuthorizationRequired(
					f"Нужен личный токен ВКонтакте для источника {source.name}",
					platform="vk",
					hint="Подключите ВКонтакте в настройках, чтобы сбор через личный аккаунт заработал.",
				)
			logger.error(f"No VK service credential available for source {source.id} - collection skipped")
			return []
		self._access_token = token
		logger.info(f"VK credential ready for source {source.id} (L2 user token)")
		try:
			return await super().collect_data(source, content_type)
		except RuntimeError as e:
			if kinds != ("user_token",) or not self._is_ip_binding_error(e) or self._token_owner is None:
				raise
			# The user token is bound to a foreign IP. Renew it (which binds it to
			# the IP this request actually runs from) and retry once.
			logger.info(f"VK L2 token rejected for source {source.id} ({e}); refreshing and retrying once")
			from app.services.social.vk_oauth import refresh_user_token

			refreshed = await refresh_user_token(self._token_owner)
			if not refreshed:
				raise
			self._access_token = refreshed
			return await super().collect_data(source, content_type)

	@staticmethod
	def _is_ip_binding_error(error: RuntimeError) -> bool:
		"""True for VK error 5 whose text is the IP-binding rejection.

		Code 5 alone is ambiguous (invalid/expired token also maps to 5), so the
		message is narrowed to the "another ip address" wording — the one case a
		refresh fixes. Anything else is left to fail so a real auth problem is not
		masked by an unnecessary refresh.
		"""
		return "given to another ip address" in str(error)

	@staticmethod
	def _is_layer1_unavailable(error: RuntimeError) -> bool:
		"""True when the VK error means L1 cannot reach the source (codes 15/5)."""
		msg = str(error)
		for code in VKClient.L1_UNAVAILABLE_CODES:
			if f"({code})" in msg:
				return True
		return False

	def _token(self) -> str | None:
		"""Resolved token; falls back to the legacy env key when unresolved."""
		return self._access_token or settings.VK_SERVICE_KEY

	def _get_api_method(self, source_type: SourceType, content_type: str) -> str:
		"""
		Get VK API method based on source type and content type.

		Args:
				source_type: Type of source (USER, GROUP, CHANNEL, PUBLIC, etc.)
				content_type: Type of content to collect (posts, comments, photos, etc.)

		Returns:
				VK API method name
		"""
		# Group-like methods (same API for all community types)
		group_methods = {
			"posts": "wall.get",
			"comments": "wall.getComments",
			"info": "groups.getById",
		}

		# User-like methods
		user_methods = {
			"posts": "wall.get",
			"comments": "wall.getComments",
			"info": "users.get",
		}

		# Map SourceType to appropriate method groups
		methods = {
			# User types
			SourceType.USER: user_methods,
			SourceType.BOT: user_methods,
			# Group types (VK communities)
			SourceType.GROUP: group_methods,
			SourceType.CHANNEL: group_methods,
			SourceType.PUBLIC: group_methods,
			SourceType.PAGE: group_methods,
			SourceType.EVENT: group_methods,
			SourceType.SUPERGROUP: group_methods,
			# Specialized types
			SourceType.MARKET: {
				"posts": "wall.get",
				"comments": "wall.getComments",
				"info": "groups.getById",
				"products": "market.get",
				"product_info": "market.getById",
			},
			SourceType.ALBUM: {
				"photos": "photos.get",
				"info": "photos.getAlbums",
			},
			SourceType.CHAT: {
				"messages": "messages.getHistory",
				"info": "messages.getConversationById",
			},
			SourceType.BROADCAST: {
				"messages": "messages.getHistory",
				"info": "messages.getConversationById",
			},
		}

		# Get method group for the source type
		method_group = methods.get(source_type, {})

		# Return specific method or default to wall.get
		return method_group.get(content_type, "wall.get")

	async def _resolve_external_id(self, external_id: str) -> str:
		"""
		Convert screen_name to numeric ID if needed.

		Args:
				external_id: Either numeric ID or screen_name

		Returns:
				str: Numeric ID (with minus for groups)
		"""
		# If already numeric (or negative for groups)
		if external_id.lstrip("-").isdigit():
			return external_id

		# Resolve screen_name via VK API
		logger.info(f"Resolving screen_name: {external_id}")
		platform_params = self.platform.params or {}
		api_base = platform_params.get("api_base_url") or settings.VK_API_BASE_URL
		api_version = platform_params.get("api_version") or settings.VK_API_VERSION
		url = f"{api_base}/utils.resolveScreenName"
		params = {"screen_name": external_id, "access_token": self._token(), "v": api_version}

		import httpx

		async with httpx.AsyncClient() as client:
			response = await client.get(url, params=params)
			data = response.json()

			if "response" in data and data["response"]:
				object_id = data["response"]["object_id"]
				object_type = data["response"]["type"]

				# For groups/pages/events, add minus prefix
				if object_type in ["group", "page", "event"]:
					resolved_id = f"-{object_id}"
				else:
					resolved_id = str(object_id)

				logger.info(f"Resolved {external_id} → {resolved_id} (type: {object_type})")
				return resolved_id

			logger.warning(f"Cannot resolve screen_name: {external_id}, using as-is")
			return external_id

	def _build_params(self, source: Source, method: str) -> dict:
		"""
		Build VK API request parameters.

		Constructs parameters according to VK API requirements:
		- owner_id: User ID (positive) or Group ID (negative)
		- domain: Short name of user/group
		- count: Amount posts (max 100)
		- offset: Pagination offset
		- extended: Include additional fields (0 or 1)
		- filter: Type of posts (all, owner, others, suggests)

		Args:
				source: Source object with external_id and params
				method: VK API method name

		Returns:
				Dictionary with request parameters
		"""
		platform_params = self.platform.params or {}
		source_params = source.params.get("collection", {}) if source.params else {}
		force_refresh = source.params.get("force_refresh", False)
		cli_dates = source.params.get("cli_dates", {}) if source.params else {}

		# Base parameters for all requests
		base_params = {
			"access_token": self._token(),
			"v": platform_params.get("api_version") or settings.VK_API_VERSION,  # Configurable API version
		}

		# UNIFIED DATE RANGE LOGIC FOR ALL METHODS
		# Priority 1: force_refresh with start_date - override last_checked
		if force_refresh and cli_dates.get("start_date"):
			logger.info("Force refresh with start_date - overriding last_checked")
			date_from = cli_dates.get("start_date")
			date_to = cli_dates.get("end_date")

		# Priority 2: incremental collection - use last_checked
		elif source.last_checked:
			logger.info(f"Incremental mode - using last_checked: {source.last_checked}")
			date_from = source.last_checked
			date_to = cli_dates.get("end_date")  # Can use CLI end_date in incremental mode

		# Priority 3: first collection or no last_checked - use CLI dates if available
		else:
			date_from = cli_dates.get("start_date")
			date_to = cli_dates.get("end_date")
			if date_from:
				logger.info(f"First collection with CLI dates: {date_from} to {date_to}")
			else:
				logger.info("First collection - no date filters applied")

		# Method-specific parameters
		if method in ("wall.get", "wall.getComments"):
			# Parse owner_id from external_id
			owner_id = self._parse_owner_id(source.external_id, source.source_type)

			params_dict = {
				"owner_id": owner_id,
				"count": source_params.get("count", 50),  # Max 100 posts
				"offset": source_params.get("offset", 0),
				"extended": source_params.get("extended", 1),  # Include additional fields
				"filter": source_params.get("filter", "all"),  # all, owner, others, suggests
			}

			# Apply date filters for wall methods (support start_time/end_time)
			if date_from:
				date_from_dt = universal_date_parser(date_from)
				start_time = to_unix_timestamp(date_from_dt)
				if start_time:
					params_dict["start_time"] = start_time
					logger.info(f"Applied start_time: {date_from} -> {start_time}")

			if date_to:
				date_to_dt = universal_date_parser(date_to)
				end_time = to_unix_timestamp(date_to_dt)
				if end_time:
					params_dict["end_time"] = end_time
					logger.info(f"Applied end_time: {date_to} -> {end_time}")

			base_params.update(params_dict)

		elif method == "users.get":
			# User info request - typically doesn't need date filters
			user_ids = self._parse_owner_id(source.external_id, SourceType.USER)
			base_params.update(
				{
					"user_ids": abs(int(user_ids)),
					"fields": "photo_max,city,verified,followers_count",
				}
			)

		elif method == "groups.getById":
			# Group info request - typically doesn't need date filters
			group_id = abs(int(self._parse_owner_id(source.external_id, SourceType.GROUP)))
			base_params.update(
				{
					"group_id": group_id,
					"fields": "description,members_count,activity,verified",
				}
			)

		elif method == "market.get":
			# Market products request
			owner_id = self._parse_owner_id(source.external_id, source.source_type)
			params_dict = {
				"owner_id": owner_id,
				"count": source_params.get("count", 50),
				"offset": source_params.get("offset", 0),
				"extended": source_params.get("extended", 1),
			}

			# Market methods might need date filters - add if VK API supports
			# Note: VK Market API may have different date parameters
			if date_from:
				logger.debug("Date filters not implemented for market.get method")

			base_params.update(params_dict)

		elif method == "photos.get":
			# Album photos request
			owner_id = self._parse_owner_id(source.external_id, source.source_type)
			params_dict = {
				"owner_id": owner_id,
				"album_id": source_params.get("album_id", "wall"),
				"count": source_params.get("count", 50),
				"offset": source_params.get("offset", 0),
				"extended": source_params.get("extended", 1),
			}

			# Photos methods might need date filters
			if date_from:
				logger.debug("Date filters not implemented for photos.get method")

			base_params.update(params_dict)

		elif method == "messages.getHistory":
			# Chat messages (requires proper access token)
			peer_id = source.external_id
			params_dict = {
				"peer_id": peer_id,
				"count": source_params.get("count", 50),
				"offset": source_params.get("offset", 0),
				"rev": source_params.get("reverse", 0),
			}

			# Messages might need date filters
			if date_from:
				logger.debug("Date filters not implemented for messages.getHistory method")

			base_params.update(params_dict)

		# Log final parameters for debugging
		if date_from or date_to:
			logger.info(f"Final date range: {date_from} to {date_to}")
		else:
			logger.info("No date filters applied - collecting all available data")

		# Merge with any custom source parameters
		return {**base_params, **source_params}

	def _extract_items_from_response(self, response: dict) -> list:
		"""Extract items from VK API response"""
		return response.get("response", {}).get("items", [])

	def _should_stop_pagination(self, response: dict, items: list, page_size: int, total_collected: int) -> bool:
		"""Determine if VK pagination should stop"""
		if not items:
			return True

		total_count = response.get("response", {}).get("count", 0)
		return len(items) < page_size or total_collected >= total_count

	def _build_paginated_response(self, all_items: list) -> dict:
		"""Build VK response structure for normalization"""
		return {"response": {"items": all_items}}

	def _parse_owner_id(self, external_id: str, source_type: SourceType) -> str:
		"""
		Parse VK owner_id from external_id.

		VK uses:
		- Positive IDs for users (e.g., "12345" or "id12345")
		- Negative IDs for groups (e.g., "-12345" or "club12345")

		Args:
				external_id: An external identifier from Source
				source_type: Type of source as SourceType enum

		Returns:
				Formatted owner_id as string
		"""
		# Clean up common prefixes
		clean_id = external_id.strip()
		clean_id = clean_id.replace("id", "").replace("club", "").replace("public", "").replace("event", "")
		clean_id = clean_id.replace("-", "")

		# Ensure numeric
		try:
			numeric_id = int(clean_id)
		except ValueError:
			# A non-numeric screen_name. If we already resolved it to a numeric
			# ID for this run, recurse with the resolved value; otherwise fall
			# back to returning the raw screen_name (the API will reject it).
			resolved = self._resolved_external_id or external_id
			if resolved != external_id and resolved.lstrip("-").isdigit():
				logger.warning(f"Invalid VK ID format: {external_id}, using resolved {resolved}")
				return self._parse_owner_id(resolved, source_type)
			logger.warning(f"Invalid VK ID format: {external_id}, using as-is")
			return external_id

		# Apply negative for all community types
		# VK treats all community types as groups (negative IDs)
		community_types = (
			SourceType.GROUP,
			SourceType.CHANNEL,
			SourceType.PUBLIC,
			SourceType.PAGE,
			SourceType.EVENT,
			SourceType.MARKET,
			SourceType.SUPERGROUP,
			SourceType.BROADCAST,
		)

		# User types get positive IDs
		user_types = (SourceType.USER, SourceType.BOT)

		if source_type in community_types:
			return str(-abs(numeric_id))
		elif source_type in user_types:
			return str(abs(numeric_id))
		else:
			# Default to positive ID for unknown types
			logger.warning(f"Unknown source type {source_type}, using positive ID")
			return str(abs(numeric_id))

	@staticmethod
	def _normalize_direct_attachments(attachments: Any) -> list[dict] | None:
		"""Snapshot direct photos only; never use video previews as media URLs.

		Choose the largest valid size by pixel area (first input wins ties).
		Missing dimensions rank below positive integer dimensions. Rejected or
		unavailable media remains a placeholder under the shared staging policy.
		No downloads, repost traversal, token lookup or provider calls occur here.
		"""
		from app.utils.content_attachments import normalize_attachments

		if type(attachments) is not list:
			return None
		snapshots = []
		for attachment in attachments:
			if type(attachment) is not dict:
				snapshots.append({"type": "unknown", "url": None})
				continue
			media_type = attachment.get("type")
			url = None
			if media_type == "photo":
				photo = attachment.get("photo")
				sizes = photo.get("sizes") if type(photo) is dict else None
				best_area = -1
				for size in sizes if type(sizes) is list else []:
					if type(size) is not dict:
						continue
					ref = normalize_attachments([{"type": "photo", "url": size.get("url")}])[0]["url"]
					if ref is None:
						continue
					width, height = size.get("width"), size.get("height")
					area = width * height if type(width) is int and type(height) is int and width > 0 and height > 0 else 0
					if area > best_area:
						url, best_area = ref, area
			snapshots.append({"type": media_type, "url": url})
		return normalize_attachments(snapshots)

	def _normalize_response(self, raw_data: dict, source_type: SourceType) -> list[dict[str, Any]]:
		"""
		Normalize VK API response to unified format.

		Converts VK-specific fields to common format:
		— ID: Post/comment ID
		— text: Post text content
		— date: Publication datetime
		— reactions: Combined engagement metrics
		— comments: Comment count
		— shares: Repost count
		— views: View count

		Args:
						raw_data: Raw response from VK API
						source_type: Type of source

		Returns:
						List of normalized content items
		"""
		# Handle VK API response format
		if "response" not in raw_data:
			logger.warning(f"Unexpected VK response format: {raw_data}")
			return []

		response = raw_data["response"]
		items = response.get("items", [])

		if not items:
			logger.info("No items in VK response")
			return []

		normalized = []
		for item in items:
			try:
				# Extract engagement metrics
				likes = item.get("likes", {}).get("count", 0)
				comments = item.get("comments", {}).get("count", 0)
				reposts = item.get("reposts", {}).get("count", 0)
				views = item.get("views", {}).get("count", 0)

				# Build normalized item
				normalized_item = {
					"id": str(item.get("id", "")),
					"external_id": f"{item.get('owner_id', '')}_{item.get('id', '')}",
					"text": item.get("text", ""),
					"date": datetime.fromtimestamp(item.get("date", 0)),
					# Engagement metrics
					"likes": likes,
					"comments": comments,
					"shares": reposts,
					"views": views,
					"reactions": likes + comments + reposts,  # Combined metric
					"metric_availability": {
						"reactions": all(
							isinstance(item.get(k), dict) and item[k].get("count") is not None
							for k in ("likes", "comments", "reposts")
						),
						"comments": isinstance(item.get("comments"), dict) and item["comments"].get("count") is not None,
						"views": isinstance(item.get("views"), dict) and item["views"].get("count") is not None,
					},
					# Metadata
					"source_type": get_enum_value(source_type),
					"platform": "vkontakte",
					"post_type": item.get("post_type", "post"),
					# Additional VK-specific fields
					"from_id": item.get("from_id"),
					"owner_id": item.get("owner_id"),
					"is_pinned": item.get("is_pinned", False),
					"marked_as_ads": item.get("marked_as_ads", False),
				}

				# Include attachments info if present
				if "attachments" in item:
					raw_attachments = item["attachments"]
					attachment_types = [att.get("type") if type(att) is dict else None for att in raw_attachments] if type(raw_attachments) is list else []
					normalized_item["has_attachments"] = True
					normalized_item["attachment_types"] = attachment_types
					snapshots = self._normalize_direct_attachments(raw_attachments)
					if snapshots is not None:
						normalized_item["attachments"] = snapshots

				normalized.append(normalized_item)

			except Exception as e:
				logger.error(f"Error normalizing VK item: {e}", exc_info=True)
				continue

		logger.info(f"Normalized {len(normalized)} VK items from {len(items)} raw items")
		return normalized

	async def post_comment(
			self,
			owner_id: int,
			post_id: int,
			message: str,
			dry_run: bool = True,
	) -> dict:
		"""Post a comment to a VK wall post.

		Args:
				owner_id: Owner ID of the post (negative for groups)
				post_id: Post ID
				message: Comment text
				dry_run: If True, return payload without posting (default True)

		Returns:
				Dict with 'success', 'payload', and optionally 'comment_id'
		"""
		payload = {
			"owner_id": owner_id,
			"post_id": post_id,
			"message": message,
		}

		if dry_run:
			logger.info(f"[DRY RUN] VK post_comment: owner={owner_id}, post={post_id}")
			return {"success": True, "dry_run": True, "payload": payload}

		token = self._access_token or await resolve_token("vk")
		if not token:
			return {"success": False, "error": "No VK token available"}

		platform_params = self.platform.params or {}
		api_version = platform_params.get("api_version") or settings.VK_API_VERSION
		params = {**payload, "access_token": token, "v": api_version}
		result = await self._make_request("wall.createComment", params)

		if "error" in result:
			return {"success": False, "error": result["error"].get("error_msg", "Unknown error")}

		comment_id = result.get("response", {}).get("comment_id")
		return {"success": True, "dry_run": False, "comment_id": comment_id, "payload": payload}
