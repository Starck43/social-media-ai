"""Strict synthetic personal-account HTML; no application/DB/provider imports."""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

ROOT = Path(__file__).resolve().parents[2]
MODES = ("active", "reauth", "revoked", "empty", "error", "unsafe")


def context(mode):
    account = dict(
        authorization_id=12,
        provider_id="telegram",
        auth_method="mtproto",
        subject_key="90001",
        revision=2,
        state=mode if mode in {"active", "reauth", "revoked"} else "active",
    )
    if mode == "unsafe":
        account["subject_key"] = '<img src=x onerror="bad()">' + "1234567890" * 30
    return dict(
        section="settings",
        user=SimpleNamespace(username="synthetic-user", role=None, has_admin_access=lambda: False),
        tenant=None,
        memberships=[],
        workspaces=[],
        perms=None,
        nav=[],
        mobile_nav=[],
        csrf="synthetic-shell-token",
        account_csrf="synthetic-account-token",
        flashes=[],
        request=SimpleNamespace(query_params={}),
        member_level=None,
        accounts=[] if mode in {"empty", "error"} else [account],
        account_error=(
            "Не удалось загрузить авторизации. Попробуйте обновить страницу позже." if mode == "error" else None
        ),
    )


def render(mode):
    env = Environment(
        loader=FileSystemLoader(ROOT / "app/web/templates"),
        autoescape=select_autoescape(),
        undefined=StrictUndefined,
        extensions=["jinja2.ext.do"],
    )
    env.filters["ui_label"] = str
    env.filters["human_dt"] = lambda value, **kwargs: str(value or "")
    env.globals["static_v"] = lambda path: "http://fixture.test/static/" + path
    env.globals["url_for"] = lambda name, path: "http://fixture.test/static/" + path
    return env.get_template("web/telegram_accounts.html").render(**context(mode))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for mode in MODES:
        (args.output / f"accounts-{mode}.html").write_text(render(mode))
    print(f"Rendered {len(MODES)} strict synthetic personal-account states")
