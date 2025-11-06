#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 The Linux Foundation

"""
Test script for INFO.yaml collector functionality.

This script tests the INFOYamlCollector class to ensure it properly:
- Collects INFO.yaml files from the info-master repository
- Parses project metadata correctly
- Validates issue tracker URLs
- Enriches committer data with git activity
- Handles missing or malformed data gracefully
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict

# Add parent directory to path to import generate_reports
sys.path.insert(0, str(Path(__file__).parent))

from generate_reports import INFOYamlCollector, setup_logging

def test_info_yaml_collection():
    """Test basic INFO.yaml collection from local repository."""
    print("\n" + "="*80)
    print("TEST: INFO.yaml Collection")
    print("="*80)

    # Setup logging
    logger = setup_logging("DEBUG")

    # Test configuration
    config = {
        "info_yaml": {
            "enabled": True,
            "local_path": "testing/info-master",
            "activity_windows": {
                "current": 365,
                "active": 1095,
            }
        }
    }

    # Default time windows
    time_windows = {
        "last_365_days": {"days": 365, "start": "2024-01-20", "end": "2025-01-20"},
        "last_3_years": {"days": 1095, "start": "2022-01-20", "end": "2025-01-20"},
    }

    # Create collector
    collector = INFOYamlCollector(config, time_windows, logger)

    # Set info-master path
    info_master_path = Path("testing/info-master")
    if not info_master_path.exists():
        print(f"❌ FAILED: info-master path does not exist: {info_master_path}")
        return False

    collector.set_info_master_path(info_master_path)

    # Collect all projects
    print("\n📁 Collecting INFO.yaml files...")
    projects = collector.collect_all_projects()

    if not projects:
        print("❌ FAILED: No projects collected")
        return False

    print(f"✅ Collected {len(projects)} projects")

    # Display sample projects
    print("\n📋 Sample Projects:")
    for i, project in enumerate(projects[:5]):
        print(f"\n  Project {i+1}:")
        print(f"    Name: {project.get('project_name')}")
        print(f"    Server: {project.get('gerrit_server')}")
        print(f"    Path: {project.get('project_path')}")
        print(f"    Created: {project.get('creation_date')}")
        print(f"    Lifecycle: {project.get('lifecycle_state')}")
        print(f"    Lead: {project.get('project_lead', {}).get('name')}")
        print(f"    Committers: {len(project.get('committers', []))}")

        issue_tracking = project.get('issue_tracking', {})
        if issue_tracking:
            print(f"    Issue Tracker: {issue_tracking.get('url', 'N/A')}")

    # Group by Gerrit server
    servers = {}
    for project in projects:
        server = project.get('gerrit_server', 'unknown')
        if server not in servers:
            servers[server] = []
        servers[server].append(project)

    print(f"\n📊 Projects by Gerrit Server:")
    for server, server_projects in sorted(servers.items()):
        print(f"  {server}: {len(server_projects)} projects")

    return True


def test_url_validation():
    """Test issue tracker URL validation."""
    print("\n" + "="*80)
    print("TEST: Issue Tracker URL Validation")
    print("="*80)

    logger = setup_logging("INFO")

    config = {
        "info_yaml": {
            "validate_urls": True,
            "url_timeout": 10.0,
        }
    }

    time_windows = {}
    collector = INFOYamlCollector(config, time_windows, logger)

    # Test URLs
    test_urls = [
        ("https://jira.onap.org/projects/AAF", "Valid JIRA URL"),
        ("https://github.com/onap/aaf", "Valid GitHub URL"),
        ("https://invalid-domain-that-does-not-exist-12345.com", "Invalid domain"),
        ("", "Empty URL"),
    ]

    print("\n🔗 Testing URL validation:")
    for url, description in test_urls:
        print(f"\n  Testing: {description}")
        print(f"  URL: {url or '(empty)'}")

        is_valid, error_msg = collector.validate_issue_tracker_url(url)

        if is_valid:
            print(f"  ✅ Valid")
        else:
            print(f"  ❌ Invalid: {error_msg}")

    return True


def test_committer_enrichment():
    """Test enriching committers with git activity data."""
    print("\n" + "="*80)
    print("TEST: Committer Git Activity Enrichment")
    print("="*80)

    logger = setup_logging("INFO")

    config = {
        "info_yaml": {
            "activity_windows": {
                "current": 365,
                "active": 1095,
            }
        }
    }

    time_windows = {}
    collector = INFOYamlCollector(config, time_windows, logger)

    # Mock committers
    committers = [
        {
            "name": "John Doe",
            "email": "john.doe@example.com",
            "company": "Example Corp",
            "id": "johnd",
        },
        {
            "name": "Jane Smith",
            "email": "jane.smith@example.com",
            "company": "Example Corp",
            "id": "janes",
        },
    ]

    # Mock git author data
    authors_data = {
        "john.doe@example.com": {
            "name": "John Doe",
            "days_since_last_commit": 100,  # Current (within 365 days)
        },
        "jane.smith@example.com": {
            "name": "Jane Smith",
            "days_since_last_commit": 500,  # Active (between 365-1095 days)
        },
    }

    print("\n👥 Enriching committer activity:")
    enriched = collector._enrich_committers_activity(committers, authors_data)

    for committer in enriched:
        name = committer.get("name")
        status = committer.get("activity_status")
        color = committer.get("activity_color")
        print(f"  {name}: {status} ({color})")

    # Verify results
    assert enriched[0]["activity_status"] == "current"
    assert enriched[0]["activity_color"] == "green"
    assert enriched[1]["activity_status"] == "active"
    assert enriched[1]["activity_color"] == "orange"

    print("  ✅ Activity enrichment working correctly")

    return True


def test_info_yaml_parsing():
    """Test parsing individual INFO.yaml files."""
    print("\n" + "="*80)
    print("TEST: INFO.yaml File Parsing")
    print("="*80)

    logger = setup_logging("INFO")

    config = {}
    time_windows = {}
    collector = INFOYamlCollector(config, time_windows, logger)

    # Find a sample INFO.yaml file
    info_master_path = Path("testing/info-master")
    if not info_master_path.exists():
        print("❌ FAILED: info-master path does not exist")
        return False

    collector.set_info_master_path(info_master_path)

    # Find first INFO.yaml file
    sample_file = next(info_master_path.rglob("INFO.yaml"), None)
    if not sample_file:
        print("❌ FAILED: No INFO.yaml files found")
        return False

    print(f"\n📄 Parsing sample file: {sample_file.relative_to(info_master_path)}")

    # Parse it
    project_data = collector._parse_info_yaml(sample_file)

    if not project_data:
        print("❌ FAILED: Parsing returned None")
        return False

    print("\n✅ Successfully parsed INFO.yaml")
    print("\n📋 Parsed Data:")
    print(f"  Project: {project_data.get('project_name')}")
    print(f"  Gerrit Server: {project_data.get('gerrit_server')}")
    print(f"  Project Path: {project_data.get('project_path')}")
    print(f"  Creation Date: {project_data.get('creation_date')}")
    print(f"  Lifecycle: {project_data.get('lifecycle_state')}")
    print(f"  Lead: {project_data.get('project_lead', {}).get('name')}")
    print(f"  Lead Email: {project_data.get('project_lead', {}).get('email')}")
    print(f"  Committers: {len(project_data.get('committers', []))}")

    # Display committers
    committers = project_data.get('committers', [])
    if committers:
        print("\n  👥 Committers:")
        for committer in committers[:3]:  # Show first 3
            print(f"    - {committer.get('name')} ({committer.get('email')})")

    # Display issue tracking
    issue_tracking = project_data.get('issue_tracking', {})
    if issue_tracking:
        print(f"\n  🔗 Issue Tracker:")
        print(f"    Type: {issue_tracking.get('type')}")
        print(f"    URL: {issue_tracking.get('url')}")

    return True


def main():
    """Run all tests."""
    print("\n" + "="*80)
    print("INFO.yaml Collector Test Suite")
    print("="*80)

    tests = [
        ("INFO.yaml Collection", test_info_yaml_collection),
        ("INFO.yaml Parsing", test_info_yaml_parsing),
        ("Committer Enrichment", test_committer_enrichment),
        ("URL Validation", test_url_validation),
    ]

    results = []

    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"\n❌ EXCEPTION in {test_name}: {e}")
            import traceback
            traceback.print_exc()
            results.append((test_name, False))

    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {test_name}")

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 All tests passed!")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
