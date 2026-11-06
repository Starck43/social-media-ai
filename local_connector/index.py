"""SQLite FTS5 metadata/content index and incremental scanner."""

from __future__ import annotations

import hashlib
import mimetypes
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .paths import DEFAULT_EXCLUDED_NAMES, PathSafetyError, canonicalize_root, iter_files, resolve_relative


@dataclass(frozen=True)
class Chunk:
    text: str
    page_anchor: str | None = None
    sheet_anchor: str | None = None


@dataclass(frozen=True)
class FileRecord:
    path_hash: str
    root_hash: str
    relative_path: str
    size: int
    mtime_ns: int
    content_hash: str
    file_type: str


@dataclass(frozen=True)
class ScanReport:
    indexed: int = 0
    skipped: int = 0
    deleted: int = 0
    errors: tuple[str, ...] = ()


class IndexStore:
    """Durable local index. Raw content never leaves this SQLite file."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path).expanduser()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database_path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    def _create_schema(self) -> None:
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS files (
                path_hash TEXT PRIMARY KEY,
                root_hash TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                file_type TEXT NOT NULL,
                indexed_at REAL NOT NULL,
                UNIQUE(root_hash, relative_path)
            );
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY,
                path_hash TEXT NOT NULL REFERENCES files(path_hash) ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                page_anchor TEXT,
                sheet_anchor TEXT,
                UNIQUE(path_hash, chunk_index)
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                text, content='chunks', content_rowid='id'
            );
            CREATE INDEX IF NOT EXISTS ix_chunks_path_hash ON chunks(path_hash);
            """)
        self.connection.commit()

    @staticmethod
    def path_hash(relative_path: str) -> str:
        return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()

    def close(self) -> None:
        self.connection.close()

    def get_by_hash(self, path_hash: str) -> FileRecord | None:
        row = self.connection.execute(
            "SELECT path_hash, root_hash, relative_path, size, mtime_ns, content_hash, file_type "
            "FROM files WHERE path_hash = ?",
            (path_hash,),
        ).fetchone()
        return FileRecord(**dict(row)) if row else None

    def replace_file(self, record: FileRecord, chunks: Sequence[Chunk]) -> None:
        with self.connection:
            old_ids = [
                row[0]
                for row in self.connection.execute("SELECT id FROM chunks WHERE path_hash = ?", (record.path_hash,))
            ]
            if old_ids:
                marks = ",".join("?" for _ in old_ids)
                self.connection.execute(f"DELETE FROM chunks_fts WHERE rowid IN ({marks})", old_ids)
            self.connection.execute("DELETE FROM chunks WHERE path_hash = ?", (record.path_hash,))
            self.connection.execute(
                """
                INSERT INTO files(path_hash, root_hash, relative_path, size, mtime_ns, content_hash, file_type, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path_hash) DO UPDATE SET
                    relative_path=excluded.relative_path, size=excluded.size,
                    mtime_ns=excluded.mtime_ns, content_hash=excluded.content_hash,
                    file_type=excluded.file_type, indexed_at=excluded.indexed_at
                """,
                (
                    record.path_hash,
                    record.root_hash,
                    record.relative_path,
                    record.size,
                    record.mtime_ns,
                    record.content_hash,
                    record.file_type,
                    time.time(),
                ),
            )
            for index, chunk in enumerate(chunks):
                cursor = self.connection.execute(
                    """
                    INSERT INTO chunks(path_hash, chunk_index, text, page_anchor, sheet_anchor)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (record.path_hash, index, chunk.text, chunk.page_anchor, chunk.sheet_anchor),
                )
                self.connection.execute(
                    "INSERT INTO chunks_fts(rowid, text) VALUES (?, ?)", (cursor.lastrowid, chunk.text)
                )

    def remove_paths_not_seen(self, seen_hashes: set[str]) -> int:
        rows = self.connection.execute("SELECT path_hash FROM files").fetchall()
        removed = [row[0] for row in rows if row[0] not in seen_hashes]
        with self.connection:
            for path_hash in removed:
                ids = [
                    row[0] for row in self.connection.execute("SELECT id FROM chunks WHERE path_hash = ?", (path_hash,))
                ]
                if ids:
                    marks = ",".join("?" for _ in ids)
                    self.connection.execute(f"DELETE FROM chunks_fts WHERE rowid IN ({marks})", ids)
                self.connection.execute("DELETE FROM files WHERE path_hash = ?", (path_hash,))
        return len(removed)

    def delete_all(self) -> None:
        with self.connection:
            self.connection.execute("DELETE FROM chunks_fts")
            self.connection.execute("DELETE FROM files")

    def get_file(self, relative_path: str) -> FileRecord | None:
        row = self.connection.execute(
            "SELECT path_hash, root_hash, relative_path, size, mtime_ns, content_hash, file_type "
            "FROM files WHERE relative_path = ? ORDER BY root_hash LIMIT 1",
            (relative_path,),
        ).fetchone()
        return FileRecord(**dict(row)) if row else None

    def list_files(self) -> list[FileRecord]:
        rows = self.connection.execute(
            "SELECT path_hash, root_hash, relative_path, size, mtime_ns, content_hash, file_type "
            "FROM files ORDER BY relative_path"
        ).fetchall()
        return [FileRecord(**dict(row)) for row in rows]

    def search(self, query: str, limit: int = 50) -> list[dict[str, str | int | None]]:
        if not query or not 0 < limit <= 50:
            raise ValueError("query and limit are bounded")
        try:
            rows = self.connection.execute(
                """
            SELECT f.root_hash, f.relative_path, f.size, f.mtime_ns, f.file_type,
                   c.text, c.page_anchor, c.sheet_anchor
            FROM chunks_fts AS fts
            JOIN chunks AS c ON c.id = fts.rowid
            JOIN files AS f ON f.path_hash = c.path_hash
            WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?
            """,
                (query, limit),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            raise ValueError("invalid search query") from exc
        return [dict(row) for row in rows]


class IndexEngine:
    """Incrementally index selected roots and prune deleted files."""

    def __init__(
        self,
        store: IndexStore,
        *,
        text_extensions: Iterable[str] = (".txt", ".md", ".markdown", ".csv", ".json", ".log"),
        max_text_bytes: int = 4 * 1024 * 1024,
        chunk_size: int = 4000,
    ) -> None:
        if max_text_bytes <= 0 or chunk_size <= 0:
            raise ValueError("index limits must be positive")
        self.store = store
        self.text_extensions = {extension.lower() for extension in text_extensions}
        self.max_text_bytes = max_text_bytes
        self.chunk_size = chunk_size

    @staticmethod
    def _content_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _read(self, path: Path, size: int) -> tuple[str, list[Chunk]]:
        chunks: list[Chunk] = []
        if path.suffix.lower() in self.text_extensions and size <= self.max_text_bytes:
            text = path.read_bytes().decode("utf-8", errors="replace")
            chunks = [
                Chunk(text=text[offset : offset + self.chunk_size]) for offset in range(0, len(text), self.chunk_size)
            ]
        file_type = mimetypes.guess_type(path.name)[0] or path.suffix.lower().lstrip(".") or "unknown"
        return file_type, chunks

    @staticmethod
    def _root_hash(root: Path | str) -> str:
        canonical = canonicalize_root(root)
        return hashlib.sha256(str(canonical).encode("utf-8")).hexdigest()

    def index_file(self, root: Path | str, relative_path: str) -> bool:
        path = resolve_relative(root, relative_path)
        stat = path.stat()
        root_hash = self._root_hash(root)
        path_hash = self.store.path_hash(f"{root_hash}\x00{relative_path}")
        content_hash = self._content_hash(path)
        existing = self.store.get_by_hash(path_hash)
        if existing and existing.size == stat.st_size and existing.mtime_ns == stat.st_mtime_ns:
            if existing.content_hash == content_hash:
                return False
        file_type, chunks = self._read(path, stat.st_size)
        self.store.replace_file(
            FileRecord(
                path_hash=path_hash,
                root_hash=root_hash,
                relative_path=relative_path,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                content_hash=content_hash,
                file_type=file_type,
            ),
            chunks,
        )
        return True

    def scan(
        self,
        roots: Iterable[Path | str],
        *,
        excluded_paths: Iterable[str] = (),
        excluded_names: Iterable[str] = (),
    ) -> ScanReport:
        indexed = skipped = 0
        errors: list[str] = []
        seen: set[str] = set()
        names = tuple(DEFAULT_EXCLUDED_NAMES) + tuple(excluded_names)
        for root in roots:
            try:
                iterator = iter_files(root, excluded_paths=excluded_paths, excluded_names=names)
                for _path, relative_path in iterator:
                    root_hash = self._root_hash(root)
                    path_hash = self.store.path_hash(f"{root_hash}\x00{relative_path}")
                    seen.add(path_hash)
                    try:
                        if self.index_file(root, relative_path):
                            indexed += 1
                        else:
                            skipped += 1
                    except (OSError, PathSafetyError, UnicodeError) as exc:
                        errors.append(f"{relative_path}: {type(exc).__name__}")
            except (OSError, PathSafetyError) as exc:
                errors.append(f"root: {type(exc).__name__}")
        deleted = self.store.remove_paths_not_seen(seen)
        return ScanReport(indexed=indexed, skipped=skipped, deleted=deleted, errors=tuple(errors))
