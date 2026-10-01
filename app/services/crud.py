from sqlalchemy import select
from sqlalchemy.orm.clsregistry import ClsRegistryToken

from app.models import Base, ModelType, Permission
from app.types import ActionType
from app.utils.permission import generate_permission_codename


def get_model(tablename: str) -> type | ClsRegistryToken:
	"""
	Получить класс модели по имени таблицы.
	"""
	for mapper in Base.registry.mappers:
		cls = mapper.class_
		if getattr(cls, "__tablename__", None) == tablename:
			return cls
	raise ValueError(f"No model found for table '{tablename}'")


def get_models() -> list[type | ClsRegistryToken]:
	"""
	Получить список всех зарегистрированных моделей SQLAlchemy.
	"""
	return [
		mapper.class_
		for mapper in Base.registry.mappers
		if hasattr(mapper.class_, "__tablename__")
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
	"""Create permissions for a model if they don't exist yet.

	`Permission` and `ModelType` are global models, so the async managers would
	also work — but the callers are not async: Alembic's `env.py` hands over a
	*sync* connection, and the setup script opens a sync `SessionLocal`. Mixing
	the manager layer in here would mean `asyncio.run` inside a migration.

	So this is the one place that stays on Core statements: built from the
	mapped tables rather than interpolated `text()` SQL, so the schema comes
	from the model metadata (`settings.DB_SCHEMA`) instead of being spelled
	out at the call site.
	"""
	table = Permission.__table__

	# Check if permissions exist for current model
	existing_codenames = {
		row[0]
		for row in connection.execute(
			select(table.c.codename).where(table.c.model_type_id == model_type_id)
		).fetchall()
	}

	permissions_to_create = []

	for action in ActionType:
		try:
			action_name = action.name

			codename = generate_permission_codename(app_label, model_name, action)
			action_value = action.db_value

			if codename not in existing_codenames:
				permissions_to_create.append(
					{
						"codename": codename,
						"name": f"Can {action_value} {model_name.capitalize()}",
						"action_type": action_name,
						"model_type_id": model_type_id,
					}
				)
		except ValueError:
			print(f"⚠️ Skipping invalid action type: {action.value}")
			continue

	if permissions_to_create:
		# `created_at`/`updated_at` come from TimestampMixin's server default
		connection.execute(table.insert(), permissions_to_create)
		print(f"✅ Created {len(permissions_to_create)} new permissions for {app_label}.{model_name}")
	else:
		print(f"ℹ️ Permissions for {app_label}.{model_name} already exist")
