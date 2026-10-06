import logging
from datetime import UTC, date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from app.core.config import settings
from app.core.analysis_constants import DEFAULT_ANALYSIS_PARAMS
from app.models import AgentScenario, AIAnalytics, LLMModel, LLMProvider, Source
from app.services.ai.chain_resolver import resolve_chain_async
from app.services.ai.content_classifier import ContentClassifier
from app.services.ai.dedup import batch_hash, filter_analyzed, hashes_hash, item_hash
from app.services.ai.llm_client import LLMClientFactory
from app.services.ai.llm_provider_resolver import LLMProviderResolver
from app.services.ai.prompts import PromptBuilder
from app.services.ai.scenario import build_output_schema
from app.services.ai.theme_matcher import ThemeMatcher
from app.types import PeriodType
from app.types.enums.bot_types import AnalyzeType
from app.types.enums.llm_types import MediaType
from app.utils.date_parsing import universal_date_parser
from app.utils.enum_helpers import get_enum_value
from app.utils.translit import translit_slug

logger = logging.getLogger(__name__)


class AIAnalyzer:
    """
    AI Analyzer with multi-LLM support.

    This service handles:
    — Classification of content by media type (text, image, video)
    — Selection of appropriate LLM providers for each content type
    — Parallel analysis using multiple specialized LLMs
    — Unification of results into a single comprehensive summary
    — Full LLM tracing for debugging and monitoring
    """

    def __init__(self):
        self.theme_matcher = ThemeMatcher()
        # Rows skipped by the scenario's relevance_filter since this instance was
        # created — the job handler reads it off to report in the job result.
        self.filtered_skipped = 0

    async def analyze_content(
        self,
        content: list[dict],
        source: Source,
        analyze_by: str = None,
        force_reanalyze: bool = False,
        agent_scenario=None,
        trigger_config=None,
        task_payload: Optional[dict[str, Any]] = None,
    ) -> list[AIAnalytics]:
        """
        Analyze content based on analyze_by mode.

        Args:
                content: List of content items
                source: Source being analyzed
                analyze_by: Analysis mode: "days" or "themes"
                force_reanalyze: Bypass dedup and re-analyze everything (full-cycle refresh)
                agent_scenario: Already-resolved scenario (task's own). When given,
                        the tenant-default lookup is skipped, so a task's scenario
                        actually shapes the analysis instead of being silently ignored.
                task_payload: Task-specific parameters (brands, competitors, etc.)

        Returns:
                List of AIAnalytics records (one per day with activity)
        """
        analyze_by = analyze_by or (
            agent_scenario.analyze_type
            if agent_scenario and agent_scenario.analyze_type
            else await self._default_scenario_analyze_type(source)
        )
        analyze_by = get_enum_value(analyze_by)

        if analyze_by == "themes":
            return await self._analyze_content_by_themes(
                content,
                source,
                force_reanalyze=force_reanalyze,
                agent_scenario=agent_scenario,
                trigger_config=trigger_config,
                task_payload=task_payload,
            )
        elif analyze_by == "sources":
            return await self._analyze_content_by_sources(
                content,
                source,
                force_reanalyze=force_reanalyze,
                agent_scenario=agent_scenario,
                trigger_config=trigger_config,
                task_payload=task_payload,
            )
        elif analyze_by == "monitored_users":
            return await self._analyze_content_by_monitored_users(
                content,
                source,
                force_reanalyze=force_reanalyze,
                agent_scenario=agent_scenario,
                trigger_config=trigger_config,
                task_payload=task_payload,
            )
        else:
            return await self._analyze_content_by_days(
                content,
                source,
                force_reanalyze=force_reanalyze,
                agent_scenario=agent_scenario,
                trigger_config=trigger_config,
                task_payload=task_payload,
            )

    async def _default_scenario_analyze_type(self, source: Source) -> str:
        """The analysis mode ("days"/"themes") of the workspace default scenario.

        The source no longer owns a scenario, so the default is the only source
        of the mode for taskless runs; "themes" is the fallback when none set.
        """
        try:
            default_sc = await AgentScenario.objects.get_default_scenario(tenant_id=source.tenant_id)
            if default_sc is not None and default_sc.analyze_type:
                return get_enum_value(default_sc.analyze_type)
        except Exception:
            pass
        return "themes"

    async def base_analyze_content(
        self,
        content: list[dict],
        source: Source,
        topic_chain_id: Optional[str] = None,
        parent_analysis_id: Optional[int] = None,
        analysis_date: Optional[date] = None,
        force_reanalyze: bool = False,
        analyze_type: Optional[str] = None,
        agent_scenario: Optional["AgentScenario"] = None,
        trigger_config: Optional[dict[str, Any]] = None,
        task_payload: Optional[dict[str, Any]] = None,
    ) -> Optional[AIAnalytics]:
        """
        Comprehensive analysis of collected content using multiple LLM providers.

        `trigger_config` and `task_payload` are passed in rather than read off
        the scenario: they live on the task that runs this analysis, and the
        scenario is shared by tasks whose payloads differ.

        Args:
                content: List of normalized content items
                source: Source from which content was collected
                topic_chain_id: Optional chain ID for ongoing topics
                parent_analysis_id: Optional parent analysis ID for threaded analysis
                analysis_date: Optional date to use for this analysis (defaults to today)
                force_reanalyze: Bypass dedup and analyze everything (used by a
                        full-cycle `--force-refresh` task run so stored rows are
                        overwritten; costs tokens, so it is never the default).
                analyze_type: The scenario's analysis mode ("days"/"themes"/
                        "sources"/"monitored_users"); when set, it shapes the
                        auto-generated chain id so a mode's records share one
                        stable chain.
                agent_scenario: Already-resolved scenario (a task's own). When
                        given, the tenant-default lookup is skipped — the caller
                        resolved the scenario, so this analysis must use it.
                task_payload: Task-specific TARGET parameters (brands,
                        competitors, hashtags); injected into the prompt
                        instruction, never into the response schema.

        Returns:
                AIAnalytics object with complete analysis results or None if failed
        """
        if not content:
            logger.warning(f"No content to analyze for source {source.id}")
            return None

        # Dedup before anything expensive: items already covered by an earlier
        # analysis must never reach the LLM again (fail-open, see dedup.py).
        # `force_reanalyze` is the deliberate exception: a full-cycle refresh
        # re-analyzes the whole selected period so its rows get overwritten.
        if not force_reanalyze:
            content, known = await filter_analyzed(content, source.id)
            if not content:
                if known is not None:
                    logger.info(f"Batch for source {source.id} fully covered by analytics {known.id}, returning as-is")
                    return known
                logger.warning(f"No content to analyze for source {source.id} after dedup filtering")
                return None
        else:
            logger.info(f"Force re-analysis for source {source.id}: bypassing dedup ({len(content)} items)")

        # Load the scenario: the source no longer carries one. A caller that
        # resolved a scenario (a task's own) passes it in; everything else
        # (push ingest, CLI collect, agent collect) falls back to the tenant
        # default.
        if agent_scenario is None:
            try:
                default_sc = await AgentScenario.objects.get_default_scenario(tenant_id=source.tenant_id)
                if default_sc:
                    agent_scenario = default_sc
                    logger.info(f"Using tenant default scenario '{agent_scenario.name}' for source {source.id}")
            except Exception:
                pass

        # Lazy structured-output schema: derive from analysis_types when the
        # scenario has no explicit output_schema (in-memory, not persisted).
        if agent_scenario is not None and not agent_scenario.output_schema:
            agent_scenario.output_schema = build_output_schema(agent_scenario.analysis_types, agent_scenario.scope)

        # Prepare metadata
        content_stats = self._calculate_content_stats(content, analysis_date)
        platform_name = await self._get_platform_name(source)

        # Classify content by media type
        classified = ContentClassifier.classify_content(content)

        try:
            # Analyze each content type with appropriate LLM
            analysis_results = {}

            # Text analysis
            if classified[MediaType.TEXT.db_value]:
                text_result = await self._analyze_text(
                    classified[MediaType.TEXT.db_value],
                    agent_scenario,
                    content_stats,
                    platform_name,
                    source,
                    trigger_config,
                    task_payload,
                )
                if text_result:
                    analysis_results["text_analysis"] = text_result

            # Image analysis
            if classified[MediaType.IMAGE.db_value]:
                image_result = await self._analyze_images(
                    classified[MediaType.IMAGE.db_value], agent_scenario, platform_name, trigger_config, task_payload
                )
                if image_result:
                    analysis_results["image_analysis"] = image_result

            # Video analysis
            if classified[MediaType.VIDEO.db_value]:
                video_result = await self._analyze_videos(
                    classified[MediaType.VIDEO.db_value], agent_scenario, platform_name, trigger_config, task_payload
                )
                if video_result:
                    analysis_results["video_analysis"] = video_result

            # Check if we have any meaningful analysis results.
            #
            # A timed-out or errored LLM call still returns a truthy `parsed`
            # stub (`{"analysis": "Timeout"}` / `{"analysis": "Error: ..."}`),
            # which would otherwise be saved as a real analysis and mark the
            # batch as analyzed — silently blocking a retry and leaving the
            # chain with a stats-only entry and no conclusion. Treat those
            # stubs as "no result" so the batch stays unanalysed and is retried.
            has_results = False
            for result in analysis_results.values():
                if not result or not result.get("parsed"):
                    continue
                parsed = result["parsed"]
                text = parsed.get("analysis") if isinstance(parsed, dict) else parsed
                if isinstance(text, str) and (text.startswith("Timeout") or text.startswith("Error")):
                    logger.warning(f"LLM result is an error stub ('{text[:40]}'), not saving analysis")
                    continue
                has_results = True
                break

            if not has_results:
                logger.warning(f"No meaningful analysis results for source {source.id}, skipping save")
                return None

            # Create unified summary if multiple analyses
            unified_summary = await self._create_unified_summary(analysis_results, agent_scenario)

            # Auto-generate topic_chain_id if not provided
            # Phase 1: use resolve_chain_async for proper lookup by topic_hint
            main_topics = (analysis_results.get("text_analysis", {}).get("parsed", {}) or {}).get(
                "main_topics"
            ) or []
            # Extract topic_hint from LLM response (Phase 1: normalize + lookup existing)
            text_parsed = (analysis_results.get("text_analysis", {}) or {}).get("parsed") or {}
            topic_hint: str | None = text_parsed.get("topic_hint")
            if unified_summary:
                topic_hint = unified_summary.get("topic_hint") or topic_hint

            if not topic_chain_id:
                if topic_hint:
                    # resolve_chain does normalization + lookup in ai_analytics by topic_hint
                    topic_chain_id, chain_label = await resolve_chain_async(
                        source.tenant_id, source.id, topic_hint
                    )
                    logger.info(f"Chain resolved: {topic_chain_id} for source {source.id} via hint={topic_hint!r}")
                else:
                    # Fallback to source+scenario-based ID (no topic_hint in LLM response)
                    topic_chain_id = self._generate_topic_chain_id(
                        source, main_topics, agent_scenario, analyze_type=analyze_type
                    )
                    chain_label = self._resolve_chain_label(main_topics, analysis_results)
                    logger.info(f"Using topic chain: {topic_chain_id} for source {source.id}")
            else:
                chain_label = self._resolve_chain_label(main_topics, analysis_results)

            # Save comprehensive analysis
            analysis = await self._save_analysis(
                analysis_results,
                unified_summary,
                source,
                content_stats,
                platform_name,
                agent_scenario,
                topic_chain_id,
                chain_label,
                parent_analysis_id=parent_analysis_id,
                analysis_date=analysis_date,
                content_hash=batch_hash(content),
                content_hashes=[item_hash(i) for i in content],
            )

            return analysis

        except Exception as e:
            logger.error(f"Error analyzing content for source {source.id}: {e}", exc_info=True)
            return None

    async def _analyze_content_by_days(
        self,
        content: list[dict],
        source: Source,
        force_reanalyze: bool = False,
        agent_scenario: "AgentScenario" = None,
        trigger_config: "dict | None" = None,
        task_payload: "dict | None" = None,
    ) -> list[AIAnalytics]:
        """
        Group content by days and analyze each day separately.

        Args:
                content: List of content items
                source: Source being analyzed
                force_reanalyze: Bypass dedup and re-analyze everything
                task_payload: Task-specific parameters (brands, competitors, etc.)

        Returns:
                List of AIAnalytics records (one per day with activity)
        """
        from collections import defaultdict

        if not content:
            logger.warning(f"No content to analyze for source {source.id}")
            return []

        content_by_day = defaultdict(list)

        for item in content:
            pub_date = item.get("published_at") or item.get("date") or item.get("created_at")
            pub_date = universal_date_parser(pub_date)
            if pub_date:
                day = pub_date.date()
            else:
                day = date.today()
            content_by_day[day].append(item)

        logger.info(f"Grouped {len(content)} items into {len(content_by_day)} days for source {source.id}")

        analytics_list = []

        for day, day_content in sorted(content_by_day.items()):
            logger.info(f"Analyzing {len(day_content)} items for source {source.id} on {day}")

            try:
                analytics = await self.base_analyze_content(
                    content=day_content,
                    source=source,
                    analysis_date=day,
                    force_reanalyze=force_reanalyze,
                    agent_scenario=agent_scenario,
                    trigger_config=trigger_config,
                    task_payload=task_payload,
                )

                if analytics:
                    analytics_list.append(analytics)
                else:
                    logger.warning(f"Skipping empty analytics for day {day}, source {source.id}")

            except Exception as e:
                logger.error(f"Error analyzing day {day} for source {source.id}: {e}")
                continue

        logger.info(
            f"Created {len(analytics_list)} analytics records for source {source.id} "
            f"(analyzed {len(content_by_day)} days)"
        )

        return analytics_list

    async def _analyze_content_by_themes(
        self,
        content: list[dict],
        source: Source,
        force_reanalyze: bool = False,
        agent_scenario: "AgentScenario" = None,
        trigger_config: "dict | None" = None,
        task_payload: "dict | None" = None,
    ) -> list[AIAnalytics]:
        """
        Analyze content with automatic theme detection and linking.

        Args:
                content: List of content items
                source: Source being analyzed
                force_reanalyze: Bypass dedup and re-analyze everything
                task_payload: Task-specific parameters (brands, competitors, etc.)

        Returns:
                List of AIAnalytics records (typically one record with theme linking)
        """
        if not content:
            logger.warning(f"No content to analyze for source {source.id}")
            return []

        analysis = await self.base_analyze_content(
            content,
            source,
            force_reanalyze=force_reanalyze,
            agent_scenario=agent_scenario,
            trigger_config=trigger_config,
            task_payload=task_payload,
        )
        if not analysis:
            return []

        # Apply theme linking
        await self._auto_link_to_existing_theme(analysis, source)

        return [analysis]

    async def _analyze_content_by_sources(
        self,
        content: list[dict],
        source: Source,
        force_reanalyze: bool = False,
        agent_scenario: "AgentScenario" = None,
        trigger_config: "dict | None" = None,
        task_payload: "dict | None" = None,
    ) -> list[AIAnalytics]:
        """Analyze content grouped by its origin source.

        `analyze_type = "sources"` builds one analysis per distinct source the
        batch came from, each with a stable per-source chain id, so the
        retrospective follows a single source's evolution over time instead of
        being fragmented by topic. A batch that is already a single source's
        content collapses to one record.

        Args:
                content: List of content items
                source: The primary source being analyzed (fallback chain owner)
                force_reanalyze: Bypass dedup and re-analyze everything
                task_payload: Task-specific parameters (brands, competitors, etc.)

        Returns:
                List of AIAnalytics records (one per source group)
        """
        from collections import OrderedDict

        if not content:
            return []

        # Group by the item's own source id when present; otherwise every item
        # belongs to the caller's `source`.
        groups: OrderedDict[int | str, list[dict]] = OrderedDict()
        for item in content:
            src_id = item.get("source_id") or item.get("source") or source.id
            groups.setdefault(src_id, []).append(item)

        analytics_list = []
        for src_key, group in groups.items():
            try:
                analysis = await self.base_analyze_content(
                    group,
                    source,
                    force_reanalyze=force_reanalyze,
                    analyze_type="sources",
                    agent_scenario=agent_scenario,
                    trigger_config=trigger_config,
                    task_payload=task_payload,
                )
                if analysis:
                    analytics_list.append(analysis)
            except Exception as e:  # noqa: BLE001
                logger.error(f"Error analyzing source group {src_key} for source {source.id}: {e}")
                continue

        logger.info(
            f"Analyzed {len(groups)} source group(s) for source {source.id} "
            f"(analyze_type=sources), {len(analytics_list)} records"
        )
        return analytics_list

    async def _analyze_content_by_monitored_users(
        self,
        content: list[dict],
        source: Source,
        force_reanalyze: bool = False,
        agent_scenario: "AgentScenario" = None,
        trigger_config: "dict | None" = None,
        task_payload: "dict | None" = None,
    ) -> list[AIAnalytics]:
        """Analyze content grouped by the tracked user who authored it.

        `analyze_type = "monitored_users"` builds one analysis per distinct
        author among the content (the users listed in
        `Source.params["monitored_users"]` when the collector filtered by them).
        Each user gets a stable chain id, so the retrospective follows one
        person's activity across runs.

        Args:
                content: List of content items
                source: The source being analyzed
                force_reanalyze: Bypass dedup and re-analyze everything
                task_payload: Task-specific parameters (brands, competitors, etc.)

        Returns:
                List of AIAnalytics records (one per monitored user group)
        """
        from collections import OrderedDict

        if not content:
            return []

        def _author_key(item: dict) -> str:
            for key in ("author_id", "user_id", "owner_id", "from_id"):
                if item.get(key) is not None:
                    return str(item[key])
            # The platform clients pack the author into a dict.
            author = item.get("author")
            if isinstance(author, dict):
                for key in ("id", "user_id", "owner_id", "from_id", "username", "name"):
                    if author.get(key) is not None:
                        return str(author[key])
            # No explicit author — fall back to the source's own identity.
            return str(source.id)

        groups: OrderedDict[str, list[dict]] = OrderedDict()
        for item in content:
            groups.setdefault(_author_key(item), []).append(item)

        analytics_list = []
        for author, group in groups.items():
            try:
                chain_id = f"src_{source.id}_user_{author}"
                analysis = await self.base_analyze_content(
                    group,
                    source,
                    topic_chain_id=chain_id,
                    force_reanalyze=force_reanalyze,
                    agent_scenario=agent_scenario,
                    trigger_config=trigger_config,
                    task_payload=task_payload,
                )
                if analysis:
                    analytics_list.append(analysis)
            except Exception as e:  # noqa: BLE001
                logger.error(f"Error analyzing monitored user {author} for source {source.id}: {e}")
                continue

        logger.info(
            f"Analyzed {len(groups)} monitored user(s) for source {source.id} "
            f"(analyze_type=monitored_users), {len(analytics_list)} records"
        )
        return analytics_list

    async def _auto_link_to_existing_theme(self, analysis: AIAnalytics, source: Source):
        """
        Automatically link analysis to existing theme if topics match.

        Args:
                analysis: Newly created AIAnalytics record
                source: Source being analyzed
        """
        try:
            # Safely convert JSON field to dict
            summary_dict = analysis.summary_data or {}

            # Extract main_topics from analysis results
            main_topics = self._extract_main_topics(summary_dict)

            if not main_topics:
                logger.debug(f"No main_topics found for analysis {analysis.id}")
                return

            # Update analysis with main_topics for future matching
            await AIAnalytics.objects.update_by_id(analysis.id, main_topics=main_topics)

            # Find similar existing analytics using pre-initialized matcher
            similar_analytics = await self.theme_matcher.find_similar_analytics(main_topics, source.id)

            if similar_analytics and similar_analytics.topic_chain_id:
                # Link to existing theme chain
                await AIAnalytics.objects.update_by_id(
                    analysis.id,
                    topic_chain_id=similar_analytics.topic_chain_id,
                    chain_label=similar_analytics.chain_label or similar_analytics.topic_chain_id,
                )
                logger.info(
                    f"Auto-linked analysis {analysis.id} to existing theme chain: "
                    f"{similar_analytics.topic_chain_id}"
                )

        except Exception as e:
            logger.error(f"Error in auto theme linking: {e}")

    def _extract_main_topics(self, summary_data: dict) -> list[str]:
        """
        Extract main_topics from analysis summary data.
        """
        try:
            # Try text analysis first
            text_analysis = summary_data.get("multi_llm_analysis", {}).get("text_analysis", {})
            if text_analysis and "main_topics" in text_analysis:
                return text_analysis["main_topics"]

            # Try unified summary
            unified_summary = summary_data.get("unified_summary", {})
            if unified_summary and "main_topics" in unified_summary:
                return unified_summary["main_topics"]

            # Fallback to empty list
            return []

        except Exception as e:
            logger.warning(f"Error extracting main_topics: {e}")
            return []

    async def _analyze_text(
        self,
        text_items: list[dict],
        agent_scenario: Optional[AgentScenario],
        content_stats: dict[str, Any],
        platform_name: str,
        source: Source,
        trigger_config: Optional[dict[str, Any]] = None,
        task_payload: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """Analyze text content using text LLM provider."""
        try:
            # Get LLM provider for text
            model = await self._get_llm_model(agent_scenario, MediaType.TEXT)
            if not model:
                logger.warning("No text LLM provider configured, skipping text analysis")
                return None

            # Prepare text content
            text_content = ContentClassifier.prepare_text_content(text_items)

            # Build prompt using new unified system
            source_type = getattr(source, "source_type", None)
            stype = get_enum_value(source_type) if source_type else ""

            prompt = PromptBuilder.get_prompt(
                MediaType.TEXT,
                scenario=agent_scenario,
                task_payload=task_payload,
                text=text_content,
                stats=content_stats,
                platform_name=platform_name,
                source_type=stype,
                trigger_config=trigger_config,
            )

            # Create LLM client and analyze
            client = LLMClientFactory.create(model)
            kwargs: dict[str, Any] = {}
            if agent_scenario and agent_scenario.max_tokens:
                kwargs["max_tokens"] = agent_scenario.max_tokens
            if agent_scenario and agent_scenario.output_schema:
                prompt = prompt + "\n\nОтвет должен соответствовать JSON Schema:\n" + str(agent_scenario.output_schema)
            result = await client.analyze(prompt, **kwargs)

            logger.info(f"Text analysis completed using {model.name}")
            return result

        except Exception as e:
            logger.error(f"Error in text analysis: {e}", exc_info=True)
            return None

    async def _analyze_images(
        self,
        image_items: list[dict],
        agent_scenario: Optional[AgentScenario],
        platform_name: str,
        trigger_config: Optional[dict[str, Any]] = None,
        task_payload: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """Analyze images using image LLM provider."""
        try:
            # Get LLM provider for images
            provider = await self._get_llm_model(agent_scenario, MediaType.IMAGE)
            if not provider:
                logger.warning("No image LLM provider configured, skipping image analysis")
                return None

            # Extract image URLs
            media_urls = ContentClassifier.get_media_urls(image_items)
            if not media_urls:
                return None

            # Build prompt using new unified system
            prompt = PromptBuilder.get_prompt(
                MediaType.IMAGE,
                scenario=agent_scenario,
                task_payload=task_payload,
                count=len(media_urls),
                platform_name=platform_name,
                trigger_config=trigger_config,
            )

            # Create LLM client and analyze
            client = LLMClientFactory.create(provider)
            kwargs: dict[str, Any] = {"media_urls": media_urls}
            if agent_scenario and agent_scenario.max_tokens:
                kwargs["max_tokens"] = agent_scenario.max_tokens
            result = await client.analyze(prompt, **kwargs)

            logger.info(f"Image analysis completed using {provider.name}, analyzed {len(media_urls)} images")
            return result

        except Exception as e:
            logger.error(f"Error in image analysis: {e}", exc_info=True)
            return None

    async def _analyze_videos(
        self,
        video_items: list[dict],
        agent_scenario: Optional[AgentScenario],
        platform_name: str,
        trigger_config: Optional[dict[str, Any]] = None,
        task_payload: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """Analyze videos using video LLM provider."""
        try:
            # Get LLM provider for videos
            provider = await self._get_llm_model(agent_scenario, MediaType.VIDEO)
            if not provider:
                logger.warning("No video LLM provider configured, skipping video analysis")
                return None

            # Extract video URLs
            media_urls = ContentClassifier.get_media_urls(video_items)
            if not media_urls:
                return None

            # Build prompt using new unified system
            prompt = PromptBuilder.get_prompt(
                MediaType.VIDEO,
                scenario=agent_scenario,
                task_payload=task_payload,
                count=len(media_urls),
                platform_name=platform_name,
                trigger_config=trigger_config,
            )

            # Create LLM client and analyze
            client = LLMClientFactory.create(provider)
            kwargs: dict[str, Any] = {"media_urls": media_urls}
            if agent_scenario and agent_scenario.max_tokens:
                kwargs["max_tokens"] = agent_scenario.max_tokens
            result = await client.analyze(prompt, **kwargs)

            logger.info(f"Video analysis completed using {provider.name}, analyzed {len(media_urls)} videos")
            return result

        except Exception as e:
            logger.error(f"Error in video analysis: {e}", exc_info=True)
            return None

    async def _create_unified_summary(
        self, analysis_results: dict[str, Any], agent_scenario: Optional[AgentScenario]
    ) -> Optional[dict[str, Any]]:
        """
        Create unified summary from multiple analysis results.

        This combines insights from text, image, and video analyses into
        a single coherent summary with actionable insights.
        """
        if len(analysis_results) <= 1:
            # Only one type of analysis, no need to unify
            return None

        try:
            # Get default text provider for summary creation
            model = await self._get_llm_model(agent_scenario, MediaType.TEXT)
            if not model:
                logger.warning("No text LLM provider for unified summary")
                return None

            # Extract parsed results
            text_analysis = analysis_results.get("text_analysis", {}).get("parsed", {})
            image_analysis = analysis_results.get("image_analysis", {}).get("parsed", {})
            video_analysis = analysis_results.get("video_analysis", {}).get("parsed", {})

            # Build unification prompt using new unified system
            prompt = PromptBuilder.get_unified_summary_prompt(
                text_analysis, image_analysis, video_analysis, scenario=agent_scenario
            )

            # Create summary
            client = LLMClientFactory.create(model)
            kwargs: dict[str, Any] = {}
            if agent_scenario and agent_scenario.max_tokens:
                kwargs["max_tokens"] = agent_scenario.max_tokens
            result = await client.analyze(prompt, **kwargs)

            logger.info("Unified summary created successfully")
            return result

        except Exception as e:
            logger.error(f"Error creating unified summary: {e}", exc_info=True)
            return None

    async def _get_llm_model(
        self, agent_scenario: Optional[AgentScenario], media_type: MediaType | str
    ) -> Optional[LLMModel]:
        """
        Get appropriate LLM model for media type.

        Priority:
        1. Get model by media type from scenario
        2. Auto-resolve by llm_strategy (fallback)
        3. Fall back to default model for media type

        Returns:
                LLMModel instance or None
        """

        # Convert string to MediaType if needed
        if isinstance(media_type, str):
            media_type = MediaType(media_type)

        # Priority 1: Get model by media type from scenario, then provider from model
        if agent_scenario:
            model_id = None

            if media_type == MediaType.TEXT and agent_scenario.text_llm_model_id:
                model_id = agent_scenario.text_llm_model_id
            elif media_type == MediaType.IMAGE and agent_scenario.image_llm_model_id:
                model_id = agent_scenario.image_llm_model_id
            elif media_type == MediaType.VIDEO and agent_scenario.video_llm_model_id:
                model_id = agent_scenario.video_llm_model_id

            # Load model and get its provider
            if model_id:
                try:
                    # Load model with provider relationship to avoid session issues
                    model = await LLMModel.objects.select_related("provider").get(id=model_id)
                    if model.is_active and model.provider.is_active:
                        logger.info(f"✅ Select model {model.name} (provider: {model.provider.name}) for {media_type}")
                        return model

                except Exception as e:
                    logger.warning(f"Failed to load model {model_id}: {e}, trying fallback")

        # Priority 2: Auto-resolve by llm_strategy (fallback)
        if agent_scenario and agent_scenario.llm_strategy:
            try:
                model = await self._auto_resolve_model(agent_scenario, media_type)
                if model:
                    return model

            except Exception as e:
                logger.warning(f"Failed to auto-resolve model: {e}")

        # Priority 3: Fall back to default provider for media type
        try:
            # Use get_enum_value to ensure we pass a string, not tuple
            media_type_str = get_enum_value(media_type)
            model = await LLMModel.objects.get_model_for_capability(media_type_str)
            if model:
                logger.info(f"✅ Select default fallback model {model.name} for {media_type}")
                return model

        except Exception as e:
            logger.error(f"❌ No model found for {media_type}. {e}")

        return None

    async def _auto_resolve_model(self, agent_scenario: AgentScenario, media_type: MediaType) -> Optional[LLMModel]:
        """Auto-resolve provider using strategy-based approach."""

        # Get all active providers and their models
        all_providers = await LLMProvider.objects.filter(is_active=True)
        all_models = await LLMModel.objects.select_related("provider").filter(is_active=True)
        active_models = [m for m in all_models if m.provider.is_active]

        # Group models by provider and find best model for each provider
        provider_best_models = {}
        for model in active_models:
            provider_id = model.provider_id
            if provider_id not in provider_best_models:
                provider_best_models[provider_id] = model
            else:
                # Keep model with most capabilities (prioritize multimodal models)
                current = provider_best_models[provider_id]
                if len(model.capabilities) > len(current.capabilities):
                    provider_best_models[provider_id] = model

        # Build available providers dict for a resolver - simplified without rigid provider typing
        available = {}
        for provider in all_providers:
            if provider.id not in provider_best_models:
                continue

            model = provider_best_models[provider.id]

            # Use generic provider type based on capabilities instead of rigid URL parsing
            provider_type = self._get_generic_provider_type(model.capabilities)

            available[provider.id] = (provider_type, model.name, model.capabilities or [])

        if not available:
            logger.error("No active providers available for auto-resolve")
            return None

        # Resolve by strategy
        resolved = LLMProviderResolver.resolve_for_content_types(
            content_types=agent_scenario.content_types or [],
            available_providers=available,
            strategy=str(agent_scenario.llm_strategy),
        )

        # Get provider for this media type
        media_type_str = media_type.value if hasattr(media_type, "value") else str(media_type)
        if media_type_str in resolved:
            provider_config = resolved[media_type_str]
            provider_id = provider_config["provider_id"]
            provider = await LLMProvider.objects.get(id=provider_id)
            logger.info(
                f"✅ Auto-resolved provider {provider.name} for {media_type} "
                f"using strategy '{agent_scenario.llm_strategy}'"
            )
            return provider

        return None

    def _get_generic_provider_type(self, capabilities: list[str]) -> str:
        """Get generic provider type based on capabilities instead of rigid URL parsing."""
        if not capabilities:
            return "text"  # Default fallback

        # Determine primary capability for categorization
        if "video" in capabilities:
            return "multimodal"  # Video usually implies multimodal capabilities
        elif "image" in capabilities and "text" in capabilities:
            return "multimodal"
        elif "image" in capabilities:
            return "vision"
        elif "text" in capabilities:
            return "text"
        else:
            return "specialized"

    async def _get_platform_name(self, source: Source) -> str:
        """Get platform name safely."""
        try:
            plat = getattr(source, "platform", None)
            if plat and getattr(plat, "name", None):
                return plat.name
        except Exception:
            pass

        from app.models import Platform

        obj = await Platform.objects.get(id=source.platform_id)
        return obj.name

    def _calculate_content_stats(self, content: list[dict], analysis_date: Optional[date] = None) -> dict[str, Any]:
        """
        Calculate content statistics including actual post date range.

        Args:
                content: List of content items
                analysis_date: Optional analysis date for event-based mode
        """
        if not content:
            return {}

        # Extract basic data
        texts = [item.get("text", "") for item in content]
        reactions = [item.get("reactions", 0) for item in content]
        comments = [item.get("comments", 0) for item in content]
        views = [item.get("views", 0) for item in content]

        # Distinct authors: VK items carry from_id/owner_id; Telegram comments a
        # from_id. Any of these is enough to count an active user.
        authors: set[str] = set()
        for item in content:
            for key in ("from_id", "owner_id", "author_id", "user_id"):
                if item.get(key) is not None:
                    authors.add(str(item[key]))
                    break

        total_posts = len(content)
        total_reactions = sum(reactions)
        total_comments = sum(comments)
        total_views = sum(views)

        # Extract and parse post dates using existing utility
        post_dates = []
        for item in content:
            pub_date = item.get("published_at") or item.get("date") or item.get("created_at")
            if parsed_date := universal_date_parser(pub_date):
                post_dates.append(parsed_date)

        # Calculate content date range
        content_date_range = {}
        if post_dates:
            min_date, max_date = min(post_dates), max(post_dates)
            content_date_range = {
                "earliest": min_date.isoformat(),
                "latest": max_date.isoformat(),
                "span_days": (max_date - min_date).days,
            }

        # Determine date range for context
        if analysis_date and post_dates:
            date_range_dict = {"first": analysis_date.isoformat(), "last": analysis_date.isoformat()}
        else:
            # Use actual dates from content (fallback to None if no dates)
            dates = [item.get("date") for item in content if item.get("date")]
            date_range_dict = {"first": min(dates) if dates else None, "last": max(dates) if dates else None}

        # Calculate all statistics
        return {
            "total_posts": total_posts,
            "messages_count": total_posts,
            "active_users": len(authors),
            "avg_text_length": sum(len(t) for t in texts) / total_posts if texts else 0,
            "total_reactions": total_reactions,
            "total_comments": total_comments,
            "total_views": total_views,
            "avg_reactions_per_post": total_reactions / total_posts if total_posts else 0,
            "avg_comments_per_post": total_comments / total_posts if total_posts else 0,
            "engagement_rate": (total_reactions + total_comments + total_views) / total_posts if total_posts else 0,
            "date_range": date_range_dict,  # Context-aware date range
            "content_date_range": content_date_range,  # Actual post dates
        }

    def _make_json_serializable(self, obj):
        """Recursively convert non-JSON serializable objects to strings/primitives."""
        from datetime import date, datetime
        from enum import Enum

        if isinstance(obj, (datetime, date)):
            return obj.isoformat()

        if isinstance(obj, Enum):
            # return the database value or name; str(obj) also works if consistent
            return getattr(obj, "value", obj.name)

        if isinstance(obj, dict):
            return {k: self._make_json_serializable(v) for k, v in obj.items()}

        if isinstance(obj, (list, tuple, set)):
            return [self._make_json_serializable(v) for v in obj]

        return obj

    #: Scale of AIAnalytics.estimated_cost (USD cents, 6 decimals = 1e-8 USD).
    COST_SCALE = Decimal("0.000001")

    async def _price_usage(self, usage_by_result: list[dict[str, Any]]) -> Decimal:
        """Price token usage from llm_models rates (USD per 1K tokens).

        Returns the estimated cost in USD cents, quantised to the scale of the
        `estimated_cost` column. Decimal throughout: tariffs are USD per 1K with
        4+ decimals, so float would both drift and hide sub-cent calls that
        cheap models produce on nearly every run. Unknown models contribute 0
        (never hardcode rates); missing usage rows are priced at 0.
        """
        total_usd = Decimal(0)
        for entry in usage_by_result:
            model_id = entry.get("model_id")
            req_tokens = entry.get("request_tokens", 0) or 0
            resp_tokens = entry.get("response_tokens", 0) or 0
            if not model_id or (req_tokens <= 0 and resp_tokens <= 0):
                continue
            try:
                model = await LLMModel.objects.filter(model_id=model_id, is_active=True).first()
            except Exception as e:
                logger.warning(f"Cost lookup failed for model {model_id}: {e}")
                continue
            if not model:
                logger.warning(f"No llm_models row for {model_id}; cost contribution skipped")
                continue
            total_usd += Decimal(req_tokens) / 1000 * Decimal(str(model.input_cost_per_1k or 0.0)) + Decimal(
                resp_tokens
            ) / 1000 * Decimal(str(model.output_cost_per_1k or 0.0))
        cents = total_usd * 100
        return cents.quantize(self.COST_SCALE, rounding=ROUND_HALF_UP) if cents > 0 else Decimal(0)

    def _build_trace_payload(self, analysis_results: dict[str, Any]) -> dict[str, Any]:
        """Build response_payload trace: full responses in DEBUG, metadata otherwise."""
        if settings.DEBUG:
            return {
                k: (v.get("response", {}) if isinstance(v, dict) else v) for k, v in (analysis_results or {}).items()
            }
        trace: dict[str, Any] = {}
        for analysis_type, result in (analysis_results or {}).items():
            if not isinstance(result, dict):
                continue
            response = result.get("response") if isinstance(result.get("response"), dict) else {}
            parsed = result.get("parsed", {})
            trace[analysis_type] = {
                "model": (result.get("request") or {}).get("model"),
                "usage": response.get("usage", {}),
                "parsed_keys": sorted(parsed.keys()) if isinstance(parsed, dict) else [],
            }
        return trace

    async def _find_matching_topic_chain(
        self, source: Source, current_topics: list[str], lookback_days: int = 7
    ) -> Optional[str]:
        """
        Find existing topic chain that matches current analysis topics.

        This enables continuing topics detection across multiple analysis runs.
        Uses simple string matching to find topics that appear in recent analyses.

        Args:
                source: Source being analyzed
                current_topics: List of topics from current analysis
                lookback_days: How many days back to search for matching topics

        Returns:
                Existing topic_chain_id if match found, None otherwise
        """
        if not current_topics:
            return None

        # Get recent analyses for this source
        cutoff_date = date.today() - timedelta(days=lookback_days)
        recent_analyses = (
            await AIAnalytics.objects.filter(source_id=source.id, analysis_date__gte=cutoff_date)
            .order_by(AIAnalytics.analysis_date.desc())
            .limit(10)
        )

        if not recent_analyses:
            return None

        # Normalize current topics for comparison
        current_topics_normalized = [t.lower().strip() for t in current_topics if t]

        # Check each recent analysis for matching topics
        for analysis in recent_analyses:
            if not analysis.topic_chain_id or not analysis.summary_data:
                continue

            # Extract topics from previous analysis
            prev_topics = []
            multi_llm = analysis.summary_data.get("multi_llm_analysis", {})
            text_analysis = multi_llm.get("text_analysis", {})

            if "main_topics" in text_analysis:
                prev_topics.extend(text_analysis["main_topics"])

            # Also check unified summary
            unified = analysis.summary_data.get("unified_summary", {})
            if "main_themes" in unified:
                prev_topics.extend(unified["main_themes"])

            if not prev_topics:
                continue

            # Normalize previous topics
            prev_topics_normalized = [t.lower().strip() for t in prev_topics if t]

            # Check for matches (at least 50% overlap)
            matches = sum(1 for topic in current_topics_normalized if topic in prev_topics_normalized)
            match_ratio = matches / len(current_topics_normalized) if current_topics_normalized else 0

            if match_ratio >= 0.5:  # 50% of current topics match previous topics
                logger.info(
                    f"Found matching topic chain: {analysis.topic_chain_id} "
                    f"(match ratio: {match_ratio:.2f}, source: {source.id})"
                )
                return analysis.topic_chain_id

        return None

    def _generate_topic_chain_id(
        self, source: Source, main_topics: list[str], agent_scenario: AgentScenario = None, analyze_type: str = None
    ):
        """
        Generate topic chain ID for source.

        NEW LOGIC: One source + one scenario = one chain (timeline by dates).
        All analyses for this source+scenario go into the same chain.

        The mode changes what the chain follows:
          - "sources": a stable per-source chain (source + scenario only)
          - "monitored_users": per monitored user (source + scenario + author),
            so each tracked person's activity has its own timeline
          - otherwise: the chain is anchored on the top topic (themes), so an
            evolving theme keeps one chain across runs

        Args:
                source: Source being analysed
                main_topics: List of main topics from analysis
                agent_scenario: Bot scenario (optional)
                analyze_type: The scenario's analysis mode, if known

        Returns:
                Chain ID string
        """
        top_topic = main_topics[0] if main_topics else "general"
        normalized_topic = translit_slug(top_topic)[:20]

        scn = f"scn_{agent_scenario.id}" if agent_scenario and agent_scenario.id else "def"

        # The mode may arrive as an `AnalyzeType` member (straight off the ORM)
        # or as its db_value string (the CLI, the by-sources/by-users branches),
        # so compare on the normalised value.
        mode = get_enum_value(analyze_type)
        if mode == AnalyzeType.SOURCES.db_value:
            return f"src_{source.id}_{scn}_all"
        if mode == AnalyzeType.MONITORED_USERS.db_value:
            return f"src_{source.id}_{scn}_users"
        if agent_scenario and agent_scenario.id:
            return f"src_{source.id}_scn_{agent_scenario.id}_{normalized_topic}"
        else:
            return f"src_{source.id}_{normalized_topic}"

    @staticmethod
    def _resolve_chain_label(main_topics: list[str], analysis_results: dict[str, Any]) -> str:
        """Human-readable name for the topic chain.

        Prefers the top ``main_topics`` entry (the theme the chain follows),
        falls back to the text analysis ``analysis_title``, then to a generic
        label. Truncated so it fits the ``chain_label`` column.
        """
        if main_topics:
            label = str(main_topics[0]).strip()
        else:
            title = (analysis_results.get("text_analysis", {}).get("parsed", {}) or {}).get("analysis_title")
            label = str(title).strip() if title else "Общая тема"
        return label[:255]

    async def _save_analysis(
        self,
        analysis_results: dict[str, Any],
        unified_summary: Optional[dict[str, Any]],
        source: Source,
        content_stats: dict[str, Any],
        platform_name: str,
        agent_scenario: Optional["AgentScenario"] = None,
        topic_chain_id: Optional[str] = None,
        chain_label: Optional[str] = None,
        parent_analysis_id: Optional[int] = None,
        analysis_date: Optional[date] = None,
        content_hash: Optional[str] = None,
        content_hashes: Optional[list[str]] = None,
    ) -> Any | None:
        """Save comprehensive analysis results to database."""
        from datetime import date as date_class
        from datetime import datetime

        # Use provided date or default to today
        if analysis_date is None:
            analysis_date = date_class.today()

        # Relevance filtering (Phase 3): when the scenario's scope asks for it,
        # drop rows the model itself flagged as noise (`is_meaningful == false`)
        # or too uncertain (`confidence < min_confidence`). Happens BEFORE any
        # write, so a filtered day leaves no row behind and stays unanalysed.
        if agent_scenario is not None:
            scope = agent_scenario.scope or {}
            if scope.get("relevance_filter"):
                text_parsed = (analysis_results.get("text_analysis") or {}).get("parsed", {}) or {}
                is_meaningful = text_parsed.get("is_meaningful", True)
                confidence = text_parsed.get("confidence", 1.0)
                try:
                    confidence = float(confidence)
                except (TypeError, ValueError):
                    confidence = 1.0
                min_confidence = float(scope.get("min_confidence", DEFAULT_ANALYSIS_PARAMS["min_confidence"]))
                if not is_meaningful or confidence < min_confidence:
                    logger.info(
                        f"Relevance filter skipped source {source.id}: "
                        f"is_meaningful={is_meaningful}, confidence={confidence:.2f} < {min_confidence:.2f}"
                    )
                    self.filtered_skipped += 1
                    return None

        # Extract LLM tracing info from first available analysis
        llm_model = None
        prompt_text = None
        response_payload = {}
        # Per-result token usage with resolved model reference (model_id string + DB row)
        usage_by_result: list[dict[str, Any]] = []

        # Track cost metrics for aggregation
        total_request_tokens = 0
        total_response_tokens = 0
        providers_used = set()
        media_types_analyzed = set()

        for analysis_type, result in analysis_results.items():
            if result and isinstance(result, dict):
                llm_model = result.get("request", {}).get("model")
                prompt_text = result.get("request", {}).get("prompt")

                # Extract token usage from response
                response = result.get("response", {})
                usage = response.get("usage", {})

                req_tokens = usage.get("prompt_tokens", 0) or 0
                resp_tokens = usage.get("completion_tokens", 0) or 0
                total_request_tokens += req_tokens
                total_response_tokens += resp_tokens
                usage_by_result.append(
                    {
                        "model_id": result.get("request", {}).get("model"),
                        "request_tokens": req_tokens,
                        "response_tokens": resp_tokens,
                    }
                )

                # Extract provider from request
                provider = result.get("request", {}).get("provider")
                if provider:
                    providers_used.add(provider)

                # Track media type
                if "text" in analysis_type:
                    media_types_analyzed.add("text")
                elif "image" in analysis_type:
                    media_types_analyzed.add("image")
                elif "video" in analysis_type:
                    media_types_analyzed.add("video")

        # Safe enum/string handling for source_type
        st = getattr(source, "source_type", None)
        st_val = get_enum_value(st) if st is not None else ""

        # Extract analysis_title and analysis_summary from AI responses (prefer unified, fallback to text)
        analysis_title = None
        analysis_summary = None

        if unified_summary and unified_summary.get("parsed", {}):
            parsed = unified_summary["parsed"]
            if parsed.get("analysis_title"):
                analysis_title = parsed["analysis_title"]
            if parsed.get("analysis_summary"):
                analysis_summary = parsed["analysis_summary"]

        # Fallback to text_analysis
        if not analysis_title or not analysis_summary:
            text_parsed = analysis_results.get("text_analysis", {}).get("parsed", {})
            if not analysis_title and text_parsed.get("analysis_title"):
                analysis_title = text_parsed["analysis_title"]
            if not analysis_summary and text_parsed.get("analysis_summary"):
                analysis_summary = text_parsed["analysis_summary"]

        # Fallback to image/video
        if not analysis_title:
            if analysis_results.get("image_analysis", {}).get("parsed", {}).get("analysis_title"):
                analysis_title = analysis_results["image_analysis"]["parsed"]["analysis_title"]
            elif analysis_results.get("video_analysis", {}).get("parsed", {}).get("analysis_title"):
                analysis_title = analysis_results["video_analysis"]["parsed"]["analysis_title"]

        # Post-process analysis_title: ensure it contains date for event-based analysis
        if analysis_date and analysis_title:
            # Format date in human-readable Russian format
            import locale

            try:
                # Try to set Russian locale for proper month names
                locale.setlocale(locale.LC_TIME, "ru_RU.UTF-8")
            except:
                pass  # Fallback to default if Russian locale not available

            date_str = analysis_date.strftime("%d %B %Y")  # e.g., "17 октября 2025"

            # Check if title already contains date in various formats
            has_date = any(
                [
                    str(analysis_date.year) in analysis_title,
                    str(analysis_date.day) in analysis_title,
                    date_str.lower() in analysis_title.lower(),
                    "за день" in analysis_title.lower() or "за дату" in analysis_title.lower(),
                ]
            )

            if not has_date:
                # Prepend or append date to title
                if "активность" in analysis_title.lower():
                    analysis_title = f"Активность за {date_str}"
                else:
                    analysis_title = f"{analysis_title} ({date_str})"
                logger.info(f"Enhanced analysis_title with date: {analysis_title}")

        # Build comprehensive data structure
        comprehensive_data = {
            "analysis_title": analysis_title,  # AI-generated title for dashboard display
            "analysis_summary": analysis_summary,  # AI-generated summary for details display
            "multi_llm_analysis": {
                "text_analysis": analysis_results.get("text_analysis", {}).get("parsed", {}),
                "image_analysis": analysis_results.get("image_analysis", {}).get("parsed", {}),
                "video_analysis": analysis_results.get("video_analysis", {}).get("parsed", {}),
            },
            "unified_summary": unified_summary.get("parsed", {}) if unified_summary else {},
            "content_statistics": self._make_json_serializable(content_stats),
            "source_metadata": {"source_type": st_val, "platform": platform_name, "source_name": source.name},
            "analysis_metadata": {
                "analysis_version": "3.0-multi-llm",
                "analysis_timestamp": datetime.now(UTC).isoformat(),
                "content_samples_analyzed": content_stats.get("total_posts", 0),
                "llm_providers_used": len([r for r in analysis_results.values() if r]),
            },
        }

        # Dedup ledger: hashes of the items this run analyzed (merged with the
        # previous set on the update branch below).
        if content_hashes is not None:
            comprehensive_data["content_hashes"] = content_hashes

        # Add scenario information if used
        if agent_scenario:
            comprehensive_data["scenario_metadata"] = {
                "scenario_id": agent_scenario.id,
                "scenario_name": agent_scenario.name,
                "analysis_types": agent_scenario.analysis_types,
                "content_types": agent_scenario.content_types,
            }

        # Cost from llm_models prices (input_cost_per_1k / output_cost_per_1k, USD).
        # estimated_cost stores USD cents with sub-cent precision; NULL is kept for
        # "unknown or free" so a zero-cost row never looks like a pricing bug.
        estimated_cost_cents = await self._price_usage(usage_by_result)

        # Debug tracing only: full LLM responses go to response_payload when
        # DEBUG is on; in production keep metadata (usage + parsed keys) only,
        # so raw content is never persisted (vision.md invariant).
        response_payload = self._build_trace_payload(analysis_results)

        # Primary provider (most used)
        primary_provider = list(providers_used)[0] if providers_used else None

        # Check if analysis already exists for this date
        existing_analysis = await AIAnalytics.objects.filter(
            source_id=source.id, analysis_date=analysis_date, period_type=PeriodType.DAY
        ).first()

        if existing_analysis:
            # Merge dedup sets: a partial re-run must not forget (and re-pay
            # for) items covered by earlier analyses of the same day.
            existing_hashes = (existing_analysis.summary_data or {}).get("content_hashes") or []
            merged_hashes = sorted(set(existing_hashes) | set(content_hashes or []))
            if merged_hashes:
                comprehensive_data["content_hashes"] = merged_hashes
                content_hash = hashes_hash(merged_hashes)
            elif content_hash is None:
                content_hash = existing_analysis.content_hash

            # Update existing analysis using manager
            updated_analysis = await AIAnalytics.objects.update_by_id(
                existing_analysis.id,
                summary_data=comprehensive_data,
                content_hash=content_hash,
                llm_model=llm_model or "multi-llm",
                prompt_text=prompt_text if settings.DEBUG else None,
                response_payload=self._make_json_serializable(response_payload) if response_payload else None,
                topic_chain_id=topic_chain_id or existing_analysis.topic_chain_id,
                chain_label=chain_label or existing_analysis.chain_label,
                # Preserve existing chain_id or set new one
                parent_analysis_id=parent_analysis_id,
                request_tokens=total_request_tokens if total_request_tokens > 0 else None,
                response_tokens=total_response_tokens if total_response_tokens > 0 else None,
                estimated_cost=estimated_cost_cents if estimated_cost_cents > 0 else None,
                provider_type=primary_provider,
                media_types=list(media_types_analyzed) if media_types_analyzed else None,
            )

            scenario_info = f" using scenario '{agent_scenario.name}'" if agent_scenario else ""
            logger.info(
                f"Multi-LLM analysis updated for source {source.id}{scenario_info} "
                f"(analytics_id: {existing_analysis.id}, providers: {len(analysis_results)})"
            )
            return updated_analysis

        # Create analytics record
        analytics = await AIAnalytics.objects.create(
            source_id=source.id,
            summary_data=comprehensive_data,
            content_hash=content_hash,
            llm_model=llm_model or "multi-llm",
            prompt_text=prompt_text if settings.DEBUG else None,
            response_payload=self._make_json_serializable(response_payload) if response_payload else None,
            analysis_date=analysis_date,
            period_type=PeriodType.DAY,
            topic_chain_id=topic_chain_id,
            chain_label=chain_label,
            parent_analysis_id=parent_analysis_id,
            # Cost tracking fields
            request_tokens=total_request_tokens if total_request_tokens > 0 else None,
            response_tokens=total_response_tokens if total_response_tokens > 0 else None,
            estimated_cost=estimated_cost_cents if estimated_cost_cents > 0 else None,
            provider_type=primary_provider,
            media_types=list(media_types_analyzed) if media_types_analyzed else None,
        )

        scenario_info = f" using scenario '{agent_scenario.name}'" if agent_scenario else ""
        logger.info(
            f"Multi-LLM analysis saved for source {source.id}{scenario_info} "
            f"(analytics_id: {analytics.id}, providers: {len(analysis_results)})"
        )
        return analytics
