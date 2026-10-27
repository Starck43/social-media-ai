"""Compatibility aliases; canonical declarations live in domain packages."""

from .managers.digest_run_manager import DigestRunManager
from .notifications.digest_run import DigestRun

__all__ = ["DigestRun", "DigestRunManager"]
