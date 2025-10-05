import sys
from enum import Enum
from typing import cast

from sqlalchemy import text

from app.core.database import SessionLocal
from app.types.models import UserRole


def seed_roles() -> None:
	"""Seed the database with default roles if they don't exist."""
	db = SessionLocal()

	try:
		print("Starting role seeding...")

		# Get existing roles
		existing_roles = db.execute(
			text("SELECT id, name, codename FROM social_manager.roles")
		).fetchall()

		existing_codenames = {role.codename for role in existing_roles}
		created_count = 0
		updated_count = 0

		# Create or update each role
		for role_enum in UserRole:
			# Add type annotation to help the linter
			role = cast(Enum, role_enum)
			role_codename = role.name  # Use the enum name in uppercase as codename
			description = role.value

			if role_codename in existing_codenames:
				# Update existing role
				db.execute(
					text("""
						UPDATE social_manager.roles 
						SET 
							name = :name, 
							description = :description,
							created_at = NOW(),
							updated_at = NOW()
						WHERE codename = :codename
					"""),
					{
						"name": role_codename.lower(),
						"codename": role_codename,
						"description": description,
					}
				)
				updated_count += 1
				print(f"✓ Updated role: {role_codename}")
			else:
				# Insert new role
				db.execute(
					text(
						"""
						INSERT INTO social_manager.roles (name, codename, description, created_at, updated_at)
						VALUES (:name, :codename, :description, NOW(), NOW())
					"""
				).bindparams(
					name=role_codename.lower(),  # Use the enum name in lowercase as the human-readable name
					codename=role_codename,  # Use the enum name as codename in uppercase
					description=description,
				)
				)
				created_count += 1
				print(f"✓ Created role: {role_codename}")

		db.commit()
		print(f"\n✅ Successfully seeded roles: {created_count} created, {updated_count} updated")

	except Exception as e:
		db.rollback()
		print(f"❌ Error seeding roles: {str(e)}", file=sys.stderr)
		raise
	finally:
		db.close()


if __name__ == "__main__":
	seed_roles()
