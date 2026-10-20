"""Task selection: which collected items a task is allowed to analyse.

Pure functions only — no database, no platform client, no provider call — so the
scope can be tested and reasoned about in isolation. The task, not a source and
not a scenario, owns the selection (see `docs/design/task-selection.md`):

1. sources — applied by the caller when it reads staged rows;
2. tracked users — an author filter, `platform:external_id`, never a collection
   target and never an instruction to read someone's personal wall;
3. text scope — whole phrases an item must contain.

Content types are *not* handled here: the scenario's `content_types` already
decides what is collected and analysed, and selection must not widen or narrow
it. Without tracked users the source's own items pass layer 2 unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

# Authors considered by a task. Accepted as a mapping (`{"tracked_users": [...]}`)
# or as the list itself; both end up as normalised `platform:external_id` strings.
TRACKED_USERS_KEY = "tracked_users"
KEYWORDS_KEY = "keywords_list"
TOPICS_KEY = "topics_list"
MATCH_KEY = "keywords_match"

MATCH_ANY = "any"
MATCH_ALL = "all"
MATCH_MODES = (MATCH_ANY, MATCH_ALL)

# Pass reasons (why an item survives) and rejection reasons (why it does not).
PASS_NO_TRACKED_USERS = "no_tracked_users"
PASS_AUTHOR_TRACKED = "author_tracked"
PASS_NO_TEXT_SCOPE = "no_text_scope"
PASS_TEXT_MATCHED = "text_matched"

REJECT_AUTHOR_NOT_TRACKED = "author_not_tracked"
REJECT_NO_TEXT_MATCH = "no_text_match"

_PLATFORM_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


class SelectionError(ValueError):
    """Malformed selection configuration. Never silently dropped."""


@dataclass(frozen=True)
class TrackedUser:
    """One platform identity a task wants to see."""

    platform: str
    external_id: str

    def key(self) -> str:
        return f"{self.platform}:{self.external_id}"


@dataclass(frozen=True)
class SelectionScope:
    """The normalised selection of one task."""

    tracked_users: tuple[TrackedUser, ...] = ()
    keywords: tuple[str, ...] = ()
    match: str = MATCH_ANY

    @property
    def has_author_filter(self) -> bool:
        return bool(self.tracked_users)

    @property
    def has_text_scope(self) -> bool:
        return bool(self.keywords)


@dataclass
class SelectionResult:
    """Eligible items plus the per-item and aggregate evidence of selection.

    `reasons` counts every rejection and every pass, keyed by the constants
    above, so a run can report *why* an item was left out without storing its
    text. `unknown_tracked_users` lists configured identities that no candidate
    author matched: a task configured for people who never appear is usually a
    misconfiguration, not a quiet day.
    """

    eligible: list[Any] = field(default_factory=list)
    rejected: list[Any] = field(default_factory=list)
    reasons: dict[str, int] = field(default_factory=dict)
    unknown_tracked_users: list[str] = field(default_factory=list)

    def count(self, reason: str) -> int:
        return self.reasons.get(reason, 0)


def normalise_platform(value: Any) -> str:
    """Lowercase platform name, optionally tolerating a `vk.com`/`ВКонтакте` alias."""
    if not isinstance(value, str):
        raise SelectionError("tracked_user_platform_invalid")
    platform = value.strip().lower()
    if platform.startswith("@"):
        raise SelectionError("tracked_user_platform_invalid")
    if platform in {"vkontakte", "вконтакте", "vk.com"}:
        platform = "vk"
    elif platform in {"tg", "telegram.org", "телеграм"}:
        platform = "telegram"
    if not _PLATFORM_RE.match(platform):
        raise SelectionError("tracked_user_platform_invalid")
    return platform


def _external_id(raw: Any) -> str:
    """The platform's own identifier; leading `@` of a username is not part of it."""
    if not isinstance(raw, str):
        raise SelectionError("tracked_user_external_id_invalid")
    value = raw.strip().lstrip("@").strip()
    if not value or any(char.isspace() for char in value) or ":" in value:
        raise SelectionError("tracked_user_external_id_invalid")
    # A username-shaped id is lowercased; numeric ids are unaffected and keep
    # their sign (VK communities are negative, but tracked users are not
    # necessarily users — the caller resolves what the platform accepts).
    return value.lower() if not value.lstrip("-").isdigit() else value


def parse_tracked_user(raw: Any) -> TrackedUser:
    """`platform:external_id` (the stored form) or `{platform, external_id}`."""
    if isinstance(raw, Mapping):
        return TrackedUser(
            platform=normalise_platform(raw.get("platform")),
            external_id=_external_id(raw.get("external_id")),
        )
    if not isinstance(raw, str):
        raise SelectionError("tracked_user_invalid")
    text = raw.strip()
    if not text:
        raise SelectionError("tracked_user_invalid")
    # Split on the first colon only: Telegram ids may themselves contain one.
    platform, sep, external = text.partition(":")
    if not sep:
        raise SelectionError("tracked_user_platform_required")
    return TrackedUser(platform=normalise_platform(platform), external_id=_external_id(external))


def parse_tracked_users(value: Any) -> tuple[TrackedUser, ...]:
    """Normalise a task's tracked-user list, de-duplicated and order-preserved.

    A string is accepted as one entry, not split: the CLI splits its own input
    and models this config through the task payload contract. Malformed entries
    raise `SelectionError` so the task save fails visibly.
    """
    if value is None:
        return ()
    if isinstance(value, (str, Mapping)):
        entries: Sequence[Any] = [value]
    elif isinstance(value, Iterable):
        entries = list(value)
    else:
        raise SelectionError("tracked_users_invalid")
    seen: set[str] = set()
    result: list[TrackedUser] = []
    for entry in entries:
        if entry is None:
            # An empty form field is not a configured identity.
            continue
        user = parse_tracked_user(entry)
        if user.key() in seen:
            continue
        seen.add(user.key())
        result.append(user)
    return tuple(result)


def _phrases(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        entries = [value]
    elif isinstance(value, (list, tuple)):
        entries = list(value)
    else:
        raise SelectionError("selection_phrases_invalid")
    phrases: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, str):
            raise SelectionError("selection_phrases_invalid")
        phrase = " ".join(entry.split())
        key = _normalise(phrase)
        if not key or key in seen:
            continue
        seen.add(key)
        phrases.append(phrase)
    return tuple(phrases)


def build_scope(payload: Mapping[str, Any] | None) -> SelectionScope:
    """Selection from a task payload. Unknown keys are ignored on purpose."""
    data = payload or {}
    match = data.get(MATCH_KEY) or MATCH_ANY
    if match not in MATCH_MODES:
        raise SelectionError("keywords_match_invalid")
    # `topics_list` is the same scope under a second name: a topic a person
    # asked to see is a phrase, and two lists with two meanings would both be
    # silently "the model gets it as context".
    phrases = _phrases(data.get(KEYWORDS_KEY)) + _phrases(data.get(TOPICS_KEY))
    deduped: list[str] = []
    seen: set[str] = set()
    for phrase in phrases:
        key = _normalise(phrase)
        if key and key not in seen:
            seen.add(key)
            deduped.append(phrase)
    return SelectionScope(
        tracked_users=parse_tracked_users(data.get(TRACKED_USERS_KEY)),
        keywords=tuple(deduped),
        match=match,
    )


def _normalise(text: str) -> str:
    return " ".join((text or "").lower().replace("ё", "е").split())


def _phrase_in(haystack: str, phrase: str) -> bool:
    """Whole-phrase, word-bounded, case-insensitive, `ё` = `е`, inflection-aware.

    A literal word-bounded match wins first, so an exact phrase inside a longer
    word still means what it meant before. When no exact form occurs, both
    sides are reduced to their matching stems (`utils/ru_stem.py`), so «Италия»
    finds «Италию»/«Италии» and «поездка» finds «поездку». Stemming is a
    matching key, not a lemma: it never widens the phrase to a different word —
    «дизайн» still does not match «дизайнер» because their stems differ.
    """
    needle = _normalise(phrase)
    if not needle:
        return False
    if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None:
        return True
    from app.utils.ru_stem import stem_tokens

    phrase_stems = stem_tokens(needle)
    if not phrase_stems:
        return False
    haystack_stems = stem_tokens(haystack)
    width = len(phrase_stems)
    return any(
        haystack_stems[index : index + width] == phrase_stems
        for index in range(len(haystack_stems) - width + 1)
    )


def _item_text(item: Mapping[str, Any]) -> str:
    """Text a selection may read: the body, the author name and the permalink.

    A wall repost carries the author of the reposted item, not the wall: that
    is what makes a "who wrote about X" selection possible over carriers.
    """
    parts: list[str] = [str(item.get("text") or "")]
    author = item.get("author")
    if isinstance(author, Mapping):
        for key in ("name", "username", "screen_name"):
            value = author.get(key)
            if isinstance(value, str) and value:
                parts.append(value)
    permalink = item.get("permalink")
    if isinstance(permalink, str) and permalink:
        parts.append(permalink)
    return _normalise(" \n ".join(parts))


def _author_keys(item: Mapping[str, Any]) -> set[str]:
    """Platform identities an item can be recognised by.

    `author` carries the platform identity of the item's subject; `platform` is
    the item's own platform. Both spellings (`id`/`external_id`) are read so a
    snapshot from any client normaliser matches.
    """
    author = item.get("author")
    if not isinstance(author, Mapping):
        return set()
    platform = author.get("platform") or item.get("platform")
    if not isinstance(platform, str) or not platform:
        return set()
    try:
        normalised_platform = normalise_platform(platform)
    except SelectionError:
        return set()
    keys: set[str] = set()
    for field_name in ("external_id", "id", "screen_name", "username"):
        value = author.get(field_name)
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, int):
            keys.add(f"{normalised_platform}:{value}")
            continue
        if isinstance(value, str) and value.strip():
            try:
                keys.add(f"{normalised_platform}:{_external_id(value)}")
            except SelectionError:
                continue
    return keys


def select_items(items: Iterable[Any], scope: SelectionScope) -> SelectionResult:
    """Apply the author filter and the text scope, in that order.

    Order matters for diagnostics: an item from an untracked author is reported
    as such even if its text also would not match, so an empty result points at
    the layer that was actually too narrow.
    """
    tracked = {user.key() for user in scope.tracked_users}
    matched_tracked: set[str] = set()
    result = SelectionResult()

    def _record(container: list[Any], reason: str) -> None:
        result.reasons[reason] = result.reasons.get(reason, 0) + 1
        container.append(item)

    for item in items:
        if not isinstance(item, Mapping):
            # A malformed item cannot be proven eligible: reject it and keep it
            # visible as a selection outcome rather than silently analysing it.
            _record(result.rejected, REJECT_AUTHOR_NOT_TRACKED)
            continue
        if scope.has_author_filter:
            keys = _author_keys(item)
            matched = keys & tracked
            if not matched:
                _record(result.rejected, REJECT_AUTHOR_NOT_TRACKED)
                continue
            matched_tracked |= matched
        if scope.has_text_scope:
            haystack = _item_text(item)
            hits = [phrase for phrase in scope.keywords if _phrase_in(haystack, phrase)]
            passed = len(hits) == len(scope.keywords) if scope.match == MATCH_ALL else bool(hits)
            if not passed:
                _record(result.rejected, REJECT_NO_TEXT_MATCH)
                continue
            _record(result.eligible, PASS_TEXT_MATCHED)
            continue
        _record(result.eligible, PASS_AUTHOR_TRACKED if scope.has_author_filter else PASS_NO_TRACKED_USERS)

    if tracked:
        result.unknown_tracked_users = sorted(tracked - matched_tracked)
    return result
