"""Notification-related enum types."""
from enum import Enum

from app.utils.db_enums import database_enum


@database_enum
class NotificationType(Enum):
	"""Types of system notifications."""

	REPORT_READY = "report_ready"
	MOOD_CHANGE = "mood_change"
	TREND_ALERT = "trend_alert"
	TOPIC_RESUMED = "topic_resumed"
	API_ERROR = "api_error"
	CONNECTION_ERROR = "connection_error"
	SOURCE_INACTIVE = "source_inactive"
	RATE_LIMIT_WARNING = "rate_limit_warning"
	BOT_COMMENT = "bot_comment"
	BOT_SKIPPED = "bot_skipped"
	SYSTEM_BACKUP = "system_backup"
	SYSTEM_UPDATE = "system_update"
	SUBSCRIBED_ACTIVITY = "subscribed_activity"
	KEYWORD_MENTION = "keyword_mention"
