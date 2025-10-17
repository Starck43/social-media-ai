"""
Pydantic schemas for Dashboard API endpoints.

These schemas define the structure for dashboard statistics,
source summaries, analytics summaries, and trend data.
"""

from typing import Optional

from pydantic import BaseModel, Field


class DashboardStats(BaseModel):
    """
    Dashboard statistics response.
    
    Provides an overview of sources, platforms, analytics and notifications.
    """
    
    total_sources: int = Field(..., description="Total number of sources")
    active_sources: int = Field(..., description="Number of active sources")
    total_platforms: int = Field(..., description="Total number of platforms")
    active_platforms: int = Field(..., description="Number of active platforms")
    total_analytics: int = Field(..., description="Total number of analytics records")
    total_topics: int = Field(..., description="Total number of unique topics")
    unread_notifications: int = Field(..., description="Number of unread notifications")
    sources_by_platform: dict = Field(..., description="Source count grouped by platform")
    sources_by_type: dict = Field(..., description="Source count grouped by type")
    analytics_by_period: dict = Field(..., description="Analytics count grouped by period")


class SourceSummary(BaseModel):
    """
    Source summary for dashboard display.
    
    Provides compact view of source information with related data.
    """
    
    id: int
    name: str
    platform_name: str = Field(..., description="Name of the platform")
    source_type: str = Field(..., description="Type of source (USER, GROUP, CHANNEL)")
    is_active: bool
    last_checked: Optional[str] = Field(None, description="Last check timestamp (ISO format)")
    analytics_count: int = Field(..., description="Number of analytics for this source")
    bot_scenario_name: Optional[str] = Field(None, description="Assigned bot scenario name")


class AnalyticsSummary(BaseModel):
    """
    Analytics summary for dashboard display.
    
    Provides compact view of analytics record with source information.
    """
    
    id: int
    source_id: int
    source_name: str = Field(..., description="Name of the source")
    analysis_date: str = Field(..., description="Analysis date (ISO format)")
    period_type: str = Field(..., description="Period type (DAILY, WEEKLY, etc.)")
    topic_chain_id: Optional[str] = Field(None, description="Topic chain ID for continuity")
    llm_model: Optional[str] = Field(None, description="LLM model used for analysis")
    created_at: str = Field(..., description="Creation timestamp (ISO format)")


class TrendData(BaseModel):
    """
    Trend data point for time series visualization.
    
    Used for displaying metrics over time (sentiment, activity, engagement).
    """
    
    date: str = Field(..., description="Date of the data point (ISO format)")
    value: float = Field(..., description="Metric value")
    label: str = Field(..., description="Human-readable label for the value")


# ===================== Aggregation responses =====================

class SentimentTrendItem(BaseModel):
    date: str = Field(..., description="Дата (ISO)")
    avg_sentiment_score: float = Field(..., description="Средняя тональность")
    distribution: dict[str, int] = Field(default_factory=dict, description="Распределение по меткам")
    total_analyses: int = Field(..., description="Количество анализов за период")


class SentimentTrendsResponse(BaseModel):
    trends: list[SentimentTrendItem]
    period_days: int
    group_by: str


class TopTopicItem(BaseModel):
    topic: str
    count: int
    avg_sentiment: float


class TopTopicsResponse(BaseModel):
    topics: list[TopTopicItem]
    period_days: int
    total_topics: int


class LLMProviderStats(BaseModel):
    requests: int
    total_tokens: int
    avg_tokens_per_request: float
    estimated_cost_usd: float


class LLMStatsResponse(BaseModel):
    providers: dict[str, LLMProviderStats]
    summary: dict[str, int | float]


class ContentMixItem(BaseModel):
    count: int
    percentage: float


class ContentMixResponse(BaseModel):
    media_types: dict[str, ContentMixItem]


class EngagementMetricsResponse(BaseModel):
    avg_reactions_per_post: float
    avg_comments_per_post: float
    total_reactions: int
    total_comments: int


# ===================== Topic chains =====================

class TopicChainListItem(BaseModel):
    chain_id: str
    source_id: int
    analyses_count: int
    first_date: Optional[str] = None
    last_date: Optional[str] = None
    topics_count: int
    topics: list[str]
    source: Optional[dict] = None


class TopicChainDetail(BaseModel):
    chain_id: str
    source_info: dict
    chain_data: dict
    topic_statistics: dict
    total_analyses: int


class TopicChainEvolutionItem(BaseModel):
    analysis_date: str
    topics: list[str]
    sentiment_score: float
    post_url: Optional[str] = None
