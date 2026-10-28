"""Compatibility imports for the modular task web surface.

The router and historical helper imports remain available here while forms,
use cases, presenters, and HTTP endpoints live in focused modules.
"""

from .task_shared import *
from .task_forms import *
from .task_use_cases import *
from .task_presenters import *
from .task_create_routes import *
from .task_routes import *
from .job_routes import *

__all__ = [name for name in globals() if not name.startswith("__")]
