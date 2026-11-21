"""Default-off fixed-corpus measurement harness for P12-T12 step 6 preparation (S4).

Measures repeated metadata scan HTTP calls and bytes, catalog object and
observation journal growth, content downloads and extractions, stored original
body counts, logical scope bytes, schema-level PostgreSQL physical storage,
query latency percentiles, and traced Python heap usage against a deterministic
synthetic corpus and real PostgreSQL managers.

Never enables runtime UI/action flags, never opens live provider connections,
never writes raw file paths/names/contents into reports, and never invents
Yandex exact-byte version authority.
"""

from __future__ import annotations

import json
import math
import platform
import sys
import time
import tracemalloc
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import parse_qs, urlsplit

import httpx
from sqlalchemy import func, select, text

from app.core.config import settings
from app.core.database import async_session_maker
from app.core.tenant_context import current_tenant_id, is_bypass
from app.integrations.file_workspace.content_read import ContentReadStream
from app.integrations.file_workspace.insight_candidates import InsightCandidate, InsightCandidateBatch
from app.integrations.file_workspace.line_evidence import LineRange, build_line_evidence
from app.integrations.file_workspace.plain_text import MAX_INPUT_BYTES
from app.integrations.file_workspace.policy import _components
from app.integrations.file_workspace.private_admission import ExactVersionAuthority
from app.integrations.file_workspace.yandex_content import DownloadedText, FileDownloadUnavailable
from app.models import FileCatalogObject, FileCatalogObservation, FileCatalogScan
from app.models.file_workspace.private_insights import FileInsightAttempt, FileInsightMount, FilePrivateInsight
from app.models.managers.file_catalog_manager import FileCatalogManager, _owned, _path
from app.models.managers.file_content_manager import FileContentManager
from app.models.managers.file_insight_manager import FileInsightManager
from app.services.file_workspace.catalog_scan import resume_catalog_scan, start_catalog_scan
from app.services.file_workspace.file_content import FileContentService
from app.services.file_workspace.private_insights import PrivateInsightWorkflow
from app.services.file_workspace.queued_actions import execute_file_workspace_action

HARNESS_SCHEMA_VERSION = "p12-t12-s4-v1"
_MAX_CORPUS_ROUNDS = 10
_MAX_EXTRACT_ROUNDS = 10
_MAX_QUERY_ITERATIONS = 100
_TABLE_NAMES = (
    "file_catalog_objects",
    "file_catalog_observations",
    "file_catalog_scans",
    "file_insight_attempts",
    "file_private_insights",
)


class MeasurementHarnessError(ValueError):
    """Sanitized validation error; never echoes paths, tokens, or payloads."""

    def __init__(self, code: str = "invalid_measurement_input"):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True, repr=False)
class CorpusEntry:
    path: str
    kind: Literal["file", "dir"]
    payload: bytes = b""

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


@dataclass(frozen=True, slots=True, repr=False)
class CorpusRoundSpec:
    name: str
    entries: tuple[CorpusEntry, ...]


@dataclass(frozen=True, slots=True, repr=False)
class FixedCorpusSpec:
    corpus_id: str
    root_folder: str
    page_size: int
    search_query: str
    rounds: tuple[CorpusRoundSpec, ...]


def _sample_text(label: str, lines: int, pad_width: int = 48) -> bytes:
    rows = [f"{label} line {idx:03d}: " + ("x" * pad_width) for idx in range(1, lines + 1)]
    return ("\n".join(rows) + "\n").encode("utf-8")


_ROUND_1_ENTRIES: tuple[CorpusEntry, ...] = (
    CorpusEntry("disk:/work/notes", "dir"),
    CorpusEntry("disk:/work/reports", "dir"),
    CorpusEntry("disk:/work/budget_overview.txt", "file", _sample_text("budget_overview", 8, 40)),
    CorpusEntry("disk:/work/readme.txt", "file", _sample_text("readme", 4, 32)),
    CorpusEntry("disk:/work/notes/meeting_notes.txt", "file", _sample_text("meeting_notes", 12, 48)),
    CorpusEntry("disk:/work/notes/todo_list.txt", "file", _sample_text("todo_list", 6, 36)),
    CorpusEntry("disk:/work/reports/q1_budget.txt", "file", _sample_text("q1_budget", 24, 64)),
    CorpusEntry("disk:/work/reports/q2_summary.txt", "file", _sample_text("q2_summary", 40, 84)),
)

_ROUND_2_ENTRIES: tuple[CorpusEntry, ...] = _ROUND_1_ENTRIES

_ROUND_3_ENTRIES: tuple[CorpusEntry, ...] = (
    CorpusEntry("disk:/work/notes", "dir"),
    CorpusEntry("disk:/work/reports", "dir"),
    CorpusEntry("disk:/work/budget_overview.txt", "file", _sample_text("budget_overview", 8, 40)),
    CorpusEntry("disk:/work/readme.txt", "file", _sample_text("readme", 4, 32)),
    CorpusEntry("disk:/work/notes/meeting_notes_final.txt", "file", _sample_text("meeting_notes_final", 14, 48)),
    CorpusEntry("disk:/work/notes/todo_list.txt", "file", _sample_text("todo_list", 6, 36)),
    CorpusEntry("disk:/work/reports/q2_summary.txt", "file", _sample_text("q2_summary", 40, 84)),
)

DEFAULT_FIXED_CORPUS = FixedCorpusSpec(
    corpus_id="p12-t12-s4-fixed-v1",
    root_folder="disk:/work",
    page_size=3,
    search_query="budget",
    rounds=(
        CorpusRoundSpec("initial_scan", _ROUND_1_ENTRIES),
        CorpusRoundSpec("repeat_unchanged_scan", _ROUND_2_ENTRIES),
        CorpusRoundSpec("delta_and_missing_reconcile", _ROUND_3_ENTRIES),
    ),
)


def _positive_int(value: Any, *, high: int = 2_147_483_647, code: str = "invalid_measurement_input") -> int:
    if type(value) is not int or not 1 <= value <= high:
        raise MeasurementHarnessError(code)
    return value


def _validate_corpus(corpus: FixedCorpusSpec) -> str:
    if not isinstance(corpus, FixedCorpusSpec) or not corpus.corpus_id or len(corpus.corpus_id) > 64:
        raise MeasurementHarnessError("invalid_corpus")
    try:
        root = _path(corpus.root_folder)
    except (ValueError, TypeError):
        raise MeasurementHarnessError("invalid_corpus_root") from None
    _positive_int(corpus.page_size, high=100, code="invalid_page_size")
    if not isinstance(corpus.search_query, str) or not corpus.search_query.strip() or len(corpus.search_query) > 64:
        raise MeasurementHarnessError("invalid_search_query")
    if not corpus.rounds or len(corpus.rounds) > _MAX_CORPUS_ROUNDS:
        raise MeasurementHarnessError("invalid_corpus_rounds")
    root_parts = _components(root)
    for round_spec in corpus.rounds:
        if not isinstance(round_spec, CorpusRoundSpec) or not round_spec.name:
            raise MeasurementHarnessError("invalid_corpus_round")
        seen_paths: set[str] = set()
        dir_paths: set[str] = {root}
        for entry in round_spec.entries:
            if not isinstance(entry, CorpusEntry) or entry.kind not in ("file", "dir"):
                raise MeasurementHarnessError("invalid_corpus_entry")
            try:
                canonical = _path(entry.path)
                parts = _components(canonical)
            except (ValueError, TypeError):
                raise MeasurementHarnessError("invalid_corpus_path") from None
            if canonical != entry.path or len(parts) <= len(root_parts) or parts[: len(root_parts)] != root_parts:
                raise MeasurementHarnessError("invalid_corpus_path")
            if canonical in seen_paths:
                raise MeasurementHarnessError("duplicate_corpus_path")
            seen_paths.add(canonical)
            if entry.kind == "dir":
                if entry.payload != b"":
                    raise MeasurementHarnessError("invalid_dir_payload")
                dir_paths.add(canonical)
            else:
                if type(entry.payload) is not bytes or not 1 <= len(entry.payload) <= MAX_INPUT_BYTES:
                    raise MeasurementHarnessError("invalid_file_payload")
        for entry in round_spec.entries:
            parent = "disk:/" + "/".join(_components(entry.path)[:-1])
            if parent not in dir_paths:
                raise MeasurementHarnessError("missing_corpus_parent_dir")
    return root


class _InstrumentedCatalogTransport:
    """Deterministic mock Yandex metadata endpoint counting HTTP calls and bytes."""

    def __init__(self, root_folder: str, entries: tuple[CorpusEntry, ...]):
        self.root_folder = root_folder
        self.children_by_folder: dict[str, list[CorpusEntry]] = {root_folder: []}
        for entry in entries:
            if entry.kind == "dir":
                self.children_by_folder.setdefault(entry.path, [])
            parent = "disk:/" + "/".join(_components(entry.path)[:-1])
            self.children_by_folder.setdefault(parent, []).append(entry)
        for folder in self.children_by_folder:
            self.children_by_folder[folder].sort(key=lambda item: item.name)
        self.http_requests = 0
        self.response_bytes = 0

    def build_client(self) -> httpx.AsyncClient:
        def handler(request: httpx.Request) -> httpx.Response:
            params = parse_qs(urlsplit(str(request.url)).query)
            folder = params["path"][0]
            offset = int(params.get("offset", ["0"])[0])
            limit = int(params.get("limit", ["100"])[0])
            items = self.children_by_folder.get(folder, [])
            chunk = items[offset : offset + limit]
            body = json.dumps(
                {
                    "path": folder,
                    "type": "dir",
                    "_embedded": {
                        "items": [{"path": item.path, "name": item.name, "type": item.kind} for item in chunk],
                        "offset": offset,
                        "limit": limit,
                    },
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            self.http_requests += 1
            self.response_bytes += len(body)
            return httpx.Response(200, content=body, headers={"content-type": "application/json"})

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class _InstrumentedTextClient:
    """Synthetic bounded text client counting content downloads without network I/O."""

    def __init__(self, payload_by_path: dict[str, bytes]):
        self.payload_by_path = payload_by_path
        self.download_calls = 0
        self.downloaded_bytes = 0

    async def download(self, *, path: str, name: str, token: str) -> DownloadedText:
        if not token or path not in self.payload_by_path:
            raise FileDownloadUnavailable()
        payload = self.payload_by_path[path]
        if path.rsplit("/", 1)[-1] != name:
            raise FileDownloadUnavailable()
        self.download_calls += 1
        self.downloaded_bytes += len(payload)
        return DownloadedText(payload=payload)


async def _scope_counts_and_bytes(*, tenant_id: int, actor_id: int, connection_id: int) -> dict[str, int]:
    async with _owned(tenant_id, actor_id, connection_id) as (session, _, _, root_key):
        obj_rows = (
            await session.execute(
                select(
                    FileCatalogObject.lifecycle_state,
                    func.count(FileCatalogObject.id),
                    func.coalesce(func.sum(text("pg_column_size(file_catalog_objects.*)")), 0),
                )
                .where(
                    FileCatalogObject.tenant_id == tenant_id,
                    FileCatalogObject.connection_id == connection_id,
                    FileCatalogObject.root_key == root_key,
                )
                .group_by(FileCatalogObject.lifecycle_state)
            )
        ).all()
        lifecycle_counts = {"present": 0, "missing": 0, "stale": 0}
        object_bytes = 0
        for state, cnt, size_bytes in obj_rows:
            if state in lifecycle_counts:
                lifecycle_counts[state] = int(cnt)
            object_bytes += int(size_bytes or 0)
        object_total = sum(lifecycle_counts.values())

        obs_cnt, obs_bytes = (
            await session.execute(
                select(
                    func.count(FileCatalogObservation.id),
                    func.coalesce(func.sum(text("pg_column_size(file_catalog_observations.*)")), 0),
                )
                .join(
                    FileCatalogObject,
                    (FileCatalogObject.tenant_id == FileCatalogObservation.tenant_id)
                    & (FileCatalogObject.id == FileCatalogObservation.object_id),
                )
                .where(
                    FileCatalogObject.tenant_id == tenant_id,
                    FileCatalogObject.connection_id == connection_id,
                    FileCatalogObject.root_key == root_key,
                )
            )
        ).one()

        scan_cnt, scan_bytes = (
            await session.execute(
                select(
                    func.count(FileCatalogScan.id),
                    func.coalesce(func.sum(text("pg_column_size(file_catalog_scans.*)")), 0),
                ).where(
                    FileCatalogScan.tenant_id == tenant_id,
                    FileCatalogScan.connection_id == connection_id,
                )
            )
        ).one()

        att_cnt, att_bytes = (
            await session.execute(
                select(
                    func.count(FileInsightAttempt.id),
                    func.coalesce(func.sum(text("pg_column_size(file_insight_attempts.*)")), 0),
                ).where(
                    FileInsightAttempt.tenant_id == tenant_id,
                    FileInsightAttempt.connection_id == connection_id,
                )
            )
        ).one()

        ins_cnt, ins_charged, ins_cipher, ins_row_bytes = (
            await session.execute(
                select(
                    func.count(FilePrivateInsight.id),
                    func.coalesce(func.sum(FilePrivateInsight.charged_size), 0),
                    func.coalesce(func.sum(func.octet_length(FilePrivateInsight.payload_encrypted)), 0),
                    func.coalesce(func.sum(text("pg_column_size(file_private_insights.*)")), 0),
                ).where(
                    FilePrivateInsight.tenant_id == tenant_id,
                    FilePrivateInsight.connection_id == connection_id,
                )
            )
        ).one()

        mount = await session.scalar(
            select(FileInsightMount).where(
                FileInsightMount.tenant_id == tenant_id,
                FileInsightMount.connection_id == connection_id,
            )
        )

        return {
            "object_total": int(object_total),
            "present": lifecycle_counts["present"],
            "missing": lifecycle_counts["missing"],
            "stale": lifecycle_counts["stale"],
            "observation_total": int(obs_cnt or 0),
            "scan_row_count": int(scan_cnt or 0),
            "attempt_count": int(att_cnt or 0),
            "insight_count": int(ins_cnt or 0),
            "processing_allowed": bool(mount.processing_allowed) if mount is not None else False,
            "retention_allowed": bool(mount.retention_allowed) if mount is not None else False,
            "objects_logical_bytes": int(object_bytes),
            "observations_logical_bytes": int(obs_bytes or 0),
            "scans_logical_bytes": int(scan_bytes or 0),
            "attempts_logical_bytes": int(att_bytes or 0),
            "insights_charged_bytes": int(ins_charged or 0),
            "insights_ciphertext_bytes": int(ins_cipher or 0),
            "insights_logical_bytes": int(ins_row_bytes or 0),
            "total_logical_bytes": int(
                object_bytes
                + int(obs_bytes or 0)
                + int(scan_bytes or 0)
                + int(att_bytes or 0)
                + int(ins_row_bytes or 0)
            ),
        }


async def _schema_physical_storage() -> dict[str, Any]:
    schema = settings.DB_SCHEMA
    safe_schema = schema.replace('"', '""')
    tables: dict[str, dict[str, int]] = {}
    total_table_bytes = 0
    total_index_bytes = 0
    total_relation_bytes = 0
    async with async_session_maker() as session:
        for table_name in _TABLE_NAMES:
            qualified = f'"{safe_schema}"."{table_name}"'
            row = (
                await session.execute(
                    text(
                        "SELECT "
                        "pg_relation_size(to_regclass(:rel)), "
                        "pg_indexes_size(to_regclass(:rel)), "
                        "pg_total_relation_size(to_regclass(:rel))"
                    ),
                    {"rel": qualified},
                )
            ).one()
            tbl_b = int(row[0] or 0)
            idx_b = int(row[1] or 0)
            tot_b = int(row[2] or 0)
            tables[table_name] = {
                "table_bytes": tbl_b,
                "index_bytes": idx_b,
                "total_bytes": tot_b,
            }
            total_table_bytes += tbl_b
            total_index_bytes += idx_b
            total_relation_bytes += tot_b
    return {
        "schema": schema,
        "tables": tables,
        "total_table_bytes": total_table_bytes,
        "total_index_bytes": total_index_bytes,
        "total_relation_bytes": total_relation_bytes,
    }


def _latency_stats(samples_ms: list[float]) -> dict[str, Any]:
    if not samples_ms:
        return {"count": 0, "min_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0, "mean_ms": 0.0}
    ordered = sorted(samples_ms)
    n = len(ordered)

    def _pct(p: float) -> float:
        idx = min(n - 1, max(0, math.ceil(p * n) - 1))
        return round(ordered[idx], 3)

    return {
        "count": n,
        "min_ms": round(ordered[0], 3),
        "p50_ms": _pct(0.50),
        "p95_ms": _pct(0.95),
        "max_ms": round(ordered[-1], 3),
        "mean_ms": round(sum(ordered) / n, 3),
    }


async def _measure_latency(fn, iterations: int) -> dict[str, Any]:
    samples: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        await fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return _latency_stats(samples)


async def _run_synthetic_retention_probe(
    *,
    tenant_id: int,
    actor_id: int,
    connection_id: int,
    object_id: int,
    payload_by_path: dict[str, bytes],
    insight_manager: FileInsightManager,
    version_authority: ExactVersionAuthority,
) -> dict[str, Any]:
    """Exercise private retention storage/idempotency/forget using a caller-supplied test seam."""
    workflow = PrivateInsightWorkflow(insight_manager)

    class _ProbeReader:
        @asynccontextmanager
        async def open(self, context, *, max_bytes: int):
            payload = payload_by_path[context.path]
            if len(payload) > max_bytes:
                raise MeasurementHarnessError("probe_payload_exceeds_max_bytes")

            async def _chunks():
                yield payload

            yield ContentReadStream(
                context=context,
                chunks=_chunks(),
                expected_bytes=len(payload),
                complete=True,
                provider_version="synthetic-v1",
            )

    source, receipt = await workflow.read(
        tenant_id=tenant_id,
        actor_id=actor_id,
        connection_id=connection_id,
        object_id=object_id,
        reader=_ProbeReader(),
        version_semantics="synthetic_test_harness_only",
    )
    evidence = build_line_evidence(source, ranges=(LineRange(1, min(3, source.extraction.line_count)),))
    batch = InsightCandidateBatch(
        receipt.binding.context.request.nonce,
        evidence,
        (InsightCandidate("Synthetic measurement summary line.", (1,)),),
    )
    saved = await workflow.save(source, batch, receipt, versions=version_authority)
    replayed = await workflow.save(source, batch, receipt, versions=version_authority)
    retry_idempotent = saved.id == replayed.id
    after_save = await _scope_counts_and_bytes(
        tenant_id=tenant_id,
        actor_id=actor_id,
        connection_id=connection_id,
    )
    await insight_manager.forget_object(
        tenant_id=tenant_id,
        actor_id=actor_id,
        connection_id=connection_id,
        object_id=object_id,
    )
    after_forget = await _scope_counts_and_bytes(
        tenant_id=tenant_id,
        actor_id=actor_id,
        connection_id=connection_id,
    )
    return {
        "synthetic_version_authority_used": True,
        "retry_idempotent": retry_idempotent,
        "attempt_count_after_save": after_save["attempt_count"],
        "insight_count_after_save": after_save["insight_count"],
        "charged_bytes_after_save": after_save["insights_charged_bytes"],
        "ciphertext_bytes_after_save": after_save["insights_ciphertext_bytes"],
        "insight_count_after_forget": after_forget["insight_count"],
        "cleanup_restored_zero": after_forget["insight_count"] == 0,
    }


async def run_fixed_corpus_measurement(
    *,
    tenant_id: int,
    actor_id: int,
    connection_id: int,
    corpus: FixedCorpusSpec = DEFAULT_FIXED_CORPUS,
    extract_rounds: int = 2,
    query_iterations: int = 10,
    extraction_mode: Literal["auto", "preview", "original", "none"] = "auto",
    insight_manager: FileInsightManager | None = None,
    synthetic_version_authority: ExactVersionAuthority | None = None,
) -> dict[str, Any]:
    """Run the deterministic P12-T12 fixed-corpus measurement suite in PostgreSQL.

    Reports scan HTTP counts/bytes, observation journal growth, content download
    and extraction counts, stored body counts (always 0 full originals), scope
    logical bytes, PostgreSQL physical relation bytes, query latency stats, and
    traced Python memory without modifying feature flags or leaking filenames/text.
    """
    _positive_int(tenant_id)
    _positive_int(actor_id)
    _positive_int(connection_id)
    root_folder = _validate_corpus(corpus)
    if type(extract_rounds) is not int or not 0 <= extract_rounds <= _MAX_EXTRACT_ROUNDS:
        raise MeasurementHarnessError("invalid_extract_rounds")
    _positive_int(query_iterations, high=_MAX_QUERY_ITERATIONS, code="invalid_query_iterations")
    if extraction_mode not in ("auto", "preview", "original", "none"):
        raise MeasurementHarnessError("invalid_extraction_mode")
    if (insight_manager is None) != (synthetic_version_authority is None):
        raise MeasurementHarnessError("incomplete_retention_probe_dependencies")

    initial_scope = await _scope_counts_and_bytes(
        tenant_id=tenant_id,
        actor_id=actor_id,
        connection_id=connection_id,
    )
    baseline_physical = await _schema_physical_storage()

    effective_mode: Literal["preview", "original", "none"]
    if extraction_mode == "auto":
        effective_mode = "preview" if initial_scope["processing_allowed"] else "original"
    else:
        if extraction_mode == "preview" and not initial_scope["processing_allowed"]:
            raise MeasurementHarnessError("processing_consent_required_for_preview")
        effective_mode = extraction_mode

    catalog_manager = FileCatalogManager()
    content_manager = FileContentManager(enabled=True)

    was_tracing = tracemalloc.is_tracing()
    if not was_tracing:
        tracemalloc.start()
    else:
        tracemalloc.reset_peak()

    total_t0 = time.perf_counter()
    try:
        round_reports: list[dict[str, Any]] = []
        total_scan_requests = 0
        total_scan_bytes = 0
        total_download_calls = 0
        total_downloaded_bytes = 0
        total_extraction_calls = 0
        total_non_present_denials = 0
        last_payload_by_path: dict[str, bytes] = {}
        all_distinct_paths: set[str] = set()
        total_corpus_file_bytes = 0
        total_corpus_lines = 0

        for round_index, round_spec in enumerate(corpus.rounds, start=1):
            before_scope = await _scope_counts_and_bytes(
                tenant_id=tenant_id,
                actor_id=actor_id,
                connection_id=connection_id,
            )
            transport = _InstrumentedCatalogTransport(root_folder, round_spec.entries)
            payload_by_path = {entry.path: entry.payload for entry in round_spec.entries if entry.kind == "file"}
            last_payload_by_path = payload_by_path
            for entry in round_spec.entries:
                all_distinct_paths.add(entry.path)
            if round_index == 1:
                total_corpus_file_bytes = sum(len(v) for v in payload_by_path.values())
                total_corpus_lines = sum(v.count(b"\n") for v in payload_by_path.values())

            scan_t0 = time.perf_counter()
            await start_catalog_scan(
                tenant_id=tenant_id,
                actor_id=actor_id,
                connection_id=connection_id,
                folder=root_folder,
                page_size=corpus.page_size,
                recursive=True,
            )
            async with transport.build_client() as client:
                scan_state = "partial"
                while scan_state == "partial":
                    scan_result = await resume_catalog_scan(
                        tenant_id=tenant_id,
                        actor_id=actor_id,
                        connection_id=connection_id,
                        client=client,
                    )
                    scan_state = str(scan_result["state"])
            scan_ms = (time.perf_counter() - scan_t0) * 1000.0

            text_client = _InstrumentedTextClient(payload_by_path)
            content_service = FileContentService(content_manager, client=text_client)

            catalog_items: list[dict[str, Any]] = []
            after = 0
            while True:
                page = await catalog_manager.list_page(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    connection_id=connection_id,
                    after=after,
                    limit=100,
                )
                catalog_items.extend(page["items"])
                if page["next_after"] is None:
                    break
                after = int(page["next_after"])

            extract_calls_round = 0
            denials_round = 0
            extract_t0 = time.perf_counter()
            if effective_mode != "none" and extract_rounds > 0:
                file_items = [item for item in catalog_items if item["kind"] == "file"]
                for _ in range(extract_rounds):
                    for item in file_items:
                        if item["lifecycle_state"] != "present":
                            try:
                                if effective_mode == "preview":
                                    await content_service.preview(
                                        start=1,
                                        end=16,
                                        tenant_id=tenant_id,
                                        actor_id=actor_id,
                                        connection_id=connection_id,
                                        object_id=int(item["id"]),
                                    )
                                else:
                                    await content_service.original(
                                        tenant_id=tenant_id,
                                        actor_id=actor_id,
                                        connection_id=connection_id,
                                        object_id=int(item["id"]),
                                    )
                            except FileDownloadUnavailable:
                                denials_round += 1
                            continue
                        if effective_mode == "preview":
                            await content_service.preview(
                                start=1,
                                end=16,
                                tenant_id=tenant_id,
                                actor_id=actor_id,
                                connection_id=connection_id,
                                object_id=int(item["id"]),
                            )
                            extract_calls_round += 1
                        else:
                            await content_service.original(
                                tenant_id=tenant_id,
                                actor_id=actor_id,
                                connection_id=connection_id,
                                object_id=int(item["id"]),
                            )
                            extract_calls_round += 1
            extract_ms = (time.perf_counter() - extract_t0) * 1000.0

            after_scope = await _scope_counts_and_bytes(
                tenant_id=tenant_id,
                actor_id=actor_id,
                connection_id=connection_id,
            )

            total_scan_requests += transport.http_requests
            total_scan_bytes += transport.response_bytes
            total_download_calls += text_client.download_calls
            total_downloaded_bytes += text_client.downloaded_bytes
            total_extraction_calls += extract_calls_round
            total_non_present_denials += denials_round

            round_reports.append(
                {
                    "round_index": round_index,
                    "round_name": round_spec.name,
                    "scan_state": scan_state,
                    "scan_http_requests": transport.http_requests,
                    "scan_response_bytes": transport.response_bytes,
                    "scan_duration_ms": round(scan_ms, 3),
                    "content_download_calls": text_client.download_calls,
                    "content_downloaded_bytes": text_client.downloaded_bytes,
                    "extraction_calls": extract_calls_round,
                    "extraction_denied_non_present": denials_round,
                    "extraction_duration_ms": round(extract_ms, 3),
                    "objects_before": before_scope["object_total"],
                    "objects_after": after_scope["object_total"],
                    "observations_before": before_scope["observation_total"],
                    "observations_after": after_scope["observation_total"],
                    "observation_delta": after_scope["observation_total"] - before_scope["observation_total"],
                    "present_count": after_scope["present"],
                    "missing_count": after_scope["missing"],
                    "stale_count": after_scope["stale"],
                }
            )

        present_page = await catalog_manager.list_page(
            tenant_id=tenant_id,
            actor_id=actor_id,
            connection_id=connection_id,
            lifecycle="present",
            limit=100,
        )
        present_files = [item for item in present_page["items"] if item["kind"] == "file"]
        sample_object_id = int(present_files[0]["id"]) if present_files else None

        latency_ms: dict[str, Any] = {
            "list_page_all": await _measure_latency(
                lambda: catalog_manager.list_page(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    connection_id=connection_id,
                    limit=corpus.page_size,
                ),
                query_iterations,
            ),
            "list_page_search": await _measure_latency(
                lambda: catalog_manager.list_page(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    connection_id=connection_id,
                    query=corpus.search_query,
                    limit=corpus.page_size,
                ),
                query_iterations,
            ),
            "list_page_lifecycle_present": await _measure_latency(
                lambda: catalog_manager.list_page(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    connection_id=connection_id,
                    lifecycle="present",
                    limit=corpus.page_size,
                ),
                query_iterations,
            ),
        }
        if sample_object_id is not None:
            latency_ms["detail_lookup"] = await _measure_latency(
                lambda: catalog_manager.detail(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    connection_id=connection_id,
                    object_id=sample_object_id,
                ),
                query_iterations,
            )
        if not is_bypass() and current_tenant_id() == tenant_id:
            latency_ms["queued_catalog_search"] = await _measure_latency(
                lambda: execute_file_workspace_action(
                    {
                        "action": "catalog_search",
                        "connection_id": connection_id,
                        "query": corpus.search_query,
                        "page_size": corpus.page_size,
                        "_trusted_initiated_by_user_id": actor_id,
                    }
                ),
                query_iterations,
            )

        retention_probe: dict[str, Any] | None = None
        if insight_manager is not None and synthetic_version_authority is not None and sample_object_id is not None:
            retention_probe = await _run_synthetic_retention_probe(
                tenant_id=tenant_id,
                actor_id=actor_id,
                connection_id=connection_id,
                object_id=sample_object_id,
                payload_by_path=last_payload_by_path,
                insight_manager=insight_manager,
                version_authority=synthetic_version_authority,
            )

        final_scope = await _scope_counts_and_bytes(
            tenant_id=tenant_id,
            actor_id=actor_id,
            connection_id=connection_id,
        )
        final_physical = await _schema_physical_storage()
        current_mem, peak_mem = tracemalloc.get_traced_memory()
    finally:
        if not was_tracing:
            tracemalloc.stop()

    total_ms = (time.perf_counter() - total_t0) * 1000.0
    return {
        "schema_version": HARNESS_SCHEMA_VERSION,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "python_version": sys.version.split()[0],
            "platform": platform.platform(),
            "db_schema": settings.DB_SCHEMA,
            "provider_mode": "mock_transport_synthetic_corpus",
            "flags_snapshot": {
                "FILE_CATALOG_UI_ENABLED": bool(settings.FILE_CATALOG_UI_ENABLED),
                "FILE_WORKSPACE_ACTIONS_ENABLED": bool(settings.FILE_WORKSPACE_ACTIONS_ENABLED),
                "FILE_PRIVATE_RESULTS_UI_ENABLED": bool(settings.FILE_PRIVATE_RESULTS_UI_ENABLED),
            },
            "synthetic_version_authority_used": retention_probe is not None,
            "yandex_exact_version_supported": False,
        },
        "corpus": {
            "corpus_id": corpus.corpus_id,
            "round_count": len(corpus.rounds),
            "page_size": corpus.page_size,
            "distinct_paths_seen": len(all_distinct_paths),
            "initial_corpus_file_bytes": total_corpus_file_bytes,
            "initial_corpus_lines": total_corpus_lines,
            "extract_rounds": extract_rounds,
            "query_iterations": query_iterations,
            "extraction_mode": effective_mode,
        },
        "totals": {
            "scan_http_requests": total_scan_requests,
            "scan_response_bytes": total_scan_bytes,
            "content_download_calls": total_download_calls,
            "content_downloaded_bytes": total_downloaded_bytes,
            "extraction_calls": total_extraction_calls,
            "extraction_denied_non_present": total_non_present_denials,
            "stored_original_bodies": 0,
            "catalog_objects_total": final_scope["object_total"],
            "catalog_objects_present": final_scope["present"],
            "catalog_objects_missing": final_scope["missing"],
            "catalog_objects_stale": final_scope["stale"],
            "catalog_observations_total": final_scope["observation_total"],
            "observation_journal_growth": final_scope["observation_total"] - initial_scope["observation_total"],
            "private_attempt_count": final_scope["attempt_count"],
            "private_insight_count": final_scope["insight_count"],
        },
        "rounds": round_reports,
        "storage": {
            "scope_logical_bytes": {
                "file_catalog_objects_bytes": final_scope["objects_logical_bytes"],
                "file_catalog_observations_bytes": final_scope["observations_logical_bytes"],
                "file_catalog_scans_bytes": final_scope["scans_logical_bytes"],
                "file_insight_attempts_bytes": final_scope["attempts_logical_bytes"],
                "file_private_insights_charged_bytes": final_scope["insights_charged_bytes"],
                "file_private_insights_ciphertext_bytes": final_scope["insights_ciphertext_bytes"],
                "file_private_insights_row_bytes": final_scope["insights_logical_bytes"],
                "total_logical_bytes": final_scope["total_logical_bytes"],
                "logical_bytes_delta": final_scope["total_logical_bytes"] - initial_scope["total_logical_bytes"],
            },
            "schema_physical_bytes": {
                "schema": final_physical["schema"],
                "baseline_total_relation_bytes": baseline_physical["total_relation_bytes"],
                "final_total_relation_bytes": final_physical["total_relation_bytes"],
                "delta_total_relation_bytes": (
                    final_physical["total_relation_bytes"] - baseline_physical["total_relation_bytes"]
                ),
                "tables": final_physical["tables"],
            },
        },
        "latency_ms": latency_ms,
        "memory": {
            "tracemalloc_current_bytes": int(current_mem),
            "tracemalloc_peak_bytes": int(peak_mem),
            "total_harness_duration_ms": round(total_ms, 3),
        },
        "retention_probe": retention_probe,
        "limitations": [
            "Synthetic fixed corpus executed over httpx.MockTransport; does not certify live provider latency.",
            "PostgreSQL pg_total_relation_size reports schema-wide relation pages rather than per-tenant billing.",
            "Catalog/preview mode stores zero original file bodies; Yandex ExactVersionAuthority remains unavailable.",
            "Report intentionally excludes raw file paths, filenames, file contents, and credentials.",
        ],
    }
