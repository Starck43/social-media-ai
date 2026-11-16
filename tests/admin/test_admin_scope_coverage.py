"""No console table escapes the "your own rows or your workspaces" rule.

`AdminScopeMiddleware` narrows every `/admin` request, so the gate is no longer
a list of per-view flags that a new page can forget to set. This module is the
guard for *that*: it inspects the models the console actually registers and
refuses a page whose rows would be neither personally nor workspace scoped.

Two categories are legitimate and must stay explicit and visible here:

- **personal** — the model is `__personal_scoped__` (LLM providers/models), so
  `BaseManager` filters on `owner_user_id` for every platform role;
- **platform catalogs / platform settings** — the grant matrix (`users`,
  `roles`, `permissions`), the global platform list and the personal credential
  vault. They are not workspace data: `UserAdmin` and the catalog editors carry
  their own rank/owner gates and the vault has its own owner check.

Everything else must be `__tenant_scoped__`; otherwise the middleware has
nothing to narrow and the page would list the whole table.

Where a behavior is observable it is asserted behaviorally: the created row's
owner and the manager's refusal, not the shape of `views.py`.
"""

import re
import secrets
from pathlib import Path

import pytest

from app.core.tenant_context import tenant_scope

ROOT = Path(__file__).resolve().parents[2]

# Tables that are the platform's own administration, not somebody's data.
PLATFORM_CATALOG_MODELS = {
    "User",
    "PlatformRole",
    "Permission",
    "Platform",
    "Tenant",
    "UserCredential",
}


def _registered_models() -> list[str]:
    """The model names in `setup_admin()`'s `view_configs`."""
    source = (ROOT / "app/admin/setup.py").read_text()
    block = source.split("view_configs = [", 1)[1].split("]", 1)[0]
    views = re.findall(r"^\s*(\w+),", block, flags=re.MULTILINE)
    assert len(views) == 16, views
    from app.admin import setup as setup_module

    app = setup_module.__dict__
    return [app[name].model.__name__ for name in views]


def test_every_console_model_is_narrowed_by_the_scope():
    from app.models import Base

    ungated = []
    for model_name in _registered_models():
        if model_name in PLATFORM_CATALOG_MODELS:
            continue
        model = next(
            item for item in Base.registry._class_registry.values() if getattr(item, "__name__", None) == model_name
        )
        personal = bool(getattr(model, "__personal_scoped__", False) and hasattr(model, "owner_user_id"))
        tenant = bool(getattr(model, "__tenant_scoped__", False) and hasattr(model, "tenant_id"))
        if not (personal or tenant):
            ungated.append(model_name)
    assert ungated == [], (
        "These console views list every row: neither a personal owner nor a "
        f"workspace narrows them — {ungated}. Add the scope or declare the page "
        "as a platform catalog in PLATFORM_CATALOG_MODELS with its own gate."
    )


def test_the_personal_catalog_pages_are_not_disabled_by_view_flags():
    """The 0109 crutch (`can_create/can_edit = False`) is gone: scope decides now.

    A behavior assertion, not a source scan: the views must offer create/edit so
    that the actor's own catalogue stays writable, and the console scope — not a
    hidden flag — is what refuses everybody else's rows.
    """
    from app.admin.views import LLMModelAdmin, LLMProviderAdmin

    for view_class in (LLMProviderAdmin, LLMModelAdmin):
        view = view_class()
        assert view.can_create is not False, view_class.__name__
        assert view.can_edit is not False, view_class.__name__
        # The manager already filters the rows by `owner_user_id`; the view must
        # not be the place that hides the actor's own catalogue from them.
        assert getattr(view.model, "__personal_scoped__", False), view_class.__name__


@pytest.mark.asyncio
async def test_a_console_write_never_lands_in_a_foreign_catalogue(monkeypatch):
    """A console create cannot pick a fixed or foreign owner — the manager refuses.

    The previous check scanned `views.py` for a hard-coded id, which would pass a
    write that derives the owner from the request. Here the actual write path is
    exercised: with the actor's own catalogue bound, an unnamed create belongs to
    the actor, and one that names another user is never accepted.
    """
    from app.core.permissions import permission_scope
    from app.core.personal_catalog_context import catalog_execution_scope
    from app.models import LLMProvider, PlatformRole, User
    from app.models.managers import base_manager as base_manager_module

    # The autouse fixture leaves the console-wide bypass on; this is the
    # interactive case, where the actor's catalogue is the only one bound.
    monkeypatch.setattr(base_manager_module, "is_bypass", lambda: False)

    suffix = secrets.token_hex(5)
    with tenant_scope(bypass=True):
        role = await PlatformRole.objects.get(codename="admin")
        actor = await User.objects.create_user(
            username=f"console-owner-{suffix}",
            email=f"console-owner-{suffix}@example.com",
            password="scope-password-1",
            role_id=role.id,
        )
        other = await User.objects.create_user(
            username=f"console-stranger-{suffix}",
            email=f"console-stranger-{suffix}@example.com",
            password="scope-password-1",
            role_id=role.id,
        )

    def provider(**values):
        return dict(
            values, uid=f"console-{secrets.token_hex(5)}", api_format="openai", base_url="https://example.invalid/v1"
        )

    # The console binds the actor's own catalogue: never an owner from a form or path.
    with tenant_scope(bypass=False), permission_scope(actor), catalog_execution_scope(actor.id):
        own = await LLMProvider.objects.create(name="console-own", **provider())
        assert own.owner_user_id == actor.id
        with pytest.raises(PermissionError):
            await LLMProvider.objects.create(name="console-forged", owner_user_id=other.id, **provider())

    with catalog_execution_scope(actor.id):
        stored = await LLMProvider.objects.get(id=own.id)
        assert stored.owner_user_id == actor.id
    with catalog_execution_scope(other.id):
        assert await LLMProvider.objects.all() == []
