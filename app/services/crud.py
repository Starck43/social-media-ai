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
    return [mapper.class_ for mapper in Base.registry.mappers if hasattr(mapper.class_, "__tablename__")]


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

    from sqlalchemy import MetaData, Table, inspect

    from app.core.access_catalog import resource_for

    resource = resource_for(model_name)
    if resource is None:
        return
    # Historical Alembic revisions run before the transition column removal.
    physical = {c["name"] for c in inspect(connection).get_columns("permissions", schema=Permission.__table__.schema)}
    if "codename" in physical:
        table = Table("permissions", MetaData(), schema=Permission.__table__.schema, autoload_with=connection)
    else:
        table = Permission.__table__
    existing = set(
        connection.execute(select(table.c.action_type).where(table.c.model_type_id == model_type_id)).scalars()
    )
    for action in resource.actions:
        if action.name in existing or action in existing:
            continue
        labels = getattr(table.c.action_type.type, "enums", None)
        action_token = action.name
        if labels and action_token not in labels:
            action_token = "edit" if action.db_value == "update" and "edit" in labels else action.db_value
            if action_token not in labels:
                continue  # New actions are seeded by the transition migration.
        values = dict(
            name=f"Can {action.db_value} {resource.key}", model_type_id=model_type_id, action_type=action_token
        )
        if "codename" in physical:
            values["codename"] = f"{app_label.lower()}.{model_name.lower()}.{action.db_value}"
        connection.execute(table.insert().values(**values))
