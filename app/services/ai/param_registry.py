"""
Parameter registry: separates methodology from specifics in analysis parameters.

METHODOLOGY_PARAMS (scope): analysis method configuration
  - Stay in AgentScenario.scope
  - Define HOW to analyze (categories, scale, limits, context_window)
  - Do NOT change the LLM response schema

TARGET_PARAMS (payload): specific analysis objects
  - Move to AgentTask.payload
  - Define WHAT to look for (brands, competitors, hashtags)
  - Inject into prompt instruction, not the response schema
"""

from typing import Any, Dict, List, Optional

SCOPE_PARAMS: Dict[str, List[str]] = {
    "sentiment": ["categories", "scale"],
    "topics": ["max_topics"],
    "keywords": ["max_keywords", "min_relevance"],
    "brand_mentions": ["context_window"],
    "competitor": ["activities"],
    "hashtag_analysis": ["max_hashtags"],
    "influencer": ["min_followers", "min_impact_score"],
    "toxicity": ["toxicity_levels", "threshold"],
    "demographics": ["max_locations"],
    "trends": ["momentum_levels"],
    "intent": ["intent_types"],
    "engagement": ["engagement_types"],
    "viral_detection": ["viral_threshold"],
}

PAYLOAD_PARAMS: Dict[str, List[str]] = {
    "brand_mentions": ["brands"],
    "competitor": ["competitors"],
    "hashtag_analysis": ["hashtags"],
    "influencer": ["influencer_names"],
    "keywords": ["keywords_list"],
    "topics": ["topic_list"],
}

# Human-readable labels for the target params, so a warning reads as a sentence
# a non-technical owner can act on ("укажите бренды", not "payload.brands").
PAYLOAD_PARAM_LABELS: Dict[str, str] = {
    "brands": "бренды (brand_mentions)",
    "competitors": "конкурентов (competitor)",
    "hashtags": "хэштеги (hashtag_analysis)",
    "influencer_names": "авторов (influencer)",
    "keywords_list": "ключевые слова (keywords)",
    "topic_list": "темы (topics)",
}

# Every payload-param name, in one set, for scope-guard scans.
ALL_PAYLOAD_PARAMS: set[str] = {p for params in PAYLOAD_PARAMS.values() for p in params}


def is_scope_param(analysis_type: str, param: str) -> bool:
    """Check if a parameter is a methodology (scope) parameter."""
    return param in SCOPE_PARAMS.get(analysis_type, [])


def is_payload_param(analysis_type: str, param: str) -> bool:
    """Check if a parameter is a target (payload) parameter."""
    return param in PAYLOAD_PARAMS.get(analysis_type, [])


def get_payload_params_for_type(analysis_type: str) -> List[str]:
    """Get all payload parameter names for a given analysis type."""
    return PAYLOAD_PARAMS.get(analysis_type, [])


def get_scope_params_for_type(analysis_type: str) -> List[str]:
    """Get all scope parameter names for a given analysis type."""
    return SCOPE_PARAMS.get(analysis_type, [])


def extract_target_values(payload: Dict, analysis_types: List[str]) -> Dict[str, Any]:
    """Extract target values from payload for all analysis types.

    This builds the instruction dict that gets merged into the prompt.
    E.g. payload={"brands": ["Coca-Cola", "Sprite"]} for "brand_mentions"
    → {"brand_mentions": {"brands": ["Coca-Cola", "Sprite"]}}
    """
    result = {}
    for atype in analysis_types:
        params = get_payload_params_for_type(atype)
        for param in params:
            if param in payload:
                result.setdefault(atype, {})[param] = payload[param]
    return result


def target_params_in_scope(scope: Optional[Dict[str, Any]]) -> List[str]:
    """Payload-param names found in a scenario's scope, so callers can reject them.

    Specific targets (brands, competitors, hashtags, …) belong in
    `AgentTask.payload`, never in `AgentScenario.scope` — a scope that carries
    them becomes a one-brand scenario instead of a reusable methodology. Both
    spellings are caught: top-level (`scope["brands"]`) and nested under the
    analysis type (`scope["brand_mentions"]["brands"]`).
    """
    scope = scope or {}
    found: List[str] = []
    for key in ALL_PAYLOAD_PARAMS:
        if key in scope:
            found.append(key)
        for config in scope.values():
            if isinstance(config, dict) and key in config:
                found.append(key)
    return sorted(set(found))


def missing_target_params(analysis_types, payload: Optional[Dict[str, Any]]) -> List[str]:
    """Human-readable warnings: which specific targets a task should carry.

    A scenario with `brand_mentions` in its analysis types wants to know *which*
    brands to look for; those live in `AgentTask.payload.brands`. When a task is
    bound to such a scenario but the payload is empty, analysis has nothing to
    aim at — callers surface this as a hint, not a hard error.
    """
    analysis_types = list(analysis_types or [])
    payload = payload or {}
    missing: List[str] = []
    for atype in analysis_types:
        for param in get_payload_params_for_type(atype):
            if not payload.get(param):
                label = PAYLOAD_PARAM_LABELS.get(param, param)
                missing.append(f"{label}")
    return missing
