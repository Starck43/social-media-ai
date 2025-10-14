"""Platform and source-related enum types."""
from enum import Enum

from app.utils.db_enums import database_enum


@database_enum
class PlatformType(Enum):
	"""Social media platforms."""
	VK = "vk"
	TELEGRAM = "telegram"

	def get_client_class(self):
		"""Get client class name for the platform."""
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
class MonitoringStatus(Enum):
	"""Status of source monitoring."""
	ACTIVE = "active"
	PAUSED = "paused"
	ERROR = "error"
	DISABLED = "disabled"
