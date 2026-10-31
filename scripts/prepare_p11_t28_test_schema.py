"""Ensure P11-T28's post-baseline permission reference row in the local test schema.

This is a narrowly scoped reference-data preparation step. It does not run a
migration, reset the test schema, modify platform grants or connect to public.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from urllib.parse import urlsplit

from dotenv import load_dotenv

from scripts.setup_test_db import resolve_and_redirect, resolve_test_schema

LOCK_KEY = 1370093
EXPECTED_SCHEMA = "test_schema"
EXPECTED_DATABASE = "social_manager"


def _validate_target(test_url: str, schema: str) -> None:
    parts = urlsplit(test_url)
    if (
        parts.hostname not in {"localhost", "127.0.0.1"}
        or (parts.port or 5432) != 5432
        or parts.path.lstrip("/") != EXPECTED_DATABASE
        or schema != EXPECTED_SCHEMA
    ):
        raise RuntimeError("Refusing target; expected localhost:5432/social_manager/test_schema")


async def _prepare() -> bool:
    # Import only after resolve_and_redirect has bound application settings to
    # test_schema. Never invoke setup_test_db's reset/create/seed workflow here.
    from sqlalchemy import text

    logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.WARNING)
    from app.core.database import async_engine
    from app.core.tenant_context import tenant_scope
    from app.models import ModelType, Permission
    from app.types import ActionType

    created = False
    async with async_engine.connect() as lock_connection:
        locked = (
            await lock_connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY})
        ).scalar_one()
        if not locked:
            raise RuntimeError("Another verifier owns test_schema (advisory lock 1370093)")
        try:
            with tenant_scope(bypass=True):
                resource = await ModelType.objects.filter(resource_key="tenancy.workspace").first()
                if resource is None:
                    raise RuntimeError("Canonical tenancy.workspace model_type is missing; refusing to seed it")
                existing = await Permission.objects.get_by_codename("tenancy.workspace.delete")
                if existing is None:
                    await Permission.objects.create_permission(
                        "tenancy.workspace.delete",
                        "Delete workspace (test schema)",
                        ActionType.DELETE,
                        resource.id,
                    )
                    created = True
                elif existing.model_type_id != resource.id or existing.action_type != ActionType.DELETE:
                    raise RuntimeError("Existing tenancy.workspace.delete row conflicts with the access catalog")
        finally:
            await lock_connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": LOCK_KEY})
    await async_engine.dispose()
    return created


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate and print the intended target only")
    args = parser.parse_args()
    load_dotenv()
    schema = resolve_test_schema()
    # The helper validates separation from the configured working schema and
    # redirects environment variables before application imports.
    try:
        _, test_url, resolved_schema = resolve_and_redirect()
        _validate_target(test_url, resolved_schema)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.check:
        print("Target: localhost:5432/social_manager/test_schema; no changes made")
        return 0
    try:
        created = await _prepare()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    state = "created" if created else "already present"
    print(f"tenancy.workspace.delete reference permission: {state} (test_schema only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
