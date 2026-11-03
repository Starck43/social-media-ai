"""Strict rendered Telegram picker/login forms; synthetic data only."""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

ROOT = Path(__file__).resolve().parents[2]


def _shell():
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
        flashes=[],
        request=SimpleNamespace(query_params={}),
        member_level=None,
    )


def _environment():
    env = Environment(
        loader=FileSystemLoader(ROOT / "app/web/templates"),
        autoescape=select_autoescape(),
        undefined=StrictUndefined,
        extensions=["jinja2.ext.do"],
    )
    env.globals["static_v"] = lambda path: "http://fixture.test/static/" + path
    env.globals["url_for"] = lambda name, path: "http://fixture.test/static/" + path
    return env


def source_context(selected: bool, empty: bool = False):
    source = SimpleNamespace(
        name="Synthetic channel",
        source_id=17,
        external_id="@synthetic_channel",
        selection_state="selected" if selected else "unselected",
        grant_id=31 if selected else None,
        authorization_revision=4 if selected else None,
        binding_token="synthetic-binding-token",
    )
    return {
        **_shell(),
        "source_error": None,
        "runtime_enabled": False,
        "source_csrf": "synthetic-source-token",
        "picker": SimpleNamespace(
            source=source,
            can_edit=True,
            choices=(
                []
                if empty
                else [
                    SimpleNamespace(
                        grant_id=32,
                        authorization_id=18,
                        authorization_revision=5,
                        subject_key="90001",
                    )
                ]
            ),
        ),
    }


def login_context(state: str):
    attempt = None
    error = None
    if state == "error":
        error = "Synthetic validation error"
    elif state in {"code", "password"}:
        attempt = SimpleNamespace(
            state=state,
            revision=6,
            expires_at="synthetic expiry",
            attempt_id=19,
            failure_code=None,
            code_attempts_remaining=3,
            password_attempts_remaining=2,
            recovery_authorization_id=None,
        )
    return {
        **_shell(),
        "login_error": error,
        "lookup_key": None,
        "recovery_target": None,
        "recovery_revision": None,
        "login_tokens": {
            "start": "synthetic-login-token",
            "code": "synthetic-code-token",
            "password": "synthetic-password-token",
            "cancel": "synthetic-cancel-token",
        },
        "request_key": "synthetic-request-key",
        "attempt": attempt,
    }


def render_all(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    env = _environment()
    cases = {
        "source-unselected": ("web/telegram_source_account.html", source_context(False)),
        "source-selected": ("web/telegram_source_account.html", source_context(True)),
        "source-empty": ("web/telegram_source_account.html", source_context(False, empty=True)),
        **{
            f"login-{state}": ("web/telegram_login.html", login_context(state))
            for state in ("start", "code", "password", "error")
        },
    }
    for name, (template, context) in cases.items():
        (output / f"{name}.html").write_text(env.get_template(template).render(**context))
    return len(cases)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(f"Rendered {render_all(args.output)} strict synthetic Telegram form states")
