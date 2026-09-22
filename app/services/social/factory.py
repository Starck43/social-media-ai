from app.services.social.base import BaseClient
from app.services.social.tg_client import TelegramClient
from app.services.social.vk_client import VKClient


def get_social_client(platform) -> BaseClient:
	"""
	Factory function to get the appropriate social media client.

	Args:
		platform: Platform model instance (with .params, .platform_type, etc.)

	Returns:
		BaseClient: Appropriate client instance
	"""

	# Map using enum values for comparison (platform_type might be string or enum)
	client_map = {
		'vk': VKClient,
		'telegram': TelegramClient,
	}

	platform_type = platform.platform_type.db_value
	client_class = client_map.get(platform_type)
	if not client_class:
		raise ValueError(f"Unsupported platform type: {platform.platform_type}")

	return client_class(platform)
