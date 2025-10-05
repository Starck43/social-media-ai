"""
Script to update permission action types from 'edit' to 'update'.
"""
import logging
import sys
from pathlib import Path

from sqlalchemy import text
from app.core.database import SessionLocal

# Add project root to path
project_root = str(Path(__file__).parent.parent)
sys.path.append(project_root)

# Set up logging
logger = logging.getLogger(__name__)


def update_permission_actions():
    """Update all 'edit' action types to 'update' in the database."""
    logger.info("🔄 Updating permission action types from 'edit' to 'update'...")
    
    db = SessionLocal()
    try:
        # First, check if there are any permissions with 'edit' action
        result = db.execute(
            text("""
                SELECT COUNT(*) 
                FROM social_manager.permissions 
                WHERE action_type = 'EDIT' OR codename LIKE '%.EDIT'
            """)
        ).scalar()
        
        if result == 0:
            logger.info("✅ No permissions with 'edit' action found. Nothing to update.")
            return
            
        # Update action_type in permissions table
        update_result = db.execute(
            text("""
                UPDATE social_manager.permissions 
                SET action_type = 'UPDATE',
                    codename = REGEXP_REPLACE(codename, '\\.edit$', '.update'),
                    updated_at = NOW()
                WHERE action_type = 'EDIT' 
                OR codename LIKE '%.edit'
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
