"""SQLite FTS5 metadata/content index and incremental scanner."""

from __future__ import annotations

import hashlib

try:
    import fcntl
except ImportError:  # pragma: no cover - unsupported platforms fail closed below
    fcntl = None
import mimetypes
import os
import sqlite3
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .extract import DocumentLimits, OcrRunner, extract_file
from .extract.models import decode_notes, encode_notes
from .paths import DEFAULT_EXCLUDED_NAMES, PathSafetyError, SelectedRoot, iter_files, open_selected_file


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
    author: str | None = None
    document_created: int | None = None
    document_modified: int | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.notes:
            object.__setattr__(self, "notes", tuple(str(item) for item in self.notes[:16] if 0 < len(str(item)) <= 128))


@dataclass(frozen=True)
class ScanReport:
    indexed: int = 0
    skipped: int = 0
    deleted: int = 0
    errors: tuple[str, ...] = ()


class IndexStore:
    """Durable local index. Raw content never leaves this SQLite file."""

    def __init__(self, database_path: Path | str) -> None:
        if os.name != "posix" or fcntl is None or not hasattr(os, "O_NOFOLLOW"):
            raise PermissionError("private connector storage is unavailable on this platform")
        self.database_path = Path(os.path.abspath(Path(database_path).expanduser()))
        self._prepare_private_parent(self.database_path.parent)
        if self.database_path.is_symlink():
            raise PermissionError("connector database must not be a symbolic link")
        if self.database_path.exists():
            info = self.database_path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise PermissionError("existing connector database is not owner-private")
        else:
            fd = os.open(
                self.database_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                0o600,
            )
            os.close(fd)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{self.database_path}{suffix}")
            if sidecar.is_symlink():
                raise PermissionError("connector database sidecar must not be a symbolic link")
            if sidecar.exists():
                info = sidecar.stat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                    raise PermissionError("existing connector database sidecar is not owner-private")
        self.lock = threading.RLock()
        lock_path = Path(f"{self.database_path}.lock")
        if lock_path.is_symlink():
            raise PermissionError("connector cache lock must not be a symbolic link")
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        lock_info = os.fstat(lock_fd)
        if lock_info.st_uid != os.getuid() or stat.S_IMODE(lock_info.st_mode) & 0o077:
            os.close(lock_fd)
            raise PermissionError("connector cache lock is not owner-private")
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(lock_fd)
            raise PermissionError("connector cache is already in use") from exc
        self._lock_fd = lock_fd
        try:
            self.connection = sqlite3.connect(self.database_path, check_same_thread=False)
            self._closed = False
            self.database_path.chmod(0o600)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA foreign_keys = ON")
            self.connection.execute("PRAGMA secure_delete = ON")
            self.connection.execute("PRAGMA journal_mode = WAL")
            self._create_schema()
        except BaseException:
            if hasattr(self, "connection"):
                self.connection.close()
            os.close(self._lock_fd)
            raise

    @staticmethod
    def _prepare_private_parent(path: Path) -> None:
        missing: list[Path] = []
        current = path
        while not current.exists():
            missing.append(current)
            if current.parent == current:
                raise PermissionError("connector cache parent is unavailable")
            current = current.parent
        for directory in reversed(missing):
            try:
                directory.mkdir(mode=0o700)
                directory.chmod(0o700)
            except FileExistsError:
                pass
        resolved = path.resolve(strict=True)
        info = resolved.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise PermissionError("connector cache directory must be owned and private")
        if path != resolved:
            raise PermissionError("connector cache directory must not contain a symbolic link")

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
                author TEXT,
                document_created INTEGER,
                document_modified INTEGER,
                notes TEXT,
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
            CREATE TABLE IF NOT EXISTS connector_control (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                generation INTEGER NOT NULL,
                paused INTEGER NOT NULL CHECK(paused IN (0, 1)),
                pause_reason TEXT
            );
            INSERT OR IGNORE INTO connector_control(singleton, generation, paused, pause_reason) VALUES (1, 0, 0, NULL);
            CREATE TABLE IF NOT EXISTS approved_roots (
                selection_path TEXT PRIMARY KEY,
                canonical_path TEXT NOT NULL,
                device INTEGER NOT NULL,
                inode INTEGER NOT NULL
            );
            """)
        self._upgrade_file_columns()
        self.connection.commit()

    def _upgrade_file_columns(self) -> None:
        """Add P18-T04 columns to databases created by the T03 scaffold."""

        existing = {row[1] for row in self.connection.execute("PRAGMA table_info(files)")}
        for column, column_type in (
            ("author", "TEXT"),
            ("document_created", "INTEGER"),
            ("document_modified", "INTEGER"),
            ("notes", "TEXT"),
        ):
            if column not in existing:
                self.connection.execute(f"ALTER TABLE files ADD COLUMN {column} {column_type}")

    @property
    def generation(self) -> int:
        with self.lock:
            row = self.connection.execute("SELECT generation FROM connector_control WHERE singleton = 1").fetchone()
            return int(row[0])

    @property
    def indexing_paused(self) -> bool:
        with self.lock:
            row = self.connection.execute("SELECT paused FROM connector_control WHERE singleton = 1").fetchone()
            return bool(row[0])

    @property
    def pause_reason(self) -> str | None:
        with self.lock:
            row = self.connection.execute("SELECT pause_reason FROM connector_control WHERE singleton = 1").fetchone()
            return str(row[0]) if row[0] is not None else None

    def pause_indexing(self, reason: str = "root_changed") -> None:
        if reason not in {"root_changed", "deleted"}:
            raise ValueError("unsupported pause reason")
        with self.lock, self.connection:
            self.connection.execute(
                """UPDATE connector_control
                   SET paused = 1,
                       pause_reason = CASE
                           WHEN pause_reason = 'deleted' THEN 'deleted'
                           ELSE ?
                       END
                   WHERE singleton = 1""",
                (reason,),
            )

    def resume_indexing(self) -> None:
        with self.lock, self.connection:
            self.connection.execute("UPDATE connector_control SET paused = 0, pause_reason = NULL WHERE singleton = 1")

    @staticmethod
    def _selection_key(path: Path | str) -> str:
        return os.path.normpath(os.path.abspath(Path(path).expanduser()))

    def approved_root(self, path: Path | str) -> tuple[int, int] | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT device, inode FROM approved_roots WHERE selection_path = ?",
                (self._selection_key(path),),
            ).fetchone()
        return (int(row[0]), int(row[1])) if row else None

    def list_approved_roots(self) -> list[str]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT selection_path FROM approved_roots ORDER BY selection_path"
            ).fetchall()
        return [str(row[0]) for row in rows]

    def approve_root(self, root: SelectedRoot) -> None:
        root.verify()
        with self.lock, self.connection:
            self.connection.execute(
                """INSERT INTO approved_roots(selection_path, canonical_path, device, inode)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(selection_path) DO UPDATE SET
                       canonical_path=excluded.canonical_path,
                       device=excluded.device,
                       inode=excluded.inode""",
                (root.selection_path, str(root.path), root.device, root.inode),
            )

    def set_approved_roots(self, roots: Iterable[SelectedRoot]) -> None:
        selected = tuple(roots)
        for root in selected:
            root.verify()
        with self.lock, self.connection:
            self.connection.execute("DELETE FROM approved_roots")
            self.connection.executemany(
                "INSERT INTO approved_roots(selection_path, canonical_path, device, inode) VALUES (?, ?, ?, ?)",
                [(root.selection_path, str(root.path), root.device, root.inode) for root in selected],
            )

    def restore_root(self, path: Path | str) -> SelectedRoot:
        key = self._selection_key(path)
        with self.lock:
            row = self.connection.execute(
                "SELECT canonical_path, device, inode FROM approved_roots WHERE selection_path = ?", (key,)
            ).fetchone()
            if row is None:
                row = self.connection.execute(
                    "SELECT canonical_path, device, inode FROM approved_roots WHERE canonical_path = ?", (key,)
                ).fetchone()
        if row is None:
            raise PathSafetyError("folder must be selected again")
        selected = SelectedRoot(Path(path))
        if str(selected.path) != str(row[0]) or selected.device != int(row[1]) or selected.inode != int(row[2]):
            raise PathSafetyError("selected folder changed; select it again")
        return selected

    @staticmethod
    def path_hash(relative_path: str) -> str:
        return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()

    def close(self) -> None:
        with self.lock:
            if self._closed:
                return
            try:
                self.connection.close()
            finally:
                os.close(self._lock_fd)
                self._closed = True

    @classmethod
    def _record_from_row(cls, row):
        data = dict(row)
        raw_notes = data.pop("notes", None)
        return FileRecord(**data, notes=decode_notes(raw_notes))

    def get_by_hash(self, path_hash: str) -> FileRecord | None:
        with self.lock:
            row = self.connection.execute(
                f"SELECT {self._FILE_COLUMNS} FROM files WHERE path_hash = ?",
                (path_hash,),
            ).fetchone()
        return self._record_from_row(row) if row else None

    _FILE_COLUMNS = (
        "path_hash, root_hash, relative_path, size, mtime_ns, content_hash, file_type, "
        "author, document_created, document_modified, notes"
    )

    def replace_file(self, record: FileRecord, chunks: Sequence[Chunk]) -> None:
        with self.lock, self.connection:
            if self.indexing_paused:
                raise PathSafetyError("indexing is paused")
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
                INSERT INTO files(path_hash, root_hash, relative_path, size, mtime_ns, content_hash, file_type, indexed_at,
                                  author, document_created, document_modified, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path_hash) DO UPDATE SET
                    relative_path=excluded.relative_path, size=excluded.size,
                    mtime_ns=excluded.mtime_ns, content_hash=excluded.content_hash,
                    file_type=excluded.file_type, indexed_at=excluded.indexed_at,
                    author=excluded.author, document_created=excluded.document_created,
                    document_modified=excluded.document_modified, notes=excluded.notes
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
                    record.author,
                    record.document_created,
                    record.document_modified,
                    encode_notes(record.notes),
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

    def remove_paths_not_seen(self, seen_hashes: set[str], completed_roots: set[str]) -> int:
        with self.lock, self.connection:
            rows = self.connection.execute("SELECT path_hash, root_hash FROM files").fetchall()
            removed = [row[0] for row in rows if row[1] in completed_roots and row[0] not in seen_hashes]
            for path_hash in removed:
                ids = [
                    row[0] for row in self.connection.execute("SELECT id FROM chunks WHERE path_hash = ?", (path_hash,))
                ]
                if ids:
                    marks = ",".join("?" for _ in ids)
                    self.connection.execute(f"DELETE FROM chunks_fts WHERE rowid IN ({marks})", ids)
                self.connection.execute("DELETE FROM files WHERE path_hash = ?", (path_hash,))
        return len(removed)

    def delete_all(self, *, pause: bool = True) -> None:
        with self.lock:
            with self.connection:
                reason = "deleted" if pause else None
                self.connection.execute(
                    """UPDATE connector_control
                       SET generation = generation + 1, paused = ?, pause_reason = ?
                       WHERE singleton = 1""",
                    (int(pause), reason),
                )
                self.connection.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('delete-all')")
                self.connection.execute("DELETE FROM files")
            self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.connection.execute("VACUUM")

    def get_file(self, relative_path: str) -> FileRecord | None:
        with self.lock:
            row = self.connection.execute(
                f"SELECT {self._FILE_COLUMNS} FROM files WHERE relative_path = ? ORDER BY root_hash LIMIT 1",
                (relative_path,),
            ).fetchone()
        return self._record_from_row(row) if row else None

    def list_files(self) -> list[FileRecord]:
        with self.lock:
            rows = self.connection.execute(f"SELECT {self._FILE_COLUMNS} FROM files ORDER BY relative_path").fetchall()
        return [self._record_from_row(row) for row in rows]

    def search(self, query: str, limit: int = 50) -> list[dict[str, str | int | None]]:
        if not query or not 0 < limit <= 50:
            raise ValueError("query and limit are bounded")
        try:
            with self.lock:
                rows = self.connection.execute(
                    """
            SELECT f.root_hash, f.relative_path, f.size, f.mtime_ns, f.file_type,
                   f.author, f.document_created, f.document_modified, f.notes,
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
        results = [dict(row) for row in rows]
        for item in results:
            item["notes"] = decode_notes(item.get("notes"))
        return results


class IndexEngine:
    """Incrementally index selected roots and prune deleted files."""

    def __init__(
        self,
        store: IndexStore,
        *,
        text_extensions: Iterable[str] = (".txt", ".md", ".markdown", ".csv", ".json", ".log"),
        max_text_bytes: int = 4 * 1024 * 1024,
        chunk_size: int = 4000,
        document_extensions: Iterable[str] = (
            ".pdf",
            ".docx",
            ".xlsx",
            ".png",
            ".jpg",
            ".jpeg",
            ".tiff",
            ".tif",
            ".bmp",
            ".gif",
            ".webp",
        ),
        limits: DocumentLimits | None = None,
    ) -> None:
        if max_text_bytes <= 0 or chunk_size <= 0:
            raise ValueError("index limits must be positive")
        self.store = store
        self.text_extensions = {extension.lower() for extension in text_extensions}
        self.document_extensions = {extension.lower() for extension in document_extensions}
        self.max_text_bytes = max_text_bytes
        self.chunk_size = chunk_size
        self.limits = limits if limits is not None else DocumentLimits()
        self._ocr_runner: OcrRunner | None = None
        self._roots: dict[str, SelectedRoot] = {}

    def _pin_root(self, root: SelectedRoot | Path | str) -> SelectedRoot:
        if isinstance(root, SelectedRoot):
            return root
        lexical = Path(root).expanduser()
        if not lexical.is_absolute():
            lexical = Path.cwd() / lexical
        key = os.path.normpath(str(lexical))
        selected = self._roots.get(key)
        if selected is None:
            selected = SelectedRoot(lexical)
            self._roots[selected.selection_path] = selected
            self._roots[key] = selected
        return selected

    @staticmethod
    def _root_hash(root: SelectedRoot) -> str:
        identity = f"{root.path}\x00{root.device}\x00{root.inode}"
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    @staticmethod
    def _excluded(relative_path: str, excluded_paths: set[tuple[str, ...]], excluded_names: set[str]) -> bool:
        parts = tuple(part.casefold() for part in Path(relative_path).parts)
        if any(part.startswith(".") or part in excluded_names for part in parts):
            return True
        return any(parts[: len(excluded)] == excluded for excluded in excluded_paths)

    def _ocr(self) -> OcrRunner | None:
        """Lazy bounded OCR runner with private scratch outside selected roots."""

        if self._ocr_runner is None:
            try:
                workdir = self.store.database_path.parent / "ocr-tmp"
                IndexStore._prepare_private_parent(workdir)
                self._ocr_runner = OcrRunner(
                    self.limits.with_ocr_budget(),
                    workdir=workdir,
                    max_image_bytes=self.limits.max_document_bytes,
                )
            except (PathSafetyError, OSError, ValueError):
                return None
        return self._ocr_runner

    def _safe_extract(
        self, data: bytes, relative_path: str, file_started: float, *, with_ocr: bool
    ) -> tuple[list[Chunk], tuple[str, ...], str | None, int | None, int | None]:
        """Extraction that never raises: unexpected errors become note codes."""

        if time.monotonic() - file_started > self.limits.max_document_seconds:
            return [], ("limit.time",), None, None, None
        try:
            runner = self._ocr() if with_ocr else None
            result = extract_file(data, relative_path, self.limits, runner, chunk_size=self.chunk_size)
        except Exception as exc:
            return [], ("extraction." + type(exc).__name__.lower(),), None, None, None
        if with_ocr:
            chunks = [
                Chunk(text=item.text, page_anchor=item.page_anchor, sheet_anchor=item.sheet_anchor)
                for item in result.chunks
            ]
        else:
            chunks = [Chunk(text=item.text) for item in result.chunks]
        return chunks, result.notes, result.author, result.created, result.modified

    def _read_selected(self, root: SelectedRoot, relative_path: str) -> tuple[FileRecord, list[Chunk]]:
        path_parts = Path(relative_path).parts
        file_type = (
            mimetypes.guess_type(path_parts[-1])[0] or Path(relative_path).suffix.lower().lstrip(".") or "unknown"
        )
        content_type = Path(relative_path).suffix.lower()
        is_plain = content_type in self.text_extensions
        is_document = content_type in self.document_extensions
        keep = is_plain or is_document
        cap = self.max_text_bytes if is_plain else (self.limits.max_document_bytes if is_document else 0)
        content: list[bytes] = []
        notes: list[str] = []
        digest = hashlib.sha256()
        file_started = time.monotonic()
        with open_selected_file(root, relative_path) as stream:
            before = os.fstat(stream.fileno())
            eligible = keep and before.st_size <= cap
            remaining = cap + 1 if eligible else 0
            while True:
                block = stream.read(1024 * 1024 if not eligible else min(1024 * 1024, remaining))
                if not block:
                    break
                digest.update(block)
                if eligible:
                    content.append(block)
                    remaining -= len(block)
                    if remaining < 0:
                        eligible = False
                        content.clear()
            if eligible and remaining < 1:
                raise PathSafetyError("file grew beyond its indexing limit")
            info = os.fstat(stream.fileno())
        chunks: list[Chunk] = []
        author: str | None = None
        document_created: int | None = None
        document_modified: int | None = None
        if is_plain and eligible:
            raw = b"".join(content)
            if content_type in (".txt", ".md", ".markdown"):
                chunks, plain_notes, _, _, _ = self._safe_extract(raw, relative_path, file_started, with_ocr=False)
                notes.extend(plain_notes)
            else:
                text = raw.decode("utf-8", errors="replace")
                chunks = [
                    Chunk(text=text[offset : offset + self.chunk_size])
                    for offset in range(0, len(text), self.chunk_size)
                ]
        elif is_document:
            if eligible and content:
                chunks, doc_notes, author, document_created, document_modified = self._safe_extract(
                    b"".join(content), relative_path, file_started, with_ocr=True
                )
                notes.extend(doc_notes)
                chunks = self._bound_document_chunks(chunks, notes)
            elif eligible:
                notes.append("document.empty")
            else:
                notes.append("limit.size")
        root_hash = self._root_hash(root)
        path_hash = self.store.path_hash(f"{root_hash}\x00{relative_path}")
        record = FileRecord(
            path_hash=path_hash,
            root_hash=root_hash,
            relative_path=relative_path,
            size=info.st_size,
            mtime_ns=info.st_mtime_ns,
            content_hash=digest.hexdigest(),
            file_type=file_type,
            author=author,
            document_created=document_created,
            document_modified=document_modified,
            notes=tuple(notes[:16]),
        )
        return record, chunks

    def _bound_document_chunks(self, chunks: list[Chunk], notes: list[str]) -> list[Chunk]:
        """Defense-in-depth total character bound on document extractor output."""

        if not chunks:
            return chunks
        budget = self.limits.max_document_characters
        kept: list[Chunk] = []
        truncated = False
        for chunk in chunks:
            if budget <= 0:
                truncated = True
                break
            if len(chunk.text) > budget:
                kept.append(
                    Chunk(text=chunk.text[:budget], page_anchor=chunk.page_anchor, sheet_anchor=chunk.sheet_anchor)
                )
                truncated = True
                break
            budget -= len(chunk.text)
            kept.append(chunk)
        if truncated:
            notes.append("limit.characters")
        return kept

    def index_file(
        self,
        root: SelectedRoot | Path | str,
        relative_path: str,
        *,
        excluded_paths: Iterable[str] = (),
        excluded_names: Iterable[str] = (),
    ) -> bool:
        selected = self._pin_root(root)
        custom_exclusions = {tuple(part.casefold() for part in Path(item).parts) for item in excluded_paths}
        try:
            cache_relative = self.store.database_path.relative_to(selected.path).as_posix()
            custom_exclusions.update(
                tuple(part.casefold() for part in Path(f"{cache_relative}{suffix}").parts)
                for suffix in ("", "-wal", "-shm", "-journal", ".lock", "-lock")
            )
        except ValueError:
            pass
        denied_names = {name.casefold() for name in DEFAULT_EXCLUDED_NAMES}
        denied_names.update(name.casefold() for name in excluded_names)
        if self._excluded(relative_path, custom_exclusions, denied_names):
            raise PathSafetyError("path is excluded from indexing")
        with self.store.lock:
            if self.store.indexing_paused:
                raise PathSafetyError("indexing is paused")
            record, chunks = self._read_selected(selected, relative_path)
            existing = self.store.get_by_hash(record.path_hash)
            if existing and existing.size == record.size and existing.mtime_ns == record.mtime_ns:
                if existing.content_hash == record.content_hash:
                    return False
            self.store.replace_file(record, chunks)
            return True

    def scan(
        self,
        roots: Iterable[SelectedRoot | Path | str],
        *,
        excluded_paths: Iterable[str] = (),
        excluded_names: Iterable[str] = (),
    ) -> ScanReport:
        indexed = skipped = 0
        errors: list[str] = []
        seen: set[str] = set()
        completed_roots: set[str] = set()
        user_excluded_paths = tuple(excluded_paths)
        names = set(DEFAULT_EXCLUDED_NAMES) | {name.casefold() for name in excluded_names}
        try:
            selected_roots = [self._pin_root(root) for root in roots]
        except (OSError, PathSafetyError) as exc:
            return ScanReport(errors=(f"root: {type(exc).__name__}",))
        with self.store.lock:
            if self.store.indexing_paused:
                return ScanReport(errors=("indexing paused",))
            generation = self.store.generation
            for root in selected_roots:
                root_hash = self._root_hash(root)
                root_exclusions = set(user_excluded_paths)
                try:
                    cache_relative = self.store.database_path.relative_to(root.path).as_posix()
                    root_exclusions.add(cache_relative)
                    root_exclusions.add(f"{cache_relative}-wal")
                    root_exclusions.add(f"{cache_relative}-shm")
                    root_exclusions.add(f"{cache_relative}-journal")
                    root_exclusions.add(f"{cache_relative}.lock")
                    root_exclusions.add(f"{cache_relative}-lock")
                except ValueError:
                    pass
                try:
                    iterator = iter_files(root, excluded_paths=root_exclusions, excluded_names=names)
                    for _path, relative_path in iterator:
                        path_hash = self.store.path_hash(f"{root_hash}\x00{relative_path}")
                        seen.add(path_hash)
                        try:
                            if self.index_file(
                                root,
                                relative_path,
                                excluded_paths=root_exclusions,
                                excluded_names=names,
                            ):
                                indexed += 1
                            else:
                                skipped += 1
                        except (OSError, PathSafetyError, UnicodeError) as exc:
                            errors.append(f"{relative_path}: {type(exc).__name__}")
                    completed_roots.add(root_hash)
                except (OSError, PathSafetyError) as exc:
                    errors.append(f"root: {type(exc).__name__}")
            if generation != self.store.generation:
                return ScanReport(indexed=indexed, skipped=skipped, errors=tuple(errors + ["scan invalidated"]))
            deleted = self.store.remove_paths_not_seen(seen, completed_roots)
        return ScanReport(indexed=indexed, skipped=skipped, deleted=deleted, errors=tuple(errors))
