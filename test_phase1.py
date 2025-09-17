#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 The Linux Foundation

"""
Test script for Phase 1 functionality of the Repository Reporting System.

This script validates:
- Configuration loading and deep merge
- Time window computation
- Config digest generation
- Basic logging setup
- Schema validation
"""

import sys
import tempfile
import json
import datetime
from pathlib import Path

# Add the project root to Python path to import our module
sys.path.insert(0, str(Path(__file__).parent))

try:
    from generate_reports import (
        load_configuration,
        deep_merge_dicts,
        compute_time_windows,
        compute_config_digest,
        setup_logging,
        SCHEMA_VERSION,
        SCRIPT_VERSION,
        DEFAULT_TIME_WINDOWS
    )
except ImportError as e:
    print(f"ERROR: Failed to import from generate_reports.py: {e}")
    print("Make sure generate_reports.py is in the same directory as this test script.")
    sys.exit(1)

def test_deep_merge():
    """Test the deep merge functionality."""
    print("Testing deep merge functionality...")

    base = {
        "level1": {
            "level2a": {
                "value1": "base_value1",
                "value2": "base_value2"
            },
            "level2b": ["base_item1", "base_item2"]
        },
        "simple_key": "base_simple"
    }

    override = {
        "level1": {
            "level2a": {
                "value1": "override_value1",
                "value3": "override_value3"
            },
            "level2c": "new_section"
        },
        "new_key": "override_new"
    }

    result = deep_merge_dicts(base, override)

    # Verify merge results
    assert result["level1"]["level2a"]["value1"] == "override_value1", "Override should replace base value"
    assert result["level1"]["level2a"]["value2"] == "base_value2", "Base value should be preserved"
    assert result["level1"]["level2a"]["value3"] == "override_value3", "New override value should be added"
    assert result["level1"]["level2b"] == ["base_item1", "base_item2"], "Base list should be preserved"
    assert result["level1"]["level2c"] == "new_section", "New override section should be added"
    assert result["simple_key"] == "base_simple", "Simple base value should be preserved"
    assert result["new_key"] == "override_new", "New override key should be added"

    print("  ✅ Deep merge functionality works correctly")

def test_time_windows():
    """Test time window computation."""
    print("Testing time window computation...")

    # Test with default configuration
    config = {"time_windows": DEFAULT_TIME_WINDOWS}
    windows = compute_time_windows(config)

    # Verify all default windows are present
    expected_windows = set(DEFAULT_TIME_WINDOWS.keys())
    actual_windows = set(windows.keys())
    assert expected_windows == actual_windows, f"Expected windows {expected_windows}, got {actual_windows}"

    # Verify window structure
    for window_name, window_data in windows.items():
        assert "days" in window_data, f"Window {window_name} missing 'days'"
        assert "start" in window_data, f"Window {window_name} missing 'start'"
        assert "end" in window_data, f"Window {window_name} missing 'end'"
        assert "start_timestamp" in window_data, f"Window {window_name} missing 'start_timestamp'"
        assert "end_timestamp" in window_data, f"Window {window_name} missing 'end_timestamp'"

        # Verify the days match expected
        expected_days = DEFAULT_TIME_WINDOWS[window_name]
        assert window_data["days"] == expected_days, f"Window {window_name} has wrong days: {window_data['days']} vs {expected_days}"

    # Test with custom time windows
    custom_config = {
        "time_windows": {
            "last_7_days": 7,
            "last_month": 30,
            "last_year": 365
        }
    }
    custom_windows = compute_time_windows(custom_config)
    assert len(custom_windows) == 3, "Custom windows should have 3 entries"
    assert "last_7_days" in custom_windows, "Custom window should be present"
    assert custom_windows["last_7_days"]["days"] == 7, "Custom window should have correct days"

    print("  ✅ Time window computation works correctly")

def test_config_digest():
    """Test configuration digest computation."""
    print("Testing configuration digest computation...")

    config1 = {
        "project": "test",
        "settings": {
            "value1": 123,
            "value2": ["a", "b", "c"]
        }
    }

    config2 = {
        "settings": {
            "value2": ["a", "b", "c"],
            "value1": 123
        },
        "project": "test"
    }

    config3 = {
        "project": "test",
        "settings": {
            "value1": 124,  # Different value
            "value2": ["a", "b", "c"]
        }
    }

    digest1 = compute_config_digest(config1)
    digest2 = compute_config_digest(config2)
    digest3 = compute_config_digest(config3)

    # Same content (different order) should produce same digest
    assert digest1 == digest2, "Configs with same content should have same digest"

    # Different content should produce different digest
    assert digest1 != digest3, "Configs with different content should have different digests"

    # Verify digest format
    assert len(digest1) == 64, "Digest should be 64 characters (SHA256)"
    assert all(c in '0123456789abcdef' for c in digest1), "Digest should be hexadecimal"

    print("  ✅ Configuration digest computation works correctly")

def test_logging_setup():
    """Test logging setup."""
    print("Testing logging setup...")

    # Test with default settings
    logger1 = setup_logging()
    assert logger1.name == "repo_reporter", "Logger should have correct name"

    # Test with custom settings
    logger2 = setup_logging(level="DEBUG", include_timestamps=False)
    assert logger2.name == "repo_reporter", "Logger should have correct name"

    print("  ✅ Logging setup works correctly")

def test_configuration_loading():
    """Test configuration loading with template and project override."""
    print("Testing configuration loading...")

    # Create temporary configuration files
    with tempfile.TemporaryDirectory() as temp_dir:
        config_dir = Path(temp_dir)

        # Create template config
        template_config = {
            "project": "template_default",
            "output": {
                "top_n_repos": 30,
                "include_sections": {
                    "contributors": True,
                    "organizations": True
                }
            },
            "time_windows": {
                "last_30_days": 30,
                "last_365_days": 365
            },
            "activity_threshold_days": 365
        }

        template_path = config_dir / "template.config"
        with open(template_path, 'w') as f:
            json.dump(template_config, f)  # Using JSON for simplicity in test

        # Create project override config
        project_config = {
            "output": {
                "top_n_repos": 15,  # Override
                "include_sections": {
                    "organizations": False  # Override
                }
            },
            "time_windows": {
                "last_7_days": 7  # Add new window
            },
            "activity_threshold_days": 180  # Override
        }

        project_path = config_dir / "test-project.config"
        with open(project_path, 'w') as f:
            json.dump(project_config, f)

        # Test loading with project override
        try:
            merged_config = load_configuration(config_dir, "test-project")
        except Exception as e:
            print(f"  ❌ Configuration loading failed: {e}")
            return False

        # Verify merge results
        assert merged_config["project"] == "test-project", "Project name should be set correctly"
        assert merged_config["output"]["top_n_repos"] == 15, "Override value should be used"
        assert merged_config["output"]["include_sections"]["contributors"] == True, "Template value should be preserved"
        assert merged_config["output"]["include_sections"]["organizations"] == False, "Override value should be used"
        assert merged_config["time_windows"]["last_30_days"] == 30, "Template window should be preserved"
        assert merged_config["time_windows"]["last_365_days"] == 365, "Template window should be preserved"
        assert merged_config["time_windows"]["last_7_days"] == 7, "Override window should be added"
        assert merged_config["activity_threshold_days"] == 180, "Override threshold should be used"

        # Test loading with non-existent project (should only use template)
        merged_config_no_override = load_configuration(config_dir, "nonexistent-project")
        assert merged_config_no_override["project"] == "nonexistent-project", "Project name should be set"
        assert merged_config_no_override["output"]["top_n_repos"] == 30, "Template value should be used"
        assert merged_config_no_override["activity_threshold_days"] == 365, "Template value should be used"

    print("  ✅ Configuration loading works correctly")

def test_schema_constants():
    """Test that schema constants are properly defined."""
    print("Testing schema constants...")

    # Verify version constants
    assert isinstance(SCHEMA_VERSION, str), "SCHEMA_VERSION should be a string"
    assert isinstance(SCRIPT_VERSION, str), "SCRIPT_VERSION should be a string"
    assert "." in SCHEMA_VERSION, "SCHEMA_VERSION should be in version format"
    assert "." in SCRIPT_VERSION, "SCRIPT_VERSION should be in version format"

    # Verify default time windows
    assert isinstance(DEFAULT_TIME_WINDOWS, dict), "DEFAULT_TIME_WINDOWS should be a dict"
    assert len(DEFAULT_TIME_WINDOWS) > 0, "DEFAULT_TIME_WINDOWS should not be empty"

    for window_name, days in DEFAULT_TIME_WINDOWS.items():
        assert isinstance(window_name, str), f"Window name {window_name} should be string"
        assert isinstance(days, int), f"Window days for {window_name} should be integer"
        assert days > 0, f"Window days for {window_name} should be positive"

    print("  ✅ Schema constants are properly defined")

def run_all_tests():
    """Run all Phase 1 tests."""
    print("🧪 Running Phase 1 Tests for Repository Reporting System")
    print(f"   Script Version: {SCRIPT_VERSION}")
    print(f"   Schema Version: {SCHEMA_VERSION}")
    print("-" * 60)

    tests = [
        test_deep_merge,
        test_time_windows,
        test_config_digest,
        test_logging_setup,
        test_configuration_loading,
        test_schema_constants
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            print(f"  ❌ {test_func.__name__} failed: {e}")
            failed += 1
            import traceback
            traceback.print_exc()

    print("-" * 60)
    print(f"📊 Test Results: {passed} passed, {failed} failed")

    if failed == 0:
        print("🎉 All Phase 1 tests passed! Foundation is solid.")
        return True
    else:
        print("💥 Some tests failed. Please fix issues before proceeding to Phase 2.")
        return False

if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
