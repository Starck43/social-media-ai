"""Bot-related enum types."""
from enum import Enum

from app.utils.db_enums import database_enum


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
