"""Tests for the manual collection CLI (cli/commands/collect.py)."""

import secrets
import uuid

import pytest

from app.core.tenant_context import tenant_scope
from app.models import CollectedItem, Job, Platform, Source
from app.models.managers.tenant_manager import tenants
from app.types import SourceType
from cli.commands.collect import _parse_date


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("01-05-2025", "2025-05-01"),
        ("01.05.2025", "2025-05-01"),
        ("2025-05-01", "2025-05-01"),
    ],
)
def test_parse_date_valid(value, expected):
    assert str(_parse_date(value)) == expected


def test_parse_date_none():
    assert _parse_date(None) is None


def test_parse_date_invalid():
    with pytest.raises(Exception):
        _parse_date("not-a-date")


@pytest.mark.tenancy
async def test_a_tenant_scoped_collect_gets_a_job_and_stamps_run_id(monkeypatch):
    """The CLI's tenant-resolved path runs through `run_job_inline`.

    That is the point of the wiring: a collection that goes through a job row
    stamps `run_id` on its staged raw items, so the run is not invisible in the
    source page's «Что собрано» preview (the old direct `run_handler` call had
    no job and left every staged row with `run_id` NULL).
    """
    tenant = await tenants.create(name=f"CLI {secrets.token_hex(4)}", slug=f"cl{secrets.token_hex(4)}")
    job_id = None
    try:
        # The CLI runs with the tenant guard off and resolves the workspace
        # explicitly (see cli/commands/collect.py::_run), so set up the same way.
        with tenant_scope(bypass=True):
            platform = await Platform.objects.create(
                name=f"vk_{uuid.uuid4().hex[:8]}",
                platform_type="vk",
                base_url="https://vk.com",
                params={},
            )
            source = await Source.objects.create(
                tenant_id=tenant.id,
                platform_id=platform.id,
                name="cli-src",
                source_type=SourceType.CHANNEL.name,
                external_id="cli-src-ext",
                is_active=True,
            )

            class _FakeClient:
                async def collect_data(self, source, content_type):
                    return [{"id": "item-1", "text": "hello", "published_at": "2026-10-01", "platform": "vk"}]

            monkeypatch.setattr(
                "app.services.monitoring.collector.get_social_client", lambda platform_obj: _FakeClient()
            )

            from app.jobs.dispatcher import run_job_inline

            # The CLI's tenant branch calls exactly this (see cli/commands/collect.py).
            outcome = await run_job_inline("collect", {"source_ids": [source.id]}, tenant_id=tenant.id)

            assert outcome and outcome.get("status") == "done", outcome
            job_id = outcome["job_id"]

            job = await Job.objects.get(id=job_id)
            assert job is not None
            assert job.job_type == "collect"
            assert job.tenant_id == tenant.id

            item = await CollectedItem.objects.filter(source_id=source.id).first()
            assert item is not None, "a collection must stage its raw rows"
            assert item.run_id == job_id, "a job-backed collect must stamp run_id on the staged rows"

            await CollectedItem.objects.delete(run_id=job_id)
            await Job.objects.delete_by_id(job_id)
            job_id = None
            await Source.objects.delete_by_id(source.id)
            await Platform.objects.delete_by_id(platform.id)
    finally:
        with tenant_scope(bypass=True):
            if job_id is not None:
                await CollectedItem.objects.delete(run_id=job_id)
                await Job.objects.delete_by_id(job_id)
        await tenants.delete_by_id(tenant.id)
