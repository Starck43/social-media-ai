"""P21-T13 increment A: catalog custody, model metadata and plan grants (dormant).

Revision: p21t13_catalog_storage (descriptive placeholder; the number is assigned
at merge). `down_revision` is the live `dev` head `0155`.

Order matters and is all-or-nothing inside the caller's transaction:

1. create `llm_catalogs` and its two partial uniques;
2. add nullable `catalog_id` to providers/models and backfill it from custody:
   every existing row becomes a member of its owner's **personal** catalog, so a
   provider id, model id, owner, uid or payer consent never changes. A second run
   is idempotent;
3. create one personal catalog per distinct retained owner (explicit insert, no
   identity guess: a provider's owner and its models' owners must already agree,
   the composite FK proves it today);
4. add null-tolerant metadata columns, backfill only what the database can prove
   (the legacy CSV capability list and the exact copy of the stored prices) and
   leave `pricing_state` NULL — a legacy zero is **not** proven free;
5. guard preflight with an explicit refuse-and-report: duplicate
   `(catalog_id, provider.uid)` or `(provider_id, model_id)` rows block the new
   uniques instead of being deleted;
6. add the uniques, the composite provider FK, custody/uniqueness guards;
7. relax the retained owner columns, then drop only the constraints the new
   catalog authority replaces. Downgrade restores them in reverse.

No HTTP, dispatch, admin or feature flag is touched.
"""

import sqlalchemy as sa
from alembic import context, op

from app.core.config import settings

revision = "p21t13_catalog_storage"
down_revision = "0155"
branch_labels = None
depends_on = None

PROVIDER_COLUMNS = (sa.Column("managed_credential_ref", sa.String(100), nullable=True),)
MODEL_COLUMNS = (
    sa.Column("capabilities", sa.dialects.postgresql.JSONB, nullable=True),
    sa.Column("input_capabilities", sa.dialects.postgresql.JSONB, nullable=True),
    sa.Column("operation_capabilities", sa.dialects.postgresql.JSONB, nullable=True),
    sa.Column("input_price", sa.Numeric(18, 8), nullable=True),
    sa.Column("output_price", sa.Numeric(18, 8), nullable=True),
    sa.Column("pricing_state", sa.String(16), nullable=True),
    sa.Column("context_window_tokens", sa.Integer, nullable=True),
    sa.Column("max_output_tokens", sa.Integer, nullable=True),
)

#: ``(trigger, table, function)``; every trigger fires BEFORE INSERT OR UPDATE.
#: The names are literal SQL so the guard-pairing tool reads them without
#: following the module import.
GUARD_TRIGGERS = (
    ("guard_llm_catalog_custody", "llm_providers", "guard_llm_catalog_custody"),
    ("guard_llm_catalog_custody", "llm_models", "guard_llm_catalog_custody"),
    ("guard_llm_catalog_identity", "llm_catalogs", "guard_llm_catalog_identity"),
    ("guard_llm_provider_credential_custody", "llm_providers", "guard_llm_provider_credential_custody"),
    ("guard_llm_model_capability", "llm_models", "guard_llm_model_capability"),
    ("guard_plan_model_grant_platform", "plan_model_grants", "guard_plan_model_grant_platform"),
)

GUARD_DELETE_TRIGGERS = (
    ("guard_llm_managed_retirement", "llm_providers", "guard_llm_managed_retirement"),
    ("guard_llm_managed_retirement", "llm_models", "guard_llm_managed_retirement"),
)

#: ``(function, body)`` pairs in creation order; each body already carries the
#: build schema, so the guard-pairing tool reads the identifier this revision
#: creates.
GUARD_FUNCTIONS = (
    (
        "guard_llm_catalog_custody",
        (
            "DECLARE catalog_kind varchar(16); catalog_owner integer; parent_catalog integer; parent_owner integer; "
            "BEGIN IF TG_OP = 'UPDATE' AND NEW.catalog_id IS DISTINCT FROM OLD.catalog_id THEN "
            "RAISE EXCEPTION 'Catalog custody is immutable' USING ERRCODE = '23514'; END IF; "
            "IF TG_OP = 'INSERT' AND NEW.catalog_id IS NULL THEN "
            "IF NEW.owner_user_id IS NULL THEN RAISE EXCEPTION 'Explicit platform catalog required' "
            "USING ERRCODE = '23514'; END IF; "
            "IF TG_TABLE_NAME = 'llm_providers' THEN "
            "PERFORM id FROM {s}.users WHERE id = NEW.owner_user_id AND is_active IS TRUE FOR UPDATE; "
            "IF NOT FOUND THEN RAISE EXCEPTION 'Active personal owner required' USING ERRCODE = '23514'; END IF; "
            "INSERT INTO {s}.llm_catalogs (kind, owner_user_id) VALUES ('personal', NEW.owner_user_id) "
            "ON CONFLICT (owner_user_id) WHERE kind = 'personal' DO NOTHING; "
            "SELECT id INTO NEW.catalog_id FROM {s}.llm_catalogs "
            "WHERE kind = 'personal' AND owner_user_id = NEW.owner_user_id; "
            "ELSE SELECT catalog_id, owner_user_id INTO parent_catalog, parent_owner FROM {s}.llm_providers "
            "WHERE id = NEW.provider_id; IF NOT FOUND THEN RAISE EXCEPTION 'Provider does not exist' "
            "USING ERRCODE = '23503'; END IF; "
            "IF parent_owner IS DISTINCT FROM NEW.owner_user_id THEN RAISE EXCEPTION 'Personal provider owner mismatch' "
            "USING ERRCODE = '23514'; END IF; NEW.catalog_id := parent_catalog; END IF; END IF; SELECT "
            "kind, owner_user_id INTO catalog_kind, catalog_owner FROM "
            "{s}.llm_catalogs WHERE id = NEW.catalog_id; IF catalog_kind IS NULL THEN RAISE EXCEPTION "
            "'Catalog % does not exist', NEW.catalog_id USING ERRCODE = '23503'; END IF; "
            "IF catalog_kind = 'platform' THEN IF NEW.owner_user_id IS NOT NULL THEN RAISE EXCEPTION "
            "'Platform catalog rows must not retain an owner' USING ERRCODE = '23514'; END IF; "
            "ELSIF catalog_kind = 'personal' THEN IF NEW.owner_user_id IS DISTINCT FROM catalog_owner THEN "
            "RAISE EXCEPTION 'Retained owner must equal the personal catalog owner' USING ERRCODE = '23514'; "
            "END IF; ELSE RAISE EXCEPTION 'Unknown catalog kind' USING ERRCODE = '23514'; END IF; "
            "IF TG_TABLE_NAME = 'llm_models' THEN SELECT catalog_id INTO parent_catalog FROM "
            "{s}.llm_providers WHERE id = NEW.provider_id; IF parent_catalog IS DISTINCT FROM "
            "NEW.catalog_id THEN RAISE EXCEPTION 'Model catalog must equal its provider catalog' "
            "USING ERRCODE = '23514'; END IF; END IF; RETURN NEW; END; "
        ),
    ),
    (
        "guard_llm_catalog_identity",
        (
            "BEGIN IF TG_OP = 'UPDATE' AND (NEW.kind IS DISTINCT FROM OLD.kind OR "
            "NEW.owner_user_id IS DISTINCT FROM OLD.owner_user_id) THEN RAISE EXCEPTION "
            "'Catalog identity is immutable' USING ERRCODE = '23514'; END IF; RETURN NEW; END; "
        ),
    ),
    (
        "guard_llm_managed_retirement",
        (
            "BEGIN IF TG_OP = 'DELETE' THEN IF EXISTS (SELECT 1 FROM {s}.llm_catalogs "
            "WHERE id = OLD.catalog_id AND kind = 'platform') THEN RAISE EXCEPTION "
            "'Managed rows must be deactivated, not deleted' USING ERRCODE = '23514'; END IF; "
            "RETURN OLD; END IF; RETURN NEW; END; "
        ),
    ),
    (
        "guard_llm_provider_credential_custody",
        (
            "DECLARE catalog_kind varchar(16); BEGIN SELECT kind INTO catalog_kind FROM "
            "{s}.llm_catalogs WHERE id = NEW.catalog_id; IF catalog_kind = 'platform' THEN IF "
            "NEW.encrypted_api_key IS NOT NULL THEN RAISE EXCEPTION 'Platform providers must not store "
            "personal key material' USING ERRCODE = '23514'; END IF; ELSIF catalog_kind = 'personal' THEN "
            "IF NEW.managed_credential_ref IS NOT NULL THEN RAISE EXCEPTION 'Personal providers must not "
            "reference managed credentials' USING ERRCODE = '23514'; END IF; ELSE RAISE EXCEPTION "
            "'Unknown catalog kind' USING ERRCODE = '23514'; END IF; RETURN NEW; END; "
        ),
    ),
    (
        "guard_llm_model_capability",
        (
            "BEGIN IF (TG_OP = 'INSERT' AND NEW.capabilities IS NULL) OR "
            "(TG_OP = 'UPDATE' AND NEW.model_type IS DISTINCT FROM OLD.model_type "
            "AND NEW.capabilities IS NOT DISTINCT FROM OLD.capabilities) THEN "
            "IF NOT EXISTS (SELECT 1 FROM unnest(string_to_array(NEW.model_type, ',')) AS t "
            "WHERE btrim(t) <> '' AND btrim(t) NOT IN ('text','image','video','embedding','audio')) THEN "
            "SELECT jsonb_agg(DISTINCT btrim(t) ORDER BY btrim(t)) INTO NEW.capabilities "
            "FROM unnest(string_to_array(NEW.model_type, ',')) AS t WHERE btrim(t) <> ''; "
            "ELSIF NEW.owner_user_id IS NOT NULL THEN NEW.capabilities := NULL; ELSE "
            "RAISE EXCEPTION 'Unknown platform model capability' USING ERRCODE = '23514'; END IF; END IF; "
            "IF NEW.capabilities IS NOT NULL THEN IF jsonb_typeof(NEW.capabilities) <> 'array' THEN "
            "RAISE EXCEPTION 'Capabilities must be a JSON array' USING ERRCODE = '23514'; END IF; "
            "IF EXISTS (SELECT 1 FROM jsonb_array_elements_text(NEW.capabilities) AS e(value) WHERE "
            "e.value NOT IN ('text','image','video','embedding','audio')) THEN RAISE EXCEPTION "
            "'Unknown model capability' USING ERRCODE = '23514'; END IF; "
            "IF (SELECT count(*) FROM jsonb_array_elements_text(NEW.capabilities)) "
            "<> (SELECT count(DISTINCT value) FROM jsonb_array_elements_text(NEW.capabilities) AS e(value)) "
            "THEN RAISE EXCEPTION 'Duplicate model capability' USING ERRCODE = '23514'; END IF; "
            "IF (SELECT array_agg(value ORDER BY value) FROM jsonb_array_elements_text(NEW.capabilities) "
            "AS e(value)) IS DISTINCT FROM (SELECT array_agg(DISTINCT t ORDER BY t) FROM "
            "unnest(string_to_array(NEW.model_type, ',')) AS t WHERE btrim(t) <> '') THEN RAISE EXCEPTION "
            "'Legacy model_type must name exactly the capability set' USING ERRCODE = '23514'; END IF; "
            "END IF; RETURN NEW; END; "
        ),
    ),
    (
        "guard_plan_model_grant_platform",
        (
            "DECLARE catalog_kind varchar(16); catalog_active boolean; model_active boolean; "
            "provider_active boolean; supported boolean; BEGIN SELECT c.kind, c.is_active, m.is_active, p.is_active "
            "INTO catalog_kind, catalog_active, model_active, provider_active FROM "
            "{s}.llm_models m JOIN "
            "{s}.llm_providers p ON p.id = m.provider_id AND p.catalog_id = m.catalog_id JOIN "
            "{s}.llm_catalogs c ON c.id = m.catalog_id WHERE m.id = NEW.llm_model_id; "
            "IF catalog_kind IS DISTINCT FROM 'platform' THEN RAISE EXCEPTION 'Platform model required' "
            "USING ERRCODE = '23514'; END IF; "
            "IF TG_OP = 'UPDATE' AND NEW.plan = OLD.plan AND NEW.capability = OLD.capability "
            "AND NEW.llm_model_id = OLD.llm_model_id AND NEW.is_default IS FALSE "
            "AND (NEW.is_active IS FALSE OR NEW.is_active IS NOT DISTINCT FROM OLD.is_active) THEN "
            "RETURN NEW; END IF; "
            "IF catalog_active IS DISTINCT FROM TRUE "
            "OR model_active IS DISTINCT FROM TRUE OR provider_active IS DISTINCT FROM TRUE THEN RAISE EXCEPTION "
            "'Plan grants accept models of an active platform catalog only' USING ERRCODE = '23514'; END IF; "
            "SELECT coalesce(jsonb_typeof(m.capabilities) = 'array' AND m.capabilities ? NEW.capability, false) "
            "INTO supported FROM "
            "{s}.llm_models m WHERE m.id = NEW.llm_model_id; IF supported IS DISTINCT FROM TRUE THEN "
            "RAISE EXCEPTION 'Granted capability is not supported by the model' USING ERRCODE = '23514'; "
            "END IF; RETURN NEW; END; "
        ),
    ),
)


#: The build schema and its quoted form; both are module constants so the
#: guard-pairing tool and this revision render the same identifier.
SCHEMA = settings.DB_SCHEMA
s = '"' + SCHEMA + '"'

#: Places the frozen legacy CSV order; the model module and the guard restate it.
_CAPABILITY_VALUES = "('text', 'image', 'video', 'embedding', 'audio')"

#: The exact capability backfill the upgrade writes: the legacy CSV tokens that
#: are routing capabilities, deduplicated and ordered, `NULL` when empty.
#: `{a}` is the capabilities column and `{t}` the legacy `model_type` column.
BACKFILLED_CAPABILITIES = (
    "nullif((SELECT jsonb_agg(DISTINCT btrim(t) ORDER BY btrim(t)) "
    "FROM unnest(string_to_array({t}, ',')) AS t WHERE btrim(t) IN " + _CAPABILITY_VALUES + "), '[]'::jsonb)"
)


def retained_metadata(name):
    """Metadata whose survival proves an operator decision, not the backfill.

    `capabilities`, `input_price` and `output_price` are absent: `upgrade`
    writes them for every retained row from data the downgrade keeps
    (`model_type`, `input_cost_per_1k`, `output_cost_per_1k`), so a personal row
    whose added metadata is exactly that deterministic copy rolls back without
    losing anything. Any other value — a classification, a ceiling, a new input
    or operation capability, or a `capabilities` set that the legacy CSV cannot
    reproduce — is an explicit decision and refuses the rollback.
    """
    return (
        f"{name}.pricing_state IS NOT NULL OR {name}.context_window_tokens IS NOT NULL "
        f"OR {name}.max_output_tokens IS NOT NULL OR {name}.input_capabilities IS NOT NULL "
        f"OR {name}.operation_capabilities IS NOT NULL OR ({name}.capabilities IS NOT NULL "
        f"AND {name}.capabilities IS DISTINCT FROM "
        f"{BACKFILLED_CAPABILITIES.format(a=name + '.capabilities', t=name + '.model_type')})"
    )


def _count(connection, quoted, sql):
    return int(connection.scalar(sa.text(sql.format(s=quoted))) or 0)


def _refuse(connection, quoted, reason, sql):
    rows = _count(connection, quoted, sql)
    if rows:
        raise RuntimeError(f"Catalog storage refused: {reason} ({rows} row(s)); map them explicitly, never drop")


def upgrade():
    if context.is_offline_mode():
        raise RuntimeError("Catalog storage requires a live duplicate/backfill inventory")
    schema = settings.DB_SCHEMA
    connection = op.get_bind()

    connection.execute(sa.text(f"LOCK TABLE {s}.llm_providers, {s}.llm_models IN ACCESS EXCLUSIVE MODE"))

    op.create_table(
        "llm_catalogs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column(
            "owner_user_id",
            sa.Integer,
            sa.ForeignKey(f"{schema}.users.id", name="fk_llm_catalogs_owner_user", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "(kind = 'platform' AND owner_user_id IS NULL) OR (kind = 'personal' AND owner_user_id IS NOT NULL)",
            name="ck_llm_catalogs_kind_owner",
        ),
        schema=schema,
    )
    op.create_index("ix_llm_catalogs_owner_user_id", "llm_catalogs", ["owner_user_id"], schema=schema)
    op.create_index(
        "uq_llm_catalogs_one_platform",
        "llm_catalogs",
        ["kind"],
        unique=True,
        schema=schema,
        postgresql_where=sa.text("kind = 'platform'"),
    )
    op.create_index(
        "uq_llm_catalogs_one_personal_owner",
        "llm_catalogs",
        ["owner_user_id"],
        unique=True,
        schema=schema,
        postgresql_where=sa.text("kind = 'personal'"),
    )

    _refuse(
        connection,
        s,
        "model owner differs from its provider owner",
        f"SELECT count(*) FROM {s}.llm_models m JOIN {s}.llm_providers p ON p.id = m.provider_id "
        f"WHERE m.owner_user_id IS DISTINCT FROM p.owner_user_id",
    )
    # One personal catalog per distinct retained owner, deterministic by id, and
    # a second run finds every owner already mapped.
    connection.execute(
        sa.text(
            f"INSERT INTO {s}.llm_catalogs (kind, owner_user_id) "
            f"SELECT 'personal', o.owner_user_id FROM ("
            f"  SELECT owner_user_id FROM {s}.llm_providers WHERE owner_user_id IS NOT NULL "
            f"  UNION SELECT owner_user_id FROM {s}.llm_models WHERE owner_user_id IS NOT NULL"
            f") o WHERE NOT EXISTS ("
            f"  SELECT 1 FROM {s}.llm_catalogs c WHERE c.kind = 'personal' AND c.owner_user_id = o.owner_user_id"
            f") ORDER BY o.owner_user_id"
        )
    )
    op.add_column("llm_providers", sa.Column("catalog_id", sa.Integer, nullable=True), schema=schema)
    op.add_column("llm_models", sa.Column("catalog_id", sa.Integer, nullable=True), schema=schema)
    connection.execute(
        sa.text(
            f"UPDATE {s}.llm_providers SET catalog_id = c.id FROM {s}.llm_catalogs c "
            f"WHERE c.kind = 'personal' AND c.owner_user_id = llm_providers.owner_user_id "
            f"AND llm_providers.catalog_id IS NULL"
        )
    )
    connection.execute(
        sa.text(
            f"UPDATE {s}.llm_models SET catalog_id = c.id FROM {s}.llm_catalogs c "
            f"WHERE c.kind = 'personal' AND c.owner_user_id = llm_models.owner_user_id "
            f"AND llm_models.catalog_id IS NULL"
        )
    )
    unbackfilled = _count(
        connection,
        s,
        f"SELECT (SELECT count(*) FROM {s}.llm_providers WHERE catalog_id IS NULL) "
        f"+ (SELECT count(*) FROM {s}.llm_models WHERE catalog_id IS NULL)",
    )
    if unbackfilled:
        raise RuntimeError("Catalog storage refused: retained rows without custody; map them explicitly")
    # The composite FK below proves every model shares its provider's catalog.
    orphan = _count(
        connection,
        s,
        f"SELECT count(*) FROM {s}.llm_models m LEFT JOIN {s}.llm_providers p "
        f"ON p.id = m.provider_id AND p.catalog_id = m.catalog_id WHERE p.id IS NULL",
    )
    if orphan:
        raise RuntimeError("Catalog storage refused: models outside their provider catalog before the FK")

    for column in PROVIDER_COLUMNS:
        op.add_column("llm_providers", column, schema=schema)
    for column in MODEL_COLUMNS:
        op.add_column("llm_models", column, schema=schema)

    # Provable backfills only: the legacy CSV capability list, and an exact copy
    # of the stored price in the unit the row already declares. `decision` is not
    # a routing capability (it is the retired permission `model_type` registry),
    # so it is not stored in JSONB, and an empty set is NULL rather than `[]`.
    # A CSV token outside the vocabulary would be silently dropped by the copy,
    # so it refuses first — the guard trigger is created later in this revision.
    _refuse(
        connection,
        s,
        "model_type names a capability outside the stored vocabulary",
        f"SELECT count(*) FROM {s}.llm_models m CROSS JOIN LATERAL "
        f"unnest(string_to_array(m.model_type, ',')) AS t WHERE btrim(t) <> '' AND btrim(t) NOT IN "
        "('text', 'image', 'video', 'embedding', 'audio', 'decision')",
    )
    connection.execute(
        sa.text(
            f"UPDATE {s}.llm_models SET capabilities = "
            f"{BACKFILLED_CAPABILITIES.format(a='capabilities', t='model_type')} "
            f"WHERE capabilities IS NULL AND NOT EXISTS (SELECT 1 FROM "
            f"unnest(string_to_array(llm_models.model_type, ',')) AS t WHERE btrim(t) = 'decision')"
        )
    )
    connection.execute(
        sa.text(
            f"UPDATE {s}.llm_models SET input_price = input_cost_per_1k, output_price = output_cost_per_1k "
            f"WHERE input_price IS NULL AND output_price IS NULL"
        )
    )

    # Explicit preflight: a collision blocks the unique index, nothing is deleted.
    _refuse(
        connection,
        s,
        "duplicate (catalog_id, uid) providers",
        f"SELECT count(*) FROM (SELECT catalog_id, uid FROM {s}.llm_providers "
        f"GROUP BY catalog_id, uid HAVING count(*) > 1) d",
    )
    _refuse(
        connection,
        s,
        "duplicate (provider_id, model_id) models",
        f"SELECT count(*) FROM (SELECT provider_id, model_id FROM {s}.llm_models "
        f"GROUP BY provider_id, model_id HAVING count(*) > 1) d",
    )

    op.alter_column("llm_providers", "catalog_id", existing_type=sa.Integer, nullable=False, schema=schema)
    op.alter_column("llm_models", "catalog_id", existing_type=sa.Integer, nullable=False, schema=schema)
    op.create_foreign_key(
        "fk_llm_provider_catalog",
        "llm_providers",
        "llm_catalogs",
        ["catalog_id"],
        ["id"],
        source_schema=schema,
        referent_schema=schema,
        ondelete="RESTRICT",
    )
    op.create_unique_constraint("uq_llm_provider_id_catalog", "llm_providers", ["id", "catalog_id"], schema=schema)
    op.create_index("ix_llm_provider_catalog_id", "llm_providers", ["catalog_id"], schema=schema)
    op.create_index("ix_llm_model_catalog_id", "llm_models", ["catalog_id"], schema=schema)
    op.create_foreign_key(
        "fk_llm_model_provider_catalog",
        "llm_models",
        "llm_providers",
        ["provider_id", "catalog_id"],
        ["id", "catalog_id"],
        source_schema=schema,
        referent_schema=schema,
        ondelete="CASCADE",
    )
    op.create_unique_constraint("uq_llm_model_provider_model", "llm_models", ["provider_id", "model_id"], schema=schema)

    op.create_check_constraint(
        "ck_llm_provider_managed_ref_ctrl",
        "llm_providers",
        "managed_credential_ref IS NULL OR managed_credential_ref ~ '^[a-z][a-z0-9_-]{0,99}$'",
        schema=schema,
    )
    for name, condition in (
        (
            "ck_llm_models_prices",
            "(input_price IS NULL OR input_price >= 0) AND (output_price IS NULL OR output_price >= 0)",
        ),
        ("ck_llm_models_pricing_state", "pricing_state IS NULL OR pricing_state IN ('free', 'priced', 'undefined')"),
        (
            "ck_llm_models_free_price_zero",
            "(pricing_state IS DISTINCT FROM 'free') OR ((input_price IS NOT NULL AND output_price IS NOT NULL) "
            "AND (input_price = 0 AND output_price = 0))",
        ),
        (
            "ck_llm_models_priced_positive",
            "(pricing_state IS DISTINCT FROM 'priced') OR ((input_price IS NOT NULL AND output_price IS NOT NULL) "
            "AND (input_price > 0 OR output_price > 0))",
        ),
        ("ck_llm_models_context_window", "context_window_tokens IS NULL OR context_window_tokens > 0"),
        ("ck_llm_models_max_output_tokens", "max_output_tokens IS NULL OR max_output_tokens > 0"),
        ("ck_llm_models_capability_shape", "capabilities IS NULL OR jsonb_typeof(capabilities) = 'array'"),
        (
            "ck_llm_models_input_capability_shape",
            "input_capabilities IS NULL OR jsonb_typeof(input_capabilities) = 'array'",
        ),
        (
            "ck_llm_models_operation_capability_shape",
            "operation_capabilities IS NULL OR jsonb_typeof(operation_capabilities) = 'array'",
        ),
    ):
        op.create_check_constraint(name, "llm_models", condition, schema=schema)

    op.create_table(
        "plan_model_grants",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("plan", sa.String(20), nullable=False),
        sa.Column("capability", sa.String(64), nullable=False),
        sa.Column(
            "llm_model_id",
            sa.Integer,
            sa.ForeignKey(f"{schema}.llm_models.id", name="fk_plan_model_grant_model", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("priority", sa.Integer, nullable=False, server_default="100"),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("plan IN ('starter', 'pro', 'business')", name="ck_plan_model_grant_plan"),
        sa.CheckConstraint(
            "capability IN ('text', 'image', 'video', 'embedding', 'audio')",
            name="ck_plan_model_grant_capability",
        ),
        sa.CheckConstraint("priority >= 0", name="ck_plan_model_grant_priority"),
        schema=schema,
    )
    op.create_index("ix_plan_model_grants_llm_model_id", "plan_model_grants", ["llm_model_id"], schema=schema)
    op.create_index(
        "uq_plan_model_grant_membership",
        "plan_model_grants",
        ["plan", "capability", "llm_model_id"],
        unique=True,
        schema=schema,
    )
    op.create_index(
        "uq_plan_model_grant_active_default",
        "plan_model_grants",
        ["plan", "capability"],
        unique=True,
        schema=schema,
        postgresql_where=sa.text("is_active AND is_default"),
    )
    op.create_index(
        "ix_plan_model_grant_order",
        "plan_model_grants",
        ["plan", "capability", "is_active", "priority", "llm_model_id"],
        schema=schema,
    )

    # Guard DDL restates the module's guard objects for this build schema; the
    # guard-pairing tool reads these literal statements and compares them with
    # scripts/schema_guards.py, so the bootstrap and the revision cannot drift.
    for guard_name, guard_body in GUARD_FUNCTIONS:
        guard_function = guard_body.format(s=s)
        op.execute(
            sa.text(f"CREATE FUNCTION {s}.{guard_name}() RETURNS trigger LANGUAGE plpgsql AS $$ {guard_function} $$")
        )
    for guard_trigger, guard_table, guard_function in GUARD_TRIGGERS:
        op.execute(
            sa.text(
                f"CREATE TRIGGER {guard_trigger} BEFORE INSERT OR UPDATE ON {s}.{guard_table} "
                f"FOR EACH ROW EXECUTE FUNCTION {s}.{guard_function}()"
            )
        )

    for guard_trigger, guard_table, guard_function in GUARD_DELETE_TRIGGERS:
        op.execute(
            sa.text(
                f"CREATE TRIGGER {guard_trigger} BEFORE DELETE ON {s}.{guard_table} "
                f"FOR EACH ROW EXECUTE FUNCTION {s}.{guard_function}()"
            )
        )

    # Staged retirement: the new guards exist and every retained owner is a
    # projection of its catalog, then the owner-only constraints yield. The
    # composite model FK goes first (it owns the old provider unique and pins the
    # model owner to the provider owner); the owner uniques go once the catalog
    # uniques they duplicate exist.
    # The old composite FK pins the model owner to the provider owner, so it must
    # go before the columns can be widened; its referenced unique follows once
    # nothing points at it.
    op.drop_constraint("fk_llm_model_provider_owner", "llm_models", schema=schema, type_="foreignkey")
    op.create_unique_constraint("uq_llm_provider_catalog_uid", "llm_providers", ["catalog_id", "uid"], schema=schema)
    op.alter_column("llm_providers", "owner_user_id", existing_type=sa.Integer, nullable=True, schema=schema)
    op.alter_column("llm_models", "owner_user_id", existing_type=sa.Integer, nullable=True, schema=schema)
    op.drop_constraint("uq_llm_provider_owner_uid", "llm_providers", schema=schema, type_="unique")
    op.drop_constraint("uq_llm_provider_id_owner", "llm_providers", schema=schema, type_="unique")


def downgrade():
    if context.is_offline_mode():
        raise RuntimeError("Cannot prove an empty catalog-storage rollback offline")
    schema = settings.DB_SCHEMA
    connection = op.get_bind()
    connection.execute(
        sa.text(f"LOCK TABLE {s}.llm_providers, {s}.llm_models, {s}.llm_catalogs " f"IN ACCESS EXCLUSIVE MODE")
    )
    # Refusals run before any guard is dropped, while every guard still proves
    # the state; they are ordered so the *first* failure names the real loss.
    for reason, sql in (
        ("platform catalogs must be preserved", f"SELECT count(*) FROM {s}.llm_catalogs WHERE kind = 'platform'"),
        ("plan grants must be preserved", f"SELECT count(*) FROM {s}.plan_model_grants"),
        (
            "platform providers must be preserved",
            f"SELECT count(*) FROM {s}.llm_providers p JOIN {s}.llm_catalogs c ON c.id = p.catalog_id "
            f"WHERE c.kind = 'platform'",
        ),
        (
            "retained owners are not recoverable for owner-less rows",
            f"SELECT count(*) FROM {s}.llm_providers WHERE owner_user_id IS NULL",
        ),
        (
            "retained owners are not recoverable for owner-less rows",
            f"SELECT count(*) FROM {s}.llm_models WHERE owner_user_id IS NULL",
        ),
        (
            "explicit pricing or metadata decisions must be preserved",
            f"SELECT count(*) FROM {s}.llm_models m WHERE {retained_metadata('m')}",
        ),
    ):
        _refuse(connection, s, reason, sql)

    # Owner constraints are restored before the model FK that references them
    # (`fk_llm_model_provider_owner` needs `uq_llm_provider_id_owner` to exist),
    # after the role the new custody objects filled is released. Upstream order:
    # drop the new uniques/FK, drop the constraining indexes, widen the columns,
    # then rebuild the owner uniques and the composite FK.
    op.drop_constraint("uq_llm_provider_catalog_uid", "llm_providers", schema=schema, type_="unique")
    op.drop_constraint("uq_llm_model_provider_model", "llm_models", schema=schema, type_="unique")
    op.drop_constraint("fk_llm_model_provider_catalog", "llm_models", schema=schema, type_="foreignkey")
    op.drop_constraint("uq_llm_provider_id_catalog", "llm_providers", schema=schema, type_="unique")
    op.drop_index("ix_llm_provider_catalog_id", table_name="llm_providers", schema=schema)
    op.alter_column("llm_models", "owner_user_id", existing_type=sa.Integer, nullable=False, schema=schema)
    op.alter_column("llm_providers", "owner_user_id", existing_type=sa.Integer, nullable=False, schema=schema)
    # Compatibility owner indexes were never dropped by upgrade.
    op.drop_index("ix_llm_model_catalog_id", table_name="llm_models", schema=schema)
    op.create_unique_constraint("uq_llm_provider_id_owner", "llm_providers", ["id", "owner_user_id"], schema=schema)
    op.create_unique_constraint("uq_llm_provider_owner_uid", "llm_providers", ["owner_user_id", "uid"], schema=schema)
    op.create_foreign_key(
        "fk_llm_model_provider_owner",
        "llm_models",
        "llm_providers",
        ["provider_id", "owner_user_id"],
        ["id", "owner_user_id"],
        source_schema=schema,
        referent_schema=schema,
        ondelete="CASCADE",
    )

    for guard_trigger, guard_table, _guard_function in (*GUARD_TRIGGERS, *GUARD_DELETE_TRIGGERS):
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS {guard_trigger} ON {s}.{guard_table}"))
    for guard_name, _guard_body in GUARD_FUNCTIONS:
        op.execute(sa.text(f"DROP FUNCTION IF EXISTS {s}.{guard_name}()"))

    op.drop_index("ix_plan_model_grant_order", table_name="plan_model_grants", schema=schema)
    op.drop_index("uq_plan_model_grant_active_default", table_name="plan_model_grants", schema=schema)
    op.drop_index("uq_plan_model_grant_membership", table_name="plan_model_grants", schema=schema)
    op.drop_index("ix_plan_model_grants_llm_model_id", table_name="plan_model_grants", schema=schema)
    op.drop_table("plan_model_grants", schema=schema)

    for name in (
        "ck_llm_models_capability_shape",
        "ck_llm_models_input_capability_shape",
        "ck_llm_models_operation_capability_shape",
        "ck_llm_models_max_output_tokens",
        "ck_llm_models_context_window",
        "ck_llm_models_priced_positive",
        "ck_llm_models_free_price_zero",
        "ck_llm_models_pricing_state",
        "ck_llm_models_prices",
    ):
        op.drop_constraint(name, "llm_models", schema=schema, type_="check")
    op.drop_constraint("ck_llm_provider_managed_ref_ctrl", "llm_providers", schema=schema, type_="check")

    # The catalog custody objects were already released with the owner restore.
    op.drop_constraint("fk_llm_provider_catalog", "llm_providers", schema=schema, type_="foreignkey")

    for column in reversed(MODEL_COLUMNS):
        op.drop_column("llm_models", column.name, schema=schema)
    for column in reversed(PROVIDER_COLUMNS):
        op.drop_column("llm_providers", column.name, schema=schema)
    # `catalog_id` precedes its indexes and the catalog table itself.
    op.drop_column("llm_models", "catalog_id", schema=schema)
    op.drop_column("llm_providers", "catalog_id", schema=schema)

    op.drop_index("uq_llm_catalogs_one_personal_owner", table_name="llm_catalogs", schema=schema)
    op.drop_index("uq_llm_catalogs_one_platform", table_name="llm_catalogs", schema=schema)
    op.drop_index("ix_llm_catalogs_owner_user_id", table_name="llm_catalogs", schema=schema)
    op.drop_table("llm_catalogs", schema=schema)
