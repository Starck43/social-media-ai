"""Collection-time selection decisions and staged-row metadata."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.ai.dedup import item_hash
from app.services.monitoring.selection import (
    CITY_MISMATCH,
    CITY_UNKNOWN,
    STATUS_NEEDS_REVIEW,
    STATUS_REJECTED,
    STATUS_SELECTED,
    CityScope,
    SelectionScope,
    select_for_collection,
)
from app.utils.collected_content import build_staged_rows


def _post(external_id="1_1", text="", author="1", content_type="post", parent=None, city=None):
    item = {
        "platform": "vk",
        "external_id": external_id,
        "text": text,
        "content_type": content_type,
        "author": {"platform": "vk", "external_id": author, "id": int(author)},
    }
    if city is not None:
        item["author"]["city"] = city
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


# ---------------------------------------------------------------------------
# City layer: declared by a profile, decided after the author and text layers.
# ---------------------------------------------------------------------------


def test_city_prefer_keeps_a_known_mismatch_and_does_not_block_the_unknown():
    """`prefer` never parks an item on the city: a known mismatch is noted,
    an unknown city is kept with a reason that says so."""
    inside = _post("1_1", "пост", city="Киров")
    outside = _post("1_2", "пост", city="Москва")
    unknown = _post("1_3", "пост")

    result = select_for_collection(
        [inside, outside, unknown],
        SelectionScope(),
        city=CityScope(name="Киров"),
    )

    assert result.selected == [inside, outside, unknown]
    assert result.needs_review == []
    assert result.rejected == []
    assert result.city_matched == 1
    assert result.count(CITY_UNKNOWN) == 1
    assert [(decision.status, decision.reason) for decision in result.decisions] == [
        (STATUS_SELECTED, "no_tracked_users"),
        (STATUS_SELECTED, CITY_MISMATCH),
        (STATUS_SELECTED, CITY_UNKNOWN),
    ]


def test_city_require_rejects_a_known_mismatch():
    outside = _post("1_1", "пост", city="Москва")
    unknown = _post("1_2", "пост")

    result = select_for_collection([outside, unknown], SelectionScope(), city=CityScope(name="Киров", mode="require"))

    assert result.selected == []
    assert result.rejected == [outside]
    assert result.needs_review == [unknown]
    assert [decision.reason for decision in result.decisions] == [CITY_MISMATCH, CITY_UNKNOWN]


def test_city_review_still_parks_the_unknown_for_an_operator():
    """`review` keeps its meaning: unknown → на проверку, a known mismatch is kept."""
    outside = _post("1_1", "пост", city="Москва")
    unknown = _post("1_2", "пост")

    result = select_for_collection([outside, unknown], SelectionScope(), city=CityScope(name="Киров", mode="review"))

    assert result.selected == [outside]
    assert result.needs_review == [unknown]
    assert result.rejected == []
    assert [(decision.status, decision.reason) for decision in result.decisions] == [
        (STATUS_SELECTED, CITY_MISMATCH),
        (STATUS_NEEDS_REVIEW, CITY_UNKNOWN),
    ]


def test_city_does_not_reopen_an_item_rejected_by_the_text_layer():
    unmatched = _post("1_1", "Совсем другое")

    result = select_for_collection(
        [unmatched],
        SelectionScope(keywords=("Италия",)),
        city=CityScope(name="Киров"),
    )

    assert result.rejected == [unmatched]
    assert result.needs_review == []
    assert result.decisions[0].reason == "no_text_match"


def test_city_prefer_selects_a_thread_whose_post_city_is_unknown():
    """`prefer` must not park a community thread: the post's unknown city keeps it."""
    post = _post("1_1", "пост без фразы")
    comment = _post("1_1:9", "в комментарии Италия", author="2", content_type="comment", parent="1_1", city="Киров")

    result = select_for_collection(
        [post, comment],
        SelectionScope(keywords=("Италия",)),
        thread=True,
        city=CityScope(name="Киров"),
    )

    # The thread is decided by its post, so the matching commenter cannot make
    # the city count as matched — but in `prefer` an unknown post city only
    # renames the reason, it no longer waits for an operator.
    assert result.selected == [post, comment]
    assert result.needs_review == []
    assert result.reasons.get(CITY_UNKNOWN) == 2
    assert [(decision.status, decision.reason) for decision in result.decisions] == [
        (STATUS_SELECTED, CITY_UNKNOWN),
        (STATUS_SELECTED, CITY_UNKNOWN),
    ]


def test_city_review_parks_a_thread_whose_post_city_is_unknown():
    post = _post("1_1", "пост без фразы")
    comment = _post("1_1:9", "в комментарии Италия", author="2", content_type="comment", parent="1_1", city="Киров")

    result = select_for_collection(
        [post, comment],
        SelectionScope(keywords=("Италия",)),
        thread=True,
        city=CityScope(name="Киров", mode="review"),
    )

    # The post's author is unknown, so in `review` the thread waits for an
    # operator even though the matching commenter declares the task's city.
    assert result.selected == []
    assert result.needs_review == [post, comment]
    assert [decision.reason for decision in result.decisions] == [CITY_UNKNOWN, CITY_UNKNOWN]


def test_city_require_rejects_a_thread_whose_post_is_elsewhere():
    post = _post("1_1", "Италия", city="Москва")
    comment = _post("1_1:9", "Италия тоже", author="2", content_type="comment", parent="1_1")

    result = select_for_collection(
        [post, comment],
        SelectionScope(keywords=("Италия",)),
        thread=True,
        city=CityScope(name="Киров", mode="require"),
    )

    assert result.rejected == [post, comment]
    assert [decision.reason for decision in result.decisions] == [CITY_MISMATCH, CITY_MISMATCH]


def test_city_matching_is_case_and_spacing_insensitive():
    post = _post("1_1", "пост", city="  КИРОВ ")

    result = select_for_collection([post], SelectionScope(), city=CityScope(name="Киров"))

    assert result.selected == [post]
    assert result.city_matched == 1
