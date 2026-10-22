"""The runtime's own lifecycle for a task's mention-search phrases.

The task's «Что искать» phrases become its SEARCH lanes on their own: every
collect run makes sure the lanes exist and are linked BEFORE the sources are
read, so a fresh task collects its phrases in the same run instead of waiting
for a hand-made search source. The create / reuse / refuse / link rules live
in ONE place — `lane_migration.ensure_query_lanes`, the runtime counterpart
of the reviewed lane migration; this module only derives WHICH lanes the
intent asks for and renders the bounded operator note.

`queries_match="all"` collapses the phrases into ONE lane whose query is the
phrases joined by a space — the platform's own conjunction. The exact
all-phrases conjunction over a post's text is enforced by the text scope
(`keywords_list` + `keywords_match`), never by the platform query.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from app.services.collection.intent import MATCH_ALL

if TYPE_CHECKING:
    from app.services.collection.intent import CollectionIntent

# A note is one operator sentence; refuse/missing lists stay a bounded sample.
MAX_NOTE = 200
MAX_TEXT = 160


def mention_lane_queries(intent: "CollectionIntent") -> tuple[str, ...]:
    """The phrases as lanes: one per phrase (`any`), one joined lane (`all`)."""
    if intent.queries_match == MATCH_ALL:
        # One search instead of several: the platform query ANDs the words
        # itself. The exact all-phrases conjunction over the post text is
        # enforced by the text scope (`keywords_list` + `keywords_match`),
        # not by this query.
        return (" ".join(intent.queries),) if intent.queries else ()
    return intent.queries


def _brief(text: Any, limit: int = MAX_TEXT) -> str:
    return " ".join(str(text or "").split())[:limit]


def mention_note(summary: Mapping[str, Any]) -> str:
    """One bounded operator sentence for the mention-lane step's evidence."""
    code = summary.get("error_code")
    if code:
        hint = summary.get("error_hint") or "причина неизвестна"
        return f"Ветки поиска упоминаний не обеспечены: {hint} ({code})"[:MAX_NOTE]
    total = int(summary.get("total") or 0)
    missing = len(summary.get("missing") or [])
    if summary.get("dry_run"):
        base = (
            f"Фразы упоминаний: без веток {missing} из {total}"
            if missing
            else f"Фразы упоминаний: ветки готовы ({total})"
        )
        return f"{base}; сухой прогон ничего не создаёт"[:MAX_NOTE]
    skipped = missing + len(summary.get("refused") or [])
    tail = f", пропущено {skipped}" if skipped else ""
    return (
        f"Фразы упоминаний: создано веток {int(summary.get('created') or 0)}, "
        f"переиспользовано {int(summary.get('reused') or 0)}{tail}"
    )[:MAX_NOTE]
