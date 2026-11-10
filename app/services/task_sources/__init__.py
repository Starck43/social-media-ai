"""Read-time projections from connected task sources."""

from .bitrix24 import Bitrix24TaskSourceService, task_source_service

__all__ = ["Bitrix24TaskSourceService", "task_source_service"]
