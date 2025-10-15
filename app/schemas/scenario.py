"""
Pydantic schemas for Bot Scenario API endpoints.

These schemas define the structure for creating, updating, and returning
bot scenario data through the API.
"""

from typing import Optional, List, Any
from pydantic import BaseModel, Field

from app.types import BotActionType, BotTriggerType


class ScenarioBase(BaseModel):
	"""Base schema with common scenario fields."""

	name: str = Field(..., min_length=1, max_length=255, description="Scenario name")
	description: Optional[str] = Field(None, description="Scenario description")
	ai_prompt: Optional[str] = Field(None, description="AI prompt template with variables")
	is_active: bool = Field(True, description="Whether scenario is active")
	collection_interval_hours: int = Field(1, ge=1, le=168, description="Collection interval in hours (1-168, max 1 week)")


class ScenarioCreate(ScenarioBase):
	"""
	Schema for creating a new bot scenario.

	The analysis_types and content_types fields are now separate from scope.
	Scope contains only configuration parameters for the selected analysis types.
	"""

	analysis_types: list[str] = Field(
		default_factory=list,
		description="List of analysis type names (e.g. ['sentiment', 'keywords', 'topics'])"
	)
	content_types: list[str] = Field(
		default_factory=list,
		description="List of content type values (e.g. ['posts', 'comments', 'videos'])"
	)
	scope: Optional[dict] = Field(
		None,
		description="Configuration parameters for analysis (e.g. {'sentiment_config': {...}})"
	)
	trigger_type: Optional[BotTriggerType] = Field(
		None,
		description="Trigger condition for when to analyze/act"
	)
	trigger_config: Optional[dict[str, Any]] = Field(
		None,
		description="Configuration for trigger evaluation"
	)
	action_type: Optional[BotActionType] = Field(
		None,
		description="Action to perform after analysis (NOTIFICATION, COMMENT, etc.)"
	)


class ScenarioUpdate(BaseModel):
	"""
	Schema for updating an existing bot scenario.

	All fields are optional to allow partial updates.
	"""

	name: Optional[str] = Field(None, min_length=1, max_length=255)
	description: Optional[str] = None
	analysis_types: Optional[list[str]] = None
	content_types: Optional[list[str]] = None
	scope: Optional[dict] = None
	ai_prompt: Optional[str] = None
	trigger_type: Optional[BotTriggerType] = None
	trigger_config: Optional[dict[str, Any]] = None
	action_type: Optional[BotActionType] = None
	is_active: Optional[bool] = None
	collection_interval_hours: Optional[int] = Field(None, ge=1, le=168)


class ScenarioResponse(BaseModel):
	"""
	Schema for returning scenario data in API responses.

	Includes all fields from the database model plus formatted timestamps.
	"""

	id: int
	name: str
	description: Optional[str]
	analysis_types: list[str]
	content_types: list[str]
	scope: Optional[dict]
	ai_prompt: Optional[str]
	action_type: Optional[str]
	is_active: bool
	collection_interval_hours: int
	created_at: str
	updated_at: str

	class Config:
		from_attributes = True


class ScenarioAssign(BaseModel):
	"""Schema for assigning a scenario to a source."""

	source_id: int = Field(..., gt=0, description="Source ID to assign scenario to")
	scenario_id: Optional[int] = Field(None, gt=0, description="Scenario ID to assign (null to remove)")


class ScenarioSourcesResponse(BaseModel):
	"""Schema for returning sources using a specific scenario."""

	scenario_id: int
	scenario_name: str
	sources: list[dict]
