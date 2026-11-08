"""Per-file resource limits for bounded text extraction and OCR.

Every bound is enforced before parsing or before an external process starts, so
a malformed or pathologically compressed document can only cost bounded CPU,
bounded memory and bounded output. The defaults are development-scaffold
budgets, not approved production budgets.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DocumentLimits:
    """Bounded budget shared by every extractor and OCR invocation."""

    # Size and output budget for one file.
    max_document_bytes: int = 50 * 1024 * 1024
    max_document_characters: int = 2_000_000
    max_embedded_image_bytes: int = 20 * 1024 * 1024
    # Wall-clock budget for the whole extraction of one file.
    max_document_seconds: float = 60.0

    # ZIP container (DOCX/XLSX) decompression bomb guard, checked before parse.
    max_uncompressed_bytes: int = 64 * 1024 * 1024

    # Container content budgets.
    max_pdf_pages: int = 50
    max_pdf_ocr_images: int = 100
    max_docx_paragraphs: int = 20_000
    max_docx_table_rows: int = 50_000
    max_xlsx_sheets: int = 100
    max_xlsx_rows: int = 20_000
    max_xlsx_cells_per_row: int = 512

    # OCR budget per single Tesseract subprocess.
    ocr_languages: tuple[str, ...] = ("rus", "eng")
    ocr_cpu_seconds: int = 120
    ocr_wall_seconds: float = 300.0
    ocr_memory_bytes: int = 2 * 1024 * 1024 * 1024
    # Optional pinned binary; None means resolve `tesseract` from PATH.
    ocr_binary: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "max_document_bytes",
            "max_document_characters",
            "max_embedded_image_bytes",
            "max_document_seconds",
            "max_uncompressed_bytes",
            "max_pdf_pages",
            "max_pdf_ocr_images",
            "max_docx_paragraphs",
            "max_docx_table_rows",
            "max_xlsx_sheets",
            "max_xlsx_rows",
            "max_xlsx_cells_per_row",
            "ocr_cpu_seconds",
            "ocr_wall_seconds",
            "ocr_memory_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"{name} must be positive")
        if not self.ocr_languages or any(not lang or len(lang) > 32 for lang in self.ocr_languages):
            raise ValueError("ocr_languages names are bounded")
        if any("+ " in lang for lang in self.ocr_languages):
            raise ValueError("ocr_languages names must not contain separators")

    def with_ocr_budget(self) -> "OcrBudget":
        return OcrBudget(
            languages=tuple(self.ocr_languages),
            cpu_seconds=int(self.ocr_cpu_seconds),
            wall_seconds=float(self.ocr_wall_seconds),
            memory_bytes=int(self.ocr_memory_bytes),
            binary=self.ocr_binary,
        )


@dataclass(frozen=True)
class OcrBudget:
    """One bounded Tesseract subprocess invocation."""

    languages: tuple[str, ...]
    cpu_seconds: int
    wall_seconds: float
    memory_bytes: int
    binary: str | None = None

    def __post_init__(self) -> None:
        if not self.languages or any(not lang or len(lang) > 32 for lang in self.languages):
            raise ValueError("ocr_languages names are bounded")
        if any(("+" in lang or " " in lang or "\t" in lang) for lang in self.languages):
            raise ValueError("ocr_languages names must not contain separators")
        if not isinstance(self.cpu_seconds, int) or isinstance(self.cpu_seconds, bool) or self.cpu_seconds < 1:
            raise ValueError("cpu_seconds must be a positive integer")
        if self.wall_seconds <= 0:
            raise ValueError("wall_seconds must be positive")
        if not isinstance(self.memory_bytes, int) or isinstance(self.memory_bytes, bool) or self.memory_bytes < 0:
            raise ValueError("memory_bytes must not be negative")
