import asyncio
import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

import httpx

from app.core.config import settings
from app.models import LLMModel
from app.services.ai.json_schema_builder import validate_with_pydantic

logger = logging.getLogger(__name__)

_last_request_time: dict[str, float] = {}
MINUTE = 60

# AgentMessage.cost / DigestRun.llm_cost are plain USD floats; quantise to 1e-9
# so sub-cent calls from cheap models never round to "free".
_COST_SCALE = Decimal("0.000000001")


async def _record_llm_usage(model: LLMModel, success: bool, usage: dict | None = None) -> None:
    """Update model usage/health counters (last_used_at, last_success_at, last_error_at, use_count, fail_count).

    One UPDATE per model attempt — the per-client chat() call is the single
    recording point; chat_with_fallback must not record on top of it.
    Naive UTC matches the plain DateTime columns; NULL counters read as 0.
    """
    try:
        now = datetime.utcnow()
        updates = {"last_used_at": now, "use_count": (model.use_count or 0) + 1}
        if success:
            updates["last_success_at"] = now
        else:
            updates["last_error_at"] = now
            updates["fail_count"] = (model.fail_count or 0) + 1
        await LLMModel.objects.update_by_id(model.id, **updates)
        # Update local object for potential re-use in same request
        for k, v in updates.items():
            setattr(model, k, v)
    except Exception:
        # Never break the main flow for metrics
        logger.debug("Failed to record LLM usage for model %s", model.id, exc_info=True)


def price_usage_usd(model: LLMModel, prompt_tokens: int, completion_tokens: int) -> float:
    """Price token usage from llm_models tariffs (USD per 1K tokens) -> USD.

    Same formula as AIAnalyzer._price_usage but returns plain USD (that one
    returns USD cents for the ai_analytics column). Decimal throughout — tariffs
    have 4+ decimals and cheap models bill well under a cent per call; unknown
    tariffs contribute 0 (never hardcode rates).
    """
    usd = Decimal(prompt_tokens or 0) / 1000 * Decimal(str(getattr(model, "input_cost_per_1k", 0) or 0)) + (
        Decimal(completion_tokens or 0) / 1000 * Decimal(str(getattr(model, "output_cost_per_1k", 0) or 0))
    )
    if usd <= 0:
        return 0.0
    return float(usd.quantize(_COST_SCALE, rounding=ROUND_HALF_UP))


def _cap_text(m: LLMModel) -> bool:
    return m.model_type in ("text", "image")  # image-capable models also handle text


def default_model_sort_key(m: LLMModel) -> tuple[bool, bool, int]:
    """Order key for default-model resolution.

    Shared by every place that picks "the" model (digest, agent fallback
    chain, settings dropdown): default provider first, then the default
    model, then lowest id. Same tuple as LLMModelManager's resolvers, so the
    admin flags predict the runtime choice. A missing relation is treated as
    non-default so the picker never crashes — it just deprioritises the row.
    """
    provider = getattr(m, "provider", None)
    return (
        not bool(getattr(provider, "is_default", False)),
        not bool(getattr(m, "is_default", False)),
        int(getattr(m, "id", 0) or 0),
    )


async def _allowed_model_types() -> Optional[set[str]]:
    """Model types the ambient workspace's tier may use, or None when unfiltered.

    `starter` buys text, so an image model in the global fleet must not be chosen
    for it — the tier is a promise about what is billed, and routing a `starter`
    workspace to a vision model breaks it. Returns None (allow everything) when
    no workspace is in scope, so operator-level calls and seeding still work.
    """
    from app.core.tenant_context import current_tenant_id
    from app.models.managers.tenant_manager import tenants

    tenant_id = current_tenant_id()
    if tenant_id is None:
        return None
    tenant = await tenants.get(id=tenant_id)
    if tenant is None:
        return None
    allowed = tenant.plan_limits().get("model_types")
    return None if allowed is None else set(allowed)


# ──────────────────────────────────────────────────────────────
# Base client
# ──────────────────────────────────────────────────────────────


class LLMClient(ABC):
    def __init__(self, model: LLMModel):
        self.provider = model.provider
        self.model = model
        self.api_key = model.provider.get_api_key()
        self.base_url = model.provider.base_url.rstrip("/")
        self.model_name = model.model_id
        self.max_tokens = model.max_tokens
        self.default_temperature = model.default_temperature
        self.timeout = float(getattr(settings, "LLM_DEFAULT_TIMEOUT", 60.0))

    @classmethod
    def create(cls, model: LLMModel) -> "LLMClient":
        return LLMClientFactory.create(model)

    def _usage_block(self, prompt_tokens: int, completion_tokens: int) -> dict[str, Any]:
        """Normalized usage: token counts + priced USD cost.

        `cost` is what the daily cap and DigestRun.llm_cost account from; keep
        the key present (0.0 when tariffs are unknown) so callers never branch.
        """
        prompt_tokens = int(prompt_tokens or 0)
        completion_tokens = int(completion_tokens or 0)
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost": price_usage_usd(self.model, prompt_tokens, completion_tokens),
        }

    @abstractmethod
    async def analyze(
        self, prompt: str, media_urls: Optional[list[str]] = None, pydantic_model: Optional[type] = None, **kwargs
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def chat(
        self, messages: list[dict[str, Any]], tools: Optional[list[dict[str, Any]]] = None, **kwargs
    ) -> dict[str, Any]: ...

    async def _rate_limit(self):
        key = self.provider.name.lower()
        now = asyncio.get_event_loop().time()
        delay = float(getattr(settings, "LLM_REQUEST_DELAY", 2000)) / 1000.0
        if key in _last_request_time:
            elapsed = now - _last_request_time[key]
            if elapsed < delay:
                await asyncio.sleep(delay - elapsed)
        _last_request_time[key] = asyncio.get_event_loop().time()


# ──────────────────────────────────────────────────────────────
# OpenAI-compatible client
# ──────────────────────────────────────────────────────────────


class OpenAICompatibleClient(LLMClient):
    async def analyze(
        self, prompt: str, media_urls: Optional[list[str]] = None, pydantic_model: Optional[type] = None, **kwargs
    ) -> dict[str, Any]:
        if not self.api_key:
            raise ValueError(f"API key not set for {self.provider.name}")
        await self._rate_limit()

        payload = self._prepare_analyze(prompt, media_urls, **kwargs)
        timeout = min(self.timeout, 60.0)

        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                    timeout=timeout,
                )
                if r.status_code != 200:
                    raise httpx.HTTPStatusError(f"{r.status_code}: {r.text}", request=r.request, response=r)
                data = r.json()
        except httpx.TimeoutException:
            logger.error(f"Timeout for {self.provider.name} after {timeout}s")
            await _record_llm_usage(self.model, success=False)
            return {"request": payload, "response": {"error": "timeout"}, "parsed": {"analysis": "Timeout"}}
        except Exception as e:
            logger.error(f"Unexpected error for {self.provider.name}: {e}")
            await _record_llm_usage(self.model, success=False)
            return {"request": payload, "response": {"error": str(e)}, "parsed": {"analysis": f"Error: {e}"}}

        parsed = self._parse_response(data)
        if pydantic_model is not None:
            try:
                parsed = validate_with_pydantic(parsed, pydantic_model, strict=True)
            except Exception as exc:
                logger.warning("Pydantic validation failed for %s: %s", self.provider.name, exc)
                data["error"] = "invalid_structured_output"
                parsed = {}

        response = {
            "request": {"model": self.model_name, "prompt": prompt, "provider": self.provider.name.lower()},
            "response": data,
            "parsed": parsed,
            "usage": self._usage_block(
                (data.get("usage") or {}).get("prompt_tokens", 0), (data.get("usage") or {}).get("completion_tokens", 0)
            ),
        }
        await _record_llm_usage(self.model, success=not bool(data.get("error")), usage=response.get("usage"))
        return response

    async def chat(
        self, messages: list[dict[str, Any]], tools: Optional[list[dict[str, Any]]] = None, **kwargs
    ) -> dict[str, Any]:
        if not self.api_key:
            raise ValueError(f"API key not set for {self.provider.name}")
        await self._rate_limit()

        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": float(kwargs.pop("temperature", self.default_temperature)),
            "max_tokens": int(kwargs.pop("max_tokens", self.max_tokens)),
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = kwargs.pop("tool_choice", "auto")
        payload.update(kwargs)

        timeout = min(float(payload.get("timeout", self.timeout)), 60.0)

        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(
                    f"{self.base_url}/chat/completions", headers=self._headers(), json=payload, timeout=timeout
                )
                if r.status_code != 200:
                    raise httpx.HTTPStatusError(f"{r.status_code}: {r.text}", request=r.request, response=r)
                data = r.json()

            response = self._parse_chat(data)
            await _record_llm_usage(self.model, success=not bool(data.get("error")), usage=response.get("usage"))
            return response
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.TransportError):
            await _record_llm_usage(self.model, success=False)
            raise

    # ── internal helpers ─────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        header_name = self.provider.auth_header or "Authorization: Bearer {key}"
        if "{key}" in header_name:
            h["Authorization"] = f"Bearer {self.api_key}"
        else:
            k, _, v = header_name.partition(": ")
            h[k] = v.format(key=self.api_key) if "{key}" in v else v
        return h

    def _prepare_analyze(self, prompt: str, media_urls: Optional[list[str]], **kwargs) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _system_prompt(bool(media_urls))},
        ]
        if media_urls:
            content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
            content += [{"type": "image_url", "image_url": {"url": u}} for u in media_urls]
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "temperature": float(kwargs.pop("temperature", self.default_temperature)),
            "max_tokens": int(kwargs.pop("max_tokens", self.max_tokens)),
            "stream": False,
        }
        if not media_urls:
            payload["response_format"] = {"type": "json_object"}
        payload.update(kwargs)
        return payload

    def _parse_response(self, response: dict) -> dict[str, Any]:
        choices = response.get("choices") or [{}]
        content = (choices[0].get("message") or {}).get("content")
        return _try_json(content)

    def _parse_chat(self, data: dict) -> dict[str, Any]:
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        tool_calls: list[dict[str, Any]] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args = fn.get("arguments") or "{}"
            try:
                parsed = json.loads(args) if isinstance(args, str) else dict(args)
            except json.JSONDecodeError:
                parsed = {"_raw": args}
            tool_calls.append(
                {"id": tc.get("id") or fn.get("name", ""), "name": fn.get("name", ""), "arguments": parsed}
            )
        usage = data.get("usage") or {}
        block = self._usage_block(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        block["total_tokens"] = usage.get("total_tokens", 0) or block["total_tokens"]
        return {
            "content": msg.get("content"),
            "tool_calls": tool_calls,
            "usage": block,
            "finish_reason": choice.get("finish_reason"),
            "raw": data,
        }


# ──────────────────────────────────────────────────────────────
# Anthropic client (real implementation)
# ──────────────────────────────────────────────────────────────


class AnthropicClient(LLMClient):
    ANTHROPIC_VERSION = "2023-06-01"

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": self.ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }

    async def analyze(
        self, prompt: str, media_urls: Optional[list[str]] = None, pydantic_model: Optional[type] = None, **kwargs
    ) -> dict[str, Any]:
        if not self.api_key:
            raise ValueError(f"API key not set for {self.provider.name}")
        await self._rate_limit()
        system = _system_prompt(bool(media_urls))
        content = prompt
        if media_urls:
            blocks: list[dict] = [{"type": "text", "text": prompt}]
            blocks += [{"type": "image", "source": {"type": "url", "url": u}} for u in media_urls]
            content = blocks

        payload = {
            "model": self.model_name,
            "max_tokens": int(kwargs.pop("max_tokens", self.max_tokens)),
            "temperature": float(kwargs.pop("temperature", self.default_temperature)),
            "system": system,
            "messages": [{"role": "user", "content": content}],
        }
        timeout = min(self.timeout, 60.0)
        request_meta = {"model": self.model_name, "prompt": prompt, "provider": self.provider.name.lower()}
        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(f"{self.base_url}/messages", headers=self._headers(), json=payload, timeout=timeout)
                if r.status_code != 200:
                    raise httpx.HTTPStatusError(f"{r.status_code}: {r.text}", request=r.request, response=r)
                data = r.json()
        except httpx.TimeoutException:
            logger.error(f"Anthropic timeout after {timeout}s")
            await _record_llm_usage(self.model, success=False)
            return {"request": request_meta, "response": {"error": "timeout"}, "parsed": {"analysis": "Timeout"}}
        except Exception as e:
            logger.error(f"Anthropic error: {e}")
            await _record_llm_usage(self.model, success=False)
            return {"request": request_meta, "response": {"error": str(e)}, "parsed": {"analysis": f"Error: {e}"}}

        text = "\n".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text")
        parsed = _try_json(text)
        if pydantic_model is not None:
            try:
                parsed = validate_with_pydantic(parsed, pydantic_model, strict=True)
            except Exception as exc:
                logger.warning("Pydantic validation failed for Anthropic %s: %s", self.provider.name, exc)
                data["error"] = "invalid_structured_output"
                parsed = {}

        usage = data.get("usage") or {}
        response = {
            "request": {"model": self.model_name, "prompt": prompt, "provider": self.provider.name.lower()},
            "response": data,
            "parsed": parsed,
            "usage": self._usage_block(usage.get("input_tokens", 0), usage.get("output_tokens", 0)),
        }
        await _record_llm_usage(self.model, success=not bool(data.get("error")), usage=response.get("usage"))
        return response

    async def chat(
        self, messages: list[dict[str, Any]], tools: Optional[list[dict[str, Any]]] = None, **kwargs
    ) -> dict[str, Any]:
        if not self.api_key:
            raise ValueError(f"API key not set for {self.provider.name}")
        await self._rate_limit()

        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        chat_msgs = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]

        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": chat_msgs,
            "max_tokens": int(kwargs.pop("max_tokens", self.max_tokens)),
            "temperature": float(kwargs.pop("temperature", self.default_temperature)),
            "stream": False,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "input_schema": t["function"]["parameters"],
                }
                for t in tools
            ]

        timeout = min(self.timeout, 60.0)
        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(f"{self.base_url}/messages", headers=self._headers(), json=payload, timeout=timeout)
                if r.status_code != 200:
                    raise httpx.HTTPStatusError(f"{r.status_code}: {r.text}", request=r.request, response=r)
                data = r.json()

            response = self._parse_chat(data)
            await _record_llm_usage(self.model, success=not bool(data.get("error")), usage=response.get("usage"))
            return response
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.TransportError):
            await _record_llm_usage(self.model, success=False)
            raise

    def _parse_chat(self, data: dict) -> dict[str, Any]:
        content_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in data.get("content") or []:
            if block.get("type") == "text":
                content_parts.append(block["text"])
            elif block.get("type") == "tool_use":
                tool_calls.append({"id": block["id"], "name": block["name"], "arguments": block.get("input", {})})

        usage = data.get("usage") or {}
        return {
            "content": "\n".join(content_parts) or None,
            "tool_calls": tool_calls,
            "usage": self._usage_block(usage.get("input_tokens", 0), usage.get("output_tokens", 0)),
            "finish_reason": data.get("stop_reason"),
            "raw": data,
        }


# ──────────────────────────────────────────────────────────────
# Custom client (generic, per-model endpoint override)
# ──────────────────────────────────────────────────────────────


class CustomClient(LLMClient):
    async def analyze(
        self, prompt: str, media_urls: Optional[list[str]] = None, pydantic_model: Optional[type] = None, **kwargs
    ) -> dict[str, Any]:
        if not self.api_key:
            raise ValueError(f"API key not set for {self.provider.name}")
        await self._rate_limit()

        endpoint = self.model.custom_endpoint_path
        if not endpoint:
            raise ValueError(f"Model {self.model.name} has api_format=custom but no custom_endpoint_path set")
        payload: dict[str, Any] = {"model": self.model_name, "prompt": prompt}
        if media_urls:
            payload["media_urls"] = media_urls
        payload.update(kwargs)

        timeout = min(self.timeout, 60.0)
        request_meta = {
            "model": self.model_name,
            "prompt": prompt,
            "provider": self.provider.name.lower(),
            "endpoint": endpoint,
        }

        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(f"{self.base_url}{endpoint}", headers=self._headers(), json=payload, timeout=timeout)
                if r.status_code != 200:
                    raise httpx.HTTPStatusError(f"{r.status_code}: {r.text}", request=r.request, response=r)
                data = r.json()
        except httpx.TimeoutException:
            logger.error(f"Custom endpoint timeout for {self.provider.name} after {timeout}s")
            await _record_llm_usage(self.model, success=False)
            return {"request": request_meta, "response": {"error": "timeout"}, "parsed": {"analysis": "Timeout"}}
        except Exception as e:
            logger.error(f"Custom endpoint error for {self.provider.name}: {e}")
            await _record_llm_usage(self.model, success=False)
            return {"request": request_meta, "response": {"error": str(e)}, "parsed": {"analysis": f"Error: {e}"}}

        content = _extract_custom_content(data)
        parsed = _try_json(content) if isinstance(content, str) else content
        if pydantic_model is not None:
            try:
                parsed = validate_with_pydantic(parsed, pydantic_model, strict=True)
            except Exception as exc:
                logger.warning("Pydantic validation failed for custom %s: %s", self.provider.name, exc)
                data["error"] = "invalid_structured_output"
                parsed = {}

        response = {
            "request": request_meta,
            "response": data,
            "parsed": parsed,
            "usage": self._usage_block(0, 0),
        }
        await _record_llm_usage(self.model, success=not bool(data.get("error")))
        return response

    async def chat(
        self, messages: list[dict[str, Any]], tools: Optional[list[dict[str, Any]]] = None, **kwargs
    ) -> dict[str, Any]:
        if not self.api_key:
            raise ValueError(f"API key not set for {self.provider.name}")
        await self._rate_limit()

        endpoint = self.model.custom_endpoint_path
        if not endpoint:
            raise ValueError(f"Model {self.model.name} has api_format=custom but no custom_endpoint_path set")
        payload: dict[str, Any] = {"model": self.model_name, "messages": messages}
        if tools:
            payload["tools"] = tools
        payload.update(kwargs)

        timeout = min(self.timeout, 60.0)
        request_meta = {"model": self.model_name, "provider": self.provider.name.lower(), "endpoint": endpoint}

        try:
            async with httpx.AsyncClient() as c:
                r = await c.post(f"{self.base_url}{endpoint}", headers=self._headers(), json=payload, timeout=timeout)
                if r.status_code != 200:
                    raise httpx.HTTPStatusError(f"{r.status_code}: {r.text}", request=r.request, response=r)
                data = r.json()
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.TransportError):
            await _record_llm_usage(self.model, success=False)
            raise

        content = _extract_custom_content(data)
        return {
            "content": content,
            "tool_calls": [],
            "usage": self._usage_block(0, 0),
            "finish_reason": None,
            "raw": data,
        }

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        header_name = self.provider.auth_header or "Authorization: Bearer {key}"
        if "{key}" in header_name:
            h["Authorization"] = f"Bearer {self.api_key}"
        else:
            k, _, v = header_name.partition(": ")
            h[k] = v.format(key=self.api_key) if "{key}" in v else v
        return h


def _extract_custom_content(data: dict) -> Any:
    for key in ("answers", "content", "text", "result", "output", "response"):
        val = data.get(key)
        if val is not None:
            return val
    if isinstance(data, dict) and len(data) == 1:
        return next(iter(data.values()))
    return data


# ──────────────────────────────────────────────────────────────
# Factory
# ──────────────────────────────────────────────────────────────


class LLMClientFactory:
    _clients: dict[str, type[LLMClient]] = {
        "openai": OpenAICompatibleClient,
        "anthropic": AnthropicClient,
        "custom": CustomClient,
    }

    @classmethod
    def create(cls, model: LLMModel) -> LLMClient:
        if model is None:
            raise ValueError("LLMModel is required")
        fmt = model.provider.api_format.lower()
        client_cls = cls._clients.get(fmt)
        if client_cls is None:
            logger.warning(f"Unknown api_format '{fmt}' for provider '{model.provider.name}', using OpenAI-compatible")
            client_cls = OpenAICompatibleClient
        return client_cls(model)


# ──────────────────────────────────────────────────────────────
# Fallback chain
# ──────────────────────────────────────────────────────────────


async def _llm_models(query) -> list[LLMModel]:
    """Load models with their provider attached.

    `prefetch_related` is not optional: the factory and the fallback ordering
    both read `model.provider` after the session is gone.
    """
    return list(await query.prefetch_related("provider"))


async def chat_with_fallback(
    messages: list[dict[str, Any]], tools: Optional[list[dict[str, Any]]] = None,
    preferred_model: Optional[LLMModel] = None, **kwargs
) -> dict[str, Any]:
    models = await _llm_models(LLMModel.objects.filter(is_active=True).order_by(LLMModel.id))

    allowed_types = await _allowed_model_types()
    text_models = [m for m in models if _cap_text(m) and (allowed_types is None or m.model_type in allowed_types)]
    if not text_models and allowed_types is not None:
        logger.warning("Workspace tier allows only %s models; none is active", sorted(allowed_types))
    text_models = [m for m in text_models if m.provider and m.provider.is_active]
    # The tier filter always wins over the preferred global model. If the
    # cost-efficient preference fails/is disallowed, keep fallback economical.
    text_models.sort(key=lambda m: (
        m.id != getattr(preferred_model, "id", None),
        ((m.input_cost_per_1k or 0) + (m.output_cost_per_1k or 0)) if preferred_model is not None else 0,
        default_model_sort_key(m),
    ))

    last_err: Optional[Exception] = None
    for model in text_models:
        try:
            client = LLMClientFactory.create(model)
            # Usage is recorded inside client.chat (single recording point).
            return await client.chat(messages, tools=tools, **kwargs)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if code == 429:
                last_err = e
                logger.warning(f"{model.provider.name}/{model.model_id} rate-limited (429), trying next")
            elif code < 500:
                # 4xx errors: if tools were provided and error suggests tools/function
                # calling is not supported, try next model instead of raising immediately.
                if tools and _is_tools_not_supported_error(e):
                    last_err = e
                    logger.warning(
                        f"{model.provider.name}/{model.model_id} appears not to support tools ({code}), trying next"
                    )
                else:
                    raise
            else:
                last_err = e
                logger.warning(f"{model.provider.name}/{model.model_id} failed ({code}), trying next")
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last_err = e
            logger.warning(f"{model.provider.name}/{model.model_id} timeout/transport, trying next")
    raise last_err or RuntimeError("No LLM models available")


def _is_tools_not_supported_error(e: httpx.HTTPStatusError) -> bool:
    """Check if a 4xx error indicates that tools/function calling is not supported."""
    try:
        error_data = e.response.json()
        error_msg = str(error_data).lower()
    except Exception:
        error_msg = e.response.text.lower()

    # Keywords that suggest tools/function calling is not supported
    unsupported_keywords = [
        "tool",
        "function",
        "unsupported",
        "not supported",
        "not implemented",
        "invalid_request_error",
        "upstream",
        "rejected",
        "capability",
    ]
    return any(kw in error_msg for kw in unsupported_keywords)


async def resolve_model() -> Optional[LLMModel]:
    if settings.AGENT_MODEL:
        m = await _llm_models(LLMModel.objects.filter(name=settings.AGENT_MODEL))
        m = m[0] if m else None
        if m:
            return m
        logger.warning(f"AGENT_MODEL '{settings.AGENT_MODEL}' not found, falling back")

    models = await _llm_models(LLMModel.objects.filter(is_active=True).order_by(LLMModel.id))
    # An explicit AGENT_MODEL is honoured even on a tier that does not include
    # its type: it is an operator's deliberate override of the runtime, and a
    # blank env var must not turn into "no model at all". The automatic choice
    # below is filtered.
    allowed_types = await _allowed_model_types()
    models.sort(key=default_model_sort_key)
    for m in models:
        if _cap_text(m) and (allowed_types is None or m.model_type in allowed_types):
            return m
    if allowed_types is None:
        logger.error("No active text LLM model available")
    else:
        logger.error("No active LLM model of type %s available for this workspace's tier", sorted(allowed_types))
    return None


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────


def _system_prompt(has_media: bool) -> str:
    if has_media:
        return "Ты - аналитик социальных сетей. Анализируй изображения и текст. Дай краткую аннотацию."
    return "Ты - аналитик социальных сетей. Анализируй посты. Отвечай в формате JSON."


def _try_json(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"analysis": text}
    return parsed if isinstance(parsed, dict) else {"analysis": text}


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip().replace("$", "").replace("USD", "").strip().lower()
        try:
            return float(s)
        except ValueError:
            return None
    return None


def extract_cost_info(raw_response: Any) -> dict[str, Any]:
    """Extract token usage and reported dollar costs from a raw LLM API response.

    Understands OpenAI/Anthropic usage plus common provider cost extensions
    (OpenRouter `cost`, LiteLLM proxy `prompt_cost`/`completion_cost`/`total_cost`).

    Returns:
        tokens:      {"input": int, "output": int, "total": int}
        cost:        {"input": float|None, "output": float|None, "total": float|None}
        price_per_1k: {"input": float|None, "output": float|None}  (derived from cost/tokens)
        has_cost_report: bool
    """
    empty = {
        "tokens": {"input": 0, "output": 0, "total": 0},
        "cost": {"input": None, "output": None, "total": None},
        "price_per_1k": {"input": None, "output": None},
        "has_cost_report": False,
    }
    if not isinstance(raw_response, dict):
        return empty

    usage = raw_response.get("usage")
    if not isinstance(usage, dict) or not usage:
        # Some providers report cost at the top level of the response
        if _num(raw_response.get("total_cost")) is not None or _num(raw_response.get("cost")) is not None:
            return {
                "tokens": {"input": 0, "output": 0, "total": 0},
                "cost": {
                    "input": None,
                    "output": None,
                    "total": _num(raw_response.get("total_cost") or raw_response.get("cost")),
                },
                "price_per_1k": {"input": None, "output": None},
                "has_cost_report": True,
            }
        return empty

    input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)

    input_cost = _num(usage.get("prompt_cost") or usage.get("input_cost"))
    output_cost = _num(usage.get("completion_cost") or usage.get("output_cost"))
    total_cost = _num(usage.get("total_cost") or usage.get("cost"))
    if total_cost is None:
        total_cost = _num(raw_response.get("total_cost") or raw_response.get("cost"))

    has_report = any(v is not None for v in (input_cost, output_cost, total_cost))

    def per_1k(cost: Optional[float], tokens: int) -> Optional[float]:
        if cost is None or tokens <= 0:
            return None
        return round(cost / tokens * 1000, 10)

    return {
        "tokens": {
            "input": input_tokens,
            "output": output_tokens,
            "total": input_tokens + output_tokens,
        },
        "cost": {"input": input_cost, "output": output_cost, "total": total_cost},
        "price_per_1k": {
            "input": per_1k(input_cost, input_tokens),
            "output": per_1k(output_cost, output_tokens),
        },
        "has_cost_report": has_report,
    }
