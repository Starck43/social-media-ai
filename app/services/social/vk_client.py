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
from app.services.social.credentials import resolve_token
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

    Credentials come from the tenant vault (`app.services.social.credentials`):
    `vk/user_token` first, then `vk/service_token`, with env as the legacy
    fallback. The token is resolved once per run in `collect_data`.
    """

    # Filled by collect_data() before any request parameters are built.
    _access_token: str | None = None
    # Resolved numeric external_id (screen_name -> id) for the current run.
    _resolved_external_id: str | None = None

    async def collect_data(self, source: Source, content_type: str = "posts") -> list[dict]:
        """
        Resolve the tenant's VK token and the source's external_id, then
        delegate to the shared collector.

        Vault first, env fallback. Without a token VK answers every method with
        error 5 (auth failed), so we stop before spending a request and let the
        caller record the source as failed.

        A screen_name (e.g. `russkikh_natalia`) is resolved to a numeric ID once
        here so that `_build_params` can send a valid `owner_id` to the VK API.
        The numeric result is kept per-run; the stored `external_id` on the
        Source row is left untouched (either numeric or screen_name works).
        """
        self._access_token = await resolve_token("vk")
        if not self._access_token:
            logger.error(f"No VK credential available for source {source.id} - collection skipped")
            return []

        self._resolved_external_id = await self._resolve_external_id(source.external_id)
        logger.info(f"VK credential ready for source {source.id}")
        return await super().collect_data(source, content_type)

    def _token(self) -> str | None:
        """Resolved token; falls back to the legacy env key when unresolved."""
        return self._access_token or settings.VK_SERVICE_ACCESS_TOKEN

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
                    attachment_types = [att.get("type") for att in item["attachments"]]
                    normalized_item["has_attachments"] = True
                    normalized_item["attachment_types"] = attachment_types

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
