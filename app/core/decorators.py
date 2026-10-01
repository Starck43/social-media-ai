def app_label(label: str):
	"""Декоратор для установки app_label"""

	def decorator(cls):
		cls._app_label = label
		return cls

	return decorator
