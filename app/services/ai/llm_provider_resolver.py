"""
LLM Provider Resolution — maps content types to optimal provider/model pairs.
"""
from typing import Optional

from app.models import LLMModel


class LLMProviderResolver:
    """Smart resolution of LLM providers based on required capabilities."""

    @staticmethod
    def get_required_capabilities(content_types: list[str]) -> dict[str, list[str]]:
        requirements: dict[str, list[str]] = {"text": [], "image": [], "video": []}
        ct_map = {"posts": "text", "comments": "text", "mentions": "text", "reactions": "text",
                  "videos": "video", "reels": "video", "stories": "image"}
        for ct in content_types:
            media = ct_map.get(ct)
            if media and ct not in requirements[media]:
                requirements[media].append(ct)
        return {k: v for k, v in requirements.items() if v}

    @staticmethod
    def find_optimal_provider(required_caps: list[str], available: dict[int, tuple[str, str, list[str]]],
                              prefer_multimodal: bool = True) -> Optional[tuple[int, str, str, list[str]]]:
        candidates = []
        for pid, (ptype, model_id, caps) in available.items():
            if all(c in caps for c in required_caps):
                candidates.append((pid, ptype, model_id, caps, len(caps)))
        if not candidates:
            return None
        candidates.sort(key=lambda x: (-x[4], x[0]) if prefer_multimodal else (x[4], x[0]))
        best = candidates[0]
        return best[0], best[1], best[2], best[3]

    @classmethod
    def resolve_for_content_types(cls, content_types: list[str],
                                  available_providers: dict[int, tuple[str, str, list[str]]],
                                  strategy: str = "cost_efficient") -> dict[str, dict]:
        requirements = cls.get_required_capabilities(content_types)
        if not requirements:
            return {}
        result: dict[str, dict] = {}
        all_caps = list(requirements.keys())

        if strategy in ("multimodal", "quality"):
            provider = cls.find_optimal_provider(all_caps, available_providers, prefer_multimodal=True)
            if provider:
                pid, pname, mid, caps = provider
                for mt in all_caps:
                    result[mt] = {"provider_id": pid, "provider_name": pname, "model_id": mid, "capabilities": caps}
        elif strategy == "cost_efficient":
            for mt in requirements:
                p = cls.find_optimal_provider([mt], available_providers, prefer_multimodal=False)
                if p:
                    pid, pname, mid, caps = p
                    result[mt] = {"provider_id": pid, "provider_name": pname, "model_id": mid, "capabilities": caps}

        for mt in requirements:
            if mt not in result:
                p = cls.find_optimal_provider([mt], available_providers, prefer_multimodal=False)
                if p:
                    pid, pname, mid, caps = p
                    result[mt] = {"provider_id": pid, "provider_name": pname, "model_id": mid, "capabilities": caps}
        return result

    @classmethod
    def resolve_from_agent_scenario(cls, agent_scenario) -> dict[str, dict]:
        result = {}
        for slot, attr in [("text", "text_llm_model"), ("image", "image_llm_model"), ("video", "video_llm_model")]:
            model = getattr(agent_scenario, attr, None)
            if model:
                result[slot] = {
                    "provider_id": model.provider_id, "provider_name": model.provider.name,
                    "model_id": model.model_id,
                    "capabilities": model.capabilities,
                }
        return result

    @classmethod
    def auto_resolve(cls, content_types: list[str], strategy: str = "cost_efficient") -> dict[str, dict]:
        return {}  # DB-backed resolution done by caller