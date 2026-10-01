"""Shared logic for the direct CLI commands (`cli.main collect|analyze|...`).

Unlike `task run` (which drives a real AgentTask through the queue), these
commands execute a job handler *now* without creating a task or touching the
queue. The sources come from a unified `--src` flag; the handler runs inside the
resolved workspace's tenant scope so the ORM queryset guard applies.

Every job type is a distinct handler (see `app/jobs/handlers.py::HANDLERS`), so
a direct command is just: resolve sources -> build the handler payload -> call
the handler. No dispatcher, no bookkeeping, no notification.
"""

from __future__ import annotations

from typing import Any


async def resolve_sources(src: str | None, tenant_id: int | None) -> list:
    """Resolve the unified `--src` flag into a list of `Source` rows.

    Accepts, comma/space separated:
      - numeric source ids       (739, 740)
      - a source url / external  (https://vk.com/russkikh_natalia)
      - a platform name keyword  (vk, telegram, max)
    Empty value = all active sources in the (optional) workspace.
    """
    from app.models import Platform, Source

    qs = Source.objects.filter(is_active=True)
    if tenant_id:
        qs = qs.filter(tenant_id=tenant_id)
    qs = qs.select_related("platform", "agent_scenario")

    parts = [p for p in (src or "").replace(",", " ").split() if p.strip()]
    if not parts:
        return list(await qs)

    ids: list[int] = []
    url_keys: list[str] = []
    platform_keys: list[str] = []
    for p in parts:
        if p.isdigit():
            ids.append(int(p))
        elif p.lower() in ("vk", "telegram", "tg", "max"):
            platform_keys.append(p.lower())
        else:
            url_keys.append(p.rstrip("/").split("/")[-1])

    sources: list[Source] = []
    if ids:
        sources += await Source.objects.filter(id__in=ids, is_active=True).select_related("platform", "agent_scenario")
    if url_keys:
        sources += await Source.objects.filter(external_id__in=url_keys, is_active=True).select_related(
            "platform", "agent_scenario"
        )
    if platform_keys:
        platforms = {pl.name.lower(): pl.id for pl in await Platform.objects.all()}
        keys = {("tg" if k == "telegram" else k) for k in platform_keys}
        matched = [pl_id for name, pl_id in platforms.items() if name in keys]
        if matched:
            sources += await Source.objects.filter(platform_id__in=matched, is_active=True).select_related(
                "platform", "agent_scenario"
            )

    # De-dupe by id, keep resolution order.
    seen: set[int] = set()
    out: list[Source] = []
    for s in sources:
        if s.id not in seen:
            seen.add(s.id)
            out.append(s)
    return out


async def run_handler(job_type: str, payload: dict[str, Any], tenant_id: int | None) -> dict[str, Any]:
    """Call a job handler inside the resolved workspace scope and return its stats."""
    from contextlib import nullcontext

    from app.core.tenant_context import tenant_scope
    from app.jobs.handlers import HANDLERS

    handler = HANDLERS.get(job_type)
    if handler is None:
        raise ValueError(f"Unknown job type: {job_type}")

    scope = tenant_scope(tenant_id) if tenant_id else nullcontext()
    with scope:
        result = await handler(payload)
    return result or {}
