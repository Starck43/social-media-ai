from sqlalchemy import text, inspect as sa_inspect

from app.core.config import settings
from app.services.crud import create_permissions_for_model
from scripts.migrations.deps import get_model_class_by_table_name, get_app_label_from_model


def register_model_types(connection, is_upgrade: bool = True):
	"""Automatically register/unregister models in ModelType."""

	inspector = sa_inspect(connection)
	schema = settings.DB_SCHEMA

	# Get all tables in a schema (excluding system tables)
	all_tables = set(inspector.get_table_names(schema=schema))
	user_tables = all_tables - {'model_types', 'role_permission', 'alembic_version'}

	# Handle case when model_types table doesn't exist
	if 'model_types' not in all_tables:
		if is_upgrade:
			print("⚠️ model_types table doesn't exist. Nothing to register. Skipping...")
		else:
			print("ℹ️ model_types table doesn't exist. Nothing to clean up. Skipping...")
		return

	# Get now registered tables
	registered_tables = {row[0] for row in connection.execute(
		text(f'SELECT table_name FROM "{schema}"."model_types"')
	).fetchall()}

	# ✅ Регистрируем новые таблицы
	tables_to_register = user_tables - registered_tables

	if tables_to_register:
		print(f"ℹ️ [ModelType]: Registering new tables: {list(tables_to_register)}")

	for table_name in tables_to_register:
		model_class = get_model_class_by_table_name(table_name)
		if model_class is None:
			print(f"⚠️ No model class found for table: {table_name}. Skipping...")
			continue

		app_label = get_app_label_from_model(model_class)
		model_name = model_class.__name__

		print(f"✅ {app_label}.{table_name}: {model_name} added")

		connection.execute(
			text(f"""
				INSERT INTO "{schema}"."model_types" 
				(app_name, model_name, table_name, description, is_managed, created_at, updated_at) 
				VALUES (:app_label, :model_name, :table_name, 'Automatically registered model', true, NOW(), NOW())
			"""),
			{
				"app_label": app_label.lower(),
				"model_name": model_name.lower(),
				"table_name": table_name,
			}
		)

		# Get model_type_id for the newly registered model
		model_type_id = connection.execute(
			text(f'SELECT id FROM "{schema}"."model_types" WHERE table_name = :table_name'),
			{"table_name": table_name}
		).scalar()

		# Create permissions for current model
		if model_type_id:
			create_permissions_for_model(connection, model_type_id, app_label, model_name)
		else:
			print(f"❌ Failed to get model_type_id for {table_name}")

	# ✅ Удаляем записи для несуществующих таблиц
	tables_to_remove = registered_tables - user_tables
	if tables_to_remove:
		print(f"ℹ️ [ModelType]: Removing registrations for dropped tables: {list(tables_to_remove)}")

	for table_name in tables_to_remove:
		print(f"🗑️ {table_name} removed")
		connection.execute(
			text(f'DELETE FROM "{schema}"."model_types" WHERE table_name = :table_name'),
			{"table_name": table_name}
		)
