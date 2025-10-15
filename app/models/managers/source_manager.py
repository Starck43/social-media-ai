from __future__ import annotations

from datetime import datetime, timedelta, UTC
from typing import Optional, TYPE_CHECKING

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..source import Source, SourceUserRelationship
    from app.types import SourceType


class SourceManager(BaseManager):
    """
    Manager for Source model with specialized methods for social media monitoring.

    Provides methods for:
    — Finding sources by a platform, type, activity status
    — Monitoring queue management
    — Bulk operations
    — Search and filtering
    """

    def __init__(self):
        from ..source import Source
        super().__init__(Source)

    async def get_by_platform(
        self,
        platform_id: int,
    ) -> list["Source"]:
        """
        Get sources by platform ID.

        Args:
                platform_id: ID of the platform

        Returns:
                List of Source objects
        """
        return await self.filter(platform_id=platform_id, is_active=True)

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

        return await qs

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
        cutoff_time = datetime.now(UTC) - timedelta(hours=hours_since_check)

        qs = self.filter(is_active=True)

        if platform_id:
            qs = qs.filter(platform_id=platform_id)

        # Get sources that were never checked or checked before cutoff
        sources = await qs

        return [s for s in sources if s.last_checked is None or s.last_checked <= cutoff_time]

    async def update_last_checked(self, source_id: int, timestamp: Optional[datetime] = None) -> Optional["Source"]:
        """
        Update last_checked timestamp for a source.

        Args:
                source_id: ID of the source to update
                timestamp: An optional timestamp (defaults to now UTC)

        Returns:
                Updated Source object or None if not found
        """
        if timestamp is None:
            # Ensure timezone-aware datetime
            timestamp = datetime.now(UTC)
        elif timestamp.tzinfo is None:
            # Add UTC timezone if naive
            timestamp = timestamp.replace(tzinfo=UTC)

        return await self.update_by_id(source_id, last_checked=timestamp)

    async def get_by_external_id(self, platform_id: int, external_id: str) -> Optional["Source"]:
        """
        Get source by a platform and external ID.

        Args:
                platform_id: Platform ID
                external_id: External ID in the social platform

        Returns:
                Source object or None
        """
        return await self.filter(platform_id=platform_id, external_id=external_id).first()

    async def create_source(
        self,
        platform_id: int,
        source_type: "SourceType",
        external_id: str,
        name: Optional[str] = None,
        params: Optional[dict] = None,
        is_active: bool = True,
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

        Returns:
                Created Source object

        Raises:
                ValueError: If source already exists
        """
        # Check if source already exists
        existing = await self.get_by_external_id(platform_id, external_id)
        if existing:
            raise ValueError(f"Source with platform_id={platform_id} and external_id='{external_id}' already exists")

        return await self.create(
            platform_id=platform_id,
            source_type=source_type,
            external_id=external_id,
            name=name or f"Source {external_id}",
            params=params or {},
            is_active=is_active,
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
            sources = await qs
            # Filter in memory for now (can be optimized with database LIKE)
            sources = [
                s
                for s in sources
                if query.lower() in (s.name or "").lower() or query.lower() in (s.external_id or "").lower()
            ]
        else:
            sources = await qs

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

        return await qs

    async def get_with_monitored_users(self, source_id: int) -> Optional["Source"]:
        """
        Get source with prefetched monitored_users relationship.

        Args:
                source_id: Source ID

        Returns:
                Source object with monitored_users loaded
        """
        return await self.filter(id=source_id).prefetch_related("monitored_users").first()

    async def get_with_scenario(self, source_id: int) -> Optional["Source"]:
        """
        Get source with prefetched bot_scenario relationship.

        Args:
                source_id: Source ID

        Returns:
                Source object with bot_scenario loaded
        """
        return await self.filter(id=source_id).prefetch_related("bot_scenario").first()

    async def get_by_scenario(self, scenario_id: int, is_active: Optional[bool] = True) -> list["Source"]:
        """
        Get sources using a specific bot scenario.

        Args:
                scenario_id: Bot scenario ID
                is_active: Filter by active status

        Returns:
                List of Source objects using the scenario
        """
        qs = self.filter(bot_scenario_id=scenario_id)

        if is_active is not None:
            qs = qs.filter(is_active=is_active)

        return await qs

    async def assign_scenario(self, source_id: int, scenario_id: Optional[int]) -> Optional["Source"]:
        """
        Assign or remove a bot scenario from a source.

        Args:
                source_id: Source ID
                scenario_id: Bot scenario ID (None to remove)

        Returns:
                Updated Source object or None if not found
        """
        return await self.update_by_id(source_id, bot_scenario_id=scenario_id)

    async def get_stats(self) -> dict:
        """
        Get statistics about sources.

        Returns:
                Dict with source statistics
        """
        all_sources = await self.filter()

        stats = {
            "total": len(all_sources),
            "active": len([s for s in all_sources if s.is_active]),
            "inactive": len([s for s in all_sources if not s.is_active]),
            "by_type": {},
            "by_platform": {},
            "never_checked": len([s for s in all_sources if s.last_checked is None]),
            "with_scenario": len([s for s in all_sources if s.bot_scenario_id is not None]),
        }

        # Count by source type
        from app.utils.enum_helpers import get_enum_value
        
        for source in all_sources:
            type_name = get_enum_value(source.source_type)
            stats["by_type"][type_name] = stats["by_type"].get(type_name, 0) + 1
            stats["by_platform"][source.platform_id] = stats["by_platform"].get(source.platform_id, 0) + 1

        return stats


class SourceUserRelationshipManager(BaseManager):
    """
    Manager for SourceUserRelationship model.
    Handles relationships between sources and users for monitoring.
    """

    def get_queryset(self):
        """Return base queryset for SourceUserRelationship."""
        return super().get_queryset()

    async def get_by_source_and_user(self, source_id: int, user_id: int) -> "SourceUserRelationship | None":
        """Get relationship by source and user IDs."""
        return await self.get_queryset().filter(source_id=source_id, user_id=user_id).first()

    async def get_monitored_users_for_source(self, source_id: int) -> list["SourceUserRelationship"]:
        """Get all users monitored by a specific source."""
        return await self.get_queryset().filter(source_id=source_id).all()

    async def get_sources_tracking_user(self, user_id: int) -> list["SourceUserRelationship"]:
        """Get all sources that track a specific user."""
        return await self.get_queryset().filter(user_id=user_id).all()
