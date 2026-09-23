"""Integration tests for multi-tenant isolation (real PostgreSQL).

Each test uses @pytest.mark.tenancy to opt out of the conftest bypass so that
BaseManager/QuerySet enforce the tenant guard.

Cleanup: every test deletes only the tenant(s) it created; CASCADE takes care
of all tenant-scoped rows. No global TRUNCATE is used.
"""

from __future__ import annotations

import pytest

from app.core.tenant_context import TenantContextError, tenant_scope
from app.models.agent_memory import AgentMemory
from app.models.tenant import Tenant, TenantChannel

# ═══════════════════════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════════════════════


def _slug(prefix: str) -> str:
    """Short readable slugs that are unlikely to collide on a shared DB."""
    import secrets

    return f"{prefix}-{secrets.token_hex(3)}"


async def _delete_tenant(slug: str) -> None:
    """Delete a tenant by slug; CASCADE removes all its tenant-scoped rows."""
    t = await Tenant.objects.get(slug=slug)
    if t is not None:
        await Tenant.objects.delete_by_id(t.id)


# ═══════════════════════════════════════════════════════════════════════════
# 1. Isolated rows — same scope+key in two tenants
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.tenancy
@pytest.mark.asyncio
async def test_isolated_agent_memory_same_key() -> None:
    """A and B each store ('prefs','tone'); neither sees the other's row."""
    slug_a = _slug("iso-a")
    slug_b = _slug("iso-b")

    try:
        ta = await Tenant.objects.create(slug=slug_a, name="ISO A")
        tb = await Tenant.objects.create(slug=slug_b, name="ISO B")

        with tenant_scope(ta.id):
            ma = await AgentMemory.objects.create(scope="prefs", key="tone", value="friendly")

        with tenant_scope(tb.id):
            mb = await AgentMemory.objects.create(scope="prefs", key="tone", value="strict")

        # —— A sees only A ————————————————————————————————————————————
        with tenant_scope(ta.id):
            rows = await AgentMemory.objects.filter(scope="prefs", key="tone")
            assert len(rows) == 1
            assert rows[0].id == ma.id
            assert rows[0].value == "friendly"
            assert rows[0].tenant_id == ta.id

        # —— B sees only B ————————————————————————————————————————————
        with tenant_scope(tb.id):
            rows = await AgentMemory.objects.filter(scope="prefs", key="tone")
            assert len(rows) == 1
            assert rows[0].id == mb.id
            assert rows[0].value == "strict"
            assert rows[0].tenant_id == tb.id

        # —— Bypass sees both ——————————————————————————————————————————
        with tenant_scope(bypass=True):
            rows = await AgentMemory.objects.filter(scope="prefs", key="tone")
            ids = {r.id for r in rows}
            assert ma.id in ids
            assert mb.id in ids

    finally:
        await _delete_tenant(slug_a)
        await _delete_tenant(slug_b)


# ═══════════════════════════════════════════════════════════════════════════
# 2. Update — A can only touch A's rows
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.tenancy
@pytest.mark.asyncio
async def test_cross_tenant_update_by_id_noop() -> None:
    """update_by_id of a row from the wrong tenant returns None; value unchanged."""
    slug_a = _slug("upd-a")
    slug_b = _slug("upd-b")

    try:
        ta = await Tenant.objects.create(slug=slug_a, name="UPD A")
        tb = await Tenant.objects.create(slug=slug_b, name="UPD B")

        with tenant_scope(ta.id):
            await AgentMemory.objects.create(scope="upd", key="color", value="blue")

        with tenant_scope(tb.id):
            mb = await AgentMemory.objects.create(scope="upd", key="color", value="red")

        # —— from A's context, try to update B's row ———————————————————
        with tenant_scope(ta.id):
            result = await AgentMemory.objects.update_by_id(mb.id, value="hacked")
            assert result is None

        # —— B's row is untouched ——————————————————————————————————————
        with tenant_scope(tb.id):
            row = await AgentMemory.objects.get(id=mb.id)
            assert row is not None
            assert row.value == "red"

        # —— B can update its own row ——————————————————————————————————
        with tenant_scope(tb.id):
            upd = await AgentMemory.objects.update_by_id(mb.id, value="green")
            assert upd is not None
            assert upd.value == "green"

    finally:
        await _delete_tenant(slug_a)
        await _delete_tenant(slug_b)


# ═══════════════════════════════════════════════════════════════════════════
# 3. Delete — A cannot delete B's row
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.tenancy
@pytest.mark.asyncio
async def test_cross_tenant_delete_by_id_noop() -> None:
    """delete_by_id of a row from the wrong tenant returns False; row survives."""
    slug_a = _slug("del-a")
    slug_b = _slug("del-b")

    try:
        ta = await Tenant.objects.create(slug=slug_a, name="DEL A")
        tb = await Tenant.objects.create(slug=slug_b, name="DEL B")

        with tenant_scope(tb.id):
            mb = await AgentMemory.objects.create(scope="del", key="item", value="keeper")

        # —— from A's context, try to delete B's row ———————————————————
        with tenant_scope(ta.id):
            ok = await AgentMemory.objects.delete_by_id(mb.id)
            assert ok is False

        # —— B's row still exists ——————————————————————————————————————
        with tenant_scope(tb.id):
            row = await AgentMemory.objects.get(id=mb.id)
            assert row is not None
            assert row.value == "keeper"

    finally:
        await _delete_tenant(slug_a)
        await _delete_tenant(slug_b)


# ═══════════════════════════════════════════════════════════════════════════
# 4. No-tenant access → TenantContextError
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.tenancy
@pytest.mark.asyncio
async def test_read_without_tenant_raises() -> None:
    """Reading tenant-scoped data without a tenant context is a hard error."""
    with pytest.raises(TenantContextError, match="no tenant is in context"):
        await AgentMemory.objects.filter(scope="err", key="read")


@pytest.mark.tenancy
@pytest.mark.asyncio
async def test_create_without_tenant_raises() -> None:
    """Creating tenant-scoped data without a tenant context is a hard error."""
    with pytest.raises(TenantContextError, match="without a tenant in context"):
        await AgentMemory.objects.create(scope="err", key="write", value="oops")


# ═══════════════════════════════════════════════════════════════════════════
# 5. Cross-tenant create → TenantContextError
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.tenancy
@pytest.mark.asyncio
async def test_create_cross_tenant_rejected() -> None:
    """create(tenant_id=B) from tenant A's context raises TenantContextError."""
    slug_a = _slug("crx-a")
    slug_b = _slug("crx-b")

    try:
        ta = await Tenant.objects.create(slug=slug_a, name="CRX A")
        tb = await Tenant.objects.create(slug=slug_b, name="CRX B")

        with tenant_scope(ta.id):
            with pytest.raises(TenantContextError, match="Refusing to create"):
                await AgentMemory.objects.create(scope="x", key="y", value="bad", tenant_id=tb.id)

    finally:
        await _delete_tenant(slug_a)
        await _delete_tenant(slug_b)


# ═══════════════════════════════════════════════════════════════════════════
# 6. TenantChannel — cannot rebind to another tenant
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.tenancy
@pytest.mark.asyncio
async def test_channel_cannot_rebind_to_different_tenant() -> None:
    """After a chat is bound to tenant A, binding it to tenant B raises ValueError."""
    slug_a = _slug("ch-a")
    slug_b = _slug("ch-b")

    try:
        ta = await Tenant.objects.create(slug=slug_a, name="CH A")
        tb = await Tenant.objects.create(slug=slug_b, name="CH B")

        chat_id = f"rebind-test-{slug_a}"

        with tenant_scope(ta.id):
            ch1 = await TenantChannel.objects.bind(tenant_id=ta.id, channel="telegram", chat_id=chat_id, kind="private")
            assert ch1 is not None
            assert ch1.tenant_id == ta.id

        with tenant_scope(tb.id):
            with pytest.raises(ValueError, match="already bound to another tenant"):
                await TenantChannel.objects.bind(tenant_id=tb.id, channel="telegram", chat_id=chat_id, kind="private")

    finally:
        await _delete_tenant(slug_a)
        await _delete_tenant(slug_b)


# ═══════════════════════════════════════════════════════════════════════════
# 7. Cleanup safety — tenant deletion cascades to scoped rows only
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.tenancy
@pytest.mark.asyncio
async def test_cleanup_cascade_only_affects_target_tenant() -> None:
    """Deleting tenant A does not affect tenant B's rows."""
    slug_a = _slug("csc-a")
    slug_b = _slug("csc-b")

    ta = await Tenant.objects.create(slug=slug_a, name="CSC A")
    tb = await Tenant.objects.create(slug=slug_b, name="CSC B")

    with tenant_scope(ta.id):
        await AgentMemory.objects.create(scope="csc", key="k1", value="A1")

    with tenant_scope(tb.id):
        mb = await AgentMemory.objects.create(scope="csc", key="k1", value="B1")

    # Delete tenant A — cascades should remove only A's rows.
    await Tenant.objects.delete_by_id(ta.id)

    # B's row must still exist.
    with tenant_scope(tb.id):
        row = await AgentMemory.objects.get(id=mb.id)
        assert row is not None
        assert row.value == "B1"

    # Clean up B as well.
    await _delete_tenant(slug_b)
