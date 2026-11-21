"""P12-T12 S4: Default-off fixed-corpus measurement harness tests (PG15 test_schema)."""

from __future__ import annotations

import json
import tracemalloc
from datetime import datetime, timezone

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.tenant_context import tenant_scope
from app.integrations.file_workspace.plain_text import MAX_INPUT_BYTES
from app.integrations.file_workspace.private_admission import ExactVersionAssertion
from app.integrations.file_workspace.private_cipher import PrivateCipher
from app.models.managers.file_insight_manager import POLICY_REVISION, FileInsightManager
from app.models.managers.workspace_connection_manager import ConnectionDenied
from app.services.file_workspace.measurement_harness import (
    DEFAULT_FIXED_CORPUS,
    HARNESS_SCHEMA_VERSION,
    CorpusEntry,
    CorpusRoundSpec,
    FixedCorpusSpec,
    MeasurementHarnessError,
    run_fixed_corpus_measurement,
)
from tests.shared.file_catalog_fixtures import actor
from tests.shared.file_catalog_fixtures import data as catalog_data  # noqa: F401


@pytest.fixture
def data(catalog_data):  # noqa: F811
    return catalog_data


pytestmark = pytest.mark.tenancy


async def test_fixed_corpus_measurement_records_scans_journal_storage_latency_and_sanitization(data):
    with actor(data) as args:
        report = await run_fixed_corpus_measurement(
            **args,
            corpus=DEFAULT_FIXED_CORPUS,
            extract_rounds=2,
            query_iterations=5,
        )

    assert report["schema_version"] == HARNESS_SCHEMA_VERSION
    assert report["environment"]["provider_mode"] == "mock_transport_synthetic_corpus"
    assert report["environment"]["yandex_exact_version_supported"] is False
    assert report["environment"]["synthetic_version_authority_used"] is False
    assert report["environment"]["flags_snapshot"] == {
        "FILE_CATALOG_UI_ENABLED": bool(settings.FILE_CATALOG_UI_ENABLED),
        "FILE_WORKSPACE_ACTIONS_ENABLED": bool(settings.FILE_WORKSPACE_ACTIONS_ENABLED),
        "FILE_PRIVATE_RESULTS_UI_ENABLED": bool(settings.FILE_PRIVATE_RESULTS_UI_ENABLED),
    }

    assert report["corpus"]["corpus_id"] == DEFAULT_FIXED_CORPUS.corpus_id
    assert report["corpus"]["round_count"] == 3
    assert report["corpus"]["extraction_mode"] == "original"
    assert report["corpus"]["distinct_paths_seen"] == 9
    assert report["corpus"]["initial_corpus_file_bytes"] > 0
    assert report["corpus"]["initial_corpus_lines"] == 94

    totals = report["totals"]
    assert totals["scan_http_requests"] == 12
    assert totals["scan_response_bytes"] > 0
    assert totals["content_download_calls"] == 34
    assert totals["extraction_calls"] == 34
    assert totals["extraction_denied_non_present"] == 4
    assert totals["stored_original_bodies"] == 0
    assert totals["catalog_objects_total"] == 9
    assert totals["catalog_objects_present"] == 7
    assert totals["catalog_objects_missing"] == 2
    assert totals["catalog_objects_stale"] == 0
    assert totals["catalog_observations_total"] == 9
    assert totals["observation_journal_growth"] == 9
    assert totals["private_attempt_count"] == 0
    assert totals["private_insight_count"] == 0

    r1, r2, r3 = report["rounds"]
    assert r1["scan_state"] == "exhausted"
    assert r1["scan_http_requests"] == 4
    assert r1["objects_after"] == 8
    assert r1["observation_delta"] == 8
    assert r1["extraction_calls"] == 12

    assert r2["scan_state"] == "exhausted"
    assert r2["scan_http_requests"] == 4
    assert r2["objects_after"] == 8
    assert r2["observation_delta"] == 0
    assert r2["extraction_calls"] == 12

    assert r3["scan_state"] == "exhausted"
    assert r3["scan_http_requests"] == 4
    assert r3["objects_after"] == 9
    assert r3["observation_delta"] == 1
    assert r3["present_count"] == 7
    assert r3["missing_count"] == 2
    assert r3["extraction_calls"] == 10
    assert r3["extraction_denied_non_present"] == 4

    logical = report["storage"]["scope_logical_bytes"]
    assert logical["file_catalog_objects_bytes"] > 0
    assert logical["file_catalog_observations_bytes"] > 0
    assert logical["file_catalog_scans_bytes"] > 0
    assert logical["file_private_insights_charged_bytes"] == 0
    assert logical["total_logical_bytes"] == logical["logical_bytes_delta"]

    physical = report["storage"]["schema_physical_bytes"]
    assert physical["schema"] == settings.DB_SCHEMA
    assert physical["final_total_relation_bytes"] > 0
    assert set(physical["tables"]) == {
        "file_catalog_objects",
        "file_catalog_observations",
        "file_catalog_scans",
        "file_insight_attempts",
        "file_private_insights",
    }

    for op_name in (
        "list_page_all",
        "list_page_search",
        "list_page_lifecycle_present",
        "detail_lookup",
        "queued_catalog_search",
    ):
        stats = report["latency_ms"][op_name]
        assert stats["count"] == 5
        assert 0.0 <= stats["min_ms"] <= stats["p50_ms"] <= stats["p95_ms"] <= stats["max_ms"]
        assert stats["mean_ms"] >= 0.0

    assert report["memory"]["tracemalloc_peak_bytes"] > 0
    assert report["memory"]["total_harness_duration_ms"] > 0.0
    assert report["retention_probe"] is None

    serialized = json.dumps(report, ensure_ascii=False)
    for forbidden in (
        "budget_overview.txt",
        "q1_budget.txt",
        "meeting_notes_final.txt",
        "disk:/work",
        "budget_overview line 001",
        "p12t12-synthetic-token",
    ):
        assert forbidden not in serialized


async def test_fixed_corpus_measurement_preview_mode_and_synthetic_retention_probe(data):
    class _SyntheticVersion:
        async def verify(self, source, receipt):
            return ExactVersionAssertion(
                source.context.request,
                receipt.raw_sha256,
                receipt.provider_version,
                receipt.version_semantics,
                receipt.provider_object_id,
                datetime.now(timezone.utc),
            )

    store = FileInsightManager(enabled=True, cipher=PrivateCipher(Fernet.generate_key().decode()))
    try:
        with actor(data) as args:
            await store.set_consent(**args, processing=True, retention=True, policy_revision=POLICY_REVISION)
            report = await run_fixed_corpus_measurement(
                **args,
                corpus=DEFAULT_FIXED_CORPUS,
                extract_rounds=1,
                query_iterations=3,
                extraction_mode="auto",
                insight_manager=store,
                synthetic_version_authority=_SyntheticVersion(),
            )
    finally:
        with tenant_scope(bypass=True):
            await FileInsightManager._purge_expired_batch(limit=100)

    assert report["corpus"]["extraction_mode"] == "preview"
    assert report["environment"]["synthetic_version_authority_used"] is True
    assert report["environment"]["yandex_exact_version_supported"] is False
    assert report["totals"]["stored_original_bodies"] == 0
    assert report["totals"]["extraction_calls"] == 17
    assert report["totals"]["extraction_denied_non_present"] == 2

    probe = report["retention_probe"]
    assert probe is not None
    assert probe["synthetic_version_authority_used"] is True
    assert probe["retry_idempotent"] is True
    assert probe["attempt_count_after_save"] == 1
    assert probe["insight_count_after_save"] == 1
    assert probe["charged_bytes_after_save"] > 0
    assert probe["ciphertext_bytes_after_save"] >= probe["charged_bytes_after_save"]
    assert probe["insight_count_after_forget"] == 0
    assert probe["cleanup_restored_zero"] is True


async def test_fixed_corpus_measurement_rejects_unauthorized_actors_missing_consent_and_bad_specs(data):
    with actor(data, user=0, tenant=0, connection=0) as owner_args:
        with pytest.raises(MeasurementHarnessError, match="processing_consent_required_for_preview"):
            await run_fixed_corpus_measurement(**owner_args, extraction_mode="preview", query_iterations=1)

    with actor(data, user=1, tenant=0, connection=0) as wrong_user_args:
        with pytest.raises(ConnectionDenied):
            await run_fixed_corpus_measurement(**wrong_user_args, extract_rounds=0, query_iterations=1)

    with actor(data, user=0, tenant=1, connection=0) as cross_tenant_args:
        with pytest.raises(ConnectionDenied):
            await run_fixed_corpus_measurement(**cross_tenant_args, extract_rounds=0, query_iterations=1)

    bad_corpus = FixedCorpusSpec(
        corpus_id="bad",
        root_folder="disk:/work",
        page_size=2,
        search_query="x",
        rounds=(CorpusRoundSpec("r1", (CorpusEntry("disk:/other/file.txt", "file", b"ok\n"),)),),
    )
    oversized_corpus = FixedCorpusSpec(
        corpus_id="huge",
        root_folder="disk:/work",
        page_size=2,
        search_query="x",
        rounds=(
            CorpusRoundSpec(
                "r1",
                (CorpusEntry("disk:/work/huge.txt", "file", b"a" * (MAX_INPUT_BYTES + 1)),),
            ),
        ),
    )
    with actor(data) as args:
        with pytest.raises(MeasurementHarnessError, match="invalid_corpus_path"):
            await run_fixed_corpus_measurement(**args, corpus=bad_corpus, query_iterations=1)
        with pytest.raises(MeasurementHarnessError, match="invalid_file_payload"):
            await run_fixed_corpus_measurement(**args, corpus=oversized_corpus, query_iterations=1)
        with pytest.raises(MeasurementHarnessError, match="incomplete_retention_probe_dependencies"):
            await run_fixed_corpus_measurement(
                **args,
                insight_manager=FileInsightManager(enabled=True),
                query_iterations=1,
            )


async def test_fixed_corpus_measurement_preserves_tracemalloc_state(data):
    was_tracing = tracemalloc.is_tracing()
    if was_tracing:
        tracemalloc.stop()
    try:
        with actor(data) as args:
            await run_fixed_corpus_measurement(**args, extract_rounds=0, query_iterations=1)
        assert tracemalloc.is_tracing() is False

        tracemalloc.start()
        with actor(data) as args:
            await run_fixed_corpus_measurement(**args, extract_rounds=0, query_iterations=1)
        assert tracemalloc.is_tracing() is True
    finally:
        if tracemalloc.is_tracing() and not was_tracing:
            tracemalloc.stop()
        elif not tracemalloc.is_tracing() and was_tracing:
            tracemalloc.start()
