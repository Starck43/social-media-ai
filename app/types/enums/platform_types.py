"""Platform and source-related enum types."""
from enum import Enum

from app.utils.db_enums import database_enum


@database_enum
class PlatformType(Enum):
	"""Social media platforms."""
	# Format: NAME = ("db_value", "Display Name", "Emoji")
	VK = ("vk", "ВКонтакте", "🔵")
	TELEGRAM = ("telegram", "Telegram", "✈️")

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
	def choices(cls, use_db_value: bool = True):
		"""Get choices for form fields."""
		if use_db_value:
			return [(p.db_value, p.label) for p in cls]
		return [(p.name, p.label) for p in cls]
	
	@classmethod
	def get_by_value(cls, value: str):
		"""Get enum by database value."""
		for platform in cls:
			if platform.db_value == value:
				return platform
		return None
	
	@classmethod
	def get_by_name(cls, name: str):
		"""Get enum by name."""
		try:
			return cls[name]
		except KeyError:
			return None


@database_enum
class SourceType(Enum):
	"""Types of content sources in social networks."""
	# Format: NAME = ("db_value", "Display Name", "Emoji")
	
	# Common types
	USER = ("user", "Пользователь", "👤")
	GROUP = ("group", "Группа", "👥")
	CHANNEL = ("channel", "Канал", "📢")
	CHAT = ("chat", "Чат", "💬")
	PAGE = ("page", "Страница", "📄")

	# VK specific
	PUBLIC = ("public", "Публичная страница", "📰")
	EVENT = ("event", "Мероприятие", "🎉")
	MARKET = ("market", "Магазин", "🛒")
	ALBUM = ("album", "Альбом", "📸")

	# Telegram specific
	SUPERGROUP = ("supergroup", "Супергруппа", "👥+")
	BOT = ("bot", "Бот", "🤖")
	BROADCAST = ("broadcast", "Рассылка", "📻")
	
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
			return [(s.db_value, s.label) for s in cls]
		return [(s.name, s.label) for s in cls]
	
	@classmethod
	def get_by_value(cls, value: str):
		"""Get enum by database value."""
		for source_type in cls:
			if source_type.db_value == value:
				return source_type
		return None
	
	@classmethod
	def get_by_name(cls, name: str):
		"""Get enum by name."""
		try:
			return cls[name]
		except KeyError:
			return None


@database_enum
class MonitoringStatus(Enum):
	"""Status of source monitoring."""
	# Format: NAME = ("db_value", "Display Name", "Emoji")
	ACTIVE = ("active", "Активен", "✅")
	PAUSED = ("paused", "Приостановлен", "⏸️")
	ERROR = ("error", "Ошибка", "❌")
	DISABLED = ("disabled", "Отключен", "⛔")
	
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
	def choices(cls, use_db_value: bool = True):
		"""Get choices for form fields."""
		if use_db_value:
			return [(s.db_value, s.label) for s in cls]
		return [(s.name, s.label) for s in cls]
	
	@classmethod
	def get_by_value(cls, value: str):
		"""Get enum by database value."""
		for status in cls:
			if status.db_value == value:
				return status
		return None
	
	@classmethod
	def get_by_name(cls, name: str):
		"""Get enum by name."""
		try:
			return cls[name]
		except KeyError:
			return None
