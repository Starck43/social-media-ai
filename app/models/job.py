"""Compatibility aliases; canonical declarations live in domain packages."""

from .managers.job_manager import JobManager
from .scheduling.job import Job

__all__ = ["Job", "JobManager"]
