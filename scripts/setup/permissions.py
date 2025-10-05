import sys

from sqlalchemy import text

from app.core.config import settings
from app.core.database import SessionLocal
from app.services.crud import create_permissions_for_model
from app.services.user.permissions import RolePermissionService
from app.types.models import ActionType


def seed_permissions():
	"""Function to create missing permissions"""

	schema = settings.DB_SCHEMA
	db = SessionLocal()

	try:
		# Check if model_types table exists
		table_exists = db.execute(
			text('''
				SELECT EXISTS (
					SELECT FROM information_schema.tables 
					WHERE table_schema = :schema 
					AND table_name = 'model_types'
				)
			'''),
			{"schema": schema}
		).scalar()

		if not table_exists:
			print("❌ Error: 'model_types' table does not exist")
			return

		# Get registered tables list
		registered_tables = db.execute(
			text(f'SELECT id, app_name, model_name FROM "{schema}"."model_types"')
		).fetchall()

		if not registered_tables:
			print("ℹ️ No tables found in the 'model_types' table")
			print("Please make sure you have run the model type migrations first.")
			return

		print(f"📋 Found {len(registered_tables)} registered tables")

		# Print available action types for debugging
		print(f"🔄 Available action types: {[action.name for action in ActionType]}")

		for t in registered_tables:
			print(f"\n🔍 Processing model: {t.app_name}.{t.model_name} (ID: {t.id})")

			try:
				# Create permissions for each registered table
				create_permissions_for_model(db.connection(), t.id, t.app_name, t.model_name)
				db.commit()  # Commit after each model to avoid partial updates
			except Exception as e:
				db.rollback()
				print(f"❌ Error creating permissions for {t.app_name}.{t.model_name}: {str(e)}")
				# Continue with next model even if one fails
				continue

	except Exception as e:
		db.rollback()
		print(f"❌ Unexpected error: {str(e)}", file=sys.stderr)
		raise
	finally:
		db.close()


def assign_default_roles_permissions():
	"""
	Initial assignment of default permissions.
	Must be run after roles and permissions are created.
	"""

	print("\nDefault permissions assigning ...")
	RolePermissionService.assign_default_permissions()


if __name__ == "__main__":
	seed_permissions()
	assign_default_roles_permissions()
