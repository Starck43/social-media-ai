"""
Scenario preferences — what the owner kept choosing, stored in `agent_memory`.

The chat agent creates scenarios one conversation at a time, so the choices a
person makes twice (grouping by days, always posts + comments, the brands they
track) are re-asked every time. They are written here under the
`scenario_prefs` scope and read back as defaults for the next draft, in the web
wizard's first step and in `scenario_create`.

Plain `agent_memory` rows rather than a new table: the store is already a
per-workspace key/value with provenance and it already feeds the system prompt,
so a preference learned from a chat is visible to the model without another
plumbing path. Values are stored as JSON text — `value` is a `Text` column, and
a list must survive the round-trip as a list.

See `docs/CHAT_BOT_SCENARIOS.md#agent-memory-for-preferences`.
"""

from __future__ import annotations

import json
from typing import Any

#: `agent_memory.scope` that groups the scenario preferences.
SCOPE = "scenario_prefs"

#: Preference keys and what they mean. `default_language` is kept for the same
#: reason the docs list it: it is the one preference the agent reasons about
#: outside a draft (which language to answer in), not a draft field.
KEYS: tuple[str, ...] = ("default_language", "preferred_analyze_type", "brands")

#: Preference keys `apply_to_draft` fills. `default_language` is not a scenario
#: field, so it is remembered but never auto-filled into a draft.
DRAFT_KEYS: tuple[str, ...] = ("preferred_analyze_type", "brands")


async def remember(**prefs: Any) -> dict[str, Any]:
    """Store the given preferences; `None` values are skipped, not deleted.

    Only keys from `KEYS` are accepted, and only strings, lists of strings and
    numbers are stored — a preference is something small a person chose, not an
    arbitrary blob. Returns what was written, for the tool result.
    """
    from app.models.managers.agent_memory_manager import agent_memory

    written: dict[str, Any] = {}
    for key, value in prefs.items():
        if key not in KEYS:
            continue
        if value in (None, "", [], {}):
            continue
        if not isinstance(value, (str, int, float, list)):
            continue
        payload = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        await agent_memory.write(key, payload, scope=SCOPE, source="manual")
        written[key] = value
    return written


async def load() -> dict[str, Any]:
    """Every stored preference, JSON-decoded back to its original shape."""
    from app.models.managers.agent_memory_manager import agent_memory

    stored = await agent_memory.as_dict(scope=SCOPE)
    prefs: dict[str, Any] = {}
    for key, raw in stored.items():
        prefs[key] = _decode(raw)
    return prefs


async def apply_to_draft(draft: Any) -> Any:
    """Fill a draft's empty fields from remembered preferences.

    Only empty fields are filled: an explicit choice from this conversation
    always outranks a remembered one, or re-opening the wizard would silently
    undo what the person just picked. Returns the same draft for chaining.
    """
    prefs = await load()
    if not draft.analyze_type:
        draft.analyze_type = prefs.get("preferred_analyze_type")
    brands = prefs.get("brands")
    if brands:
        competitor = dict((draft.scope or {}).get("competitor") or {})
        brand_names = competitor.get("brand_names") or competitor.get("competitor_list") or []
        merged = [b for b in brand_names if b]
        merged += [b for b in brands if b and b not in merged]
        if merged:
            draft.scope = {**(draft.scope or {}), "competitor": {**competitor, "brand_names": merged}}
    return draft


def _decode(raw: Any) -> Any:
    """JSON-decoded value, or the raw string when it is not JSON."""
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw
