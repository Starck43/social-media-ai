"""Tests for the direct CLI commands (`cli.main collect|analyze|prune|...`).

Unlike `task run` (drives an AgentTask through the queue), the direct commands
resolve sources via the unified `--src` flag and call the job handler now. These
tests cover `resolve_sources` parsing and the `run_handler` wiring for a job type
whose handler needs no LLM/network (`prune`).
"""

import uuid

import pytest

from app.core.tenant_context import tenant_scope
from app.models import Platform, Source
from app.types import SourceType
from cli.run import resolve_sources, run_handler


async def _resolve(src, tenant_id):
    with tenant_scope(bypass=True):
        return await resolve_sources(src, tenant_id)


async def _run(job_type, payload, tenant_id):
    with tenant_scope(bypass=True):
        return await run_handler(job_type, payload, tenant_id)


@pytest.fixture
async def platform():
    p = await Platform.objects.create(
        name=f"vk_{uuid.uuid4().hex[:8]}",
        platform_type="vk",
        base_url="https://vk.com",
        params={},
    )
    yield p
    await Platform.objects.delete_by_id(p.id)


@pytest.fixture
async def sources(platform):
    items = []
    for name in ("alpha", "beta", "skipme"):
        s = await Source.objects.create(
            platform_id=platform.id,
            name=name,
            source_type=SourceType.CHANNEL.name,
            external_id=name,
            is_active=True,
        )
        items.append(s)
    yield items
    for s in items:
        await Source.objects.delete_by_id(s.id)


async def test_resolve_sources_by_id(platform, sources):
    got = await _resolve(f"{sources[0].id} {sources[1].id}", None)
    assert {s.id for s in got} == {sources[0].id, sources[1].id}


async def test_resolve_sources_empty_means_all_active(sources):
    got = await _resolve(None, None)
    assert {s.id for s in sources} <= {s.id for s in got}


async def test_resolve_sources_by_external_id(sources):
    got = await _resolve(f"https://vk.com/{sources[2].external_id}", None)
    assert {s.id for s in got} == {sources[2].id}


async def test_resolve_sources_dedupes(platform, sources):
    got = await _resolve(f"{sources[0].id} {sources[0].id}", None)
    assert len(got) == 1


async def test_run_handler_prune(platform, sources):
    """`prune` calls its handler directly and returns the deleted count."""
    stats = await _run("prune", {"days": 7}, None)
    assert isinstance(stats, dict)
    assert "deleted" in stats


async def test_run_handler_unknown_type():
    with pytest.raises(ValueError):
        await _run("nope", {}, None)
