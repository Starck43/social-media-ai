"""Compatibility aliases; canonical declarations live in domain packages."""

from .analysis.analyzed_content_receipt import AnalyzedContentReceipt
from .managers.analyzed_content_receipt_manager import AnalyzedContentReceiptManager

__all__ = ["AnalyzedContentReceipt", "AnalyzedContentReceiptManager"]
