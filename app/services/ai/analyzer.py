import logging
from datetime import UTC
from typing import Optional, Any
import json

import httpx

from app.core.config import settings
from app.models import Source, AIAnalytics, BotScenario
from app.types import PeriodType

logger = logging.getLogger(__name__)

# Import new analyzer
try:
	from app.services.ai.analyzer_v2 import AIAnalyzerV2
	USE_V2_ANALYZER = True
	logger.info("Using AIAnalyzerV2 with multi-LLM support")
except ImportError as e:
	logger.warning(f"Failed to import AIAnalyzerV2, using legacy analyzer: {e}")
	USE_V2_ANALYZER = False


class AIAnalyzer:
	"""
	Service for comprehensive social media content analysis.
	
	LEGACY VERSION: This class is deprecated in favor of AIAnalyzerV2 which supports
	multiple LLM providers and multi-modal content analysis.
	
	This service handles:
	— Collecting content from various social media sources
	— Building AI prompts based on bot scenarios or default templates
	— Calling the LLM API for analysis
	— Saving analysis results with full LLM tracing
	
	The analyzer supports both scenario-based and default analysis modes.
	"""

	def __init__(self):
		"""Initialize an analyzer with API configuration from settings."""
		self.api_key = settings.DEEPSEEK_API_KEY
		self.api_url = settings.DEEPSEEK_API_URL
		
		# Use V2 analyzer if available
		if USE_V2_ANALYZER:
			self._v2_analyzer = AIAnalyzerV2()
		else:
			self._v2_analyzer = None

	async def analyze_content(
			self,
			content: list[dict],
			source: Source,
			topic_chain_id: Optional[str] = None,
			parent_analysis_id: Optional[int] = None,
	) -> Optional[AIAnalytics]:
		"""
		Comprehensive analysis of collected content using AI.

		Args:
				content: List of normalized content items
				source: Source from which content was collected
				topic_chain_id: Optional chain ID for ongoing topics
				parent_analysis_id: Optional parent analysis ID for threaded analysis

		Returns:
				AIAnalytics object with complete analysis results or None if failed
		"""
		# Use V2 analyzer if available for multi-LLM support
		if self._v2_analyzer:
			logger.info(f"Using AIAnalyzerV2 for source {source.id}")
			return await self._v2_analyzer.analyze_content(
				content, source, topic_chain_id, parent_analysis_id
			)
		
		# Fall back to legacy single-LLM analysis
		logger.info(f"Using legacy AIAnalyzer for source {source.id}")
		
		if not content:
			logger.warning(f"No content to analyze for source {source.id}")
			return None

		# Load bot scenario if assigned to the source
		# Bot scenario defines analysis_types, scope and custom AI prompt
		bot_scenario = None
		if source.bot_scenario_id:
			from app.models import BotScenario
			try:
				bot_scenario = await BotScenario.objects.get(id=source.bot_scenario_id)
				logger.info(
					f"Using bot scenario '{bot_scenario.name}' (ID: {bot_scenario.id}) "
					f"with analysis types: {bot_scenario.analysis_types} for source {source.id}"
				)
			except Exception as e:
				logger.warning(f"Failed to load bot scenario {source.bot_scenario_id}: {e}")

		# Prepare content and metadata
		text_content = self._prepare_text(content)
		content_stats = self._calculate_content_stats(content)
		platform_name = await self._get_platform_name(source)

		try:
			# Build prompt (scenario-based or default)
			# Scenario-based: Uses custom prompt template with analysis_types and scope
			# Default: Uses comprehensive analysis prompt with all standard features
			if bot_scenario and bot_scenario.ai_prompt:
				from app.services.ai.scenario import ScenarioPromptBuilder

				# Build context for prompt variable substitution
				context = {
					'platform': platform_name,
					'source_type': source.source_type.value if hasattr(source.source_type, 'value') else str(
						source.source_type),
					'total_posts': len(content),
					'content': text_content,
					'date_range': content_stats.get('date_range'),
				}

				# ScenarioPromptBuilder merges scope with defaults and substitutes variables
				prompt = ScenarioPromptBuilder.build_prompt(bot_scenario, context)
				logger.info(
					f"Using scenario-based prompt for analysis types: {bot_scenario.analysis_types}, "
					f"content types: {bot_scenario.content_types}"
				)
			else:
				prompt = self._get_default_prompt(text_content, content_stats, source, platform_name)
				logger.info("Using default comprehensive analysis prompt (no scenario assigned)")

			# Call LLM
			result = await self._call_api_with_prompt(prompt)

			# Save analysis results to database
			# This creates an AIAnalytics record with:
			# — AI analysis results (sentiment, topics, etc.)
			# — Content statistics (total posts, reactions, etc.)
			# — LLM tracing data (model, prompt, response)
			analysis = await self._save_analysis(
				result, source, content_stats, platform_name, bot_scenario, topic_chain_id, parent_analysis_id
			)
			return analysis

		except Exception as e:
			logger.error(f"Error analyzing content for source {source.id}: {e}", exc_info=True)
			return None

	def _prepare_text(self, content: list[dict]) -> str:
		"""
		Prepare content for AI analysis with intelligent sampling.
		
		To avoid sending too much data to the LLM, we sample representative
		content items using a step function (first, middle, last posts).
		
		Args:
			content: List of normalized content items with 'text' and 'date' fields
			
		Returns:
			Formatted text ready for LLM analysis
		"""
		texts = []
		# Take representative sample (evenly distributed across the dataset)
		sample_size = min(100, len(content))
		step = max(1, len(content) // sample_size)

		for i in range(0, len(content), step):
			if len(texts) >= sample_size:
				break
			text = content[i].get("text", "")
			if text and len(text.strip()) > 10:  # Skip very short texts
				texts.append(f"[{content[i].get('date', '')}] {text}")

		return "\n\n".join(texts[:sample_size])

	async def _get_platform_name(self, source: Source) -> str:
		"""
		Safely get platform name without relying on lazy relationship loading.
		
		First attempts to get from 'source.platform' relationship if already loaded,
		otherwise fetches from a database to avoid lazy loading issues in async context.
		
		Args:
			source: Source object with platform_id
			
		Returns:
			Platform name (e.g., “VK”, “Telegram”)
		"""
		try:
			plat = getattr(source, "platform", None)
			if plat and getattr(plat, "name", None):
				return plat.name
		except Exception:
			pass

		# Fetch from a database if not already loaded
		from app.models import Platform
		obj = await Platform.objects.get(id=source.platform_id)
		return obj.name

	def _calculate_content_stats(self, content: list[dict]) -> dict[str, Any]:
		"""
		Calculate basic content statistics for analysis context.
		
		These stats help the LLM understand the scale and engagement of the content,
		and are saved alongside the AI analysis for reference.
		
		Args:
			content: List of normalized content items
			
		Returns:
			Dictionary with statistics (total posts, avg reactions, date range, etc.)
		"""
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

	def _get_default_prompt(
			self, text: str, stats: dict[str, Any], source: Source, platform_name: str
	) -> str:
		"""
		Build default comprehensive analysis prompt when no scenario is assigned.
		
		This prompt requests a full analysis covering:
		— Sentiment analysis (emotions, positive/negative topics)
		— Topic analysis (main themes, emerging topics)
		— Engagement analysis (viral potential, audience interest)
		— Content analysis (quality, key phrases, hashtags)
		— Audience insights (mood, concerns, suggestions)
		
		Args:
			text: Prepared content text
			stats: Content statistics
			source: Source object
			platform_name: Name of the social media platform
			
		Returns:
			Complete prompt for DeepSeek API
		"""
		# Safely extract source type value
		source_type = getattr(source, "source_type", None)
		if hasattr(source_type, "value"):
			stype = source_type.value
		else:
			stype = str(source_type) if source_type is not None else ""

		return f"""
Проанализируй контент из социальной сети и предоставь комплексный анализ в JSON формате.

ИСХОДНЫЕ ДАННЫЕ:
— Тип источника: {stype}
— Платформа: {platform_name}
— Общее количество постов: {stats.get('total_posts', 0)}
— Период: {stats.get('date_range', {}).get('first')} — {stats.get('date_range', {}).get('last')}

КОНТЕНТ ДЛЯ АНАЛИЗА:
{text}

ВЕРНИ ОТВЕТ В СЛЕДУЮЩЕЙ JSON СТРУКТУРЕ:
{{
    "sentiment_analysis": {{
        "overall_sentiment": "positive/negative/neutral/mixed",
        "sentiment_score": 0.0-1.0,
        "dominant_emotions": ["эмоция1", "эмоция2", "эмоция3"],
        "positive_topics": ["тема1", "тема2"],
        "negative_topics": ["тема1", "тема2"],
        "sentiment_summary": "краткое описание эмоционального настроя"
    }},
    "topic_analysis": {{
        "main_topics": ["тема1", "тема2", "тема3", "тема4", "тема5"],
        "topic_prevalence": {{"тема1": 0.25, "тема2": 0.20, "тема3": 0.15}},
        "emerging_topics": ["новая тема1", "новая тема2"],
        "topic_summary": "краткое описание тематического содержания"
    }},
    "engagement_analysis": {{
        "engagement_level": "high/medium/low",
        "engagement_score": 0.0-1.0,
        "viral_potential": "high/medium/low",
        "audience_interest": ["интерес1", "интерес2"],
        "engagement_summary": "краткое описание вовлеченности"
    }},
    "content_analysis": {{
        "content_quality": "high/medium/low",
        "content_types": ["тип1", "тип2"],
        "key_phrases": ["фраза1", "фраза2", "фраза3"],
        "hashtags": ["#хэштег1", "#хэштег2"],
        "content_summary": "краткое описание качества контента"
    }},
    "audience_insights": {{
        "audience_mood": "описание настроения аудитории",
        "key_concerns": ["проблема1", "проблема2"],
        "suggestions": ["предложение1", "предложение2"],
        "audience_summary": "краткое описание аудитории"
    }},
    "metadata": {{
        "analysis_version": "2.0",
        "processed_samples": {len(text.split('\n\n'))},
        "analysis_timestamp": "временная метка"
    }}
}}

Будь точным и объективным в анализе. Используй статистические данные для подкрепления выводов.
"""

	async def _call_api_with_prompt(self, prompt: str) -> dict:
		"""
		Call DeepSeek API with prepared prompt and parse response.
		
		Uses JSON response format for structured analysis data.
		Includes full request and response for LLM tracing.
		
		Args:
			prompt: Complete prompt for the LLM
			
		Returns:
			Dictionary with 'request' and 'response' keys for tracing
			
		Raises:
			ValueError: If API key is not configured
			httpx.HTTPError: If API request fails
		"""
		if not self.api_key:
			raise ValueError("DEEPSEEK_API_KEY not configured")

		async with httpx.AsyncClient() as client:
			response = await client.post(
				self.api_url,
				headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
				json={
					"model": "deepseek-chat",
					"messages": [{"role": "user", "content": prompt}],
					"temperature": 0.2,
					"max_tokens": 2000,
					"response_format": {"type": "json_object"},
				},
				timeout=60.0,
			)
			response.raise_for_status()
			return {"request": {"model": "deepseek-chat", "prompt": prompt}, "response": response.json()}

	async def _save_analysis(
			self,
			api_result: dict,
			source: Source,
			content_stats: dict[str, Any],
			platform_name: str,
			bot_scenario: Optional['BotScenario'] = None,
			topic_chain_id: Optional[str] = None,
			parent_analysis_id: Optional[int] = None,
	) -> AIAnalytics:
		"""
		Save comprehensive analysis results to database with full LLM tracing.
		
		Creates an AIAnalytics record containing:
		— AI analysis results (parsed from LLM response)
		— Content statistics (engagement, post counts, etc.)
		— Source metadata (platform, source type, name)
		— LLM tracing data (model, prompt, full response)
		— Scenario information if applicable
		
		This enables full audit trail of AI analysis for debugging and monitoring.
		
		Args:
			api_result: Dictionary with 'request' and 'response' from LLM API
			source: Source that content was collected from
			content_stats: Statistics about the analyzed content
			platform_name: Name of the social media platform
			bot_scenario: Bot scenario that was used (if any)
			topic_chain_id: Optional chain ID for topic continuity
			parent_analysis_id: Optional parent analysis for threaded analysis
			
		Returns:
			Created AIAnalytics object
		"""
		from datetime import date, datetime

		# Extract request/response from LLM API call
		req = api_result.get("request", {})
		resp = api_result.get("response", {})
		content = resp.get("choices", [{}])[0].get("message", {}).get("content", "{}")

		# Try to parse LLM response as JSON
		# If parsing fails, store raw response for debugging
		try:
			result_data = json.loads(content)
		except json.JSONDecodeError:
			logger.warning("Failed to parse AI response as JSON, storing raw response")
			result_data = {"raw_response": content, "parse_error": True}

		# Combine AI analysis with content statistics and metadata
		# Safe enum/string handling for source_type
		st = getattr(source, "source_type", None)
		if hasattr(st, "value"):
			st_val = st.value
		else:
			st_val = str(st) if st is not None else ""

		# Build comprehensive analysis data structure
		comprehensive_data = {
			"ai_analysis": result_data,
			"content_statistics": content_stats,
			"source_metadata": {
				"source_type": st_val,
				"platform": platform_name,
				"source_name": source.name
			},
			"analysis_metadata": {
				"analysis_version": "2.0",
				"analysis_timestamp": datetime.now(UTC).isoformat(),
				"content_samples_analyzed": content_stats.get("total_posts", 0),
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

		# Create analytics record with LLM tracing
		analytics = await AIAnalytics.objects.create(
			source_id=source.id,
			summary_data=comprehensive_data,
			llm_model=req.get("model"),
			prompt_text=req.get("prompt"),
			response_payload=resp,
			analysis_date=date.today(),
			period_type=PeriodType.DAILY,
			topic_chain_id=topic_chain_id,
			parent_analysis_id=parent_analysis_id,
		)

		scenario_info = f" using scenario '{bot_scenario.name}'" if bot_scenario else ""
		logger.info(
			f"Comprehensive analysis saved for source {source.id}{scenario_info} "
			f"(analytics_id: {analytics.id}, chain: {topic_chain_id}, parent: {parent_analysis_id})"
		)
		return analytics
