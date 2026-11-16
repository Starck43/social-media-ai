"""Shared ZIP container guard for OOXML documents (DOCX/XLSX).

A DOCX or XLSX file is a ZIP archive. Before any parser touches it, the total
declared uncompressed size is checked so a small file with a compressed bomb
inside is rejected without decompressing it.
"""

from __future__ import annotations

import io
import zipfile


def inspect_zip(data: bytes, max_uncompressed_bytes: int) -> tuple[str, int]:
    """Return ``(status, uncompressed_total)`` without decompressing anything.

    Status is one of ``"ok"``, ``"too_large"`` or ``"invalid"``; the total is
    the declared uncompressed sum of the members that were listed.
    """

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            total = 0
            for info in archive.infolist():
                size = int(info.file_size) if info.file_size and info.file_size > 0 else 0
                total += size
                if total > max_uncompressed_bytes:
                    return "too_large", total
            return "ok", total
    except (zipfile.BadZipFile, EOFError, OSError, ValueError):
        return "invalid", 0
