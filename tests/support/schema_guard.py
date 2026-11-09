"""Fail-closed guard that keeps DB tests off the working schema.

``require_isolated_schema()`` derives the working schema from settings (never a
literal), refuses to continue when the resolved test schema equals it, and
returns the isolated test schema otherwise. This lets the same DB suite run
against ``test_schema`` or an isolated scratch schema (``DB_TEST_SCHEMA``) with
no edits between runs, removing the forced serialization on the single shared
``test_schema``.
"""

from __future__ import annotations

from app.core.config import settings
from scripts.setup_test_db import working_schema_before_redirect


def require_isolated_schema() -> str:
    """Return the isolated test schema, refusing the working schema.

    Raises:
        RuntimeError: when the test schema equals the working schema derived
            from settings. The message names both schemas.
    """
    test_schema = settings.DB_SCHEMA
    working_schema = working_schema_before_redirect()
    if test_schema == working_schema:
        raise RuntimeError(
            "Refusing to run DB tests against the working schema "
            f"{working_schema!r}; resolved test schema is {test_schema!r}. "
            "Set DB_TEST_SCHEMA to a schema other than the working schema."
        )
    return test_schema
