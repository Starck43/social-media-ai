"""Shared fixture helpers for the P18-T04 extraction tests.

Plain importable module (no conftest): the suite runs with ``--noconftest`` in
the standalone scaffold scope, so helpers are imported directly by test files.
"""

from __future__ import annotations

import io
import stat
import sys
import textwrap
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from local_connector.extract.limits import DocumentLimits  # noqa: E402
from local_connector.index import IndexEngine, IndexStore  # noqa: E402
from local_connector.paths import SelectedRoot  # noqa: E402

STUB_TESSERACT = r"""#!/usr/bin/env python3
import os
import sys
import time

mode = os.environ.get("P18T04_OCR", "ok")
argv = sys.argv[1:]
if "--list-langs" in argv:
    print("List of available languages in ...:")
    for name in os.environ.get("P18T04_OCR_LANGS", "rus,eng").split(","):
        if name:
            print(name)
    sys.exit(0)
if mode == "slow":
    time.sleep(30)
    sys.exit(0)
if mode == "spin":
    while True:
        pass
if mode == "fail":
    sys.stderr.write("stub ocr failure\n")
    sys.exit(3)
print("OCR-TEXT-MARKER")
sys.exit(0)
"""


def stub_tesseract(directory: Path, name: str = "tesseract-stub.py") -> Path:
    """Write an executable Tesseract double with env-driven behavior."""

    binary = directory / name
    binary.write_text(textwrap.dedent(STUB_TESSERACT), encoding="utf-8")
    binary.chmod(stat.S_IRWXU)
    return binary


def make_jpeg(width: int = 160, height: int = 80) -> bytes:
    from PIL import Image, ImageDraw

    picture = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(picture)
    draw.text((10, 30), "SCANMARKER", fill="black")
    buffer = io.BytesIO()
    picture.save(buffer, format="JPEG")
    return buffer.getvalue()


def make_png(width: int = 64, height: int = 32) -> bytes:
    from PIL import Image

    picture = Image.new("RGB", (width, height), "white")
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    return buffer.getvalue()


def make_pdf(
    pages: tuple[str, ...] = ("Hello T04",),
    jpeg: bytes | None = None,
    author: str | None = None,
    creation: str | None = None,
    modification: str | None = None,
) -> bytes:
    """Build a minimal valid multi-page PDF with a shared Type1 font.

    The optional JPEG is embedded as a DCTDecode image on the last page.
    """

    buffer = io.BytesIO()
    buffer.write(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}

    def start(number: int) -> None:
        offsets[number] = buffer.tell()
        buffer.write(f"{number} 0 obj\n".encode("latin-1"))

    def close(object_body: bytes = b"") -> None:
        if object_body:
            buffer.write(object_body)
        buffer.write(b"endobj\n")

    def stream_object(number: int, dictionary: str, payload: bytes) -> None:
        start(number)
        buffer.write(f"<< {dictionary} >>\nstream\n".encode("latin-1"))
        buffer.write(payload)
        buffer.write(b"\nendstream\nendobj\n")

    def escape(text: str) -> str:
        return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    page_count = len(pages)
    font_number = 3 + 2 * page_count
    image_number = font_number + 1 if jpeg is not None else None
    info_number = (image_number or font_number) + 1
    last_number = info_number

    start(1)
    close(b"<< /Type /Catalog /Pages 2 0 R >>\n")
    kids = [f"{3 + 2 * index} 0 R" for index in range(page_count)]
    start(2)
    close((f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {page_count} >>\n").encode("latin-1"))
    for index, text in enumerate(pages):
        page_number = 3 + 2 * index
        content_number = 4 + 2 * index
        has_image = jpeg is not None and index == page_count - 1
        resources = f"/Resources <</Font <</F1 {font_number} 0 R>>"
        if has_image:
            resources += f" /XObject <</Im0 {image_number} 0 R>>"
        resources += ">>"
        start(page_number)
        close(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {content_number} 0 R {resources} >>\n"
            ).encode("latin-1")
        )
        if has_image:
            content = f"q 240 0 0 160 50 600 cm /Im0 Do Q BT /F1 12 Tf 50 400 Td ({escape(text)}) Tj ET"
        else:
            content = f"BT /F1 12 Tf 50 700 Td ({escape(text)}) Tj ET"
        stream_object(content_number, f"/Length {len(content.encode('latin-1'))}", content.encode("latin-1"))
    start(font_number)
    close(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>\n")
    if image_number is not None:
        import PIL.Image

        with PIL.Image.open(io.BytesIO(jpeg)) as picture:
            width, height = picture.size
        stream_object(
            image_number,
            f"/Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceRGB "
            f"/BitsPerComponent 8 /Filter /DCTDecode /Length {len(jpeg)}",
            jpeg,
        )

    info_parts = ["/Producer (p18-t04-test-pdf)"]
    if author is not None:
        info_parts.append(f"/Author ({escape(author)})")
    if creation is not None:
        info_parts.append(f"/CreationDate ({creation})")
    if modification is not None:
        info_parts.append(f"/ModDate ({modification})")
    start(info_number)
    close(("<< " + " ".join(info_parts) + " >>\n").encode("latin-1"))

    xref_start = buffer.tell()
    buffer.write(b"xref\n0 %d\n" % (last_number + 1))
    buffer.write(b"0000000000 65535 f \n")
    for number in range(1, last_number + 1):
        buffer.write(("%010d 00000 n \n" % offsets[number]).encode("ascii"))
    buffer.write(
        b"trailer\n<< /Size "
        + str(last_number + 1).encode("ascii")
        + b" /Root 1 0 R /Info "
        + str(info_number).encode("ascii")
        + b" 0 R >>\nstartxref\n"
        + str(xref_start).encode("ascii")
        + b"\n%%EOF\n"
    )
    return buffer.getvalue()


def make_docx(
    paragraphs: tuple[str, ...] = ("paragraph one",),
    table: tuple[tuple[str, str], ...] = (),
    author: str | None = None,
    heading: bool = True,
) -> bytes:
    import docx

    document = docx.Document()
    if heading:
        document.add_heading("P18 T04 heading", level=1)
    for text in paragraphs:
        document.add_paragraph(text)
    if table:
        added = document.add_table(rows=len(table), cols=2)
        for row, (left, right) in zip(added.rows, table):
            row.cells[0].text = left
            row.cells[1].text = right
    if author is not None:
        document.core_properties.author = author
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def make_xlsx(sheets: dict[str, tuple[tuple[object, ...], ...]] | None = None) -> bytes:
    import openpyxl

    if sheets is None:
        sheets = {"Sheet1": (("a", "b"), (1, "text"))}
    workbook = openpyxl.Workbook()
    default = workbook.active
    used_default = "Sheet1" in sheets
    if used_default:
        target = default
        target.title = "Sheet1"
        for row in sheets["Sheet1"]:
            target.append(list(row))
    else:
        try:
            workbook.remove(default)
        except KeyError:
            pass
    for board, rows in sheets.items():
        if board == "Sheet1" and used_default:
            continue
        target = workbook.create_sheet(board)
        for row in rows:
            target.append(list(row))
    workbook.properties.creator = "p18-t04-test"
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def make_zip_bomb(member: str, inflated: int) -> bytes:
    """Small on-disk ZIP whose declared member size crosses the container guard."""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", b"small content types marker")
        archive.writestr(member, b"Q" * inflated)
    return buffer.getvalue()


class Harness:
    """One isolated selected root with a private store and engine."""

    def __init__(
        self,
        tmp: Path,
        *,
        limits: DocumentLimits | None = None,
        text_extensions: tuple[str, ...] = (".txt", ".md", ".markdown", ".csv", ".json", ".log"),
        max_text_bytes: int = 1024 * 1024,
    ) -> None:
        self.limits = limits if limits is not None else DocumentLimits()
        self.docs = tmp / "docs"
        self.docs.mkdir()
        self.root = SelectedRoot(self.docs)
        self.store = IndexStore(tmp / "cache" / "index.db")
        self.engine = IndexEngine(
            self.store,
            text_extensions=text_extensions,
            max_text_bytes=max_text_bytes,
            limits=self.limits,
        )

    def write(self, relative: str, data: bytes | str) -> Path:
        target = self.docs / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            data = data.encode("utf-8")
        target.write_bytes(data)
        return target

    def index(self, relative: str) -> bool:
        return self.engine.index_file(self.root, relative)

    def close(self) -> None:
        self.store.close()
