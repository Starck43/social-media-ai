"""Opt-in contracts for new business routers; legacy routes are not rewritten."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.schemas.business import BusinessError

logger = logging.getLogger(__name__)

BUSINESS_TAGS = {
    "crm": "CRM",
    "comms": "Communications",
    "planner": "Planner",
    "calendar": "Calendar",
    "social": "Social",
    "onec": "1C",
    "files": "Files",
    "documents": "Documents",
    "commands": "Commands",
}
ERRORS = {
    400: ("invalid_request", "Invalid request"),
    401: ("authentication_required", "Authentication required"),
    403: ("permission_denied", "Permission denied"),
    404: ("not_found", "Resource not found"),
    409: ("conflict", "Operation conflicts with current state"),
    422: ("validation_error", "Invalid request fields"),
    429: ("rate_limited", "Request limit exceeded"),
    500: ("internal_error", "Internal server error"),
    503: ("unavailable", "Service unavailable"),
}
_TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,128}", re.ASCII)


def openapi_options(environment: str, enabled: bool | None) -> dict:
    """Only recognized development/test environments default to public docs."""
    expose = enabled if enabled is not None else environment.strip().lower() in {"development", "test", "testing"}
    return {
        "openapi_url": "/openapi.json" if expose else None,
        "docs_url": "/docs" if expose else None,
        "redoc_url": "/redoc" if expose else None,
    }


def _error(request: Request, status: int) -> JSONResponse:
    status = status if status in ERRORS else 500
    code, message = ERRORS[status]
    request_id = getattr(request.state, "request_id", None)
    if not isinstance(request_id, str) or _TOKEN.fullmatch(request_id) is None:
        request_id = uuid.uuid4().hex
    body = BusinessError(code=code, message=message, request_id=request_id)
    return JSONResponse(body.model_dump(), status_code=status, headers={"X-Request-ID": request_id})


async def require_business_scope(request: Request) -> None:
    """Consume the current middleware membership; never trust a tenant header."""
    from app.core.tenant_context import current_tenant_id, is_bypass

    user = getattr(request.state, "api_user", None)
    tenant_id = current_tenant_id()
    if user is None or not user.is_active:
        raise HTTPException(401)
    if (
        is_bypass()
        or type(tenant_id) is not int
        or tenant_id <= 0
        or getattr(request.state, "tenant_id", None) != tenant_id
    ):
        raise HTTPException(403)


async def require_idempotency_key(request: Request) -> str:
    """Validate syntax only; command service must atomically bind actor + payload."""
    values = request.headers.getlist("Idempotency-Key")
    if len(values) != 1 or _TOKEN.fullmatch(values[0]) is None:
        raise HTTPException(400)
    return values[0]


class BusinessRoute(APIRoute):
    def __init__(self, *args, **kwargs):
        if kwargs.get("response_model") is None:
            raise ValueError("Business routes require an explicit response model")
        if not set(kwargs.get("tags") or ()) & set(BUSINESS_TAGS.values()):
            raise ValueError("Business routes require a registered tag")
        responses = dict(kwargs.get("responses") or {})
        for status, (_, message) in ERRORS.items():
            responses[status] = {"model": BusinessError, "description": message}
        kwargs["responses"] = responses
        extra = kwargs.get("openapi_extra") or {}
        if extra.get("x-creates-command"):
            if set(kwargs.get("methods") or ()) != {"POST"}:
                raise ValueError("Command creation requires POST")
            kwargs["dependencies"] = [*(kwargs.get("dependencies") or ()), Depends(require_idempotency_key)]
            kwargs["openapi_extra"] = {
                **extra,
                "parameters": [
                    *(
                        parameter
                        for parameter in extra.get("parameters", ())
                        if not (parameter.get("in") == "header" and parameter.get("name") == "Idempotency-Key")
                    ),
                    {
                        "in": "header",
                        "name": "Idempotency-Key",
                        "required": True,
                        "description": "Syntax only; command service owns atomic actor/payload replay binding",
                        "schema": {"type": "string", "pattern": r"^[A-Za-z0-9_.:-]{1,128}$"},
                    },
                ],
            }
        super().__init__(*args, **kwargs)

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def guarded(request: Request):
            try:
                return await handler(request)
            except HTTPException as error:
                # Deliberately do not serialize detail, provider text or payload.
                return _error(request, error.status_code)
            except RequestValidationError:
                return _error(request, 422)
            except Exception as error:
                logger.error("business_api_failure error_type=%s", type(error).__name__)
                return _error(request, 500)

        return guarded


def business_router(domain: str, *, permission: Callable) -> APIRouter:
    """Require caller-supplied per-action permission plus trusted workspace scope.

    Use one router per permission/action group; handlers still recheck execution
    rights. ApiScopeMiddleware remains the membership and authentication owner.
    """
    if domain not in BUSINESS_TAGS:
        raise ValueError("Unknown business API domain")
    return APIRouter(
        prefix=f"/{domain}",
        tags=[BUSINESS_TAGS[domain]],
        dependencies=[Depends(require_business_scope), Depends(permission)],
        route_class=BusinessRoute,
    )


def validate_business_routes(router: APIRouter) -> None:
    """Contract gate for every route in a new module; not a legacy-route waiver."""
    for route in router.routes:
        if not isinstance(route, BusinessRoute):
            raise ValueError("Business API must use BusinessRoute")
        if route.response_model is None or not set(route.tags) & set(BUSINESS_TAGS.values()):
            raise ValueError("Missing business response model or tag")
        if any(status not in route.responses for status in ERRORS):
            raise ValueError("Missing documented business errors")
