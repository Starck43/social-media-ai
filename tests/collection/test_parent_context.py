"""Parent context follows a collected comment without changing its identity."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.models.collection.collected_item import CollectedItem
from app.services.ai.content_classifier import ContentClassifier
from app.services.ai.dedup import item_hash
from app.services.monitoring.collector import _attach_parent_context, _parent_context_snapshot
from app.utils.collected_content import build_staged_rows


def test_parent_snapshot_is_allowlisted_json_safe_and_bounded():
    oversized = "x" * 20_000
    snapshot = _parent_context_snapshot(
        {
            "external_id": "wall-1",
            "platform": "vk",
            "text": oversized,
            "published_at": datetime(2026, 10, 24, tzinfo=timezone.utc),
            "permalink": "https://example.test/post",
            "author": {"id": 7, "name": "Owner", "access_token": "never-copy"},
            "provider_payload": {"secret": "never-copy"},
        }
    )

    assert snapshot is not None
    assert snapshot["external_id"] == "wall-1"
    assert snapshot["date"] == "2026-10-24T00:00:00+00:00"
    assert len(snapshot["text"]) <= 16_000
    assert snapshot["text"].endswith("[parent text truncated]")
    assert snapshot["author"] == {"id": 7, "name": "Owner"}
    assert "provider_payload" not in snapshot


def test_parent_context_survives_staging_projection_and_prompt_without_hash_changes():
    parent = {
        "external_id": "wall-1",
        "platform": "vk",
        "text": "The event starts at noon",
        "published_at": "2026-10-23T12:00:00Z",
        "permalink": "https://example.test/post",
        "author": {"id": 7, "name": "Owner"},
    }
    comment = {
        "external_id": "wall-1:2",
        "content_type": "comment",
        "parent_external_id": "wall-1",
        "platform": "vk",
        "text": "I will be there",
    }
    original_hash = item_hash(dict(comment))
    _attach_parent_context([parent], [comment])

    row = build_staged_rows([comment], SimpleNamespace(id=7, params={}), run_id=11)[0]
    projected = CollectedItem(
        id=1,
        source_id=7,
        external_id=row["external_id"],
        content_hash=row["content_hash"],
        content_type=row["content_type"],
        parent_external_id=row["parent_external_id"],
        lane_id=None,
        platform=row["platform"],
        published_at=None,
        media_type=None,
        attachments=None,
        text=row["text"],
        metrics=row["metrics"],
        author=row["author"],
        permalink=row["permalink"],
    ).as_agent_item()
    prompt = ContentClassifier.prepare_text_content([projected])

    assert row["metrics"]["parent_context"]["external_id"] == "wall-1"
    assert projected["parent_context"] == row["metrics"]["parent_context"]
    assert item_hash(comment) == original_hash, "parent metadata must not change the comment receipt hash"
    assert projected["external_id"] == "wall-1:2", "the comment remains the primary content identity"
    assert "[CONTEXT: parent vk:wall-1" in prompt
    assert "The event starts at noon" in prompt
    assert "I will be there" in prompt


def test_a_short_comment_is_promptable_when_its_parent_context_is_present():
    comment = {
        "content_type": "comment",
        "external_id": "reply-1",
        "text": "Yes!",
        "parent_context": {"external_id": "post-1", "platform": "vk", "text": "A contextual post"},
    }

    assert ContentClassifier.select_text_content([comment]) == [comment]
