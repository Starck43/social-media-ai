"""No-DB regression for the collection ledger inside unified analyze jobs."""

from __future__ import annotations

import ast
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


class _Query:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def order_by(self, *_args):
        return self

    def limit(self, _value):
        return self

    def __await__(self):
        async def resolve():
            return self.rows

        return resolve().__await__()


def _load_seen_hashes(models):
    tree = ast.parse((ROOT / "app/services/ai/dedup.py").read_text())
    function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "seen_hashes")
    function.body = [
        node for node in function.body if not (isinstance(node, ast.ImportFrom) and node.module == "app.models")
    ]
    module = ast.Module(body=[function], type_ignores=[])
    scope = {
        "AIAnalytics": models.AIAnalytics,
        "CollectedItem": models.CollectedItem,
        "Job": models.Job,
        "date": date,
        "timedelta": timedelta,
        "SEEN_LOOKBACK_DAYS": 30,
        "SEEN_MAX_JOBS": 100,
        "MAX_ANALYSES": 200,
        "logger": SimpleNamespace(warning=lambda *_args: None),
    }
    exec(compile(ast.fix_missing_locations(module), "isolated seen_hashes", "exec"), scope)
    return scope["seen_hashes"]


@pytest.mark.asyncio
async def test_seen_hashes_reads_legacy_collect_and_nested_unified_collection_only():
    collect = SimpleNamespace(
        job_type="collect",
        result={"per_source": [{"source_id": 17, "content_hashes": ["legacy-collected"]}]},
    )
    unified = SimpleNamespace(
        job_type="analyze",
        result={
            "per_source": [
                {
                    "source_id": 17,
                    "content_hashes": ["must-not-be-treated-as-collection"],
                    "collection": {"content_hashes": ["unified-collected"]},
                }
            ]
        },
    )
    jobs_query = _Query([collect, unified])
    calls = {}
    jobs = SimpleNamespace(
        created_at=SimpleNamespace(desc=lambda: "created_at DESC"),
        objects=SimpleNamespace(filter=lambda **kwargs: calls.setdefault("job_filter", kwargs) or jobs_query),
    )
    # `dict.setdefault` returns the filter dict, so use a direct query factory.
    jobs.objects.filter = lambda **kwargs: (calls.__setitem__("job_filter", kwargs) or jobs_query)
    analytics_query = _Query([])
    analytics = SimpleNamespace(
        analysis_date=SimpleNamespace(desc=lambda: "analysis_date DESC"),
        objects=SimpleNamespace(filter=lambda **_kwargs: analytics_query),
    )
    staged = SimpleNamespace(objects=SimpleNamespace(hashes_for_source=lambda _source_id: _empty_hashes()))
    models = SimpleNamespace(AIAnalytics=analytics, CollectedItem=staged, Job=jobs)

    hashes = await _load_seen_hashes(models)(17)

    assert hashes == {"legacy-collected", "unified-collected"}
    assert calls["job_filter"] == {"job_type__in": ("collect", "analyze")}


async def _empty_hashes():
    return set()
