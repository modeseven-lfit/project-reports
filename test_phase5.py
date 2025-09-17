#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 The Linux Foundation

"""
Phase 5 Test Suite: Output Generation (Markdown, HTML, ZIP)
==========================================================

Tests the ReportRenderer class functionality including:
- Markdown report generation with formatted tables
- HTML conversion with embedded CSS
- Number formatting and abbreviation
- Age formatting (days to human readable)
- ZIP packaging with all artifacts
- Table generation with emoji indicators
- Report structure and content validation
"""

import sys
import logging
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, Any

# Add the project root to Python path so we can import the main module
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from generate_reports import ReportRenderer, setup_logging, save_resolved_config, create_report_bundle

def create_test_config() -> Dict[str, Any]:
    """Create test configuration for renderer."""
    return {
        "project": "test-rendering",
        "output": {
            "include_sections": {
                "contributors": True,
                "organizations": True,
                "repo_feature_matrix": True,
                "inactive_distributions": True
            },
            "top_n_repos": 5,
            "bottom_n_repos": 5
        },
        "render": {
            "abbreviate_large_numbers": True,
            "large_number_threshold": 1000,
            "show_net_lines": True,
            "show_added_removed": False,
            "emoji": {
                "active": "✅",
                "inactive": "⚠️",
                "missing": "❌"
            }
        }
    }

def create_test_report_data() -> Dict[str, Any]:
    """Create comprehensive test data for rendering."""
    return {
        "schema_version": "1.0.0",
        "generated_at": "2025-09-17T20:19:32.123456+00:00",
        "project": "test-rendering",
        "config_digest": "abc123def456...",
        "script_version": "1.0.0",
        "time_windows": {
            "last_30_days": {"days": 30},
            "last_90_days": {"days": 90},
            "last_365_days": {"days": 365},
            "last_3_years": {"days": 1095}
        },
        "repositories": [
            {
                "name": "active-high-volume",
                "path": "/path/to/active-high-volume",
                "last_commit_timestamp": "2025-09-17T19:00:00Z",
                "days_since_last_commit": 0,
                "is_active": True,
                "commit_counts": {
                    "last_30_days": 150,
                    "last_90_days": 450,
                    "last_365_days": 1800,
                    "last_3_years": 5400
                },
                "loc_stats": {
                    "last_30_days": {"added": 5000, "removed": 1000, "net": 4000},
                    "last_90_days": {"added": 15000, "removed": 3000, "net": 12000},
                    "last_365_days": {"added": 60000, "removed": 12000, "net": 48000},
                    "last_3_years": {"added": 180000, "removed": 36000, "net": 144000}
                },
                "unique_contributors": {
                    "last_30_days": 8,
                    "last_90_days": 12,
                    "last_365_days": 25,
                    "last_3_years": 45
                },
                "features": {
                    "dependabot": {"present": True, "files": [".github/dependabot.yml"]},
                    "pre_commit": {"present": True, "config_file": ".pre-commit-config.yaml", "repos_count": 6},
                    "readthedocs": {"present": True, "config_type": "readthedocs", "config_files": [".readthedocs.yml"]},
                    "project_types": {"detected_types": ["python", "docker"], "primary_type": "python"},
                    "workflows": {"count": 3, "classified": {"verify": 2, "merge": 1, "other": 0}}
                }
            },
            {
                "name": "moderate-activity",
                "path": "/path/to/moderate-activity",
                "last_commit_timestamp": "2025-08-15T10:30:00Z",
                "days_since_last_commit": 33,
                "is_active": True,
                "commit_counts": {
                    "last_30_days": 0,
                    "last_90_days": 25,
                    "last_365_days": 320,
                    "last_3_years": 890
                },
                "loc_stats": {
                    "last_30_days": {"added": 0, "removed": 0, "net": 0},
                    "last_90_days": {"added": 800, "removed": 200, "net": 600},
                    "last_365_days": {"added": 12000, "removed": 4000, "net": 8000},
                    "last_3_years": {"added": 35000, "removed": 8000, "net": 27000}
                },
                "unique_contributors": {
                    "last_30_days": 0,
                    "last_90_days": 3,
                    "last_365_days": 8,
                    "last_3_years": 15
                },
                "features": {
                    "dependabot": {"present": False},
                    "pre_commit": {"present": False},
                    "readthedocs": {"present": False},
                    "project_types": {"detected_types": ["java"], "primary_type": "java"},
                    "workflows": {"count": 1, "classified": {"verify": 1, "merge": 0, "other": 0}}
                }
            },
            {
                "name": "old-inactive-repo",
                "path": "/path/to/old-inactive-repo",
                "last_commit_timestamp": "2022-03-15T14:20:00Z",
                "days_since_last_commit": 1112,  # ~3 years
                "is_active": False,
                "commit_counts": {
                    "last_30_days": 0,
                    "last_90_days": 0,
                    "last_365_days": 0,
                    "last_3_years": 45
                },
                "loc_stats": {
                    "last_30_days": {"added": 0, "removed": 0, "net": 0},
                    "last_90_days": {"added": 0, "removed": 0, "net": 0},
                    "last_365_days": {"added": 0, "removed": 0, "net": 0},
                    "last_3_years": {"added": 2500, "removed": 500, "net": 2000}
                },
                "unique_contributors": {
                    "last_30_days": 0,
                    "last_90_days": 0,
                    "last_365_days": 0,
                    "last_3_years": 3
                },
                "features": {
                    "dependabot": {"present": False},
                    "pre_commit": {"present": True, "config_file": ".pre-commit-config.yml", "repos_count": 2},
                    "readthedocs": {"present": False},
                    "project_types": {"detected_types": ["c++"], "primary_type": "c++"},
                    "workflows": {"count": 0, "classified": {"verify": 0, "merge": 0, "other": 0}}
                }
            }
        ],
        "authors": [
            {
                "name": "Alice Developer",
                "email": "alice@bigcorp.com",
                "username": "alice",
                "domain": "bigcorp.com",
                "commits": {
                    "last_30_days": 85,
                    "last_90_days": 255,
                    "last_365_days": 1200,
                    "last_3_years": 3600
                },
                "lines_added": {
                    "last_30_days": 3500,
                    "last_90_days": 10500,
                    "last_365_days": 45000,
                    "last_3_years": 135000
                },
                "lines_removed": {
                    "last_30_days": 700,
                    "last_90_days": 2100,
                    "last_365_days": 9000,
                    "last_3_years": 27000
                },
                "lines_net": {
                    "last_30_days": 2800,
                    "last_90_days": 8400,
                    "last_365_days": 36000,
                    "last_3_years": 108000
                },
                "repositories_count": {
                    "last_30_days": 2,
                    "last_90_days": 2,
                    "last_365_days": 3,
                    "last_3_years": 5
                }
            },
            {
                "name": "Bob Contributor",
                "email": "bob@startup.io",
                "username": "bob",
                "domain": "startup.io",
                "commits": {
                    "last_30_days": 65,
                    "last_90_days": 195,
                    "last_365_days": 800,
                    "last_3_years": 2400
                },
                "lines_added": {
                    "last_30_days": 1300,
                    "last_90_days": 3900,
                    "last_365_days": 20000,
                    "last_3_years": 60000
                },
                "lines_removed": {
                    "last_30_days": 300,
                    "last_90_days": 900,
                    "last_365_days": 5000,
                    "last_3_years": 15000
                },
                "lines_net": {
                    "last_30_days": 1000,
                    "last_90_days": 3000,
                    "last_365_days": 15000,
                    "last_3_years": 45000
                },
                "repositories_count": {
                    "last_30_days": 1,
                    "last_90_days": 2,
                    "last_365_days": 2,
                    "last_3_years": 3
                }
            },
            {
                "name": "Charlie Legacy",
                "email": "charlie@oldcorp.net",
                "username": "charlie",
                "domain": "oldcorp.net",
                "commits": {
                    "last_30_days": 0,
                    "last_90_days": 0,
                    "last_365_days": 120,
                    "last_3_years": 380
                },
                "lines_added": {
                    "last_30_days": 0,
                    "last_90_days": 0,
                    "last_365_days": 7000,
                    "last_3_years": 22000
                },
                "lines_removed": {
                    "last_30_days": 0,
                    "last_90_days": 0,
                    "last_365_days": 2000,
                    "last_3_years": 6000
                },
                "lines_net": {
                    "last_30_days": 0,
                    "last_90_days": 0,
                    "last_365_days": 5000,
                    "last_3_years": 16000
                },
                "repositories_count": {
                    "last_30_days": 0,
                    "last_90_days": 0,
                    "last_365_days": 2,
                    "last_3_years": 3
                }
            }
        ],
        "organizations": [
            {
                "domain": "bigcorp.com",
                "contributor_count": 1,
                "commits": {
                    "last_30_days": 85,
                    "last_90_days": 255,
                    "last_365_days": 1200,
                    "last_3_years": 3600
                },
                "lines_added": {
                    "last_30_days": 3500,
                    "last_90_days": 10500,
                    "last_365_days": 45000,
                    "last_3_years": 135000
                },
                "lines_removed": {
                    "last_30_days": 700,
                    "last_90_days": 2100,
                    "last_365_days": 9000,
                    "last_3_years": 27000
                },
                "lines_net": {
                    "last_30_days": 2800,
                    "last_90_days": 8400,
                    "last_365_days": 36000,
                    "last_3_years": 108000
                },
                "repositories_count": {
                    "last_30_days": 2,
                    "last_90_days": 2,
                    "last_365_days": 3,
                    "last_3_years": 5
                }
            },
            {
                "domain": "startup.io",
                "contributor_count": 1,
                "commits": {
                    "last_30_days": 65,
                    "last_90_days": 195,
                    "last_365_days": 800,
                    "last_3_years": 2400
                },
                "lines_net": {
                    "last_30_days": 1000,
                    "last_90_days": 3000,
                    "last_365_days": 15000,
                    "last_3_years": 45000
                },
                "repositories_count": {
                    "last_30_days": 1,
                    "last_90_days": 2,
                    "last_365_days": 2,
                    "last_3_years": 3
                }
            }
        ],
        "summaries": {
            "counts": {
                "total_repositories": 3,
                "active_repositories": 2,
                "inactive_repositories": 1,
                "total_commits": 2000,
                "total_authors": 3,
                "total_organizations": 2
            },
            "activity_distribution": {
                "very_old": [],
                "old": [{"name": "old-inactive-repo", "days_since_last_commit": 1112}],
                "recent_inactive": []
            },
            "top_active_repositories": [
                {
                    "name": "active-high-volume",
                    "commit_counts": {"last_365_days": 1800},
                    "loc_stats": {"last_365_days": {"net": 48000}},
                    "unique_contributors": {"last_365_days": 25},
                    "days_since_last_commit": 0,
                    "is_active": True
                },
                {
                    "name": "moderate-activity",
                    "commit_counts": {"last_365_days": 320},
                    "loc_stats": {"last_365_days": {"net": 8000}},
                    "unique_contributors": {"last_365_days": 8},
                    "days_since_last_commit": 33,
                    "is_active": True
                }
            ],
            "least_active_repositories": [
                {
                    "name": "old-inactive-repo",
                    "commit_counts": {"last_365_days": 0},
                    "days_since_last_commit": 1112
                }
            ],
            "top_contributors_commits": [
                {
                    "name": "Alice Developer",
                    "email": "alice@bigcorp.com",
                    "domain": "bigcorp.com",
                    "commits": {"last_365_days": 1200},
                    "lines_net": {"last_365_days": 36000},
                    "repositories_count": {"last_365_days": 3}
                },
                {
                    "name": "Bob Contributor",
                    "email": "bob@startup.io",
                    "domain": "startup.io",
                    "commits": {"last_365_days": 800},
                    "lines_net": {"last_365_days": 15000},
                    "repositories_count": {"last_365_days": 2}
                }
            ],
            "top_contributors_loc": [
                {
                    "name": "Alice Developer",
                    "email": "alice@bigcorp.com",
                    "domain": "bigcorp.com",
                    "commits": {"last_365_days": 1200},
                    "lines_net": {"last_365_days": 36000},
                    "repositories_count": {"last_365_days": 3}
                },
                {
                    "name": "Bob Contributor",
                    "email": "bob@startup.io",
                    "domain": "startup.io",
                    "commits": {"last_365_days": 800},
                    "lines_net": {"last_365_days": 15000},
                    "repositories_count": {"last_365_days": 2}
                }
            ],
            "top_organizations": [
                {
                    "domain": "bigcorp.com",
                    "contributor_count": 1,
                    "commits": {"last_365_days": 1200},
                    "lines_net": {"last_365_days": 36000},
                    "repositories_count": {"last_365_days": 3}
                }
            ]
        },
        "errors": []
    }

def test_markdown_generation(renderer: ReportRenderer, test_data: Dict[str, Any]) -> bool:
    """Test Markdown report generation."""
    print("Testing Markdown report generation...")

    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as tmp_file:
        temp_path = Path(tmp_file.name)

    try:
        # Generate markdown report
        markdown_content = renderer.render_markdown_report(test_data, temp_path)

        # Verify file was created and has content
        if not temp_path.exists():
            print("❌ Markdown file was not created")
            return False

        content = temp_path.read_text(encoding='utf-8')
        if len(content) < 100:
            print("❌ Markdown content is too short")
            return False

        # Check for essential sections
        required_sections = [
            "# 📊 Repository Analysis Report:",
            "## 📈 Global Summary",
            "## 🏆 Top Active Repositories",
            "## 👥 Top Contributors",
            "## 🏢 Top Organizations",
            "## 🔧 Repository Feature Matrix"
        ]

        for section in required_sections:
            if section not in content:
                print(f"❌ Missing required section: {section}")
                return False

        # Check for proper table formatting
        if "| Rank |" not in content:
            print("❌ Missing properly formatted tables")
            return False

        # Check for emoji indicators
        if not any(emoji in content for emoji in ["✅", "❌", "⚠️"]):
            print("❌ Missing emoji indicators")
            return False

        # Check for number formatting
        if "1.8K" not in content and "1800" not in content:
            print("❌ Number formatting issue")
            return False

        print("✅ Markdown generation working correctly")
        return True

    finally:
        if temp_path.exists():
            temp_path.unlink()

def test_html_generation(renderer: ReportRenderer, test_data: Dict[str, Any]) -> bool:
    """Test HTML report generation."""
    print("Testing HTML report generation...")

    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as md_file:
        md_path = Path(md_file.name)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as html_file:
        html_path = Path(html_file.name)

    try:
        # First generate markdown
        markdown_content = renderer.render_markdown_report(test_data, md_path)

        # Then convert to HTML
        renderer.render_html_report(markdown_content, html_path)

        # Verify HTML file was created
        if not html_path.exists():
            print("❌ HTML file was not created")
            return False

        html_content = html_path.read_text(encoding='utf-8')
        if len(html_content) < 200:
            print("❌ HTML content is too short")
            return False

        # Check for proper HTML structure
        required_html_elements = [
            "<!DOCTYPE html>",
            "<html lang=\"en\">",
            "<head>",
            "<meta charset=\"UTF-8\">",
            "<title>Repository Analysis Report</title>",
            "<style>",
            "<body>",
            "<table>",
            "<th>",
            "<td>"
        ]

        for element in required_html_elements:
            if element not in html_content:
                print(f"❌ Missing required HTML element: {element}")
                return False

        # Check for CSS styling
        css_properties = [
            "font-family:",
            "border-collapse:",
            "background-color:",
            "padding:"
        ]

        for prop in css_properties:
            if prop not in html_content:
                print(f"❌ Missing CSS property: {prop}")
                return False

        # Check that tables are properly converted
        if "<table>" not in html_content or "<tr>" not in html_content:
            print("❌ Tables not properly converted to HTML")
            return False

        print("✅ HTML generation working correctly")
        return True

    finally:
        for path in [md_path, html_path]:
            if path.exists():
                path.unlink()

def test_number_formatting(renderer: ReportRenderer) -> bool:
    """Test number formatting functionality."""
    print("Testing number formatting...")

    test_cases = [
        (0, "0"),
        (42, "42"),
        (999, "999"),
        (1000, "1.0K"),
        (1234, "1.2K"),
        (12345, "12.3K"),
        (1000000, "1.0M"),
        (1234567, "1.2M"),
        (1000000000, "1.0B"),
        (-1500, "-1.5K"),
        (2500, "+2.5K")  # Test signed formatting
    ]

    for number, expected in test_cases[:-1]:  # All but the signed one
        result = renderer._format_number(number)
        if result != expected:
            print(f"❌ Number formatting failed: {number} -> expected '{expected}', got '{result}'")
            return False

    # Test signed formatting
    signed_result = renderer._format_number(2500, signed=True)
    if signed_result != "+2.5K":
        print(f"❌ Signed number formatting failed: expected '+2.5K', got '{signed_result}'")
        return False

    print("✅ Number formatting working correctly")
    return True

def test_age_formatting(renderer: ReportRenderer) -> bool:
    """Test age formatting functionality."""
    print("Testing age formatting...")

    test_cases = [
        (0, "Today"),
        (1, "1 day ago"),
        (5, "5 days ago"),
        (7, "1 week ago"),
        (14, "2 weeks ago"),
        (30, "1 month ago"),
        (60, "2 months ago"),
        (365, "1 year ago"),
        (730, "2 years ago"),
        (1100, "3 years ago")
    ]

    for days, expected in test_cases:
        result = renderer._format_age(days)
        if result != expected:
            print(f"❌ Age formatting failed: {days} days -> expected '{expected}', got '{result}'")
            return False

    print("✅ Age formatting working correctly")
    return True

def test_zip_packaging(config: Dict[str, Any]) -> bool:
    """Test ZIP packaging functionality."""
    print("Testing ZIP packaging...")

    with tempfile.TemporaryDirectory() as temp_dir:
        output_dir = Path(temp_dir) / "test_output"
        output_dir.mkdir(parents=True, exist_ok=True)

        # Create some test files
        test_files = {
            "report_raw.json": '{"test": "data"}',
            "report.md": "# Test Report\n\nThis is a test.",
            "report.html": "<html><body><h1>Test Report</h1></body></html>",
            "config_resolved.json": '{"config": "resolved"}'
        }

        for filename, content in test_files.items():
            (output_dir / filename).write_text(content, encoding='utf-8')

        # Create ZIP bundle
        logger = setup_logging("ERROR")  # Reduce noise
        try:
            zip_path = create_report_bundle(output_dir, "test-project", logger)

            # Verify ZIP was created
            if not zip_path.exists():
                print("❌ ZIP file was not created")
                return False

            # Verify ZIP contents
            with zipfile.ZipFile(zip_path, 'r') as zipf:
                zip_contents = zipf.namelist()

                expected_files = [
                    "reports/test-project/report_raw.json",
                    "reports/test-project/report.md",
                    "reports/test-project/report.html",
                    "reports/test-project/config_resolved.json"
                ]

                for expected_file in expected_files:
                    if expected_file not in zip_contents:
                        print(f"❌ Missing file in ZIP: {expected_file}")
                        return False

                # Test reading a file from ZIP
                with zipf.open("reports/test-project/report.md") as f:
                    md_content = f.read().decode('utf-8')
                    if "Test Report" not in md_content:
                        print("❌ ZIP file content is incorrect")
                        return False

            print("✅ ZIP packaging working correctly")
            return True

        except Exception as e:
            print(f"❌ ZIP packaging failed: {e}")
            return False

def test_report_structure(renderer: ReportRenderer, test_data: Dict[str, Any]) -> bool:
    """Test overall report structure and completeness."""
    print("Testing report structure and completeness...")

    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as tmp_file:
        temp_path = Path(tmp_file.name)

    try:
        markdown_content = renderer.render_markdown_report(test_data, temp_path)
        content = temp_path.read_text(encoding='utf-8')

        # Test that essential report structure exists (use generic checks)
        data_checks = [
            # Basic structure
            ("Repository Analysis Report:", "Report title should appear"),
            ("Generated:", "Generation timestamp should appear"),
            ("Global Summary", "Global summary section should appear"),
            ("Top Active Repositories", "Top repositories section should appear"),
            ("Top Contributors", "Contributors section should appear"),

            # Table formatting
            ("| Rank |", "Ranked tables should be present"),
            ("|---", "Table separators should be present"),

            # Status indicators (at least one should appear)
            ("✅", "Active status indicators should appear"),

            # Feature matrix elements
            ("Repository Feature Matrix", "Feature matrix section should appear"),
            ("Type |", "Feature matrix table headers should appear"),

            # Metadata
            ("Schema Version:", "Schema version should appear"),
            ("Script Version:", "Script version should appear"),
            ("Time Windows:", "Time windows should be documented"),
            ("Generated with ❤️", "Footer should appear")
        ]

        for expected_text, description in data_checks:
            if expected_text not in content:
                print(f"❌ {description}: missing '{expected_text}'")
                return False

        # Test basic table structure exists
        pipe_tables = content.count("|")
        if pipe_tables < 10:  # Should have several tables with multiple columns
            print(f"❌ Insufficient table formatting: expected many pipes, got {pipe_tables}")
            return False

        # Test basic section presence (more flexible)
        required_sections = ["Global Summary", "Repository", "Contributors"]
        for section in required_sections:
            if section not in content:
                print(f"❌ Missing essential section content: {section}")
                return False

        print("✅ Report structure and completeness working correctly")
        return True

    finally:
        if temp_path.exists():
            temp_path.unlink()

def test_configuration_integration(renderer: ReportRenderer, test_data: Dict[str, Any]) -> bool:
    """Test configuration-driven output customization."""
    print("Testing configuration integration...")

    # Test with contributors section disabled
    restricted_config = create_test_config()
    restricted_config["output"]["include_sections"]["contributors"] = False

    restricted_renderer = ReportRenderer(restricted_config, setup_logging("ERROR"))

    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as tmp_file:
        temp_path = Path(tmp_file.name)

    try:
        markdown_content = restricted_renderer.render_markdown_report(test_data, temp_path)
        content = temp_path.read_text(encoding='utf-8')

        # Should not have contributors section
        if "## 👥 Top Contributors" in content:
            print("❌ Contributors section should be disabled but appears in output")
            return False

        # Should still have other sections
        if "## 📈 Global Summary" not in content:
            print("❌ Global summary should still appear when contributors disabled")
            return False

        print("✅ Configuration integration working correctly")
        return True

    finally:
        if temp_path.exists():
            temp_path.unlink()

def run_all_tests() -> bool:
    """Run all Phase 5 tests."""
    print("🧪 Running Phase 5 Tests for Repository Reporting System")
    print("   Testing: Output Generation (Markdown, HTML, ZIP)")
    print("-" * 60)

    # Setup
    logger = setup_logging("ERROR")  # Reduce noise during testing
    config = create_test_config()
    renderer = ReportRenderer(config, logger)
    test_data = create_test_report_data()

    # Run tests
    tests = [
        ("Markdown generation", lambda: test_markdown_generation(renderer, test_data)),
        ("HTML generation", lambda: test_html_generation(renderer, test_data)),
        ("number formatting", lambda: test_number_formatting(renderer)),
        ("age formatting", lambda: test_age_formatting(renderer)),
        ("ZIP packaging", lambda: test_zip_packaging(config)),
        ("report structure", lambda: test_report_structure(renderer, test_data)),
        ("configuration integration", lambda: test_configuration_integration(renderer, test_data)),
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
        print("🎉 All Phase 5 tests passed! Output generation is working!")
        return True
    else:
        print("❌ Some Phase 5 tests failed. Check implementation.")
        return False

if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
