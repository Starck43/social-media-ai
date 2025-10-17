"""
Helper functions for working with enums.
"""
from typing import Any


def get_enum_value(enum_val: Any) -> str:
	"""
	Get string value from enum, handling tuple enums and simple enums.
	
	For tuple enums (with db_value), returns db_value.
	For simple enums, returns value.
	For strings, returns as-is.
	
	Args:
		enum_val: Enum value, can be tuple enum, simple enum, or string
		
	Returns:
		String representation of the value
		
	Examples:
		>>> get_enum_value(PlatformType.VK)  # tuple enum
		'vk'
		>>> get_enum_value(SomeSimpleEnum.VALUE)  # simple enum
		'value'
		>>> get_enum_value('already_string')
		'already_string'
	"""
	if enum_val is None:
		return ''
	
	# For tuple enum, use db_value
	if hasattr(enum_val, 'db_value'):
		return enum_val.db_value
	
	# For simple enum, use value
	if hasattr(enum_val, 'value'):
		return str(enum_val.value)
	
	# Already a string or other type
	return str(enum_val)


def get_enum_label(enum_val: Any) -> str:
	"""
	Get display label from enum with emoji.
	
	For tuple enums with label property, returns label.
	Otherwise, returns display_name or string value.
	
	Args:
		enum_val: Enum value
		
	Returns:
		Display label (with emoji if available)
		
	Examples:
		>>> get_enum_label(PlatformType.VK)
		'🔵 ВКонтакте'
		>>> get_enum_label('simple_string')
		'simple_string'
	"""
	if enum_val is None:
		return ''
	
	# For tuple enum with label
	if hasattr(enum_val, 'label'):
		return enum_val.label
	
	# For tuple enum with display_name
	if hasattr(enum_val, 'display_name'):
		return enum_val.display_name
	
	# Fallback
	return str(enum_val)
