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
	TEXT = "text"
	IMAGE = "image"
	VIDEO = "video"
	AUDIO = "audio"
