"""Compatibility aliases; canonical declarations live in domain packages."""

from .analysis.ai_analytics import AIAnalytics
from .managers.ai_analytics_manager import AIAnalyticsManager

__all__ = ["AIAnalytics", "AIAnalyticsManager"]
