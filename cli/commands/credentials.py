"""Manage platform credentials (personal vault + env).

Personal secrets (VK L2 `user_token`, Telegram MTProto parts) live encrypted in
`user_credentials` keyed by `users.id` and are resolved by
`app.services.social.credentials`. Application-level secrets (app_id,
client_secret, service_token, bot_token) are read from the environment and are
not managed here — set them in `.env`. This CLI is the operator's entry point
for the personal L2 tokens; a secret is never printed back.
"""

import typer
from rich import print as rprint
from rich.table import Table

app = typer.Typer(name="credentials", help="Manage platform credentials (personal vault + env)")


def _run(coro):
    """Run an async command as the platform owner (workspaces stay explicit)."""
    import asyncio

    from app.core.tenant_context import tenant_scope

    with tenant_scope(bypass=True):
        return asyncio.run(coro)


async def _resolve_tenant_id(value: str) -> int:
    from app.models import Tenant

    if value.isdigit():
        tenant = await Tenant.objects.get(id=int(value))
    else:
        tenant = await Tenant.objects.filter(slug=value).first()
    if tenant is None:
        rprint(f"[red]Workspace {value!r} not found[/red]")
        raise typer.Exit(1)
    return tenant.id


async def _resolve_user_id(value: str, tenant_id: int) -> int:
    """Resolve a web user id that is an active member of the workspace."""
    from app.models.managers.tenant_manager import tenant_users

    if not value.isdigit():
        rprint(f"[red]User id must be an integer, got {value!r}[/red]")
        raise typer.Exit(1)
    uid = int(value)
    member = await tenant_users.get(tenant_id=tenant_id, user_id=uid, is_active=True)
    if member is None:
        rprint(f"[red]User {uid} is not an active web member of workspace {tenant_id}[/red]")
        raise typer.Exit(1)
    return uid


async def _workspace_owner_id(tenant_id: int) -> int:
    """Newest active web member of the workspace, or exit if none."""
    from app.models.managers.tenant_manager import tenant_users

    members = await tenant_users.web_memberships_for_tenant(tenant_id)
    if not members:
        rprint("[red]No web member found in this workspace; pass --user <id> explicitly[/red]")
        raise typer.Exit(1)
    return members[-1].user_id


@app.command("list")
def list_credentials(
    user: str = typer.Option("", "--user", help="Web user id whose personal tokens to list"),
    tenant: str = typer.Option("owner", "--tenant", help="Workspace slug or id (to resolve the user)"),
):
    """List a user's personal vault credentials. Secrets are never printed."""

    async def _list():
        from app.models.managers.user_credential_manager import user_credentials

        tenant_id = await _resolve_tenant_id(tenant)
        user_id = await _resolve_user_id(user, tenant_id) if user else await _workspace_owner_id(tenant_id)
        rows = await user_credentials.filter(user_id=user_id)

        table = Table(title=f"Personal credentials (user {user_id})")
        for column in ("id", "platform", "kind", "label", "expires_at", "active"):
            table.add_column(column)
        for row in rows:
            table.add_row(
                str(row.id),
                row.platform,
                row.kind,
                row.label or "—",
                row.expires_at.strftime("%Y-%m-%d") if row.expires_at else "—",
                "yes" if row.is_active else "no",
            )
        rprint(table)

    _run(_list())


@app.command("disable")
def disable_credential(
    platform: str = typer.Argument(..., help="vk | telegram"),
    kind: str = typer.Argument(..., help="Personal secret kind, e.g. user_token | session"),
    user: str = typer.Option("", "--user", help="Web user id that owns the token"),
    tenant: str = typer.Option("owner", "--tenant", help="Workspace slug or id (to resolve the user)"),
):
    """Deactivate the newest matching personal credential without deleting it."""

    async def _disable():
        from app.models.managers.user_credential_manager import user_credentials

        tenant_id = await _resolve_tenant_id(tenant)
        user_id = await _resolve_user_id(user, tenant_id) if user else await _workspace_owner_id(tenant_id)
        rows = await user_credentials.active(user_id=user_id, platform=platform.strip().lower())
        row = next((r for r in rows if r.kind == kind), None)
        if row is None:
            rprint("[yellow]Nothing to disable[/yellow]")
            raise typer.Exit(1)
        await user_credentials.update_by_id(row.id, is_active=False)
        rprint(f"[green]Disabled credential #{row.id} (user {user_id}, {row.platform}/{row.kind})[/green]")

    _run(_disable())


@app.command("login")
def login_credential(
    platform: str = typer.Argument("telegram", help="Interactive L2 login; only 'telegram' for now"),
    user: str = typer.Option("", "--user", help="Web user id that owns the session"),
    tenant: str = typer.Option("owner", "--tenant", help="Workspace slug or id (to resolve the user)"),
):
    """Authorize an MTProto user session (code + optional 2FA) and store it.

    Re-running performs a fresh login and replaces the stored session parts.
    api_id/api_hash are taken from env when present, otherwise prompted. The
    session string is never echoed.
    """
    if platform.strip().lower() != "telegram":
        rprint("[red]Only 'telegram' supports interactive login (MTProto L2)[/red]")
        raise typer.Exit(1)

    async def _login():
        from app.services.social.credentials import resolve_token
        from app.services.social.tg_session import check_session, interactive_login, load_session, save_session

        tenant_id = await _resolve_tenant_id(tenant)
        user_id = await _resolve_user_id(user, tenant_id) if user else await _workspace_owner_id(tenant_id)
        api_id_raw = await resolve_token("telegram", kinds=("api_id",))
        api_hash = await resolve_token("telegram", kinds=("api_hash",))
        if api_id_raw and api_hash:
            rprint("Using api_id/api_hash from the environment.")
        else:
            api_id_raw = api_id_raw or typer.prompt("API id (from my.telegram.org)")
            api_hash = api_hash or typer.prompt("API hash", hide_input=True)
        try:
            api_id = int(api_id_raw)
        except ValueError:
            rprint("[red]API id must be an integer[/red]")
            raise typer.Exit(1)

        try:
            session = await interactive_login(api_id, api_hash)
        except ImportError:
            rprint("[red]telethon is not installed: pip install telethon[/red]")
            raise typer.Exit(1)
        except Exception as e:  # noqa: BLE001 - codes/2FA/network all end here
            rprint(f"[red]Login failed: {e}[/red]")
            raise typer.Exit(1)

        await save_session(user_id, api_id=api_id, api_hash=api_hash, session=session)
        stored = await load_session(user_id=user_id)
        verdict = await check_session(stored) if stored else "error: session did not resolve after save"
        colour = "green" if verdict.startswith("ok") else "red"
        rprint(f"[{colour}]telegram L2 session saved: {verdict}[/{colour}]")

    _run(_login())


@app.command("oauth")
def oauth_credential(
    platform: str = typer.Argument(..., help="Platform to authorize; only 'vk' supports PKCE OAuth for now"),
    tenant: str = typer.Option("owner", "--tenant", help="Workspace slug or id"),
    user: str = typer.Option("", "--user", help="Web user id that owns the L2 token (default: workspace owner)"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the authorize URL in the default browser"),
    wait: int = typer.Option(180, "--wait", help="Seconds to wait for the callback before giving up"),
):
    """Run the full PKCE OAuth flow and store the L2 token in the personal vault.

    Opens the VK authorize URL; after the user consents, VK redirects to the
    public callback (`/api/v1/social/callback`), which exchanges the code and
    saves `vk/user_token` (with a refresh token) to the personal vault of the
    given web user (`--user`, or the workspace owner when omitted). This command
    waits for that write and reports the result.
    """
    if platform.strip().lower() != "vk":
        rprint("[red]Only 'vk' supports PKCE OAuth via this command[/red]")
        raise typer.Exit(1)

    async def _oauth():
        import asyncio
        import webbrowser

        from app.services.social.vk_oauth import build_authorize_url

        tenant_id = await _resolve_tenant_id(tenant)
        user_id = await _resolve_user_id(user, tenant_id) if user else await _workspace_owner_id(tenant_id)
        try:
            url = build_authorize_url(tenant_id, user_id=user_id)
        except RuntimeError as e:
            rprint(f"[red]{e}[/red]")
            rprint("[dim]Set VK_APP_ID / VK_CLIENT_ACCESS_KEY in .env first[/dim]")
            raise typer.Exit(1)

        rprint(f"[cyan]Откройте ссылку и авторизуйте приложение VK:[/cyan]\n{url}")
        if open_browser:
            webbrowser.open(url)

        deadline = asyncio.get_event_loop().time() + wait
        interval = 2.0
        while True:
            row = await _find_oauth_token(user_id)
            if row is not None:
                rprint("[green]VK L2 token сохранён в персональный vault[/green]")
                rprint(
                    f"[dim]credential #{row.id} (user {user_id}, vk/user_token), expires: "
                    f"{row.expires_at.strftime('%Y-%m-%d %H:%M') if row.expires_at else 'long-lived'}[/dim]"
                )
                return
            if asyncio.get_event_loop().time() >= deadline:
                rprint("[red]Таймаут ожидания callback. Проверьте URL и повторите команду.[/red]")
                raise typer.Exit(1)
            await asyncio.sleep(interval)

    _run(_oauth())


async def _find_oauth_token(user_id: int):
    """Newest active `vk/user_token` row for a user that carries a refresh token, or None."""
    from app.models.managers.user_credential_manager import user_credentials

    rows = await user_credentials.active(user_id=user_id, platform="vk")
    for row in sorted(rows, key=lambda r: r.updated_at or r.created_at, reverse=True):
        if row.kind == "user_token" and (row.meta or {}).get("refresh_token"):
            return row
    return None


async def _ping(platform: str, token: str) -> str:
    """Cheap platform call that proves the token works; never raises."""
    import httpx

    from app.core.config import settings

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if platform == "vk":
                resp = await client.get(
                    f"{settings.VK_API_BASE_URL.rstrip('/')}/account.getProfileInfo",
                    params={"access_token": token, "v": settings.VK_API_VERSION},
                )
                data = resp.json()
                if "response" in data:
                    return "ok"
                return f"error: {data.get('error', {}).get('error_msg', resp.status_code)}"
            if platform == "telegram":
                resp = await client.get(f"{settings.TELEGRAM_API_BASE_URL.rstrip('/')}/bot{token}/getMe")
                data = resp.json()
                if data.get("ok"):
                    return f"ok (bot @{data['result'].get('username', '?')})"
                return f"error: {data.get('description', resp.status_code)}"
            if platform == "max":
                resp = await client.get(f"{settings.MAX_API_URL}/me", headers={"Authorization": token})
                return "ok" if resp.status_code == 200 else f"error: HTTP {resp.status_code}"
    except Exception as e:  # noqa: BLE001 - diagnostics must never crash the CLI
        return f"error: {e}"
    return "skipped"


@app.command("test")
def test_credentials(
    user: str = typer.Option("", "--user", help="Web user id whose personal tokens to test"),
    tenant: str = typer.Option("owner", "--tenant", help="Workspace slug or id (to resolve the user)"),
):
    """Show where each platform's secret comes from and whether it works."""

    async def _test():
        from app.services.social.credentials import credential_status, resolve_token
        from app.services.social.tg_session import check_session, load_session

        tenant_id = await _resolve_tenant_id(tenant)
        user_id = await _resolve_user_id(user, tenant_id) if user else await _workspace_owner_id(tenant_id)
        for platform, source in (await credential_status(owner_user_id=user_id)).items():
            if source == "missing":
                rprint(f"[yellow]{platform}: missing[/yellow]")
                continue
            token = await resolve_token(platform, owner_user_id=user_id)
            verdict = await _ping(platform, token) if token else "no token"
            colour = "green" if verdict.startswith("ok") else "red"
            rprint(f"[{colour}]{platform}: {source} -> {verdict}[/{colour}]")

        # L2 is separate from the bot token: an MTProto session, if configured.
        mtproto = await load_session(user_id=user_id)
        if mtproto is None:
            rprint("[dim]telegram L2 (MTProto): not configured (credentials login telegram --user <id>)[/dim]")
        else:
            verdict = await check_session(mtproto)
            colour = "green" if verdict.startswith("ok") else "red"
            hint = "" if colour == "green" else " - re-run: credentials login telegram --user <id>"
            rprint(f"[{colour}]telegram L2 (MTProto): {verdict}{hint}[/{colour}]")

    _run(_test())
