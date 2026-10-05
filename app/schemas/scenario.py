"""
Pydantic schemas for Bot Scenario API endpoints.

These schemas define the structure for creating, updating, and returning
bot scenario data through the API.
"""

from typing import Optional, Any

from pydantic import BaseModel, Field, model_validator



def _validate_prompt_field(value: Optional[str], field_name: str) -> list[str]:
	"""Unknown prompt variables in one prompt field, for the warning list."""
	if not value:
		return []
	from app.services.ai.prompt_variables import PromptVariables
	return [f"{field_name}: {{{name}}}" for name in PromptVariables.validate_prompt(value)]



class ScenarioBase(BaseModel):
	"""Base schema with common scenario fields."""

	name: str = Field(..., min_length=1, max_length=255, description="Scenario name")
	description: Optional[str] = Field(None, description="Scenario description")
	# Unified prompt system: one base prompt + per-media overrides + summary prompt
	base_prompt: Optional[str] = Field(None, description="Core LLM instruction for analysis")
	media_overrides: Optional[dict[str, str]] = Field(None, description='Per-media overrides: {"image": "...", "video": "..."}')
	summary_prompt: Optional[str] = Field(None, description="Custom prompt to build unified summary")
	# Legacy (kept for backward compatibility)
	ai_prompt: Optional[str] = Field(None, description="[Deprecated] Legacy AI prompt template")
	is_active: bool = Field(True, description="Whether scenario is active")
	max_tokens: Optional[int] = Field(None, ge=1, description="Max tokens for LLM responses in this scenario")
	output_schema: Optional[dict[str, Any]] = Field(None, description="JSON Schema for structured LLM output")

	@model_validator(mode="after")
	def _check_prompt_variables(self):
		"""Collect unknown prompt variables as warnings, never errors.

		The docs' validation contract: a prompt with an unknown `{var}` is saved
		anyway (the placeholder simply stays literal), but the caller is told
		which names will not be substituted. Stored on the instance so the API
		layer can echo them in the response.
		"""
		warnings: list[str] = []
		warnings += _validate_prompt_field(self.base_prompt, "base_prompt")
		for media, prompt in (self.media_overrides or {}).items():
			warnings += _validate_prompt_field(prompt, f"media_overrides.{media}")
		warnings += _validate_prompt_field(self.summary_prompt, "summary_prompt")
		self._validation_warnings: list[str] = warnings
		return self


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
	# Unified prompt fields
	base_prompt: Optional[str] = None
	media_overrides: Optional[dict[str, str]] = None
	summary_prompt: Optional[str] = None
	# Legacy
	ai_prompt: Optional[str] = None
	is_active: Optional[bool] = None
	max_tokens: Optional[int] = Field(None, ge=1)
	output_schema: Optional[dict[str, Any]] = None

	@model_validator(mode="after")
	def _check_prompt_variables(self):
		"""Collect unknown prompt variables as warnings (same contract as create)."""
		warnings: list[str] = []
		warnings += _validate_prompt_field(self.base_prompt, "base_prompt")
		for media, prompt in (self.media_overrides or {}).items():
			warnings += _validate_prompt_field(prompt, f"media_overrides.{media}")
		warnings += _validate_prompt_field(self.summary_prompt, "summary_prompt")
		self._validation_warnings: list[str] = warnings
		return self


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
	# Unified prompt fields
	base_prompt: Optional[str]
	media_overrides: Optional[dict[str, str]]
	summary_prompt: Optional[str]
	# Legacy
	ai_prompt: Optional[str]
	validation_warnings: list[str] = Field(
		default_factory=list,
		description="Unknown prompt variables (warnings, not errors)",
	)
	is_active: bool
	max_tokens: Optional[int]
	output_schema: Optional[dict]
	created_at: str
	updated_at: str

	class Config:
		from_attributes = True


class ScenarioListItem(BaseModel):
	"""Schema for listing scenarios in filters and simple lists."""

	id: int
	name: str
	description: Optional[str] = None
