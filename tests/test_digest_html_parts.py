"""Deterministic HTML boundaries and complete frozen-list verification."""

from copy import deepcopy
from html import unescape
from html.parser import HTMLParser

import pytest

from app.services.digest.checkpoints import CheckpointError, new_checkpoint, reconstruct_html_parts, verify_parts
from app.services.digest.html_parts import PART_LIMIT, SPLITTER_VERSION, HtmlPartsError, split_digest_html
from app.services.digest.render import render_digest


class Parsed(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.stack, self.visible, self.styled = [], [], []
        self.feed(text)
        self.close()
        assert not self.stack

    def handle_starttag(self, tag, attrs):
        assert tag in {"b", "i", "blockquote"} and attrs == []
        self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack and self.stack.pop() == tag

    def handle_data(self, text):
        self.visible.append(text)
        self.styled.extend((char, tuple(self.stack)) for char in text)


def visible(text):
    return "".join(Parsed(text).visible)


@pytest.mark.parametrize(
    "text",
    [
        "plain text",
        "<b>bold</b> and <i>italic</i>",
        "<blockquote>summary &amp; detail</blockquote>",
        "<b><i>nested</i></b>",
        "&lt;escaped&gt; &#128512; &#x1F600; &quot;",
        "prefix\n\n suffix\n",
    ],
)
def test_small_inputs_unchanged(text):
    assert split_digest_html(text) == [text]


@pytest.mark.parametrize("limit", [32, 60, 127, 4000])
@pytest.mark.parametrize("wrapper", ["b", "i", "blockquote"])
def test_long_styled_text_preserves_visible_text_and_whitespace(limit, wrapper):
    text = f"<{wrapper}>" + ("word 😀 &amp; &lt;x&gt;\n\n" * 210) + f"</{wrapper}>"
    parts = split_digest_html(text, limit=limit)
    assert len(parts) > 1
    assert all(len(p.encode("utf-16-le")) // 2 <= limit for p in parts)
    assert "".join(visible(p) for p in parts) == visible(text)
    assert parts == split_digest_html(text, limit=limit)


def test_nested_styles_are_reopened_without_losing_text():
    text = "<blockquote><b>" + ("a😀" * 80) + "<i>" + (" &amp; b" * 80) + "</i></b></blockquote>"
    parts = split_digest_html(text, limit=90)
    assert all(len(p.encode("utf-16-le")) // 2 <= 90 for p in parts)
    assert "".join(visible(p) for p in parts) == visible(text)


def test_actual_renderer_with_long_summary_and_escaped_values():
    text = render_digest(
        {
            "title": "A < B & C",
            "period_start": "2026-10-01",
            "period_end": "2026-10-08",
            "brief": "## title\n**Период:** today\n## **Nested heading**\n- *line*\n" * 80,
        },
        summary="Long 😀 < > & summary " * 650,
    )
    parts = split_digest_html(text)
    assert len(parts) > 3
    assert all(len(p.encode("utf-16-le")) // 2 <= PART_LIMIT for p in parts)
    assert "".join(visible(p) for p in parts) == visible(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "  ",
        "<b></b>",
        "<b>missing",
        "</b>wrong",
        "<b><i>x</b></i>",
        "<a href='x'>link</a>",
        "<b class='x'>b</b>",
        "<script>x</script>",
        "<!-- comment -->x",
        "a & unknown",
        "&nbsp;",
        "&#0;",
        "&#xD800;",
        "&#1114112;",
        "\ud800",
        "<B>x</B>",
    ],
)
def test_invalid_or_unsupported_input_fails_closed(text):
    with pytest.raises(HtmlPartsError):
        split_digest_html(text)


@pytest.mark.parametrize("limit", [0, -1, True, 4001, "40"])
def test_invalid_limit_fails_closed(limit):
    with pytest.raises(HtmlPartsError):
        split_digest_html("text", limit=limit)


def test_indivisible_wrappers_or_entities_cannot_be_truncated():
    with pytest.raises(HtmlPartsError):
        split_digest_html("<blockquote>x</blockquote>", limit=10)
    with pytest.raises(HtmlPartsError):
        split_digest_html("&amp;", limit=4)


def frozen():
    content = "<b>" + "frozen 😀 &amp; text " * 500 + "</b>"
    parts = split_digest_html(content)
    state = new_checkpoint(
        run_id=1,
        tenant_id=2,
        generation="fixed",
        content=content,
        splitter=SPLITTER_VERSION,
        targets=[
            {"binding_id": 1, "channel": "telegram", "destination_id": "1", "parts": parts},
            {"binding_id": 2, "channel": "max", "destination_id": "2", "parts": parts},
        ],
    )
    return content, parts, state


def test_reconstruction_verifies_every_target_before_returning_parts():
    content, parts, state = frozen()
    assert reconstruct_html_parts(state, content) == parts
    changed = deepcopy(state)
    changed["targets"][1]["parts"][-1]["sha256"] = "0" * 64
    with pytest.raises(CheckpointError):
        reconstruct_html_parts(changed, content)


@pytest.mark.parametrize("change", ["shorter", "longer", "reordered", "changed"])
def test_full_list_cannot_drop_tail_reorder_or_add_parts(change):
    _, parts, state = frozen()
    if change == "shorter":
        parts = parts[:-1]
    elif change == "longer":
        parts = parts + ["extra"]
    elif change == "reordered":
        parts = list(reversed(parts))
    else:
        parts[0] += "modified"
    with pytest.raises(CheckpointError):
        verify_parts(state, 0, parts)


def test_unknown_splitter_or_changed_snapshot_has_no_fallback():
    content, _, state = frozen()
    unknown = deepcopy(state)
    unknown["splitter"] = "future-version"
    with pytest.raises(CheckpointError):
        reconstruct_html_parts(unknown, content)
    with pytest.raises(CheckpointError):
        reconstruct_html_parts(state, content + " extra")


def test_utf16_codepoints_and_entities_remain_atomic():
    text = "😀&amp;" * 12
    parts = split_digest_html(text, limit=9)
    assert all(len(p.encode("utf-16-le")) // 2 <= 9 for p in parts)
    assert "".join(unescape(p) for p in parts) == unescape(text)


def test_final_newlines_are_kept_with_visible_content_not_empty_message():
    text = "<blockquote>" + "word 😀 &amp; &lt;x&gt;\n\n" * 210 + "</blockquote>"
    parts = split_digest_html(text, limit=60)
    assert all(visible(part).strip() for part in parts)
    assert "".join(visible(part) for part in parts) == visible(text)


def test_unrepresentable_long_whitespace_is_not_discarded():
    with pytest.raises(HtmlPartsError):
        split_digest_html("x" + " " * 100, limit=30)


def test_original_formatting_is_preserved_across_reopened_boundaries():
    text = "prefix <b>" + "bold 😀 " * 90 + "<i>" + "nested &amp; " * 90 + "</i></b> suffix"
    parts = split_digest_html(text, limit=80)
    assert [styled for part in parts for styled in Parsed(part).styled] == Parsed(text).styled


def test_custom_limit_cannot_masquerade_as_default_frozen_version():
    content, _, state = frozen()
    different = split_digest_html(content, limit=2000)
    with pytest.raises(CheckpointError):
        verify_parts(state, 0, different)
