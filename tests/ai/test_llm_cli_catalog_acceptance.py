"""Sequential real-PostgreSQL CLI catalog acceptance; never prepares/migrates.

Run only through the approved root conftest and primary R10 runner. Two
synthetic users own their own providers/models; listing, counts and mock probes
go through the actual CLI handler source inside real permission scopes. Only
external provider key/HTTP paths are replaced with tripwires. Cleanup removes
only this fixture's non-financial rows.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import pytest
import typer
from rich.table import Table

# STOP before importing application models/engines if the normal redirect is absent.
_expected = Path(__file__).resolve().parents[1] / "conftest.py"
_conftest = next(
    (module for module in tuple(sys.modules.values()) if getattr(module, "__file__", None) == str(_expected)),
    None,
)
if _conftest is None or getattr(_conftest, "TEST_SCHEMA", None) != "test_schema":
    raise RuntimeError("STOP: root conftest/test_schema redirect required; never use --noconftest")
_target = urlsplit(getattr(_conftest, "TEST_DATABASE_URL", ""))
if (
    _target.hostname not in {"localhost", "127.0.0.1"}
    or (_target.port or 5432) != 5432
    or _target.path != "/social_manager"
):
    raise RuntimeError("STOP: only pre-approved local social_manager/test_schema is allowed")

# isort: split
from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.permissions import operator_permission_scope, permission_scope
from app.core.tenant_context import tenant_scope
from app.models import (
    LLMDispatchReceipt,
    LLMModel,
    LLMProvider,
    PersonalLLMDispatch,
    PlatformRole,
    User,
)
from app.services.ai import model_probe
from app.services.ai.personal_catalog import save_provider_bundle

pytestmark = [pytest.mark.tenancy, pytest.mark.asyncio]

# The actual CLI source, loaded without importing the whole cli package.
_cli_path = Path(__file__).resolve().parents[2] / "cli/commands/llm.py"
_cli_spec = importlib.util.spec_from_file_location("_acceptance_cli_llm", _cli_path)
if _cli_spec is None or _cli_spec.loader is None:
    raise RuntimeError("STOP: CLI source is unavailable")
cli_llm = importlib.util.module_from_spec(_cli_spec)
_cli_spec.loader.exec_module(cli_llm)

_TELEMETRY_FIELDS = (
    "use_count",
    "fail_count",
    "last_success_at",
    "last_error_at",
    "last_request_cost",
    "last_request_cost_at",
)


def _telemetry(row):
    return {name: getattr(row, name) for name in _TELEMETRY_FIELDS}


def _json_report(output):
    start, end = output.index("{"), output.rindex("}")
    return json.loads(output[start : end + 1])


async def _money_counts(user_id, model_id):
    async with async_session_maker() as session:
        receipts = (
            await session.execute(
                select(func.count()).select_from(LLMDispatchReceipt).where(LLMDispatchReceipt.model_id == model_id)
            )
        ).scalar_one()
        dispatches = (
            await session.execute(
                select(func.count())
                .select_from(PersonalLLMDispatch)
                .where(PersonalLLMDispatch.payer_user_id == user_id)
            )
        ).scalar_one()
    return receipts, dispatches


@pytest.fixture
def tables(monkeypatch):
    recorded = []

    class RecordingTable(Table):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.recorded_rows = []
            recorded.append(self)

        def add_row(self, *args, **kwargs):
            self.recorded_rows.append(args)
            return super().add_row(*args, **kwargs)

    monkeypatch.setattr(cli_llm, "Table", RecordingTable)
    return recorded


@dataclass
class OwnedCatalog:
    users: list = field(default_factory=list)

    @contextmanager
    def as_user(self, user):
        with tenant_scope(None), permission_scope(user):
            yield

    async def bundle(self, user, *, models=None):
        with self.as_user(user):
            return await save_provider_bundle(
                user_id=user.id,
                provider={
                    "name": "Synthetic catalog",
                    "uid": f"accept-{uuid4().hex}",
                    "base_url": "https://catalog.example.invalid/v1",
                    "api_key": "synthetic-not-a-live-key",
                },
                models=models if models is not None else [{"name": "Synthetic text", "model_id": "synthetic-text"}],
            )

    async def row(self, user, model_id):
        with self.as_user(user):
            return await LLMModel.objects.select_related("provider").get(id=model_id, owner_user_id=user.id)

    async def cleanup(self):
        for user in reversed(self.users):
            with self.as_user(user):
                await LLMProvider.objects.filter(owner_user_id=user.id).delete()
        with tenant_scope(bypass=True), operator_permission_scope():
            for user in reversed(self.users):
                await User.objects.delete_by_id(user.id)


@pytest.fixture
async def owned_catalog():
    data = OwnedCatalog()
    role = await PlatformRole.objects.filter(codename="user").first()
    assert role is not None, "STOP: missing seeded platform user role; do not seed"
    for _ in range(2):
        name = f"catalog-cli-{uuid4().hex}"
        with tenant_scope(bypass=True), operator_permission_scope():
            user = await User.objects.create_user(
                username=name,
                password=f"Synthetic-{uuid4().hex}",
                email=f"{name}@example.invalid",
                role_id=role.id,
                is_active=True,
            )
        data.users.append(user)
    try:
        yield data
    finally:
        await data.cleanup()


async def test_owner_only_provider_and_model_listing_and_counts(owned_catalog, tables):
    data = owned_catalog
    first, second = data.users
    first_bundle = await data.bundle(
        first,
        models=[
            {"name": "Own A", "model_id": "own-a"},
            {"name": "Own B", "model_id": "own-b"},
        ],
    )
    second_bundle = await data.bundle(second, models=[{"name": "Foreign C", "model_id": "foreign-c"}])

    with data.as_user(first):
        await asyncio.to_thread(cli_llm.provider_list)
    provider_table = tables[-1]
    assert [column.header for column in provider_table.columns] == [
        "id",
        "name",
        "api_format",
        "base_url",
        "active",
        "models",
    ]
    assert provider_table.recorded_rows == [
        (
            str(first_bundle["id"]),
            "Synthetic catalog",
            "openai",
            "https://catalog.example.invalid/v1",
            "yes",
            "2",
        )
    ]

    with data.as_user(first):
        await asyncio.to_thread(cli_llm.model_list, provider=None, active=None, model_type=None)
    model_table = tables[-1]
    assert {row[0] for row in model_table.recorded_rows} == {str(row["id"]) for row in first_bundle["models"]}
    assert {row[2] for row in model_table.recorded_rows} == {"Own A", "Own B"}
    assert "Foreign C" not in str(model_table.recorded_rows)

    with data.as_user(second):
        await asyncio.to_thread(cli_llm.provider_list)
    assert tables[-1].recorded_rows == [
        (
            str(second_bundle["id"]),
            "Synthetic catalog",
            "openai",
            "https://catalog.example.invalid/v1",
            "yes",
            "1",
        )
    ]
    with data.as_user(second):
        await asyncio.to_thread(cli_llm.model_list, provider=None, active=None, model_type=None)
    assert {row[2] for row in tables[-1].recorded_rows} == {"Foreign C"}


async def test_foreign_model_lookup_fails_generically(owned_catalog, monkeypatch, capsys):
    data = owned_catalog
    first, second = data.users
    await data.bundle(first)
    foreign = await data.bundle(second, models=[{"name": "Foreign only", "model_id": "foreign-only"}])

    def forbidden(*args, **kwargs):
        raise AssertionError("probe must not run for a foreign model")

    monkeypatch.setattr(model_probe, "probe_mock", forbidden)
    with data.as_user(first), pytest.raises(typer.Exit) as error:
        await asyncio.to_thread(cli_llm.model_test, foreign["models"][0]["id"], "hello", True)

    assert error.value.exit_code == 1
    output = capsys.readouterr().out
    assert "Local model test failed" in output
    assert "Foreign only" not in output


async def test_own_mock_is_local_and_writes_no_telemetry_or_money(owned_catalog, monkeypatch, capsys):
    data = owned_catalog
    user = data.users[0]
    model_api_id = f"mock-{uuid4().hex}"
    bundle = await data.bundle(user, models=[{"name": "Mock model", "model_id": model_api_id}])
    model_id = bundle["models"][0]["id"]
    before = _telemetry(await data.row(user, model_id))
    before_money = await _money_counts(user.id, model_api_id)

    def forbidden(*args, **kwargs):
        raise AssertionError("provider key or HTTP client must not be used")

    async def forbidden_async(*args, **kwargs):
        raise AssertionError("real probe must not run")

    monkeypatch.setattr(LLMProvider, "get_api_key", forbidden)
    monkeypatch.setattr(httpx, "AsyncClient", forbidden)
    monkeypatch.setattr(httpx, "Client", forbidden)
    monkeypatch.setattr(model_probe, "probe_real_capped", forbidden_async)

    with data.as_user(user):
        await asyncio.to_thread(cli_llm.model_test, model_id, "hello", True)

    report = _json_report(capsys.readouterr().out)
    assert report["mode"] == "mock"
    assert report["ok"] is True
    assert report["is_real_response"] is False
    assert report["tokens_spent"] == 0
    assert report["estimated_cost_usd"] == 0.0
    assert "Mock model" in report["content"]
    assert set(report) == {
        "mode",
        "ok",
        "is_real_response",
        "tokens_spent",
        "content",
        "simulated_usage",
        "estimated_cost_usd",
    }

    assert _telemetry(await data.row(user, model_id)) == before
    assert await _money_counts(user.id, model_api_id) == before_money


async def test_real_path_exits_before_backend_for_a_trusted_actor(owned_catalog, monkeypatch, capsys):
    data = owned_catalog
    user = data.users[0]
    bundle = await data.bundle(user)
    model_id = bundle["models"][0]["id"]
    monkeypatch.setattr(cli_llm, "_run", lambda *args, **kwargs: pytest.fail("backend must not run"))

    with data.as_user(user), pytest.raises(typer.Exit) as error:
        await asyncio.to_thread(cli_llm.model_test, model_id, "hello", False)

    assert error.value.exit_code == 1
    assert "authenticated payer" in capsys.readouterr().out


async def test_no_actor_commands_fail_before_any_backend(monkeypatch, tables, capsys):
    monkeypatch.setattr(cli_llm, "_run", lambda *args, **kwargs: pytest.fail("backend must not run"))
    commands = (
        (cli_llm.provider_list, ()),
        (cli_llm.model_list, (None, None, None)),
        (cli_llm.model_test, (1, "hello", True)),
    )
    with permission_scope(None), tenant_scope(bypass=True):
        for handler, args in commands:
            with pytest.raises(typer.Exit) as error:
                await asyncio.to_thread(handler, *args)
            assert error.value.exit_code == 1
    output = capsys.readouterr().out
    assert output.count("authenticated active user") == len(commands)
    assert tables == []


async def test_acceptance_environment_is_the_approved_schema():
    assert settings.DB_SCHEMA == "test_schema", "STOP: schema redirect changed"
