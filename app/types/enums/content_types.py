"""Content-related enum types."""
from enum import Enum

from app.utils.db_enums import database_enum


@database_enum
class ContentType(Enum):
	"""Types of content that can be monitored."""
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
	
	@property
	def label(self):
		"""Get label with emoji."""
		return f"{self._emoji} {self._display_name}"
	
	@classmethod
	def choices(cls, use_db_value: bool = True):
		"""Get choices for form fields."""
		if use_db_value:
			return [(c.db_value, c.label) for c in cls]
		return [(c.name, c.label) for c in cls]
	
	@classmethod
	def get_by_value(cls, value: str):
		"""Get enum by database value."""
		for content in cls:
			if content.db_value == value:
				return content
		return None
	
	@classmethod
	def get_by_name(cls, name: str):
		"""Get enum by name."""
		try:
			return cls[name]
		except KeyError:
			return None
	
	@property
	def required_media_type(self) -> str:
		"""Get required MediaType for this ContentType."""
		# Map ContentType to MediaType capability
		mapping = {
			"posts": "text",      # Posts are primarily text
			"comments": "text",   # Comments are text
			"videos": "video",    # Videos need video capability
			"stories": "image",   # Stories are images/short videos
			"reels": "video",     # Reels are videos
			"reactions": "text",  # Reactions are analyzed as text sentiment
			"mentions": "text",   # Mentions are text
		}
		return mapping.get(self.db_value, "text")


@database_enum
class MediaType(Enum):
	"""Types of media content for LLM processing."""
	# Format: NAME = ("db_value", "Display Name", "Emoji")
	TEXT = ("text", "Текст", "📝")
	IMAGE = ("image", "Изображение", "🖼️")
	VIDEO = ("video", "Видео", "🎥")
	AUDIO = ("audio", "Аудио", "🎵")
	
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
			return [(c.db_value, c.label) for c in cls]
		return [(c.name, c.label) for c in cls]
	
	@classmethod
	def get_by_value(cls, value: str):
		"""Get enum by database value."""
		for media in cls:
			if media.db_value == value:
				return media
		return None
	
	@classmethod
	def get_by_name(cls, name: str):
		"""Get enum by name or value (for backward compatibility)."""
		# Try by name first
		try:
			return cls[name.upper()]
		except (KeyError, AttributeError):
			pass
		
		# Try by db_value
		return cls.get_by_value(name)
