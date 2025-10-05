"""
Permission Manager for SQLAlchemy models.
This module provides a PermissionManager class for the Permission model.
"""
from typing import TYPE_CHECKING, Optional, Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .base_manager import BaseManager

if TYPE_CHECKING:
    from ..permission import Permission


class PermissionManager(BaseManager):
    """Manager for Permission model with a custom query and CRUD methods."""
    
    def __init__(self):
        from ..permission import Permission as PM
        super().__init__(PM)
    
    def _create_with_session(self, db: Session, data: dict[str, Any]) -> 'Permission':
        """Internal method to create a permission with an existing session."""
        try:
            permission = self.model(**data)
            db.add(permission)
            db.commit()
            db.refresh(permission)
            return permission
        except SQLAlchemyError as e:
            db.rollback()
            raise e

    def _update_with_session(self, db: Session, permission: 'Permission', data: dict[str, Any]) -> 'Permission':
        """Internal method to update a permission with an existing session."""
        try:
            for key, value in data.items():
                if hasattr(permission, key):
                    setattr(permission, key, value)
            db.add(permission)
            db.commit()
            db.refresh(permission)
            return permission
        except SQLAlchemyError as e:
            db.rollback()
            raise e

    def _delete_with_session(self, db: Session, permission: 'Permission') -> bool:
        """Internal method to delete a permission with an existing session."""
        try:
            db.delete(permission)
            db.commit()
            return True
        except SQLAlchemyError as e:
            db.rollback()
            raise e

    def create(self, data: dict[str, Any], db: Session = None) -> 'Permission':
        """Create a new permission.

        Args:
            data: Dictionary containing permission data (codename, name, action_type, model_type_id)
            db: Optional SQLAlchemy session

        Returns:
            The created Permission instance
        """
        if db is None:
            from app.core.database import db_session
            with db_session() as session:
                return self._create_with_session(session, data)
        return self._create_with_session(db, data)
    
    def update(self, permission: 'Permission', data: dict[str, Any], db: Session = None) -> 'Permission':
        """Update an existing permission.

        Args:
            permission: The permission instance to update
            data: Dictionary containing fields to update
            db: Optional SQLAlchemy session

        Returns:
            The updated Permission instance
        """
        if db is None:
            from app.core.database import db_session
            with db_session() as session:
                return self._update_with_session(session, permission, data)
        return self._update_with_session(db, permission, data)

    def delete(self, permission: 'Permission', db: Session = None) -> bool:
        """Delete a permission.
        
        Args:
            permission: The permission instance to delete
            db: Optional SQLAlchemy session
            
        Returns:
            bool: True if deletion was successful
        """
        if db is None:
            from app.core.database import db_session
            with db_session() as session:
                return self._delete_with_session(session, permission)
        return self._delete_with_session(db, permission)

    def get_by_codename(self, codename: str, db: Session = None) -> Optional['Permission']:
        """Get a permission by its codename."""
        return self.get_queryset(db).filter(self.model.codename == codename).first()

    def filter_by_action_type(self, action_type: str, db: Session = None) -> list['Permission']:
        """Filter permissions by action type."""
        return self.get_queryset(db).filter(self.model.action_type == action_type).all()

    def get_for_model(self, model_name: str, db: Session = None) -> list['Permission']:
        """Get all permissions for a specific model."""
        return self.get_queryset(db).filter(
            self.model.codename.like(f'%.{model_name}.%')
        ).all()
