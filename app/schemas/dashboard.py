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
    
    Provides overview of sources, platforms, analytics, and notifications.
    """
    
    total_sources: int = Field(..., description="Total number of sources")
    active_sources: int = Field(..., description="Number of active sources")
    total_platforms: int = Field(..., description="Total number of platforms")
    active_platforms: int = Field(..., description="Number of active platforms")
    total_analytics: int = Field(..., description="Total number of analytics records")
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
