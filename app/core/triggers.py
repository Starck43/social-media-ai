"""The trigger conditions, described once.

Each condition is declared here with its config keys and defaults, and the
declaration is what the runtime evaluates (`TriggerEvaluator`), what the web
editor renders, and what `describe()` puts in a prompt. One place, so a hint
cannot describe a rule the evaluator does not apply — or the reverse.

The conditions belong to a *task*: a task says when to look and what to do about
a match, while a scenario only says how to analyse. See `docs/AGENT_TASKS.md`.
"""

from dataclasses import dataclass, field
from typing import Any

from app.types import BotTriggerType

# Phase tells the user what a condition costs. `TEXT` decides from the raw post
# without a model call; `ANALYSIS` needs the result, so it can only run after.
PHASE_TEXT = "text"
PHASE_ANALYSIS = "analysis"


@dataclass(frozen=True)
class TriggerSpec:
    """One condition: its phase, its config keys and their defaults."""

    trigger_type: BotTriggerType
    # Which phase decides: on the raw text, or on the stored analysis.
    phase: str
    # config key -> default. The keys are what `trigger_config` is expected to
    # hold; anything absent falls back to the value here.
    defaults: dict[str, Any] = field(default_factory=dict)
    # One sentence, for the editor hint and for the prompt.
    summary: str = ""

    def config_for(self, config: dict[str, Any] | None) -> dict[str, Any]:
        """`config` with the declared defaults filled in."""
        merged = dict(self.defaults)
        if config:
            merged.update(config)
        return merged

    def describe(self) -> str:
        """Human-readable form, e.g. `любой из: жалоба, срочно`."""
        if not self.defaults:
            return self.summary
        parts = [f"{key}={self.config_for(None)[key]!r}" for key in self.defaults]
        return f"{self.summary} ({', '.join(parts)})" if self.summary else ", ".join(parts)


def _keywords(config: dict[str, Any]) -> list[str]:
    """Configured keywords, normalised to a lowercased list."""
    raw = config.get("keywords") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(k).strip() for k in raw if str(k).strip()]


# Whole-word, case-insensitive: a keyword must not match inside another word,
# which would make "art" fire on "article".
def _word_in(text: str, keyword: str) -> bool:
    import re

    return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text, re.IGNORECASE) is not None


def match_keywords(text: str, config: dict[str, Any] | None) -> bool:
    """KEYWORD_MATCH. An empty keyword list means no filtering."""
    keywords = _keywords((config or {}))
    if not keywords:
        return True
    # `match` is the documented key; `mode` is what the runtime and the saved
    # configs actually use. Both are read so neither one silently stops
    # filtering — a stored `mode: "all"` that is ignored would turn an
    # "all of these words" rule into "any of them".
    mode = (config or {}).get("match") or (config or {}).get("mode") or "any"
    haystack = text or ""
    hits = [kw for kw in keywords if _word_in(haystack, kw)]
    if mode == "all":
        return len(hits) == len(keywords)
    return bool(hits)


def match_mentions(text: str, config: dict[str, Any] | None) -> bool:
    """USER_MENTION. Names match literally, with or without a leading `@`."""
    usernames = (config or {}).get("usernames") or []
    if isinstance(usernames, str):
        usernames = [usernames]
    cleaned = [str(u).strip().lstrip("@").lower() for u in usernames if str(u).strip()]
    if not cleaned:
        return True
    haystack = (text or "").lower()
    return any(name in haystack for name in cleaned)


def sentiment_threshold(result: dict[str, Any], config: dict[str, Any] | None) -> bool:
    """SENTIMENT_THRESHOLD. Decides on the stored analysis, so it runs after it."""
    cfg = dict(config or {})
    threshold = float(cfg.get("threshold", 0.5))
    direction = cfg.get("direction", "below")
    score = _sentiment_score(result)
    if score is None:
        return True  # nothing to judge on: do not silently drop the content
    return score < threshold if direction == "below" else score > threshold


def _sentiment_score(result: dict[str, Any]) -> float | None:
    """The score out of whatever the analysis stored."""
    sentiment = (result or {}).get("sentiment")
    if isinstance(sentiment, dict):
        for key in ("score", "overall_score", "average_score", "sentiment_score"):
            value = sentiment.get(key)
            if value is not None:
                return float(value)
        return None
    if isinstance(sentiment, (int, float)):
        return float(sentiment)
    return None


SPECS: dict[BotTriggerType, TriggerSpec] = {
    BotTriggerType.KEYWORD_MATCH: TriggerSpec(
        BotTriggerType.KEYWORD_MATCH,
        PHASE_TEXT,
        {"keywords": [], "match": "any"},
        "срабатывает при ключевых словах в тексте",
    ),
    BotTriggerType.USER_MENTION: TriggerSpec(
        BotTriggerType.USER_MENTION,
        PHASE_TEXT,
        {"usernames": []},
        "срабатывает при упоминании",
    ),
    BotTriggerType.SENTIMENT_THRESHOLD: TriggerSpec(
        BotTriggerType.SENTIMENT_THRESHOLD,
        PHASE_ANALYSIS,
        {"threshold": 0.5, "direction": "below"},
        "срабатывает по тональности результата",
    ),
    BotTriggerType.ACTIVITY_SPIKE: TriggerSpec(
        BotTriggerType.ACTIVITY_SPIKE,
        PHASE_TEXT,
        {"baseline_period_hours": 24, "spike_multiplier": 3.0},
        "срабатывает при всплеске активности",
    ),
}


def describe(trigger_type: BotTriggerType | None, config: dict[str, Any] | None = None) -> str:
    """One-line description of a configured trigger, for a hint or a prompt."""
    if not trigger_type:
        return "Триггер не задан — контент проходит без фильтрации"
    spec = SPECS.get(trigger_type)
    if spec is None:
        return str(trigger_type)
    return spec.describe() if config else spec.summary or str(trigger_type)
