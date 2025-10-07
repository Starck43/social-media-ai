from typing import cast

from sqlalchemy import text
from sqlalchemy.orm.clsregistry import ClsRegistryToken

from app.core.config import settings
from app.models import Base
from app.types.models import ActionType


def get_model(tablename: str) -> type | ClsRegistryToken:
	"""
	Получить класс модели по имени таблицы.
	"""
	for model in Base.registry._class_registry.values():
		if hasattr(model, '__tablename__') and model.__tablename__ == tablename:
			return model
	raise ValueError(f"No model found for table '{tablename}'")


def get_models() -> list[type | ClsRegistryToken]:
	"""
	Получить список всех зарегистрированных моделей SQLAlchemy.
	"""
	return [
		model for model in Base.registry._class_registry.values()
		if hasattr(model, '__tablename__')
	]


def create_tables(engine, checkfirst: bool = True) -> None:
	"""
	Создать все таблицы в базе данных.

	Args:
		engine: Движок SQLAlchemy
		checkfirst: Если True, проверяет существование таблиц перед созданием
	"""
	Base.metadata.create_all(bind=engine, checkfirst=checkfirst)


def drop_tables(engine, checkfirst: bool = True) -> None:
	"""
	Удалить все таблицы из базы данных.

	Args:
		engine: Движок SQLAlchemy
		checkfirst: Если True, проверяет существование таблиц перед удалением
	"""
	Base.metadata.drop_all(bind=engine, checkfirst=checkfirst)


def create_permissions_for_model(connection, model_type_id: int, app_label: str, model_name: str):
	"""Create permissions for model if they don't exist."""

	schema = settings.DB_SCHEMA

	# Check if permissions exist for current model
	existing_permissions = {row[0] for row in connection.execute(
		text(f'SELECT codename FROM "{schema}"."permissions" WHERE model_type_id = :model_type_id'),
		{"model_type_id": model_type_id}
	).fetchall()}

	permissions_to_create = []

	for action in ActionType:
		try:
			action_name = action.name
			action_value = action.value

			codename = f"{app_label}.{model_name}.{action_value}"

			if codename not in existing_permissions:
				permissions_to_create.append({
					"codename": codename,
					"name": f"Can {action_value} {model_name.capitalize()}",
					"action_type": action_name,
					"model_type_id": model_type_id
				})
		except ValueError:
			print(f"⚠️ Skipping invalid action type: {action.value}")
			continue

	if permissions_to_create:
		connection.execute(
			text(f"""
				INSERT INTO "{schema}"."permissions" 
				(codename, name, action_type, model_type_id, created_at, updated_at) 
				VALUES (
					:codename, :name, :action_type, :model_type_id, NOW(), NOW()
				)
			"""),
			permissions_to_create
		)
		print(f"✅ Created {len(permissions_to_create)} new permissions for {app_label}.{model_name}")
	else:
		print(f"ℹ️ Permissions for {app_label}.{model_name} already exist")
