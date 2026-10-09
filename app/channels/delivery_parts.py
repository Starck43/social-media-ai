"""Conservative result helpers for checkpoint-aware, single-request delivery.

These helpers do not authorize destinations, retry, split or persist receipts.
Raw transport exceptions/provider descriptions must not enter this result.
"""

from typing import Any

# 408/409 and 5xx are deliberately absent: remote acceptance can be unclear.
REJECTED_HTTP_STATUSES = frozenset({400, 401, 403, 404, 413, 422, 429})


def valid_part(chat_id: str, text: str, limit: int) -> bool:
    if not isinstance(chat_id, str) or not chat_id.strip() or chat_id != chat_id.strip():
        return False
    if not isinstance(text, str) or not text.strip():
        return False
    try:
        # Conservative raw UTF-16 bound, including HTML markup/entities.
        return len(text.encode("utf-16-le")) // 2 <= limit
    except UnicodeEncodeError:
        return False


def receipt_id(value: Any) -> str | None:
    if type(value) is int and value > 0:
        return str(value)
    if isinstance(value, str) and value.strip() and value == value.strip():
        return value
    return None


def part_result(outcome: str, *, message_id: str | None = None, error_code: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"success": outcome == "sent", "outcome": outcome}
    if message_id is not None:
        result["message_id"] = message_id
    if error_code is not None:
        result["error_code"] = error_code
    return result
