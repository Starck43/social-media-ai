"""No-DB contract tests for the task selection core.

Owner command: `.venv/bin/python -m pytest tests/test_task_selection_core.py -q --noconftest -o addopts=`
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "_task_selection", _ROOT / "app" / "services" / "monitoring" / "selection.py"
    )
    module = importlib.util.module_from_spec(spec)
    # Register before execution: dataclasses resolve their own module by name.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


selection = _load()


def item(text="", author=None, platform="vk", permalink=None):
    data = {"text": text, "platform": platform}
    if author is not None:
        data["author"] = author
    if permalink is not None:
        data["permalink"] = permalink
    return data


class TestTrackedUsers:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("vk:123456", ("vk", "123456")),
            ("vk:@Designer", ("vk", "designer")),
            ("VK:123", ("vk", "123")),
            ("ВКонтакте:42", ("vk", "42")),
            ("telegram:@durov", ("telegram", "durov")),
        ],
    )
    def test_parsing_forms(self, raw, expected):
        user = selection.parse_tracked_user(raw)
        assert (user.platform, user.external_id) == expected

    def test_mapping_form(self):
        user = selection.parse_tracked_user({"platform": "tg", "external_id": "@name"})
        assert user.key() == "telegram:name"

    @pytest.mark.parametrize("raw", ["", "123456", ":42", "vk:", "vk:two words", "1vk:42", "vk:a:b"])
    def test_malformed_entries_fail_visibly(self, raw):
        with pytest.raises(selection.SelectionError):
            selection.parse_tracked_user(raw)

    def test_dedupe_keeps_order_and_platform_scope(self):
        users = selection.parse_tracked_users(["vk:1", "telegram:1", "vk:@A", "vk:a", None])
        assert [u.key() for u in users] == ["vk:1", "telegram:1", "vk:a"]

    def test_none_and_empty_are_no_filter(self):
        assert selection.parse_tracked_users(None) == ()
        assert selection.parse_tracked_users([]) == ()


class TestScope:
    def test_empty_payload_is_no_filter(self):
        scope = selection.build_scope({})
        assert not scope.has_author_filter and not scope.has_text_scope
        assert scope.match == "any"

    def test_keywords_and_topics_share_one_scope(self):
        scope = selection.build_scope({"keywords_list": ["Киров"], "topics_list": ["дизайн интерьера", "киров"]})
        assert scope.keywords == ("Киров", "дизайн интерьера")

    @pytest.mark.parametrize("value", ["some", 1, {"a": "b"}])
    def test_invalid_match_mode_fails(self, value):
        with pytest.raises(selection.SelectionError):
            selection.build_scope({"keywords_match": value})


class TestSelection:
    def test_without_tracked_users_source_items_pass(self):
        result = selection.select_items([item("пост стены"), item("ещё пост")], selection.build_scope({}))
        assert len(result.eligible) == 2
        assert result.count(selection.PASS_NO_TRACKED_USERS) == 2
        assert result.unknown_tracked_users == []

    def test_tracked_users_filter_by_author_only(self):
        scope = selection.build_scope({"tracked_users": ["vk:1"]})
        result = selection.select_items(
            [
                item("пост Анны", author={"platform": "vk", "id": 1, "name": "Анна"}),
                item("пост Бориса", author={"platform": "vk", "id": 2, "name": "Борис"}),
                item("стена без автора"),
            ],
            scope,
        )
        assert [i["text"] for i in result.eligible] == ["пост Анны"]
        assert result.count(selection.REJECT_AUTHOR_NOT_TRACKED) == 2
        assert result.count(selection.PASS_AUTHOR_TRACKED) == 1

    def test_platform_isolation(self):
        scope = selection.build_scope({"tracked_users": ["vk:1"]})
        result = selection.select_items([item("x", author={"platform": "telegram", "id": 1})], scope)
        assert result.eligible == []

    def test_unknown_tracked_users_are_reported(self):
        scope = selection.build_scope({"tracked_users": ["vk:1", "vk:9"]})
        result = selection.select_items([item("x", author={"platform": "vk", "id": 1})], scope)
        assert result.unknown_tracked_users == ["vk:9"]

    def test_author_snapshot_is_matched_by_external_id_or_username(self):
        scope = selection.build_scope({"tracked_users": ["vk:@anna"]})
        result = selection.select_items(
            [item("x", author={"platform": "vk", "external_id": "@Anna"})], scope
        )
        assert result.eligible

    def test_text_scope_any(self):
        scope = selection.build_scope({"keywords_list": ["Киров", "дизайн"]})
        result = selection.select_items([item("дизайн в Москве"), item("ремонт в Кирове")], scope)
        assert [i["text"] for i in result.eligible] == ["дизайн в Москве"]
        assert result.count(selection.REJECT_NO_TEXT_MATCH) == 1

    def test_text_scope_all(self):
        scope = selection.build_scope({"keywords_list": ["Киров", "дизайн"], "keywords_match": "all"})
        result = selection.select_items([item("дизайн в Кирове")], scope)
        assert result.eligible == []
        scope = selection.build_scope({"keywords_list": ["Кирове", "дизайн"], "keywords_match": "all"})
        result = selection.select_items([item("дизайн в Кирове"), item("дизайн в Москве")], scope)
        assert [i["text"] for i in result.eligible] == ["дизайн в Кирове"]

    def test_phrase_is_not_stemmed(self):
        """An inflected form is a different word: the operator names each form."""
        scope = selection.build_scope({"keywords_list": ["Киров"]})
        result = selection.select_items([item("в Кирове"), item("из Кирова"), item("Киров")], scope)
        assert [i["text"] for i in result.eligible] == ["Киров"]

    def test_phrase_is_whole_and_case_insensitive_with_yo(self):
        scope = selection.build_scope({"keywords_list": ["Елена", "киров"]})
        result = selection.select_items([item("привет, ЛЕНА"), item("Елена и Киров"), item("ёлкa")], scope)
        assert [i["text"] for i in result.eligible] == ["Елена и Киров"]

    def test_text_scope_reads_author_name_and_permalink(self):
        scope = selection.build_scope({"keywords_list": ["кабанов", "kabanov"]})
        by_author = item("пост", author={"platform": "vk", "id": 5, "name": "Александр Кабанов"})
        by_link = item("пост", permalink="https://vk.com/wall-1_2?from=kabanov")
        result = selection.select_items([by_author, by_link], scope)
        assert len(result.eligible) == 2

    def test_author_filter_is_applied_before_text(self):
        scope = selection.build_scope({"tracked_users": ["vk:1"], "keywords_list": ["Италия"]})
        result = selection.select_items(
            [item("Италия", author={"platform": "vk", "id": 2}), item("просто", author={"platform": "vk", "id": 1})],
            scope,
        )
        assert result.count(selection.REJECT_AUTHOR_NOT_TRACKED) == 1
        assert result.count(selection.REJECT_NO_TEXT_MATCH) == 1
        assert result.eligible == []

    def test_tracked_plus_text_scope(self):
        scope = selection.build_scope({"tracked_users": ["vk:1"], "keywords_list": ["Италия"]})
        result = selection.select_items(
            [
                item("Италия, Милан", author={"platform": "vk", "id": 1}),
                item("Киров", author={"platform": "vk", "id": 1}),
                item("Италия", author={"platform": "vk", "id": 2}),
            ],
            scope,
        )
        assert [i["text"] for i in result.eligible] == ["Италия, Милан"]
        assert result.count(selection.PASS_TEXT_MATCHED) == 1

    def test_malformed_item_is_not_eligible(self):
        result = selection.select_items(["raw text", None], selection.build_scope({}))
        assert result.eligible == []
        assert result.count(selection.REJECT_AUTHOR_NOT_TRACKED) == 2

    def test_content_types_are_out_of_scope(self):
        """Selection never decides content type; the scenario does."""
        scope = selection.build_scope({"content_types": ["comments"], "tracked_users": ["vk:1"]})
        result = selection.select_items(
            [item("пост", author={"platform": "vk", "id": 1}), item("комментарий", author={"platform": "vk", "id": 1})],
            scope,
        )
        assert len(result.eligible) == 2


class TestStemmedWordForms:
    """Селекция понимает словоформы: разные падежи одного слова — одна тема.

    `app/utils/ru_stem.py` сводит оба конца к общему стем-ключу, поэтому
    «Италия» находит «Италию»/«Италии», а «поездка» — «поездку». Стем — это
    ключ матчинга, а не лемма: «дизайн» не расширяется до «дизайнер».
    """

    def test_italy_wordforms_match_one_keyword(self):
        scope = selection.build_scope({"keywords_list": ["Италия"]})
        result = selection.select_items(
            [
                item("Поездка в Италию"),
                item("Отдых в Италии"),
                item("Италия, Милан"),
                item("отпуск на Кипре"),
            ],
            scope,
        )
        assert [i["text"] for i in result.eligible] == ["Поездка в Италию", "Отдых в Италии", "Италия, Милан"]
        assert result.count(selection.REJECT_NO_TEXT_MATCH) == 1

    def test_keyword_in_oblique_case_matches_nominative_item(self):
        scope = selection.build_scope({"keywords_list": ["Италию"]})
        result = selection.select_items([item("Италия — это про еду"), item("Италию не планировали")], scope)
        assert [i["text"] for i in result.eligible] == ["Италия — это про еду", "Италию не планировали"]

    def test_italy_inflected_keyword_matches_another_inflected_item(self):
        scope = selection.build_scope({"keywords_list": ["Италии"]})
        result = selection.select_items([item("говорили об Италии"), item("поехали в Италию")], scope)
        assert len(result.eligible) == 2

    def test_trip_wordforms_match(self):
        scope = selection.build_scope({"keywords_list": ["поездка"]})
        result = selection.select_items(
            [item("рассказ про поездку"), item("поездка в горы"), item("поезд отходит в 8")],
            scope,
        )
        assert [i["text"] for i in result.eligible] == ["рассказ про поездку", "поездка в горы"]

    def test_design_does_not_match_designer(self):
        """Стем-ключи «дизайн» и «дизайнер» разные: слово не расширяется."""
        scope = selection.build_scope({"keywords_list": ["дизайн"]})
        result = selection.select_items(
            [item("ищу дизайнера в студию"), item("дизайн интерьера")], scope
        )
        assert [i["text"] for i in result.eligible] == ["дизайн интерьера"]

    def test_designer_keyword_does_not_match_design(self):
        scope = selection.build_scope({"keywords_list": ["дизайнер"]})
        result = selection.select_items([item("дизайн в Москве")], scope)
        assert result.eligible == []
