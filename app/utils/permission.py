"""Stable permission representation, independent of ORM/UI naming."""

from app.core.access_catalog import resource_for


def generate_permission_codename(app_label, model_name, action):
    resource = resource_for(model_name)
    if resource is None or action not in resource.actions:
        raise ValueError("Unknown resource action")
    return f"{resource.key}.{action.db_value}"
