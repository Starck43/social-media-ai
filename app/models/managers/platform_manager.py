from __future__ import annotations

from datetime import datetime
from typing import Optional, TYPE_CHECKING

from .base_manager import BaseManager

if TYPE_CHECKING:
	from ..platform import Platform
	from app.types.models import PlatformType


class PlatformManager(BaseManager['Platform']):
	"""
	Manager for Platform model with specialized methods for platform management.
	
	Provides methods for:
	— Managing active platforms
	— Rate limit tracking
	— Sync status monitoring
	— Platform statistics
	
	Note: By default, only active platforms (is_active=True) are returned.
	"""

	def __init__(self):
		from ..platform import Platform
		super().__init__(Platform)

	async def get_by_name(
		self,
		name: str,
		is_active: Optional[bool] = True
	) -> Optional['Platform']:
		"""
		Get platform by exact name match.

		Args:
			name: Platform name to search for
			is_active: Filter by active status (default: True)

		Returns:
			Platform object if found, None otherwise
		"""
		qs = self.filter(name=name)
		
		if is_active is not None:
			qs = qs.filter(is_active=is_active)
		
		return await qs.first()

	async def get_active_platforms(
		self,
		platform_type: Optional['PlatformType'] = None
	) -> list['Platform']:
		"""
		Get all active platforms.

		Args:
			platform_type: An optional filter by platform type

		Returns:
			List of active Platform objects
		"""
		qs = self.filter(is_active=True)
		
		if platform_type:
			qs = qs.filter(platform_type=platform_type)
		
		return await qs

	async def get_by_type(
		self,
		platform_type: 'PlatformType',
		is_active: Optional[bool] = True
	) -> list['Platform']:
		"""
		Get platforms by type.

		Args:
			platform_type: Type of platform (VKONTAKTE, TELEGRAM, etc.)
			is_active: Filter by active status (default: True)

		Returns:
			List of Platform objects
		"""
		qs = self.filter(platform_type=platform_type)
		
		if is_active is not None:
			qs = qs.filter(is_active=is_active)
		
		return await qs

	async def update_rate_limit(
		self,
		platform_id: int,
		remaining: Optional[int] = None,
		reset_at: Optional[datetime] = None
	) -> Optional['Platform']:
		"""
		Update rate limit information for a platform.

		Args:
			platform_id: ID of the platform
			remaining: Remaining rate limit count
			reset_at: Rate limit reset timestamp

		Returns:
			Updated Platform object or None if not found
		"""
		update_data = {}
		
		if remaining is not None:
			update_data['rate_limit_remaining'] = remaining
		
		if reset_at is not None:
			update_data['rate_limit_reset_at'] = reset_at
		
		if update_data:
			return await self.update_by_id(platform_id, **update_data)
		
		return await self.get(id=platform_id)

	async def create_platform(
		self,
		name: str,
		platform_type: 'PlatformType',
		base_url: str,
		params: Optional[dict] = None,
		is_active: bool = True
	) -> 'Platform':
		"""
		Create a new platform with validation.

		Args:
			name: Platform name (e.g., 'VK', 'Telegram')
			platform_type: Type of platform
			base_url: Base URL for the platform
			params: API configuration parameters
			is_active: Whether the platform is active

		Returns:
			Created Platform object

		Raises:
			ValueError: If platform with same name already exists
		"""
		existing = await self.get_by_name(name, is_active=None)
		if existing:
			raise ValueError(f"Platform with name '{name}' already exists")

		return await self.create(
			name=name,
			platform_type=platform_type,
			base_url=base_url,
			params=params or {},
			is_active=is_active
		)

	async def get_with_sources(
		self,
		platform_id: int
	) -> Optional['Platform']:
		"""
		Get platform with prefetched sources relationship.

		Args:
			platform_id: Platform ID

		Returns:
			Platform object with sources loaded
		"""
		return await (
			self.filter(id=platform_id)
			.prefetch_related("sources")
			.first()
		)

	async def bulk_update_status(
		self,
		platform_ids: list[int],
		is_active: bool
	) -> int:
		"""
		Update active status for multiple platforms.

		Args:
			platform_ids: List of platform IDs
			is_active: New active status

		Returns:
			Amount updated platforms
		"""
		if not platform_ids:
			return 0

		updated_count = 0
		for platform_id in platform_ids:
			result = await self.update_by_id(platform_id, is_active=is_active)
			if result:
				updated_count += 1

		return updated_count

	async def get_stats(self) -> dict:
		"""
		Get statistics for all platforms.

		Returns:
			Dictionary with platform statistics
		"""
		from ..source import Source
		
		# Get all platforms (including inactive for complete stats)
		all_platforms = await self.filter()
		
		stats = {
			'total': len(all_platforms),
			'active': len([p for p in all_platforms if p.is_active]),
			'inactive': len([p for p in all_platforms if not p.is_active]),
			'by_type': {},
			'sources_per_platform': {}
		}
		
		for platform in all_platforms:
			# Count by type
			type_name = platform.platform_type.value if hasattr(platform.platform_type, 'value') else str(platform.platform_type)
			stats['by_type'][type_name] = stats['by_type'].get(type_name, 0) + 1
			
			# Count sources for this platform
			sources = await Source.objects.filter(platform_id=platform.id)
			stats['sources_per_platform'][platform.name] = len(sources)
		
		return stats

	async def search_platforms(
		self,
		query: str,
		is_active: Optional[bool] = True
	) -> list['Platform']:
		"""
		Search platforms by name.

		Args:
			query: Search query string
			is_active: Filter by active status (default: True)

		Returns:
			List of matching Platform objects
		"""
		# Get platforms with optional active filter
		if is_active is not None:
			platforms = await self.filter(is_active=is_active)
		else:
			platforms = await self.filter()
		
		# Filter by name in memory
		if query:
			platforms = [
				p for p in platforms
				if query.lower() in (p.name or '').lower()
			]
		
		return platforms
