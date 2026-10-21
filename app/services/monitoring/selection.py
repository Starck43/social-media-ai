"""Task selection: which collected items a task is allowed to analyse.

Pure functions only — no database, no platform client, no provider call — so the
scope can be tested and reasoned about in isolation. The task, not a source and
not a scenario, owns the selection (see `docs/design/task-selection.md`):

1. sources — applied by the caller when it reads staged rows;
2. tracked users — an author filter, `platform:external_id`, never a collection
   target and never an instruction to read someone's personal wall;
3. text scope — whole phrases an item must contain.

Content types are *not* handled here: the task's collection intent already
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
        haystack_stems[index : index + width] == phrase_stems for index in range(len(haystack_stems) - width + 1)
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


def author_matches(item: Any, scope: SelectionScope) -> bool:
    """True when the item's author passes the task's author filter.

    The text scope is deliberately not applied: comment expansion decides which
    parents to read *before* the thread is evaluated, and a post whose own text
    does not match can still carry the matching comment that made the task
    comment-enabled.
    """
    if not scope.has_author_filter:
        return True
    if not isinstance(item, Mapping):
        return False
    tracked = {user.key() for user in scope.tracked_users}
    return bool(_author_keys(item) & tracked)


def eligible_posts(items: Iterable[Any], scope: SelectionScope | None = None) -> list[Any]:
    """Posts whose dependent lanes (comments, engagement) may be read.

    Only the author layer applies. The text scope stays a decision about the
    post *and* its thread, and an engagement lane only needs to know whether the
    post belongs to the task's population at all.
    """
    resolved = scope or SelectionScope()
    return [
        item
        for item in items
        if isinstance(item, Mapping)
        and str(item.get("content_type") or "post").lower() != "comment"
        and author_matches(item, resolved)
    ]


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


# ---------------------------------------------------------------------------
# Collection-time decisions
# ---------------------------------------------------------------------------
#
# Stage C moves the deterministic selection to collection. The analyser reads
# only stored rows, so it needs a per-item status/reason, not just aggregate
# eligible/rejected counts. `select_items` above remains the legacy analysis
# path for rows staged before this stage; `select_for_collection` is the new
# collection path and also supports a thread-level pass for post + comments.

STATUS_SELECTED = "selected"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_REJECTED = "rejected"

THREAD_MATCHED = "thread_matched"
THREAD_COMMENT_MATCHED = "thread_comment_matched"

# A city is not a post attribute: it is declared by a profile, so it can be
# absent, and absence is neither a pass nor a silent drop. `prefer` keeps a
# known mismatch as a ranked, selected row and does NOT let an unknown city
# block the item (a profile that declares no city is normal); `review` is the
# mode that sends the unknown city to an operator; `require` is the only mode
# that rejects a known mismatch.
CITY_PREFER = "prefer"
CITY_REQUIRE = "require"
CITY_REVIEW = "review"
CITY_MODES = (CITY_PREFER, CITY_REQUIRE, CITY_REVIEW)

CITY_MATCHED = "city_matched"
CITY_MISMATCH = "city_mismatch"
CITY_UNKNOWN = "city_unknown"


def normalise_city(value: Any) -> str:
    """Comparison key for a self-declared city: case, `ё` and spacing do not matter."""
    if not isinstance(value, str):
        return ""
    return _normalise(value)


@dataclass(frozen=True)
class CityScope:
    """The task's city preference, compared against a profile-declared city."""

    name: str
    mode: str = CITY_PREFER

    def __post_init__(self) -> None:
        if not normalise_city(self.name):
            raise SelectionError("city_name_invalid")
        if self.mode not in CITY_MODES:
            raise SelectionError("city_mode_invalid")

    @property
    def key(self) -> str:
        return normalise_city(self.name)

    def matches(self, value: Any) -> bool:
        return normalise_city(value) == self.key


def city_decision(item: Any, city: CityScope) -> tuple[str, str]:
    """Collection status/reason for one item's author-declared city.

    Only `require` rejects a known mismatch. An unknown city never means
    rejected either: `prefer` keeps the item selected (the reason says the city
    is unknown, not that it was rejected — a community without a declared city
    is normal and must not park every post outside analysis), while `review`
    and `require` send the unknown city to an operator instead.
    """
    value = _author_city(item)
    if value is None:
        if city.mode in (CITY_REQUIRE, CITY_REVIEW):
            return STATUS_NEEDS_REVIEW, CITY_UNKNOWN
        return STATUS_SELECTED, CITY_UNKNOWN
    if city.matches(value):
        return STATUS_SELECTED, CITY_MATCHED
    if city.mode == CITY_REQUIRE:
        return STATUS_REJECTED, CITY_MISMATCH
    return STATUS_SELECTED, CITY_MISMATCH


def _author_city(item: Any) -> str | None:
    """The city an item or its author declares, if any."""
    if not isinstance(item, Mapping):
        return None
    author = item.get("author")
    if isinstance(author, Mapping):
        for key in ("city", "city_name"):
            value = author.get(key)
            if isinstance(value, str) and value.strip():
                return value
    value = item.get("city")
    return value if isinstance(value, str) and value.strip() else None


@dataclass(frozen=True)
class SelectionDecision:
    """One item's collection-time outcome."""

    item: Any
    status: str
    reason: str


@dataclass
class CollectionSelection:
    """Per-item decisions plus the aggregate counters/reasons."""

    decisions: list[SelectionDecision] = field(default_factory=list)
    selected: list[Any] = field(default_factory=list)
    needs_review: list[Any] = field(default_factory=list)
    rejected: list[Any] = field(default_factory=list)
    reasons: dict[str, int] = field(default_factory=dict)
    unknown_tracked_users: list[str] = field(default_factory=list)
    # Items whose declared city proved the task's city. Kept apart from
    # `reasons`: a match does not replace the pass reason that admitted the item.
    city_matched: int = 0

    def count(self, reason: str) -> int:
        return self.reasons.get(reason, 0)


def select_for_collection(
    items: Iterable[Any],
    scope: SelectionScope,
    *,
    thread: bool = False,
    city: CityScope | None = None,
) -> CollectionSelection:
    """Apply task selection before staging and return per-item statuses.

    ``thread=True`` groups a post with its comments by ``external_id`` /
    ``parent_external_id``. If any member matches, the whole thread is kept;
    a matching comment can therefore keep a non-matching parent post, which is
    the point of a comment-enabled task. Items whose parent is absent from the
    batch are evaluated as their own group.

    ``city`` adds the author-city layer *after* the author and text layers: an
    item that already failed those is reported by the layer that rejected it.
    The city belongs to the post's author, so a thread is decided by its post,
    not by whichever commenter happened to match.
    """
    materialised = list(items)
    tracked = {user.key() for user in scope.tracked_users}
    matched_tracked: set[str] = set()
    city_matched = 0
    if thread:
        groups: dict[str, list[Any]] = {}
        for item in materialised:
            groups.setdefault(_thread_key(item), []).append(item)
        decisions: list[SelectionDecision] = []
        for group in groups.values():
            evaluations = [_evaluate_item(item, scope, tracked) for item in group]
            matched = any(passed for passed, _reason, _keys in evaluations)
            city_status, city_reason = _group_city(group, city)
            if matched and city_status == STATUS_NEEDS_REVIEW:
                decisions.extend(
                    SelectionDecision(item=item, status=STATUS_NEEDS_REVIEW, reason=CITY_UNKNOWN) for item in group
                )
                continue
            if matched and city_status == STATUS_REJECTED:
                decisions.extend(
                    SelectionDecision(item=item, status=STATUS_REJECTED, reason=CITY_MISMATCH) for item in group
                )
                continue
            for item, (passed, reason, keys) in zip(group, evaluations):
                matched_tracked |= keys
                if matched:
                    kept_reason = (
                        CITY_MISMATCH
                        if city_reason == CITY_MISMATCH
                        else (
                            CITY_UNKNOWN
                            if city_reason == CITY_UNKNOWN
                            else (THREAD_COMMENT_MATCHED if _is_comment(item) and passed else THREAD_MATCHED)
                        )
                    )
                    decisions.append(SelectionDecision(item=item, status=STATUS_SELECTED, reason=kept_reason))
                else:
                    decisions.append(SelectionDecision(item=item, status=STATUS_REJECTED, reason=reason))
            if matched and city_reason == CITY_MATCHED:
                city_matched += 1
    else:
        decisions = []
        for item in materialised:
            passed, reason, keys = _evaluate_item(item, scope, tracked)
            matched_tracked |= keys
            status = STATUS_SELECTED if passed else STATUS_REJECTED
            kept_reason = reason
            if passed and city is not None:
                city_status, city_reason = city_decision(item, city)
                if city_status == STATUS_NEEDS_REVIEW:
                    status, kept_reason = STATUS_NEEDS_REVIEW, CITY_UNKNOWN
                elif city_status == STATUS_REJECTED:
                    status, kept_reason = STATUS_REJECTED, CITY_MISMATCH
                elif city_reason == CITY_MISMATCH:
                    kept_reason = CITY_MISMATCH
                elif city_reason == CITY_UNKNOWN:
                    # `prefer`: kept, but the outcome says the city is unknown
                    # instead of silently pretending the city matched.
                    kept_reason = CITY_UNKNOWN
                else:
                    city_matched += 1
            decisions.append(SelectionDecision(item=item, status=status, reason=kept_reason))
    result = _aggregate_decisions(decisions, tracked, matched_tracked)
    result.city_matched = city_matched
    return result


def _group_city(group: Sequence[Any], city: CityScope | None) -> tuple[str, str]:
    """City outcome for a whole thread, decided by its post when one is present."""
    if city is None:
        return STATUS_SELECTED, ""
    subject = next((item for item in group if not _is_comment(item)), group[0])
    return city_decision(subject, city)


def _evaluate_item(item: Any, scope: SelectionScope, tracked: set[str]) -> tuple[bool, str, set[str]]:
    if not isinstance(item, Mapping):
        return False, REJECT_AUTHOR_NOT_TRACKED, set()
    matched_keys: set[str] = set()
    if scope.has_author_filter:
        keys = _author_keys(item)
        matched = keys & tracked
        if not matched:
            return False, REJECT_AUTHOR_NOT_TRACKED, matched_keys
        matched_keys |= matched
    if scope.has_text_scope:
        haystack = _item_text(item)
        hits = [phrase for phrase in scope.keywords if _phrase_in(haystack, phrase)]
        passed = len(hits) == len(scope.keywords) if scope.match == MATCH_ALL else bool(hits)
        if not passed:
            return False, REJECT_NO_TEXT_MATCH, matched_keys
        return True, PASS_TEXT_MATCHED, matched_keys
    return True, PASS_AUTHOR_TRACKED if scope.has_author_filter else PASS_NO_TRACKED_USERS, matched_keys


def _aggregate_decisions(
    decisions: list[SelectionDecision],
    tracked: set[str],
    matched_tracked: set[str],
) -> CollectionSelection:
    result = CollectionSelection()
    for decision in decisions:
        result.decisions.append(decision)
        result.reasons[decision.reason] = result.reasons.get(decision.reason, 0) + 1
        if decision.status == STATUS_SELECTED:
            result.selected.append(decision.item)
        elif decision.status == STATUS_NEEDS_REVIEW:
            result.needs_review.append(decision.item)
        else:
            result.rejected.append(decision.item)
    if tracked:
        result.unknown_tracked_users = sorted(tracked - matched_tracked)
    return result


def _is_comment(item: Any) -> bool:
    return isinstance(item, Mapping) and str(item.get("content_type") or "").lower() == "comment"


def _thread_key(item: Any) -> str:
    if not isinstance(item, Mapping):
        return f"item:{id(item)}"
    external = item.get("external_id") or item.get("id") or id(item)
    if _is_comment(item):
        parent = item.get("parent_external_id")
        if parent:
            return f"thread:{parent}"
    return f"thread:{external}"
