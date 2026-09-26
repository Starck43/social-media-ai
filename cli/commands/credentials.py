"""Manage platform credentials in the per-tenant vault.

Secrets are stored encrypted in `tenant_credentials` (Fernet, `CREDENTIALS_KEY`)
and resolved by `app.services.social.credentials`, which keeps environment
variables as the legacy fallback. This CLI is the operator's entry point: the
secret is entered here (or in the admin) and is never printed back.
"""

import typer
from rich import print as rprint
from rich.table import Table

app = typer.Typer(name="credentials", help="Manage platform credentials (tenant vault)")


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


@app.command("list")
def list_credentials(tenant: str = typer.Option("", "--tenant", help="Workspace slug or id (default: all)")):
    """List vault credentials. Secrets are never printed."""

    async def _list():
        from app.models.managers.tenant_manager import tenant_credentials

        if tenant:
            tenant_id = await _resolve_tenant_id(tenant)
            rows = await tenant_credentials.filter(tenant_id=tenant_id)
        else:
            rows = await tenant_credentials.all()

        table = Table(title="Platform credentials (vault)")
        for column in ("id", "tenant", "platform", "kind", "label", "expires_at", "active"):
            table.add_column(column)
        for row in rows:
            table.add_row(
                str(row.id),
                str(row.tenant_id),
                row.platform,
                row.kind,
                row.label or "—",
                row.expires_at.strftime("%Y-%m-%d") if row.expires_at else "—",
                "yes" if row.is_active else "no",
            )
        rprint(table)

    _run(_list())


@app.command("set")
def set_credential(
    platform: str = typer.Argument(..., help="vk | telegram | max"),
    kind: str = typer.Argument(
        ...,
        help="vk: user_token | service_token; telegram/max: bot_token; " "telegram L2: api_id | api_hash | session",
    ),
    tenant: str = typer.Option("owner", "--tenant", help="Workspace slug or id"),
    label: str = typer.Option("", "--label", help="Free-form note"),
    secret: str | None = typer.Option(None, "--secret", "-s", help="Secret value; prompted (hidden) when omitted"),
):
    """Store (or replace) a credential. The value is encrypted before saving."""
    if not secret:
        secret = typer.prompt("Secret", hide_input=True)

    async def _store():
        from app.models.managers.tenant_manager import tenant_credentials
        from app.services.social.credentials import SETTABLE_KINDS

        platform_name = platform.strip().lower()
        known = SETTABLE_KINDS.get(platform_name)
        if not known:
            rprint(f"[red]Unknown platform {platform_name!r}; expected one of: {', '.join(SETTABLE_KINDS)}[/red]")
            raise typer.Exit(1)
        if kind not in known:
            rprint(f"[red]Unknown kind {kind!r} for {platform_name}; expected one of: {', '.join(known)}[/red]")
            raise typer.Exit(1)

        tenant_id = await _resolve_tenant_id(tenant)
        row = await tenant_credentials.store(
            tenant_id=tenant_id, platform=platform_name, kind=kind, secret=secret, label=label or None
        )
        rprint(f"[green]Saved {platform_name}/{kind} for workspace {tenant_id} (credential #{row.id})[/green]")

    _run(_store())


@app.command("disable")
def disable_credential(
    platform: str = typer.Argument(..., help="vk | telegram | max"),
    kind: str = typer.Argument(..., help="Secret kind, e.g. user_token"),
    tenant: str = typer.Option("owner", "--tenant", help="Workspace slug or id"),
):
    """Deactivate the newest matching credential without deleting it."""

    async def _disable():
        from app.models.managers.tenant_manager import tenant_credentials

        tenant_id = await _resolve_tenant_id(tenant)
        rows = await tenant_credentials.active(tenant_id=tenant_id, platform=platform.strip().lower())
        row = next((r for r in rows if r.kind == kind), None)
        if row is None:
            rprint("[yellow]Nothing to disable[/yellow]")
            raise typer.Exit(1)
        await tenant_credentials.update_by_id(row.id, is_active=False)
        rprint(f"[green]Disabled credential #{row.id} ({row.platform}/{row.kind})[/green]")

    _run(_disable())


@app.command("login")
def login_credential(
    platform: str = typer.Argument("telegram", help="Interactive L2 login; only 'telegram' for now"),
    tenant: str = typer.Option("owner", "--tenant", help="Workspace slug or id"),
):
    """Authorize an MTProto user session (code + optional 2FA) and store it.

    Re-running performs a fresh login and replaces the stored session parts.
    api_id/api_hash are taken from the vault/env when present, otherwise
    prompted. The session string is never echoed.
    """
    if platform.strip().lower() != "telegram":
        rprint("[red]Only 'telegram' supports interactive login (MTProto L2)[/red]")
        raise typer.Exit(1)

    async def _login():
        from app.services.social.credentials import resolve_token
        from app.services.social.tg_session import check_session, interactive_login, load_session, save_session

        tenant_id = await _resolve_tenant_id(tenant)
        api_id_raw = await resolve_token("telegram", tenant_id=tenant_id, kinds=("api_id",))
        api_hash = await resolve_token("telegram", tenant_id=tenant_id, kinds=("api_hash",))
        if api_id_raw and api_hash:
            rprint("Using api_id/api_hash from the vault/env.")
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

        await save_session(tenant_id, api_id=api_id, api_hash=api_hash, session=session)
        stored = await load_session(tenant_id=tenant_id)
        verdict = await check_session(stored) if stored else "error: session did not resolve after save"
        colour = "green" if verdict.startswith("ok") else "red"
        rprint(f"[{colour}]telegram L2 session saved: {verdict}[/{colour}]")

    _run(_login())


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
def test_credentials(tenant: str = typer.Option("owner", "--tenant", help="Workspace slug or id")):
    """Show where each platform's secret comes from and whether it works."""

    async def _test():
        from app.services.social.credentials import credential_status, resolve_token
        from app.services.social.tg_session import check_session, load_session

        tenant_id = await _resolve_tenant_id(tenant)
        for platform, source in (await credential_status(tenant_id=tenant_id)).items():
            if source == "missing":
                rprint(f"[yellow]{platform}: missing[/yellow]")
                continue
            token = await resolve_token(platform, tenant_id=tenant_id)
            verdict = await _ping(platform, token) if token else "no token"
            colour = "green" if verdict.startswith("ok") else "red"
            rprint(f"[{colour}]{platform}: {source} -> {verdict}[/{colour}]")

        # L2 is separate from the bot token: an MTProto session, if configured.
        mtproto = await load_session(tenant_id=tenant_id)
        if mtproto is None:
            rprint("[dim]telegram L2 (MTProto): not configured (credentials login telegram)[/dim]")
        else:
            verdict = await check_session(mtproto)
            colour = "green" if verdict.startswith("ok") else "red"
            hint = "" if colour == "green" else " - re-run: credentials login telegram"
            rprint(f"[{colour}]telegram L2 (MTProto): {verdict}{hint}[/{colour}]")

    _run(_test())
