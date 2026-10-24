"""Bootstrap the single platform owner without the web console.

Industry practice, and this script, is one command on the host at deploy time —
the same shell that already holds production access. Nothing here is reachable
from HTTP: no console, API or admin action assigns `superuser`, so the owner is
minted only by a reviewed command (or by revision `0110`, which does the same
for the first deploy).

The command is idempotent and never guesses:

* an existing owner is reported and left alone (`--verify` exits non-zero if none);
* `--login <username>` names the account explicitly;
* without `--login` the script promotes the sole active `admin`, and refuses
  when there is none or more than one;
* `--create` makes the owner account itself (needs `--login`, `--email`,
  `--password` or `P9_OWNER_PASSWORD`).

Usage::

    python -m scripts.setup.bootstrap_owner --verify
    python -m scripts.setup.bootstrap_owner --login owner
    python -m scripts.setup.bootstrap_owner --create --login owner --email owner@example.com
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from app.core.tenant_context import tenant_scope
from app.models import PlatformRole, User
from app.types import PlatformRoleType

PASSWORD_ENV = "P9_OWNER_PASSWORD"


class BootstrapError(RuntimeError):
    """The command cannot decide or apply the owner safely."""


async def _role(code: PlatformRoleType) -> PlatformRole:
    role = await PlatformRole.objects.get_by_codename(code)
    if role is None:
        raise BootstrapError(f"Platform role catalog has no {code.value!r} row: run scripts/setup/roles.py first")
    return role


async def _current_owner() -> User | None:
    """The account holding `superuser`, if any (there should be at most one)."""
    return (
        await User.objects.prefetch_related("role").filter(role_id=(await _role(PlatformRoleType.SUPERUSER)).id).first()
    )


async def _pick_admin(login: str | None) -> User:
    """The account to promote: the named one, or the sole active admin."""
    if login is not None:
        account = await User.objects.prefetch_related("role").get(username=login)
        if account is None:
            raise BootstrapError(f"No account named {login!r}")
        if not account.is_active:
            raise BootstrapError(f"Account {login!r} is disabled: enable it before promoting")
        if account.role is not None and account.role.codename == PlatformRoleType.ADMIN.value:
            return account
        current = getattr(account.role, "codename", None)
        raise BootstrapError(f"Account {login!r} must already hold the admin role, got {current!r}")
    admin_role = await _role(PlatformRoleType.ADMIN)
    admins = await User.objects.prefetch_related("role").filter(role_id=admin_role.id, is_active=True).all()
    if not admins:
        raise BootstrapError(
            "No active admin to promote: create the owner with --create, "
            "or promote an account by hand and pass --login <username>"
        )
    if len(admins) > 1:
        names = ", ".join(sorted(account.username for account in admins))
        raise BootstrapError(f"{len(admins)} active admins ({names}): pass --login <username> to name the owner")
    return admins[0]


async def _promote(account: User) -> User:
    superuser_role = await _role(PlatformRoleType.SUPERUSER)
    promoted = await User.objects.update_by_id(account.id, role_id=superuser_role.id)
    if promoted is None:
        raise BootstrapError(f"Account {account.username!r} disappeared during promotion")
    promoted = await User.objects.prefetch_related("role").get(id=account.id)
    return promoted or account


async def bootstrap_owner(
    login: str | None = None,
    *,
    create: bool = False,
    email: str | None = None,
    password: str | None = None,
    verify: bool = False,
) -> int:
    """Promote (or create) the owner. Returns a process exit code."""
    owner = await _current_owner()
    if owner is not None:
        print(f"Platform owner already set: {owner.username} (id={owner.id}); nothing to do")
        return 0
    if verify:
        print("Platform owner is not set", file=sys.stderr)
        return 1

    if create:
        if not login:
            raise BootstrapError("--create needs --login <username>")
        if await User.objects.get(username=login) is not None:
            raise BootstrapError(f"Account {login!r} already exists: promote it with --login instead of --create")
        secret = password or os.environ.get(PASSWORD_ENV, "")
        if not secret:
            raise BootstrapError(f"--create needs a password: pass --password or set {PASSWORD_ENV}")
        if not email:
            raise BootstrapError("--create needs --email <address>")
        admin_role = await _role(PlatformRoleType.ADMIN)
        account = await User.objects.create_user(
            username=login,
            password=secret,
            email=email,
            is_active=True,
            role_id=admin_role.id,
        )
    else:
        account = await _pick_admin(login)

    promoted = await _promote(account)
    print(f"Platform owner set: {promoted.username} (id={promoted.id})")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bootstrap the single platform owner (idempotent, no web UI).")
    parser.add_argument("--login", help="username to promote (default: the sole active admin)")
    parser.add_argument("--create", action="store_true", help="create the owner account instead of promoting one")
    parser.add_argument("--email", help="email for --create")
    parser.add_argument("--password", help=f"password for --create (prefer {PASSWORD_ENV})")
    parser.add_argument("--verify", action="store_true", help="exit 1 when no owner is set, change nothing")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    with tenant_scope(bypass=True):
        try:
            return asyncio.run(
                bootstrap_owner(
                    args.login,
                    create=args.create,
                    email=args.email,
                    password=args.password,
                    verify=args.verify,
                )
            )
        except BootstrapError as exc:
            print(f"bootstrap_owner: {exc}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
