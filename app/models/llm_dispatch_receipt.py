"""Receipt export and existing manager-binding convention."""

from .analysis.llm_dispatch_receipt import LLMDispatchReceipt
from .managers.llm_dispatch_receipt_manager import LLMDispatchReceiptManager

LLMDispatchReceipt.objects = LLMDispatchReceiptManager()
