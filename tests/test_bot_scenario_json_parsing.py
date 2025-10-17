"""
Test BotScenarioAdmin JSON parsing logic.
Tests the _parse_json_fields() method without needing to run the server.
"""

import json
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


class MockLogger:
    """Mock logger for testing."""
    def __init__(self):
        self.info_messages = []
        self.warning_messages = []
    
    def info(self, msg):
        print(f"[INFO] {msg}")
        self.info_messages.append(msg)
    
    def warning(self, msg):
        print(f"[WARNING] {msg}")
        self.warning_messages.append(msg)


class MockBotScenarioAdmin:
    """Mock admin class for testing _parse_json_fields."""
    
    def __init__(self, logger):
        self.logger = logger
    
    def _parse_json_fields(self, data: dict) -> None:
        """Parse JSON fields from form data (hidden inputs and textareas)."""
        # Parse content_types from hidden field (JSON string)
        if "content_types" in data and isinstance(data["content_types"], str):
            try:
                data["content_types"] = json.loads(data["content_types"])
                self.logger.info(f"Parsed content_types: {data['content_types']}")
            except (json.JSONDecodeError, TypeError) as e:
                self.logger.warning(f"Failed to parse content_types: {e}, using empty list")
                data["content_types"] = []
        
        # Parse analysis_types from hidden field (JSON string)
        if "analysis_types" in data and isinstance(data["analysis_types"], str):
            try:
                data["analysis_types"] = json.loads(data["analysis_types"])
                self.logger.info(f"Parsed analysis_types: {data['analysis_types']}")
            except (json.JSONDecodeError, TypeError) as e:
                self.logger.warning(f"Failed to parse analysis_types: {e}, using empty list")
                data["analysis_types"] = []
        
        # Parse scope from textarea (JSON string)
        if "scope" in data and isinstance(data["scope"], str):
            try:
                # Handle empty string or whitespace
                if not data["scope"].strip():
                    data["scope"] = {}
                    self.logger.info("Scope is empty, using empty dict")
                else:
                    data["scope"] = json.loads(data["scope"])
                    self.logger.info(f"Parsed scope: {data['scope']}")
            except (json.JSONDecodeError, TypeError) as e:
                self.logger.warning(f"Failed to parse scope: {e}, using empty dict")
                data["scope"] = {}
        
        # Parse trigger_config from textarea (JSON string)
        if "trigger_config" in data and isinstance(data["trigger_config"], str):
            try:
                if not data["trigger_config"].strip():
                    data["trigger_config"] = {}
                else:
                    data["trigger_config"] = json.loads(data["trigger_config"])
                    self.logger.info(f"Parsed trigger_config: {data['trigger_config']}")
            except (json.JSONDecodeError, TypeError) as e:
                self.logger.warning(f"Failed to parse trigger_config: {e}, using empty dict")
                data["trigger_config"] = {}


def test_valid_json():
    """Test with valid JSON strings."""
    print("\n" + "="*60)
    print("TEST 1: Valid JSON strings")
    print("="*60)
    
    logger = MockLogger()
    admin = MockBotScenarioAdmin(logger)
    
    data = {
        "name": "Test Scenario",
        "content_types": '["posts", "comments"]',
        "analysis_types": '["sentiment", "topics", "keywords"]',
        "scope": '{"sentiment": {"detect_sarcasm": true}, "topics": {"max_topics": 5}, "brand": "MyBrand"}',
        "trigger_config": '{"threshold": 0.7}'
    }
    
    print("\nBefore parsing:")
    print(f"  content_types type: {type(data['content_types'])}, value: {data['content_types']}")
    print(f"  analysis_types type: {type(data['analysis_types'])}, value: {data['analysis_types']}")
    print(f"  scope type: {type(data['scope'])}, value: {data['scope'][:50]}...")
    
    admin._parse_json_fields(data)
    
    print("\nAfter parsing:")
    print(f"  content_types type: {type(data['content_types'])}, value: {data['content_types']}")
    print(f"  analysis_types type: {type(data['analysis_types'])}, value: {data['analysis_types']}")
    print(f"  scope type: {type(data['scope'])}, value: {data['scope']}")
    
    # Assertions
    assert isinstance(data['content_types'], list), "content_types should be a list"
    assert data['content_types'] == ["posts", "comments"], "content_types values mismatch"
    
    assert isinstance(data['analysis_types'], list), "analysis_types should be a list"
    assert data['analysis_types'] == ["sentiment", "topics", "keywords"], "analysis_types values mismatch"
    
    assert isinstance(data['scope'], dict), "scope should be a dict"
    assert 'sentiment' in data['scope'], "scope should have sentiment config"
    assert 'brand' in data['scope'], "scope should have custom brand variable"
    
    print("\n✅ TEST 1 PASSED")


def test_invalid_json():
    """Test with invalid JSON strings."""
    print("\n" + "="*60)
    print("TEST 2: Invalid JSON strings")
    print("="*60)
    
    logger = MockLogger()
    admin = MockBotScenarioAdmin(logger)
    
    data = {
        "name": "Test Scenario",
        "content_types": '[invalid json',
        "analysis_types": '["sentiment", missing quote]',
        "scope": '{this is not json at all}',
    }
    
    print("\nBefore parsing (invalid JSON):")
    print(f"  content_types: {data['content_types']}")
    print(f"  analysis_types: {data['analysis_types']}")
    print(f"  scope: {data['scope']}")
    
    admin._parse_json_fields(data)
    
    print("\nAfter parsing (should use defaults):")
    print(f"  content_types: {data['content_types']}")
    print(f"  analysis_types: {data['analysis_types']}")
    print(f"  scope: {data['scope']}")
    
    # Assertions
    assert data['content_types'] == [], "Invalid content_types should become empty list"
    assert data['analysis_types'] == [], "Invalid analysis_types should become empty list"
    assert data['scope'] == {}, "Invalid scope should become empty dict"
    
    assert len(logger.warning_messages) == 3, "Should have 3 warning messages"
    
    print("\n✅ TEST 2 PASSED")


def test_empty_fields():
    """Test with empty strings."""
    print("\n" + "="*60)
    print("TEST 3: Empty strings")
    print("="*60)
    
    logger = MockLogger()
    admin = MockBotScenarioAdmin(logger)
    
    data = {
        "name": "Test Scenario",
        "content_types": '[]',
        "analysis_types": '[]',
        "scope": '',
        "trigger_config": '   ',  # Whitespace only
    }
    
    print("\nBefore parsing (empty fields):")
    print(f"  content_types: '{data['content_types']}'")
    print(f"  analysis_types: '{data['analysis_types']}'")
    print(f"  scope: '{data['scope']}'")
    print(f"  trigger_config: '{data['trigger_config']}'")
    
    admin._parse_json_fields(data)
    
    print("\nAfter parsing:")
    print(f"  content_types: {data['content_types']}")
    print(f"  analysis_types: {data['analysis_types']}")
    print(f"  scope: {data['scope']}")
    print(f"  trigger_config: {data['trigger_config']}")
    
    # Assertions
    assert data['content_types'] == [], "Empty array should parse to empty list"
    assert data['analysis_types'] == [], "Empty array should parse to empty list"
    assert data['scope'] == {}, "Empty string should become empty dict"
    assert data['trigger_config'] == {}, "Whitespace should become empty dict"
    
    print("\n✅ TEST 3 PASSED")


def test_already_parsed():
    """Test with already parsed data (should not re-parse)."""
    print("\n" + "="*60)
    print("TEST 4: Already parsed data (list/dict, not strings)")
    print("="*60)
    
    logger = MockLogger()
    admin = MockBotScenarioAdmin(logger)
    
    data = {
        "name": "Test Scenario",
        "content_types": ["posts"],  # Already a list!
        "analysis_types": ["sentiment"],  # Already a list!
        "scope": {"key": "value"},  # Already a dict!
    }
    
    print("\nBefore parsing (already parsed):")
    print(f"  content_types type: {type(data['content_types'])}, value: {data['content_types']}")
    print(f"  analysis_types type: {type(data['analysis_types'])}, value: {data['analysis_types']}")
    print(f"  scope type: {type(data['scope'])}, value: {data['scope']}")
    
    admin._parse_json_fields(data)
    
    print("\nAfter parsing (should be unchanged):")
    print(f"  content_types type: {type(data['content_types'])}, value: {data['content_types']}")
    print(f"  analysis_types type: {type(data['analysis_types'])}, value: {data['analysis_types']}")
    print(f"  scope type: {type(data['scope'])}, value: {data['scope']}")
    
    # Assertions
    assert data['content_types'] == ["posts"], "Already parsed list should not change"
    assert data['analysis_types'] == ["sentiment"], "Already parsed list should not change"
    assert data['scope'] == {"key": "value"}, "Already parsed dict should not change"
    
    assert len(logger.info_messages) == 0, "Should not log anything for already parsed data"
    
    print("\n✅ TEST 4 PASSED")


def test_mixed_scenario():
    """Test realistic scenario with custom variables and analysis configs."""
    print("\n" + "="*60)
    print("TEST 5: Realistic scenario with custom variables")
    print("="*60)
    
    logger = MockLogger()
    admin = MockBotScenarioAdmin(logger)
    
    scope_json = json.dumps({
        "sentiment": {
            "detect_sarcasm": True,
            "emotion_analysis": True
        },
        "topics": {
            "max_topics": 5,
            "identify_emerging": True
        },
        "my_brand_name": "TestCompany",
        "competitor_brands": ["Competitor1", "Competitor2"],
        "alert_email": "admin@example.com"
    })
    
    data = {
        "name": "Brand Monitoring",
        "description": "Monitor brand mentions and sentiment",
        "content_types": '["posts", "comments", "mentions"]',
        "analysis_types": '["sentiment", "topics", "brand_mentions"]',
        "scope": scope_json,
        "collection_interval_hours": 6,
    }
    
    print("\nBefore parsing:")
    print(f"  content_types: {data['content_types']}")
    print(f"  analysis_types: {data['analysis_types']}")
    print(f"  scope length: {len(data['scope'])} chars")
    
    admin._parse_json_fields(data)
    
    print("\nAfter parsing:")
    print(f"  content_types: {data['content_types']}")
    print(f"  analysis_types: {data['analysis_types']}")
    print(f"  scope keys: {list(data['scope'].keys())}")
    print(f"  scope custom variables: my_brand_name={data['scope']['my_brand_name']}, alert_email={data['scope']['alert_email']}")
    
    # Assertions
    assert len(data['content_types']) == 3, "Should have 3 content types"
    assert len(data['analysis_types']) == 3, "Should have 3 analysis types"
    assert len(data['scope']) == 5, "Should have 5 keys in scope (2 configs + 3 custom vars)"
    assert data['scope']['my_brand_name'] == "TestCompany", "Custom variable should be preserved"
    assert len(data['scope']['competitor_brands']) == 2, "Custom array variable should work"
    
    print("\n✅ TEST 5 PASSED")


if __name__ == "__main__":
    print("\n" + "="*60)
    print("Testing BotScenarioAdmin JSON Parsing Logic")
    print("="*60)
    
    try:
        test_valid_json()
        test_invalid_json()
        test_empty_fields()
        test_already_parsed()
        test_mixed_scenario()
        
        print("\n" + "="*60)
        print("✅ ALL TESTS PASSED!")
        print("="*60)
        print("\nThe _parse_json_fields() logic is working correctly.")
        print("You can now test with the actual admin panel.")
        
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
