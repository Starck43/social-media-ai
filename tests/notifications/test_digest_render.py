"""Tests for digest rendering and splitting (no DB/network)."""

from datetime import date

from app.channels.max import split_message as max_split
from app.channels.telegram import split_message as tg_split
from app.services.digest.render import render_digest, render_plain, split_digest


class TestSplitMessage:
    def test_short_text_single_chunk(self):
        assert tg_split("hello") == ["hello"]
        assert max_split("hello") == ["hello"]

    def test_long_text_paragraph_boundaries(self):
        text = "\n\n".join(f"Paragraph {i} " + "x" * 80 for i in range(200))
        for fn in (tg_split, max_split):
            chunks = fn(text)
            assert len(chunks) > 1
            assert all(len(c) <= 4096 for c in chunks)
            assert "".join(chunks).replace("\n", "").startswith("Paragraph 0")

    def test_no_newlines_hard_cut(self):
        text = "a" * 10_000
        chunks = tg_split(text)
        assert all(len(c) <= 4096 for c in chunks)
        assert sum(len(c) for c in chunks) >= 9_900  # only newlines dropped

    def test_split_digest_respects_max_limit(self):
        text = "\n\n".join(f"Block {i} " + "y" * 100 for i in range(100))
        for chunk in split_digest(text):
            assert len(chunk) <= 4000


class TestRenderDigest:
    def _data(self):
        return {
            "title": "📊 Дайджест",
            "period": "day",
            "period_start": date(2026, 9, 22),
            "period_end": date(2026, 9, 22),
            "stats": {"analyses": 12, "content_items": 34},
            "sentiment": {"distribution": {"positive": 5, "neutral": 4, "negative": 3}},
            "topics": [{"topic": "release", "count": 4}, {"topic": "bugs", "count": 2}],
            "llm": {"model": "deepseek-chat"},
        }

    def test_full_render_contains_sections(self):
        out = render_digest(self._data(), summary="Всё спокойно")
        assert "<b>📊 Дайджест</b>" in out
        assert "Overview" in out
        assert "🟢 positive: 5" in out
        assert "release" in out
        assert "<blockquote>Всё спокойно</blockquote>" in out
        assert "deepseek-chat" in out

    def test_html_escaping(self):
        data = self._data()
        data["topics"] = [{"topic": "<script>alert(1)</script>", "count": 1}]
        out = render_digest(data)
        assert "<script>" not in out
        assert "&lt;script&gt;" in out

    def test_empty_data_renders_header_only(self):
        out = render_digest({"title": "X", "period_start": date(2026, 9, 22), "period_end": date(2026, 9, 22)})
        assert "<b>X</b>" in out
        assert "Overview" not in out

    def test_plain_render(self):
        out = render_plain(self._data())
        assert "Дайджест" in out
        assert "stats" in out
