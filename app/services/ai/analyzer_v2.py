import logging
from datetime import UTC
from typing import Optional, Any, Dict
import json

from app.core.config import settings
from app.models import Source, AIAnalytics, BotScenario, LLMProvider
from app.types import PeriodType
from app.services.ai.llm_client import LLMClientFactory
from app.services.ai.content_classifier import ContentClassifier
from app.services.ai.prompts import PromptBuilder

logger = logging.getLogger(__name__)


class AIAnalyzerV2:
	"""
	Enhanced AI Analyzer with multi-LLM support.
	
	This service handles:
	— Classification of content by media type (text, image, video)
	— Selection of appropriate LLM providers for each content type
	— Parallel analysis using multiple specialized LLMs
	— Unification of results into a single comprehensive summary
	— Full LLM tracing for debugging and monitoring
	"""

	async def analyze_content(
			self,
			content: list[dict],
			source: Source,
			topic_chain_id: Optional[str] = None,
			parent_analysis_id: Optional[int] = None,
	) -> Optional[AIAnalytics]:
		"""
		Comprehensive analysis of collected content using multiple LLM providers.

		Args:
			content: List of normalized content items
			source: Source from which content was collected
			topic_chain_id: Optional chain ID for ongoing topics
			parent_analysis_id: Optional parent analysis ID for threaded analysis

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
		content_stats = self._calculate_content_stats(content)
		platform_name = await self._get_platform_name(source)

		# Classify content by media type
		classified = ContentClassifier.classify_content(content)
		
		try:
			# Analyze each content type with appropriate LLM
			analysis_results = {}
			
			# Text analysis
			if classified['text']:
				text_result = await self._analyze_text(
					classified['text'],
					bot_scenario,
					content_stats,
					platform_name,
					source
				)
				if text_result:
					analysis_results['text_analysis'] = text_result
			
			# Image analysis
			if classified['images']:
				image_result = await self._analyze_images(
					classified['images'],
					bot_scenario,
					platform_name
				)
				if image_result:
					analysis_results['image_analysis'] = image_result
			
			# Video analysis
			if classified['videos']:
				video_result = await self._analyze_videos(
					classified['videos'],
					bot_scenario,
					platform_name
				)
				if video_result:
					analysis_results['video_analysis'] = video_result
			
			# Create unified summary if multiple analyses
			unified_summary = await self._create_unified_summary(analysis_results, bot_scenario)
			
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
			)
			
			return analysis

		except Exception as e:
			logger.error(f"Error analyzing content for source {source.id}: {e}", exc_info=True)
			return None

	async def _analyze_text(
		self,
		text_items: list[dict],
		bot_scenario: Optional[BotScenario],
		content_stats: Dict[str, Any],
		platform_name: str,
		source: Source
	) -> Optional[Dict[str, Any]]:
		"""Analyze text content using text LLM provider."""
		try:
			# Get LLM provider for text
			provider = await self._get_llm_provider(bot_scenario, 'text')
			if not provider:
				logger.warning("No text LLM provider configured, skipping text analysis")
				return None
			
			# Prepare text content
			text_content = ContentClassifier.prepare_text_content(text_items)
			
			# Build prompt
			if bot_scenario and bot_scenario.ai_prompt:
				from app.services.ai.scenario import ScenarioPromptBuilder
				context = {
					'platform': platform_name,
					'source_type': source.source_type.value if hasattr(source.source_type, 'value') else str(source.source_type),
					'total_posts': len(text_items),
					'content': text_content,
					'date_range': content_stats.get('date_range'),
				}
				prompt = ScenarioPromptBuilder.build_prompt(bot_scenario, context)
			else:
				source_type = getattr(source, "source_type", None)
				stype = source_type.value if hasattr(source_type, "value") else str(source_type) if source_type else ""
				prompt = PromptBuilder.build_text_prompt(text_content, content_stats, platform_name, stype)
			
			# Create LLM client and analyze
			client = LLMClientFactory.create(provider)
			result = await client.analyze(prompt)
			
			logger.info(f"Text analysis completed using {provider.name}")
			return result
			
		except Exception as e:
			logger.error(f"Error in text analysis: {e}", exc_info=True)
			return None

	async def _analyze_images(
		self,
		image_items: list[dict],
		bot_scenario: Optional[BotScenario],
		platform_name: str
	) -> Optional[Dict[str, Any]]:
		"""Analyze images using image LLM provider."""
		try:
			# Get LLM provider for images
			provider = await self._get_llm_provider(bot_scenario, 'image')
			if not provider:
				logger.warning("No image LLM provider configured, skipping image analysis")
				return None
			
			# Extract image URLs
			media_urls = ContentClassifier.get_media_urls(image_items)
			if not media_urls:
				return None
			
			# Build prompt
			prompt = PromptBuilder.build_image_prompt(len(media_urls), platform_name)
			
			# Create LLM client and analyze
			client = LLMClientFactory.create(provider)
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
	) -> Optional[Dict[str, Any]]:
		"""Analyze videos using video LLM provider."""
		try:
			# Get LLM provider for videos
			provider = await self._get_llm_provider(bot_scenario, 'video')
			if not provider:
				logger.warning("No video LLM provider configured, skipping video analysis")
				return None
			
			# Extract video URLs
			media_urls = ContentClassifier.get_media_urls(video_items)
			if not media_urls:
				return None
			
			# Build prompt
			prompt = PromptBuilder.build_video_prompt(len(media_urls), platform_name)
			
			# Create LLM client and analyze
			client = LLMClientFactory.create(provider)
			result = await client.analyze(prompt, media_urls=media_urls)
			
			logger.info(f"Video analysis completed using {provider.name}, analyzed {len(media_urls)} videos")
			return result
			
		except Exception as e:
			logger.error(f"Error in video analysis: {e}", exc_info=True)
			return None

	async def _create_unified_summary(
		self,
		analysis_results: Dict[str, Any],
		bot_scenario: Optional[BotScenario]
	) -> Optional[Dict[str, Any]]:
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
			provider = await self._get_llm_provider(bot_scenario, 'text')
			if not provider:
				logger.warning("No text LLM provider for unified summary")
				return None
			
			# Extract parsed results
			text_analysis = analysis_results.get('text_analysis', {}).get('parsed', {})
			image_analysis = analysis_results.get('image_analysis', {}).get('parsed', {})
			video_analysis = analysis_results.get('video_analysis', {}).get('parsed', {})
			
			# Build unification prompt
			prompt = PromptBuilder.build_unified_summary_prompt(
				text_analysis,
				image_analysis,
				video_analysis
			)
			
			# Create summary
			client = LLMClientFactory.create(provider)
			result = await client.analyze(prompt)
			
			logger.info("Unified summary created successfully")
			return result
			
		except Exception as e:
			logger.error(f"Error creating unified summary: {e}", exc_info=True)
			return None

	async def _get_llm_provider(
		self,
		bot_scenario: Optional[BotScenario],
		media_type: str
	) -> Optional[LLMProvider]:
		"""
		Get appropriate LLM provider for media type.
		
		Args:
			bot_scenario: Bot scenario with provider configuration
			media_type: Type of media (text, image, video)
			
		Returns:
			LLMProvider instance or None
		"""
		provider_id = None
		
		# Try to get from scenario first
		if bot_scenario:
			if media_type == 'text':
				provider_id = bot_scenario.text_llm_provider_id
			elif media_type == 'image':
				provider_id = bot_scenario.image_llm_provider_id
			elif media_type == 'video':
				provider_id = bot_scenario.video_llm_provider_id
		
		# Load provider if configured
		if provider_id:
			try:
				provider = await LLMProvider.objects.get(id=provider_id)
				if provider.is_active:
					return provider
				logger.warning(f"Provider {provider_id} is inactive")
			except Exception as e:
				logger.warning(f"Failed to load provider {provider_id}: {e}")
		
		# Fall back to default provider for media type
		provider = await LLMProvider.objects.get_default_for_media_type(media_type)
		return provider

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

	def _calculate_content_stats(self, content: list[dict]) -> dict[str, Any]:
		"""Calculate content statistics."""
		if not content:
			return {}

		texts = [item.get("text", "") for item in content]
		dates = [item.get("date") for item in content if item.get("date")]
		reactions = [item.get("reactions", 0) for item in content]
		comments = [item.get("comments", 0) for item in content]

		return {
			"total_posts": len(content),
			"avg_text_length": sum(len(t) for t in texts) / len(texts) if texts else 0,
			"total_reactions": sum(reactions),
			"total_comments": sum(comments),
			"avg_reactions_per_post": sum(reactions) / len(content) if content else 0,
			"avg_comments_per_post": sum(comments) / len(content) if content else 0,
			"date_range": {"first": min(dates) if dates else None, "last": max(dates) if dates else None},
		}

	async def _save_analysis(
			self,
			analysis_results: Dict[str, Any],
			unified_summary: Optional[Dict[str, Any]],
			source: Source,
			content_stats: dict[str, Any],
			platform_name: str,
			bot_scenario: Optional['BotScenario'] = None,
			topic_chain_id: Optional[str] = None,
			parent_analysis_id: Optional[int] = None,
	) -> AIAnalytics:
		"""Save comprehensive analysis results to database."""
		from datetime import date, datetime

		# Extract LLM tracing info from first available analysis
		llm_model = None
		prompt_text = None
		response_payload = {}
		
		for analysis_type, result in analysis_results.items():
			if result and isinstance(result, dict):
				llm_model = result.get('request', {}).get('model')
				prompt_text = result.get('request', {}).get('prompt')
				response_payload[analysis_type] = result.get('response', {})

		# Safe enum/string handling for source_type
		st = getattr(source, "source_type", None)
		st_val = st.value if hasattr(st, "value") else str(st) if st is not None else ""

		# Build comprehensive data structure
		comprehensive_data = {
			"multi_llm_analysis": {
				"text_analysis": analysis_results.get('text_analysis', {}).get('parsed', {}),
				"image_analysis": analysis_results.get('image_analysis', {}).get('parsed', {}),
				"video_analysis": analysis_results.get('video_analysis', {}).get('parsed', {}),
			},
			"unified_summary": unified_summary.get('parsed', {}) if unified_summary else {},
			"content_statistics": content_stats,
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

		# Create analytics record
		analytics = await AIAnalytics.objects.create(
			source_id=source.id,
			summary_data=comprehensive_data,
			llm_model=llm_model or "multi-llm",
			prompt_text=prompt_text,
			response_payload=response_payload,
			analysis_date=date.today(),
			period_type=PeriodType.DAILY,
			topic_chain_id=topic_chain_id,
			parent_analysis_id=parent_analysis_id,
		)

		scenario_info = f" using scenario '{bot_scenario.name}'" if bot_scenario else ""
		logger.info(
			f"Multi-LLM analysis saved for source {source.id}{scenario_info} "
			f"(analytics_id: {analytics.id}, providers: {len(analysis_results)})"
		)
		return analytics
