from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import with_db_session
from app.core.permissions import require_permission
from app.types import ActionType
from app.utils.enum_helpers import get_enum_value

from .base_manager import BaseManager, QuerySet

if TYPE_CHECKING:
    from app.types import SourceType

    from ..collection.source import Source


def _protect_telegram_binding(source, changes):
    """Generic CRUD may preserve a binding, never add/retarget/remove one.

    This protects explicit consent through API/web/agent/CLI manager consumers.
    Clearing and reselection must use the picker, including before target edits.
    The reserved key is rejected regardless of platform to prevent retargeting.
    """
    previous = source.params if type(source.params) is dict else {}
    incoming = changes.get("params", previous)
    if type(incoming) is not dict:
        raise PermissionError("telegram_source_picker_required")
    has_binding = "telegram_authorization" in previous
    if not has_binding and "telegram_authorization" in incoming:
        raise PermissionError("telegram_source_picker_required")
    if not has_binding:
        return
    for key in ("tenant_id", "platform_id", "external_id", "owner_user_id"):
        if key in changes and changes[key] != getattr(source, key):
            raise PermissionError("telegram_source_picker_required")
    for key in ("telegram_authorization", "token_owner", "mode"):
        # JSON type fidelity: true and 1 are not the same consent pin.
        if json.dumps(previous.get(key), sort_keys=True) != json.dumps(incoming.get(key), sort_keys=True):
            raise PermissionError("telegram_source_picker_required")


class SourceManager(BaseManager["Source"]):
    """
    Manager for Source model with specialized methods for social media monitoring.

    Provides methods for:
    — Finding sources by a platform, type, activity status
    — Monitoring queue management
    — Bulk operations
    — Search and filtering

    User-facing writes are gated by the caller's platform role
    (`source.create/update/delete`). Collector bookkeeping
    (`update_last_checked`) stays unchecked: ingest/collector run without a
    user context.
    """

    def __init__(self):
        from ..collection.source import Source

        super().__init__(Source)

    # --- permission-gated writes (user surfaces: web, agent tools, CLI) ----

    @require_permission("source", ActionType.CREATE)
    async def create(self, *args: Any, session: AsyncSession | None = None, **kwargs: Any) -> "Source":
        if type(kwargs.get("params")) is dict and "telegram_authorization" in kwargs["params"]:
            raise PermissionError("telegram_source_picker_required")
        if session is not None:
            return await super().create(*args, session=session, **kwargs)
        return await super().create(*args, **kwargs)

    @require_permission("source", ActionType.UPDATE)
    @with_db_session
    async def update_by_id(
        self, instance_id: int, *args: Any, session: AsyncSession | None = None, **kwargs: Any
    ) -> Optional["Source"]:
        # Lock the fresh scoped row, not a form's stale JSON copy. The dedicated
        # picker writes in its own stronger account/grant/source transaction.
        row = (
            await session.execute(
                select(self.model)
                .where(self.model.id == instance_id, *self._tenant_criterion())
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        _protect_telegram_binding(row, kwargs)
        return await super().update_by_id(instance_id, *args, session=session, **kwargs)

    @require_permission("source", ActionType.DELETE)
    async def delete_by_id(self, instance_id: int, *args: Any, session: AsyncSession | None = None) -> bool:
        if session is not None:
            return await super().delete_by_id(instance_id, *args, session=session)
        return await super().delete_by_id(instance_id, *args)

    async def get_source_with_platform(
        self,
        source_id: Optional[int] = None,
        external_id: Optional[str] = None,
    ) -> tuple["Source", dict[str, Any]]:
        """
        Get source with its platform data in a single query using the ORM.

        Args:
            source_id: ID of the source to fetch
            external_id: External ID of the source (requires platform_id)

        Returns:
            A tuple of (Source instance, platform_data_dict)
        """
        if not source_id and not external_id:
            raise ValueError("Either source_id or external_id must be provided")

        # Build the base query
        query = self.filter(is_active=True)

        if source_id:
            query = query.filter(id=source_id)
        elif external_id:
            query = query.filter(external_id=external_id)

        # Execute the query with platform eager loading
        source = await query.select_related("platform").first()

        if not source:
            identifier = f"ID {source_id}" if source_id else f"external_id '{external_id}'"
            raise ValueError(f"Active source with {identifier} not found")

        # Extract platform data - only include fields that exist on Platform model
        platform = source.platform
        platform_data: dict[str, Any] = {
            "id": platform.id,
            "name": platform.name,
            "platform_type": get_enum_value(platform.platform_type),
            "is_active": platform.is_active,
        }

        return source, platform_data

    async def get_active_sources(
        self, platform_id: Optional[int] = None, source_type: Optional["SourceType"] = None
    ) -> list["Source"]:
        """
        Get all active sources with optional filters.

        Args:
                platform_id: Optional platform ID filter
                source_type: Optional source type filter

        Returns:
                List of active Source objects
        """
        qs = self.filter(is_active=True)

        if platform_id:
            qs = qs.filter(platform_id=platform_id)

        if source_type:
            qs = qs.filter(source_type=source_type)

        return list(await qs)

    async def get_sources_for_monitoring(
        self, hours_since_check: int = 24, platform_id: Optional[int] = None
    ) -> list["Source"]:
        """
        Get sources that need monitoring (not checked recently or never checked).

        Args:
                hours_since_check: Hours since last check to consider stale
                platform_id: Optional platform filter

        Returns:
                List of Source objects needing monitoring
        """
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours_since_check)

        qs = self.filter(is_active=True)

        if platform_id:
            qs = qs.filter(platform_id=platform_id)

        # Get sources that were never checked or checked before cutoff
        sources = await qs

        return [s for s in sources if s.last_checked is None or cast(datetime, s.last_checked) <= cutoff_time]

    async def get_source_by_platform(self, platform_id: int, external_id: str) -> Optional["Source"]:
        """
        Get source by a platform and external ID.

        Args:
                platform_id: Platform ID
                external_id: External ID in the social platform

        Returns:
                Source object or None
        """
        return await self.filter(platform_id=platform_id, external_id=external_id).first()

    @require_permission("source", ActionType.CREATE)
    async def create_source(
        self,
        platform_id: int,
        source_type: "SourceType",
        external_id: str,
        name: Optional[str] = None,
        params: Optional[dict] = None,
        is_active: bool = True,
        owner_user_id: Optional[int] = None,
    ) -> "Source":
        """
        Create a new source with validation.

        Args:
                platform_id: ID of the platform
                source_type: Type of source (USER, GROUP, CHANNEL, CHAT)
                external_id: External ID in the social platform
                name: Optional display name
                params: Optional parameters for collection
                is_active: Whether the source is active
                owner_user_id: The member whose personal credential it collects
                    with; None means service credential only

        Returns:
                Created Source object

        Raises:
                ValueError: If source already exists
        """
        # Check if source already exists
        existing = await self.get_source_by_platform(platform_id, external_id)
        if existing:
            raise ValueError(f"Source with platform_id={platform_id} and external_id='{external_id}' already exists")

        return await self.create(
            platform_id=platform_id,
            source_type=source_type,
            external_id=external_id,
            name=name or f"Source {external_id}",
            params=params or {},
            is_active=is_active,
            owner_user_id=owner_user_id,
        )

    async def search_sources(
        self, query: str, platform_id: Optional[int] = None, source_type: Optional["SourceType"] = None
    ) -> list["Source"]:
        """
        Search sources by name or external ID with filters.

        Args:
                query: Search query string
                platform_id: Optional platform filter
                source_type: Optional source type filter

        Returns:
                List of matching Source objects
        """

        # Start with basic filter
        qs = self.filter(is_active=True)

        # Apply text search
        if query:
            sources = list(await qs)
            # Filter in memory for now (can be optimized with database LIKE)
            sources = [
                s
                for s in sources
                if query.lower() in (s.name or "").lower() or query.lower() in (s.external_id or "").lower()
            ]
        else:
            sources = list(await qs)

        # Apply filters
        if platform_id:
            sources = [s for s in sources if s.platform_id == platform_id]

        if source_type:
            sources = [s for s in sources if s.source_type == source_type]

        return sources

    async def get_by_type(
        self,
        source_type: "SourceType",
        platform_id: Optional[int] = None,
    ) -> list["Source"]:
        """
        Get sources by type with optional filters.

        Args:
                source_type: Type of sources to get
                platform_id: Optional platform filter

        Returns:
                List of Source objects
        """
        qs = self.filter(source_type=source_type, is_active=True)

        if platform_id:
            qs = qs.filter(platform_id=platform_id)

        return list(await qs)

    async def update_last_checked(self, source_id: int, timestamp: Optional[datetime] = None) -> Optional["Source"]:
        """Update last_checked timestamp for a source.

        Collector/ingest bookkeeping: bypasses the permission gate (runs
        without a user context). User-facing toggles go through the gated
        `update_by_id` instead.

        Args:
                source_id: ID of the source to update
                timestamp: An optional timestamp (defaults to now UTC)

        Returns:
                Updated Source object or None if not found
        """
        from .base_manager import BaseManager

        if timestamp is None:
            # Ensure timezone-aware datetime
            timestamp = datetime.now(timezone.utc)
        elif timestamp.tzinfo is None:
            # Add UTC timezone if naive
            timestamp = timestamp.replace(tzinfo=timezone.utc)

        return await BaseManager.update_by_id(self, source_id, last_checked=timestamp)

    async def get_stats(self) -> dict:
        """
        Get statistics about sources.

        Returns:
                Dict with source statistics
        """
        all_sources = await self.filter()

        stats: dict[str, Any] = {
            "total": len(all_sources),
            "active": len([s for s in all_sources if s.is_active]),
            "inactive": len([s for s in all_sources if not s.is_active]),
            "by_type": {},
            "by_platform": {},
            "never_checked": len([s for s in all_sources if s.last_checked is None]),
        }

        for source in all_sources:
            type_name = get_enum_value(source.source_type)
            stats["by_type"][type_name] = stats["by_type"].get(type_name, 0) + 1
            stats["by_platform"][source.platform_id] = stats["by_platform"].get(source.platform_id, 0) + 1

        return stats
