#!/usr/bin/env python3
"""Test script to create and verify BotScenario form data."""
import asyncio
from app.models import BotScenario


async def test_scenario_crud():
    """Test creating, reading, and updating a scenario."""
    print("=" * 60)
    print("Testing BotScenario CRUD operations")
    print("=" * 60)
    
    # 1. Create a test scenario
    print("\n1. Creating test scenario...")
    scenario = await BotScenario.objects.create(
        name="Test Scenario",
        description="This is a test scenario for form testing",
        analysis_types=["sentiment", "keywords", "topics"],
        content_types=["posts", "comments"],
        scope={
            "sentiment_config": {
                "categories": ["positive", "negative", "neutral"],
                "confidence_threshold": 0.8
            },
            "keywords_config": {
                "keywords": ["test", "demo", "example"]
            },
            "custom_var": "test_value"
        },
        ai_prompt="Test prompt with {custom_var}",
        action_type=None,
        is_active=True,
        cooldown_minutes=30
    )
    print(f"✓ Created scenario with ID: {scenario.id}")
    print(f"  - analysis_types: {scenario.analysis_types}")
    print(f"  - content_types: {scenario.content_types}")
    print(f"  - scope keys: {list(scenario.scope.keys())}")
    
    # 2. Read it back
    print(f"\n2. Reading scenario {scenario.id}...")
    loaded = await BotScenario.objects.get(id=scenario.id)
    print(f"✓ Loaded scenario: {loaded.name}")
    print(f"  - analysis_types: {loaded.analysis_types}")
    print(f"  - content_types: {loaded.content_types}")
    print(f"  - scope: {loaded.scope}")
    
    # 3. Verify data integrity
    print("\n3. Verifying data integrity...")
    assert loaded.analysis_types == ["sentiment", "keywords", "topics"], "analysis_types mismatch!"
    assert loaded.content_types == ["posts", "comments"], "content_types mismatch!"
    assert "sentiment_config" in loaded.scope, "sentiment_config not in scope!"
    assert "keywords_config" in loaded.scope, "keywords_config not in scope!"
    assert loaded.scope["custom_var"] == "test_value", "custom_var mismatch!"
    print("✓ All data verified correctly")
    
    # 4. Update the scenario
    print(f"\n4. Updating scenario {scenario.id}...")
    await BotScenario.objects.update_by_id(
        scenario.id,
        analysis_types=["sentiment", "engagement"],
        content_types=["posts", "videos", "comments"],
        scope={
            "sentiment_config": {
                "categories": ["positive", "negative"]
            },
            "engagement_config": {
                "metrics": ["likes", "shares"]
            },
            "updated_var": "new_value"
        }
    )
    
    # 5. Read updated data
    print(f"\n5. Reading updated scenario {scenario.id}...")
    updated = await BotScenario.objects.get(id=scenario.id)
    print(f"✓ Updated scenario: {updated.name}")
    print(f"  - analysis_types: {updated.analysis_types}")
    print(f"  - content_types: {updated.content_types}")
    print(f"  - scope keys: {list(updated.scope.keys())}")
    
    # 6. Verify updates
    print("\n6. Verifying updates...")
    assert updated.analysis_types == ["sentiment", "engagement"], "Updated analysis_types mismatch!"
    assert updated.content_types == ["posts", "videos", "comments"], "Updated content_types mismatch!"
    assert "engagement_config" in updated.scope, "engagement_config not in updated scope!"
    assert updated.scope.get("updated_var") == "new_value", "updated_var not found!"
    print("✓ All updates verified correctly")
    
    # 7. List all scenarios
    print("\n7. Listing all scenarios...")
    all_scenarios = await BotScenario.objects.all()
    print(f"✓ Total scenarios in database: {len(all_scenarios)}")
    for s in all_scenarios:
        print(f"  - ID {s.id}: {s.name}")
        print(f"    analysis_types: {s.analysis_types}")
        print(f"    content_types: {s.content_types}")
    
    print("\n" + "=" * 60)
    print("✓ All tests passed successfully!")
    print("=" * 60)
    print(f"\nTest scenario ID {scenario.id} left in database for manual testing in admin.")
    print(f"Open: http://localhost:8000/admin/bot_scenario/{scenario.id}/edit")
    print("\nExpected behavior in form:")
    print("  ✓ Checkboxes for 'sentiment' and 'engagement' should be checked")
    print("  ✓ Checkboxes for 'posts', 'videos', 'comments' should be checked")
    print("  ✓ JSON editor should show scope with engagement_config and updated_var")
    print("  ✓ Visual editor should show fields for sentiment and engagement configs")
    
    return scenario.id


if __name__ == "__main__":
    scenario_id = asyncio.run(test_scenario_crud())
    print(f"\n✓ Test completed. Scenario ID: {scenario_id}")
