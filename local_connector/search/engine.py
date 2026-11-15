"""Authorized local FTS5 search engine with bm25 ranking, filters, snippets and ACL checks."""

from __future__ import annotations

import mimetypes
import sqlite3
from collections.abc import Callable, Iterable
from pathlib import Path

from ..index import IndexEngine, IndexStore
from ..paths import DEFAULT_EXCLUDED_NAMES, SelectedRoot
from .acl import normalize_folder_prefixes, verify_root_and_file_access
from .models import (
    MAX_HITS,
    LocalFileReference,
    LocalSearchHit,
    LocalSearchResult,
    RootRegistration,
    SearchError,
    SearchProtocolError,
    SearchQuery,
)
from .snippets import build_fts5_match_query, extract_snippet

_BATCH_SIZE = 200
_MAX_SCANNED_CHUNKS = 4000


def _split_sheet_cell_anchor(sheet_anchor: str | None) -> tuple[str | None, str | None]:
    if not sheet_anchor:
        return None, None
    if "!" in sheet_anchor:
        sheet, _, cell = sheet_anchor.rpartition("!")
        if sheet and cell and cell.replace("$", "").isalnum():
            return sheet, cell
    return sheet_anchor, None


def _matches_file_type(file_type: str, relative_path: str, allowed_types: tuple[str, ...]) -> bool:
    if not allowed_types:
        return True
    suffix = Path(relative_path).suffix.casefold().lstrip(".")
    mime = (file_type or "").casefold()
    guessed = (mimetypes.guess_type(relative_path)[0] or "").casefold()
    for raw_target in allowed_types:
        if type(raw_target) is not str:
            continue
        target = raw_target.strip().casefold()
        if not target:
            continue
        if target.endswith("/*"):
            prefix = target[:-1]
            if mime.startswith(prefix) or guessed.startswith(prefix):
                return True
        elif target.startswith("."):
            if suffix == target.lstrip("."):
                return True
        elif "/" in target:
            if mime == target or guessed == target:
                return True
        else:
            if suffix == target or mime == target:
                return True
    return False


def _matches_folders(relative_path: str, prefixes: tuple[tuple[str, ...], ...]) -> bool:
    if not prefixes:
        return True
    parts = tuple(part.casefold() for part in Path(relative_path).parts[:-1])
    return any(parts[: len(prefix)] == prefix for prefix in prefixes)


def _bm25_to_wire_rank(bm25_score: float) -> int:
    """Convert SQLite FTS5 bm25 (more negative = better) to non-negative wire rank (higher = better)."""

    positive = max(0.0, -float(bm25_score))
    return min(2_147_483_647, max(1, int(round(positive * 1000.0))) if positive > 0 else 0)


class LocalSearchService:
    """Authorized query API over a private IndexStore with live OS ACL enforcement."""

    def __init__(
        self,
        store: IndexStore,
        roots: Iterable[RootRegistration] = (),
        *,
        excluded_paths: Iterable[str] = (),
        excluded_names: Iterable[str] = DEFAULT_EXCLUDED_NAMES,
        folder_policy: object | None = None,
        os_access_check: Callable[[Path, object], bool] | None = None,
    ) -> None:
        self.store = store
        self._roots_by_id: dict[str, RootRegistration] = {}
        self._roots_by_hash: dict[str, RootRegistration] = {}
        self.excluded_paths = tuple(excluded_paths)
        self.excluded_names = frozenset(name.casefold() for name in excluded_names)
        self.folder_policy = folder_policy
        self.os_access_check = os_access_check
        self.set_roots(roots)

    def set_roots(self, roots: Iterable[RootRegistration]) -> None:
        by_id: dict[str, RootRegistration] = {}
        by_hash: dict[str, RootRegistration] = {}
        for item in roots:
            if not isinstance(item, RootRegistration):
                raise SearchError("roots must be RootRegistration instances")
            if item.root_id in by_id:
                raise SearchError("duplicate root_id registration")
            root_hash = IndexEngine._root_hash(item.selected)
            if root_hash in by_hash:
                raise SearchError("duplicate root directory registration")
            by_id[item.root_id] = item
            by_hash[root_hash] = item
        self._roots_by_id = by_id
        self._roots_by_hash = by_hash

    def register_root(self, registration: RootRegistration) -> None:
        current = [item for item in self._roots_by_id.values() if item.root_id != registration.root_id]
        current.append(registration)
        self.set_roots(current)

    def root_registrations(self) -> tuple[RootRegistration, ...]:
        return tuple(self._roots_by_id.values())

    def root_index_counts(self, root_ids: Iterable[str]) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self.store.lock:
            for root_id in root_ids:
                reg = self._roots_by_id.get(root_id)
                if reg is None:
                    continue
                root_hash = IndexEngine._root_hash(reg.selected)
                row = self.store.connection.execute(
                    "SELECT COUNT(*) FROM files WHERE root_hash = ?",
                    (root_hash,),
                ).fetchone()
                counts[root_id] = int(row[0]) if row else 0
        return counts

    def verify_reference_access(self, root_id: str, relative_path: str, *, cloud_egress: bool) -> bool:
        """Re-check registration, boundary, consent, folder policy and live OS access."""

        if self.store.pause_reason == "deleted":
            return False
        reg = self._roots_by_id.get(root_id)
        if reg is None:
            return False
        if cloud_egress and (reg.boundary != "company" or not reg.cloud_consented):
            return False
        return (
            verify_root_and_file_access(
                reg,
                relative_path,
                excluded_paths=self.excluded_paths,
                excluded_names=self.excluded_names,
                folder_policy=self.folder_policy,
                os_access_check=self.os_access_check,
            )
            is not None
        )

    def _select_target_roots(
        self,
        requested_roots: tuple[str, ...],
        *,
        cloud_egress: bool,
    ) -> tuple[list[RootRegistration], list[str]]:
        denied: list[str] = []
        if requested_roots:
            selected: list[RootRegistration] = []
            for root_id in requested_roots:
                reg = self._roots_by_id.get(root_id)
                if reg is None:
                    denied.append(root_id)
                    continue
                if cloud_egress and (reg.boundary != "company" or not reg.cloud_consented):
                    denied.append(root_id)
                    continue
                selected.append(reg)
            if cloud_egress and denied:
                # Fail closed when any explicitly requested cloud root lacks consent or is local_only.
                raise SearchProtocolError("denied")
            return selected, denied

        if cloud_egress:
            raise SearchProtocolError("denied")

        return list(self._roots_by_id.values()), denied

    def search(self, query: SearchQuery, *, cloud_egress: bool = False) -> LocalSearchResult:
        if not isinstance(query, SearchQuery):
            raise SearchError("query must be a SearchQuery instance")

        if self.store.pause_reason == "deleted":
            return LocalSearchResult(hits=(), partial=False)

        target_regs, denied_roots = self._select_target_roots(query.filters.roots, cloud_egress=cloud_egress)
        if not target_regs:
            return LocalSearchResult(hits=(), partial=bool(denied_roots), denied_roots=tuple(denied_roots))

        verified_hashes: list[str] = []
        partial = bool(denied_roots)
        for reg in target_regs:
            try:
                reg.selected.verify()
            except Exception:
                partial = True
                denied_roots.append(reg.root_id)
                continue
            verified_hashes.append(IndexEngine._root_hash(reg.selected))

        if not verified_hashes:
            return LocalSearchResult(hits=(), partial=True, denied_roots=tuple(dict.fromkeys(denied_roots)))

        match_expr = build_fts5_match_query(query.query)
        if match_expr is None:
            return LocalSearchResult(hits=(), partial=partial, denied_roots=tuple(dict.fromkeys(denied_roots)))

        folder_prefixes = normalize_folder_prefixes(query.filters.folders)
        after_ns = query.filters.effective_modified_after_ns()
        before_ns = query.filters.effective_modified_before_ns()
        min_size = query.filters.min_size
        max_size = query.filters.max_size

        placeholders = ",".join("?" for _ in verified_hashes)
        sql = f"""
            SELECT f.path_hash, f.root_hash, f.relative_path, f.size, f.mtime_ns,
                   f.content_hash, f.file_type, f.author, f.document_created,
                   f.document_modified, f.notes,
                   c.chunk_index, c.text, c.page_anchor, c.sheet_anchor,
                   bm25(chunks_fts) AS bm25_score
            FROM chunks_fts
            JOIN chunks AS c ON c.id = chunks_fts.rowid
            JOIN files AS f ON f.path_hash = c.path_hash
            WHERE chunks_fts MATCH ? AND f.root_hash IN ({placeholders})
            ORDER BY bm25(chunks_fts) ASC, c.chunk_index ASC
            LIMIT ? OFFSET ?
        """

        hits: list[LocalSearchHit] = []
        seen_files: set[str] = set()
        offset = 0
        scanned = 0
        more_exist = False

        while scanned < _MAX_SCANNED_CHUNKS and len(hits) <= query.limit:
            with self.store.lock:
                if self.store.pause_reason == "deleted":
                    return LocalSearchResult(hits=(), partial=False)
                try:
                    rows = self.store.connection.execute(
                        sql,
                        (match_expr, *verified_hashes, _BATCH_SIZE, offset),
                    ).fetchall()
                except sqlite3.OperationalError as exc:
                    raise SearchError("invalid search query") from exc

            if not rows:
                break
            offset += len(rows)
            scanned += len(rows)

            for row in rows:
                path_hash = str(row["path_hash"])
                if path_hash in seen_files:
                    continue
                seen_files.add(path_hash)

                root_hash = str(row["root_hash"])
                reg = self._roots_by_hash.get(root_hash)
                if reg is None:
                    continue
                if cloud_egress and (reg.boundary != "company" or not reg.cloud_consented):
                    continue

                relative_path = str(row["relative_path"])
                file_type = str(row["file_type"])
                if not _matches_file_type(file_type, relative_path, query.filters.file_types):
                    continue
                if not _matches_folders(relative_path, folder_prefixes):
                    continue

                live_stat = verify_root_and_file_access(
                    reg,
                    relative_path,
                    excluded_paths=self.excluded_paths,
                    excluded_names=self.excluded_names,
                    folder_policy=self.folder_policy,
                    os_access_check=self.os_access_check,
                )
                if live_stat is None:
                    continue

                live_size = int(live_stat.st_size)
                live_mtime_ns = int(live_stat.st_mtime_ns)
                if min_size is not None and live_size < min_size:
                    continue
                if max_size is not None and live_size > max_size:
                    continue
                if after_ns is not None and live_mtime_ns < after_ns:
                    continue
                if before_ns is not None and live_mtime_ns > before_ns:
                    continue

                if len(hits) >= query.limit:
                    more_exist = True
                    break

                include_snippet = len(hits) < query.max_snippets and query.max_snippet_chars > 0
                snippet = (
                    extract_snippet(
                        str(row["text"]),
                        query.query,
                        max_chars=query.max_snippet_chars,
                    )
                    if include_snippet
                    else None
                )

                sheet_anchor, cell_anchor = _split_sheet_cell_anchor(row["sheet_anchor"])
                allow_abs = query.allow_absolute_paths and reg.allow_absolute_paths and not cloud_egress
                abs_path = str(reg.selected.path.joinpath(*Path(relative_path).parts)) if allow_abs else None

                bm25_score = float(row["bm25_score"])
                ref = LocalFileReference(
                    object_id=path_hash,
                    root_id=reg.root_id,
                    relative_path=relative_path,
                    absolute_path=abs_path,
                    page_anchor=str(row["page_anchor"]) if row["page_anchor"] is not None else None,
                    sheet_anchor=sheet_anchor,
                    cell_anchor=cell_anchor,
                    content_hash=str(row["content_hash"]),
                    mtime_ns=live_mtime_ns,
                    document_modified=int(row["document_modified"]) if row["document_modified"] is not None else None,
                    file_type=file_type,
                    size=live_size,
                )
                hits.append(
                    LocalSearchHit(
                        reference=ref,
                        rank=_bm25_to_wire_rank(bm25_score),
                        score=max(0.0, -bm25_score),
                        bm25=bm25_score,
                        snippet=snippet,
                        data_boundary=reg.boundary,
                    )
                )

            if len(rows) < _BATCH_SIZE or more_exist:
                break

        if scanned >= _MAX_SCANNED_CHUNKS and not more_exist:
            more_exist = True

        return LocalSearchResult(
            hits=tuple(hits[: min(query.limit, MAX_HITS)]),
            partial=partial or more_exist,
            denied_roots=tuple(dict.fromkeys(denied_roots)),
        )
