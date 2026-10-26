"""No console table escapes the "your own rows or your workspaces" rule.

`AdminScopeMiddleware` narrows every `/admin` request, so the gate is no longer
a list of per-view flags that a new page can forget to set. This test is the
guard for *that*: it inspects the models the console actually registers and
refuses a page whose rows would be neither personally nor workspace scoped.

Two categories are legitimate and must stay explicit and visible here:

- **personal** — the model is `__personal_scoped__` (LLM providers/models), so
  `BaseManager` filters on `owner_user_id`;
- **platform catalogs / platform settings** — the grant matrix (`users`,
  `roles`, `permissions`), the global platform list and the personal credential
  vault. They are not workspace data: `UserAdmin` and the catalog editors carry
  their own rank/owner gates and the vault has its own owner check.

Everything else must be `__tenant_scoped__`; otherwise the middleware has
nothing to narrow and the page would list the whole table.
"""

import re
from pathlib import Path

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
        model = next(item for item in Base.registry._class_registry.values() if getattr(item, "__name__", None) == model_name)
        personal = bool(getattr(model, "__personal_scoped__", False) and hasattr(model, "owner_user_id"))
        tenant = bool(getattr(model, "__tenant_scoped__", False) and hasattr(model, "tenant_id"))
        if not (personal or tenant):
            ungated.append(model_name)
    assert ungated == [], (
        "These console views list every row: neither a personal owner nor a "
        f"workspace narrows them — {ungated}. Add the scope or declare the page "
        "as a platform catalog in PLATFORM_CATALOG_MODELS with its own gate."
    )


def test_the_personal_catalog_pages_are_no_longer_switched_off():
    """The 0109 crutch (`can_create/can_edit = False`) is gone: scope decides now."""
    source = (ROOT / "app/admin/views.py").read_text()
    for view in ("LLMProviderAdmin", "LLMModelAdmin"):
        block = source.split(f"class {view}(", 1)[1].split("\nclass ", 1)[0]
        assert "can_create = False" not in block, view
        assert "can_edit = False" not in block, view


def test_no_console_write_picks_a_fixed_owner():
    """A console create must never land in a hard-coded person's catalogue."""
    source = (ROOT / "app/admin/views.py").read_text()
    assert not re.search(r'data\["owner_user_id"\]\s*=\s*\d', source)
    assert not re.search(r"\.owner_user_id\s*=\s*\d", source)


