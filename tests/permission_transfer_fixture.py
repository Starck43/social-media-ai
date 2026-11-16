"""Render the actual role form with up to 206 synthetic permissions; no app/DB startup."""

import asyncio
import re
import sys
from importlib.metadata import distribution
from pathlib import Path

import httpx
from test_admin_shell_catalog import ROOT, TYPES, actor, console, source

PACKAGE = distribution("sqladmin").locate_file("sqladmin")


async def render(destination):
    destination.mkdir(parents=True, exist_ok=True)
    loaded = source.__wrapped__()
    for state, count, selected in [
        ("initial", 30, range(21, 31)),
        ("full", 30, range(1, 31)),
        ("small", 3, []),
        ("large", 206, range(180, 207)),
        ("large-empty", 206, []),
        ("large-full", 206, range(1, 207)),
    ]:
        ctx = console(loaded, actor("superuser"))
        choices = []
        for number in range(1, count + 1):
            domain = ["agent", "platform", "tenancy"][(number - 1) // 72] if count > 30 else "mock"
            resource_key = f"{domain}.resource_{(number - 1) // 6:02}" if count > 30 else f"mock.permission_{number:02}"
            action = list(TYPES.ActionType)[(number - 1) % 6] if count > 30 else TYPES.ActionType.VIEW
            resource = loaded.canonical_model_type(
                id=number,
                resource_key=resource_key,
                model_name=f"mock_{number}",
                table_name=f"mock_{number}",
            )
            choices.append(
                loaded.Permission(
                    id=number,
                    name=(f"Название разрешения {number}" if count > 30 else f"Mock permission {number}"),
                    model_type=resource,
                    action_type=action,
                )
            )
        ctx.target.get.return_value.permissions = [permission for permission in choices if permission.id in selected]
        loaded.Permission.objects.prefetch_related.return_value.all.return_value = choices
        view = ctx.admin._views[0]
        view.session_maker.return_value.__enter__.return_value.execute.return_value.scalars.return_value.unique.return_value.all.return_value = (
            choices
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=ctx.app), base_url="https://fixture.invalid"
        ) as client:
            response = await client.get("/admin/platform-role/edit/2")
        assert response.status_code == 200, response.text
        html = response.text.replace('"/static/', '"' + (ROOT / "app/static").as_uri() + "/")
        html = html.replace("https://fixture.invalid/static/", (ROOT / "app/static").as_uri() + "/")
        html = html.replace("https://fixture.invalid/admin/statics/", (PACKAGE / "statics").as_uri() + "/")
        html = re.sub(r'<script\b[^>]*src="https?://[^"]+"[^>]*>\s*</script>', "", html)
        (destination / f"{state}.html").write_text(html)


if __name__ == "__main__":
    asyncio.run(render(Path(sys.argv[1]).resolve()))
