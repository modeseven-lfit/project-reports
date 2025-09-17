#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 The Linux Foundation

"""
Phase 4 Test Suite: Aggregation & Ranking
==========================================

Tests the DataAggregator class functionality including:
- Author rollup aggregation across repositories
- Organization aggregation by email domain
- Repository activity classification and ranking
- Age distribution bucketing
- Contributor and organization leaderboards
- Deterministic sorting with tie-breaking
"""

import sys
import logging
from pathlib import Path
from typing import Dict, List, Any

# Add the project root to Python path so we can import the main module
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from generate_reports import DataAggregator, setup_logging

def create_test_config() -> Dict[str, Any]:
    """Create test configuration for aggregator."""
    return {
        "project": "test-aggregation",
        "activity_threshold_days": 365,
        "age_buckets": {
            "very_old_years": 3,
            "old_years": 1
        },
        "output": {
            "top_n_repos": 10,
            "bottom_n_repos": 10
        },
        "time_windows": {
            "last_30_days": 30,
            "last_90_days": 90,
            "last_365_days": 365,
            "last_3_years": 1095
        }
    }

def create_test_repositories() -> List[Dict[str, Any]]:
    """Create test repository data for aggregation testing."""
    return [
        {
            "name": "active-repo-1",
            "path": "/path/to/active-repo-1",
            "last_commit_timestamp": "2025-01-15T10:00:00Z",
            "days_since_last_commit": 30,
            "active": True,
            "commits": {
                "last_30_days": 25,
                "last_90_days": 75,
                "last_365_days": 200,
                "last_3_years": 500
            },
            "lines_added": {
                "last_30_days": 500,
                "last_90_days": 1500,
                "last_365_days": 4000,
                "last_3_years": 10000
            },
            "lines_removed": {
                "last_30_days": 200,
                "last_90_days": 600,
                "last_365_days": 1600,
                "last_3_years": 4000
            },
            "lines_net": {
                "last_30_days": 300,
                "last_90_days": 900,
                "last_365_days": 2400,
                "last_3_years": 6000
            },
            "authors": [
                {
                    "name": "Alice Developer",
                    "email": "alice@example.com",
                    "username": "alice",
                    "commits": {
                        "last_30_days": 15,
                        "last_90_days": 45,
                        "last_365_days": 120,
                        "last_3_years": 300
                    },
                    "lines_added": {
                        "last_30_days": 300,
                        "last_90_days": 900,
                        "last_365_days": 2400,
                        "last_3_years": 6000
                    },
                    "lines_removed": {
                        "last_30_days": 100,
                        "last_90_days": 300,
                        "last_365_days": 800,
                        "last_3_years": 2000
                    },
                    "lines_net": {
                        "last_30_days": 200,
                        "last_90_days": 600,
                        "last_365_days": 1600,
                        "last_3_years": 4000
                    }
                },
                {
                    "name": "Bob Contributor",
                    "email": "bob@company.org",
                    "username": "bob",
                    "commits": {
                        "last_30_days": 10,
                        "last_90_days": 30,
                        "last_365_days": 80,
                        "last_3_years": 200
                    },
                    "lines_added": {
                        "last_30_days": 200,
                        "last_90_days": 600,
                        "last_365_days": 1600,
                        "last_3_years": 4000
                    },
                    "lines_removed": {
                        "last_30_days": 100,
                        "last_90_days": 300,
                        "last_365_days": 800,
                        "last_3_years": 2000
                    },
                    "lines_net": {
                        "last_30_days": 100,
                        "last_90_days": 300,
                        "last_365_days": 800,
                        "last_3_years": 2000
                    }
                }
            ],
            "features": {
                "dependabot": {"present": True},
                "pre_commit": {"present": True},
                "project_types": {"detected_types": ["python"], "primary_type": "python"}
            }
        },
        {
            "name": "active-repo-2",
            "path": "/path/to/active-repo-2",
            "last_commit_timestamp": "2025-01-10T15:30:00Z",
            "days_since_last_commit": 35,
            "active": True,
            "commits": {
                "last_30_days": 10,
                "last_90_days": 30,
                "last_365_days": 150,
                "last_3_years": 300
            },
            "lines_added": {
                "last_30_days": 200,
                "last_90_days": 600,
                "last_365_days": 3000,
                "last_3_years": 6000
            },
            "lines_removed": {
                "last_30_days": 50,
                "last_90_days": 150,
                "last_365_days": 1000,
                "last_3_years": 2000
            },
            "lines_net": {
                "last_30_days": 150,
                "last_90_days": 450,
                "last_365_days": 2000,
                "last_3_years": 4000
            },
            "authors": [
                {
                    "name": "Alice Developer",
                    "email": "alice@example.com",
                    "username": "alice",
                    "commits": {
                        "last_30_days": 8,
                        "last_90_days": 24,
                        "last_365_days": 100,
                        "last_3_years": 200
                    },
                    "lines_added": {
                        "last_30_days": 160,
                        "last_90_days": 480,
                        "last_365_days": 2000,
                        "last_3_years": 4000
                    },
                    "lines_removed": {
                        "last_30_days": 40,
                        "last_90_days": 120,
                        "last_365_days": 600,
                        "last_3_years": 1200
                    },
                    "lines_net": {
                        "last_30_days": 120,
                        "last_90_days": 360,
                        "last_365_days": 1400,
                        "last_3_years": 2800
                    }
                },
                {
                    "name": "Charlie Engineer",
                    "email": "charlie@company.org",
                    "username": "charlie",
                    "commits": {
                        "last_30_days": 2,
                        "last_90_days": 6,
                        "last_365_days": 50,
                        "last_3_years": 100
                    },
                    "lines_added": {
                        "last_30_days": 40,
                        "last_90_days": 120,
                        "last_365_days": 1000,
                        "last_3_years": 2000
                    },
                    "lines_removed": {
                        "last_30_days": 10,
                        "last_90_days": 30,
                        "last_365_days": 400,
                        "last_3_years": 800
                    },
                    "lines_net": {
                        "last_30_days": 30,
                        "last_90_days": 90,
                        "last_365_days": 600,
                        "last_3_years": 1200
                    }
                }
            ],
            "features": {
                "dependabot": {"present": False},
                "workflows": {"count": 2, "classified": {"verify": 1, "merge": 1}}
            }
        },
        {
            "name": "old-inactive-repo",
            "path": "/path/to/old-inactive-repo",
            "last_commit_timestamp": "2022-06-01T09:00:00Z",
            "days_since_last_commit": 960,  # ~2.6 years old
            "active": False,
            "commits": {
                "last_30_days": 0,
                "last_90_days": 0,
                "last_365_days": 0,
                "last_3_years": 50
            },
            "lines_added": {
                "last_30_days": 0,
                "last_90_days": 0,
                "last_365_days": 0,
                "last_3_years": 1000
            },
            "lines_removed": {
                "last_30_days": 0,
                "last_90_days": 0,
                "last_365_days": 0,
                "last_3_years": 200
            },
            "lines_net": {
                "last_30_days": 0,
                "last_90_days": 0,
                "last_365_days": 0,
                "last_3_years": 800
            },
            "authors": [
                {
                    "name": "Dave Maintainer",
                    "email": "dave@oldcompany.com",
                    "username": "dave",
                    "commits": {
                        "last_30_days": 0,
                        "last_90_days": 0,
                        "last_365_days": 0,
                        "last_3_years": 50
                    },
                    "lines_added": {
                        "last_30_days": 0,
                        "last_90_days": 0,
                        "last_365_days": 0,
                        "last_3_years": 1000
                    },
                    "lines_removed": {
                        "last_30_days": 0,
                        "last_90_days": 0,
                        "last_365_days": 0,
                        "last_3_years": 200
                    },
                    "lines_net": {
                        "last_30_days": 0,
                        "last_90_days": 0,
                        "last_365_days": 0,
                        "last_3_years": 800
                    }
                }
            ],
            "features": {
                "project_types": {"detected_types": ["java"], "primary_type": "java"}
            }
        },
        {
            "name": "very-old-repo",
            "path": "/path/to/very-old-repo",
            "last_commit_timestamp": "2020-01-01T00:00:00Z",
            "days_since_last_commit": 1840,  # ~5 years old
            "active": False,
            "commits": {
                "last_30_days": 0,
                "last_90_days": 0,
                "last_365_days": 0,
                "last_3_years": 0
            },
            "lines_added": {
                "last_30_days": 0,
                "last_90_days": 0,
                "last_365_days": 0,
                "last_3_years": 0
            },
            "lines_removed": {
                "last_30_days": 0,
                "last_90_days": 0,
                "last_365_days": 0,
                "last_3_years": 0
            },
            "lines_net": {
                "last_30_days": 0,
                "last_90_days": 0,
                "last_365_days": 0,
                "last_3_years": 0
            },
            "authors": [
                {
                    "name": "Eve Legacy",
                    "email": "eve@defunct.net",
                    "username": "eve",
                    "commits": {
                        "last_30_days": 0,
                        "last_90_days": 0,
                        "last_365_days": 0,
                        "last_3_years": 0
                    },
                    "lines_added": {
                        "last_30_days": 0,
                        "last_90_days": 0,
                        "last_365_days": 0,
                        "last_3_years": 0
                    },
                    "lines_removed": {
                        "last_30_days": 0,
                        "last_90_days": 0,
                        "last_365_days": 0,
                        "last_3_years": 0
                    },
                    "lines_net": {
                        "last_30_days": 0,
                        "last_90_days": 0,
                        "last_365_days": 0,
                        "last_3_years": 0
                    }
                }
            ],
            "features": {
                "project_types": {"detected_types": ["c++"], "primary_type": "c++"}
            }
        }
    ]

def test_author_rollups(aggregator: DataAggregator, test_repos: List[Dict[str, Any]]) -> bool:
    """Test author aggregation across multiple repositories."""
    print("Testing author rollups...")

    authors = aggregator.compute_author_rollups(test_repos)

    # Check we have the expected number of unique authors
    expected_authors = {"alice@example.com", "bob@company.org", "charlie@company.org",
                       "dave@oldcompany.com", "eve@defunct.net"}
    actual_emails = {author["email"] for author in authors}

    if actual_emails != expected_authors:
        print(f"❌ Expected authors {expected_authors}, got {actual_emails}")
        return False

    # Check Alice's aggregated stats (appears in 2 repos)
    alice = next(author for author in authors if author["email"] == "alice@example.com")
    expected_commits_365 = 120 + 100  # From both repositories
    if alice["commits"]["last_365_days"] != expected_commits_365:
        print(f"❌ Alice should have {expected_commits_365} commits in last_365_days, got {alice['commits']['last_365_days']}")
        return False

    # Check Alice touched 2 repositories
    if alice["repositories_count"]["last_365_days"] != 2:
        print(f"❌ Alice should have touched 2 repositories, got {alice['repositories_count']['last_365_days']}")
        return False

    print("✅ Author rollups working correctly")
    return True

def test_organization_rollups(aggregator: DataAggregator, test_repos: List[Dict[str, Any]]) -> bool:
    """Test organization aggregation by email domain."""
    print("Testing organization rollups...")

    authors = aggregator.compute_author_rollups(test_repos)
    organizations = aggregator.compute_org_rollups(authors)

    # Check expected domains
    expected_domains = {"example.com", "company.org", "oldcompany.com", "defunct.net"}
    actual_domains = {org["domain"] for org in organizations}

    if actual_domains != expected_domains:
        print(f"❌ Expected domains {expected_domains}, got {actual_domains}")
        return False

    # Check company.org has 2 contributors (Bob and Charlie)
    company_org = next(org for org in organizations if org["domain"] == "company.org")
    if company_org["contributor_count"] != 2:
        print(f"❌ company.org should have 2 contributors, got {company_org['contributor_count']}")
        return False

    # Check company.org's aggregated commits (Bob + Charlie in last_365_days)
    expected_commits = 80 + 50  # Bob's + Charlie's commits
    if company_org["commits"]["last_365_days"] != expected_commits:
        print(f"❌ company.org should have {expected_commits} commits, got {company_org['commits']['last_365_days']}")
        return False

    print("✅ Organization rollups working correctly")
    return True

def test_repository_classification(aggregator: DataAggregator, test_repos: List[Dict[str, Any]]) -> bool:
    """Test active/inactive repository classification and age buckets."""
    print("Testing repository classification...")

    summaries = aggregator.aggregate_global_data(test_repos)

    # Check active/inactive counts
    if summaries["counts"]["active_repositories"] != 2:
        print(f"❌ Expected 2 active repositories, got {summaries['counts']['active_repositories']}")
        return False

    if summaries["counts"]["inactive_repositories"] != 2:
        print(f"❌ Expected 2 inactive repositories, got {summaries['counts']['inactive_repositories']}")
        return False

    # Check age distribution
    activity_dist = summaries["activity_distribution"]

    # old-inactive-repo should be in "old" bucket (~2.6 years)
    old_repo_names = {repo["name"] for repo in activity_dist["old"]}
    if "old-inactive-repo" not in old_repo_names:
        print(f"❌ 'old-inactive-repo' should be in 'old' bucket, got {old_repo_names}")
        return False

    # very-old-repo should be in "very_old" bucket (~5 years)
    very_old_repo_names = {repo["name"] for repo in activity_dist["very_old"]}
    if "very-old-repo" not in very_old_repo_names:
        print(f"❌ 'very-old-repo' should be in 'very_old' bucket, got {very_old_repo_names}")
        return False

    print("✅ Repository classification working correctly")
    return True

def test_ranking_and_sorting(aggregator: DataAggregator, test_repos: List[Dict[str, Any]]) -> bool:
    """Test entity ranking with deterministic sorting."""
    print("Testing ranking and sorting...")

    # Test basic ranking
    test_entities = [
        {"name": "repo-b", "commits": {"last_365_days": 100}},
        {"name": "repo-a", "commits": {"last_365_days": 200}},
        {"name": "repo-c", "commits": {"last_365_days": 100}},  # Tie with repo-b
    ]

    # Test descending sort with nested key
    ranked = aggregator.rank_entities(test_entities, "commits.last_365_days", reverse=True)

    # Should be: repo-a (200), repo-b (100, alphabetically first), repo-c (100)
    expected_order = ["repo-a", "repo-b", "repo-c"]
    actual_order = [repo["name"] for repo in ranked]

    if actual_order != expected_order:
        print(f"❌ Expected ranking {expected_order}, got {actual_order}")
        return False

    # Test top active repositories from real data
    summaries = aggregator.aggregate_global_data(test_repos)
    top_active = summaries["top_active_repositories"]

    # active-repo-1 should rank higher than active-repo-2 (200 vs 150 commits)
    if len(top_active) < 2 or top_active[0]["name"] != "active-repo-1":
        print(f"❌ active-repo-1 should be top ranked repository")
        return False

    print("✅ Ranking and sorting working correctly")
    return True

def test_leaderboards(aggregator: DataAggregator, test_repos: List[Dict[str, Any]]) -> bool:
    """Test contributor and organization leaderboards."""
    print("Testing leaderboards...")

    summaries = aggregator.aggregate_global_data(test_repos)

    # Test contributor leaderboards exist and have data
    top_contributors_commits = summaries["top_contributors_commits"]
    top_contributors_loc = summaries["top_contributors_loc"]
    top_organizations = summaries["top_organizations"]

    if not top_contributors_commits:
        print("❌ Top contributors by commits leaderboard is empty")
        return False

    if not top_contributors_loc:
        print("❌ Top contributors by LOC leaderboard is empty")
        return False

    if not top_organizations:
        print("❌ Top organizations leaderboard is empty")
        return False

    # Alice should be top contributor (appears in 2 repos)
    if top_contributors_commits[0]["email"] != "alice@example.com":
        print(f"❌ Alice should be top contributor by commits, got {top_contributors_commits[0]['email']}")
        return False

    print("✅ Leaderboards working correctly")
    return True

def test_data_integrity(aggregator: DataAggregator, test_repos: List[Dict[str, Any]]) -> bool:
    """Test data integrity and error handling."""
    print("Testing data integrity...")

    # Test with malformed repository data
    malformed_repos = [
        {
            "name": "missing-authors-repo",
            "commits": {"last_365_days": 10},
            # Missing authors array
        },
        {
            "name": "empty-authors-repo",
            "commits": {"last_365_days": 5},
            "authors": []
        }
    ]

    # Should not crash and should handle gracefully
    try:
        authors = aggregator.compute_author_rollups(malformed_repos)
        organizations = aggregator.compute_org_rollups(authors)
        summaries = aggregator.aggregate_global_data(malformed_repos)

        # Should have minimal data but not crash
        if summaries["counts"]["total_repositories"] != 2:
            print(f"❌ Should count all repositories including malformed ones")
            return False

    except Exception as e:
        print(f"❌ Should handle malformed data gracefully, but got exception: {e}")
        return False

    print("✅ Data integrity handling working correctly")
    return True

def run_all_tests() -> bool:
    """Run all Phase 4 tests."""
    print("🧪 Running Phase 4 Tests for Repository Reporting System")
    print("   Testing: Aggregation & Ranking")
    print("-" * 60)

    # Setup
    logger = setup_logging("ERROR")  # Reduce noise during testing
    config = create_test_config()
    aggregator = DataAggregator(config, logger)
    test_repos = create_test_repositories()

    # Run tests
    tests = [
        ("author rollups", lambda: test_author_rollups(aggregator, test_repos)),
        ("organization rollups", lambda: test_organization_rollups(aggregator, test_repos)),
        ("repository classification", lambda: test_repository_classification(aggregator, test_repos)),
        ("ranking and sorting", lambda: test_ranking_and_sorting(aggregator, test_repos)),
        ("leaderboards", lambda: test_leaderboards(aggregator, test_repos)),
        ("data integrity", lambda: test_data_integrity(aggregator, test_repos)),
    ]

    passed = 0
    failed = 0

    for test_name, test_func in tests:
        try:
            if test_func():
                passed += 1
            else:
                failed += 1
                print(f"Testing {test_name}...                    ❌")
        except Exception as e:
            failed += 1
            print(f"Testing {test_name}...                    ❌")
            print(f"  Exception: {e}")

    print("-" * 60)
    print(f"📊 Test Results: {passed} passed, {failed} failed")

    if failed == 0:
        print("🎉 All Phase 4 tests passed! Aggregation and ranking are working!")
        return True
    else:
        print("❌ Some Phase 4 tests failed. Check implementation.")
        return False

if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
