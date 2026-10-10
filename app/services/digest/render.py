"""HTML rendering for digests (Telegram HTML / MAX html)."""

from __future__ import annotations

import html as html_escape
import re
from typing import Any

from app.channels.max import MAX_TEXT_LEN
from app.channels.telegram import split_message


def escape(text: str) -> str:
    return html_escape.escape(str(text), quote=False)


_MD_NUMBERED = re.compile(r"^(\d+)\.\s+(.*)$")


def _inline_md(text: str) -> str:
    """Escape a line, then turn **bold** and *italic* into HTML tags."""
    out = escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", out)
    return out


def _md_to_html(text: str) -> str:
    """Render the small Markdown subset the brief uses as Telegram/MAX HTML.

    Vocabulary: `## ` headings, `**bold**`, `*italic*`, `- ` bullets and
    `1. ` numbered items. Everything else is escaped verbatim.
    """
    lines = (text or "").splitlines()
    # The brief is self-contained (it also feeds the LLM narrative step), so it
    # carries its own title and period line — drop them here, render_digest
    # already printed the header above.
    if lines and lines[0].lstrip().startswith("## "):
        lines = lines[1:]
        if lines and lines[0].lstrip().startswith("**Период:**"):
            lines = lines[1:]

    out = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            out.append("")
            continue
        if stripped.startswith("## "):
            out.append(f"<b>{_inline_md(stripped[3:])}</b>")
        elif stripped.startswith("- "):
            out.append(f"• {_inline_md(stripped[2:])}")
        else:
            m = _MD_NUMBERED.match(stripped)
            if m:
                out.append(f"{m.group(1)}. {_inline_md(m.group(2))}")
            else:
                out.append(_inline_md(stripped))
    return "\n".join(out)


def render_digest(data: dict[str, Any], summary: str | None = None) -> str:
    """
    Render digest parts into HTML message.

    data keys: title, period_start, period_end, stats (dict), sentiment (dict),
    topics (list[dict]), engagement (dict|None), content_mix (dict|None),
    llm (dict|None), brief (str|None — the algorithmic Markdown body),
    coverage_note (str|None — deterministic limitations, separate from the LLM)
    """
    parts: list[str] = [
        f"<b>{escape(data.get('title', 'Digest'))}</b>",
        f"<i>{escape(str(data['period_start']))} — {escape(str(data['period_end']))}</i>",
    ]

    coverage_note = data.get("coverage_note")
    if coverage_note:
        parts.append(f"<b>Ограничения данных</b>\n{escape(coverage_note)}")

    stats = data.get("stats") or {}
    if stats:
        lines = [f"• {escape(str(k))}: {escape(str(v))}" for k, v in stats.items()]
        parts.append("<b>Overview</b>\n" + "\n".join(lines))

    sentiment = data.get("sentiment") or {}
    if sentiment:
        dist = sentiment.get("distribution") or {}
        emoji = {"positive": "🟢", "neutral": "⚪", "negative": "🔴"}
        lines = [f"{emoji.get(k, '•')} {escape(str(k))}: {escape(str(v))}" for k, v in dist.items() if v]
        if lines:
            parts.append("<b>Sentiment</b>\n" + "\n".join(lines))

    topics = data.get("topics") or []
    if topics:
        lines = []
        for i, t in enumerate(topics[:8], 1):
            topic = t.get("topic") or t.get("name") or "?"
            count = t.get("count") or t.get("weight") or ""
            suffix = f" ({escape(str(count))})" if count != "" else ""
            lines.append(f"{i}. {escape(str(topic))}{suffix}")
        parts.append("<b>Top topics</b>\n" + "\n".join(lines))

    if summary:
        parts.append(f"<blockquote>{escape(summary)}</blockquote>")

    brief = data.get("brief")
    if brief:
        parts.append(_md_to_html(brief))

    llm = data.get("llm") or {}
    if llm:
        parts.append(f"<i>Model: {escape(str(llm.get('model', '—')))}</i>")

    return "\n\n".join(p for p in parts if p)


def render_plain(data: dict[str, Any]) -> str:
    """Fallback plain-text render (no HTML), used when channels reject HTML."""
    lines = [f"{data.get('title', 'Digest')}", f"{data.get('period_start')} — {data.get('period_end')}"]
    coverage_note = data.get("coverage_note")
    if coverage_note:
        lines.append(f"Ограничения данных: {coverage_note}")
    for section in ("stats", "sentiment", "topics", "engagement", "content_mix", "llm"):
        value = data.get(section)
        if value:
            lines.append(f"{section}: {value}")
    return "\n".join(str(line) for line in lines)


def split_digest(text: str) -> list[str]:
    """Split digest into channel-safe chunks (max 4000 to fit MAX)."""
    return split_message(text, limit=min(MAX_TEXT_LEN, 4000))
