from typing import Any

import logging

logger = logging.getLogger(__name__)


def unescape_unicode(value: Any) -> str:
	"""Decode Unicode escape sequences in a string."""
	if not isinstance(value, str):
		return str(value)
	try:
		return value.encode().decode('unicode_escape')
	except Exception as e:
		logger.warning(f"Failed to decode string: {e}")
		return value


def register_template_filters(env):
	"""Register custom template filters."""
	env.filters['unescape_unicode'] = unescape_unicode
