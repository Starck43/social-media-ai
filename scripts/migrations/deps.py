from typing import Optional

from app.models import Base


def get_model_class_by_table_name(table_name: str) -> Optional[type]:

	"""Get model class by table name using SQLAlchemy's mapper registry."""
	for mapper in Base.registry.mappers:
		cls = mapper.class_
		if cls.__tablename__ == table_name:
			return cls
	return None


def get_app_label_from_model(model_class) -> str:
	"""Get app_label from model class with a fallback."""

	if hasattr(model_class, '_app_label'):
		return model_class._app_label
	elif hasattr(model_class, 'get_app_label'):
		return model_class.get_app_label()
	else:
		# Fallback: extract from module path
		module_parts = model_class.__module__.split('.')
		return module_parts[0] if module_parts else 'app'
