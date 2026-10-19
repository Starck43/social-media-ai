"""Stable, versioned identities for semantically distinct analyses."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from enum import Enum
from typing import Any

FINGERPRINT_VERSION = "analysis-context-v1"
LEGACY_ANALYSIS_FINGERPRINT = "legacy"

_OPERATIONAL_KEYS = {
    "agent_task_id", "task_id", "job_id", "run_id", "force_refresh", "force_reanalyze", "keep_raw",
    "one_off", "_one_off", "trusted_initiated_by_user_id", "_trusted_initiated_by_user_id",
    "initiated_by_user_id", "_initiated_by_user_id", "created_at", "updated_at",
    "start_date", "end_date", "cli_dates", "staged_window", "date_range", "time_range",
    "period", "period_type", "scenario_id", "source_id",
    "source_ids", "tenant_id", "analyze_inline", "excluded_users",
}


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if value is None or type(value) in (str, int, float, bool):
        return value
    return str(value)


_UNORDERED_TARGET_LISTS = {
    "brand", "brands", "competitor", "competitors", "hashtag", "hashtags",
    "target", "targets", "target_brands", "target_competitors", "target_hashtags",
}


def _semantic_payload(value: Any, *, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {
            str(child_key): _semantic_payload(item, key=str(child_key))
            for child_key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(child_key) not in _OPERATIONAL_KEYS
        }
    if isinstance(value, (list, tuple)):
        values = [_semantic_payload(item) for item in value]
        if key in _UNORDERED_TARGET_LISTS:
            canonical = {json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False): item
                         for item in values}
            return [canonical[item] for item in sorted(canonical)]
        return values
    return _json_value(value)


def analysis_fingerprint(
    scenario: Any,
    *,
    trigger_config: dict[str, Any] | None = None,
    task_payload: dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
) -> str:
    """Hash every resolved input that can change analysis conclusions."""
    if scenario is not None and type(getattr(scenario, "id", None)) is not int:
        raise ValueError("analysis_context_scenario_invalid")
    scenario_fields = (
        "id", "analysis_types", "content_types", "scope", "base_prompt", "media_overrides",
        "summary_prompt", "max_tokens", "output_schema", "text_llm_model_id", "image_llm_model_id",
        "video_llm_model_id", "llm_strategy",
    )
    scenario_data = (
        {field: getattr(scenario, field, None) for field in scenario_fields}
        if scenario is not None else {"id": None, "effective_configuration": "built_in_defaults"}
    )
    payload = {
        "version": FINGERPRINT_VERSION,
        "scenario": _json_value(scenario_data),
        "effective_output_schema": _json_value(output_schema),
        "trigger_config": _semantic_payload(trigger_config or {}),
        "task_payload": _semantic_payload(task_payload or {}),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
