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

    from app.core.access_catalog import resource_for

    resource = resource_for(model_class)
    if resource is None:
        raise ValueError("Unregistered model resource")
    return resource.key.split(".")[0]
