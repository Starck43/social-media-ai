"""Notification-related enum types."""
from enum import Enum

from app.utils.db_enums import database_enum


@database_enum
class NotificationType(Enum):
	"""Types of system notifications."""
	# Format: NAME = ("db_value", "Display Name", "Emoji")
	REPORT_READY = ("report_ready", "Отчет готов", "📊")
	MOOD_CHANGE = ("mood_change", "Изменение настроения", "😊😞")
	TREND_ALERT = ("trend_alert", "Оповещение о тренде", "📈")
	TOPIC_RESUMED = ("topic_resumed", "Тема возобновлена", "🔄")
	API_ERROR = ("api_error", "Ошибка API", "❌")
	CONNECTION_ERROR = ("connection_error", "Ошибка соединения", "🔌")
	SOURCE_INACTIVE = ("source_inactive", "Источник неактивен", "⏸️")
	RATE_LIMIT_WARNING = ("rate_limit_warning", "Предупреждение о лимите", "⚠️")
	BOT_COMMENT = ("bot_comment", "Комментарий бота", "🤖💬")
	BOT_SKIPPED = ("bot_skipped", "Бот пропущен", "⏭️")
	SYSTEM_BACKUP = ("system_backup", "Резервное копирование", "💾")
	SYSTEM_UPDATE = ("system_update", "Обновление системы", "🔄")
	SUBSCRIBED_ACTIVITY = ("subscribed_activity", "Активность подписки", "🔔")
	KEYWORD_MENTION = ("keyword_mention", "Упоминание ключевого слова", "🔑")
	
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
		"""Get label with emoji."""
		return f"{self._emoji} {self._display_name}"
	
	@classmethod
	def choices(cls, use_db_value: bool = False):
		"""Get choices for form fields (store_as_name=True by default)."""
		if use_db_value:
			return [(n.db_value, n.label) for n in cls]
		return [(n.name, n.label) for n in cls]
	
	@classmethod
	def get_by_value(cls, value: str):
		"""Get enum by database value."""
		for notification in cls:
			if notification.db_value == value:
				return notification
		return None
	
	@classmethod
	def get_by_name(cls, name: str):
		"""Get enum by name."""
		try:
			return cls[name]
		except KeyError:
			return None
