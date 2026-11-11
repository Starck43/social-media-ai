"""Availability guard for the optional native PDF renderer (weasyprint/pango).

A host without the native stack must skip PDF rendering tests, not fail them:
`import weasyprint` raises OSError (not ImportError) when libpango is missing,
which `pytest.importorskip` does not catch. The probe renders once per process.
"""

import pytest

_probe: str | bool | None = None


def require_pdf_renderer() -> None:
    """Skip the calling test unless real PDF rendering works on this host."""
    global _probe
    if _probe is None:
        try:
            import weasyprint

            weasyprint.HTML(string="<p>probe</p>").write_pdf()
            _probe = True
        except (ImportError, OSError) as exc:  # Missing Python package or native pango stack.
            _probe = f"{type(exc).__name__}: {exc}"
    if _probe is not True:
        pytest.skip(f"PDF renderer unavailable on this host: {_probe}", allow_module_level=False)
