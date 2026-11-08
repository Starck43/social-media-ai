"""P18-T04: bounded plain-text extraction with encoding detection."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers import Harness  # noqa: E402

from local_connector.extract.text import detect_encoding, extract_plain_text  # noqa: E402


def test_plain_text_utf8_roundtrip(tmp_path):
    harness = Harness(tmp_path)
    harness.write("note.txt", "hello world\nsecond line")
    assert harness.index("note.txt") is True
    record = harness.store.get_file("note.txt")
    assert record is not None
    assert record.notes == ()
    assert harness.store.search("second")[0]["relative_path"] == "note.txt"
    harness.close()


def test_markdown_utf8_with_bom(tmp_path):
    harness = Harness(tmp_path)
    harness.write("doc.md", b"\xef\xbb\xbf# Title\nbody")
    assert harness.index("doc.md") is True
    record = harness.store.get_file("doc.md")
    assert record is not None
    assert record.notes == ()
    assert harness.store.search("Title")
    harness.close()


def test_utf16le_bom_detected(tmp_path):
    harness = Harness(tmp_path)
    harness.write("win.txt", "russian: привет\n".encode("utf-16-le"))
    assert harness.index("win.txt") is True
    record = harness.store.get_file("win.txt")
    assert record is not None
    assert "привет" in "".join(row["text"] for row in harness.store.search("привет", limit=5))
    harness.close()


def test_utf16_without_bom_heuristic(tmp_path):
    data = ("hidden utf16 content " * 200).encode("utf-16-le")
    harness = Harness(tmp_path)
    harness.write("nobom.txt", data)
    assert harness.index("nobom.txt") is True
    record = harness.store.get_file("nobom.txt")
    assert record is not None
    assert "hidden utf16 content" in "".join(row["text"] for row in harness.store.search("hidden", limit=5))
    harness.close()


def test_latin1_fallback_note(tmp_path):
    harness = Harness(tmp_path)
    harness.write("legacy.txt", "café au lait".encode("latin-1"))
    assert harness.index("legacy.txt") is True
    record = harness.store.get_file("legacy.txt")
    assert record is not None
    assert "text.encoding_fallback_latin1" in record.notes
    assert "caf" in "".join(row["text"] for row in harness.store.search("caf*", limit=5))
    harness.close()


def test_empty_file_note(tmp_path):
    harness = Harness(tmp_path)
    harness.write("empty.txt", b"")
    assert harness.index("empty.txt") is True
    record = harness.store.get_file("empty.txt")
    assert record is not None
    assert record.notes == ("text.empty",)
    harness.close()


def test_detection_order():
    assert detect_encoding("hello".encode("utf-8"))[0] == "utf-8"
    assert detect_encoding(b"\xef\xbb\xbfhello")[0] == "utf-8-sig"
    assert detect_encoding("привет".encode("utf-16"))[0] == "utf-16-le"
    assert detect_encoding(b"\xfe\xff" + "привет".encode("utf-16-be"))[0] == "utf-16-be"
    assert detect_encoding(("abc text " * 8).encode("utf-16-le"))[0] == "utf-16-le"
    assert detect_encoding(("abc text " * 8).encode("utf-16-be"))[0] == "utf-16-be"
    assert detect_encoding(b"\x00\x00\xfe\xff\x00\x00\x00p")[0] == "utf-32-be"
    fallback, note = detect_encoding(b"caf\xe9")
    assert fallback == "latin-1"
    assert note == "text.encoding_fallback_latin1"


def test_empty_and_chunking():
    assert extract_plain_text(b"").notes == ("text.empty",)
    result = extract_plain_text(b"a" * 9000, chunk_size=4000)
    assert [len(item.text) for item in result.chunks] == [4000, 4000, 1000]
    assert result.notes == ()
