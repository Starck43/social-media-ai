"""OpenAI-compatible wire serialization for provider-neutral agent tool records."""

from __future__ import annotations

import json
from typing import Any


def tool_schema(spec: Any) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": spec.name, "description": spec.description, "parameters": spec.parameters},
    }


def tool_call(call: dict[str, Any], call_id: str | None = None) -> dict[str, Any]:
    args = call.get("arguments")
    arguments = args if isinstance(args, str) else json.dumps(args or {}, ensure_ascii=False)
    return {
        "id": call_id or call.get("id") or call.get("name") or "",
        "type": "function",
        "function": {"name": call.get("name") or "", "arguments": arguments},
    }
