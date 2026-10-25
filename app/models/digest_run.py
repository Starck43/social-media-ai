"""Legacy digest imports; canonical declaration belongs to notifications."""

from .notifications.digest_run import DigestRun

# isort: split
from .managers.digest_run_manager import DigestRunManager

__all__ = ["DigestRun"]
