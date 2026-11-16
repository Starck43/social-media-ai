"""P18-T04: bounded raster image preparation and OCR call path."""

import shutil
import stat as stat_module
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from helpers import Harness, make_jpeg, make_png, stub_tesseract  # noqa: E402

from local_connector.extract.images import extract_image, image_suffix, prepare_image  # noqa: E402
from local_connector.extract.limits import DocumentLimits, OcrBudget  # noqa: E402
from local_connector.extract.ocr import OcrRunner  # noqa: E402

BLANK_STUB = """#!/usr/bin/env python3
import sys
if "--list-langs" in sys.argv[1:]:
    print("List of available languages in ...:")
    print("rus")
    print("eng")
    sys.exit(0)
sys.exit(0)
"""


def runner(tmp_path, binary=None):
    limits = DocumentLimits()
    budget = OcrBudget(
        languages=limits.ocr_languages,
        cpu_seconds=limits.ocr_cpu_seconds,
        wall_seconds=limits.ocr_wall_seconds,
        memory_bytes=limits.ocr_memory_bytes,
        binary=str(binary) if binary is not None else None,
    )
    return OcrRunner(budget, workdir=tmp_path / "ocr-work", max_image_bytes=1 << 20)


def test_png_ocr_success(tmp_path):
    result = extract_image(make_png(), "scan.png", DocumentLimits(), runner(tmp_path, stub_tesseract(tmp_path)))
    assert result.chunks[0].text == "OCR-TEXT-MARKER"
    assert result.notes == ()


def test_jpeg_native_passthrough(tmp_path):
    jpeg = make_jpeg()
    payload, note = prepare_image(jpeg, "photo.jpg", DocumentLimits())
    assert payload == jpeg
    assert note is None
    result = extract_image(payload, "photo.jpg", DocumentLimits(), runner(tmp_path, stub_tesseract(tmp_path)))
    assert result.chunks[0].text == "OCR-TEXT-MARKER"


def test_unsupported_image_bytes(tmp_path):
    result = extract_image(
        b"not an image at all really", "fake.png", DocumentLimits(), runner(tmp_path, stub_tesseract(tmp_path))
    )
    assert result.chunks == ()
    assert result.notes == ("image.unsupported_format",)


def test_suffix_classification():
    assert image_suffix("photo.HEIC") is None
    assert image_suffix("scan.png") == ".png"
    assert image_suffix("photo.jpeg") == ".jpeg"
    assert image_suffix("noext") is None


def test_oversize_image_rejected(tmp_path):
    limits = DocumentLimits(max_embedded_image_bytes=8)
    result = extract_image(make_png(), "big.png", limits, runner(tmp_path, stub_tesseract(tmp_path)))
    assert result.notes == ("image.too_large",)


def test_no_runner_available_note(tmp_path):
    result = extract_image(make_png(), "scan.png", DocumentLimits(), None)
    assert result.notes == ("ocr_unavailable",)


def test_blank_ocr_output_has_no_chunks(tmp_path):
    blank = tmp_path / "tesseract-blank.py"
    blank.write_text(BLANK_STUB, encoding="utf-8")
    blank.chmod(stat_module.S_IRWXU)
    budget = OcrBudget(languages=("rus", "eng"), cpu_seconds=5, wall_seconds=5, memory_bytes=1 << 30, binary=str(blank))
    ocr = OcrRunner(budget, workdir=tmp_path / "ocr-work2")
    result = extract_image(make_png(), "blank.png", DocumentLimits(), ocr)
    assert result.chunks == ()
    assert result.notes == ()


def test_engine_indexes_image(tmp_path):
    harness = Harness(tmp_path)
    harness.write("scan.png", make_png())
    assert harness.index("scan.png") is True
    record = harness.store.get_file("scan.png")
    assert record is not None
    if shutil.which("tesseract") is None:
        assert record.notes and "ocr_unavailable" in record.notes[0]
    harness.close()
