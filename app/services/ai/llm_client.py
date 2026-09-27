import asyncio
import json
import logging
from abc import ABC, abstractmethod
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

import httpx

from app.core.config import settings
from app.models import LLMModel

logger = logging.getLogger(__name__)

_last_request_time: dict[str, float] = {}
MINUTE = 60

# AgentMessage.cost / DigestRun.llm_cost are plain USD floats; quantise to 1e-9
# so sub-cent calls from cheap models never round to "free".
_COST_SCALE = Decimal("0.000000001")


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
    async def analyze(self, prompt: str, media_urls: Optional[list[str]] = None, **kwargs) -> dict[str, Any]: ...

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
    async def analyze(self, prompt: str, media_urls: Optional[list[str]] = None, **kwargs) -> dict[str, Any]:
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
            return {"request": payload, "response": {"error": "timeout"}, "parsed": {"analysis": "Timeout"}}
        except Exception as e:
            logger.error(f"Unexpected error for {self.provider.name}: {e}")
            return {"request": payload, "response": {"error": str(e)}, "parsed": {"analysis": f"Error: {e}"}}

        return {
            "request": {"model": self.model_name, "prompt": prompt, "provider": self.provider.name.lower()},
            "response": data,
            "parsed": self._parse_response(data),
            "usage": self._usage_block(
                (data.get("usage") or {}).get("prompt_tokens", 0), (data.get("usage") or {}).get("completion_tokens", 0)
            ),
        }

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

        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{self.base_url}/chat/completions", headers=self._headers(), json=payload, timeout=timeout
            )
            if r.status_code != 200:
                raise httpx.HTTPStatusError(f"{r.status_code}: {r.text}", request=r.request, response=r)
            data = r.json()

        return self._parse_chat(data)

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
        try:
            content = response.get("choices", [{}])[0].get("message", {}).get("content", "{}")
            return json.loads(content)
        except (json.JSONDecodeError, KeyError, IndexError):
            return {"analysis": content}

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

    async def analyze(self, prompt: str, media_urls: Optional[list[str]] = None, **kwargs) -> dict[str, Any]:
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
            return {"request": request_meta, "response": {"error": "timeout"}, "parsed": {"analysis": "Timeout"}}
        except Exception as e:
            logger.error(f"Anthropic error: {e}")
            return {"request": request_meta, "response": {"error": str(e)}, "parsed": {"analysis": f"Error: {e}"}}

        text = "\n".join(b.get("text", "") for b in data.get("content") or [] if b.get("type") == "text")
        usage = data.get("usage") or {}
        return {
            "request": {"model": self.model_name, "prompt": prompt, "provider": self.provider.name.lower()},
            "response": data,
            "parsed": _try_json(text),
            "usage": self._usage_block(usage.get("input_tokens", 0), usage.get("output_tokens", 0)),
        }

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
        async with httpx.AsyncClient() as c:
            r = await c.post(f"{self.base_url}/messages", headers=self._headers(), json=payload, timeout=timeout)
            if r.status_code != 200:
                raise httpx.HTTPStatusError(f"{r.status_code}: {r.text}", request=r.request, response=r)
            data = r.json()

        return self._parse_chat(data)

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
# Factory
# ──────────────────────────────────────────────────────────────


class LLMClientFactory:
    _clients: dict[str, type[LLMClient]] = {"openai": OpenAICompatibleClient, "anthropic": AnthropicClient}

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


async def chat_with_fallback(
    messages: list[dict[str, Any]], tools: Optional[list[dict[str, Any]]] = None, **kwargs
) -> dict[str, Any]:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.core.database import new_session

    async with new_session() as db:
        r = await db.execute(
            select(LLMModel)
            .options(selectinload(LLMModel.provider))
            .where(LLMModel.is_active == True)
            .order_by(LLMModel.id)
        )
        models = r.scalars().unique().all()

    text_models = [m for m in models if _cap_text(m)]
    text_models.sort(key=lambda m: (not m.provider.is_default, m.id))

    last_err: Optional[Exception] = None
    for model in text_models:
        try:
            client = LLMClientFactory.create(model)
            return await client.chat(messages, tools=tools, **kwargs)
        except httpx.HTTPStatusError as e:
            if e.response.status_code < 500:
                raise
            last_err = e
            logger.warning(f"{model.provider.name}/{model.model_id} failed ({e.response.status_code}), trying next")
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last_err = e
            logger.warning(f"{model.provider.name}/{model.model_id} timeout/transport, trying next")
    raise last_err or RuntimeError("No LLM models available")


async def resolve_model() -> Optional[LLMModel]:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.core.database import new_session

    async with new_session() as db:
        if settings.AGENT_MODEL:
            r = await db.execute(
                select(LLMModel).options(selectinload(LLMModel.provider)).where(LLMModel.name == settings.AGENT_MODEL)
            )
            m = r.scalar_one_or_none()
            if m:
                return m
            logger.warning(f"AGENT_MODEL '{settings.AGENT_MODEL}' not found, falling back")

        r = await db.execute(
            select(LLMModel)
            .options(selectinload(LLMModel.provider))
            .where(LLMModel.is_active == True)
            .order_by(LLMModel.is_default.desc(), LLMModel.id)
        )
        models = r.scalars().unique().all()
        for m in models:
            if _cap_text(m):
                return m
        logger.error("No active text LLM model available")
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
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return {"analysis": text}


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
