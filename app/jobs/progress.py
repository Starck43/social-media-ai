"""Bounded per-source progress for long collect/analyze runs (run-status modal).

The handler reports one step per source; the snapshot is written to the running
Job's `result["progress"]` under the same claim predicates as the heartbeat, so
a stale or cancelled generation can never write progress. Progress is a display
hint, not an outcome: the final `result` replaces it, and a failed write never
fails the run. A write that finds no matching running claim stops the handler
at the next source boundary (same meaning as heartbeat claim loss).

Top-level imports are stdlib-only: the dispatcher tests load this module as-is.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional, Sequence

logger = logging.getLogger(__name__)

TRACKED_JOB_TYPES = frozenset({"collect", "analyze"})
MAX_ITEMS = 50
MAX_NAME = 80
_STATES = frozenset({"done", "running", "waiting", "skipped"})
_OUTCOMES = frozenset({"collected", "empty", "error", "auth_required", "rate_limited"})
# Only bounded counters/flags; never content hashes, hints, messages or payloads.
_INT_KEYS = ("items", "new_items", "analyzed", "actions")
_RAW_KEYS = ("raw_total", "raw_selected", "raw_processed")
_FLAG_KEYS = ("collection_error",)


class ProgressStopped(RuntimeError):
    """The running generation is no longer owned; stop before the next source."""

    def __init__(self):
        super().__init__("Job claim no longer owned; progress stopped.")


def _name(value: Any, fallback: str) -> str:
    text = value if type(value) is str and value.strip() else fallback
    return text.strip()[:MAX_NAME]


def _count(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= 10**9 else None


def _item(source: Any, state: str, entry: dict | None) -> dict[str, Any]:
    source_id = getattr(source, "id", None)
    item: dict[str, Any] = {
        "source_id": source_id if type(source_id) is int and source_id > 0 else None,
        "name": _name(getattr(source, "name", None), f"Источник {source_id}" if source_id else "Источник"),
        "state": state,
    }
    if entry:
        outcome = entry.get("outcome")
        if type(outcome) is str and outcome in _OUTCOMES:
            item["outcome"] = outcome
        for key in (*_INT_KEYS, *_RAW_KEYS):
            count = _count(entry.get(key))
            if count is not None:
                item[key] = count
        if entry.get("error") is True or outcome == "error":
            item["error"] = True
        if entry.get("collection_error") is True:
            item["collection_error"] = True
    return item


def build_snapshot(
    sources: Sequence[Any],
    position: int,
    per_source: Sequence[dict],
    *,
    phase: str = "processing",
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Snapshot before handling `sources[position]`; earlier ones are finished."""
    total = len(sources)
    position = max(0, min(position, total))
    latest: dict[int, dict] = {}
    for entry in per_source or ():
        if isinstance(entry, dict) and type(entry.get("source_id")) is int:
            latest[entry["source_id"]] = entry
    items = []
    for index, source in enumerate(sources):
        if index < position:
            entry = latest.get(getattr(source, "id", None))
            items.append(_item(source, "done" if entry else "skipped", entry))
        elif index == position:
            items.append(_item(source, "running", None))
        else:
            items.append(_item(source, "waiting", None))
    first = 0
    if total > MAX_ITEMS:
        # Keep the current source visible with a few finished ones before it.
        first = max(0, min(position - 10, total - MAX_ITEMS))
    return {
        "phase": phase if phase in {"collection", "analysis"} else "processing",
        "total": total,
        "completed": position,
        "current": items[position]["name"] if position < total else None,
        "first": first,
        "items": items[first:first + MAX_ITEMS],
        "updated_at": (now or datetime.now(timezone.utc)).isoformat(),
    }


async def _default_writer(claim: Any, snapshot: dict) -> bool:
    from app.models.managers.job_manager import JobManager

    return await JobManager().record_progress(claim, snapshot)


async def _default_settler(claim: Any) -> Any:
    from app.models.managers.job_manager import JobManager

    return await JobManager().settle_cancelled_discard(claim)


class ProgressSession:
    """One claimed generation's progress writer; bound by the dispatcher."""

    def __init__(
        self,
        claim: Any,
        *,
        writer: Callable[[Any, dict], Awaitable[Any]] | None = None,
        settler: Callable[[Any], Awaitable[Any]] | None = None,
    ):
        self.claim = claim
        self.calls = 0
        self.lost = False
        self.phase = "processing"
        self._writer = writer or _default_writer
        self._settler = settler or _default_settler

    @property
    def tracked(self) -> bool:
        return getattr(self.claim, "job_type", None) in TRACKED_JOB_TYPES

    async def step(self, sources: Sequence[Any], per_source: Sequence[dict]) -> None:
        if self.lost:
            raise ProgressStopped()
        if not self.tracked:
            return
        position = self.calls
        self.calls += 1
        snapshot = build_snapshot(sources, position, per_source, phase=self.phase)
        try:
            written = await self._writer(self.claim, snapshot)
        except Exception as error:  # noqa: BLE001 — progress must never fail a run
            logger.warning(
                "job_progress_write_failed job_id=%s error_kind=%s",
                getattr(self.claim, "job_id", None),
                type(error).__name__[:40],
            )
            return
        if written is False:
            self.lost = True
            raise ProgressStopped()

    async def update_current(self, sources: Sequence[Any], per_source: Sequence[dict], current: dict) -> None:
        """Refresh current-source counters without advancing source position."""
        if self.lost:
            raise ProgressStopped()
        if not self.tracked:
            return
        snapshot = build_snapshot(sources, max(0, self.calls - 1), per_source, phase=self.phase)
        item = next((entry for entry in snapshot["items"] if entry.get("source_id") == current.get("source_id")), None)
        if item is not None:
            item["state"] = "running"
            for key in (*_RAW_KEYS, *_FLAG_KEYS):
                count = _count(current.get(key))
                if key in _FLAG_KEYS:
                    if current.get(key) is True:
                        item[key] = True
                elif count is not None:
                    item[key] = count
        try:
            written = await self._writer(self.claim, snapshot)
        except ProgressStopped:
            raise
        except Exception as error:  # noqa: BLE001 — progress must never fail a run
            logger.warning("job_progress_write_failed job_id=%s error_kind=%s", getattr(self.claim, "job_id", None), type(error).__name__[:40])
            return
        if written is False:
            self.lost = True
            raise ProgressStopped()

    async def settle(self) -> None:
        """After a stopped collect run: finish an operator's discard choice."""
        if getattr(self.claim, "job_type", None) != "collect":
            return
        try:
            await self._settler(self.claim)
        except Exception as error:  # noqa: BLE001 — cleanup is best effort
            logger.warning(
                "job_cancel_settle_failed job_id=%s error_kind=%s",
                getattr(self.claim, "job_id", None),
                type(error).__name__[:40],
            )

    async def begin_phase(self, phase: str) -> None:
        """Start a distinct source pass without carrying its completed count."""
        if self.lost:
            raise ProgressStopped()
        self.phase = phase if phase in {"collection", "analysis"} else "processing"
        self.calls = 0


_current: ContextVar[Optional[ProgressSession]] = ContextVar("job_progress_session", default=None)


def bind(session: ProgressSession):
    return _current.set(session)


def unbind(token) -> None:
    _current.reset(token)


async def step(sources: Sequence[Any], per_source: Sequence[dict]) -> None:
    """Handler hook: call once at the top of each per-source iteration."""
    session = _current.get()
    if session is not None:
        await session.step(sources, per_source)


async def update_current(sources: Sequence[Any], per_source: Sequence[dict], current: dict) -> None:
    session = _current.get()
    if session is not None:
        await session.update_current(sources, per_source, current)


async def begin_phase(phase: str) -> None:
    session = _current.get()
    if session is not None:
        await session.begin_phase(phase)


def read_snapshot(result: Any, attempts: Any) -> dict[str, Any] | None:
    """Validated progress of the current attempt, or None (web side, pure)."""
    if not isinstance(result, dict):
        return None
    progress = result.get("progress")
    if not isinstance(progress, dict) or type(attempts) is not int or progress.get("attempt") != attempts:
        return None
    total, completed = _count(progress.get("total")), _count(progress.get("completed"))
    first = _count(progress.get("first")) or 0
    raw_items = progress.get("items")
    if total is None or completed is None or completed > total or not isinstance(raw_items, list):
        return None
    items = []
    for raw in raw_items[:MAX_ITEMS]:
        if not isinstance(raw, dict) or type(raw.get("state")) is not str or raw["state"] not in _STATES:
            continue
        item: dict[str, Any] = {"name": _name(raw.get("name"), "Источник"), "state": raw["state"]}
        if type(raw.get("source_id")) is int and raw["source_id"] > 0:
            item["source_id"] = raw["source_id"]
        if type(raw.get("outcome")) is str and raw["outcome"] in _OUTCOMES:
            item["outcome"] = raw["outcome"]
        for key in _INT_KEYS:
            count = _count(raw.get(key))
            if count is not None:
                item[key] = count
        for key in _RAW_KEYS:
            count = _count(raw.get(key))
            if count is not None:
                item[key] = count
        if raw.get("error") is True:
            item["error"] = True
        if raw.get("collection_error") is True:
            item["collection_error"] = True
        items.append(item)
    current = progress.get("current")
    return {
        "phase": progress.get("phase") if progress.get("phase") in {"collection", "analysis"} else "processing",
        "total": total,
        "completed": completed,
        "first": first,
        "current": _name(current, "") if type(current) is str else None,
        "items": items,
        "updated_at": progress.get("updated_at") if type(progress.get("updated_at")) is str else None,
    }
