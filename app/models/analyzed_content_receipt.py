"""Compatibility export for the canonical analysis receipt model."""

from .analysis.analyzed_content_receipt import AnalyzedContentReceipt
from .managers.analyzed_content_receipt_manager import AnalyzedContentReceiptManager  # noqa: E402

__all__ = ["AnalyzedContentReceipt", "AnalyzedContentReceiptManager"]
