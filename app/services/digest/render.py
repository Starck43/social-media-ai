"""HTML rendering for digests (Telegram HTML / MAX html)."""

from __future__ import annotations

import html as html_escape
from typing import Any

from app.channels.max import MAX_TEXT_LEN
from app.channels.telegram import split_message


def escape(text: str) -> str:
    return html_escape.escape(str(text), quote=False)


def render_digest(data: dict[str, Any], summary: str | None = None) -> str:
    """
    Render digest parts into HTML message.

    data keys: title, period_start, period_end, stats (dict), sentiment (dict),
    topics (list[dict]), engagement (dict|None), content_mix (dict|None), llm (dict|None)
    """
    parts: list[str] = []
    parts.append(f"<b>{escape(data.get('title', 'Digest'))}</b>")
    parts.append(f"<i>{escape(str(data['period_start']))} — {escape(str(data['period_end']))}</i>")

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

    llm = data.get("llm") or {}
    if llm:
        parts.append(f"<i>Model: {escape(str(llm.get('model', '—')))}</i>")

    return "\n\n".join(p for p in parts if p)


def render_plain(data: dict[str, Any]) -> str:
    """Fallback plain-text render (no HTML), used when channels reject HTML."""
    lines = [f"{data.get('title', 'Digest')}", f"{data.get('period_start')} — {data.get('period_end')}"]
    for section in ("stats", "sentiment", "topics", "engagement", "content_mix", "llm"):
        value = data.get(section)
        if value:
            lines.append(f"{section}: {value}")
    return "\n".join(str(line) for line in lines)


def split_digest(text: str) -> list[str]:
    """Split digest into channel-safe chunks (max 4000 to fit MAX)."""
    return split_message(text, limit=min(MAX_TEXT_LEN, 4000))
