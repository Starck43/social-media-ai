"""
Intelligent LLM Provider Resolution System.

Automatically selects appropriate LLM providers based on:
- Content types in the scenario
- Required media capabilities
- Available providers
- Cost optimization
"""
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from app.types.enums import ContentType, MediaType
from app.types.llm_models import LLMProviderMetadata


@dataclass
class LLMMapping:
    """Configuration for LLM provider and model."""
    provider_id: int
    provider_type: str  # "openai", "deepseek", etc.
    model_id: str       # "gpt-4-vision-preview", "deepseek-chat", etc.
    capabilities: list[str]  # ["text", "image", "video"]
    
    def to_dict(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "provider_type": self.provider_type,
            "model_id": self.model_id,
            "capabilities": self.capabilities,
        }


class LLMProviderResolver:
    """
    Smart resolution of LLM providers for content analysis.
    
    Examples:
        # Single multimodal provider for everything
        resolver = LLMProviderResolver()
        mapping = resolver.resolve_for_content_types(
            content_types=["posts", "videos", "stories"],
            available_providers={
                1: ("openai", "gpt-4-vision-preview", ["text", "image", "video"])
            }
        )
        # Result: Uses GPT-4V for all types
        
        # Mix of providers
        mapping = resolver.resolve_for_content_types(
            content_types=["posts", "videos"],
            available_providers={
                1: ("deepseek", "deepseek-chat", ["text"]),
                2: ("openai", "gpt-4-vision-preview", ["text", "image", "video"])
            }
        )
        # Result: DeepSeek for text, GPT-4V for video
    """
    
    @staticmethod
    def get_required_capabilities(content_types: list[str]) -> dict[str, list[str]]:
        """
        Determine required MediaType capabilities from ContentTypes.
        
        Args:
            content_types: List of ContentType values ["posts", "videos", "stories"]
            
        Returns:
            Dict mapping MediaType to ContentTypes that need it
            {
                "text": ["posts", "comments"],
                "image": ["stories"],
                "video": ["videos", "reels"]
            }
        """
        requirements = {
            "text": [],
            "image": [],
            "video": [],
        }
        
        # Mapping ContentType -> required MediaType
        content_to_media = {
            "posts": "text",
            "comments": "text",
            "mentions": "text",
            "reactions": "text",
            "videos": "video",
            "reels": "video",
            "stories": "image",  # Stories can be images or short videos
        }
        
        for content_type in content_types:
            media_type = content_to_media.get(content_type)
            if media_type and content_type not in requirements[media_type]:
                requirements[media_type].append(content_type)
        
        # Remove empty categories
        return {k: v for k, v in requirements.items() if v}
    
    @staticmethod
    def find_optimal_provider(
        required_capabilities: list[str],
        available_providers: dict[int, tuple[str, str, list[str]]],
        prefer_multimodal: bool = True
    ) -> Optional[tuple[int, str, str, list[str]]]:
        """
        Find the best provider that supports all required capabilities.
        
        Args:
            required_capabilities: ["text", "image", "video"]
            available_providers: {
                1: ("deepseek", "deepseek-chat", ["text"]),
                2: ("openai", "gpt-4-vision-preview", ["text", "image", "video"])
            }
            prefer_multimodal: If True, prefer providers that support all types
            
        Returns:
            (provider_id, provider_type, model_id, capabilities) or None
        """
        if not required_capabilities or not available_providers:
            return None
        
        # First pass: Find providers that support ALL required capabilities
        candidates = []
        for provider_id, (provider_type, model_id, capabilities) in available_providers.items():
            if all(cap in capabilities for cap in required_capabilities):
                candidates.append((
                    provider_id,
                    provider_type,
                    model_id,
                    capabilities,
                    len(capabilities)  # Number of capabilities (for sorting)
                ))
        
        if not candidates:
            return None
        
        # Sort by preference with stable tiebreaker
        # When capabilities count is equal, prefer lower ID (first created = higher priority)
        if prefer_multimodal:
            # Prefer providers with MORE capabilities (multimodal)
            # Tiebreaker: lower ID (first created)
            candidates.sort(key=lambda x: (-x[4], x[0]))
        else:
            # Prefer providers with FEWER capabilities (specialized)
            # Tiebreaker: lower ID (first created)
            candidates.sort(key=lambda x: (x[4], x[0]))
        
        # Return best match (without capability count)
        best = candidates[0]
        return (best[0], best[1], best[2], best[3])
    
    @classmethod
    def resolve_for_content_types(
        cls,
        content_types: list[str],
        available_providers: dict[int, tuple[str, str, list[str]]],
        strategy: str = "cost_efficient"
    ) -> dict[str, LLMMapping]:
        """
        Resolve LLM providers for given content types.
        
        Args:
            content_types: ContentType values ["posts", "videos", "stories"]
            available_providers: Available LLM providers
                {
                    1: ("deepseek", "deepseek-chat", ["text"]),
                    2: ("openai", "gpt-4-vision-preview", ["text", "image", "video"])
                }
            strategy: Resolution strategy
                - "cost_efficient": Use specialized providers (DeepSeek for text, GPT-4V for media)
                - "quality": Use best model for everything
                - "multimodal": Use single multimodal provider for all types
        
        Returns:
            Mapping of MediaType to LLMMapping
            {
                "text": LLMMapping(provider_id=1, model="deepseek-chat"),
                "image": LLMMapping(provider_id=2, model="gpt-4-vision-preview"),
                "video": LLMMapping(provider_id=2, model="gpt-4-vision-preview")
            }
        """
        # Get required capabilities
        requirements = cls.get_required_capabilities(content_types)
        
        if not requirements:
            return {}
        
        result = {}
        
        if strategy == "multimodal":
            # Try to use ONE provider for ALL types
            all_caps = list(requirements.keys())
            provider = cls.find_optimal_provider(
                all_caps,
                available_providers,
                prefer_multimodal=True
            )
            
            if provider:
                provider_id, provider_type, model_id, capabilities = provider
                for media_type in all_caps:
                    result[media_type] = LLMMapping(
                        provider_id=provider_id,
                        provider_type=provider_type,
                        model_id=model_id,
                        capabilities=capabilities
                    )
            
        elif strategy == "cost_efficient":
            # Use specialized providers for each type
            for media_type in requirements.keys():
                provider = cls.find_optimal_provider(
                    [media_type],
                    available_providers,
                    prefer_multimodal=False  # Prefer specialized
                )
                
                if provider:
                    provider_id, provider_type, model_id, capabilities = provider
                    result[media_type] = LLMMapping(
                        provider_id=provider_id,
                        provider_type=provider_type,
                        model_id=model_id,
                        capabilities=capabilities
                    )
        
        elif strategy == "quality":
            # Use best model for everything (usually multimodal high-quality)
            all_caps = list(requirements.keys())
            provider = cls.find_optimal_provider(
                all_caps,
                available_providers,
                prefer_multimodal=True
            )
            
            if provider:
                provider_id, provider_type, model_id, capabilities = provider
                for media_type in all_caps:
                    result[media_type] = LLMMapping(
                        provider_id=provider_id,
                        provider_type=provider_type,
                        model_id=model_id,
                        capabilities=capabilities
                    )
        
        # Fallback: Try to fill missing types with any available provider
        for media_type in requirements.keys():
            if media_type not in result:
                provider = cls.find_optimal_provider(
                    [media_type],
                    available_providers,
                    prefer_multimodal=False
                )
                
                if provider:
                    provider_id, provider_type, model_id, capabilities = provider
                    result[media_type] = LLMMapping(
                        provider_id=provider_id,
                        provider_type=provider_type,
                        model_id=model_id,
                        capabilities=capabilities
                    )
        
        return result
    
    @classmethod
    def resolve_from_bot_scenario(cls, bot_scenario) -> dict[str, LLMMapping]:
        """
        Resolve LLM providers from BotScenario configuration.
        
        Uses FK fields (text_llm_provider_id, etc.) for provider resolution.
        
        Args:
            bot_scenario: BotScenario instance
            
        Returns:
            Mapping of MediaType to LLMMapping
        """
        # Use FK fields to build mapping
        result = {}
        
        if hasattr(bot_scenario, 'text_llm_provider') and bot_scenario.text_llm_provider:
            provider = bot_scenario.text_llm_provider
            from app.utils.enum_helpers import get_enum_value
            result['text'] = LLMMapping(
                provider_id=provider.id,
                provider_type=get_enum_value(provider.provider_type),
                model_id=provider.model_name,
                capabilities=provider.capabilities or ['text']
            )
        
        if hasattr(bot_scenario, 'image_llm_provider') and bot_scenario.image_llm_provider:
            provider = bot_scenario.image_llm_provider
            result['image'] = LLMMapping(
                provider_id=provider.id,
                provider_type=get_enum_value(provider.provider_type),
                model_id=provider.model_name,
                capabilities=provider.capabilities or ['image']
            )
        
        if hasattr(bot_scenario, 'video_llm_provider') and bot_scenario.video_llm_provider:
            provider = bot_scenario.video_llm_provider
            result['video'] = LLMMapping(
                provider_id=provider.id,
                provider_type=get_enum_value(provider.provider_type),
                model_id=provider.model_name,
                capabilities=provider.capabilities or ['video']
            )
        
        return result
    
    @classmethod
    def auto_resolve(
        cls,
        content_types: list[str],
        strategy: str = "cost_efficient"
    ) -> dict[str, LLMMapping]:
        """
        Automatically resolve LLM providers from database.
        
        Queries available active providers and selects optimal ones.
        
        Args:
            content_types: ContentType values
            strategy: Resolution strategy
        
        Returns:
            Mapping of MediaType to LLMMapping
        """
        from app.models import LLMProvider
        
        # Get all active providers
        # In async context, this should be: await LLMProvider.objects.filter(is_active=True)
        # For now, return empty dict (caller should use resolve_for_content_types with explicit providers)
        return {}


def print_resolution_report(
    content_types: list[str],
    mapping: dict[str, LLMMapping],
    requirements: dict[str, list[str]]
):
    """Pretty print resolution report."""
    print("="*80)
    print("LLM PROVIDER RESOLUTION REPORT")
    print("="*80)
    
    print(f"\nContent Types: {', '.join(content_types)}")
    
    print("\nRequired Capabilities:")
    for media_type, content_list in requirements.items():
        print(f"  {media_type.upper()}: {', '.join(content_list)}")
    
    print("\nResolved LLM Mapping:")
    for media_type, llm_mapping in mapping.items():
        print(f"  {media_type.upper()}:")
        print(f"    Provider ID: {llm_mapping.provider_id}")
        print(f"    Provider: {llm_mapping.provider_type}")
        print(f"    Model: {llm_mapping.model_id}")
        print(f"    Capabilities: {', '.join(llm_mapping.capabilities)}")
    
    print("\n" + "="*80)
