"""Collection-time selection decisions and staged-row metadata."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.ai.dedup import item_hash
from app.services.monitoring.selection import (
    STATUS_REJECTED,
    STATUS_SELECTED,
    SelectionScope,
    select_for_collection,
)
from app.utils.collected_content import build_staged_rows


def _post(external_id="1_1", text="", author="1", content_type="post", parent=None):
    item = {
        "platform": "vk",
        "external_id": external_id,
        "text": text,
        "content_type": content_type,
        "author": {"platform": "vk", "external_id": author, "id": int(author)},
    }
    if parent:
        item["parent_external_id"] = parent
    return item


def test_collection_selection_returns_per_item_status_and_reason():
    selected = _post("1_1", "Поездка в Италию")
    rejected = _post("1_2", "Совсем другое")

    result = select_for_collection([selected, rejected], SelectionScope(keywords=("Италия",)))

    assert result.selected == [selected]
    assert result.rejected == [rejected]
    assert [(d.status, d.reason) for d in result.decisions] == [
        (STATUS_SELECTED, "text_matched"),
        (STATUS_REJECTED, "no_text_match"),
    ]
    assert result.reasons == {"text_matched": 1, "no_text_match": 1}


def test_thread_keeps_post_when_comment_matches():
    post = _post("1_1", "пост без фразы")
    comment = _post("1_1:9", "в комментарии Италия", author="2", content_type="comment", parent="1_1")

    result = select_for_collection([post, comment], SelectionScope(keywords=("Италия",)), thread=True)

    assert result.selected == [post, comment]
    assert result.rejected == []
    assert [(d.status, d.reason) for d in result.decisions] == [
        (STATUS_SELECTED, "thread_matched"),
        (STATUS_SELECTED, "thread_comment_matched"),
    ]


def test_thread_keeps_comment_when_post_matches():
    post = _post("1_1", "Италия в посте")
    comment = _post("1_1:9", "обычный комментарий", author="2", content_type="comment", parent="1_1")

    result = select_for_collection([post, comment], SelectionScope(keywords=("Италия",)), thread=True)

    assert result.selected == [post, comment]
    assert all(decision.status == STATUS_SELECTED for decision in result.decisions)


def test_thread_rejects_the_whole_thread_when_no_member_matches():
    post = _post("1_1", "пост")
    comment = _post("1_1:9", "комментарий", author="2", content_type="comment", parent="1_1")

    result = select_for_collection([post, comment], SelectionScope(keywords=("Италия",)), thread=True)

    assert result.selected == []
    assert result.rejected == [post, comment]
    assert [decision.reason for decision in result.decisions] == ["no_text_match", "no_text_match"]


def test_staged_rows_carry_selection_status_and_comment_parent():
    post = _post("1_1", "Италия")
    comment = _post("1_1:9", "комментарий", author="2", content_type="comment", parent="1_1")
    result = select_for_collection([post, comment], SelectionScope(keywords=("Италия",)), thread=True)
    selection = {item_hash(decision.item): (decision.status, decision.reason) for decision in result.decisions}
    source = SimpleNamespace(id=7, params={}, platform=SimpleNamespace(code="vk"))

    rows = build_staged_rows([post, comment], source, run_id=42, selection=selection)

    assert rows[0]["content_type"] == "post"
    assert rows[0]["selection_status"] == "selected"
    assert rows[1]["content_type"] == "comment"
    assert rows[1]["parent_external_id"] == "1_1"
    assert rows[1]["selection_reason"] == "thread_matched"
