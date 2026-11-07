"""P18-T04: engine integration for document extraction with resource limits."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers import Harness, make_docx, make_jpeg, make_pdf, make_png, make_xlsx, stub_tesseract  # noqa: E402

from local_connector.extract.limits import DocumentLimits  # noqa: E402
from local_connector.index import FileRecord, IndexStore  # noqa: E402


def ocr_limits(tmp_path):
    return DocumentLimits(ocr_binary=str(stub_tesseract(tmp_path)))


def test_scan_mixed_documents(tmp_path):
    harness = Harness(tmp_path, limits=ocr_limits(tmp_path))
    harness.write("a.pdf", make_pdf(pages=("alpha pdf text",)))
    harness.write("sub/b.docx", make_docx(paragraphs=("bravo docx text",)))
    harness.write("c.xlsx", make_xlsx({"S": (("charlie xlsx text",),)}))
    harness.write("d.txt", "delta txt text")
    harness.write("e.bin", b"\x00\x01\x02binarynoextract")
    report = harness.engine.scan([harness.root])
    assert report.indexed == 5
    assert report.errors == ()
    assert {item["relative_path"] for item in harness.store.search("alpha", limit=5)} == {"a.pdf"}
    pdf_record = harness.store.get_file("a.pdf")
    assert pdf_record is not None
    assert pdf_record.notes == ()
    rows = harness.store.search("alpha", limit=5)
    assert rows[0]["page_anchor"] == "1"
    harness.close()


def test_bad_documents_stay_indexed_with_notes(tmp_path):
    harness = Harness(tmp_path)
    harness.write("bad.pdf", b"%PDF-1.4 garbage tail")
    harness.write("bad.docx", b"not a zip")
    harness.write("bad.xlsx", b"no workbook")
    report = harness.engine.scan([harness.root])
    assert report.indexed == 3
    assert report.errors == ()
    pdf_record = harness.store.get_file("bad.pdf")
    docx_record = harness.store.get_file("bad.docx")
    xlsx_record = harness.store.get_file("bad.xlsx")
    assert pdf_record.notes and pdf_record.notes[0].startswith("pdf.unsupported_format")
    assert docx_record.notes == ("docx.unsupported_format",)
    assert xlsx_record.notes and xlsx_record.notes[0].startswith("xlsx.unsupported_format")
    # Metadata stays searchable: no text chunks were created.
    assert harness.store.search("garbage") == []
    assert len(harness.store.list_files()) == 3
    harness.close()


def test_oversize_document_notes_instead_of_crash(tmp_path):
    limits = DocumentLimits(max_document_bytes=64)
    harness = Harness(tmp_path, limits=limits)
    harness.write("big.docx", make_docx(paragraphs=("should never be parsed",)))
    assert harness.index("big.docx") is True
    record = harness.store.get_file("big.docx")
    assert record.notes == ("limit.size",)
    assert harness.store.search("should") == []
    harness.close()


def test_empty_document_note(tmp_path):
    harness = Harness(tmp_path)
    harness.write("empty.pdf", b"")
    assert harness.index("empty.pdf") is True
    record = harness.store.get_file("empty.pdf")
    assert record.notes == ("document.empty",)
    harness.close()


def test_time_budget_tiny(tmp_path):
    import local_connector.extract.limits as limits_module

    tiny = limits_module.DocumentLimits(max_document_seconds=1e-9)
    harness = Harness(tmp_path, limits=tiny)
    harness.write("slow.docx", make_docx(paragraphs=("timed out",)))
    assert harness.index("slow.docx") is True
    record = harness.store.get_file("slow.docx")
    assert record.notes == ("limit.time",)
    assert harness.store.search("timed") == []
    harness.close()


def test_character_budget_trims_document_chunks(tmp_path):
    data = make_docx(paragraphs=(("phrase " * 300), "trailer phrase"))
    limits = DocumentLimits(max_document_characters=2000)
    harness = Harness(tmp_path, limits=limits)
    harness.write("long.docx", data)
    assert harness.index("long.docx") is True
    record = harness.store.get_file("long.docx")
    assert "limit.characters" in record.notes
    total = sum(len(item["text"]) for item in harness.store.search("phrase", limit=50))
    assert total <= 2000 + 100
    harness.close()


def test_image_engine_ocr(tmp_path):
    harness = Harness(tmp_path, limits=ocr_limits(tmp_path))
    harness.write("scan.png", make_png())
    assert harness.index("scan.png") is True
    record = harness.store.get_file("scan.png")
    assert record is not None
    text = "".join(item["text"] for item in harness.store.search("MARKER", limit=5))
    assert "OCR-TEXT-MARKER" in text or record.notes == ()
    harness.close()


def test_pdf_with_scan_engine_ocr(tmp_path):
    harness = Harness(tmp_path, limits=ocr_limits(tmp_path))
    harness.write("scan.pdf", make_pdf(pages=("printed page",), jpeg=make_jpeg()))
    assert harness.index("scan.pdf") is True
    text = "".join(item["text"] for item in harness.store.search("MARKER", limit=5))
    assert "OCR-TEXT-MARKER" in text
    harness.close()


def test_engine_uncatchable_extractor_error_recorded(tmp_path, monkeypatch):
    import local_connector.extract as extract_package

    def boom(data, name, limits, ocr=None, **kwargs):
        raise RuntimeError("simulated extractor crash")

    monkeypatch.setattr(
        extract_package.index if hasattr(extract_package, "index") else extract_package,
        "extract_file",
        boom,
        raising=False,
    )
    # The engine imported extract_file directly; patch that binding instead.
    import local_connector.index as index_module

    monkeypatch.setattr(index_module, "extract_file", boom)
    harness = Harness(tmp_path)
    harness.write("crash.pdf", make_pdf(pages=("never reaches",)))
    try:
        assert harness.index("crash.pdf") is True
        record = harness.store.get_file("crash.pdf")
        assert record.notes == ("extraction.runtimeerror",)
    finally:
        harness.close()


def test_t03_schema_migration(tmp_path):
    db = tmp_path / "old" / "index.db"
    db.parent.mkdir(mode=0o700)
    db.parent.chmod(0o700)
    file_fd = os.open(db, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    os.close(file_fd)
    connection = sqlite3.connect(db)
    connection.executescript("""
        CREATE TABLE files (
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
        CREATE TABLE chunks (
            id INTEGER PRIMARY KEY,
            path_hash TEXT NOT NULL REFERENCES files(path_hash) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            page_anchor TEXT,
            sheet_anchor TEXT,
            UNIQUE(path_hash, chunk_index)
        );
        CREATE VIRTUAL TABLE chunks_fts USING fts5(text, content='chunks', content_rowid='id');
        CREATE TABLE connector_control (
            singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
            generation INTEGER NOT NULL,
            paused INTEGER NOT NULL CHECK(paused IN (0, 1)),
            pause_reason TEXT
        );
        INSERT INTO connector_control(singleton, generation, paused, pause_reason) VALUES (1, 0, 0, NULL);
        CREATE TABLE approved_roots (
            selection_path TEXT PRIMARY KEY,
            canonical_path TEXT NOT NULL,
            device INTEGER NOT NULL,
            inode INTEGER NOT NULL
        );
        """)
    connection.commit()
    connection.close()
    store = IndexStore(db)
    try:
        columns = {row[1] for row in store.connection.execute("PRAGMA table_info(files)")}
        assert {"author", "document_created", "document_modified", "notes"} <= columns
        record = FileRecord(
            path_hash=store.path_hash("r\0old.txt"),
            root_hash="r",
            relative_path="old.txt",
            size=4,
            mtime_ns=1,
            content_hash="x" * 64,
            file_type="text/plain",
            author="migrated",
            notes=("old.note",),
        )
        store.replace_file(record, [])
        back = store.get_file("old.txt")
        assert back.author == "migrated"
        assert back.notes == ("old.note",)
        assert back.document_created is None
    finally:
        store.close()


def test_config_runtime_limits_flow():
    from local_connector.config import ConnectorConfig
    from local_connector.extract.limits import DocumentLimits

    config = ConnectorConfig(index_path=Path(__file__).resolve().parents[3] / "nope" / "index.db")
    assert isinstance(config.document_limits, DocumentLimits)
    with_limits = ConnectorConfig(
        index_path=Path(__file__).resolve().parents[3] / "nope" / "index.db",
        document_limits=DocumentLimits(max_pdf_pages=7),
    )
    assert with_limits.document_limits.max_pdf_pages == 7


def test_connector_importable_without_format_backends(tmp_path):
    """A missing optional backend must not break importing the connector."""

    root = Path(__file__).resolve().parents[3]
    probe = tmp_path / "no_backend_probe.py"
    probe.write_text(
        "import sys\n"
        "for name in ('pypdf', 'docx', 'openpyxl', 'PIL'):\n"
        "    sys.modules[name] = None\n"
        "import local_connector  # noqa: E402\n"
        "from local_connector.extract import DocumentLimits, extract_file  # noqa: E402\n"
        "result = extract_file(b'plain text probe', 'probe.txt', DocumentLimits())\n"
        "assert result.chunks and result.chunks[0].text == 'plain text probe', result\n"
        "print('PROBE-OK')\n",
        encoding="utf-8",
    )
    outcome = subprocess.run(
        [sys.executable, str(probe)],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(root)},
    )
    assert outcome.returncode == 0, outcome.stderr
    assert "PROBE-OK" in outcome.stdout


def test_long_text_is_split_by_chunk_size(tmp_path):
    harness = Harness(tmp_path)
    harness.write("long.txt", "x" * 9000)
    assert harness.index("long.txt") is True
    count = harness.store.connection.execute(
        "SELECT COUNT(*) FROM chunks c "
        "JOIN files f ON f.path_hash = c.path_hash "
        "WHERE f.relative_path = 'long.txt'"
    ).fetchone()[0]
    assert count == 3
    harness.close()
