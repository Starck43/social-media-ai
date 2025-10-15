import logging
from typing import List, Optional
from datetime import datetime, UTC

from app.models import Source, Platform
from app.services.social.factory import get_social_client
from app.services.ai.analyzer import AIAnalyzer
from app.types import SourceType, NotificationType

# Try to import notification service
try:
    from app.services.notifications.service import notify
    from app.services.notifications.messenger import messenger_service

    NOTIFICATIONS_AVAILABLE = True
except ImportError:
    NOTIFICATIONS_AVAILABLE = False

logger = logging.getLogger(__name__)


class ContentCollector:
    """Service for collecting content from social media sources"""

    def __init__(self):
        self.ai_analyzer = AIAnalyzer()

    async def collect_from_source(
        self, source: Source, content_type: str = "posts", analyze: bool = True
    ) -> Optional[dict]:
        """
        Collect content from a single source.

        Args:
                source: Source to collect from
                content_type: Type of content to collect (posts, comments, etc.)
                analyze: Whether to run AI analysis on collected content

        Returns:
                Dict with collection results or None if failed
        """
        try:
            # Get platform
            platform = await Platform.objects.select_related("sources").get(id=source.platform_id)

            # Get appropriate client
            client = get_social_client(platform)

            # Collect data
            logger.info(f"Collecting {content_type} from source {source.id} ({source.name})")
            content = await client.collect_data(source, content_type)

            if not content:
                logger.warning(f"No content collected from source {source.id}")
                return None

            logger.info(f"Collected {len(content)} items from source {source.id}")

            # Run AI analysis if requested
            if analyze and content:
                await self._analyze_content(content, source)

            await Source.objects.update_last_checked(source.id)

            return {"source_id": source.id, "content_count": len(content), "analyzed": analyze}

        except Exception as e:
            logger.error(f"Error collecting from source {source.id}: {e}", exc_info=True)

            # Send critical notification if available
            if NOTIFICATIONS_AVAILABLE:
                try:
                    await notify.create(
                        title=f"Error collecting from source {source.name}",
                        message=f"Failed to collect data: {str(e)}",
                        ntype=NotificationType.API_ERROR,
                        entity_type="source",
                        entity_id=source.id,
                        send_to_messenger=True,
                    )
                except:
                    pass  # Don't fail on notification error

            return None

    async def collect_from_platform(
        self, platform_id: int, source_types: Optional[list[SourceType]] = None, analyze: bool = True
    ) -> dict:
        """
        Collect content from all active sources on a platform.

        Args:
                platform_id: Platform ID
                source_types: Optional list of source types to filter by
                analyze: Whether to run AI analysis

        Returns:
                Dict with collection statistics
        """
        # Build query
        query = Source.objects.filter(platform_id=platform_id, is_active=True)

        if source_types:
            # Filter by source types using __in lookup
            query = query.filter(source_type__in=[st.name for st in source_types])

        sources = await query

        logger.info(f"Collecting from {len(sources)} sources on platform {platform_id}")

        results = {"total_sources": len(sources), "successful": 0, "failed": 0, "total_items": 0}

        for source in sources:
            result = await self.collect_from_source(source, analyze=analyze)
            if result:
                results["successful"] += 1
                results["total_items"] += result["content_count"]
            else:
                results["failed"] += 1

        logger.info(f"Collection complete: {results}")
        return results

    async def collect_monitored_users(self, source: Source, analyze: bool = True) -> dict:
        """
        Collect content from monitored users of a source.

        This is for sources that track specific users (e.g., a GROUP tracking USER posts).

        Args:
                source: Source with monitored_users relationship
                analyze: Whether to run AI analysis

        Returns:
                Dict with collection statistics
        """
        # Load monitored users
        source_with_users = await Source.objects.prefetch_related("monitored_users").get(id=source.id)

        if not source_with_users.monitored_users:
            logger.info(f"Source {source.id} has no monitored users")
            return {"total_users": 0, "successful": 0, "failed": 0}

        logger.info(f"Collecting from {len(source_with_users.monitored_users)} monitored users")

        results = {
            "total_users": len(source_with_users.monitored_users),
            "successful": 0,
            "failed": 0,
            "total_items": 0,
        }

        for user in source_with_users.monitored_users:
            result = await self.collect_from_source(user, analyze=analyze)
            if result:
                results["successful"] += 1
                results["total_items"] += result["content_count"]
            else:
                results["failed"] += 1

        return results

    async def _analyze_content(
        self,
        content: list[dict],
        source: Source,
        topic_chain_id: Optional[str] = None,
        parent_analysis_id: Optional[int] = None,
    ):
        """
        Run AI analysis on collected content in a single comprehensive pass.

        Args:
                content: List of normalized content items
                source: Source from which content was collected
                topic_chain_id: Optional chain ID for ongoing topics
                parent_analysis_id: Optional parent analysis ID for threaded analysis
        """
        try:
            await self.ai_analyzer.analyze_content(
                content, source, topic_chain_id=topic_chain_id, parent_analysis_id=parent_analysis_id
            )
        except Exception as e:
            logger.error(f"Error analyzing content: {e}", exc_info=True)
