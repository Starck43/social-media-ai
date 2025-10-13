"""
Common constants and default parameters for AI analysis.
Shared across all scenarios to ensure consistency.
"""

from app.types.models import SentimentLabel

# Default parameters injected into all scenarios
DEFAULT_ANALYSIS_PARAMS = {
    "language": "ru",
    "min_confidence": 0.6,
    "max_samples": 100,
}

# Sentiment analysis defaults
SENTIMENT_DEFAULTS = {
    "categories": [label.value for label in SentimentLabel],
    "detect_sarcasm": False,
    "emotion_analysis": True,
    "confidence_threshold": 0.7,
}

# Trend detection defaults
TRENDS_DEFAULTS = {
    "min_mentions": 5,
    "time_window_hours": 24,
    "track_growth": True,
    "compare_periods": False,
    "top_trends_limit": 10,
}

# Engagement analysis defaults
ENGAGEMENT_DEFAULTS = {
    "metrics": ["likes", "comments", "shares", "views"],
    "calculate_rate": True,
    "detect_viral": True,
    "viral_threshold": 1000,
    "engagement_score_weights": {
        "likes": 1.0,
        "comments": 2.0,
        "shares": 3.0,
        "views": 0.1,
    },
}

# Keywords extraction defaults
KEYWORDS_DEFAULTS = {
    "min_frequency": 3,
    "exclude_common_words": True,
    "extract_entities": False,
    "max_keywords": 20,
    "entity_types": ["person", "organization", "location"],
}

# Topics identification defaults
TOPICS_DEFAULTS = {
    "max_topics": 5,
    "min_topic_weight": 0.1,
    "identify_emerging": True,
}

# Toxicity detection defaults
TOXICITY_DEFAULTS = {
    "threshold": 0.7,
    "detect_harassment": True,
    "detect_hate_speech": True,
    "detect_threats": True,
    "detect_profanity": False,
    "auto_moderate": False,
}

# Demographics analysis defaults
DEMOGRAPHICS_DEFAULTS = {
    "analyze_age_groups": True,
    "analyze_locations": True,
    "analyze_interests": True,
}

# Viral detection defaults
VIRAL_DETECTION_DEFAULTS = {
    "viral_threshold": 10000,
    "growth_rate_threshold": 2.0,
    "time_window_hours": 24,
    "track_velocity": True,
}

# Influencer activity defaults
INFLUENCER_ACTIVITY_DEFAULTS = {
    "min_followers": 1000,
    "track_engagement_rate": True,
    "analyze_content_themes": True,
}

# Competitor tracking defaults
COMPETITOR_TRACKING_DEFAULTS = {
    "competitor_list": [],
    "track_content_strategy": True,
    "compare_metrics": True,
}

# Customer intent defaults
CUSTOMER_INTENT_DEFAULTS = {
    "intent_types": ["purchase", "inquiry", "complaint", "feedback"],
    "confidence_threshold": 0.7,
}

# Brand mentions defaults
BRAND_MENTIONS_DEFAULTS = {
    "brand_names": [],
    "track_sentiment": True,
    "track_reach": True,
}

# Hashtag analysis defaults
HASHTAG_ANALYSIS_DEFAULTS = {
    "min_mentions": 5,
    "track_trending": True,
    "analyze_related": True,
}

# Map analysis type to its default parameters
ANALYSIS_TYPE_DEFAULTS = {
    "sentiment": SENTIMENT_DEFAULTS,
    "trends": TRENDS_DEFAULTS,
    "engagement": ENGAGEMENT_DEFAULTS,
    "keywords": KEYWORDS_DEFAULTS,
    "topics": TOPICS_DEFAULTS,
    "toxicity": TOXICITY_DEFAULTS,
    "demographics": DEMOGRAPHICS_DEFAULTS,
    "viral_detection": VIRAL_DETECTION_DEFAULTS,
    "influencer": INFLUENCER_ACTIVITY_DEFAULTS,
    "competitor": COMPETITOR_TRACKING_DEFAULTS,
    "intent": CUSTOMER_INTENT_DEFAULTS,
    "brand_mentions": BRAND_MENTIONS_DEFAULTS,
    "hashtag_analysis": HASHTAG_ANALYSIS_DEFAULTS,
}


def get_analysis_defaults(analysis_type: str) -> dict:
    """Get default parameters for specific analysis type."""
    return ANALYSIS_TYPE_DEFAULTS.get(analysis_type, {})


def merge_with_defaults(analysis_types: list[str], scope: dict) -> dict:
    """
    Merge scope parameters with defaults for selected analysis types.
    
    Args:
        analysis_types: List of analysis types to apply
        scope: User-defined scope parameters
    
    Returns:
        Complete configuration with defaults applied
    """
    config = {**DEFAULT_ANALYSIS_PARAMS}
    
    for analysis_type in analysis_types:
        defaults = get_analysis_defaults(analysis_type)
        type_key = f"{analysis_type}_config"
        
        # Merge user scope with defaults
        user_config = scope.get(type_key, {})
        config[type_key] = {**defaults, **user_config}
    
    # Add custom variables from scope
    for key, value in scope.items():
        if not key.endswith('_config'):
            config[key] = value
    
    return config
