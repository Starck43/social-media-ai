"""
Script to update permission action types from 'edit' to 'update'.

NOTE: `permissions.action_type` is a Postgres enum (VIEW/CREATE/UPDATE/...),
so 'EDIT' was never a valid stored value — the detection below keys off the
codename suffix ('.edit') instead, and the enum update sets a real member.
"""
import logging
import sys
from pathlib import Path

# Add project root to path
project_root = str(Path(__file__).parent.parent)
sys.path.append(project_root)

from sqlalchemy import text
from app.core.config import settings
from app.core.database import SessionLocal

# Set up logging
logger = logging.getLogger(__name__)


def update_permission_actions():
    """Update any 'edit' codename actions to 'update' in the database."""
    logger.info("🔄 Updating permission action types from 'edit' to 'update'...")

    db = SessionLocal()
    try:
        # First, check if there are any permissions with an 'edit' action token
        # (SQLAlchemy text() needs '%%' for a literal '%').
        result = db.execute(
            text(f"""
                SELECT COUNT(*)
                FROM "{settings.DB_SCHEMA}".permissions
                WHERE codename LIKE '%%.edit' OR codename LIKE '%%.EDIT'
            """)
        ).scalar()

        if result == 0:
            logger.info("✅ No permissions with 'edit' action found. Nothing to update.")
            return

        # Update codename and set the enum to a real member.
        update_result = db.execute(
            text(f"""
                UPDATE "{settings.DB_SCHEMA}".permissions
                SET action_type = 'UPDATE',
                    codename = REGEXP_REPLACE(codename, '\\.edit$', '.update'),
                    updated_at = NOW()
                WHERE codename LIKE '%%.edit' OR codename LIKE '%%.EDIT'
                RETURNING id, codename, action_type
            """)
        )

        updated = update_result.rowcount
        logger.info(f"✅ Updated {updated} permission records")

        if updated > 0:
            logger.info("\nUpdated permissions:")
            for row in update_result.fetchall():
                logger.info(f"- ID: {row[0]}, Codename: {row[1]}, Action: {row[2]}")

        db.commit()

    except Exception as e:
        db.rollback()
        logger.error(f"❌ Error updating permissions: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    print("Running permission action type update...")
    update_permission_actions()
    print("✅ Update completed")
