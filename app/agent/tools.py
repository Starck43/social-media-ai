"""Tool framework for the agent: registry, OpenAI schemas, dispatch.

A tool is an async function exposed to the LLM through function calling.
Tools marked `requires_confirmation=True` are never executed by the model
directly — the runtime intercepts them and asks the owner to confirm in text
before dispatch (works identically on Telegram and MAX, no inline buttons
protocol per channel).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

JSONSchema = dict[str, Any]


@dataclass
class Tool:
    name: str
    description: str
    parameters: JSONSchema
    handler: Callable[..., Awaitable[Any]]
    requires_confirmation: bool = False
    required_permission: str | None = None  # "agenttask.create", "source.view", etc.

    @property
    def confirm(self) -> bool:
        """Alias used by runtime.py for confirmation-gating."""
        return self.requires_confirmation

    def to_openai(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


_REGISTRY: dict[str, Tool] = {}


def tool(
    name: str,
    description: str,
    parameters: JSONSchema,
    confirm: bool = False,
    required_permission: str | None = None,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Register an async function as an agent tool.

    ``required_permission`` is a dotted codename like ``"agenttask.create"``
    that is checked by the runtime before dispatch.  ``None`` (default) means
    no permission gate — every workspace member may call the tool.
    """

    def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        if name in _REGISTRY:
            raise ValueError(f"Duplicate tool name: {name}")
        _REGISTRY[name] = Tool(
            name=name,
            description=description,
            parameters=parameters,
            handler=fn,
            requires_confirmation=confirm,
            required_permission=required_permission,
        )
        return fn

    return decorator


def get_tool(name: str) -> Tool | None:
    return _REGISTRY.get(name)


def all_tools() -> list[Tool]:
    return list(_REGISTRY.values())


def to_openai_schemas() -> list[dict[str, Any]]:
    """Tool schemas for LLMClient.chat(tools=...)."""
    return [t.to_openai() for t in _REGISTRY.values()]


def to_openai_call(call: dict[str, Any], call_id: str | None = None) -> dict[str, Any]:
    """One tool call in the shape a chat API expects inside an assistant message.

    Everywhere else in the agent a call is flat — `{"id", "name", "arguments"}`,
    which is what tools dispatch on and what `agent_messages.tool_calls` stores.
    A chat API wants the *wire* shape instead: `arguments` nested under
    `function` and serialized to a JSON string, never a dict.

    Sending the flat shape back makes the provider reject the whole request with
    `messages[N].tool_calls[0].function must be an object`, which surfaces to the
    user as a bare "Ошибка LLM: 400" after any tool call. Both the replayed
    history and the in-flight loop of a single turn must go through here.
    """
    args = call.get("arguments")
    if isinstance(args, str):
        arguments = args
    else:
        arguments = json.dumps(args or {}, ensure_ascii=False)
    return {
        "id": call_id or call.get("id") or call.get("name") or "",
        "type": "function",
        "function": {"name": call.get("name") or "", "arguments": arguments},
    }


# Public aliases used by the runtime.
TOOL_REGISTRY: dict[str, Tool] = _REGISTRY


def tool_specs() -> list[dict[str, Any]]:
    """OpenAI-compatible schemas for every registered tool."""
    return to_openai_schemas()


async def call_tool(name: str, arguments: dict[str, Any]) -> Any:
    """Dispatch a tool call, returning the raw handler result.

    Unlike `execute`, errors propagate — the caller decides how to surface them.
    """
    tool_obj = _REGISTRY.get(name)
    if tool_obj is None:
        raise LookupError(f"Unknown tool: {name}")
    return await tool_obj.handler(**(arguments or {}))


async def execute(name: str, arguments: dict[str, Any]) -> str:
    """
    Run a tool and return a JSON string result (the `role=tool` payload).

    Errors never propagate: the model sees them and can correct course.
    """
    t = _REGISTRY.get(name)
    if t is None:
        return json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False)
    try:
        result = await t.handler(**(arguments or {}))
    except TypeError as e:
        return json.dumps({"error": f"Bad arguments for {name}: {e}"}, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Tool {name} failed: {e}", exc_info=True)
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, default=str)


def confirmation_message(name: str, arguments: dict[str, Any]) -> str:
    """Human-readable confirmation request for a pending write tool."""
    args = ", ".join(f"{k}={v}" for k, v in (arguments or {}).items())
    return f"⚙️ Требуется подтверждение: {name}({args}).\nОтветьте «да» для выполнения или «нет» для отмены."


# Importing the toolset package registers all built-in tools via side effects
# on the shared module-level registry, so every consumer sees the same set.
from app.agent import toolset  # noqa: E402,F401
