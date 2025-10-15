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
	
	@property
	def label(self):
		"""Get label with emoji: '💬 Комментарий'"""
		return f"{self._emoji} {self._display_name}"
	
	@classmethod
	def choices(cls, use_db_value: bool = False):
		"""
		Get choices for form fields with an emoji.
		
		Args:
			use_db_value: If True, use db_value; if False, use enum name (for store_as_name=True)
		
		Returns:
			List of (value, label) tuples for SelectField
		"""
		if use_db_value:
			return [(action.db_value, action.label) for action in cls]
		return [(action.name, action.label) for action in cls]
	
	@classmethod
	def get_by_value(cls, value: str):
		"""Get enum by database value."""
		for action in cls:
			if action.db_value == value:
				return action
		return None
	
	@classmethod
	def get_by_name(cls, name: str):
		"""Get enum by name."""
		try:
			return cls[name]
		except KeyError:
			return None


@database_enum
class BotTriggerType(Enum):
	"""Types of bot trigger conditions."""
	# Format: NAME = ("db_value", "Display Name", "Emoji")
	KEYWORD_MATCH = ("keyword_match", "Совпадение ключевых слов", "🔑")
	SENTIMENT_THRESHOLD = ("sentiment_threshold", "Порог тональности", "😊")
	ACTIVITY_SPIKE = ("activity_spike", "Всплеск активности", "📈")
	USER_MENTION = ("user_mention", "Упоминание пользователя", "@")
	TIME_BASED = ("time_based", "По расписанию", "⏰")
	MANUAL = ("manual", "Вручную", "👆")

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
	
	@property
	def label(self):
		"""Get label with emoji: '🔑 Совпадение ключевых слов'"""
		return f"{self._emoji} {self._display_name}"
	
	@classmethod
	def choices(cls, use_db_value: bool = False):
		"""
		Get choices for form fields with an emoji.
		
		Args:
			use_db_value: If True, use db_value; if False, use enum name (for store_as_name=True)
		
		Returns:
			List of (value, label) tuples for SelectField
		"""
		if use_db_value:
			return [(trigger.db_value, trigger.label) for trigger in cls]
		return [(trigger.name, trigger.label) for trigger in cls]
	
	@classmethod
	def get_by_value(cls, value: str):
		"""Get enum by database value."""
		for trigger in cls:
			if trigger.db_value == value:
				return trigger
		return None
	
	@classmethod
	def get_by_name(cls, name: str):
		"""Get enum by name."""
		try:
			return cls[name]
		except KeyError:
			return None
