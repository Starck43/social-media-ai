from app.models import Platform
from app.services.social.base import BaseClient
from app.services.social.vk_client import VKClient
from app.services.social.tg_client import TelegramClient
from app.types import PlatformType


def get_social_client(platform: Platform) -> BaseClient:
	"""
	Factory function to get the appropriate social media client based on platform type.
	
	Args:
		platform: Platform instance with type and configuration
		
	Returns:
		BaseClient: Appropriate client instance (VKClient, TelegramClient, etc.)
		
	Raises:
		ValueError: If platform type is not supported
	"""
	# Map using enum values for comparison (platform_type might be string or enum)
	client_map = {
		'vk': VKClient,
		'telegram': TelegramClient,
	}
	
	# Get platform type value (works with both enum and string)
	# For tuple enum, use db_value; for simple enum use value; for string use as-is
	if hasattr(platform.platform_type, 'db_value'):
		platform_value = platform.platform_type.db_value
	elif hasattr(platform.platform_type, 'value'):
		platform_value = platform.platform_type.value
	else:
		platform_value = str(platform.platform_type)
	
	client_class = client_map.get(platform_value)
	if not client_class:
		raise ValueError(f"Unsupported platform type: {platform_value}")
	
	return client_class(platform)
