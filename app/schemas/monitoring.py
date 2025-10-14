"""
Pydantic schemas for Monitoring API endpoints.

These schemas define the structure for content collection requests.
"""

from typing import Optional, List
from pydantic import BaseModel, Field

from app.types import SourceType


class CollectRequest(BaseModel):
    """
    Request schema for collecting content from a single source.
    
    Triggers content collection from specified source with optional analysis.
    """
    
    source_id: int = Field(..., gt=0, description="ID of the source to collect from")
    content_type: str = Field("posts", description="Type of content to collect (posts, comments, etc.)")
    analyze: bool = Field(True, description="Whether to run AI analysis on collected content")


class CollectPlatformRequest(BaseModel):
    """
    Request schema for collecting content from all sources on a platform.
    
    Triggers content collection from all active sources on specified platform,
    with optional filtering by source types.
    """
    
    platform_id: int = Field(..., gt=0, description="ID of the platform")
    source_types: Optional[List[SourceType]] = Field(
        None,
        description="Optional filter by source types (USER, GROUP, CHANNEL)"
    )
    analyze: bool = Field(True, description="Whether to run AI analysis on collected content")


class CollectMonitoredRequest(BaseModel):
    """
    Request schema for collecting content from monitored users.
    
    Used for GROUP/CHANNEL sources that track specific USER accounts.
    """
    
    source_id: int = Field(..., gt=0, description="ID of the source with monitored users")
    analyze: bool = Field(True, description="Whether to run AI analysis on collected content")
