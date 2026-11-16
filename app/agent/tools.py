"""Fail-closed, typed tool registration and shared authorized dispatch.

OpenAI wire helpers remain compatibility wrappers at the provider boundary.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Awaitable, Callable

from app.agent.confirmation import consume_tool_confirmation
from app.agent.tool_contracts import ToolBudget, ToolContract, ToolInputError, ToolResultError
from app.core.access_catalog import resolve_permission_key
from app.core.permissions import PermissionDeniedError, get_current_user, has_permission_by_codename
from app.services.llm_tool_serialization import tool_call, tool_schema

logger = logging.getLogger(__name__)
JSONSchema = dict[str, Any]


@dataclass(frozen=True, init=False)
class Tool:
    name: str
    description: str
    handler: Callable[..., Awaitable[Any]]
    requires_confirmation: bool
    required_permission: str
    budget: ToolBudget
    contract: ToolContract = field(repr=False)

    def __init__(
        self,
        name,
        description,
        parameters,
        handler,
        requires_confirmation=False,
        required_permission=None,
        budget=None,
    ):
        if not isinstance(name, str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,63}", name) is None:
            raise ValueError("Invalid tool name")
        resolved = resolve_permission_key(required_permission)
        if resolved is None or required_permission != f"{resolved[0].key}.{resolved[1].db_value}":
            raise ValueError("Tool requires a canonical catalog permission")
        if not isinstance(description, str) or not description.strip() or type(requires_confirmation) is not bool:
            raise ValueError("Invalid tool metadata")
        budget = ToolBudget() if budget is None else budget
        if not isinstance(budget, ToolBudget):
            raise ValueError("Invalid tool budget")
        contract = ToolContract(handler, parameters)
        for key, value in (
            ("name", name),
            ("description", description),
            ("handler", handler),
            ("requires_confirmation", requires_confirmation),
            ("required_permission", required_permission),
            ("budget", budget),
            ("contract", contract),
        ):
            object.__setattr__(self, key, value)

    @property
    def parameters(self) -> JSONSchema:
        return self.contract.parameters

    @property
    def confirm(self) -> bool:
        return self.requires_confirmation

    def to_openai(self) -> dict[str, Any]:
        """Compatibility shim; serialization belongs to the provider adapter."""
        return tool_schema(self)


_REGISTRY: dict[str, Tool] = {}
TOOL_REGISTRY = MappingProxyType(_REGISTRY)


def tool(
    name: str,
    description: str,
    parameters: JSONSchema,
    confirm: bool = False,
    required_permission: str | None = None,
    *,
    budget: ToolBudget | None = None,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Register a typed async handler with a mandatory canonical permission.

    No implicit/public policy exists. A future open tool needs a separately reviewed
    policy, not a sentinel or None. Invalid declarations never enter the registry.
    """

    def decorator(fn):
        if name in _REGISTRY:
            raise ValueError(f"Duplicate tool name: {name}")
        spec = Tool(name, description, parameters, fn, confirm, required_permission, budget)
        _REGISTRY[name] = spec
        return fn

    return decorator


def get_tool(name: str) -> Tool | None:
    return _REGISTRY.get(name)


def all_tools() -> list[Tool]:
    return list(_REGISTRY.values())


def to_openai_schemas() -> list[dict[str, Any]]:
    return [tool_schema(t) for t in _REGISTRY.values()]


def to_openai_call(call: dict[str, Any], call_id: str | None = None) -> dict[str, Any]:
    return tool_call(call, call_id)


def tool_specs() -> list[dict[str, Any]]:
    return to_openai_schemas()


class ToolError(Exception):
    """Handler error dict; confirmation replay treats this as failure."""


class ToolConfirmationRequiredError(PermissionError):
    """A confirmed tool lacks its exact one-use runtime grant."""


def _check_tool_confirmation(spec: Tool, arguments: dict[str, Any]) -> None:
    if spec.confirm and not consume_tool_confirmation(spec, arguments):
        raise ToolConfirmationRequiredError("Confirmation required")


def _check_tool_permission(spec: Tool) -> None:
    # Check fresh actor rights at every invocation, including confirmed dispatch.
    permission = spec.required_permission
    if resolve_permission_key(permission) is None or not has_permission_by_codename(get_current_user(), permission):
        raise PermissionDeniedError("Permission denied")


async def _invoke(spec: Tool, arguments: Any) -> Any:
    outcome = "rejected"
    try:
        _check_tool_permission(spec)
        checked = spec.contract.arguments(arguments, spec.budget)
        _check_tool_confirmation(spec, checked)
        async with asyncio.timeout(spec.budget.timeout_seconds):
            result = await spec.handler(**checked)
        result = spec.contract.result(result, spec.budget)
        outcome = "handler_error" if isinstance(result, dict) and result.get("error") else "ok"
        return result
    finally:
        # Deliberately excludes arguments, prompts, credentials, results and exception text.
        logger.info("agent_tool_dispatch name=%s outcome=%s", spec.name, outcome)


async def call_tool(name: str, arguments: dict[str, Any]) -> Any:
    spec = get_tool(name)
    if spec is None:
        raise LookupError("Unknown tool")
    result = await _invoke(spec, arguments)
    if isinstance(result, dict) and result.get("error"):
        raise ToolError(result["error"])
    return result


async def execute(name: str, arguments: dict[str, Any]) -> str:
    spec = get_tool(name)
    if spec is None:
        return json.dumps({"error": "Unknown tool", "code": "unknown_tool"})
    try:
        result = await _invoke(spec, arguments)
    except ToolConfirmationRequiredError:
        return json.dumps({"error": "Confirmation required", "code": "confirmation_required"})
    except PermissionDeniedError:
        return json.dumps({"error": "Permission denied", "code": "permission_denied"})
    except ToolInputError:
        return json.dumps({"error": "Invalid tool arguments", "code": "invalid_arguments"})
    except (ToolResultError, TimeoutError):
        return json.dumps({"error": "Tool contract or budget exceeded", "code": "tool_contract_error"})
    except Exception:
        return json.dumps({"error": "Tool execution failed", "code": "tool_failed"})
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False)


def confirmation_message(name: str, arguments: dict[str, Any]) -> str:
    """Human-readable confirmation request for a pending write tool."""
    args = ", ".join(f"{k}={v}" for k, v in (arguments or {}).items())
    return f"⚙️ Требуется подтверждение: {name}({args}).\nОтветьте «да» для выполнения или «нет» для отмены."


# Importing the toolset package registers all built-in tools via side effects
# on the shared module-level registry, so every consumer sees the same set.
from app.agent import toolset  # noqa: E402,F401
