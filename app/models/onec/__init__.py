"""Persisted, tenant-scoped metadata for authorized read-only 1C queries (P17-T03).

Only metadata is stored: the question text is Fernet-encrypted, the typed plan is
stored as validated JSON, and row payloads are never written here.
"""

from .query_history import OneCQueryHistory

__all__ = ["OneCQueryHistory"]
