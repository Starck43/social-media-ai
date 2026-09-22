import logging
from datetime import UTC, date, timedelta
from typing import Optional, Any

from app.models import Source, AIAnalytics, BotScenario, LLMProvider, LLMModel
from app.services.ai.content_classifier import ContentClassifier
from app.services.ai.llm_client import LLMClientFactory
from app.services.ai.llm_provider_resolver import LLMProviderResolver
from app.services.ai.prompts import PromptBuilder
from app.services.ai.theme_matcher import ThemeMatcher
from app.types import PeriodType
from app.types.enums.llm_types import MediaType
from app.utils.date_parsing import universal_date_parser
from app.utils.enum_helpers import get_enum_value

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

	async def analyze_content(self, content: list[dict], source: Source, analyze_by: str = None) -> list[AIAnalytics]:
		"""
		Analyze content based on analyze_by mode.

		Args:
			content: List of content items
			source: Source being analyzed
			analyze_by: Analysis mode: "days" or "themes"

		Returns:
			List of AIAnalytics records (one per day with activity)
		"""
		analyze_by = analyze_by or source.bot_scenario.analyze_type

		if analyze_by == "themes":
			return await self._analyze_content_by_themes(content, source)
		else:
			return await self._analyze_content_by_days(content, source)

	async def base_analyze_content(
			self,
			content: list[dict],
			source: Source,
			topic_chain_id: Optional[str] = None,
			parent_analysis_id: Optional[int] = None,
			analysis_date: Optional[date] = None,
	) -> Optional[AIAnalytics]:
		"""
		Comprehensive analysis of collected content using multiple LLM providers.

		Args:
			content: List of normalized content items
			source: Source from which content was collected
			topic_chain_id: Optional chain ID for ongoing topics
			parent_analysis_id: Optional parent analysis ID for threaded analysis
			analysis_date: Optional date to use for this analysis (defaults to today)

		Returns:
			AIAnalytics object with complete analysis results or None if failed
		"""
		if not content:
			logger.warning(f"No content to analyze for source {source.id}")
			return None

		# Load bot scenario if assigned
		bot_scenario = None
		if source.bot_scenario_id:
			try:
				bot_scenario = await BotScenario.objects.get(id=source.bot_scenario_id)
				logger.info(
					f"Using bot scenario '{bot_scenario.name}' (ID: {bot_scenario.id}) "
					f"for source {source.id}"
				)
			except Exception as e:
				logger.warning(f"Failed to load bot scenario {source.bot_scenario_id}: {e}")

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
					bot_scenario,
					content_stats,
					platform_name,
					source
				)
				if text_result:
					analysis_results['text_analysis'] = text_result

			# Image analysis
			if classified[MediaType.IMAGE.db_value]:
				image_result = await self._analyze_images(
					classified[MediaType.IMAGE.db_value],
					bot_scenario,
					platform_name
				)
				if image_result:
					analysis_results['image_analysis'] = image_result

			# Video analysis
			if classified[MediaType.VIDEO.db_value]:
				video_result = await self._analyze_videos(
					classified[MediaType.VIDEO.db_value],
					bot_scenario,
					platform_name
				)
				if video_result:
					analysis_results['video_analysis'] = video_result

			# Check if we have any meaningful analysis results
			has_results = False
			for result in analysis_results.values():
				if result and result.get('parsed'):
					has_results = True
					break

			if not has_results:
				logger.warning(f"No meaningful analysis results for source {source.id}, skipping save")
				return None

			# Create unified summary if multiple analyses
			unified_summary = await self._create_unified_summary(analysis_results, bot_scenario)

			# Auto-generate topic_chain_id if not provided
			# NEW LOGIC: One source + one scenario = one chain (timeline by dates)
			if not topic_chain_id:
				topic_chain_id = self._generate_topic_chain_id(source, bot_scenario)
				logger.info(f"Using topic chain: {topic_chain_id} for source {source.id}")

			# Save comprehensive analysis
			analysis = await self._save_analysis(
				analysis_results,
				unified_summary,
				source,
				content_stats,
				platform_name,
				bot_scenario,
				topic_chain_id,
				parent_analysis_id,
				analysis_date,
			)

			return analysis

		except Exception as e:
			logger.error(f"Error analyzing content for source {source.id}: {e}", exc_info=True)
			return None

	async def _analyze_content_by_days(self, content: list[dict], source: Source) -> list[AIAnalytics]:
		"""
		Group content by days and analyze each day separately.

		Args:
			content: List of content items
			source: Source being analyzed

		Returns:
			List of AIAnalytics records (one per day with activity)
		"""
		from collections import defaultdict

		if not content:
			logger.warning(f"No content to analyze for source {source.id}")
			return []

		# Group content by day
		content_by_day = defaultdict(list)

		for item in content:
			# Extract publication date
			pub_date = item.get('published_at') or item.get('date') or item.get('created_at')

			# Convert to datetime
			pub_date = universal_date_parser(pub_date)

			# Group by date
			if pub_date:
				day = pub_date.date()
			else:
				day = date.today()

			content_by_day[day].append(item)

		logger.info(f"Grouped {len(content)} items into {len(content_by_day)} days for source {source.id}")

		# Analyze each day using base analysis
		analytics_list = []

		for day, day_content in sorted(content_by_day.items()):
			logger.info(f"Analyzing {len(day_content)} items for source {source.id} on {day}")

			try:
				# Use base analysis for each day
				analytics = await self.base_analyze_content(
					content=day_content,
					source=source,
					analysis_date=day
				)

				# Only add non-empty analytics
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

	async def _analyze_content_by_themes(self, content: list[dict], source: Source) -> list[AIAnalytics]:
		"""
		Analyze content with automatic theme detection and linking.

		Args:
			content: List of content items
			source: Source being analyzed

		Returns:
			List of AIAnalytics records (typically one record with theme linking)
		"""
		if not content:
			logger.warning(f"No content to analyze for source {source.id}")
			return []

		# Use base analysis for all content
		analysis = await self.base_analyze_content(content, source)
		if not analysis:
			return []

		# Apply theme linking
		await self._auto_link_to_existing_theme(analysis, source)

		return [analysis]

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
			await AIAnalytics.objects.update_by_id(
				analysis.id,
				main_topics=main_topics
			)

			# Find similar existing analytics using pre-initialized matcher
			similar_analytics = await self.theme_matcher.find_similar_analytics(
				main_topics, source.id
			)

			if similar_analytics and similar_analytics.topic_chain_id:
				# Link to existing theme chain
				await AIAnalytics.objects.update_by_id(
					analysis.id,
					topic_chain_id=similar_analytics.topic_chain_id
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
			text_analysis = summary_data.get('multi_llm_analysis', {}).get('text_analysis', {})
			if text_analysis and 'main_topics' in text_analysis:
				return text_analysis['main_topics']

			# Try unified summary
			unified_summary = summary_data.get('unified_summary', {})
			if unified_summary and 'main_topics' in unified_summary:
				return unified_summary['main_topics']

			# Fallback to empty list
			return []

		except Exception as e:
			logger.warning(f"Error extracting main_topics: {e}")
			return []

	async def _analyze_text(
			self,
			text_items: list[dict],
			bot_scenario: Optional[BotScenario],
			content_stats: dict[str, Any],
			platform_name: str,
			source: Source
	) -> Optional[dict[str, Any]]:
		"""Analyze text content using text LLM provider."""
		try:
			# Get LLM provider for text
			model = await self._get_llm_model(bot_scenario, MediaType.TEXT)
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
				scenario=bot_scenario,
				text=text_content,
				stats=content_stats,
				platform_name=platform_name,
				source_type=stype
			)

			# Create LLM client and analyze
			client = await LLMClientFactory.create(model)
			result = await client.analyze(prompt)

			logger.info(f"Text analysis completed using {model.name}")
			return result

		except Exception as e:
			logger.error(f"Error in text analysis: {e}", exc_info=True)
			return None

	async def _analyze_images(
			self,
			image_items: list[dict],
			bot_scenario: Optional[BotScenario],
			platform_name: str
	) -> Optional[dict[str, Any]]:
		"""Analyze images using image LLM provider."""
		try:
			# Get LLM provider for images
			provider = await self._get_llm_model(bot_scenario, MediaType.IMAGE)
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
				scenario=bot_scenario,
				count=len(media_urls),
				platform_name=platform_name
			)

			# Create LLM client and analyze
			client = await LLMClientFactory.create(provider)
			result = await client.analyze(prompt, media_urls=media_urls)

			logger.info(f"Image analysis completed using {provider.name}, analyzed {len(media_urls)} images")
			return result

		except Exception as e:
			logger.error(f"Error in image analysis: {e}", exc_info=True)
			return None

	async def _analyze_videos(
			self,
			video_items: list[dict],
			bot_scenario: Optional[BotScenario],
			platform_name: str
	) -> Optional[dict[str, Any]]:
		"""Analyze videos using video LLM provider."""
		try:
			# Get LLM provider for videos
			provider = await self._get_llm_model(bot_scenario, MediaType.VIDEO)
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
				scenario=bot_scenario,
				count=len(media_urls),
				platform_name=platform_name
			)

			# Create LLM client and analyze
			client = await LLMClientFactory.create(provider)
			result = await client.analyze(prompt, media_urls=media_urls)

			logger.info(f"Video analysis completed using {provider.name}, analyzed {len(media_urls)} videos")
			return result

		except Exception as e:
			logger.error(f"Error in video analysis: {e}", exc_info=True)
			return None

	async def _create_unified_summary(
			self,
			analysis_results: dict[str, Any],
			bot_scenario: Optional[BotScenario]
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
			model = await self._get_llm_model(bot_scenario, MediaType.TEXT)
			if not model:
				logger.warning("No text LLM provider for unified summary")
				return None

			# Extract parsed results
			text_analysis = analysis_results.get('text_analysis', {}).get('parsed', {})
			image_analysis = analysis_results.get('image_analysis', {}).get('parsed', {})
			video_analysis = analysis_results.get('video_analysis', {}).get('parsed', {})

			# Build unification prompt using new unified system
			prompt = PromptBuilder.get_unified_summary_prompt(
				text_analysis,
				image_analysis,
				video_analysis,
				scenario=bot_scenario
			)

			# Create summary
			client = await LLMClientFactory.create(model)
			result = await client.analyze(prompt)

			logger.info("Unified summary created successfully")
			return result

		except Exception as e:
			logger.error(f"Error creating unified summary: {e}", exc_info=True)
			return None

	async def _get_llm_model(
			self,
			bot_scenario: Optional[BotScenario],
			media_type: MediaType | str
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
		if bot_scenario:
			model_id = None

			if media_type == MediaType.TEXT and bot_scenario.text_llm_model_id:
				model_id = bot_scenario.text_llm_model_id
			elif media_type == MediaType.IMAGE and bot_scenario.image_llm_model_id:
				model_id = bot_scenario.image_llm_model_id
			elif media_type == MediaType.VIDEO and bot_scenario.video_llm_model_id:
				model_id = bot_scenario.video_llm_model_id

			# Load model and get its provider
			if model_id:
				try:
					# Load model with provider relationship to avoid session issues
					model = await LLMModel.objects.select_related('provider').get(id=model_id)
					if model.is_active and model.provider.is_active:
						logger.info(f"✅ Select model {model.name} (provider: {model.provider.name}) for {media_type}")
						return model

				except Exception as e:
					logger.warning(f"Failed to load model {model_id}: {e}, trying fallback")

		# Priority 2: Auto-resolve by llm_strategy (fallback)
		if bot_scenario and bot_scenario.llm_strategy:
			try:
				model = await self._auto_resolve_model(bot_scenario, media_type)
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

	async def _auto_resolve_model(
			self,
			bot_scenario: BotScenario,
			media_type: MediaType
	) -> Optional[LLMModel]:
		"""Auto-resolve provider using strategy-based approach."""

		# Get all active providers and their models
		all_providers = await LLMProvider.objects.filter(is_active=True)
		all_models = await LLMModel.objects.filter(provider__is_active=True, is_active=True)

		# Group models by provider and find best model for each provider
		provider_best_models = {}
		for model in all_models:
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

			available[provider.id] = (
				provider_type,
				model.name,
				model.capabilities or []
			)

		if not available:
			logger.error("No active providers available for auto-resolve")
			return None

		# Resolve by strategy
		resolved = LLMProviderResolver.resolve_for_content_types(
			content_types=bot_scenario.content_types or [],
			available_providers=available,
			strategy=str(bot_scenario.llm_strategy)
		)

		# Get provider for this media type
		media_type_str = media_type.value if hasattr(media_type, 'value') else str(media_type)
		if media_type_str in resolved:
			provider_config = resolved[media_type_str]
			provider_id = provider_config['provider_id']
			provider = await LLMProvider.objects.get(id=provider_id)
			logger.info(
				f"✅ Auto-resolved provider {provider.name} for {media_type} "
				f"using strategy '{bot_scenario.llm_strategy}'"
			)
			return provider

		return None

	def _get_generic_provider_type(self, capabilities: list[str]) -> str:
		"""Get generic provider type based on capabilities instead of rigid URL parsing."""
		if not capabilities:
			return 'text'  # Default fallback

		# Determine primary capability for categorization
		if 'video' in capabilities:
			return 'multimodal'  # Video usually implies multimodal capabilities
		elif 'image' in capabilities and 'text' in capabilities:
			return 'multimodal'
		elif 'image' in capabilities:
			return 'vision'
		elif 'text' in capabilities:
			return 'text'
		else:
			return 'specialized'

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

		# Extract and parse post dates using existing utility
		post_dates = []
		for item in content:
			pub_date = item.get('published_at') or item.get('date') or item.get('created_at')
			if parsed_date := universal_date_parser(pub_date):
				post_dates.append(parsed_date)

		# Calculate content date range
		content_date_range = {}
		if post_dates:
			min_date, max_date = min(post_dates), max(post_dates)
			content_date_range = {
				'earliest': min_date.isoformat(),
				'latest': max_date.isoformat(),
				'span_days': (max_date - min_date).days
			}

		# Determine date range for context
		if analysis_date and post_dates:
			date_range_dict = {
				"first": analysis_date.isoformat(),
				"last": analysis_date.isoformat()
			}
		else:
			# Use actual dates from content (fallback to None if no dates)
			dates = [item.get("date") for item in content if item.get("date")]
			date_range_dict = {
				"first": min(dates) if dates else None,
				"last": max(dates) if dates else None
			}

		# Calculate all statistics
		total_posts = len(content)
		total_reactions = sum(reactions)
		total_comments = sum(comments)

		return {
			"total_posts": total_posts,
			"avg_text_length": sum(len(t) for t in texts) / total_posts if texts else 0,
			"total_reactions": total_reactions,
			"total_comments": total_comments,
			"avg_reactions_per_post": total_reactions / total_posts if total_posts else 0,
			"avg_comments_per_post": total_comments / total_posts if total_posts else 0,
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

	async def _find_matching_topic_chain(
			self,
			source: Source,
			current_topics: list[str],
			lookback_days: int = 7
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
		recent_analyses = await AIAnalytics.objects.filter(
			source_id=source.id,
			analysis_date__gte=cutoff_date
		).order_by(AIAnalytics.analysis_date.desc()).limit(10)

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
			multi_llm = analysis.summary_data.get('multi_llm_analysis', {})
			text_analysis = multi_llm.get('text_analysis', {})

			if 'main_topics' in text_analysis:
				prev_topics.extend(text_analysis['main_topics'])

			# Also check unified summary
			unified = analysis.summary_data.get('unified_summary', {})
			if 'main_themes' in unified:
				prev_topics.extend(unified['main_themes'])

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
			self, source: Source,
			main_topics: list[str],
			bot_scenario: BotScenario = None
	):
		"""
		Generate topic chain ID for source.
		
		NEW LOGIC: One source + one scenario = one chain (timeline by dates).
		All analyses for this source+scenario go into the same chain.
		
		Args:
			source: Source being analysed
			main_topics: List of main topics from analysis
			bot_scenario: Bot scenario (optional)
		
		Returns:
			Chain ID string: "source_{id}" or "source_{id}_scenario_{id}"
		"""
		top_topic = main_topics[0] if main_topics else "general"
		normalized_topic = "".join(c for c in top_topic.lower() if c.isalnum())[:20]

		if bot_scenario and bot_scenario.id:
			return f"src_{source.id}_scn_{bot_scenario.id}_{normalized_topic}"
		else:
			return f"src_{source.id}_{normalized_topic}"

	async def _save_analysis(
			self,
			analysis_results: dict[str, Any],
			unified_summary: Optional[dict[str, Any]],
			source: Source,
			content_stats: dict[str, Any],
			platform_name: str,
			bot_scenario: Optional['BotScenario'] = None,
			topic_chain_id: Optional[str] = None,
			parent_analysis_id: Optional[int] = None,
			analysis_date: Optional[date] = None,
	) -> AIAnalytics:
		"""Save comprehensive analysis results to database."""
		from datetime import date as date_class, datetime

		# Use provided date or default to today
		if analysis_date is None:
			analysis_date = date_class.today()

		# Extract LLM tracing info from first available analysis
		llm_model = None
		prompt_text = None
		response_payload = {}

		# Track cost metrics for aggregation
		total_request_tokens = 0
		total_response_tokens = 0
		total_cost = 0
		providers_used = set()
		media_types_analyzed = set()

		for analysis_type, result in analysis_results.items():
			if result and isinstance(result, dict):
				llm_model = result.get('request', {}).get('model')
				prompt_text = result.get('request', {}).get('prompt')
				response_payload[analysis_type] = result.get('response', {})

				# Extract token usage from response
				response = result.get('response', {})
				usage = response.get('usage', {})

				total_request_tokens += usage.get('prompt_tokens', 0)
				total_response_tokens += usage.get('completion_tokens', 0)

				# Extract provider from request
				provider = result.get('request', {}).get('provider')
				if provider:
					providers_used.add(provider)

				# Track media type
				if 'text' in analysis_type:
					media_types_analyzed.add('text')
				elif 'image' in analysis_type:
					media_types_analyzed.add('image')
				elif 'video' in analysis_type:
					media_types_analyzed.add('video')

		# Safe enum/string handling for source_type
		st = getattr(source, "source_type", None)
		st_val = get_enum_value(st) if st is not None else ""

		# Extract analysis_title and analysis_summary from AI responses (prefer unified, fallback to text)
		analysis_title = None
		analysis_summary = None

		if unified_summary and unified_summary.get('parsed', {}):
			parsed = unified_summary['parsed']
			if parsed.get('analysis_title'):
				analysis_title = parsed['analysis_title']
			if parsed.get('analysis_summary'):
				analysis_summary = parsed['analysis_summary']

		# Fallback to text_analysis
		if not analysis_title or not analysis_summary:
			text_parsed = analysis_results.get('text_analysis', {}).get('parsed', {})
			if not analysis_title and text_parsed.get('analysis_title'):
				analysis_title = text_parsed['analysis_title']
			if not analysis_summary and text_parsed.get('analysis_summary'):
				analysis_summary = text_parsed['analysis_summary']

		# Fallback to image/video
		if not analysis_title:
			if analysis_results.get('image_analysis', {}).get('parsed', {}).get('analysis_title'):
				analysis_title = analysis_results['image_analysis']['parsed']['analysis_title']
			elif analysis_results.get('video_analysis', {}).get('parsed', {}).get('analysis_title'):
				analysis_title = analysis_results['video_analysis']['parsed']['analysis_title']

		# Post-process analysis_title: ensure it contains date for event-based analysis
		if analysis_date and analysis_title:
			# Format date in human-readable Russian format
			import locale
			try:
				# Try to set Russian locale for proper month names
				locale.setlocale(locale.LC_TIME, 'ru_RU.UTF-8')
			except:
				pass  # Fallback to default if Russian locale not available

			date_str = analysis_date.strftime('%d %B %Y')  # e.g., "17 октября 2025"

			# Check if title already contains date in various formats
			has_date = any([
				str(analysis_date.year) in analysis_title,
				str(analysis_date.day) in analysis_title,
				date_str.lower() in analysis_title.lower(),
				'за день' in analysis_title.lower() or 'за дату' in analysis_title.lower()
			])

			if not has_date:
				# Prepend or append date to title
				if 'активность' in analysis_title.lower():
					analysis_title = f"Активность за {date_str}"
				else:
					analysis_title = f"{analysis_title} ({date_str})"
				logger.info(f"Enhanced analysis_title with date: {analysis_title}")

		# Build comprehensive data structure
		comprehensive_data = {
			"analysis_title": analysis_title,  # AI-generated title for dashboard display
			"analysis_summary": analysis_summary,  # AI-generated summary for details display
			"multi_llm_analysis": {
				"text_analysis": analysis_results.get('text_analysis', {}).get('parsed', {}),
				"image_analysis": analysis_results.get('image_analysis', {}).get('parsed', {}),
				"video_analysis": analysis_results.get('video_analysis', {}).get('parsed', {}),
			},
			"unified_summary": unified_summary.get('parsed', {}) if unified_summary else {},
			"content_statistics": self._make_json_serializable(content_stats),
			"source_metadata": {
				"source_type": st_val,
				"platform": platform_name,
				"source_name": source.name
			},
			"analysis_metadata": {
				"analysis_version": "3.0-multi-llm",
				"analysis_timestamp": datetime.now(UTC).isoformat(),
				"content_samples_analyzed": content_stats.get("total_posts", 0),
				"llm_providers_used": len([r for r in analysis_results.values() if r])
			},
		}

		# Add scenario information if used
		if bot_scenario:
			comprehensive_data["scenario_metadata"] = {
				"scenario_id": bot_scenario.id,
				"scenario_name": bot_scenario.name,
				"analysis_types": bot_scenario.analysis_types,
				"content_types": bot_scenario.content_types,
			}

		# Estimate cost (simple estimation, can be refined)
		# Assuming average $0.01 per 1000 tokens (varies by provider)
		total_tokens = total_request_tokens + total_response_tokens
		# Use max(1, ...) to ensure at least 1 cent if tokens were used
		estimated_cost_cents = max(1, int((total_tokens / 1000) * 100)) if total_tokens > 0 else 0

		# Primary provider (most used)
		primary_provider = list(providers_used)[0] if providers_used else None

		# Check if analysis already exists for this date
		existing_analysis = await AIAnalytics.objects.filter(
			source_id=source.id,
			analysis_date=analysis_date,
			period_type=PeriodType.DAILY
		).first()

		if existing_analysis:
			# Update existing analysis using manager
			updated_analysis = await AIAnalytics.objects.update_by_id(
				existing_analysis.id,
				summary_data=comprehensive_data,
				llm_model=llm_model or "multi-llm",
				prompt_text=prompt_text,
				response_payload=self._make_json_serializable(response_payload) if response_payload else None,
				topic_chain_id=topic_chain_id or existing_analysis.topic_chain_id,
				# Preserve existing chain_id or set new one
				parent_analysis_id=parent_analysis_id,
				request_tokens=total_request_tokens if total_request_tokens > 0 else None,
				response_tokens=total_response_tokens if total_response_tokens > 0 else None,
				estimated_cost=estimated_cost_cents if estimated_cost_cents > 0 else None,
				provider_type=primary_provider,
				media_types=list(media_types_analyzed) if media_types_analyzed else None,
			)

			scenario_info = f" using scenario '{bot_scenario.name}'" if bot_scenario else ""
			logger.info(
				f"Multi-LLM analysis updated for source {source.id}{scenario_info} "
				f"(analytics_id: {existing_analysis.id}, providers: {len(analysis_results)})"
			)
			return updated_analysis

		# Create analytics record
		analytics = await AIAnalytics.objects.create(
			source_id=source.id,
			summary_data=comprehensive_data,
			llm_model=llm_model or "multi-llm",
			prompt_text=prompt_text,
			response_payload=self._make_json_serializable(response_payload) if response_payload else None,
			analysis_date=analysis_date,
			period_type=PeriodType.DAILY,
			topic_chain_id=topic_chain_id,
			parent_analysis_id=parent_analysis_id,
			# Cost tracking fields
			request_tokens=total_request_tokens if total_request_tokens > 0 else None,
			response_tokens=total_response_tokens if total_response_tokens > 0 else None,
			estimated_cost=estimated_cost_cents if estimated_cost_cents > 0 else None,
			provider_type=primary_provider,
			media_types=list(media_types_analyzed) if media_types_analyzed else None,
		)

		scenario_info = f" using scenario '{bot_scenario.name}'" if bot_scenario else ""
		logger.info(
			f"Multi-LLM analysis saved for source {source.id}{scenario_info} "
			f"(analytics_id: {analytics.id}, providers: {len(analysis_results)})"
		)
		return analytics
