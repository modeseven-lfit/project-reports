#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 The Linux Foundation

"""
Test script for API statistics tracking.

This script tests the APIStatistics class to ensure proper tracking and reporting
of GitHub, Gerrit, Jenkins API calls and info-master clone status.
"""

import sys
import os

# Add parent directory to path to import generate_reports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from generate_reports import APIStatistics


def test_api_statistics():
    """Test API statistics tracking."""
    print("🧪 Testing API Statistics Tracker\n")

    # Create a new statistics instance
    stats = APIStatistics()

    # Test 1: Initial state
    print("Test 1: Initial state")
    assert stats.get_total_calls("github") == 0
    assert stats.get_total_errors("github") == 0
    assert not stats.has_errors()
    print("✅ Initial state is correct\n")

    # Test 2: Record GitHub success
    print("Test 2: Recording GitHub successes")
    stats.record_success("github")
    stats.record_success("github")
    stats.record_success("github")
    assert stats.get_total_calls("github") == 3
    assert stats.stats["github"]["success"] == 3
    assert stats.get_total_errors("github") == 0
    print("✅ GitHub successes recorded correctly\n")

    # Test 3: Record GitHub errors
    print("Test 3: Recording GitHub errors")
    stats.record_error("github", 401)
    stats.record_error("github", 403)
    stats.record_error("github", 403)
    assert stats.get_total_errors("github") == 3
    assert stats.stats["github"]["errors"][401] == 1
    assert stats.stats["github"]["errors"][403] == 2
    assert stats.get_total_calls("github") == 6  # 3 success + 3 errors
    print("✅ GitHub errors recorded correctly\n")

    # Test 4: Record Gerrit operations
    print("Test 4: Recording Gerrit operations")
    stats.record_success("gerrit")
    stats.record_success("gerrit")
    stats.record_error("gerrit", 404)
    assert stats.get_total_calls("gerrit") == 3
    assert stats.stats["gerrit"]["success"] == 2
    assert stats.stats["gerrit"]["errors"][404] == 1
    print("✅ Gerrit operations recorded correctly\n")

    # Test 5: Record Jenkins operations
    print("Test 5: Recording Jenkins operations")
    stats.record_success("jenkins")
    stats.record_error("jenkins", 500)
    stats.record_exception("jenkins")
    assert stats.get_total_calls("jenkins") == 3
    assert stats.stats["jenkins"]["success"] == 1
    assert stats.stats["jenkins"]["errors"][500] == 1
    assert stats.stats["jenkins"]["errors"]["exception"] == 1
    print("✅ Jenkins operations recorded correctly\n")

    # Test 6: Info-master success
    print("Test 6: Recording info-master success")
    stats.record_info_master(True)
    assert stats.stats["info_master"]["success"] == True
    assert stats.stats["info_master"]["error"] is None
    print("✅ Info-master success recorded correctly\n")

    # Test 7: Info-master failure
    print("Test 7: Recording info-master failure")
    stats2 = APIStatistics()
    stats2.record_info_master(False, "Clone failed: timeout")
    assert stats2.stats["info_master"]["success"] == False
    assert stats2.stats["info_master"]["error"] == "Clone failed: timeout"
    print("✅ Info-master failure recorded correctly\n")

    # Test 8: has_errors detection
    print("Test 8: Testing error detection")
    assert stats.has_errors()  # Has GitHub, Gerrit, Jenkins errors
    stats_clean = APIStatistics()
    stats_clean.record_success("github")
    stats_clean.record_info_master(True)
    assert not stats_clean.has_errors()
    print("✅ Error detection working correctly\n")

    # Test 9: Console output formatting
    print("Test 9: Testing console output formatting")
    output = stats.format_console_output()
    assert "GitHub API Statistics" in output
    assert "Successful calls: 3" in output
    assert "Failed calls: 3" in output
    assert "Error 401: 1" in output
    assert "Error 403: 2" in output
    assert "Gerrit API Statistics" in output
    assert "Jenkins API Statistics" in output
    assert "Info-Master Clone" in output
    assert "Successfully cloned" in output
    print("✅ Console output formatted correctly\n")

    # Test 10: Console output with failures
    print("Test 10: Testing console output with info-master failure")
    output2 = stats2.format_console_output()
    assert "Info-Master Clone" in output2
    assert "Failed:" in output2
    assert "Clone failed: timeout" in output2
    print("✅ Failure output formatted correctly\n")

    # Test 11: Empty statistics
    print("Test 11: Testing empty statistics")
    stats_empty = APIStatistics()
    output_empty = stats_empty.format_console_output()
    assert output_empty == ""  # No output for empty stats
    print("✅ Empty statistics handled correctly\n")

    # Test 12: Display sample output
    print("=" * 60)
    print("Sample Console Output:")
    print("=" * 60)
    print(stats.format_console_output())
    print("=" * 60)

    print("\n🎉 All tests passed!")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(test_api_statistics())
    except Exception as e:
        print(f"\n❌ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
