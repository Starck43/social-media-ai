"""Consolidate global platform types and materialize canonical API configuration.

Revision ID: 0120
Revises: 0119

Conflicting values/source identities abort the transaction without choosing a
user's configuration over another. The private rollback snapshot is archival,
never a runtime configuration source; it contains no newly resolved secrets.
"""

from __future__ import annotations

from copy import deepcopy

import sqlalchemy as sa
from alembic import op

from app.core.config import settings

revision = "0120"
down_revision = "0119"
branch_labels = None
depends_on = None

ARCHIVE = "_migration_0120"
CONSTRAINT = "uq_platforms_type"

# Public-link metadata for a type that has no catalog row yet. Kept here rather
# than imported from the application so a historical revision stays frozen.
BOOTSTRAP_ROWS = {
    "vk": ("ВКонтакте", "https://vk.com"),
    "telegram": ("Телеграм", "https://t.me"),
}


def _merge(left: dict, right: dict) -> dict:
    result = deepcopy(left)
    for key, value in right.items():
        if key not in result:
            result[key] = deepcopy(value)
        elif isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge(result[key], value)
        elif type(result[key]) is not type(value) or result[key] != value:
            # Never include a potentially sensitive key or value in diagnostics.
            raise ValueError("platform_config_conflict: reconcile duplicate Platform params before retrying 0120")
    return result


def _plan(rows: list[dict], sources: list[dict], defaults: dict) -> tuple[list[dict], dict[int, int]]:
    groups = {}
    for row in sorted(rows, key=lambda r: r["id"]):
        if not isinstance(row["params"], dict):
            raise ValueError("platform_params_must_be_object")
        groups.setdefault(row["platform_type"], []).append(row)
    merged, mapping = [], {}
    for kind, group in groups.items():
        canonical = deepcopy(group[0])
        params = {}
        for row in group:
            if row["base_url"].rstrip("/") != canonical["base_url"].rstrip("/"):
                raise ValueError("platform_public_url_conflict: reconcile duplicates before retrying 0120")
            params = _merge(params, row["params"])
            mapping[row["id"]] = canonical["id"]
        canonical["params"] = {**defaults.get(kind, {}), **params}
        # Preserve explicit global disablement rather than enable an inactive variant.
        # NULL is unset, not a disablement: the column is nullable and the model
        # treats an absent value as active, so only an explicit False disables.
        canonical["is_active"] = all(row["is_active"] is not False for row in group)
        merged.append(canonical)
    seen = set()
    for row in sources:
        identity = (row["tenant_id"], mapping[row["platform_id"]], row["external_id"])
        if identity in seen:
            raise ValueError("platform_source_identity_conflict: reconcile duplicate sources before retrying 0120")
        seen.add(identity)
    return merged, mapping


def _defaults() -> dict:
    """Bootstrap defaults for a migrated catalog, from the application's map.

    `VK_REDIRECT_URI` and `VK_OAUTH_BASE_URL` remain the only accepted legacy
    inputs: an existing deployment registered its callback there, and the
    migrated row has to keep it. `oauth_token_url` follows the authorize base.
    Every other endpoint comes from the bootstrap map alone.
    """
    from app.services.social.platform_config import bootstrap_params

    defaults = bootstrap_params()
    vk = defaults.setdefault("vk", {})
    authorize_base = (settings.VK_OAUTH_BASE_URL or "").strip().rstrip("/")
    if authorize_base:
        vk["oauth_base_url"] = authorize_base
        vk["oauth_token_url"] = f"{authorize_base}/oauth2/auth"
    redirect_uri = (settings.VK_REDIRECT_URI or "").strip()
    if redirect_uri:
        vk["redirect_uri"] = redirect_uri
    return defaults


def _bootstrap_missing(bind, platforms, merged: list[dict], defaults: dict) -> list[int]:
    """Create a row for each platform type the catalog has none of.

    Without this, a deployment whose catalog held only one type loses the other
    one entirely, and every consumer of that type fails closed after migration.
    """
    present = {row["platform_type"] for row in merged}
    created: list[int] = []
    next_id = (bind.execute(sa.select(sa.func.max(platforms.c.id))).scalar() or 0) + 1
    for kind, (name, base_url) in BOOTSTRAP_ROWS.items():
        if kind in present:
            continue
        merged.append(
            {
                "id": next_id,
                "name": name,
                "platform_type": kind,
                "base_url": base_url,
                "params": deepcopy(defaults.get(kind, {})),
                "is_active": True,
            }
        )
        created.append(next_id)
        next_id += 1
    return created


def _ensure_unique_constraint(bind, schema) -> None:
    """Create the type constraint unless the schema already carries it.

    A `Base.metadata.create_all` schema builds it from the model, so an
    unconditional create would abort the upgrade on that path.
    """
    names = {c["name"] for c in sa.inspect(bind).get_unique_constraints("platforms", schema=schema)}
    if CONSTRAINT not in names:
        op.create_unique_constraint(CONSTRAINT, "platforms", ["platform_type"], schema=schema)


def _tables():
    bind = op.get_bind()
    schema = settings.DB_SCHEMA
    metadata = sa.MetaData()
    platforms = sa.Table("platforms", metadata, schema=schema, autoload_with=bind)
    sources = sa.Table("sources", metadata, schema=schema, autoload_with=bind)
    return bind, schema, platforms, sources


def _lock(bind, schema):
    quoted = bind.dialect.identifier_preparer.quote_schema(schema)
    bind.execute(sa.text(f"LOCK TABLE {quoted}.platforms, {quoted}.sources IN ACCESS EXCLUSIVE MODE"))


def upgrade() -> None:
    bind, schema, platforms, sources = _tables()
    _lock(bind, schema)
    # Refuse unknown references instead of letting a cascade delete their data.
    for table in sa.inspect(bind).get_table_names(schema=schema):
        for fk in sa.inspect(bind).get_foreign_keys(table, schema=schema):
            if fk["referred_table"] == "platforms" and fk.get("referred_schema") in (None, schema):
                if table != "sources" or fk["constrained_columns"] != ["platform_id"]:
                    raise ValueError("platform_unknown_reference: review 0120 before migrating")
    rows = [dict(row) for row in bind.execute(sa.select(platforms).order_by(platforms.c.id)).mappings()]
    refs = [
        dict(row)
        for row in bind.execute(
            sa.select(sources.c.id, sources.c.platform_id, sources.c.tenant_id, sources.c.external_id).order_by(
                sources.c.id
            )
        ).mappings()
    ]
    defaults = _defaults()
    merged, mapping = _plan(rows, refs, defaults)
    created = _bootstrap_missing(bind, platforms, merged, defaults)
    for row in merged:
        params = row["params"]
        if not isinstance(params.get("api_base_url"), str) or not params["api_base_url"].strip():
            raise ValueError("platform_api_url_missing")
        if row["platform_type"] == "vk" and not params.get("api_version"):
            raise ValueError("platform_api_version_missing")
    if any(ARCHIVE in row["params"] for row in rows):
        raise ValueError("platform_reserved_archive_key: review 0120 before migrating")
    snapshot = {
        "original": deepcopy(rows),
        "merged": deepcopy(merged),
        "sources": refs,
        "created": list(created),
    }
    for row in merged:
        row["params"][ARCHIVE] = deepcopy(snapshot)
    for old_id, canonical_id in mapping.items():
        if old_id != canonical_id:
            bind.execute(sources.update().where(sources.c.platform_id == old_id).values(platform_id=canonical_id))
            bind.execute(platforms.delete().where(platforms.c.id == old_id))
    for row in merged:
        if row["id"] in created:
            bind.execute(
                platforms.insert().values(
                    id=row["id"],
                    name=row["name"],
                    platform_type=row["platform_type"],
                    base_url=row["base_url"],
                    params=row["params"],
                    is_active=row["is_active"],
                )
            )
        else:
            bind.execute(
                platforms.update()
                .where(platforms.c.id == row["id"])
                .values(params=row["params"], is_active=row["is_active"])
            )
    _ensure_unique_constraint(bind, schema)


def downgrade() -> None:
    bind, schema, platforms, sources = _tables()
    _lock(bind, schema)
    current = [dict(row) for row in bind.execute(sa.select(platforms).order_by(platforms.c.id)).mappings()]
    refs = [
        dict(row)
        for row in bind.execute(
            sa.select(sources.c.id, sources.c.platform_id, sources.c.tenant_id, sources.c.external_id).order_by(
                sources.c.id
            )
        ).mappings()
    ]
    if not current:
        raise ValueError("platform_rollback_snapshot_missing: restore a reviewed backup")
    snapshot = current[0]["params"].get(ARCHIVE)
    if not isinstance(snapshot, dict):
        raise ValueError("platform_rollback_snapshot_missing: restore a reviewed backup")
    normalized = []
    for row in current:
        params = deepcopy(row["params"])
        if params.pop(ARCHIVE, None) != snapshot:
            raise ValueError("platform_rollback_archive_changed: restore a reviewed backup")
        normalized.append({**row, "params": params})
    originals, original_refs = snapshot["original"], snapshot["sources"]
    _, mapping = _plan(originals, original_refs, {})
    expected_refs = [{**r, "platform_id": mapping[r["platform_id"]]} for r in original_refs]
    if normalized != snapshot["merged"] or refs != expected_refs:
        raise ValueError("platform_rollback_changed_data: reconcile post-0120 writes before downgrade")
    op.drop_constraint(CONSTRAINT, "platforms", schema=schema, type_="unique")
    # Rows this revision created have no pre-migration counterpart to restore.
    for created_id in snapshot.get("created") or []:
        bind.execute(platforms.delete().where(platforms.c.id == created_id))
    existing = {r["id"] for r in current}
    for row in originals:
        if row["id"] in existing:
            bind.execute(platforms.update().where(platforms.c.id == row["id"]).values(**row))
        else:
            bind.execute(platforms.insert().values(**row))
    for row in original_refs:
        bind.execute(sources.update().where(sources.c.id == row["id"]).values(platform_id=row["platform_id"]))
