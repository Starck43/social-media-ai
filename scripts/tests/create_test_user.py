import sys
from pathlib import Path

# Add project root to path
project_root = str(Path(__file__).resolve().parent.parent)
sys.path.append(project_root)

from app.core.database import SessionLocal
from app.core.hashing import get_password_hash
from app.models.user import User


def create_test_user():
    """Create a test admin user if it doesn't exist."""
    db = SessionLocal()
    
    try:
        # Check if user already exists
        existing_user = db.query(User).filter(User.email == "admin@example.com").first()
        if existing_user:
            print("Test admin user already exists!")
            return
            
        # Create new user
        test_user = User(
            username="admin",
            email="admin@example.com",
            hashed_password=get_password_hash("admin"),
            is_active=True,
            is_superuser=True
        )
        
        db.add(test_user)
        db.commit()
        print("Test admin user created successfully!")
        print(f"Username: admin")
        print(f"Password: admin")
        print(f"Email: admin@example.com")
        
    except Exception as e:
        print(f"Error creating test user: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    create_test_user()
