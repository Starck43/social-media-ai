"""Offline personal catalog fixtures; no application, DB or provider imports."""

from __future__ import annotations

import argparse
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = runpy.run_path(str(ROOT / "tests/shared/settings_layout_fixtures.py"))


def catalog_context(mode="catalog"):
    context = BASE["context"]("no-workspace" if mode == "no-workspace" else "account")
    providers = [
        dict(
            id=21,
            owner_user_id=2,
            name="Qwen.AI",
            uid="qwen",
            base_url="https://provider.fixture.invalid/v1",
            api_format="openai",
            auth_header="Authorization",
            is_active=True,
            has_api_key=True,
            models=[
                dict(
                    id=31,
                    provider_id=21,
                    name="Qwen Plus",
                    model_id="qwen-plus",
                    model_type="text",
                    is_active=True,
                    is_default=True,
                    use_count=0,
                    fail_count=0,
                    success_count=0,
                    has_recent_error=False,
                ),
                dict(
                    id=32,
                    provider_id=21,
                    name="ОченьДлинноеНазваниеБезПробелов" * 4,
                    model_id="qwen-" + "long" * 35,
                    model_type="text",
                    is_active=True,
                    is_default=False,
                    use_count=0,
                    fail_count=0,
                    success_count=0,
                    has_recent_error=False,
                ),
            ],
        ),
        dict(
            id=22,
            owner_user_id=2,
            name="Другой провайдер",
            uid="other",
            base_url="https://other.fixture.invalid/v1",
            api_format="openai",
            auth_header="Authorization",
            is_active=False,
            has_api_key=False,
            models=[],
        ),
    ]
    context.update(
        tab="models",
        visible_tabs=["account", "models"],
        llm_catalog={"providers": [] if mode == "empty" else providers, "models": [], "effective_default_model_id": 31},
        model_probe_result=None,
        settings_tenant_id=7,
        llm_chat_model_id=32 if mode != "no-workspace" else None,
        llm_provider_choices=[
            {key: provider[key] for key in ("id", "name", "uid", "base_url", "has_api_key")}
            for provider in ([] if mode == "empty" else providers)
        ],
    )
    return context


def render(mode):
    html = BASE["environment"]().get_template("web/settings.html").render(**catalog_context(mode))
    return (
        html.replace("https://cdn.tailwindcss.com", "http://fixture.test/static/vendor/tailwind.js")
        .replace("https://unpkg.com/htmx.org@1.9.12", "http://fixture.test/static/vendor/htmx.js")
        .replace(
            "https://cdn.jsdelivr.net/npm/alpinejs@3.14.1/dist/cdn.min.js",
            "http://fixture.test/static/vendor/alpine.js",
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for mode in ("catalog", "empty", "no-workspace"):
        (args.output / f"{mode}.html").write_text(render(mode))
