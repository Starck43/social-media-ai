"""DB-exempt unit tests for the ACL/boundary-aware snippet retrieval service (P19-T07).

Targets ``app/services/knowledge/retrieval.py`` pure functions: citation anchor
building, snippet construction, ACL/boundary filtering, term search and LLM
citation context assembly.  No database, network or paid provider is used.
"""

from datetime import datetime

import pytest

from app.services.knowledge import retrieval as mod

__source_only__ = True


def snippet(**overrides):
    base = {
        "id": "s1",
        "title": "Договор поставки",
        "source_name": "drive/contracts",
        "source": "drive/contracts",
        "acl_scope": "public",
        "data_boundary": "cloud",
        "boundary": "cloud",
        "citation_anchor": "drive/contracts/2026.pdf · p.3",
        "anchor": "drive/contracts/2026.pdf · p.3",
        "revision_hash": "abc123",
        "hash": "abc123",
        "synced_at": "2026-01-01T00:00:00+00:00",
        "excerpt": "Срок поставки — 30 дней.",
        "extraction_limit_note": "",
        "limit": "",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# build_citation_anchor
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("path_or_id", "subpart", "expected"),
    [
        ("drive/docs/a.pdf", None, "drive/docs/a.pdf"),
        ("drive/docs/a.pdf", "p.3", "drive/docs/a.pdf · p.3"),
        ("  drive/docs/a.pdf  ", "  p.3  ", "drive/docs/a.pdf · p.3"),
        ("mail:42", "", "mail:42"),
        ("mail:42", None, "mail:42"),
        (None, None, ""),
        ("", None, ""),
        (123, None, "123"),
        (123, 7, "123 · 7"),
    ],
)
def test_build_citation_anchor(path_or_id, subpart, expected):
    assert mod.build_citation_anchor("drive", path_or_id, subpart) == expected


def test_build_citation_anchor_ignores_source_type_and_is_deterministic():
    first = mod.build_citation_anchor("drive", "x/y.pdf", "p.1")
    second = mod.build_citation_anchor("mail", "x/y.pdf", "p.1")
    assert first == second == "x/y.pdf · p.1"


# --------------------------------------------------------------------------- #
# build_knowledge_snippet
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("boundary", ["local_only", "company", "cloud", "  CLOUD  ", "Company"])
def test_build_knowledge_snippet_normalizes_boundary(boundary):
    result = mod.build_knowledge_snippet("s1", "T", "S", "public", boundary, "anchor", "hash", "excerpt", "note")
    assert result["data_boundary"] == boundary.lower().strip()
    assert result["boundary"] == result["data_boundary"]


@pytest.mark.parametrize("boundary", ["", "public", "secret", "cluod"])
def test_build_knowledge_snippet_rejects_unknown_boundary(boundary):
    with pytest.raises(ValueError, match="Unknown evidence boundary"):
        mod.build_knowledge_snippet("s1", "T", "S", "public", boundary, "anchor", "hash", "excerpt", "note")


def test_build_knowledge_snippet_shape_and_aliases():
    result = mod.build_knowledge_snippet(
        snippet_id=7,
        title="Договор",
        source_name="drive",
        acl_scope="finance",
        data_boundary="company",
        citation_anchor="drive/a.pdf",
        revision_hash="deadbeef",
        excerpt="текст",
        extraction_limit_note="OCR page 1",
        synced_at="2026-01-01T00:00:00+00:00",
    )
    assert result == {
        "id": "7",
        "title": "Договор",
        "source_name": "drive",
        "source": "drive",
        "acl_scope": "finance",
        "data_boundary": "company",
        "boundary": "company",
        "citation_anchor": "drive/a.pdf",
        "anchor": "drive/a.pdf",
        "revision_hash": "deadbeef",
        "hash": "deadbeef",
        "synced_at": "2026-01-01T00:00:00+00:00",
        "excerpt": "текст",
        "extraction_limit_note": "OCR page 1",
        "limit": "OCR page 1",
    }
    assert all(isinstance(v, str) for v in result.values())


def test_build_knowledge_snippet_generates_utc_synced_at_when_absent():
    result = mod.build_knowledge_snippet("s1", "T", "S", "public", "cloud", "a", "h", "e", "")
    parsed = datetime.fromisoformat(result["synced_at"])
    assert parsed.tzinfo is not None
    assert parsed.utcoffset().total_seconds() == 0


def test_built_snippet_survives_filter_and_search():
    built = mod.build_knowledge_snippet(
        "s1", "Акт сверки", "drive", "public", "cloud", "drive/act.pdf", "h1", "суммы", ""
    )
    assert mod.filter_snippets_by_acl([built], None) == [built]
    assert mod.search_knowledge_snippets("акт", [built], None) == [built]


# --------------------------------------------------------------------------- #
# filter_snippets_by_acl
# --------------------------------------------------------------------------- #


def test_filter_empty_list_returns_empty():
    assert mod.filter_snippets_by_acl([], ["public"]) == []


def test_filter_none_scopes_allows_only_public():
    public = snippet(id="p", acl_scope="public")
    private = snippet(id="v", acl_scope="finance")
    assert mod.filter_snippets_by_acl([public, private], None) == [public]


def test_filter_matching_scope_and_public():
    public = snippet(id="p", acl_scope="public")
    finance = snippet(id="f", acl_scope="finance")
    legal = snippet(id="l", acl_scope="legal")
    result = mod.filter_snippets_by_acl([public, finance, legal], {"finance"})
    assert [s["id"] for s in result] == ["p", "f"]


@pytest.mark.parametrize("scope", ["*", "admin"])
def test_filter_wildcard_and_admin_scopes_see_all(scope):
    snippets = [
        snippet(id="p", acl_scope="public"),
        snippet(id="f", acl_scope="finance"),
        snippet(id="l", acl_scope="legal"),
    ]
    result = mod.filter_snippets_by_acl(snippets, {scope})
    assert [s["id"] for s in result] == ["p", "f", "l"]


def test_filter_empty_scopes_excludes_non_public():
    finance = snippet(id="f", acl_scope="finance")
    assert mod.filter_snippets_by_acl([finance], []) == []


@pytest.mark.parametrize(
    ("allowed", "expected_ids"),
    [
        ("cloud", ["local_only", "company", "cloud"]),
        ("company", ["local_only", "company"]),
        ("local_only", ["local_only"]),
        ("LOCAL_ONLY", ["local_only"]),
    ],
)
def test_filter_boundary_ceiling(allowed, expected_ids):
    snippets = [
        snippet(id="local_only", data_boundary="local_only", boundary="local_only"),
        snippet(id="company", data_boundary="company", boundary="company"),
        snippet(id="cloud", data_boundary="cloud", boundary="cloud"),
    ]
    result = mod.filter_snippets_by_acl(snippets, ["*"], allowed_boundary=allowed)
    assert [s["id"] for s in result] == expected_ids


def test_filter_cloud_snippet_excluded_from_local_only_query():
    cloud = snippet(id="cloud", data_boundary="cloud", boundary="cloud")
    assert mod.filter_snippets_by_acl([cloud], ["*"], allowed_boundary="local_only") == []
    assert mod.filter_snippets_by_acl([cloud], None, allowed_boundary="local_only") == []


@pytest.mark.parametrize("allowed", ["", "secret", "public", None])
def test_filter_invalid_allowed_boundary_returns_empty(allowed):
    snippets = [snippet(id="c", data_boundary="cloud", boundary="cloud")]
    assert mod.filter_snippets_by_acl(snippets, ["*"], allowed_boundary=allowed) == []


def test_filter_skips_snippets_with_unknown_or_missing_boundary():
    snippets = [
        snippet(id="bad", data_boundary="secret", boundary="secret"),
        snippet(id="missing", data_boundary=None, boundary=None),
        snippet(id="ok", data_boundary="cloud", boundary="cloud"),
    ]
    result = mod.filter_snippets_by_acl(snippets, ["*"])
    assert [s["id"] for s in result] == ["ok"]


def test_filter_uses_legacy_boundary_key_when_data_boundary_absent():
    legacy = {"id": "legacy", "acl_scope": "public", "boundary": "cloud"}
    assert mod.filter_snippets_by_acl([legacy], None) == [legacy]


def test_filter_strips_snippet_acl_whitespace_and_blank_not_public():
    spaces = snippet(id="sp", acl_scope=" finance ")
    blank = snippet(id="blank", acl_scope="   ")
    result = mod.filter_snippets_by_acl([spaces, blank], {"finance"})
    assert [s["id"] for s in result] == ["sp"]


def test_filter_preserves_order_and_returns_originals():
    first = snippet(id="a", acl_scope="public")
    second = snippet(id="b", acl_scope="public")
    result = mod.filter_snippets_by_acl([first, second], None)
    assert result[0] is first and result[1] is second


# --------------------------------------------------------------------------- #
# search_knowledge_snippets
# --------------------------------------------------------------------------- #


def test_search_none_snippets_returns_empty():
    assert mod.search_knowledge_snippets("anything", None, ["*"]) == []


@pytest.mark.parametrize("query", ["", "   ", None])
def test_search_blank_query_returns_all_permitted(query):
    snippets = [snippet(id="p"), snippet(id="f", acl_scope="finance")]
    assert mod.search_knowledge_snippets(query, snippets, ["*"]) == snippets


def test_search_matches_title_case_insensitively():
    snippets = [snippet(id="hit", title="ДОГОВОР аренды"), snippet(id="miss", title="Счёт")]
    result = mod.search_knowledge_snippets("договор", snippets, ["*"])
    assert [s["id"] for s in result] == ["hit"]


@pytest.mark.parametrize(
    "field",
    ["source_name", "citation_anchor", "excerpt", "revision_hash"],
)
def test_search_matches_each_searchable_field(field):
    needle = "needle-42"
    target = snippet(id="hit", **{field: f"prefix {needle} suffix"})
    other = snippet(
        id="miss", title="other", source_name="other", citation_anchor="other", excerpt="x", revision_hash="y"
    )
    result = mod.search_knowledge_snippets(needle, [target, other], ["*"])
    assert [s["id"] for s in result] == ["hit"]


def test_search_falls_back_to_legacy_source_and_anchor_keys():
    legacy = {
        "id": "legacy",
        "source": "legacy-drive",
        "anchor": "legacy-anchor.md",
        "acl_scope": "public",
        "boundary": "cloud",
    }
    assert mod.search_knowledge_snippets("anchor.md", [legacy], None) == [legacy]
    assert mod.search_knowledge_snippets("legacy-drive", [legacy], None) == [legacy]


def test_search_applies_acl_before_matching():
    public = snippet(id="p", title="секрет")
    private = snippet(id="v", title="секрет", acl_scope="finance")
    result = mod.search_knowledge_snippets("секрет", [public, private], None)
    assert [s["id"] for s in result] == ["p"]


def test_search_applies_boundary_before_matching():
    local = snippet(id="local", title="секрет", data_boundary="local_only", boundary="local_only")
    cloud = snippet(id="cloud", title="секрет")
    result = mod.search_knowledge_snippets("секрет", [local, cloud], ["*"], allowed_boundary="local_only")
    assert [s["id"] for s in result] == ["local"]


def test_search_no_match_returns_empty():
    assert mod.search_knowledge_snippets("absent", [snippet()], ["*"]) == []


# --------------------------------------------------------------------------- #
# assemble_llm_citations_context
# --------------------------------------------------------------------------- #


def test_assemble_empty_returns_empty_string():
    assert mod.assemble_llm_citations_context([], "cloud") == ""


def test_assemble_single_block_format():
    item = snippet(
        title="Договор",
        source_name="drive",
        citation_anchor="drive/a.pdf · p.3",
        revision_hash="abc123",
        data_boundary="cloud",
        excerpt="  Срок 30 дней.  ",
        extraction_limit_note="",
    )
    assert mod.assemble_llm_citations_context([item]) == (
        "[1] Договор — drive (drive/a.pdf · p.3 · abc123 · boundary:cloud)\n«Срок 30 дней.»"
    )


def test_assemble_appends_extraction_note():
    item = snippet(excerpt="текст", extraction_limit_note="OCR page 2")
    block = mod.assemble_llm_citations_context([item])
    assert block.endswith("\n[OCR/Extraction note: OCR page 2]")


def test_assemble_numbers_multiple_blocks_and_separates_with_blank_line():
    items = [snippet(id="a", title="A"), snippet(id="b", title="B", extraction_limit_note="note")]
    text = mod.assemble_llm_citations_context(items)
    blocks = text.split("\n\n")
    assert len(blocks) == 2
    assert blocks[0].startswith("[1] A — ")
    assert blocks[1].startswith("[2] B — ")


@pytest.mark.parametrize(
    ("allowed", "expected_titles"),
    [
        ("cloud", ["Local", "Company", "Cloud"]),
        ("company", ["Local", "Company"]),
        ("local_only", ["Local"]),
        ("LOCAL_ONLY", ["Local"]),
    ],
)
def test_assemble_filters_by_boundary_ceiling(allowed, expected_titles):
    items = [
        snippet(title="Local", data_boundary="local_only", boundary="local_only"),
        snippet(title="Company", data_boundary="company", boundary="company"),
        snippet(title="Cloud", data_boundary="cloud", boundary="cloud"),
    ]
    text = mod.assemble_llm_citations_context(items, allowed)
    assert text.count("\n\n") + (1 if text else 0) == len(expected_titles)
    for title in ("Local", "Company", "Cloud"):
        assert (f"] {title} — " in text) is (title in expected_titles)


@pytest.mark.parametrize("allowed", ["secret", "", "public"])
def test_assemble_unknown_allowed_boundary_excludes_everything(allowed):
    items = [snippet(data_boundary="cloud", boundary="cloud")]
    assert mod.assemble_llm_citations_context(items, allowed) == ""


def test_assemble_skips_snippet_with_unknown_boundary():
    items = [snippet(id="bad", title="Bad", data_boundary="secret", boundary="secret"), snippet(id="ok", title="Ok")]
    text = mod.assemble_llm_citations_context(items)
    assert text.startswith("[1] Ok — ")
    assert "Bad" not in text


def test_assemble_applies_defaults_for_missing_optional_fields():
    bare = {"data_boundary": "cloud"}
    assert mod.assemble_llm_citations_context([bare]) == "[1] Документ — Unknown ( ·  · boundary:cloud)\n«»"


def test_assemble_uses_legacy_aliases():
    legacy = {
        "title": "Legacy",
        "source": "legacy-src",
        "anchor": "legacy.md",
        "hash": "hh",
        "boundary": "company",
        "excerpt": "txt",
        "limit": "page 1",
    }
    text = mod.assemble_llm_citations_context([legacy], "company")
    assert "[1] Legacy — legacy-src (legacy.md · hh · boundary:company)" in text
    assert "[OCR/Extraction note: page 1]" in text


@pytest.mark.parametrize(
    ("allowed", "expected"),
    [("local_only", ["local_only"]), ("company", ["local_only", "company"]),
     ("cloud", ["local_only", "company", "cloud"])],
)
def test_search_to_citations_obeys_acl_and_boundary_ceiling(allowed, expected):
    items = [snippet(id=b, title=b, data_boundary=b, boundary=b) for b in ("local_only", "company", "cloud")]
    items.append(snippet(id="private", title="private", acl_scope="finance", data_boundary="local_only"))
    items.append(snippet(id="unknown", title="unknown", data_boundary="secret", boundary="secret"))
    hits = mod.search_knowledge_snippets("", items, None, allowed_boundary=allowed)
    assert [item["id"] for item in hits] == expected
    context = mod.assemble_llm_citations_context(hits, allowed_boundary=allowed)
    for title in ("local_only", "company", "cloud", "private", "unknown"):
        assert (f"] {title} — " in context) is (title in expected)


def test_assemble_invalid_and_missing_boundaries_fail_closed():
    items = [snippet(title="Invalid", data_boundary="secret"), snippet(title="Missing", data_boundary=None, boundary=None)]
    assert mod.assemble_llm_citations_context(items, "cloud") == ""
    assert mod.assemble_llm_citations_context([snippet()], None) == ""
