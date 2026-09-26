"""
LLM optimization utilities for cost reduction and rate limiting.
"""
import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from app.models import LLMProvider

logger = logging.getLogger(__name__)


def _provider_key(provider: "LLMProvider") -> str:
    return provider.name.lower()


class LLMOptimizer:
    def __init__(self):
        self.rate_limiter = LLMRateLimiter()
        self.cost_tracker = CostTracker()
        self.batch_optimizer = BatchOptimizer()

    async def optimize_request(self, items: list[dict], provider: "LLMProvider") -> tuple[list[dict], float]:
        await self.rate_limiter.acquire(_provider_key(provider))
        if len(items) > 1 and self._supports_batch(items, provider):
            results = await self.batch_optimizer.batch_analyze(items, provider)
        else:
            results = []
            for item in items:
                result = await self._analyze_single(item, provider)
                results.append(result)
        total_input = sum(r.get("input_tokens", 0) for r in results)
        total_output = sum(r.get("output_tokens", 0) for r in results)
        cost = await self.cost_tracker.track_usage(_provider_key(provider), input_tokens=total_input,
                                                   output_tokens=total_output)
        return results, cost

    def _supports_batch(self, items: list[dict], provider: "LLMProvider") -> bool:
        if any(item.get("type") != "text" for item in items):
            return False
        from app.models import LLMModel

        models = [m for m in provider.models if m.is_active and m.can_handle("text")]
        return bool(models)

    async def _analyze_single(self, item: dict, provider: "LLMProvider") -> dict:
        return {"content": item, "input_tokens": 0, "output_tokens": 0, "result": None}


class LLMRateLimiter:
    DEFAULT_LIMITS = {"openai": 60, "anthropic": 50, "deepseek": 60, "google": 60}

    def __init__(self, custom_limits: Optional[dict[str, int]] = None):
        self.limits = {**self.DEFAULT_LIMITS, **(custom_limits or {})}
        self.requests: dict[str, list[datetime]] = defaultdict(list)

    async def acquire(self, provider: str):
        now = datetime.now()
        self.requests[provider] = [r for r in self.requests[provider] if now - r < timedelta(minutes=1)]
        max_rpm = self.limits.get(provider, 60)
        if len(self.requests[provider]) >= max_rpm:
            oldest = self.requests[provider][0]
            wait_time = 60 - (now - oldest).total_seconds()
            if wait_time > 0:
                logger.warning(f"Rate limit for {provider} ({max_rpm} RPM), waiting {wait_time:.1f}s")
                await asyncio.sleep(wait_time)
        self.requests[provider].append(datetime.now())


class CostTracker:
    def __init__(self):
        self.session_costs: dict[str, float] = defaultdict(float)
        self.session_tokens: dict[str, dict[str, int]] = defaultdict(lambda: {"input": 0, "output": 0})

    async def track_usage(self, provider: str, input_tokens: int, output_tokens: int) -> float:
        from app.models import LLMModel

        models = await LLMModel.objects.select_related("provider").filter(is_active=True).all()
        prov_models = [m for m in models if m.provider.name.lower() == provider]
        if not prov_models:
            return 0.0
        m = prov_models[0]
        cost = (input_tokens / 1_000 * m.input_cost_per_1k + output_tokens / 1_000 * m.output_cost_per_1k)
        self.session_costs[provider] += cost
        self.session_tokens[provider]["input"] += input_tokens
        self.session_tokens[provider]["output"] += output_tokens
        logger.info(f"LLM usage: {provider} | tokens: {input_tokens}↑ {output_tokens}↓ | cost: ${cost:.4f}")
        return cost

    def get_session_summary(self) -> dict[str, Any]:
        return {"total_cost": sum(self.session_costs.values()), "by_provider": {
            p: {"cost": self.session_costs[p], "tokens": self.session_tokens[p]} for p in self.session_costs}}


class BatchOptimizer:
    MAX_BATCH_SIZE = 20
    MAX_BATCH_TOKENS = 3000

    async def batch_analyze(self, items: list[dict], provider: "LLMProvider") -> list[dict]:
        text_items = [i for i in items if i.get("type") == "text"]
        if not text_items:
            return []
        batches = self._create_batches(text_items)
        all_results = []
        for batch in batches:
            prompt = self._create_batch_prompt(batch)
            all_results.append({"batch_size": len(batch), "prompt": prompt,
                                "input_tokens": int(len(prompt.split()) * 1.3),
                                "output_tokens": len(batch) * 100})
        return all_results

    def _create_batches(self, items: list[dict]) -> list[list[dict]]:
        batches, current_batch, current_tokens = [], [], 0
        for item in items:
            item_tokens = len(str(item.get("text", "")).split()) * 1.3
            if len(current_batch) >= self.MAX_BATCH_SIZE or current_tokens + item_tokens > self.MAX_BATCH_TOKENS:
                if current_batch:
                    batches.append(current_batch)
                    current_batch, current_tokens = [], 0
            current_batch.append(item)
            current_tokens += item_tokens
        if current_batch:
            batches.append(current_batch)
        return batches

    def _create_batch_prompt(self, items: list[dict]) -> str:
        posts = "\n\n".join(f"POST {i + 1}:\n{item.get('text', '')}" for i, item in enumerate(items))
        return f"""
Analyze the following {len(items)} social media posts.
For each post provide: sentiment (positive/negative/neutral), main topics (up to 3), key entities.
{posts}
Respond in JSON format:
[{{"post_id":1,"sentiment":"...","topics":[...],"entities":[...]}}]
"""