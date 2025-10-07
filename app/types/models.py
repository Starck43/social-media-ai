from enum import Enum


class UserRole(Enum):
	"""User roles in hierarchy order — DO NOT CHANGE THE ORDER!"""
	VIEWER = "Basic read-only access to view content"
	AI_BOT = "AI assistant with limited access to specific features"
	MANAGER = "Can manage team members and basic content"
	ANALYST = "Can view and analyze data and reports"
	MODERATOR = "Can moderate content and manage users"
	ADMIN = "Administrator with almost full access"
	SUPERUSER = "Superuser with full system access"

	def __str__(self) -> str:
		return self.name.lower()


class ActionType(Enum):
	VIEW = "view"
	CREATE = "create"
	UPDATE = "update"
	DELETE = "delete"
	ANALYZE = "analyze"
	MODERATE = "moderate"
	EXPORT = "export"
	CONFIGURE = "configure"

	def __str__(self) -> str:
		return self.value


class PlatformType(Enum):
	"""Types of social media platforms."""
	SOCIAL = "social"  # VK, Facebook, etc.
	MESSENGER = "messenger"  # Telegram, WhatsApp, etc.

	def __str__(self) -> str:
		return self.value


class SourceType(Enum):
	"""Types of content sources in social networks."""
	# Common types
	USER = "user"
	GROUP = "group"
	CHANNEL = "channel"
	CHAT = "chat"
	PAGE = "page"

	# VK specific
	PUBLIC = "public"  # Public page
	EVENT = "event"  # Event/meetup
	MARKET = "market"  # Products/marketplace
	ALBUM = "album"  # Photo/video album

	# Telegram specific
	SUPERGROUP = "supergroup"
	BOT = "bot"
	BROADCAST = "broadcast"

	def __str__(self) -> str:
		return self.value


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

	def __str__(self) -> str:
		return self.value


class AnalysisPeriodType(Enum):
	"""Types of analysis periods."""
	DAILY = "daily"
	WEEKLY = "weekly"
	MONTHLY = "monthly"
	CUSTOM = "custom"

	def __str__(self) -> str:
		return self.value


class SentimentLabel(Enum):
	"""Sentiment analysis labels."""
	POSITIVE = "positive"
	NEGATIVE = "negative"
	NEUTRAL = "neutral"
	MIXED = "mixed"

	def __str__(self) -> str:
		return self.value


class BotTriggerType(Enum):
	"""Types of bot trigger conditions."""
	KEYWORD_MATCH = "keyword_match"
	SENTIMENT_THRESHOLD = "sentiment_threshold"
	ACTIVITY_SPIKE = "activity_spike"
	USER_MENTION = "user_mention"
	TIME_BASED = "time_based"
	MANUAL = "manual"

	def __str__(self) -> str:
		return self.value


class ContentType(Enum):
	"""Types of content can be monitored."""
	POSTS = "posts"
	COMMENTS = "comments"
	VIDEOS = "videos"
	STORIES = "stories"
	REELS = "reels"
	REACTIONS = "reactions"
	MENTIONS = "mentions"

	def __str__(self) -> str:
		return self.value


class MonitoringStatus(Enum):
	"""Status of source monitoring."""
	ACTIVE = "active"
	PAUSED = "paused"
	ERROR = "error"
	DISABLED = "disabled"

	def __str__(self) -> str:
		return self.value
