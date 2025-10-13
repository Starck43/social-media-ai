from .enum_types import Enum, database_enum


@database_enum
class UserRoleType(Enum):
	"""User roles in hierarchy order — DO NOT CHANGE THE ORDER!"""

	VIEWER = "Basic read-only access to view content"
	AI_BOT = "AI assistant with limited access to specific features"
	MANAGER = "Can manage team members and basic content"
	ANALYST = "Can view and analyze data and reports"
	MODERATOR = "Can moderate content and manage users"
	ADMIN = "Administrator with almost full access"
	SUPERUSER = "Superuser with full system access"


@database_enum
class ActionType(Enum):
	"""User permission action types."""

	VIEW = "view"
	CREATE = "create"
	UPDATE = "update"
	DELETE = "delete"
	ANALYZE = "analyze"
	MODERATE = "moderate"
	EXPORT = "export"
	CONFIGURE = "configure"


@database_enum
class PlatformType(Enum):
	VK = "vk"
	TELEGRAM = "telegram"

	def get_client_class(self):
		clients = {
			PlatformType.VK: "VKClient",
			PlatformType.TELEGRAM: "TelegramClient",
		}
		return clients[self]


@database_enum
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


@database_enum
class PeriodType(Enum):
	"""Types of analysis periods."""
	DAILY = "Ежедневно"
	WEEKLY = "Еженедельно"
	MONTHLY = "Ежемесячно"
	CUSTOM = "Пользовательский"

	def __str__(self) -> str:
		return str(self.value)

	@property
	def display_name(self) -> str:
		return self.__str__()


@database_enum
class AnalysisType(Enum):
	"""Types of AI analysis."""
	# Format: NAME = ("db_value", "Display Name", "Emoji")
	
	# Core analysis types (v1.0)
	SENTIMENT = ("sentiment", "Анализ тональности", "😊")
	TRENDS = ("trends", "Обнаружение трендов", "📈")
	ENGAGEMENT = ("engagement", "Вовлеченность", "👥")
	KEYWORDS = ("keywords", "Ключевые слова", "🔍")
	TOPICS = ("topics", "Темы", "💡")
	TOXICITY = ("toxicity", "Токсичность", "⚠️")
	DEMOGRAPHICS = ("demographics", "Демография", "👤")
	
	# Extended analysis types (v2.0)
	VIRAL_DETECTION = ("viral_detection", "Вирусный потенциал", "🔥")
	INFLUENCER_ACTIVITY = ("influencer", "Активность инфлюенсеров", "⭐")
	COMPETITOR_TRACKING = ("competitor", "Мониторинг конкурентов", "🔎")
	CUSTOMER_INTENT = ("intent", "Намерения клиентов", "🎯")
	BRAND_MENTIONS = ("brand_mentions", "Упоминания бренда", "🏷️")
	HASHTAG_ANALYSIS = ("hashtag_analysis", "Анализ хэштегов", "#️⃣")

	def __init__(self, db_value, display_name, emoji):
		self._db_value = db_value
		self._display_name = display_name
		self._emoji = emoji

	@property
	def db_value(self):
		return self._db_value

	@property
	def display_name(self):
		return self._display_name

	@property
	def emoji(self):
		return self._emoji


@database_enum
class BotActionType(Enum):
	"""Action types the AI bot can perform."""
	# Format: NAME = ("db_value", "Display Name", "Emoji")
	COMMENT = ("comment", "Комментарий", "💬")
	REPLY = ("reply", "Ответ", "↩️")
	DIRECT_MESSAGE = ("dm", "Личное сообщение", "✉️")
	POST = ("post", "Пост", "📝")
	REACTION = ("reaction", "Реакция", "❤️")
	MODERATION = ("moderation", "Модерация", "🛡️")
	NOTIFICATION = ("notification", "Уведомление", "🔔")

	def __init__(self, db_value, display_name, emoji):
		self._db_value = db_value
		self._display_name = display_name
		self._emoji = emoji

	@property
	def db_value(self):
		return self._db_value

	@property
	def display_name(self):
		return self._display_name

	@property
	def emoji(self):
		return self._emoji


@database_enum
class BotTriggerType(Enum):
	"""Types of bot trigger conditions."""
	KEYWORD_MATCH = "keyword_match"
	SENTIMENT_THRESHOLD = "sentiment_threshold"
	ACTIVITY_SPIKE = "activity_spike"
	USER_MENTION = "user_mention"
	TIME_BASED = "time_based"
	MANUAL = "manual"


@database_enum
class SentimentLabel(Enum):
	"""Sentiment analysis labels."""
	POSITIVE = "positive"
	NEGATIVE = "negative"
	NEUTRAL = "neutral"
	MIXED = "mixed"


@database_enum
class ContentType(Enum):
	"""Types of content can be monitored."""
	# Format: NAME = ("db_value", "Display Name", "Emoji")
	POSTS = ("posts", "Посты", "📝")
	COMMENTS = ("comments", "Комментарии", "💬")
	VIDEOS = ("videos", "Видео", "🎥")
	STORIES = ("stories", "Истории", "📸")
	REELS = ("reels", "Reels", "🎬")
	REACTIONS = ("reactions", "Реакции", "❤️")
	MENTIONS = ("mentions", "Упоминания", "@")

	def __init__(self, db_value, display_name, emoji):
		self._db_value = db_value
		self._display_name = display_name
		self._emoji = emoji

	@property
	def db_value(self):
		return self._db_value

	@property
	def display_name(self):
		return self._display_name

	@property
	def emoji(self):
		return self._emoji


@database_enum
class MonitoringStatus(Enum):
	"""Status of source monitoring."""
	ACTIVE = "active"
	PAUSED = "paused"
	ERROR = "error"
	DISABLED = "disabled"
