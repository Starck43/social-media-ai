#!/usr/bin/env python3
"""
Demo: Intelligent LLM Provider Resolution

Shows how the system automatically selects appropriate LLM providers
based on content types and available models.
"""

from app.services.ai.llm_provider_resolver import (
    LLMProviderResolver,
    print_resolution_report
)


def example_1_single_multimodal_provider():
    """
    Scenario: Only GPT-4 Vision available (multimodal)
    Result: Uses it for everything
    """
    print("\n" + "="*80)
    print("EXAMPLE 1: Single Multimodal Provider")
    print("="*80)
    
    content_types = ["posts", "videos", "stories"]
    
    available_providers = {
        1: ("openai", "gpt-4-vision-preview", ["text", "image", "video"])
    }
    
    resolver = LLMProviderResolver()
    requirements = resolver.get_required_capabilities(content_types)
    mapping = resolver.resolve_for_content_types(
        content_types,
        available_providers,
        strategy="multimodal"
    )
    
    print_resolution_report(content_types, mapping, requirements)
    
    print("\n💡 Result: GPT-4 Vision handles ALL types (text, image, video)")
    print("   ✅ Pros: Consistent quality, single API key")
    print("   ❌ Cons: More expensive for simple text")


def example_2_cost_efficient_mix():
    """
    Scenario: DeepSeek for text + GPT-4V for media
    Result: Uses specialized providers (cost optimization)
    """
    print("\n" + "="*80)
    print("EXAMPLE 2: Cost-Efficient Mix")
    print("="*80)
    
    content_types = ["posts", "comments", "videos", "stories"]
    
    available_providers = {
        1: ("deepseek", "deepseek-chat", ["text"]),
        2: ("openai", "gpt-4-vision-preview", ["text", "image", "video"])
    }
    
    resolver = LLMProviderResolver()
    requirements = resolver.get_required_capabilities(content_types)
    mapping = resolver.resolve_for_content_types(
        content_types,
        available_providers,
        strategy="cost_efficient"
    )
    
    print_resolution_report(content_types, mapping, requirements)
    
    print("\n💡 Result: Specialized providers for each type")
    print("   Text: DeepSeek ($0.0001/1k tokens)")
    print("   Image/Video: GPT-4 Vision ($0.01/1k tokens)")
    print("   ✅ Pros: Cost optimized (~100x cheaper for text)")
    print("   ✅ Cons: Need multiple API keys")


def example_3_quality_strategy():
    """
    Scenario: Best quality for everything
    Result: Uses highest quality model available
    """
    print("\n" + "="*80)
    print("EXAMPLE 3: Quality Strategy")
    print("="*80)
    
    content_types = ["posts", "videos"]
    
    available_providers = {
        1: ("deepseek", "deepseek-chat", ["text"]),
        2: ("openai", "gpt-3.5-turbo", ["text"]),
        3: ("openai", "gpt-4-vision-preview", ["text", "image", "video"])
    }
    
    resolver = LLMProviderResolver()
    requirements = resolver.get_required_capabilities(content_types)
    mapping = resolver.resolve_for_content_types(
        content_types,
        available_providers,
        strategy="quality"
    )
    
    print_resolution_report(content_types, mapping, requirements)
    
    print("\n💡 Result: Best available model for all types")
    print("   ✅ Pros: Highest quality analysis")
    print("   ❌ Cons: More expensive")


def example_4_only_text_content():
    """
    Scenario: Only text content (posts, comments)
    Result: Uses cheapest text-only model
    """
    print("\n" + "="*80)
    print("EXAMPLE 4: Text-Only Content")
    print("="*80)
    
    content_types = ["posts", "comments", "mentions"]
    
    available_providers = {
        1: ("deepseek", "deepseek-chat", ["text"]),
        2: ("openai", "gpt-3.5-turbo", ["text"]),
        3: ("openai", "gpt-4-vision-preview", ["text", "image", "video"])
    }
    
    resolver = LLMProviderResolver()
    requirements = resolver.get_required_capabilities(content_types)
    mapping = resolver.resolve_for_content_types(
        content_types,
        available_providers,
        strategy="cost_efficient"
    )
    
    print_resolution_report(content_types, mapping, requirements)
    
    print("\n💡 Result: Cheapest text provider (DeepSeek)")
    print("   No need for expensive multimodal models")


def example_5_missing_capability():
    """
    Scenario: Need video analysis but only text provider available
    Result: Shows fallback behavior
    """
    print("\n" + "="*80)
    print("EXAMPLE 5: Missing Capability (Fallback)")
    print("="*80)
    
    content_types = ["posts", "videos"]
    
    available_providers = {
        1: ("deepseek", "deepseek-chat", ["text"]),
        # No video-capable provider!
    }
    
    resolver = LLMProviderResolver()
    requirements = resolver.get_required_capabilities(content_types)
    mapping = resolver.resolve_for_content_types(
        content_types,
        available_providers,
        strategy="cost_efficient"
    )
    
    print_resolution_report(content_types, mapping, requirements)
    
    if "video" not in mapping:
        print("\n⚠️  WARNING: No provider found for video analysis!")
        print("   Solution: Add GPT-4 Vision or Gemini Pro Vision")


def example_6_multiple_strategies_comparison():
    """
    Scenario: Compare different strategies for same content
    """
    print("\n" + "="*80)
    print("EXAMPLE 6: Strategy Comparison")
    print("="*80)
    
    content_types = ["posts", "videos", "stories"]
    
    available_providers = {
        1: ("deepseek", "deepseek-chat", ["text"]),
        2: ("google", "gemini-pro-vision", ["text", "image"]),
        3: ("openai", "gpt-4-vision-preview", ["text", "image", "video"])
    }
    
    resolver = LLMProviderResolver()
    
    strategies = ["cost_efficient", "multimodal", "quality"]
    
    for strategy in strategies:
        print(f"\n--- Strategy: {strategy.upper()} ---")
        mapping = resolver.resolve_for_content_types(
            content_types,
            available_providers,
            strategy=strategy
        )
        
        for media_type, llm in mapping.items():
            print(f"  {media_type}: {llm.provider_type}/{llm.model_id}")


def example_7_real_world_scenario():
    """
    Real-world: Instagram brand monitoring
    Content: Posts + Stories + Reels
    Goal: Analyze brand presence in visual content
    """
    print("\n" + "="*80)
    print("EXAMPLE 7: Real-World Instagram Brand Monitoring")
    print("="*80)
    
    content_types = ["posts", "stories", "reels"]  # Instagram content
    
    available_providers = {
        1: ("deepseek", "deepseek-chat", ["text"]),
        2: ("openai", "gpt-4-vision-preview", ["text", "image", "video"])
    }
    
    resolver = LLMProviderResolver()
    requirements = resolver.get_required_capabilities(content_types)
    mapping = resolver.resolve_for_content_types(
        content_types,
        available_providers,
        strategy="cost_efficient"
    )
    
    print_resolution_report(content_types, mapping, requirements)
    
    # Calculate estimated costs
    print("\n💰 Estimated Cost (per 10,000 posts):")
    print("   Assuming: 500 tokens/post, 50% text, 30% images, 20% videos")
    
    text_cost = (5000 * 500 / 1000) * 0.0001  # DeepSeek
    image_cost = (3000 * 500 / 1000) * 0.01   # GPT-4V
    video_cost = (2000 * 500 / 1000) * 0.01   # GPT-4V
    
    total_cost = text_cost + image_cost + video_cost
    
    print(f"   Text (DeepSeek): ${text_cost:.2f}")
    print(f"   Images (GPT-4V): ${image_cost:.2f}")
    print(f"   Videos (GPT-4V): ${video_cost:.2f}")
    print(f"   TOTAL: ${total_cost:.2f}")
    
    # Compare with all-GPT-4V
    all_gpt4v = (10000 * 500 / 1000) * 0.01
    print(f"\n   vs All GPT-4V: ${all_gpt4v:.2f}")
    print(f"   💰 Savings: ${all_gpt4v - total_cost:.2f} ({(1 - total_cost/all_gpt4v)*100:.1f}%)")


if __name__ == "__main__":
    print("\n" + "🤖 LLM PROVIDER RESOLVER DEMO 🤖".center(80))
    
    example_1_single_multimodal_provider()
    example_2_cost_efficient_mix()
    example_3_quality_strategy()
    example_4_only_text_content()
    example_5_missing_capability()
    example_6_multiple_strategies_comparison()
    example_7_real_world_scenario()
    
    print("\n" + "="*80)
    print("✅ All examples completed!")
    print("="*80)
    print("\nKey Takeaways:")
    print("  1. System automatically selects optimal LLM providers")
    print("  2. Supports multiple strategies (cost/quality/multimodal)")
    print("  3. Falls back gracefully when capabilities missing")
    print("  4. Cost savings up to 90% vs single expensive model")
    print("="*80)
