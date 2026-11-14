"""P21-T13 increment A: owner-only plan/model grant management (source-only).

Doubles answer ``scalar``/``execute``/``scalars``; assertions read the returned
row or the compiled PostgreSQL statement. No database, network or credential.

Read: tests/AGENTS.md, app/services/llm_catalog/catalog_admin.py,
app/core/permissions.py, app/models/llm/{plan_model_grant,llm_catalog,
llm_provider,llm_model}.py, app/models/identity/user.py, tests/conftest.py.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import Update, select
from sqlalchemy.dialects import postgresql

from app.core.permissions import permission_scope
from app.models import LLMCatalog, LLMModel, LLMProvider, PlanModelGrant
from app.services.llm_catalog import catalog_admin

__source_only__ = True

PG = postgresql.dialect()
OWNER_ID = 41

# The root conftest's operator grant admits any posted ID at the request layer,
# so the service must still refuse these.
DENIED_POSTED_IDS = [
    pytest.param(0, id="zero"),
    pytest.param(OWNER_ID + 1, id="other-user"),
    pytest.param(str(OWNER_ID), id="stringified"),
    pytest.param(True, id="bool-true"),
    pytest.param(4.1, id="float"),
    pytest.param(None, id="none"),
]


def owner(*, user_id=OWNER_ID, is_active=True):
    return SimpleNamespace(id=user_id, is_active=is_active, role=SimpleNamespace(codename="superuser"))


def fresh_owner(*, is_active=True, is_superuser=True):
    return SimpleNamespace(id=OWNER_ID, is_active=is_active, is_superuser=is_superuser)


def platform_catalog(*, is_active=True):
    return SimpleNamespace(id=11, kind="platform", owner_user_id=None, is_active=is_active)


def model_mapping(capabilities=("text",), model_id=7, name="Managed Text", provider_id=5, provider_name="Fleet"):
    return {
        "id": model_id,
        "name": name,
        "model_id": "vendor/model",
        "capabilities": list(capabilities),
        "provider_id": provider_id,
        "provider_name": provider_name,
    }


def granted(*, plan="pro", capability="text", model_id=7, grant_id=3, **fields):
    fields.update(id=grant_id, plan=plan, capability=capability, llm_model_id=model_id)
    fields.setdefault("priority", 100)
    fields.setdefault("is_active", True)
    fields.setdefault("is_default", False)
    return SimpleNamespace(**fields)


def stmt_text(stmt) -> str:
    return str(stmt.compile(dialect=PG))


def stmts_of(session, kind) -> list:
    return [stmt for stmt in session.executions if isinstance(stmt, kind)]


class _MappingRows:
    def __init__(self, rows):
        self._rows = [dict(row) for row in rows]

    def one_or_none(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class AutoflushContext:
    def __init__(self):
        self.entered = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    """Route the three read shapes; record every statement in send order."""

    def __init__(self, *, scalars=()):
        self._scalar_answer = None
        self._mappings_answer = []
        self._scalars_answer = list(scalars)
        self.scalar_count = 0
        self.executions = []
        self.flushes = 0
        self.commits = 0
        self.added = []
        self.order = []
        self.autoflush_contexts = []

    async def scalar(self, stmt):
        self.scalar_count += 1
        return self._scalar_answer

    async def execute(self, stmt):
        self.executions.append(stmt)
        self.order.append("execute")
        return SimpleNamespace(mappings=lambda: _MappingRows(self._mappings_answer))

    async def scalars(self, stmt):
        self.executions.append(stmt)
        self.order.append("scalars")
        return SimpleNamespace(all=lambda: list(self._scalars_answer))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushes += 1
        self.order.append("flush")

    async def commit(self):
        self.commits += 1

    @property
    def no_autoflush(self):
        context = AutoflushContext()
        self.autoflush_contexts.append(context)
        return context


class RoutedSession(FakeSession):
    """``scalar`` answers by the top-level entity the statement selects."""

    def __init__(self, routes, *, scalars=()):
        super().__init__(scalars=scalars)
        self.routes = routes
        self.statements = []

    async def scalar(self, stmt):
        self.scalar_count += 1
        self.statements.append(stmt)
        entity = stmt.column_descriptions[0]["entity"]
        return self.routes.get(entity.__name__)


def grant_session(plan="pro", capability="text", model_id=7, grant_id=3, *, scalars=(), **fields):
    return RoutedSession(
        {
            "User": fresh_owner(),
            "LLMCatalog": platform_catalog(),
            "PlanModelGrant": granted(plan=plan, capability=capability, model_id=model_id, grant_id=grant_id, **fields),
        },
        scalars=scalars,
    )


def custody_session(model_row=None, *, catalog=None, grant=None):
    """Owner read, catalog read, model custody SELECT; no existing grant by default."""
    session = RoutedSession({"User": fresh_owner(), "LLMCatalog": catalog or platform_catalog()})
    session.routes["PlanModelGrant"] = grant
    session._mappings_answer = [] if model_row is None else [model_row]
    return session


@pytest.fixture
def as_owner():
    with permission_scope(owner()):
        yield


# ------------------------------------------------------- bound actor required


async def test_unbound_actor_cannot_manage_catalog():
    with permission_scope(None):
        session = grant_session()
        with pytest.raises(PermissionError, match="Platform owner required"):
            await catalog_admin.require_platform_owner(session, actor_user_id=OWNER_ID)
    assert session.scalar_count == 0, "a missing bound actor must not even read storage"


@pytest.mark.parametrize("posted", DENIED_POSTED_IDS)
async def test_posted_other_identity_is_denied(as_owner, posted):
    session = grant_session()

    with pytest.raises(PermissionError, match="Platform owner required"):
        await catalog_admin.require_platform_owner(session, actor_user_id=posted)
    assert session.scalar_count == 0


async def test_configured_inactive_actor_is_denied_before_storage():
    with permission_scope(owner(is_active=False)):
        session = grant_session()
        with pytest.raises(PermissionError, match="Platform owner required"):
            await catalog_admin.require_platform_owner(session, actor_user_id=OWNER_ID)
    assert session.scalar_count == 0


# ----------------------------------------------------------------- fresh check


async def test_fresh_owner_is_reloaded(as_owner):
    session = grant_session()

    fresh = await catalog_admin.require_platform_owner(session, actor_user_id=OWNER_ID)

    assert fresh.id == OWNER_ID
    assert session.scalar_count == 1


@pytest.mark.parametrize(
    "fresh",
    [
        pytest.param(None, id="deleted"),
        pytest.param(fresh_owner(is_active=False), id="inactive"),
        pytest.param(fresh_owner(is_superuser=False), id="not-superuser"),
    ],
)
async def test_stale_superuser_snapshot_is_refused(as_owner, fresh):
    session = FakeSession()
    session._scalar_answer = fresh

    with pytest.raises(PermissionError, match="Platform owner required"):
        await catalog_admin.require_platform_owner(session, actor_user_id=OWNER_ID)
    assert session.flushes == 0 and session.commits == 0


async def test_owner_lookup_locks_the_user_row_and_reloads_it(as_owner):
    session = grant_session()

    await catalog_admin.require_platform_owner(session, actor_user_id=OWNER_ID)

    [stmt] = session.statements
    sql = stmt_text(stmt)
    assert "users.id =" in sql
    assert "FOR UPDATE" in sql.upper()
    assert stmt.get_execution_options().get("populate_existing") is True


# ------------------------------------------------------------------ validation


@pytest.mark.parametrize("plan", ["", "free", "PRO", " enterprise", None, 3, ["pro"]])
async def test_save_grant_refuses_unknown_plan(as_owner, plan):
    session = grant_session()

    with pytest.raises(ValueError, match="Unknown service plan"):
        await catalog_admin.save_grant(session, actor_user_id=OWNER_ID, plan=plan, capability="text", model_id=7)
    assert session.scalar_count == 1, "only the owner read happens before validation"


@pytest.mark.parametrize("capability", ["", "chat", "Text", "textual", None, 5])
async def test_save_grant_refuses_unknown_capability(as_owner, capability):
    session = grant_session()

    with pytest.raises(ValueError, match="Unknown model capability"):
        await catalog_admin.save_grant(session, actor_user_id=OWNER_ID, plan="pro", capability=capability, model_id=7)
    assert session.flushes == 0


@pytest.mark.parametrize("priority", [-1, 2**31, 1.5, "10", None, True])
async def test_save_grant_refuses_bad_priority(as_owner, priority):
    session = grant_session()

    with pytest.raises(ValueError, match="nonnegative integer"):
        await catalog_admin.save_grant(
            session, actor_user_id=OWNER_ID, plan="pro", capability="text", model_id=7, priority=priority
        )
    assert session.flushes == 0


@pytest.mark.parametrize(
    "flags",
    [
        pytest.param({"is_active": 1}, id="active-int"),
        pytest.param({"is_active": "true"}, id="active-string"),
        pytest.param({"is_default": 0}, id="default-int"),
        pytest.param({"is_default": None}, id="default-none"),
    ],
)
async def test_save_grant_refuses_non_boolean_flags(as_owner, flags):
    session = grant_session()

    with pytest.raises(ValueError, match="boolean"):
        await catalog_admin.save_grant(
            session, actor_user_id=OWNER_ID, plan="pro", capability="text", model_id=7, **flags
        )
    assert session.flushes == 0


async def test_inactive_default_is_refused(as_owner):
    session = grant_session()

    with pytest.raises(ValueError, match="inactive grant cannot be the default"):
        await catalog_admin.save_grant(
            session,
            actor_user_id=OWNER_ID,
            plan="pro",
            capability="text",
            model_id=7,
            is_active=False,
            is_default=True,
        )
    assert session.flushes == 0


@pytest.mark.parametrize("model_id", [0, -3, 2**31, 7.0, "7", True, None])
async def test_save_grant_refuses_bad_model_id(as_owner, model_id):
    session = grant_session()

    with pytest.raises(ValueError, match="positive numeric record ID"):
        await catalog_admin.save_grant(
            session, actor_user_id=OWNER_ID, plan="pro", capability="text", model_id=model_id
        )
    assert session.flushes == 0


@pytest.mark.parametrize("grant_id", [0, -1, "3", 3.0, True])
async def test_save_grant_refuses_bad_grant_id(as_owner, grant_id):
    session = grant_session()

    with pytest.raises(ValueError, match="positive numeric record ID"):
        await catalog_admin.save_grant(
            session, actor_user_id=OWNER_ID, plan="pro", capability="text", model_id=7, grant_id=grant_id
        )
    assert session.flushes == 0


# ----------------------------------------------------- active platform custody


async def test_save_grant_requires_active_platform_catalog(as_owner):
    session = custody_session(model_mapping(), catalog=platform_catalog(is_active=False))

    with pytest.raises(ValueError, match="Active platform catalog required"):
        await catalog_admin.save_grant(session, actor_user_id=OWNER_ID, plan="pro", capability="text", model_id=7)
    assert session.added == []


async def test_save_grant_requires_platform_model_row(as_owner):
    session = custody_session(None)

    with pytest.raises(ValueError, match="Active platform model"):
        await catalog_admin.save_grant(session, actor_user_id=OWNER_ID, plan="pro", capability="text", model_id=7)
    assert session.added == []


async def test_model_without_the_capability_is_refused(as_owner):
    session = custody_session(model_mapping(capabilities=("image", "video")))

    with pytest.raises(ValueError, match="Active platform model"):
        await catalog_admin.recheck_platform_model(session, model_id=7, capability="text", catalog_id=11)


async def test_model_with_null_capabilities_is_refused(as_owner):
    session = custody_session(model_mapping(capabilities=()))

    with pytest.raises(ValueError, match="Active platform model"):
        await catalog_admin.recheck_platform_model(session, model_id=7, capability="text", catalog_id=11)


async def test_active_model_returns_only_the_metadata_projection(as_owner):
    session = custody_session(model_mapping(capabilities=("text", "embedding")))

    row = await catalog_admin.recheck_platform_model(session, model_id=7, capability="embedding", catalog_id=11)

    assert row == model_mapping(capabilities=("text", "embedding"))
    sql = stmt_text(session.executions[0])
    assert "llm_catalogs" in sql and "llm_providers" in sql and "llm_models" in sql
    assert "owner_user_id IS NULL" in sql
    assert "is_active IS true" in sql
    assert "FOR UPDATE" not in sql.upper(), "no lock unless the management path asks for it"


async def test_recheck_lock_covers_provider_and_model_tables(as_owner):
    session = custody_session(model_mapping())

    await catalog_admin.recheck_platform_model(session, model_id=7, capability="text", catalog_id=11, lock=True)

    assert "FOR UPDATE OF llm_providers, llm_models" in stmt_text(session.executions[0])


async def test_save_grant_never_leaks_personal_catalog_model(as_owner):
    session = custody_session(None)

    with pytest.raises(ValueError, match="Active platform model"):
        await catalog_admin.save_grant(session, actor_user_id=OWNER_ID, plan="starter", capability="text", model_id=9)
    sql = stmt_text(session.executions[0])
    assert "llm_models.owner_user_id IS NULL" in sql
    assert "llm_providers.owner_user_id IS NULL" in sql
    assert "llm_catalogs.kind =" in sql, "only the platform catalog is ever joined"
    assert session.flushes == 0


# --------------------------------------------------------- save grant / defaults


async def test_new_grant_is_added_then_flushed(as_owner):
    session = custody_session(model_mapping())

    row = await catalog_admin.save_grant(
        session, actor_user_id=OWNER_ID, plan="business", capability="text", model_id=7, priority=5
    )

    assert isinstance(row, PlanModelGrant)
    assert (row.plan, row.capability, row.llm_model_id) == ("business", "text", 7)
    assert (row.priority, row.is_active, row.is_default) == (5, True, False)
    assert session.added == [row]
    assert session.flushes == 1
    assert session.commits == 0


async def test_default_update_is_group_scoped_and_executed_before_flush(as_owner):
    session = custody_session(model_mapping(capabilities=("text", "image")))

    row = await catalog_admin.save_grant(
        session,
        actor_user_id=OWNER_ID,
        plan="pro",
        capability="image",
        model_id=7,
        priority=1,
        is_default=True,
    )

    assert row.is_default is True
    assert session.flushes == 1
    # The model SELECT is recorded too, so isolate the group clear.
    updates = stmts_of(session, Update)
    assert len(updates) == 1, "exactly one sibling clear before the pending default write"
    sql = stmt_text(updates[0])
    assert "UPDATE" in sql and "plan_model_grants" in sql
    assert "is_active IS true" in sql
    assert "is_default IS true" in sql
    # Group floor and new value travel as bound parameters.
    params = updates[0].compile(dialect=PG).params
    assert params["plan_1"] == "pro"
    assert params["capability_1"] == "image"
    assert params["is_default"] is False
    # A brand-new row has no id yet, so it cannot and must not exclude itself.
    assert "id !=" not in sql
    assert "plan_model_grants.id" not in sql
    assert session.order.index("execute") < session.order.index("flush")


async def test_edit_clears_defaults_excluding_itself_before_flush(as_owner):
    session = custody_session(model_mapping(["text"]), grant=granted(grant_id=3, is_default=True))

    row = await catalog_admin.save_grant(
        session,
        actor_user_id=OWNER_ID,
        plan="pro",
        capability="text",
        model_id=7,
        priority=42,
        is_default=True,
        grant_id=3,
    )

    assert row is session.routes["PlanModelGrant"]
    assert (row.priority, row.is_default, row.is_active) == (42, True, True)
    assert session.added == [], "editing a row must not insert another"
    [update] = stmts_of(session, Update)
    assert "plan_model_grants.id !=" in stmt_text(update), "the edited row keeps its own default"
    assert update.compile(dialect=PG).params["id_1"] == 3
    assert session.order.index("execute") < session.order.index("flush")


async def test_default_false_on_edit_makes_no_group_update(as_owner):
    session = custody_session(model_mapping(["text"]), grant=granted(grant_id=3, is_default=True))

    row = await catalog_admin.save_grant(
        session, actor_user_id=OWNER_ID, plan="pro", capability="text", model_id=7, grant_id=3, is_default=False
    )

    assert row.is_default is False
    assert stmts_of(session, Update) == [], "no default is set, so no sibling default may be cleared"
    assert session.flushes == 1


async def test_update_skips_autoflush(as_owner):
    session = custody_session(model_mapping(["text"]), grant=granted(grant_id=3))

    await catalog_admin.save_grant(
        session, actor_user_id=OWNER_ID, plan="pro", capability="text", model_id=7, grant_id=3, is_default=True
    )

    assert stmts_of(session, Update), "the default path must clear siblings"
    assert len(session.autoflush_contexts) == 1, "exactly one no_autoflush block per clear"
    assert session.autoflush_contexts[0].entered, "a fresh context manager was requested, not a bare function"


async def test_caller_owned_transaction_is_never_committed(as_owner):
    session = custody_session(model_mapping())

    await catalog_admin.save_grant(
        session,
        actor_user_id=OWNER_ID,
        plan="pro",
        capability="text",
        model_id=7,
        priority=0,
        is_default=True,
    )

    assert session.commits == 0


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"plan": "starter"}, id="plan"),
        pytest.param({"capability": "image"}, id="capability"),
        pytest.param({"llm_model_id": 8}, id="model"),
    ],
)
async def test_grant_membership_is_immutable(as_owner, change):
    stored = {"plan": "pro", "capability": "text", "llm_model_id": 7} | change
    session = grant_session(plan=stored["plan"], capability=stored["capability"], model_id=stored["llm_model_id"])

    with pytest.raises(ValueError, match="membership is immutable"):
        await catalog_admin.save_grant(
            session, actor_user_id=OWNER_ID, plan="pro", capability="text", model_id=7, grant_id=3
        )
    assert session.flushes == 0
    assert session.added == []


async def test_unknown_grant_id_is_refused(as_owner):
    session = grant_session(grant_id=None)
    session.routes["PlanModelGrant"] = None

    with pytest.raises(ValueError, match="Unknown model grant"):
        await catalog_admin.save_grant(
            session, actor_user_id=OWNER_ID, plan="pro", capability="text", model_id=7, grant_id=3
        )
    assert session.flushes == 0


async def test_retiring_a_grant_skips_model_activity_recheck(as_owner):
    session = custody_session(
        model_mapping(["text"]), catalog=platform_catalog(is_active=False), grant=granted(grant_id=3)
    )

    row = await catalog_admin.save_grant(
        session, actor_user_id=OWNER_ID, plan="pro", capability="text", model_id=7, grant_id=3, is_active=False
    )

    assert row.is_active is False
    assert session.executions == [], "retirement only writes the grant row"
    assert session.flushes == 1


async def test_new_inactive_grant_is_refused(as_owner):
    session = custody_session(None)

    with pytest.raises(ValueError, match="New grants must target an active platform model"):
        await catalog_admin.save_grant(
            session, actor_user_id=OWNER_ID, plan="pro", capability="text", model_id=7, is_active=False
        )
    assert session.added == []
    assert session.flushes == 0


# ------------------------------------------------------------ deterministic order


async def test_ordered_grants_returns_priority_then_model_rows(as_owner):
    first = granted(grant_id=1, model_id=9, priority=10)
    second = granted(grant_id=2, model_id=4, priority=10)
    session = grant_session(scalars=[first, second])

    rows = await catalog_admin.ordered_grants(session, actor_user_id=OWNER_ID, plan="pro", capability="text")

    assert rows == [first, second], "the service returns the ordered rows it read, unchanged"
    stmt = session.executions[0]
    sql = stmt_text(stmt)
    assert [str(column.compile(dialect=PG)) for column in stmt._order_by_clauses] == [
        str(column.compile(dialect=PG)) for column in (PlanModelGrant.priority, PlanModelGrant.llm_model_id)
    ]
    assert "plan_model_grants.plan =" in sql
    assert "plan_model_grants.capability =" in sql


@pytest.mark.parametrize(
    "group",
    [
        pytest.param({"plan": "free", "capability": "text"}, id="plan"),
        pytest.param({"plan": "pro", "capability": "chat"}, id="capability"),
    ],
)
async def test_ordered_grants_validates_its_group_key(as_owner, group):
    session = grant_session()

    with pytest.raises(ValueError):
        await catalog_admin.ordered_grants(session, actor_user_id=OWNER_ID, **group)
    assert session.executions == []


# ------------------------------------------------------------ safe console choices


async def test_platform_choices_select_only_safe_provider_metadata(as_owner):
    session = grant_session()
    session._mappings_answer = [{"id": 7, "name": "Managed Text", "model_id": "vendor/model", "provider_name": "Fleet"}]

    choices = await catalog_admin.platform_model_choices(session, actor_user_id=OWNER_ID)

    assert choices == [{"id": 7, "name": "Managed Text", "model_id": "vendor/model", "provider_name": "Fleet"}]
    stmt = session.executions[0]
    sql = stmt_text(stmt)
    for forbidden in ("encrypted_api_key", "managed_credential_ref", "base_url", "auth_header", "credential_version"):
        assert forbidden not in sql, f"{forbidden} must not be projected to the console"
    assert {c["name"] for c in stmt.column_descriptions} == {"id", "name", "model_id", "provider_name"}
    assert "llm_catalogs.kind =" in sql and "llm_catalogs.is_active IS true" in sql
    assert "llm_providers.owner_user_id IS NULL" in sql
    assert "llm_models.owner_user_id IS NULL" in sql
    assert [str(column.compile(dialect=PG)) for column in stmt._order_by_clauses] == [
        str(column.compile(dialect=PG)) for column in (LLMProvider.name, LLMModel.name, LLMModel.id)
    ]


async def test_platform_choices_requires_the_owner(as_owner):
    session = grant_session()
    session.routes["User"] = fresh_owner(is_superuser=False)

    with pytest.raises(PermissionError, match="Platform owner required"):
        await catalog_admin.platform_model_choices(session, actor_user_id=OWNER_ID)
    assert session.executions == []


# ------------------------------------------------------------------ static shape


def test_provider_secret_columns_exist_so_the_exclusion_is_meaningful():
    """If the secret columns were renamed, the console exclusion would go vacuous."""
    assert {"encrypted_api_key", "managed_credential_ref"} <= set(LLMProvider.__table__.columns.keys())
    assert "owner_user_id" in LLMModel.__table__.columns
    assert "owner_user_id" in LLMCatalog.__table__.columns
    stmt = select(LLMProvider.id, LLMProvider.name)
    projection = stmt.compile(dialect=PG)
    assert list(stmt.selected_columns) == [LLMProvider.__table__.c.id, LLMProvider.__table__.c.name]
    assert "encrypted_api_key" not in str(projection)


async def test_retirement_only_deactivates_the_platform_model_without_rebinding_selections(as_owner):
    session = grant_session()
    session.routes["LLMCatalog"] = platform_catalog(is_active=False)
    session.routes["LLMModel"] = 7
    await catalog_admin.retire_platform_model(session, actor_user_id=OWNER_ID, model_id=7)
    [stmt] = stmts_of(session, Update)
    compiled = stmt.compile(dialect=PG)
    assert compiled.params["is_active"] is False
    assert "UPDATE" in str(compiled) and "llm_models" in str(compiled)
    assert "DELETE" not in str(compiled)
    assert "tenants" not in str(compiled) and "agent_scenarios" not in str(compiled)
    assert session.flushes == 1 and session.commits == 0


async def test_personal_model_cannot_be_retired_by_the_platform_writer(as_owner):
    session = grant_session()
    session.routes["LLMModel"] = None
    with pytest.raises(ValueError, match="Platform model required"):
        await catalog_admin.retire_platform_model(session, actor_user_id=OWNER_ID, model_id=7)
    assert session.executions == [] and session.flushes == 0


async def test_inactive_platform_rows_remain_safe_console_choices_for_retirement(as_owner):
    session = grant_session()
    session._mappings_answer = [{"id": 7, "name": "Retired", "model_id": "retired", "provider_name": "Fleet"}]
    choices = await catalog_admin.platform_model_choices(session, actor_user_id=OWNER_ID, include_inactive=True)
    assert choices[0]["id"] == 7
    sql = stmt_text(session.executions[0])
    assert "is_active IS true" not in sql
    assert "llm_catalogs.kind =" in sql
    assert "owner_user_id IS NULL" in sql
    assert "encrypted_api_key" not in sql and "managed_credential_ref" not in sql
