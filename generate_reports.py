#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 The Linux Foundation

"""
Repository Reporting System - Comprehensive Multi-Repository Analysis Tool

This script analyzes multiple repositories to generate comprehensive reports including:
- Git activity metrics (commits, lines of code, contributor activity)
- Repository feature detection (CI/CD, documentation, dependency management)
- Contributor and organization analysis
- Outputs in JSON, Markdown, and HTML formats

Architecture:
- Single script with modular internal structure
- Configuration-driven with template + project overrides
- JSON as canonical data source, Markdown/HTML as views
- Extensible feature detection registry
- Deterministic sorting and error handling
- Performance-conscious with optional concurrency

Schema Version: 1.0.0
"""

import argparse
import atexit
import concurrent.futures
import copy
import datetime
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union, cast
from urllib.parse import urljoin, urlparse

try:
    import yaml  # type: ignore
except ImportError:
    print(
        "ERROR: PyYAML is required. Install with: pip install PyYAML", file=sys.stderr
    )
    sys.exit(1)

try:
    import httpx  # type: ignore
except ImportError:
    print("ERROR: httpx is required. Install with: pip install httpx", file=sys.stderr)
    sys.exit(1)

# =============================================================================
# CONSTANTS AND SCHEMA DEFINITIONS
# =============================================================================

SCRIPT_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0.0"
DEFAULT_CONFIG_DIR = "configuration"
DEFAULT_OUTPUT_DIR = "reports"

# JSON Schema structure (conceptual - used for validation and documentation)
EXPECTED_JSON_SCHEMA = {
    "schema_version": str,
    "generated_at": str,  # UTC ISO8601
    "project": str,
    "config_digest": str,  # SHA256 of resolved config
    "script_version": str,
    "time_windows": dict,  # {window_name: {days: int, start: str, end: str}}
    "repositories": list,  # [repo_metrics_dict, ...]
    "authors": list,  # [author_metrics_dict, ...]
    "organizations": list,  # [org_metrics_dict, ...]
    "summaries": dict,  # Global summary statistics
    "errors": list,  # [{"repo": str, "error": str, "category": str}, ...]
}

# Default time windows (can be overridden in config)
DEFAULT_TIME_WINDOWS = {
    "last_30_days": 30,
    "last_90_days": 90,
    "last_365_days": 365,
    "last_3_years": 1095,
}

# =============================================================================
# API STATISTICS TRACKING
# =============================================================================


class APIStatistics:
    """Track statistics for external API calls (GitHub, Gerrit, Jenkins)."""

    def __init__(self):
        """Initialize statistics tracker."""
        self.stats = {
            "github": {"success": 0, "errors": {}},
            "gerrit": {"success": 0, "errors": {}},
            "jenkins": {"success": 0, "errors": {}},
            "info_master": {"success": False, "error": None},
        }

    def record_success(self, api_type: str) -> None:
        """Record a successful API call."""
        if api_type in self.stats:
            self.stats[api_type]["success"] += 1

    def record_error(self, api_type: str, status_code: int) -> None:
        """Record an API error by status code."""
        if api_type in self.stats:
            errors = self.stats[api_type]["errors"]
            errors[status_code] = errors.get(status_code, 0) + 1

    def record_exception(self, api_type: str, error_type: str = "exception") -> None:
        """Record an API exception (non-HTTP error)."""
        if api_type in self.stats:
            errors = self.stats[api_type]["errors"]
            errors[error_type] = errors.get(error_type, 0) + 1

    def record_info_master(self, success: bool, error: Optional[str] = None) -> None:
        """Record info-master clone status."""
        self.stats["info_master"]["success"] = success
        if error:
            self.stats["info_master"]["error"] = error

    def get_total_calls(self, api_type: str) -> int:
        """Get total number of API calls (success + errors)."""
        if api_type not in self.stats:
            return 0
        success = self.stats[api_type]["success"]
        errors = sum(self.stats[api_type]["errors"].values())
        return success + errors

    def get_total_errors(self, api_type: str) -> int:
        """Get total number of errors for an API."""
        if api_type not in self.stats:
            return 0
        return sum(self.stats[api_type]["errors"].values())

    def has_errors(self) -> bool:
        """Check if any API has errors."""
        for api_type in ["github", "gerrit", "jenkins"]:
            if self.get_total_errors(api_type) > 0:
                return True
        if not self.stats["info_master"]["success"] and self.stats["info_master"]["error"]:
            return True
        return False

    def format_console_output(self) -> str:
        """Format statistics for console output."""
        lines = []

        # GitHub API stats
        if self.get_total_calls("github") > 0:
            lines.append("\n📊 GitHub API Statistics:")
            lines.append(f"   ✅ Successful calls: {self.stats['github']['success']}")
            total_errors = self.get_total_errors("github")
            if total_errors > 0:
                lines.append(f"   ❌ Failed calls: {total_errors}")
                for code, count in sorted(self.stats["github"]["errors"].items(), key=lambda x: str(x[0])):
                    lines.append(f"      • Error {code}: {count}")

        # Gerrit API stats
        if self.get_total_calls("gerrit") > 0:
            lines.append("\n📊 Gerrit API Statistics:")
            lines.append(f"   ✅ Successful calls: {self.stats['gerrit']['success']}")
            total_errors = self.get_total_errors("gerrit")
            if total_errors > 0:
                lines.append(f"   ❌ Failed calls: {total_errors}")
                for code, count in sorted(self.stats["gerrit"]["errors"].items(), key=lambda x: str(x[0])):
                    lines.append(f"      • Error {code}: {count}")

        # Jenkins API stats
        if self.get_total_calls("jenkins") > 0:
            lines.append("\n📊 Jenkins API Statistics:")
            lines.append(f"   ✅ Successful calls: {self.stats['jenkins']['success']}")
            total_errors = self.get_total_errors("jenkins")
            if total_errors > 0:
                lines.append(f"   ❌ Failed calls: {total_errors}")
                for code, count in sorted(self.stats["jenkins"]["errors"].items(), key=lambda x: str(x[0])):
                    lines.append(f"      • Error {code}: {count}")

        # Info-master clone status
        if self.stats["info_master"]["success"]:
            lines.append("\n📊 Info-Master Clone:")
            lines.append("   ✅ Successfully cloned")
        elif self.stats["info_master"]["error"]:
            lines.append("\n📊 Info-Master Clone:")
            lines.append(f"   ❌ Failed: {self.stats['info_master']['error']}")

        return "\n".join(lines) if lines else ""

    def write_to_step_summary(self) -> None:
        """Write statistics to GitHub Step Summary."""
        step_summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
        if not step_summary_file:
            return

        try:
            with open(step_summary_file, "a") as f:
                f.write("\n## 📊 External API Statistics\n\n")

                # GitHub API
                if self.get_total_calls("github") > 0:
                    f.write("### GitHub API\n\n")
                    f.write(f"- ✅ Successful calls: {self.stats['github']['success']}\n")
                    total_errors = self.get_total_errors("github")
                    if total_errors > 0:
                        f.write(f"- ❌ Failed calls: {total_errors}\n")
                        f.write("\n**Error Breakdown:**\n\n")
                        for code, count in sorted(self.stats["github"]["errors"].items(), key=lambda x: str(x[0])):
                            f.write(f"- `{code}`: {count} call(s)\n")
                    f.write("\n")

                # Gerrit API
                if self.get_total_calls("gerrit") > 0:
                    f.write("### Gerrit API\n\n")
                    f.write(f"- ✅ Successful calls: {self.stats['gerrit']['success']}\n")
                    total_errors = self.get_total_errors("gerrit")
                    if total_errors > 0:
                        f.write(f"- ❌ Failed calls: {total_errors}\n")
                        f.write("\n**Error Breakdown:**\n\n")
                        for code, count in sorted(self.stats["gerrit"]["errors"].items(), key=lambda x: str(x[0])):
                            f.write(f"- `{code}`: {count} call(s)\n")
                    f.write("\n")

                # Jenkins API
                if self.get_total_calls("jenkins") > 0:
                    f.write("### Jenkins API\n\n")
                    f.write(f"- ✅ Successful calls: {self.stats['jenkins']['success']}\n")
                    total_errors = self.get_total_errors("jenkins")
                    if total_errors > 0:
                        f.write(f"- ❌ Failed calls: {total_errors}\n")
                        f.write("\n**Error Breakdown:**\n\n")
                        for code, count in sorted(self.stats["jenkins"]["errors"].items(), key=lambda x: str(x[0])):
                            f.write(f"- `{code}`: {count} call(s)\n")
                    f.write("\n")

                # Info-master
                if self.stats["info_master"]["success"]:
                    f.write("### Info-Master Repository\n\n")
                    f.write("- ✅ Successfully cloned from gerrit.linuxfoundation.org\n\n")
                elif self.stats["info_master"]["error"]:
                    f.write("### Info-Master Repository\n\n")
                    f.write(f"- ❌ Clone failed: {self.stats['info_master']['error']}\n\n")

        except Exception as e:
            logging.debug(f"Could not write API statistics to GITHUB_STEP_SUMMARY: {e}")


# Global statistics tracker
api_stats = APIStatistics()


# =============================================================================
# LOGGING SETUP
# =============================================================================


def setup_logging(
    level: str = "INFO", include_timestamps: bool = True
) -> logging.Logger:
    """Configure logging with structured format."""
    log_format = "[%(levelname)s]"
    if include_timestamps:
        log_format = "[%(asctime)s] " + log_format
    log_format += " %(message)s"

    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format=log_format,
        datefmt="%Y-%m-%d %H:%M:%S UTC" if include_timestamps else None,
    )

    logger = logging.getLogger("repo_reporter")
    return logger


# =============================================================================
# CONFIGURATION LOADING AND DEEP MERGE
# =============================================================================


def deep_merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge two dictionaries, with override taking precedence."""
    result = copy.deepcopy(base)

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge_dicts(result[key], value)
        else:
            result[key] = copy.deepcopy(value)

    return result


def load_yaml_config(config_path: Path) -> Dict[str, Any]:
    """Load and parse a YAML configuration file."""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in {config_path}: {e}")


def load_configuration(config_dir: Path, project: str) -> dict[str, Any]:
    """
    Load configuration with template + project override merge strategy.

    Args:
        config_dir: Directory containing configuration files
        project: Project name for override file

    Returns:
        Merged configuration dictionary
    """
    import sys

    template_path = config_dir / "template.config"

    # Try to find project config file case-insensitively
    project_path = None
    project_config_name = f"{project}.config"

    # First try exact match
    exact_match = config_dir / project_config_name
    if exact_match.exists():
        project_path = exact_match
        print(
            f"📝 Loading project config (exact match): {project_path}", file=sys.stderr
        )
    else:
        # Try case-insensitive search
        for config_file in config_dir.glob("*.config"):
            if config_file.name.lower() == project_config_name.lower():
                project_path = config_file
                print(
                    f"📝 Loading project config (case-insensitive match): {project_path}",
                    file=sys.stderr,
                )
                print(
                    f"   Note: Project name '{project}' matched config file '{config_file.name}'",
                    file=sys.stderr,
                )
                break

        if not project_path:
            print(
                f"⚠️  No project-specific config found for '{project}' - using template defaults only",
                file=sys.stderr,
            )
            print(
                f"   Searched for: {project_config_name} (case-insensitive)",
                file=sys.stderr,
            )

    # Load template (required)
    if not template_path.exists():
        raise FileNotFoundError(f"Template configuration not found: {template_path}")

    print(f"📝 Loading template config: {template_path}", file=sys.stderr)
    template_config = load_yaml_config(template_path)

    # Load project override (optional)
    project_config = {}
    if project_path:
        project_config = load_yaml_config(project_path)
        # Show which settings were overridden
        if "activity_thresholds" in project_config:
            current = project_config.get("activity_thresholds", {}).get("current_days")
            active = project_config.get("activity_thresholds", {}).get("active_days")
            if current is not None or active is not None:
                print(
                    f"✅ Using custom activity thresholds: current={current}, active={active}",
                    file=sys.stderr,
                )

    # Deep merge
    merged_config = deep_merge_dicts(template_config, project_config)

    # Set project name
    merged_config["project"] = project

    # Log final merged activity thresholds for debugging
    final_current = merged_config.get("activity_thresholds", {}).get("current_days")
    final_active = merged_config.get("activity_thresholds", {}).get("active_days")
    print(
        f"📊 Final merged activity thresholds: current={final_current} days, active={final_active} days",
        file=sys.stderr,
    )

    return merged_config


def compute_config_digest(config: Dict[str, Any]) -> str:
    """Compute SHA256 digest of configuration for reproducibility tracking."""
    config_json = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(config_json.encode("utf-8")).hexdigest()


# =============================================================================
# TIME WINDOW COMPUTATION
# =============================================================================


def setup_time_windows(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """
    Compute time window boundaries based on configuration.

    Returns:
        Dictionary with window definitions including start/end timestamps
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    windows = {}

    time_window_config = config.get("time_windows", DEFAULT_TIME_WINDOWS)

    for window_name, days in time_window_config.items():
        start_date = now - datetime.timedelta(days=days)
        windows[window_name] = {
            "days": days,
            "start": start_date.isoformat(),
            "end": now.isoformat(),
            "start_timestamp": start_date.timestamp(),
            "end_timestamp": now.timestamp(),
        }

    return windows


# =============================================================================
# GERRIT AND JENKINS API INTEGRATION
# =============================================================================


class GerritAPIError(Exception):
    """Base exception for Gerrit API errors."""

    pass


class GerritConnectionError(Exception):
    """Raised when connection to Gerrit server fails."""

    pass


class GerritAPIDiscovery:
    """Discovers the correct Gerrit API base URL for a given host."""

    # Common Gerrit API path patterns to test
    COMMON_PATHS = [
        "",  # Direct: https://host/
        "/r",  # Standard: https://host/r/
        "/gerrit",  # OpenDaylight style: https://host/gerrit/
        "/infra",  # Linux Foundation style: https://host/infra/
        "/a",  # Authenticated API: https://host/a/
    ]

    def __init__(self, timeout: float = 30.0):
        """Initialize discovery client."""
        self.timeout = timeout
        self.client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=True,
            headers={
                "User-Agent": "repository-reports/1.0.0",
                "Accept": "application/json",
            },
        )

    def __enter__(self):
        """Enter context manager."""
        return self

    def __exit__(self, *args):
        """Exit context manager and cleanup."""
        self.close()

    def close(self):
        """Close HTTP client."""
        if hasattr(self, "client"):
            self.client.close()

    def discover_base_url(self, host: str) -> str:
        """Discover the correct API base URL for a Gerrit host."""
        logging.debug(f"Starting API discovery for host: {host}")

        # First, try to follow redirects from the base URL
        redirect_path = self._discover_via_redirect(host)
        if redirect_path:
            test_paths = [redirect_path] + [
                p for p in self.COMMON_PATHS if p != redirect_path
            ]
        else:
            test_paths = self.COMMON_PATHS

        # Test each potential path
        for path in test_paths:
            base_url = f"https://{host}{path}"
            logging.debug(f"Testing API endpoint: {base_url}")

            if self._test_projects_api(base_url):
                logging.debug(f"Discovered working API base URL: {base_url}")
                return base_url

        # If all paths fail, raise an error
        raise GerritAPIError(
            f"Could not discover Gerrit API endpoint for {host}. "
            f"Tested paths: {test_paths}"
        )

    def _discover_via_redirect(self, host: str) -> Optional[str]:
        """Attempt to discover the API path by following redirects."""
        try:
            response = self.client.get(f"https://{host}", follow_redirects=False)
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if location:
                    parsed = urlparse(location)
                    if parsed.netloc == host or not parsed.netloc:
                        path = parsed.path.rstrip("/")
                        if path and path != "/":
                            return path
        except Exception as e:
            logging.debug(f"Error checking redirects for {host}: {e}")
        return None

    def _test_projects_api(self, base_url: str) -> bool:
        """Test if the projects API is available at the given base URL."""
        try:
            projects_url = urljoin(base_url.rstrip("/") + "/", "projects/?d")
            response = self.client.get(projects_url)

            if response.status_code == 200:
                return self._validate_projects_response(response.text)
            return False
        except Exception as e:
            logging.debug(f"Error testing projects API at {base_url}: {e}")
            return False

    def _validate_projects_response(self, response_text: str) -> bool:
        """Validate that the response looks like a valid Gerrit projects API response."""
        try:
            # Strip Gerrit's security prefix
            if response_text.startswith(")]}'"):
                json_text = response_text[4:]
            else:
                json_text = response_text

            data = json.loads(json_text)
            return isinstance(data, dict)
        except Exception:
            return False


class GerritAPIClient:
    """Client for interacting with Gerrit REST API."""

    def __init__(
        self, host: str, base_url: Optional[str] = None, timeout: float = 30.0, stats: Optional[APIStatistics] = None
    ):
        """Initialize Gerrit API client."""
        self.host = host
        self.timeout = timeout
        self.stats = stats or api_stats

        if base_url:
            self.base_url = base_url
        else:
            # Auto-discover the base URL
            with GerritAPIDiscovery(timeout) as discovery:
                self.base_url = discovery.discover_base_url(host)

        self.client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=True,
            headers={
                "User-Agent": "repository-reports/1.0.0",
                "Accept": "application/json",
            },
        )

    def __enter__(self):
        """Enter context manager."""
        return self

    def __exit__(self, *args):
        """Exit context manager and cleanup."""
        self.close()

    def close(self):
        """Close HTTP client."""
        if hasattr(self, "client"):
            self.client.close()

    def get_project_info(self, project_name: str) -> Optional[dict[str, Any]]:
        """Get detailed information about a specific project."""
        try:
            # URL-encode the project name and use the projects API with detailed information
            encoded_name = project_name.replace("/", "%2F")
            url = f"/projects/{encoded_name}?d"

            response = self.client.get(url)

            if response.status_code == 200:
                self.stats.record_success("gerrit")
                result = self._parse_json_response(response.text)
                return result
            elif response.status_code == 404:
                self.stats.record_error("gerrit", 404)
                logging.debug(f"Project not found in Gerrit: {project_name}")
                return None
            else:
                self.stats.record_error("gerrit", response.status_code)
                logging.warning(
                    f"❌ Error: Gerrit API query returned error code: {response.status_code} for project {project_name}"
                )
                return None

        except Exception as e:
            self.stats.record_exception("gerrit")
            logging.error(f"❌ Error: Gerrit API query exception for {project_name}: {e}")
            return None

    def _parse_json_response(self, response_text: str) -> dict[str, Any]:
        """Parse Gerrit JSON response, handling magic prefix."""
        # Remove Gerrit's magic prefix if present
        if response_text.startswith(")]}'"):
            clean_text = response_text[4:].lstrip()
        else:
            clean_text = response_text

        try:
            result = json.loads(clean_text)
            return result if isinstance(result, dict) else {}
        except json.JSONDecodeError as e:
            logging.error(f"Invalid JSON response: {e}")
            return {}

    def get_all_projects(self) -> dict[str, Any]:
        """Get all projects with detailed information."""
        try:
            response = self.client.get("/projects/?d")

            if response.status_code == 200:
                self.stats.record_success("gerrit")
                result = self._parse_json_response(response.text)
                logging.info(f"Fetched {len(result)} projects from Gerrit")
                return result if isinstance(result, dict) else {}
            else:
                self.stats.record_error("gerrit", response.status_code)
                logging.error(
                    f"❌ Error: Gerrit API query returned error code: {response.status_code}"
                )
                return {}

        except Exception as e:
            self.stats.record_exception("gerrit")
            logging.error(f"❌ Error: Gerrit API query exception: {e}")
            return {}


class JenkinsAPIClient:
    """Client for interacting with Jenkins REST API."""

    def __init__(self, host: str, timeout: float = 30.0, stats: Optional[APIStatistics] = None):
        """Initialize Jenkins API client."""
        self.host = host
        self.timeout = timeout
        self.base_url = f"https://{host}"
        self.api_base_path = None  # Will be discovered
        self._jobs_cache: dict[str, Any] = {}  # Cache for all jobs data
        self._cache_populated = False
        self.stats = stats or api_stats

        import httpx

        self.client = httpx.Client(timeout=timeout)

        # Discover the correct API base path
        self._discover_api_base_path()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        """Close the HTTP client."""
        if hasattr(self, "client"):
            self.client.close()

    def _discover_api_base_path(self):
        """Discover the correct API base path for this Jenkins server."""
        # Common Jenkins API path patterns to try
        api_patterns = [
            "/api/json",
            "/releng/api/json",
            "/jenkins/api/json",
            "/ci/api/json",
            "/build/api/json",
        ]

        logging.info(f"Discovering Jenkins API base path for {self.host}")

        for pattern in api_patterns:
            try:
                test_url = f"{self.base_url}{pattern}?tree=jobs[name]"
                logging.debug(f"Testing Jenkins API path: {test_url}")

                response = self.client.get(test_url)
                if response.status_code == 200:
                    self.stats.record_success("jenkins")
                    try:
                        data = response.json()
                        if "jobs" in data and isinstance(data["jobs"], list):
                            self.api_base_path = pattern
                            job_count = len(data["jobs"])
                            logging.info(
                                f"Found working Jenkins API path: {pattern} ({job_count} jobs)"
                            )
                            return
                    except Exception as e:
                        logging.debug(f"Invalid JSON response from {pattern}: {e}")
                        continue
                else:
                    logging.debug(f"HTTP {response.status_code} for {pattern}")

            except Exception as e:
                logging.debug(f"Connection error testing {pattern}: {e}")
                continue

        # If no pattern worked, default to standard path
        self.api_base_path = "/api/json"
        logging.warning(
            f"Could not discover Jenkins API path for {self.host}, using default: {self.api_base_path}"
        )

    def get_all_jobs(self) -> dict[str, Any]:
        """Get all jobs from Jenkins with caching."""
        # Return cached data if available
        if self._cache_populated and self._jobs_cache:
            logging.debug(
                f"Using cached Jenkins jobs data ({len(self._jobs_cache.get('jobs', []))} jobs)"
            )
            return self._jobs_cache

        if not self.api_base_path:
            logging.error(f"No valid API base path discovered for {self.host}")
            return {}

        try:
            url = f"{self.base_url}{self.api_base_path}?tree=jobs[name,url,color,buildable,disabled]"
            logging.info(f"Fetching Jenkins jobs from: {url}")
            response = self.client.get(url)

            logging.info(f"Jenkins API response: {response.status_code}")
            if response.status_code == 200:
                self.stats.record_success("jenkins")
                data = response.json()
                job_count = len(data.get("jobs", []))
                logging.info(f"Found {job_count} Jenkins jobs (cached for reuse)")

                # Cache the data
                self._jobs_cache = data
                self._cache_populated = True
                return data
            else:
                self.stats.record_error("jenkins", response.status_code)
                logging.warning(
                    f"❌ Error: Jenkins API query returned error code: {response.status_code} for {url}"
                )
                logging.warning(f"Response text: {response.text[:500]}")
                return {}

        except Exception as e:
            self.stats.record_exception("jenkins")
            logging.error(f"❌ Error: Jenkins API query exception for {self.host}: {e}")
            return {}

    def get_jobs_for_project(
        self, project_name: str, allocated_jobs: set[str]
    ) -> list[dict[str, Any]]:
        """Get jobs related to a specific Gerrit project with duplicate prevention."""
        logging.debug(f"Looking for Jenkins jobs for project: {project_name}")
        all_jobs = self.get_all_jobs()
        project_jobs: list[dict[str, Any]] = []

        if "jobs" not in all_jobs:
            logging.debug(
                f"No 'jobs' key found in Jenkins API response for {project_name}"
            )
            return project_jobs

        # Convert project name to job name format (replace / with -)
        project_job_name = project_name.replace("/", "-")
        logging.debug(
            f"Searching for Jenkins jobs matching pattern: {project_job_name}"
        )

        total_jobs = len(all_jobs["jobs"])
        logging.debug(f"Checking {total_jobs} total Jenkins jobs for matches")

        # Collect potential matches with scoring for better matching
        candidates: list[tuple[dict[str, Any], int]] = []

        for job in all_jobs["jobs"]:
            job_name = job.get("name", "")

            # Skip already allocated jobs
            if job_name in allocated_jobs:
                logging.debug(f"Skipping already allocated Jenkins job: {job_name}")
                continue

            # Calculate match score for better job attribution
            score = self._calculate_job_match_score(
                job_name, project_name, project_job_name
            )
            if score > 0:
                candidates.append((job, score))

        # Sort by score (highest first) to prioritize better matches
        candidates.sort(key=lambda x: x[1], reverse=True)

        for job, score in candidates:
            job_name = job.get("name", "")
            logging.debug(f"Processing Jenkins job: {job_name} (score: {score})")

            # Get detailed job info
            job_details = self.get_job_details(job_name)
            if job_details:
                project_jobs.append(job_details)
                # Mark job as allocated
                allocated_jobs.add(job_name)
                logging.info(
                    f"Allocated Jenkins job '{job_name}' to project '{project_name}' (score: {score})"
                )
            else:
                logging.warning(f"Failed to get details for Jenkins job: {job_name}")

        logging.info(
            f"Found {len(project_jobs)} Jenkins jobs for project {project_name}"
        )
        return project_jobs

    def _calculate_job_match_score(
        self, job_name: str, project_name: str, project_job_name: str
    ) -> int:
        """
        Calculate a match score for Jenkins job attribution using STRICT PREFIX MATCHING ONLY.
        This prevents duplicate allocation by ensuring jobs can only match one project.
        Higher scores indicate better matches.
        Returns 0 for no match.
        """
        job_name_lower = job_name.lower()
        project_job_name_lower = project_job_name.lower()
        project_name_lower = project_name.lower()

        # STRICT PREFIX MATCHING WITH WORD BOUNDARY ONLY
        # Job name must either:
        # 1. Be exactly equal to project name, OR
        # 2. Start with project name followed by a dash (-)
        # This prevents sdc-tosca-* from matching sdc

        if job_name_lower == project_job_name_lower:
            # Exact match - highest priority
            pass
        elif job_name_lower.startswith(project_job_name_lower + "-"):
            # Prefix with dash separator - valid match
            pass
        else:
            # No match - neither exact nor proper prefix
            return 0

        score = 0

        # Higher score for exact match
        if job_name_lower == project_job_name_lower:
            score += 1000
            return score

        # High score for exact prefix match with separator (project-*)
        if job_name_lower.startswith(project_job_name_lower + "-"):
            score += 500
        # Exact match gets highest score
        else:
            score += 100

        # Bonus for longer/more specific project paths (child projects get priority)
        path_parts = project_name.count("/") + 1
        score += path_parts * 50

        # Bonus for containing full project name components in order
        project_parts = project_name_lower.replace("/", "-").split("-")
        consecutive_matches = 0
        job_parts = job_name_lower.split("-")

        for i, project_part in enumerate(project_parts):
            if i < len(job_parts) and job_parts[i] == project_part:
                consecutive_matches += 1
            else:
                break

        score += consecutive_matches * 25

        return score

    def get_job_details(self, job_name: str) -> dict[str, Any]:
        """Get detailed information about a specific job."""
        try:
            # Extract base path without /api/json suffix for job URLs
            base_path = (
                self.api_base_path.replace("/api/json", "")
                if self.api_base_path
                else ""
            )
            url = f"{self.base_url}{base_path}/job/{job_name}/api/json"
            response = self.client.get(url)

            if response.status_code == 200:
                job_data = response.json()

                # Get last build info
                last_build_info = self.get_last_build_info(job_name)

                # Compute Jenkins job state from disabled field first
                disabled = job_data.get("disabled", False)
                buildable = job_data.get("buildable", True)
                state = self._compute_jenkins_job_state(disabled, buildable)

                # Get original color from Jenkins
                original_color = job_data.get("color", "")

                # Compute standardized status from color field, considering state
                status = self._compute_job_status_from_color(original_color)

                # Override color if job is disabled (regardless of last build result)
                if state == "disabled":
                    color = "grey"
                    if status not in ("disabled", "not_built"):
                        status = "disabled"
                else:
                    color = original_color

                # Build standardized job data structure
                job_url = job_data.get("url", "")
                if not job_url and base_path:
                    # Fallback: construct URL if not provided by API
                    job_url = f"{self.base_url}{base_path}/job/{job_name}/"

                return {
                    "name": job_name,
                    "status": status,
                    "state": state,  # Add state attribute for consistency with workflows
                    "color": color,  # Color for consistency with workflows (may be overridden for disabled jobs)
                    "urls": {
                        "job_page": job_url,  # Jenkins job status/build page
                        "source": None,  # No source URL available for Jenkins jobs
                        "api": url,  # API endpoint for this job
                    },
                    "buildable": buildable,
                    "disabled": disabled,  # Keep original field for reference
                    "description": job_data.get("description", ""),
                    "last_build": last_build_info,
                }
            else:
                logging.debug(
                    f"Jenkins job API returned {response.status_code} for {job_name}"
                )
                return {}

        except Exception as e:
            logging.debug(f"Exception fetching job details for {job_name}: {e}")
            return {}

    def _compute_jenkins_job_state(self, disabled: bool, buildable: bool) -> str:
        """
        Convert Jenkins disabled and buildable fields to standardized state.

        Jenkins job states:
        - disabled=True: Job is explicitly disabled
        - disabled=False + buildable=True: Job is active and can be built
        - disabled=False + buildable=False: Job exists but cannot be built (treat as disabled)

        Args:
            disabled: Whether the job is disabled in Jenkins
            buildable: Whether the job is buildable

        Returns:
            State string: "active", "disabled"
        """
        if disabled:
            return "disabled"
        elif buildable:
            return "active"
        else:
            # If not disabled but not buildable, consider it effectively disabled
            return "disabled"

    def _compute_workflow_color_from_state(self, state: str) -> str:
        """
        Convert GitHub workflow state to color for consistency with Jenkins jobs.

        Args:
            state: GitHub workflow state ("active", "disabled", etc.)

        Returns:
            Color string compatible with Jenkins color scheme
        """
        if not state:
            return "grey"

        state_lower = state.lower()

        # Map workflow states to colors
        state_color_map = {
            "active": "blue",  # Active workflows get blue (like successful Jenkins jobs)
            "disabled": "grey",  # Disabled workflows get grey
            "deleted": "red",  # Deleted workflows get red
        }

        return state_color_map.get(state_lower, "grey")

    def _compute_job_status_from_color(self, color: str) -> str:
        """
        Convert Jenkins color field to standardized status.

        Jenkins color meanings:
        - blue: success
        - red: failure
        - yellow: unstable
        - grey: not built/disabled
        - aborted: aborted
        - *_anime: building (animated versions)
        """
        if not color:
            return "unknown"

        color_lower = color.lower()

        # Handle animated colors (building states)
        if color_lower.endswith("_anime"):
            return "building"

        # Map standard colors
        color_map = {
            "blue": "success",
            "red": "failure",
            "yellow": "unstable",
            "grey": "disabled",
            "gray": "disabled",
            "aborted": "aborted",
            "notbuilt": "not_built",
            "disabled": "disabled",
        }

        return color_map.get(color_lower, "unknown")

    def get_last_build_info(self, job_name: str) -> dict[str, Any]:
        """Get information about the last build of a job."""
        try:
            # Extract base path without /api/json suffix for job URLs
            base_path = (
                self.api_base_path.replace("/api/json", "")
                if self.api_base_path
                else ""
            )
            url = f"{self.base_url}{base_path}/job/{job_name}/lastBuild/api/json?tree=result,duration,timestamp,building,number"
            response = self.client.get(url)

            if response.status_code == 200:
                build_data = response.json()

                # Convert timestamp to readable format
                timestamp = build_data.get("timestamp", 0)
                if timestamp:
                    from datetime import datetime

                    build_time = datetime.fromtimestamp(timestamp / 1000)
                    build_data["build_time"] = build_time.isoformat()

                # Convert duration to readable format
                duration_ms = build_data.get("duration", 0)
                if duration_ms:
                    duration_seconds = duration_ms / 1000
                    build_data["duration_seconds"] = duration_seconds

                return build_data
            else:
                return {}

        except Exception as e:
            logging.debug(f"Exception fetching last build info for {job_name}: {e}")
            return {}


class GitHubAPIClient:
    """Client for interacting with GitHub API to fetch workflow run status."""

    def __init__(self, token: str, timeout: float = 30.0, stats: Optional[APIStatistics] = None):
        """Initialize GitHub API client with token."""
        self.token = token
        self.base_url = "https://api.github.com"
        self.client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "repository-reports/1.0.0",
            },
        )
        self.logger = logging.getLogger(__name__)
        self.stats = stats or api_stats

    def _write_to_step_summary(self, message: str) -> None:
        """Write a message to GitHub Step Summary if running in GitHub Actions."""
        step_summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
        if step_summary_file:
            try:
                with open(step_summary_file, "a") as f:
                    f.write(message + "\n")
            except Exception as e:
                self.logger.debug(f"Could not write to GITHUB_STEP_SUMMARY: {e}")

    def get_repository_workflows(self, owner: str, repo: str) -> list[dict[str, Any]]:
        """Get all workflows for a repository."""
        try:
            url = f"/repos/{owner}/{repo}/actions/workflows"
            response = self.client.get(url)

            if response.status_code == 401:
                self.stats.record_error("github", 401)
                error_msg = (
                    f"❌ **GitHub API Authentication Failed** for `{owner}/{repo}`\n\n"
                )
                error_msg += "The GitHub token is invalid or has expired.\n\n"
                error_msg += "**Action Required:** Update the `CLASSIC_READ_ONLY_PAT_TOKEN` secret with a valid Classic Personal Access Token.\n"
                self.logger.error(
                    f"❌ Error: GitHub API query returned error code: 401 for {owner}/{repo}"
                )
                self._write_to_step_summary(error_msg)
                return []
            elif response.status_code == 403:
                self.stats.record_error("github", 403)
                error_msg = (
                    f"⚠️ **GitHub API Permission Denied** for `{owner}/{repo}`\n\n"
                )
                try:
                    error_body = response.json()
                    error_message = error_body.get("message", response.text)
                    error_msg += f"Error: {error_message}\n\n"
                except Exception:
                    error_msg += f"Error: {response.text}\n\n"
                error_msg += (
                    "**Likely Cause:** The GitHub token lacks required permissions.\n\n"
                )
                error_msg += "**Required Scopes:**\n"
                error_msg += "- `repo` (or at least `repo:status`)\n"
                error_msg += "- `actions:read`\n\n"
                error_msg += (
                    "**To Fix:** Update your Personal Access Token with these scopes.\n"
                )
                self.logger.error(
                    f"❌ Error: GitHub API query returned error code: 403 for {owner}/{repo}"
                )
                self._write_to_step_summary(error_msg)
                return []
            elif response.status_code == 200:
                self.stats.record_success("github")
                data = response.json()
                workflows = []

                for workflow in data.get("workflows", []):
                    # Build standardized workflow data structure
                    workflow_path = workflow.get("path", "")
                    source_url = None
                    if workflow_path and owner and repo:
                        # Convert workflow path to GitHub source URL
                        source_url = f"https://github.com/{owner}/{repo}/blob/master/{workflow_path}"

                    # Compute color from status for consistency with Jenkins jobs
                    workflow_state = workflow.get("state", "unknown")
                    color = self._compute_workflow_color_from_state(workflow_state)

                    workflows.append(
                        {
                            "id": workflow.get("id"),
                            "name": workflow.get("name"),
                            "path": workflow_path,
                            "state": workflow_state,  # "active", "disabled", etc. (enabled/disabled state)
                            "status": "unknown",  # Will be filled by get_workflow_runs_status (pass/fail status)
                            "color": color,  # Add color attribute for consistency
                            "urls": {
                                "workflow_page": f"https://github.com/{owner}/{repo}/actions/workflows/{os.path.basename(workflow_path) if workflow_path else ''}",  # GitHub Actions page
                                "source": source_url,  # Source code URL
                                "badge": workflow.get("badge_url"),  # Badge URL
                            },
                        }
                    )

                return workflows

            elif response.status_code == 404:
                self.stats.record_error("github", 404)
                self.logger.debug(f"Repository {owner}/{repo} not found or no access")
                return []
            else:
                self.stats.record_error("github", response.status_code)
                self.logger.warning(
                    f"❌ Error: GitHub API query returned error code: {response.status_code} for {owner}/{repo}"
                )
                return []

        except Exception as e:
            self.stats.record_exception("github")
            self.logger.error(f"❌ Error: GitHub API query exception for {owner}/{repo}: {e}")
            return []

    def get_workflow_runs_status(
        self, owner: str, repo: str, workflow_id: int, limit: int = 10
    ) -> dict[str, Any]:
        """Get recent workflow runs for a specific workflow to determine status."""
        try:
            url = f"/repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs"
            params = {"per_page": limit, "page": 1}

            response = self.client.get(url, params=params)

            if response.status_code == 401:
                self.stats.record_error("github", 401)
                self.logger.error(
                    f"❌ Error: GitHub API query returned error code: 401 for workflow {workflow_id} in {owner}/{repo}"
                )
                return {"status": "auth_error", "last_run": None}
            elif response.status_code == 403:
                self.stats.record_error("github", 403)
                self.logger.error(
                    f"❌ Error: GitHub API query returned error code: 403 for workflow {workflow_id} in {owner}/{repo}"
                )
                return {"status": "permission_error", "last_run": None}
            elif response.status_code == 200:
                self.stats.record_success("github")
                data = response.json()
                runs = data.get("workflow_runs", [])

                if not runs:
                    return {"status": "no_runs", "last_run": None}

                # Get the most recent run
                latest_run = runs[0]

                # Compute standardized status from conclusion and run status
                conclusion = latest_run.get("conclusion", "unknown")
                run_status = latest_run.get("status", "unknown")
                standardized_status = self._compute_workflow_status(
                    conclusion, run_status
                )

                return {
                    "status": standardized_status,  # Standardized status
                    "conclusion": conclusion,  # Keep original for compatibility
                    "run_status": run_status,  # Keep original for compatibility
                    "last_run": {
                        "id": latest_run.get("id"),
                        "number": latest_run.get("run_number"),
                        "created_at": latest_run.get("created_at"),
                        "updated_at": latest_run.get("updated_at"),
                        "html_url": latest_run.get("html_url"),
                        "head_branch": latest_run.get("head_branch"),
                        "head_sha": latest_run.get("head_sha")[:7]
                        if latest_run.get("head_sha")
                        else None,
                    },
                }
            else:
                self.stats.record_error("github", response.status_code)
                self.logger.warning(
                    f"❌ Error: GitHub API query returned error code: {response.status_code} for workflow {workflow_id} runs"
                )
                return {"status": "api_error", "last_run": None}

        except Exception as e:
            self.stats.record_exception("github")
            self.logger.error(
                f"Error fetching workflow runs for {owner}/{repo}/workflows/{workflow_id}: {e}"
            )
            return {"status": "error", "last_run": None}

    def _compute_workflow_color_from_runtime_status(self, status: str) -> str:
        """
        Convert runtime workflow status to color for consistency with Jenkins jobs.

        Args:
            status: Runtime workflow status ("success", "failure", "building", etc.)

        Returns:
            Color string compatible with Jenkins color scheme
        """
        if not status:
            return "grey"

        status_lower = status.lower()

        # Map runtime statuses to colors (matching Jenkins scheme)
        status_color_map = {
            "success": "blue",  # Success = blue (like Jenkins)
            "failure": "red",  # Failure = red
            "building": "blue_anime",  # Building = animated blue
            "in_progress": "blue_anime",  # In progress = animated blue
            "cancelled": "grey",  # Cancelled = grey
            "skipped": "grey",  # Skipped = grey
            "unknown": "grey",  # Unknown = grey
            "error": "red",  # Error = red
            "no_runs": "grey",  # No runs = grey
        }

        return status_color_map.get(status_lower, "grey")

    def _compute_workflow_status(self, conclusion: str, run_status: str) -> str:
        """
        Convert GitHub workflow conclusion and run status to standardized status.

        GitHub conclusions: success, failure, neutral, cancelled, skipped, timed_out, action_required
        GitHub run statuses: queued, in_progress, completed
        """
        if not conclusion and not run_status:
            return "unknown"

        # Handle in-progress workflows first
        if run_status in ("queued", "in_progress"):
            return "building"

        # Handle completed workflows by conclusion
        if run_status == "completed":
            conclusion_map = {
                "success": "success",
                "failure": "failure",
                "neutral": "success",
                "cancelled": "cancelled",
                "skipped": "skipped",
                "timed_out": "failure",
                "action_required": "failure",
            }
            return conclusion_map.get(conclusion, "unknown")

        return "unknown"

    def get_repository_workflow_status_summary(
        self, owner: str, repo: str
    ) -> dict[str, Any]:
        """Get comprehensive workflow status summary for a repository."""
        workflows = self.get_repository_workflows(owner, repo)

        if not workflows:
            return {
                "has_workflows": False,
                "workflows": [],
                "overall_status": "no_workflows",
                "github_owner": owner,
                "github_repo": repo,
            }

        workflow_statuses = []
        active_workflows = [w for w in workflows if w.get("state") == "active"]

        for workflow in active_workflows:
            workflow_id = workflow.get("id")
            if workflow_id:
                status_info = self.get_workflow_runs_status(owner, repo, workflow_id)

                # Merge workflow info with status info, ensuring standardized structure
                merged_workflow = {**workflow, **status_info}

                # Update URLs with source URL if not already present
                if "urls" in merged_workflow and workflow.get("path"):
                    if not merged_workflow["urls"].get("source"):
                        merged_workflow["urls"]["source"] = (
                            f"https://github.com/{owner}/{repo}/blob/master/{workflow['path']}"
                        )

                # Update color based on runtime status if available
                if "status" in status_info and status_info["status"]:
                    merged_workflow["color"] = (
                        self._compute_workflow_color_from_runtime_status(
                            status_info["status"]
                        )
                    )

                workflow_statuses.append(merged_workflow)

        # Determine overall status
        if not workflow_statuses:
            overall_status = "no_active_workflows"
        else:
            latest_statuses = [w.get("status") for w in workflow_statuses]
            if any(status == "failure" for status in latest_statuses):
                overall_status = "has_failures"
            elif any(status == "success" for status in latest_statuses):
                overall_status = "has_successes"
            else:
                overall_status = "unknown"

        return {
            "has_workflows": True,
            "total_workflows": len(workflows),
            "active_workflows": len(active_workflows),
            "workflows": workflow_statuses,
            "overall_status": overall_status,
            "github_owner": owner,
            "github_repo": repo,
        }

    def _compute_workflow_color_from_state(self, state: str) -> str:
        """
        Convert GitHub workflow state to color for consistency with Jenkins jobs.

        Args:
            state: GitHub workflow state ("active", "disabled", etc.)

        Returns:
            Color string compatible with Jenkins color scheme
        """
        if not state:
            return "grey"

        state_lower = state.lower()

        # Map workflow states to colors
        state_color_map = {
            "active": "blue",  # Active workflows get blue (like successful Jenkins jobs)
            "disabled": "grey",  # Disabled workflows get grey
            "deleted": "red",  # Deleted workflows get red
        }

        return state_color_map.get(state_lower, "grey")


# =============================================================================
# GIT DATA COLLECTION (Phase 2 - TODO)
# =============================================================================


class GitDataCollector:
    """Handles Git repository analysis and metric collection."""

    def __init__(
        self,
        config: dict[str, Any],
        time_windows: dict[str, dict[str, Any]],
        logger: logging.Logger,
    ) -> None:
        self.config = config
        self.time_windows = time_windows
        self.logger = logger
        self.cache_enabled = config.get("performance", {}).get("cache", False)
        self.cache_dir = None
        self.repos_path: Optional[Path] = (
            None  # Will be set later for relative path calculation
        )
        if self.cache_enabled:
            self.cache_dir = Path(tempfile.gettempdir()) / "repo_reporting_cache"
            self.cache_dir.mkdir(exist_ok=True)

        # Initialize Gerrit API client if configured
        self.gerrit_client = None
        self.gerrit_projects_cache: dict[
            str, dict[str, Any]
        ] = {}  # Cache for all Gerrit project data
        gerrit_config = self.config.get("gerrit", {})

        # Initialize Jenkins API client if configured
        self.jenkins_client = None
        self.jenkins_jobs_cache: dict[
            str, list[dict[str, Any]]
        ] = {}  # Cache for Jenkins job data
        self.allocated_jenkins_jobs: set[str] = (
            set()
        )  # Track allocated jobs to prevent duplicates
        self.all_jenkins_jobs: dict[str, Any] = {}  # Cache all jobs fetched once
        self.orphaned_jenkins_jobs: dict[
            str, dict[str, Any]
        ] = {}  # Track jobs from archived projects
        self._jenkins_initialized = False

        # Check for Jenkins host from environment variable
        jenkins_host = os.environ.get("JENKINS_HOST")
        jenkins_config = self.config.get("jenkins", {})

        if gerrit_config.get("enabled", False):
            host = gerrit_config.get("host")
            base_url = gerrit_config.get("base_url")
            timeout = gerrit_config.get("timeout", 30.0)

            if host:
                try:
                    self.gerrit_client = GerritAPIClient(host, base_url, timeout)
                    self.logger.info(f"Initialized Gerrit API client for {host}")
                    # Fetch all project data upfront
                    self._fetch_all_gerrit_projects()
                except Exception as e:
                    self.logger.error(
                        f"Failed to initialize Gerrit API client for {host}: {e}"
                    )
            else:
                self.logger.error("Gerrit enabled but no host configured")

        # Initialize Jenkins client
        if jenkins_host:
            # Environment variable takes precedence - enables Jenkins integration
            timeout = jenkins_config.get("timeout", 30.0)
            try:
                self.jenkins_client = JenkinsAPIClient(jenkins_host, timeout)
                self.logger.info(
                    f"Initialized Jenkins API client for {jenkins_host} (from environment)"
                )
                # Test the connection and cache all jobs upfront
                self._initialize_jenkins_cache()
            except Exception as e:
                self.logger.error(
                    f"Failed to initialize Jenkins API client for {jenkins_host}: {e}"
                )
                self.jenkins_client = None
        elif jenkins_config.get("enabled", False):
            # Fallback to config file (for backward compatibility)
            host = jenkins_config.get("host")
            timeout = jenkins_config.get("timeout", 30.0)

            if host:
                try:
                    self.jenkins_client = JenkinsAPIClient(host, timeout)
                    self.logger.info(
                        f"Initialized Jenkins API client for {host} (from config)"
                    )
                    # Initialize cache for config-based Jenkins client too
                    self._initialize_jenkins_cache()
                except Exception as e:
                    self.logger.error(
                        f"Failed to initialize Jenkins API client for {host}: {e}"
                    )
            else:
                self.logger.error("Jenkins enabled but no host configured")

    def _initialize_jenkins_cache(self):
        """Initialize Jenkins jobs cache at startup for better performance."""
        if not self.jenkins_client or self._jenkins_initialized:
            return

        try:
            self.logger.info("Caching all Jenkins jobs for efficient allocation...")
            self.all_jenkins_jobs = self.jenkins_client.get_all_jobs()
            job_count = len(self.all_jenkins_jobs.get("jobs", []))
            self.logger.info(
                f"Jenkins cache initialized: {job_count} total jobs available"
            )
            self._jenkins_initialized = True
        except Exception as e:
            self.logger.error(f"Failed to initialize Jenkins cache: {e}")
            self._jenkins_initialized = False

    def _fetch_all_gerrit_projects(self) -> None:
        """Fetch all Gerrit project data upfront and cache it."""
        if not self.gerrit_client:
            return

        try:
            all_projects = self.gerrit_client.get_all_projects()

            if all_projects:
                self.gerrit_projects_cache = all_projects
                self.logger.info(f"Cached {len(all_projects)} projects from Gerrit")
            else:
                self.logger.warning("No projects returned from Gerrit API")

        except Exception as e:
            self.logger.error(f"Failed to fetch Gerrit projects: {e}")

    def _extract_gerrit_project(self, repo_path: Path) -> str:
        """
        Extract the hierarchical Gerrit project name from the repository path.

        For paths containing hostname patterns like:
        /path/to/gerrit.o-ran-sc.org/aiml-fw/aihp/tps/kserve-adapter
        returns 'aiml-fw/aihp/tps/kserve-adapter' (the full Gerrit project hierarchy).

        Falls back to repository folder name if no hierarchical structure is detected.
        """
        try:
            path_parts = repo_path.parts

            # Strategy 1: Look for gerrit-repos-* directory pattern
            for i, part in enumerate(path_parts):
                if part.startswith("gerrit-repos-"):
                    if i < len(path_parts) - 1:
                        project_path_parts = path_parts[i + 1 :]
                        gerrit_project = "/".join(project_path_parts)
                        self.logger.debug(
                            f"Extracted Gerrit project from gerrit-repos pattern: {gerrit_project}"
                        )
                        return gerrit_project
                    break

            # Strategy 2: Look for hostname pattern (gerrit.domain.tld)
            for i, part in enumerate(path_parts):
                if "." in part and any(
                    tld in part for tld in [".org", ".com", ".net", ".io"]
                ):
                    if i < len(path_parts) - 1:
                        project_path_parts = path_parts[i + 1 :]
                        gerrit_project = "/".join(project_path_parts)
                        self.logger.debug(
                            f"Extracted Gerrit project from hostname pattern: {gerrit_project}"
                        )
                        return gerrit_project
                    break

            # Strategy 3: Look for organization root directories and extract relative path
            # Common organization names in paths
            org_names = ["onap", "o-ran-sc", "opendaylight", "fdio", "opnfv", "agl"]

            for i, part in enumerate(path_parts):
                if part.lower() in org_names:
                    # Found organization root, extract everything after it
                    if i < len(path_parts) - 1:
                        project_path_parts = path_parts[i + 1 :]
                        gerrit_project = "/".join(project_path_parts)
                        self.logger.debug(
                            f"Extracted Gerrit project from organization root '{part}': {gerrit_project}"
                        )
                        return gerrit_project
                    break

            # Strategy 4: Check if any parent directories suggest hierarchical structure
            # Look for common Gerrit project patterns (2+ levels deep)
            # Filter out root directory from path_parts
            meaningful_parts = [part for part in path_parts if part and part != "/"]
            if len(meaningful_parts) >= 3:
                # Take last 2-4 path components as potential project hierarchy
                for depth in range(4, 1, -1):  # Try 4, 3, 2 components
                    if len(meaningful_parts) >= depth:
                        potential_project = "/".join(meaningful_parts[-depth:])
                        # Validate it looks_project

            # Fallback: use just the repository folder name
            self.logger.debug(
                f"No hierarchical structure detected, using folder name: {repo_path.name}"
            )
            return repo_path.name

        except Exception as e:
            self.logger.warning(
                f"Error extracting Gerrit project from {repo_path}: {e}"
            )
            return repo_path.name

    def _derive_gerrit_url(self, repo_path: Path) -> str:
        """
        Derive the full Gerrit URL from the repository path.

        Extracts hostname and project path to create URL like:
        gerrit.o-ran-sc.org/aiml-fw/aihp/tps/kserve-adapter
        """
        try:
            path_parts = repo_path.parts

            # Look for hostname pattern and construct URL-style path
            for i, part in enumerate(path_parts):
                if "." in part and any(
                    tld in part for tld in [".org", ".com", ".net", ".io"]
                ):
                    hostname = part
                    if i < len(path_parts) - 1:
                        project_parts = path_parts[i + 1 :]
                        gerrit_url = f"{hostname}/{'/'.join(project_parts)}"
                        self.logger.debug(f"Derived Gerrit URL: {gerrit_url}")
                        return gerrit_url
                    else:
                        return hostname

            # Fallback: construct generic URL with repo name only (avoid recursive issues)
            repo_name = repo_path.name
            fallback_url = f"unknown-gerrit-host/{repo_name}"
            self.logger.warning(
                f"Could not detect Gerrit hostname, using fallback: {fallback_url}"
            )
            return fallback_url

        except Exception as e:
            self.logger.warning(f"Error deriving Gerrit URL from {repo_path}: {e}")
            return str(repo_path)

    def _extract_gerrit_host(self, repo_path: Path) -> str:
        """Extract the Gerrit hostname from the repository path."""
        try:
            path_parts = repo_path.parts
            for part in path_parts:
                if "." in part and any(
                    tld in part for tld in [".org", ".com", ".net", ".io"]
                ):
                    return part
            return "unknown-gerrit-host"
        except Exception as e:
            self.logger.warning(f"Error extracting Gerrit host from {repo_path}: {e}")
            return "unknown-gerrit-host"

    def __del__(self):
        """Cleanup Gerrit client when GitDataCollector is destroyed."""
        if hasattr(self, "gerrit_client") and self.gerrit_client:
            try:
                self.gerrit_client.close()
            except Exception:
                pass  # Ignore cleanup errors

    def collect_repo_git_metrics(self, repo_path: Path) -> dict[str, Any]:
        """
        Extract Git metrics for a single repository across all time windows.

        Uses git log --numstat --date=iso --pretty=format for unified traversal.
        Single pass filtering commits into all time windows.
        Collects: timestamps, author name/email, added/removed lines.
        Returns structured metrics or error descriptor.
        """
        # Extract Gerrit project information
        if self.repos_path:
            gerrit_project = str(repo_path.relative_to(self.repos_path))
        else:
            gerrit_project = self._extract_gerrit_project(repo_path)
        gerrit_host = self._extract_gerrit_host(repo_path)
        gerrit_url = self._derive_gerrit_url(repo_path)

        self.logger.debug(
            f"Collecting Git metrics for Gerrit project: {gerrit_project}"
        )

        # Initialize metrics structure with Gerrit-centric model
        metrics: Dict[str, Any] = {
            "repository": {
                "gerrit_project": gerrit_project,  # PRIMARY identifier
                "gerrit_host": gerrit_host,
                "gerrit_url": gerrit_url,
                "local_path": str(repo_path),  # Secondary, for internal use
                "last_commit_timestamp": None,
                "days_since_last_commit": None,
                "activity_status": "inactive",  # "current", "active", or "inactive"
                "has_any_commits": False,  # Track if repo has ANY commits (regardless of time windows)
                "total_commits_ever": 0,  # Total commits across all history
                "commit_counts": {window: 0 for window in self.time_windows},
                "loc_stats": {
                    window: {"added": 0, "removed": 0, "net": 0}
                    for window in self.time_windows
                },
                "unique_contributors": {window: set() for window in self.time_windows},  # type: ignore
                "features": {},
            },
            "authors": {},  # email -> author metrics
            "errors": [],  # List[str]
        }

        try:
            # Check if this is actually a git repository
            if not (repo_path / ".git").exists():
                errors_list = metrics["errors"]
                assert isinstance(errors_list, list)
                errors_list.append(f"Not a git repository: {repo_path}")
                return metrics

            # Check cache if enabled
            if self.cache_enabled:
                cached_metrics = self._load_from_cache(repo_path)
                if cached_metrics:
                    self.logger.debug(f"Using cached metrics for {gerrit_project}")
                    return cached_metrics

            # Get git log with numstat in a single command
            git_command = [
                "git",
                "log",
                "--numstat",
                "--date=iso",
                "--pretty=format:%H|%ad|%an|%ae|%s",
            ]

            # NOTE: Removed max_history_years filtering to ensure all commit data is captured
            # for accurate total_commits_ever, has_any_commits, and complete contributor data.
            # Time window filtering is applied separately during commit processing.

            success, output = safe_git_command(git_command, repo_path, self.logger)
            if not success:
                metrics["errors"].append(f"Git command failed: {output}")
                return metrics

            # Parse git log output
            commits_data = self._parse_git_log_output(output, gerrit_project)

            # Update total commit count regardless of time windows
            metrics["repository"]["total_commits_ever"] = len(commits_data)
            metrics["repository"]["has_any_commits"] = len(commits_data) > 0

            # Process commits into time windows
            for commit_data in commits_data:
                self._update_commit_metrics(commit_data, metrics)

            # Finalize repository metrics
            self._finalize_repo_metrics(metrics, gerrit_project)

            # Convert sets to counts for JSON serialization
            repo_data = metrics["repository"]

            # Add Jenkins job information if available
            if self.jenkins_client:
                jenkins_jobs = self._get_jenkins_jobs_for_repo(gerrit_project)

                # Store computed status for each job for consistent access
                enriched_jobs = []
                for job in jenkins_jobs:
                    if isinstance(job, dict) and "status" in job:
                        enriched_jobs.append(job)
                    else:
                        # Fallback for jobs missing status (shouldn't happen with new structure)
                        enriched_job = (
                            dict(job) if isinstance(job, dict) else {"name": str(job)}
                        )
                        enriched_job["status"] = "unknown"
                        enriched_jobs.append(enriched_job)

                repo_data["jenkins"] = {
                    "jobs": enriched_jobs,
                    "job_count": len(enriched_jobs),
                    "has_jobs": len(enriched_jobs) > 0,
                }
            unique_contributors = repo_data["unique_contributors"]
            for window in self.time_windows:
                contributor_set = unique_contributors[window]
                assert isinstance(contributor_set, set)
                unique_contributors[window] = len(contributor_set)

            self.logger.debug(
                f"Collected {len(commits_data)} commits for {gerrit_project}"
            )

            # Save to cache if enabled
            if self.cache_enabled:
                self._save_cached_metrics(repo_path, repo_data)

            return metrics

        except Exception as e:
            self.logger.error(f"Error collecting Git metrics for {gerrit_project}: {e}")
            errors_list = metrics["errors"]
            assert isinstance(errors_list, list)
            errors_list.append(f"Unexpected error: {str(e)}")
            return metrics

    def _get_jenkins_jobs_for_repo(self, repo_name: str) -> list[dict[str, Any]]:
        """Get Jenkins jobs for a specific repository with duplicate prevention."""
        if not self.jenkins_client or not self._jenkins_initialized:
            self.logger.debug(
                f"No Jenkins client available or cache not initialized for {repo_name}"
            )
            return []

        # Use cached data instead of making API calls
        if repo_name in self.jenkins_jobs_cache:
            self.logger.debug(f"Using cached Jenkins jobs for {repo_name}")
            return self.jenkins_jobs_cache[repo_name]

        try:
            jobs = self.jenkins_client.get_jobs_for_project(
                repo_name, self.allocated_jenkins_jobs
            )
            if jobs:
                self.logger.debug(
                    f"Found {len(jobs)} Jenkins jobs for {repo_name}: {[job.get('name') for job in jobs]}"
                )
                # Cache the results
                self.jenkins_jobs_cache[repo_name] = jobs
            else:
                self.logger.debug(f"No Jenkins jobs found for {repo_name}")
                self.jenkins_jobs_cache[repo_name] = []
            return jobs
        except Exception as e:
            self.logger.warning(f"Error fetching Jenkins jobs for {repo_name}: {e}")
            self.jenkins_jobs_cache[repo_name] = []
            return []

    def reset_jenkins_allocation_state(self):
        """Reset Jenkins job allocation state for a fresh start."""
        self.allocated_jenkins_jobs.clear()
        self.jenkins_jobs_cache.clear()
        self.orphaned_jenkins_jobs.clear()
        self.logger.info("Reset Jenkins job allocation state")

    def get_jenkins_job_allocation_summary(self) -> dict[str, Any]:
        """Get summary of Jenkins job allocation for auditing purposes."""
        if not self.jenkins_client or not self._jenkins_initialized:
            return {"error": "No Jenkins client available or not initialized"}

        # Use cached data
        total_jobs = len(self.all_jenkins_jobs.get("jobs", []))
        allocated_count = len(self.allocated_jenkins_jobs)
        unallocated_count = total_jobs - allocated_count

        return {
            "total_jenkins_jobs": total_jobs,
            "allocated_jobs": allocated_count,
            "unallocated_jobs": unallocated_count,
            "allocated_job_names": sorted(list(self.allocated_jenkins_jobs)),
            "allocation_percentage": round((allocated_count / total_jobs * 100), 2)
            if total_jobs > 0
            else 0,
        }

    def validate_jenkins_job_allocation(self) -> list[str]:
        """Validate Jenkins job allocation and return any issues found."""
        issues = []

        if not self.jenkins_client or not self._jenkins_initialized:
            return ["No Jenkins client available or not initialized for validation"]

        # Check for duplicate allocations (shouldn't happen with new system)
        allocation_summary = self.get_jenkins_job_allocation_summary()

        if "error" in allocation_summary:
            issues.append(allocation_summary["error"])
            return issues

        if allocation_summary["unallocated_jobs"] > 0:
            # Use cached data
            all_job_names = {
                job.get("name", "") for job in self.all_jenkins_jobs.get("jobs", [])
            }
            unallocated_jobs = all_job_names - self.allocated_jenkins_jobs

            # Try to match unallocated jobs to archived Gerrit projects
            self._allocate_orphaned_jobs_to_archived_projects(unallocated_jobs)

            # Identify infrastructure jobs that legitimately don't belong to projects
            infrastructure_patterns = [
                "lab-",
                "lf-",
                "openci-",
                "rtdv3-",
                "global-jjb-",
                "ci-management-",
                "releng-",
                "autorelease-",
                "docs-",
                "infra-",
            ]

            # After orphaned job detection, recalculate what's truly unallocated
            orphaned_job_names = set(self.orphaned_jenkins_jobs.keys())
            remaining_unallocated = unallocated_jobs - orphaned_job_names

            infrastructure_jobs = set()
            project_jobs = set()

            for job in remaining_unallocated:
                job_lower = job.lower()
                is_infrastructure = any(
                    job_lower.startswith(pattern) for pattern in infrastructure_patterns
                )
                if is_infrastructure:
                    infrastructure_jobs.add(job)
                else:
                    project_jobs.add(job)

            # Report orphaned jobs as informational (matched to archived projects)
            if orphaned_job_names:
                orphaned_jobs_list = sorted(list(orphaned_job_names))
                issues.append(
                    f"INFO: Found {len(orphaned_job_names)} Jenkins jobs matched to archived/read-only Gerrit projects"
                )
                issues.append(f"Orphaned jobs: {orphaned_jobs_list}")

                # Group by project state
                by_state: dict[str, list[str]] = {}
                for job_name in orphaned_job_names:
                    job_info = self.orphaned_jenkins_jobs[job_name]
                    state = job_info.get("state", "UNKNOWN")
                    if state not in by_state:
                        by_state[state] = []
                    by_state[state].append(job_name)

                for state, jobs in by_state.items():
                    issues.append(
                        f"  - {len(jobs)} jobs for {state} projects: {sorted(jobs)}"
                    )

            # Only report remaining project jobs as critical errors
            if project_jobs:
                project_jobs_list = sorted(list(project_jobs))
                issues.append(
                    f"CRITICAL ERROR: Found {len(project_jobs)} unallocated project Jenkins jobs"
                )
                issues.append(f"Unallocated project jobs: {project_jobs_list}")

                # Analyze patterns in project jobs only
                patterns: dict[str, int] = {}
                for job in project_jobs:
                    parts = job.lower().split("-")
                    if parts:
                        first_part = parts[0]
                        patterns[first_part] = patterns.get(first_part, 0) + 1

                if patterns:
                    common_patterns = sorted(
                        patterns.items(), key=lambda x: x[1], reverse=True
                    )[:5]
                    issues.append(
                        f"Common patterns in unallocated project jobs: {common_patterns}"
                    )

                # Generate detailed suggestions for fixing unallocated project jobs
                suggestions = []
                for job in sorted(project_jobs)[:20]:  # Analyze first 20
                    job_parts = job.lower().split("-")
                    if job_parts:
                        suggestions.append(
                            f"  - '{job}' might belong to project containing '{job_parts[0]}'"
                        )

                if suggestions:
                    issues.append("Suggestions for unallocated project jobs:")
                    issues.extend(suggestions)

            # Log infrastructure jobs as informational
            if infrastructure_jobs:
                infrastructure_jobs_list = sorted(list(infrastructure_jobs))
                issues.append(
                    f"INFO: Found {len(infrastructure_jobs)} infrastructure Jenkins jobs (not assigned to projects)"
                )
                issues.append(f"Infrastructure jobs: {infrastructure_jobs_list}")

        return issues

    def _allocate_orphaned_jobs_to_archived_projects(
        self, unallocated_jobs: set[str]
    ) -> None:
        """Try to match unallocated Jenkins jobs to archived/read-only Gerrit projects."""
        if not self.gerrit_projects_cache or not unallocated_jobs:
            return

        self.logger.info(
            f"Attempting to match {len(unallocated_jobs)} unallocated Jenkins jobs to archived Gerrit projects"
        )

        # Get all archived/read-only projects
        archived_projects = {}
        for project_name, project_info in self.gerrit_projects_cache.items():
            state = project_info.get("state", "ACTIVE")
            if state in ["READ_ONLY", "HIDDEN"]:
                archived_projects[project_name] = project_info

        self.logger.debug(
            f"Found {len(archived_projects)} archived/read-only projects in Gerrit"
        )

        # Try to match jobs to archived projects using same logic as active projects
        for job_name in list(
            unallocated_jobs
        ):  # Use list() to avoid modification during iteration
            best_match = None
            best_score = 0

            for project_name, project_info in archived_projects.items():
                project_job_name = project_name.replace("/", "-")
                # Check if jenkins_client is available
                if self.jenkins_client:
                    score = self.jenkins_client._calculate_job_match_score(
                        job_name, project_name, project_job_name
                    )
                else:
                    # Fallback to simple matching if no Jenkins client
                    score = 100 if job_name.startswith(project_job_name) else 0

                if score > best_score:
                    best_score = score
                    best_match = (project_name, project_info)

            if best_match and best_score > 0:
                project_name, project_info = best_match
                self.orphaned_jenkins_jobs[job_name] = {
                    "project_name": project_name,
                    "state": project_info.get("state", "UNKNOWN"),
                    "score": best_score,
                }
                self.logger.info(
                    f"Matched orphaned job '{job_name}' to archived project '{project_name}' (state: {project_info.get('state')}, score: {best_score})"
                )

    def get_orphaned_jenkins_jobs_summary(self) -> dict[str, Any]:
        """Get summary of Jenkins jobs matched to archived projects."""
        if not self.orphaned_jenkins_jobs:
            return {"total_orphaned_jobs": 0, "by_state": {}, "jobs": {}}

        by_state: dict[str, list[str]] = {}
        for job_name, job_info in self.orphaned_jenkins_jobs.items():
            state = job_info.get("state", "UNKNOWN")
            if state not in by_state:
                by_state[state] = []
            by_state[state].append(job_name)

        return {
            "total_orphaned_jobs": len(self.orphaned_jenkins_jobs),
            "by_state": {state: len(jobs) for state, jobs in by_state.items()},
            "jobs": dict(self.orphaned_jenkins_jobs),
        }

    def bucket_commit_into_windows(
        self,
        commit_datetime: datetime.datetime,
        time_windows: dict[str, dict[str, Any]],
    ) -> List[str]:
        """
        Determine which time windows a commit falls into.

        A commit belongs to a window if it occurred after the window's start time.
        """
        matching_windows = []
        commit_timestamp = commit_datetime.timestamp()

        for window_name, window_data in time_windows.items():
            if commit_timestamp >= window_data["start_timestamp"]:
                matching_windows.append(window_name)

        return matching_windows

    def extract_organizational_domain(self, full_domain: str) -> str:
        """
        Extract organizational domain from full domain by taking the last two parts.
        Uses configuration file for exceptions where full domain should be preserved.

        Examples:
        - users.noreply.github.com -> github.com
        - tnap-dev-vm-mangala.tnaplab.telekom.de -> telekom.de
        - contractor.linuxfoundation.org -> linuxfoundation.org
        - zte.com.cn -> zte.com.cn (preserved due to configuration)
        - simple.com -> simple.com (unchanged for 2-part domains)
        - localhost -> localhost (unchanged for single-part domains)
        """
        if not full_domain or full_domain in ["unknown", "localhost", ""]:
            return full_domain

        # Load domain configuration (with caching)
        if not hasattr(self, "_domain_config"):
            self._domain_config = self._load_domain_config()

        # Check if domain should be preserved in full
        if full_domain in self._domain_config.get("preserve_full_domain", []):
            return full_domain

        # Check for custom mappings
        custom_mappings = self._domain_config.get("custom_mappings", {})
        if full_domain in custom_mappings:
            return custom_mappings[full_domain]

        # Split domain into parts
        parts = full_domain.split(".")

        # If 2 or fewer parts, return as-is
        if len(parts) <= 2:
            return full_domain

        # Return last two parts
        return ".".join(parts[-2:])

    def _load_domain_config(self) -> dict:
        """Load organizational domain configuration from YAML file."""
        import os
        import yaml

        config_path = os.path.join(
            os.path.dirname(__file__), "configuration", "organizational_domains.yaml"
        )

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
                self.logger.debug(
                    f"Loaded organizational domain config from {config_path}"
                )
                return config
        except FileNotFoundError:
            self.logger.warning(
                f"Organizational domain config file not found: {config_path}"
            )
            return {}
        except Exception as e:
            self.logger.error(f"Error loading organizational domain config: {e}")
            return {}

    def normalize_author_identity(self, name: str, email: str) -> tuple[str, str]:
        """
        Normalize author identity with consistent format.

        - Email lowercase and trimmed
        - Username heuristic from email local part
        - Handle malformed emails gracefully
        - Domain extraction for organization analysis
        """
        # Clean and normalize inputs
        clean_name = name.strip() if name else "Unknown"
        clean_email = email.lower().strip() if email else ""

        # Handle empty or malformed emails
        if not clean_email or "@" not in clean_email:
            unknown_placeholder = self.config.get("data_quality", {}).get(
                "unknown_email_placeholder", "unknown@unknown"
            )
            clean_email = unknown_placeholder

        normalized = {
            "name": clean_name,
            "email": clean_email,
            "username": "",
            "domain": "",
        }

        # Extract username and domain from email
        if "@" in clean_email:
            # Always split on the LAST @ symbol to handle complex email addresses
            parts = clean_email.split("@")
            if len(parts) >= 2:
                normalized["username"] = "@".join(parts[:-1])
                normalized["domain"] = parts[-1].lower()
            else:
                # Shouldn't happen since we checked for @ above, but be safe
                normalized["username"] = clean_email
                normalized["domain"] = ""

        return (normalized["name"], normalized["email"])

    def _parse_git_log_output(
        self, git_output: str, repo_name: str
    ) -> List[Dict[str, Any]]:
        """
        Parse git log output into structured commit data.

        Expected format from git log --numstat --date=iso --pretty=format:%H|%ad|%an|%ae|%s
        """
        commits = []
        lines = git_output.strip().split("\n")
        current_commit = None

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Check if this is a commit header line (contains |)
            if "|" in line and len(line.split("|")) >= 5:
                # Save previous commit if exists
                if current_commit:
                    commits.append(current_commit)

                # Parse commit header: hash|date|author_name|author_email|subject
                parts = line.split("|", 4)
                try:
                    commit_date = datetime.datetime.fromisoformat(
                        parts[1].replace(" ", "T")
                    )
                    if commit_date.tzinfo is None:
                        commit_date = commit_date.replace(tzinfo=datetime.timezone.utc)
                except (ValueError, IndexError):
                    self.logger.warning(
                        f"Invalid date format in {repo_name}: {parts[1] if len(parts) > 1 else 'unknown'}"
                    )
                    continue

                current_commit = {
                    "hash": parts[0],
                    "date": commit_date,
                    "author_name": parts[2],
                    "author_email": parts[3],
                    "subject": parts[4] if len(parts) > 4 else "",
                    "files_changed": [],
                }
            else:
                # Parse numstat lines (format: added<tab>removed<tab>filename)
                parts = line.split("\t")
                if len(parts) >= 3 and current_commit:
                    try:
                        # Handle binary files (marked with -)
                        added = 0 if parts[0] == "-" else int(parts[0])
                        removed = 0 if parts[1] == "-" else int(parts[1])
                        filename = parts[2]

                        # Skip binary files if configured
                        if self.config.get("data_quality", {}).get(
                            "skip_binary_changes", True
                        ):
                            if parts[0] == "-" or parts[1] == "-":
                                continue

                        files_changed = current_commit["files_changed"]
                        assert isinstance(files_changed, list)
                        files_changed.append(
                            {
                                "filename": filename,
                                "added": added,
                                "removed": removed,
                            }
                        )
                    except (ValueError, IndexError):
                        # Skip malformed lines
                        continue

        # Don't forget the last commit
        if current_commit:
            commits.append(current_commit)

        return commits

    def _update_commit_metrics(
        self, commit: dict[str, Any], metrics: dict[str, Any]
    ) -> None:
        """Process a single commit into the metrics structure."""
        applicable_windows = self.bucket_commit_into_windows(
            commit["date"], self.time_windows
        )

        # Normalize author identity
        norm_name, norm_email = self.normalize_author_identity(
            commit["author_name"], commit["author_email"]
        )
        author_email = norm_email

        # Create author info dict for compatibility
        author_info = {
            "name": norm_name,
            "email": norm_email,
            "username": norm_name.split()[0] if norm_name else "",
            "domain": self.extract_organizational_domain(norm_email.split("@")[-1])
            if "@" in norm_email
            else "",
        }

        # Calculate LOC changes for this commit
        total_added = sum(f["added"] for f in commit["files_changed"])
        total_removed = sum(f["removed"] for f in commit["files_changed"])
        net_lines = total_added - total_removed

        # Update repository metrics for each matching window
        for window in applicable_windows:
            metrics["repository"]["commit_counts"][window] += 1
            metrics["repository"]["loc_stats"][window]["added"] += total_added
            metrics["repository"]["loc_stats"][window]["removed"] += total_removed
            metrics["repository"]["loc_stats"][window]["net"] += net_lines
            metrics["repository"]["unique_contributors"][window].add(author_email)

        # Update author metrics
        if author_email not in metrics["authors"]:
            metrics["authors"][author_email] = {
                "name": author_info["name"],
                "email": author_email,
                "username": author_info["username"],
                "domain": author_info["domain"],
                "commit_counts": {window: 0 for window in self.time_windows},
                "loc_stats": {
                    window: {"added": 0, "removed": 0, "net": 0}
                    for window in self.time_windows
                },
                "repositories": {window: set() for window in self.time_windows},  # type: ignore
            }

        # Update author metrics for each matching window
        author_metrics = metrics["authors"][author_email]
        for window in applicable_windows:
            author_metrics["commit_counts"][window] += 1
            author_metrics["loc_stats"][window]["added"] += total_added
            author_metrics["loc_stats"][window]["removed"] += total_removed
            author_metrics["loc_stats"][window]["net"] += net_lines
            author_metrics["repositories"][window].add(
                metrics["repository"]["gerrit_project"]
            )

    def _finalize_repo_metrics(self, metrics: dict[str, Any], repo_name: str) -> None:
        """Finalize repository metrics after processing all commits."""
        repo_metrics = metrics["repository"]

        # Check if repository has any commits at all
        if repo_metrics.get("has_any_commits", False):
            # Repository has commits - find last commit date
            git_command = ["git", "log", "-1", "--date=iso", "--pretty=format:%ad"]
            success, output = safe_git_command(
                git_command, Path(repo_metrics["local_path"]), self.logger
            )

            if success and output.strip():
                try:
                    last_commit_date = datetime.datetime.fromisoformat(
                        output.strip().replace(" ", "T")
                    )
                    if last_commit_date.tzinfo is None:
                        last_commit_date = last_commit_date.replace(
                            tzinfo=datetime.timezone.utc
                        )

                    repo_metrics["last_commit_timestamp"] = last_commit_date.isoformat()

                    # Calculate days since last commit
                    now = datetime.datetime.now(datetime.timezone.utc)
                    days_since = (now - last_commit_date).days
                    repo_metrics["days_since_last_commit"] = days_since

                    # Determine activity status using unified thresholds
                    current_threshold = self.config.get("activity_thresholds", {}).get(
                        "current_days", 365
                    )
                    active_threshold = self.config.get("activity_thresholds", {}).get(
                        "active_days", 1095
                    )

                    has_recent_commits = any(
                        count > 0 for count in repo_metrics["commit_counts"].values()
                    )

                    if has_recent_commits and days_since <= current_threshold:
                        repo_metrics["activity_status"] = "current"
                    elif has_recent_commits and days_since <= active_threshold:
                        repo_metrics["activity_status"] = "active"
                    else:
                        repo_metrics["activity_status"] = "inactive"

                    # Log appropriate message based on activity
                    if any(
                        count > 0 for count in repo_metrics["commit_counts"].values()
                    ):
                        self.logger.debug(
                            f"Repository {repo_name} has {repo_metrics['total_commits_ever']} commits ({sum(repo_metrics['commit_counts'].values())} recent)"
                        )
                    else:
                        self.logger.debug(
                            f"Repository {repo_name} has {repo_metrics['total_commits_ever']} commits (all historical, none recent)"
                        )

                except ValueError as e:
                    self.logger.warning(
                        f"Could not parse last commit date for {repo_name}: {e}"
                    )
        else:
            # Truly no commits - empty repository
            self.logger.info(f"Repository {repo_name} has no commits")

        # Convert author repository sets to counts for JSON serialization
        for author_email, author_data in metrics["authors"].items():
            for window in self.time_windows:
                author_data["repositories"][window] = len(
                    author_data["repositories"][window]
                )

        # Embed authors data in repository record for aggregation
        repo_authors = []
        for author_email, author_data in metrics["authors"].items():
            # Convert author data to expected format for aggregation
            author_record = {
                "name": author_data["name"],
                "email": author_data["email"],
                "username": author_data["username"],
                "domain": author_data["domain"],
                "commits": author_data["commit_counts"],
                "lines_added": {
                    window: author_data["loc_stats"][window]["added"]
                    for window in self.time_windows
                },
                "lines_removed": {
                    window: author_data["loc_stats"][window]["removed"]
                    for window in self.time_windows
                },
                "lines_net": {
                    window: author_data["loc_stats"][window]["net"]
                    for window in self.time_windows
                },
                "repositories": author_data["repositories"],
            }
            repo_authors.append(author_record)

        metrics["repository"]["authors"] = repo_authors

    def _get_repo_cache_key(self, repo_path: Path) -> Optional[str]:
        """Generate a cache key based on the repository's HEAD commit hash."""
        git_command = ["git", "rev-parse", "HEAD"]
        success, output = safe_git_command(git_command, repo_path, self.logger)

        if success and output.strip():
            head_hash = output.strip()
            # Include time windows in cache key to invalidate when windows change
            windows_key = hashlib.sha256(
                json.dumps(self.time_windows, sort_keys=True).encode()
            ).hexdigest()[:8]
            project_name = self._extract_gerrit_project(repo_path)
            # Replace path separators for cache key
            safe_project_name = project_name.replace("/", "_")
            return f"{safe_project_name}_{head_hash}_{windows_key}"

        return None

    def _get_cache_path(self, repo_path: Path) -> Optional[Path]:
        """Get the cache file path for a repository."""
        if not self.cache_dir:
            return None

        cache_key = self._get_repo_cache_key(repo_path)
        if cache_key:
            return self.cache_dir / f"{cache_key}.json"

        return None

    def _load_from_cache(self, repo_path: Path) -> Optional[Dict[str, Any]]:
        """Load cached metrics for a repository if available and valid."""
        try:
            cache_path = self._get_cache_path(repo_path)
            if not cache_path or not cache_path.exists():
                return None

            with open(cache_path, "r", encoding="utf-8") as f:
                cached_data = json.load(f)

            # Validate cache structure
            if not isinstance(cached_data, dict) or "repository" not in cached_data:
                project_name = self._extract_gerrit_project(repo_path)
                self.logger.warning(f"Invalid cache structure for {project_name}")
                return None

            # Check if cache is compatible with current time windows
            cached_windows = set(
                cached_data.get("repository", {}).get("commit_counts", {}).keys()
            )
            current_windows = set(self.time_windows.keys())

            if cached_windows != current_windows:
                self.logger.debug(
                    f"Cache invalidated for {repo_path.name}: time windows changed"
                )
                return None

            return cached_data

        except (json.JSONDecodeError, IOError, KeyError) as e:
            self.logger.debug(f"Failed to load cache for {repo_path.name}: {e}")
            return None

    def _save_cached_metrics(self, repo_path: Path, metrics: dict[str, Any]) -> None:
        """Save metrics to cache for future use."""
        try:
            cache_path = self._get_cache_path(repo_path)
            if not cache_path:
                return

            # Create a cache-friendly copy (convert sets to lists if any remain)
            cache_data = json.loads(json.dumps(metrics, default=str))

            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(cache_data, f, indent=2, default=str)

            self.logger.debug(f"Saved cache for {repo_path.name}")

        except (IOError, TypeError) as e:
            self.logger.warning(f"Failed to save cache for {repo_path.name}: {e}")


# =============================================================================
# FEATURE SCANNING AND REGISTRY (Phase 3 - TODO)
# =============================================================================


class FeatureRegistry:
    """Registry for repository feature detection functions."""

    def __init__(self, config: dict[str, Any], logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self.checks: dict[str, Any] = {}
        
        # Determine GitHub organization once at initialization
        self.github_org = self._determine_github_org()
        self.github_org_source = "not_configured"
        
        if self.github_org:
            # Log how we got the GitHub org
            if os.environ.get("GITHUB_ORG"):
                self.github_org_source = "environment_variable"
                self.logger.info(f"GitHub organization set from GITHUB_ORG environment variable: '{self.github_org}'")
            elif self.config.get("github"):
                self.github_org_source = "config_file"
                self.logger.info(f"GitHub organization set from config file: '{self.github_org}'")
            elif self.config.get("extensions", {}).get("github_api", {}).get("github_org"):
                self.github_org_source = "config_extensions"
                self.logger.info(f"GitHub organization set from extensions config: '{self.github_org}'")
        
        self._register_default_checks()

    def register(self, feature_name: str, check_function):
        """Register a feature detection function."""
        self.checks[feature_name] = check_function

    def _register_default_checks(self):
        """Register all default feature detection checks."""
        self.register("dependabot", self._check_dependabot)
        self.register("github2gerrit_workflow", self._check_github2gerrit_workflow)
        self.register("g2g", self._check_g2g)
        self.register("pre_commit", self._check_pre_commit)
        self.register("readthedocs", self._check_readthedocs)
        self.register("sonatype_config", self._check_sonatype_config)
        self.register("project_types", self._check_project_types)
        self.register("workflows", self._check_workflows)
        self.register("gitreview", self._check_gitreview)
        self.register("github_mirror", self._check_github_mirror)

    def detect_features(self, repo_path: Path) -> dict[str, Any]:
        """
        Scan repository for all enabled features.

        TODO: Implement in Phase 3
        """
        enabled_features = self.config.get("features", {}).get("enabled", [])
        results = {}

        for feature_name in enabled_features:
            if feature_name in self.checks:
                try:
                    results[feature_name] = self.checks[feature_name](repo_path)
                except Exception as e:
                    self.logger.warning(
                        f"Feature check '{feature_name}' failed for {repo_path.name}: {e}"
                    )
                    results[feature_name] = {"error": str(e)}

        return results

    def _check_dependabot(self, repo_path: Path) -> dict[str, Any]:
        """Check for Dependabot configuration."""
        config_files = [".github/dependabot.yml", ".github/dependabot.yaml"]

        found_files = []
        for config_file in config_files:
            file_path = repo_path / config_file
            if file_path.exists():
                found_files.append(config_file)

        return {"present": len(found_files) > 0, "files": found_files}

    def _check_github2gerrit_workflow(self, repo_path: Path) -> dict[str, Any]:
        """Check for GitHub to Gerrit workflow patterns."""
        workflows_dir = repo_path / ".github" / "workflows"
        if not workflows_dir.exists():
            return {"present": False, "workflows": []}

        gerrit_patterns = [
            "gerrit",
            "review",
            "submit",
            "replication",
            "github2gerrit",
            "gerrit-review",
            "gerrit-submit",
        ]

        matching_workflows: list[dict[str, str]] = []
        try:
            for workflow_file in workflows_dir.glob("*.yml"):
                try:
                    with open(workflow_file, "r", encoding="utf-8") as f:
                        content = f.read().lower()
                        for pattern in gerrit_patterns:
                            if pattern in content:
                                matching_workflows.append(
                                    {  # type: ignore
                                        "file": workflow_file.name,
                                        "pattern": pattern,
                                    }
                                )
                                break
                except (IOError, UnicodeDecodeError):
                    continue

            # Also check .yaml files
            for workflow_file in workflows_dir.glob("*.yaml"):
                try:
                    with open(workflow_file, "r", encoding="utf-8") as f:
                        content = f.read().lower()
                        for pattern in gerrit_patterns:
                            if pattern in content:
                                matching_workflows.append(
                                    {  # type: ignore
                                        "file": workflow_file.name,
                                        "pattern": pattern,
                                    }
                                )
                                break
                except (IOError, UnicodeDecodeError):
                    continue

        except OSError:
            return {"present": False, "workflows": []}

        return {"present": len(matching_workflows) > 0, "workflows": matching_workflows}

    def _check_g2g(self, repo_path: Path) -> dict[str, Any]:
        """Check for specific GitHub to Gerrit workflow files."""
        workflows_dir = repo_path / ".github" / "workflows"
        g2g_files = ["github2gerrit.yaml", "call-github2gerrit.yaml"]

        found_files = []
        for filename in g2g_files:
            file_path = workflows_dir / filename
            if file_path.exists():
                found_files.append(f".github/workflows/{filename}")

        return {
            "present": len(found_files) > 0,
            "file_paths": found_files,
            "file_path": found_files[0]
            if found_files
            else None,  # Keep for backward compatibility
        }

    def _check_pre_commit(self, repo_path: Path) -> dict[str, Any]:
        """Check for pre-commit configuration."""
        config_files = [".pre-commit-config.yaml", ".pre-commit-config.yml"]

        found_config = None
        for config_file in config_files:
            file_path = repo_path / config_file
            if file_path.exists():
                found_config = config_file
                break

        result: dict[str, Any] = {
            "present": found_config is not None,
            "config_file": found_config,
        }

        # If config exists, try to extract some basic info
        if found_config:
            try:
                config_path = repo_path / found_config
                with open(config_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    # Count number of repos/hooks (basic analysis)
                    import re

                    repos_count = len(
                        re.findall(r"^\s*-\s*repo:", content, re.MULTILINE)
                    )
                    result["repos_count"] = repos_count
            except (IOError, UnicodeDecodeError):
                pass

        return result

    def _check_readthedocs(self, repo_path: Path) -> dict[str, Any]:
        """Check for Read the Docs configuration."""
        # Check for RTD config files
        rtd_configs = [
            ".readthedocs.yml",
            ".readthedocs.yaml",
            "readthedocs.yml",
            "readthedocs.yaml",
        ]

        sphinx_configs = ["docs/conf.py", "doc/conf.py", "documentation/conf.py"]

        mkdocs_configs = ["mkdocs.yml", "mkdocs.yaml"]

        found_configs = []
        config_type = None

        # Check RTD config files
        for config in rtd_configs:
            if (repo_path / config).exists():
                found_configs.append(config)
                config_type = "readthedocs"

        # Check Sphinx configs
        for config in sphinx_configs:
            if (repo_path / config).exists():
                found_configs.append(config)
                if not config_type:
                    config_type = "sphinx"

        # Check MkDocs configs
        for config in mkdocs_configs:
            if (repo_path / config).exists():
                found_configs.append(config)
                if not config_type:
                    config_type = "mkdocs"

        return {
            "present": len(found_configs) > 0,
            "config_type": config_type,
            "config_files": found_configs,
        }

    def _check_sonatype_config(self, repo_path: Path) -> dict[str, Any]:
        """Check for Sonatype configuration files."""
        sonatype_configs = [
            ".sonatype-lift.yaml",
            ".sonatype-lift.yml",
            "lift.toml",
            "lifecycle.json",
            ".lift.toml",
            "sonatype-lift.yml",
            "sonatype-lift.yaml",
        ]

        found_configs = []
        for config in sonatype_configs:
            if (repo_path / config).exists():
                found_configs.append(config)

        return {"present": len(found_configs) > 0, "config_files": found_configs}

    def _check_project_types(self, repo_path: Path) -> dict[str, Any]:
        """Detect project types based on configuration files and repository characteristics."""
        repo_name = repo_path.name.lower()

        # Static classifications based on repository names
        if repo_name == "ci-management":
            return {
                "detected_types": ["jjb"],
                "primary_type": "jjb",
                "details": [
                    {"type": "jjb", "files": ["repository_name"], "confidence": 100}
                ],
            }

        project_types = {
            "maven": ["pom.xml"],
            "gradle": [
                "build.gradle",
                "build.gradle.kts",
                "gradle.properties",
                "settings.gradle",
            ],
            "node": ["package.json"],
            "python": [
                "pyproject.toml",
                "requirements.txt",
                "setup.py",
                "setup.cfg",
                "Pipfile",
                "poetry.lock",
            ],
            "docker": ["Dockerfile", "docker-compose.yml", "docker-compose.yaml"],
            "go": ["go.mod", "go.sum"],
            "rust": ["Cargo.toml", "Cargo.lock"],
            "java": ["build.xml", "ivy.xml"],  # Ant
            "c_cpp": ["Makefile", "CMakeLists.txt", "configure.ac", "configure.in"],
            "dotnet": ["*.csproj", "*.sln", "project.json", "*.vbproj", "*.fsproj"],
            "ruby": ["Gemfile", "Rakefile", "*.gemspec"],
            "php": ["composer.json", "composer.lock"],
            "scala": ["build.sbt", "project/build.properties"],
            "swift": ["Package.swift"],
            "kotlin": ["build.gradle.kts"],
        }

        detected_types = []
        confidence_scores = {}

        for project_type, config_files in project_types.items():
            matches = []
            for config_pattern in config_files:
                if "*" in config_pattern:
                    # Handle glob patterns
                    try:
                        matching_files = list(repo_path.glob(config_pattern))
                        if matching_files:
                            matches.extend([f.name for f in matching_files])
                    except OSError:
                        continue
                else:
                    # Regular file check
                    if (repo_path / config_pattern).exists():
                        matches.append(config_pattern)

            if matches:
                detected_types.append(
                    {"type": project_type, "files": matches, "confidence": len(matches)}
                )
                confidence_scores[project_type] = len(matches)

        # Determine primary type (highest confidence)
        primary_type = None
        if detected_types:
            primary_type = max(confidence_scores.items(), key=lambda x: x[1])[0]

        # If no programming language detected, check for documentation as fallback
        if not detected_types and self._is_documentation_repository(repo_path):
            return {
                "detected_types": ["documentation"],
                "primary_type": "documentation",
                "details": [
                    {
                        "type": "documentation",
                        "files": self._get_doc_indicators(repo_path),
                        "confidence": 50,
                    }
                ],
            }

        return {
            "detected_types": [t["type"] for t in detected_types],
            "primary_type": primary_type,
            "details": detected_types,
        }

    def _is_documentation_repository(self, repo_path: Path) -> bool:
        """Determine if a repository is primarily for documentation (fallback only)."""
        repo_name = repo_path.name.lower()

        # Only classify as documentation if repository name strongly indicates it
        strong_doc_patterns = ["documentation", "manual", "wiki", "guide", "tutorial"]
        if any(
            repo_name == pattern or repo_name.endswith(f"-{pattern}")
            for pattern in strong_doc_patterns
        ):
            return True

        # For repos named exactly "doc" or "docs"
        if repo_name in ["doc", "docs"]:
            return True

        # Check directory structure and file patterns - be more restrictive
        doc_indicators = self._get_doc_indicators(repo_path)
        return (
            len(doc_indicators) >= 5
        )  # Require more indicators for stronger confidence

    def _get_doc_indicators(self, repo_path: Path) -> list[str]:
        """Get list of documentation indicators found in the repository."""
        indicators = []

        # Check for common documentation files
        doc_files = [
            "README.md",
            "README.rst",
            "README.txt",
            "DOCS.md",
            "DOCUMENTATION.md",
            "index.md",
            "index.rst",
            "index.html",
            "sphinx.conf",
            "conf.py",  # Sphinx
            "mkdocs.yml",
            "_config.yml",  # MkDocs/Jekyll
            "Gemfile",  # Jekyll
        ]

        for doc_file in doc_files:
            if (repo_path / doc_file).exists():
                indicators.append(doc_file)

        # Check for documentation directories
        doc_dirs = [
            "docs",
            "doc",
            "documentation",
            "_docs",
            "manual",
            "guides",
            "tutorials",
        ]
        for doc_dir in doc_dirs:
            if (repo_path / doc_dir).is_dir():
                indicators.append(f"{doc_dir}/")

        # Check for common documentation file extensions in root
        try:
            doc_extensions = [".md", ".rst", ".adoc", ".txt"]
            for ext in doc_extensions:
                if list(repo_path.glob(f"*{ext}")):
                    indicators.append(f"*{ext}")
        except OSError:
            pass

        # Check for static site generators
        static_generators = [
            ".gitbook",  # GitBook
            "_config.yml",  # Jekyll
            "mkdocs.yml",  # MkDocs
            "conf.py",  # Sphinx
            "book.toml",  # mdBook
            "docusaurus.config.js",  # Docusaurus
        ]

        for generator in static_generators:
            if (repo_path / generator).exists():
                indicators.append(generator)

        return indicators

    def _check_workflows(self, repo_path: Path) -> dict[str, Any]:
        """Analyze GitHub workflows with optional GitHub API integration."""
        workflows_dir = repo_path / ".github" / "workflows"
        if not workflows_dir.exists():
            return {
                "count": 0,
                "classified": {"verify": 0, "merge": 0, "other": 0},
                "files": [],
            }

        # Get classification patterns from config
        workflow_config = self.config.get("workflows", {}).get("classify", {})
        verify_patterns = workflow_config.get(
            "verify", ["verify", "test", "ci", "check"]
        )
        merge_patterns = workflow_config.get(
            "merge", ["merge", "release", "deploy", "publish"]
        )

        workflow_files = []
        classified = {"verify": 0, "merge": 0, "other": 0}

        try:
            # Process .yml files
            for workflow_file in workflows_dir.glob("*.yml"):
                workflow_info = self._analyze_workflow_file(
                    workflow_file, verify_patterns, merge_patterns
                )
                workflow_files.append(workflow_info)
                classified[workflow_info["classification"]] += 1

            # Process .yaml files
            for workflow_file in workflows_dir.glob("*.yaml"):
                workflow_info = self._analyze_workflow_file(
                    workflow_file, verify_patterns, merge_patterns
                )
                workflow_files.append(workflow_info)
                classified[workflow_info["classification"]] += 1

        except OSError:
            return {
                "count": 0,
                "classified": {"verify": 0, "merge": 0, "other": 0},
                "files": [],
            }

        # Extract just the workflow names for telemetry
        workflow_names = [workflow_info["name"] for workflow_info in workflow_files]

        # Base result with static analysis
        result = {
            "count": len(workflow_files),
            "classified": classified,
            "files": workflow_files,
            "workflow_names": workflow_names,
            "has_runtime_status": False,
        }

        # Try GitHub API integration if enabled and token available
        github_api_enabled = (
            self.config.get("extensions", {})
            .get("github_api", {})
            .get("enabled", False)
        )
        github_token = self.config.get("extensions", {}).get("github_api", {}).get(
            "token"
        ) or os.environ.get("CLASSIC_READ_ONLY_PAT_TOKEN")

        is_github_repo = self._is_github_repository(repo_path)

        self.logger.debug(
            f"GitHub API integration check for {repo_path.name}: "
            f"enabled={github_api_enabled}, has_token={bool(github_token)}, "
            f"github_org={self.github_org} (source={self.github_org_source}), is_github_repo={is_github_repo}"
        )

        # Validate prerequisites for GitHub API integration
        if github_api_enabled and not github_token:
            self.logger.warning(
                f"GitHub API enabled but token not available (CLASSIC_READ_ONLY_PAT_TOKEN). "
                f"Workflow status will not be queried for {repo_path.name}"
            )

        if (
            github_api_enabled
            and github_token
            and self.github_org
            and is_github_repo
        ):
            try:
                owner, repo_name = self._extract_github_repo_info(repo_path, self.github_org)
                self.logger.debug(
                    f"Attempting GitHub API query for {owner}/{repo_name}"
                )
                if owner and repo_name:
                    github_client = GitHubAPIClient(github_token)
                    github_status = (
                        github_client.get_repository_workflow_status_summary(
                            owner, repo_name
                        )
                    )

                    # Merge GitHub API data with static analysis
                    result["github_api_data"] = github_status
                    result["has_runtime_status"] = True
                    
                    self.logger.debug(
                        f"Retrieved GitHub workflow status for {owner}/{repo_name}"
                    )

                    # If no local workflows were found but GitHub has workflows, use GitHub as source
                    # This handles cases where Gerrit is primary but GitHub mirror has workflows
                    if not workflow_names and github_status.get("workflows"):
                        github_workflow_names = []
                        for workflow in github_status.get("workflows", []):
                            workflow_path = workflow.get("path", "")
                            if workflow_path:
                                file_name = os.path.basename(workflow_path)
                                github_workflow_names.append(file_name)

                        if github_workflow_names:
                            result["workflow_names"] = github_workflow_names
                            result["count"] = len(github_workflow_names)
                            self.logger.debug(
                                f"Using GitHub API as workflow source for {owner}/{repo_name}: {github_workflow_names}"
                            )

            except Exception as e:
                self.logger.warning(
                    f"Failed to fetch GitHub workflow status for {repo_path}: {e}"
                )

        return result

    def _check_github_mirror(self, repo_path: Path) -> dict[str, Any]:
        """Check if repository has a GitHub mirror that actually exists."""
        try:
            # First check if it looks like a GitHub repository
            has_github_indicators = self._is_github_repository(repo_path)

            if not has_github_indicators:
                return {
                    "exists": False,
                    "owner": "",
                    "repo": "",
                    "reason": "no_github_indicators",
                }

            # Check if the GitHub repository actually exists
            owner, repo_name = self._extract_github_repo_info(repo_path)
            if not owner or not repo_name:
                return {
                    "exists": False,
                    "owner": owner,
                    "repo": repo_name,
                    "reason": "cannot_determine_github_info",
                }

            # Verify the repository exists on GitHub
            exists = self._check_github_mirror_exists(repo_path)

            return {
                "exists": exists,
                "owner": owner,
                "repo": repo_name,
                "reason": "verified" if exists else "not_found_on_github",
            }

        except Exception as e:
            self.logger.debug(f"GitHub mirror check failed for {repo_path}: {e}")
            return {
                "exists": False,
                "owner": "",
                "repo": "",
                "reason": f"error: {str(e)}",
            }

    def _analyze_workflow_file(
        self, workflow_file: Path, verify_patterns: list[str], merge_patterns: list[str]
    ) -> dict[str, Any]:
        """Analyze a single workflow file for classification."""
        workflow_info: dict[str, Any] = {
            "name": workflow_file.name,
            "classification": "other",
            "triggers": [],
            "jobs": 0,
        }

        try:
            with open(workflow_file, "r", encoding="utf-8") as f:
                content = f.read().lower()
                filename_lower = workflow_file.name.lower()

                # Classification based on filename and content with scoring
                verify_score = 0
                merge_score = 0

                # Import regex for word boundary matching
                import re

                # Score verify patterns (filename matches count more)
                for pattern in verify_patterns:
                    pattern_lower = pattern.lower()
                    if pattern_lower in filename_lower:
                        verify_score += 3  # Higher weight for filename matches
                    elif re.search(r"\b" + re.escape(pattern_lower) + r"\b", content):
                        verify_score += 1

                # Score merge patterns (filename matches count more)
                for pattern in merge_patterns:
                    pattern_lower = pattern.lower()
                    if pattern_lower in filename_lower:
                        merge_score += 3  # Higher weight for filename matches
                    elif re.search(r"\b" + re.escape(pattern_lower) + r"\b", content):
                        merge_score += 1

                # Classify based on highest score
                if merge_score > verify_score:
                    workflow_info["classification"] = "merge"
                elif verify_score > 0:
                    workflow_info["classification"] = "verify"
                # else remains "other"

                # Extract basic info
                import re

                # Find triggers (on: section)
                trigger_matches = re.findall(r"on:\s*\n\s*-?\s*(\w+)", content)
                if trigger_matches:
                    workflow_info["triggers"] = trigger_matches
                else:
                    # Try alternative format
                    if "on: push" in content:
                        triggers_list = workflow_info["triggers"]
                        assert isinstance(triggers_list, list)
                        triggers_list.append("push")
                    if "on: pull_request" in content:
                        triggers_list = workflow_info["triggers"]
                        assert isinstance(triggers_list, list)
                        triggers_list.append("pull_request")

                # Count jobs
                job_matches = re.findall(r"^\s*(\w+):\s*$", content, re.MULTILINE)
                # Filter out common YAML keys that aren't jobs
                non_job_keys = {"on", "env", "defaults", "jobs", "name", "run-name"}
                jobs = [
                    job
                    for job in job_matches
                    if job not in non_job_keys and not job.startswith("step")
                ]
                workflow_info["jobs"] = len(set(jobs))  # Remove duplicates

        except (IOError, UnicodeDecodeError):
            # File couldn't be read, return basic info
            pass

        return workflow_info

    def _is_github_repository(self, repo_path: Path) -> bool:
        """Check if repository is hosted on GitHub by examining git remotes."""
        try:
            # Check for git directory
            git_dir = repo_path / ".git"
            if not git_dir.exists():
                return False

            # Read git config or remote files
            config_file = git_dir / "config"
            if config_file.exists():
                with open(config_file, "r") as f:
                    content = f.read()
                    # Check for GitHub remotes
                    if "github.com" in content.lower():
                        return True

            # For ONAP and other projects that are mirrored on GitHub,
            # check if they have GitHub workflows (indicates GitHub presence)
            workflows_dir = repo_path / ".github" / "workflows"
            if workflows_dir.exists() and any(workflows_dir.iterdir()):
                # If we have GitHub workflows, assume it's mirrored on GitHub
                return True

            return False
        except Exception:
            return False

    def _check_github_mirror_exists(self, repo_path: Path) -> bool:
        """Check if repository actually exists on GitHub by making an API call."""
        try:
            owner, repo_name = self._extract_github_repo_info(repo_path)
            if not owner or not repo_name:
                return False

            # Try to access GitHub API to verify repository exists
            github_token = self.config.get("extensions", {}).get("github_api", {}).get(
                "token"
            ) or os.environ.get("CLASSIC_READ_ONLY_PAT_TOKEN")

            if github_token:
                try:
                    github_client = GitHubAPIClient(github_token)
                    response = github_client.client.get(f"/repos/{owner}/{repo_name}")
                    return response.status_code == 200
                except Exception as e:
                    self.logger.debug(
                        f"GitHub API check failed for {owner}/{repo_name}: {e}"
                    )

            # Fallback: make a simple HTTP request without authentication
            try:
                import httpx

                with httpx.Client(timeout=10.0) as client:
                    response = client.get(
                        f"https://api.github.com/repos/{owner}/{repo_name}"
                    )
                    return response.status_code == 200
            except Exception as e:
                self.logger.debug(
                    f"GitHub repository existence check failed for {owner}/{repo_name}: {e}"
                )
                return False

        except Exception:
            return False

    def _extract_github_repo_info(self, repo_path: Path, github_org: str = "") -> tuple[str, str]:
        """Extract GitHub owner and repo name from git remote or configuration.
        
        Args:
            repo_path: Path to the repository
            github_org: GitHub organization name from configuration (for Gerrit mirrors)
            
        Returns:
            Tuple of (owner, repo_name)
        """
        try:
            git_dir = repo_path / ".git"
            config_file = git_dir / "config"

            if not config_file.exists():
                # For mirrored repos, use configured github_org
                return self._infer_github_info_from_path(repo_path, github_org)

            with open(config_file, "r") as f:
                content = f.read()

            # Look for GitHub remote URLs
            import re

            # Match both HTTPS and SSH formats
            patterns = [
                r"url = https://github\.com/([^/]+)/([^/\s]+)(?:\.git)?",
                r"url = git@github\.com:([^/]+)/([^/\s]+)(?:\.git)?",
            ]

            for pattern in patterns:
                match = re.search(pattern, content)
                if match:
                    owner, repo = match.groups()
                    # Clean up repo name
                    repo = repo.rstrip(".git")
                    return owner, repo

            # Fallback to path-based inference for mirrored repos
            return self._infer_github_info_from_path(repo_path, github_org)
        except Exception:
            return "", ""

    def _infer_github_info_from_path(self, repo_path: Path, github_org: str = "") -> tuple[str, str]:
        """Infer GitHub owner/repo from repository path for mirrored repos.
        
        For Gerrit repos mirrored to GitHub, the path structure is typically:
        ./gerrit.example.org/repo-name -> github_org/repo-name
        
        Args:
            repo_path: Path to the repository
            github_org: GitHub organization name from configuration
            
        Returns:
            Tuple of (owner, repo_name)
        """
        try:
            if not github_org:
                self.logger.debug(
                    f"Cannot infer GitHub info for {repo_path.name}: github_org not provided"
                )
                return "", ""
            
            # Get just the repository name from the path
            # For paths like ./gerrit.onap.org/aai/babel, we want "aai-babel"
            # For paths like ./gerrit.onap.org/simple-repo, we want "simple-repo"
            path_parts = repo_path.parts
            
            # Find the Gerrit host in the path (e.g., "gerrit.onap.org")
            gerrit_host_index = -1
            for i, part in enumerate(path_parts):
                if "gerrit" in part.lower() or "git" in part.lower():
                    gerrit_host_index = i
                    break
            
            if gerrit_host_index >= 0 and gerrit_host_index < len(path_parts) - 1:
                # Get all path components after the gerrit host
                repo_parts = path_parts[gerrit_host_index + 1:]
                if repo_parts:
                    # Join multi-level paths with hyphens
                    # e.g., ["aai", "babel"] -> "aai-babel"
                    repo_name = "-".join(repo_parts)
                    self.logger.debug(
                        f"Inferred GitHub repo: {github_org}/{repo_name} from path {repo_path}"
                    )
                    return github_org, repo_name
            
            # Fallback: use just the repo name
            repo_name = repo_path.name
            self.logger.debug(
                f"Using fallback GitHub repo: {github_org}/{repo_name} from path {repo_path}"
            )
            return github_org, repo_name

        except Exception as e:
            self.logger.debug(
                f"Failed to infer GitHub info for {repo_path}: {e}"
            )
            return "", ""

    def _determine_github_org(self) -> str:
        """Determine GitHub organization once at initialization.
        
        Priority order:
        1. GITHUB_ORG environment variable (from workflow matrix)
        2. config["github"] (from project config)
        3. config["extensions"]["github_api"]["github_org"] (from config)
        
        Returns:
            GitHub organization name, or empty string if not found
        """
        # Priority: GITHUB_ORG env var > config["github"] > config["extensions"]["github_api"]["github_org"]
        github_org = (
            os.environ.get("GITHUB_ORG", "") or
            self.config.get("github", "") or
            self.config.get("extensions", {})
            .get("github_api", {})
            .get("github_org", "")
        )
        
        return github_org

    def _derive_github_org_from_gerrit_host(self, gerrit_host: str) -> str:
        """Derive GitHub organization name from Gerrit hostname.
        
        For example:
        - gerrit.onap.org -> 'onap'
        - gerrit.o-ran-sc.org -> 'o-ran-sc'
        - git.opendaylight.org -> 'opendaylight'
        
        Args:
            gerrit_host: Gerrit hostname (e.g., "gerrit.onap.org")
            
        Returns:
            Derived GitHub organization name, or empty string if derivation fails
        """
        try:
            host_lower = gerrit_host.lower()
            
            # Remove 'gerrit.' or 'git.' prefix
            if host_lower.startswith('gerrit.'):
                remaining = gerrit_host[len('gerrit.'):]
            elif host_lower.startswith('git.'):
                remaining = gerrit_host[len('git.'):]
            else:
                return ""
            
            # Remove TLD suffix (.org, .io, .com, etc)
            # Split on '.' and take everything except the last part
            parts = remaining.split('.')
            if len(parts) >= 2:
                # Join all but the last part (TLD)
                github_org = '.'.join(parts[:-1])
                self.logger.debug(
                    f"Derived GitHub org '{github_org}' from hostname '{gerrit_host}'"
                )
                return github_org
            
            return ""
            
        except Exception as e:
            self.logger.debug(
                f"Failed to derive GitHub org from hostname {gerrit_host}: {e}"
            )
            return ""



    def _check_gitreview(self, repo_path: Path) -> dict[str, Any]:
        """Check for .gitreview configuration file."""
        gitreview_file = repo_path / ".gitreview"

        if not gitreview_file.exists():
            return {"present": False, "file": None, "config": {}}

        # Parse .gitreview file content
        config = {}
        try:
            with open(gitreview_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        config[key.strip()] = value.strip()
        except (IOError, UnicodeDecodeError):
            # File exists but couldn't be read
            pass

        return {"present": True, "file": ".gitreview", "config": config}


# =============================================================================
# AGGREGATION AND RANKING (Phase 4 - TODO)
# =============================================================================


class DataAggregator:
    """Handles aggregation of repository data into global summaries."""

    def __init__(self, config: dict[str, Any], logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger

    def aggregate_global_data(
        self, repo_metrics: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        Aggregate all repository metrics into global summaries.

        Performs comprehensive aggregation including:
        - Active/inactive classification
        - Author and organization rollups
        - Top/least active repository identification
        - Contributor leaderboards
        - Age distribution analysis
        """
        self.logger.info("Starting global data aggregation")

        # Debug: Analyze repository commit status
        self._analyze_repository_commit_status(repo_metrics)

        # Configuration values for unified activity status
        current_threshold = self.config.get("activity_thresholds", {}).get(
            "current_days", 365
        )
        active_threshold = self.config.get("activity_thresholds", {}).get(
            "active_days", 1095
        )

        # Primary time window for rankings (usually last_365_days)
        primary_window = "last_365_days"

        # Classify repositories by unified activity status
        current_repos = []
        active_repos = []
        inactive_repos = []

        total_commits = 0
        total_lines_added = 0
        no_commit_repos = []  # Separate list for repositories with no commits

        for repo in repo_metrics:
            days_since_last = repo.get("days_since_last_commit")

            # Count total commits and lines of code
            total_commits += repo.get("commit_counts", {}).get(primary_window, 0)
            total_lines_added += (
                repo.get("loc_stats", {}).get(primary_window, {}).get("added", 0)
            )

            # Check if repository has no commits at all (use the explicit flag)
            has_any_commits = repo.get("has_any_commits", False)

            if not has_any_commits:
                # Repository with no commits - separate category
                no_commit_repos.append(repo)
            else:
                # Repository has commits - categorize by unified activity status
                # Handle case where days_since_last_commit might be None
                if days_since_last is None:
                    # If we have commits but no days_since_last, treat as inactive
                    inactive_repos.append(repo)
                else:
                    activity_status = repo.get("activity_status", "inactive")

                    if activity_status == "current":
                        current_repos.append(repo)
                    elif activity_status == "active":
                        active_repos.append(repo)
                    else:
                        inactive_repos.append(repo)

        # Aggregate author and organization data
        self.logger.info("Computing author rollups")
        authors = self.compute_author_rollups(repo_metrics)

        self.logger.info("Computing organization rollups")
        organizations = self.compute_org_rollups(authors)

        # Build complete repository list (all repositories sorted by activity)
        # Combine all activity status repositories for comprehensive view
        all_repos = current_repos + active_repos + inactive_repos

        # Sort all repositories by commits in primary window (descending)
        all_repositories_by_activity = self.rank_entities(
            all_repos,
            f"commit_counts.{primary_window}",
            reverse=True,
            limit=None,  # No limit - show all repositories
        )

        # Keep separate lists for different activity statuses
        top_current = self.rank_entities(
            current_repos, f"commit_counts.{primary_window}", reverse=True, limit=None
        )

        top_active = self.rank_entities(
            active_repos, f"commit_counts.{primary_window}", reverse=True, limit=None
        )

        least_active = self.rank_entities(
            inactive_repos, "days_since_last_commit", reverse=True, limit=None
        )

        # Build contributor leaderboards
        top_contributors_commits = self.rank_entities(
            authors, f"commits.{primary_window}", reverse=True, limit=None
        )

        top_contributors_loc = self.rank_entities(
            authors, f"lines_net.{primary_window}", reverse=True, limit=None
        )

        # Build organization leaderboard
        top_organizations = self.rank_entities(
            organizations, f"commits.{primary_window}", reverse=True, limit=None
        )

        # Build comprehensive summaries
        summaries = {
            "counts": {
                "total_repositories": len(repo_metrics),
                "current_repositories": len(current_repos),
                "active_repositories": len(active_repos),
                "inactive_repositories": len(inactive_repos),
                "no_commit_repositories": len(no_commit_repos),
                "total_commits": total_commits,
                "total_lines_added": total_lines_added,
                "total_authors": len(authors),
                "total_organizations": len(organizations),
            },
            "activity_status_distribution": {
                "current": [
                    {
                        "gerrit_project": r.get("gerrit_project", "Unknown"),
                        "days_since_last_commit": r.get("days_since_last_commit")
                        if r.get("days_since_last_commit") is not None
                        else 999999,
                    }
                    for r in current_repos
                ],
                "active": [
                    {
                        "gerrit_project": r.get("gerrit_project", "Unknown"),
                        "days_since_last_commit": r.get("days_since_last_commit")
                        if r.get("days_since_last_commit") is not None
                        else 999999,
                    }
                    for r in active_repos
                ],
                "inactive": [
                    {
                        "gerrit_project": r.get("gerrit_project", "Unknown"),
                        "days_since_last_commit": r.get("days_since_last_commit")
                        if r.get("days_since_last_commit") is not None
                        else 999999,
                    }
                    for r in inactive_repos
                ],
            },
            "top_current_repositories": top_current,
            "top_active_repositories": top_active,
            "least_active_repositories": least_active,
            "all_repositories": all_repositories_by_activity,
            "no_commit_repositories": no_commit_repos,
            "top_contributors_commits": top_contributors_commits,
            "top_contributors_loc": top_contributors_loc,
            "top_organizations": top_organizations,
        }

        self.logger.info(
            f"Aggregation complete: {len(current_repos)} current, {len(active_repos)} active, {len(inactive_repos)} inactive, {len(no_commit_repos)} no-commit repositories"
        )
        self.logger.info(
            f"Found {len(authors)} authors across {len(organizations)} organizations"
        )

        return summaries

    def _analyze_repository_commit_status(
        self, repo_metrics: list[dict[str, Any]]
    ) -> None:
        """Diagnostic function to analyze repository commit status."""
        self.logger.info("=== Repository Analysis ===")

        total_repos = len(repo_metrics)
        repos_with_commits = 0
        repos_no_commits = 0

        sample_no_commit_repos: list[dict[str, Any]] = []

        for repo in repo_metrics:
            repo_name = repo.get("gerrit_project", "Unknown")
            commit_counts = repo.get("commit_counts", {})

            # Check if repository has any commits across all time windows
            has_commits = any(count > 0 for count in commit_counts.values())

            if has_commits:
                repos_with_commits += 1
            else:
                repos_no_commits += 1
                if (
                    len(sample_no_commit_repos) < 3
                ):  # Collect sample for detailed analysis
                    sample_no_commit_repos.append(
                        {"gerrit_project": repo_name, "commit_counts": commit_counts}
                    )

        self.logger.info(f"Total repositories: {total_repos}")
        self.logger.info(f"Repositories with commits: {repos_with_commits}")
        self.logger.info(f"Repositories with NO commits: {repos_no_commits}")

        if sample_no_commit_repos:
            self.logger.info("Sample repositories with NO commits:")
            for repo in sample_no_commit_repos:
                self.logger.info(f"  - {repo['gerrit_project']}")

    def compute_author_rollups(
        self, repo_metrics: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        Aggregate author metrics across all repositories.

        Merges author data by email address, summing metrics across all repos
        and tracking unique repositories touched per time window.
        """
        from collections import defaultdict

        author_aggregates: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "name": "",
                "email": "",
                "username": "",
                "domain": "",
                "repositories_touched": defaultdict(set),
                "commits": defaultdict(int),
                "lines_added": defaultdict(int),
                "lines_removed": defaultdict(int),
                "lines_net": defaultdict(int),
            }
        )

        # Aggregate across all repositories
        for repo in repo_metrics:
            repo_name = repo.get("gerrit_project", "unknown")

            # Process each author in this repository
            for author in repo.get("authors", []):
                email = author.get("email", "").lower().strip()
                if not email or email == "unknown@unknown":
                    continue

                # Initialize author info (first occurrence wins for name/username)
                if not author_aggregates[email]["name"]:
                    author_aggregates[email]["name"] = author.get("name", "")
                    author_aggregates[email]["email"] = email
                    author_aggregates[email]["username"] = author.get("username", "")
                    author_aggregates[email]["domain"] = author.get("domain", "")

                # Aggregate metrics for each time window
                for window_name in author.get("commits", {}):
                    repos_set = cast(
                        set[str],
                        author_aggregates[email]["repositories_touched"][window_name],
                    )
                    repos_set.add(repo_name)
                    author_aggregates[email]["commits"][window_name] += author.get(
                        "commits", {}
                    ).get(window_name, 0)
                    author_aggregates[email]["lines_added"][window_name] += author.get(
                        "lines_added", {}
                    ).get(window_name, 0)
                    author_aggregates[email]["lines_removed"][window_name] += (
                        author.get("lines_removed", {}).get(window_name, 0)
                    )
                    author_aggregates[email]["lines_net"][window_name] += author.get(
                        "lines_net", {}
                    ).get(window_name, 0)

        # Convert to list format and finalize repository counts
        authors: List[Dict[str, Any]] = []
        for email, data in author_aggregates.items():
            author_record = {
                "name": data["name"],
                "email": email,
                "username": data["username"],
                "domain": data["domain"],
                "commits": dict(data["commits"]),
                "lines_added": dict(data["lines_added"]),
                "lines_removed": dict(data["lines_removed"]),
                "lines_net": dict(data["lines_net"]),
                "repositories_touched": {
                    window: set(repos)
                    for window, repos in data["repositories_touched"].items()
                },
                "repositories_count": {
                    window: len(repos)
                    for window, repos in data["repositories_touched"].items()
                },
            }
            authors.append(author_record)

        self.logger.info(
            f"Aggregated {len(authors)} unique authors across repositories"
        )
        return authors

    def compute_org_rollups(
        self, authors: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """
        Aggregate organization metrics from author data.

        Groups authors by email domain and aggregates their contributions.
        """
        from collections import defaultdict

        org_aggregates: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "domain": "",
                "contributor_count": 0,
                "contributors": set(),
                "commits": defaultdict(int),
                "lines_added": defaultdict(int),
                "lines_removed": defaultdict(int),
                "lines_net": defaultdict(int),
                "repositories_count": defaultdict(set),
            }
        )

        # Aggregate by domain
        for author in authors:
            domain = author.get("domain", "").strip().lower()
            if not domain or domain in ["unknown", "localhost", ""]:
                continue

            org_aggregates[domain]["domain"] = domain
            contributors_set = cast(set[str], org_aggregates[domain]["contributors"])
            contributors_set.add(author.get("email", ""))

            # Sum metrics across all time windows
            for window_name in author.get("commits", {}):
                org_aggregates[domain]["commits"][window_name] += author.get(
                    "commits", {}
                ).get(window_name, 0)
                org_aggregates[domain]["lines_added"][window_name] += author.get(
                    "lines_added", {}
                ).get(window_name, 0)
                org_aggregates[domain]["lines_removed"][window_name] += author.get(
                    "lines_removed", {}
                ).get(window_name, 0)
                org_aggregates[domain]["lines_net"][window_name] += author.get(
                    "lines_net", {}
                ).get(window_name, 0)

                # Track unique repositories per organization
                author_repos = author.get("repositories_touched", {}).get(
                    window_name, set()
                )
                if author_repos:
                    repos_set = cast(
                        set[str],
                        org_aggregates[domain]["repositories_count"][window_name],
                    )
                    repos_set.update(author_repos)

        # Convert to list format
        organizations = []
        for domain, data in org_aggregates.items():
            org_record = {
                "domain": domain,
                "contributor_count": len(data["contributors"]),
                "commits": dict(data["commits"]),
                "lines_added": dict(data["lines_added"]),
                "lines_removed": dict(data["lines_removed"]),
                "lines_net": dict(data["lines_net"]),
                "repositories_count": {
                    window: len(repos)
                    for window, repos in data["repositories_count"].items()
                },
            }
            organizations.append(org_record)

        self.logger.info(
            f"Aggregated {len(organizations)} organizations from author domains"
        )
        return organizations

    def rank_entities(
        self,
        entities: list[dict[str, Any]],
        sort_key: str,
        reverse: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        Sort entities by a metric with deterministic tie-breaking.

        Primary sort by the specified metric, secondary sort by name for stability.
        Handles nested dictionary keys (e.g., "commits.last_365_days").
        """

        def get_sort_value(entity):
            """Extract sort value, handling nested keys."""
            if "." in sort_key:
                keys = sort_key.split(".")
                value = entity
                for key in keys:
                    value = value.get(key, 0) if isinstance(value, dict) else 0
            else:
                value = entity.get(sort_key, 0)

            # Handle None values with appropriate defaults based on the metric
            if value is None:
                if sort_key == "days_since_last_commit":
                    return 999999  # Very large number for very old/no commits
                else:
                    return 0  # Default for other metrics

            # Ensure numeric return value
            if not isinstance(value, (int, float)):
                return 0
            return value

        def get_name(entity):
            """Extract name for tie-breaking."""
            return (
                entity.get("name")
                or entity.get("gerrit_project")
                or entity.get("domain")
                or entity.get("email")
                or ""
            )

        # Sort with primary metric (reverse if specified) and secondary name (always ascending)
        if reverse:
            sorted_entities = sorted(
                entities, key=lambda x: (-get_sort_value(x), get_name(x))
            )
        else:
            sorted_entities = sorted(
                entities, key=lambda x: (get_sort_value(x), get_name(x))
            )

        if limit and limit > 0:
            return sorted_entities[:limit]

        return sorted_entities


# =============================================================================
# OUTPUT RENDERING (Phase 5 - TODO)
# =============================================================================


class ReportRenderer:
    """Handles rendering of aggregated data into various output formats."""

    def __init__(self, config: dict[str, Any], logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger

    def render_json_report(self, data: dict[str, Any], output_path: Path) -> None:
        """
        Write the canonical JSON report.

        TODO: Implement in Phase 5
        """
        self.logger.info(f"Writing JSON report to {output_path}")

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)

    def render_markdown_report(self, data: dict[str, Any], output_path: Path) -> str:
        """
        Generate Markdown report from JSON data.

        Creates structured Markdown with tables, emoji indicators, and formatted numbers.
        """
        self.logger.info(f"Generating Markdown report to {output_path}")

        markdown_content = self._generate_markdown_content(data)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(markdown_content)

        return markdown_content

    def render_html_report(self, markdown_content: str, output_path: Path) -> None:
        """
        Convert Markdown to HTML with embedded styling.

        Converts Markdown tables and formatting to proper HTML with CSS styling.
        """
        self.logger.info(f"Converting to HTML report at {output_path}")

        html_content = self._convert_markdown_to_html(markdown_content)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_content)

    def package_zip_report(self, output_dir: Path, project: str) -> Path:
        """
        Package all report outputs into a ZIP file.

        Creates a ZIP containing JSON, Markdown, HTML, and configuration files.
        """
        zip_path = output_dir / f"{project}_report_bundle.zip"
        self.logger.info(f"Creating ZIP package at {zip_path}")

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            # Add all files in the output directory
            for file_path in output_dir.iterdir():
                if file_path.is_file() and file_path != zip_path:
                    # Add to ZIP with relative path
                    arcname = f"reports/{project}/{file_path.name}"
                    zipf.write(file_path, arcname)
                    self.logger.debug(f"Added {file_path.name} to ZIP")

        return zip_path

    def _generate_markdown_content(self, data: dict[str, Any]) -> str:
        """Generate complete Markdown content from JSON data."""
        include_sections = self.config.get("output", {}).get("include_sections", {})

        sections = []

        # Title and metadata
        sections.append(self._generate_title_section(data))

        # Global summary
        sections.append(self._generate_summary_section(data))

        # Organizations (moved up)
        if include_sections.get("organizations", True):
            sections.append(self._generate_organizations_section(data))

        # Contributors (moved up)
        if include_sections.get("contributors", True):
            sections.append(self._generate_contributors_section(data))

        # Repository activity distribution (renamed)
        if include_sections.get("inactive_distributions", True):
            sections.append(self._generate_activity_distribution_section(data))

        # Combined repositories table (replaces separate active/inactive tables)
        sections.append(self._generate_all_repositories_section(data))

        # Repositories with no commits
        sections.append(self._generate_no_commit_repositories_section(data))

        # Repository feature matrix
        if include_sections.get("repo_feature_matrix", True):
            sections.append(self._generate_feature_matrix_section(data))

        # Deployed CI/CD jobs telemetry
        sections.append(self._generate_deployed_workflows_section(data))

        # Orphaned Jenkins jobs from archived projects
        sections.append(self._generate_orphaned_jobs_section(data))

        # Footer
        sections.append("Generated with ❤️ by Release Engineering")

        # Filter out empty sections to avoid unnecessary whitespace
        non_empty_sections = [section for section in sections if section.strip()]
        return "\n\n".join(non_empty_sections)

    def _generate_title_section(self, data: dict[str, Any]) -> str:
        """Generate title and metadata section."""
        project = data.get("project", "Repository Analysis")
        generated_at = data.get("generated_at", "")
        total_repos = (
            data.get("summaries", {}).get("counts", {}).get("total_repositories", 0)
        )
        current_repos = (
            data.get("summaries", {}).get("counts", {}).get("current_repositories", 0)
        )
        active_repos = (
            data.get("summaries", {}).get("counts", {}).get("active_repositories", 0)
        )
        total_authors = (
            data.get("summaries", {}).get("counts", {}).get("total_authors", 0)
        )

        # Format timestamp
        if generated_at:
            try:
                from datetime import datetime

                dt = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
                formatted_time = dt.strftime("%B %d, %Y at %H:%M UTC")
            except:
                formatted_time = generated_at
        else:
            formatted_time = "Unknown"

        return f"""# 📊 Gerrit Project Analysis Report: {project}

**Generated:** {formatted_time}
**Schema Version:** {data.get("schema_version", "1.0.0")}"""

    def _generate_summary_section(self, data: dict[str, Any]) -> str:
        """Generate global summary statistics section."""
        counts = data.get("summaries", {}).get("counts", {})

        total_repos = counts.get("total_repositories", 0)
        current_repos = counts.get("current_repositories", 0)
        active_repos = counts.get("active_repositories", 0)
        inactive_repos = counts.get("inactive_repositories", 0)
        no_commit_repos = counts.get("no_commit_repositories", 0)
        total_commits = counts.get("total_commits", 0)
        total_lines_added = counts.get("total_lines_added", 0)
        total_authors = counts.get("total_authors", 0)
        total_orgs = counts.get("total_organizations", 0)

        # Calculate percentages
        current_pct = (current_repos / total_repos * 100) if total_repos > 0 else 0
        active_pct = (active_repos / total_repos * 100) if total_repos > 0 else 0
        inactive_pct = (inactive_repos / total_repos * 100) if total_repos > 0 else 0
        no_commit_pct = (no_commit_repos / total_repos * 100) if total_repos > 0 else 0

        # Get configuration thresholds for definitions
        current_threshold = self.config.get("activity_thresholds", {}).get(
            "current_days", 365
        )
        active_threshold = self.config.get("activity_thresholds", {}).get(
            "active_days", 1095
        )

        return f"""## 📈 Global Summary

**✅ Current** commits within last {current_threshold} days
**☑️ Active** commits between {current_threshold}-{active_threshold} days
**🛑 Inactive** no commits in {active_threshold}+ days

| Metric | Count | Percentage |
|--------|-------|------------|
| Total Gerrit Projects | {self._format_number(total_repos)} | 100% |
| Current Gerrit Projects | {self._format_number(current_repos)} | {current_pct:.1f}% |
| Active Gerrit Projects | {self._format_number(active_repos)} | {active_pct:.1f}% |
| Inactive Gerrit Projects | {self._format_number(inactive_repos)} | {inactive_pct:.1f}% |
| No Apparent Commits | {self._format_number(no_commit_repos)} | {no_commit_pct:.1f}% |
| Total Commits | {self._format_number(total_commits)} | - |
| Total Lines of Code | {self._format_number(total_lines_added)} | - |"""

    def _generate_activity_distribution_section(self, data: dict[str, Any]) -> str:
        """Generate repository activity distribution section."""
        return ""  # This section is now disabled

    def _generate_activity_table(self, repos: list[dict[str, Any]]) -> str:
        """Generate activity table for inactive repositories."""
        if not repos:
            return "No repositories in this category."

        # Sort by days since last commit (descending)
        def sort_key(x):
            days = x.get("days_since_last_commit")
            return days if days is not None else 999999

        sorted_repos = sorted(repos, key=sort_key, reverse=True)

        lines = [
            "| Repository | Days Inactive | Last Commit Date |",
            "|------------|---------------|-------------------|",
        ]

        from datetime import datetime, timedelta

        for repo in sorted_repos:  # Show all repositories, not just top 20
            name = repo.get("gerrit_project", "Unknown")
            days = repo.get("days_since_last_commit")
            if days is None:
                days = 999999  # Very large number for repos with no commits
                date_str = "Unknown"
            else:
                # Calculate actual date
                last_activity_date = datetime.now() - timedelta(days=days)
                date_str = last_activity_date.strftime("%Y-%m-%d")
            lines.append(f"| {name} | {days:,} | {date_str} |")

        return "\n".join(lines)

    def _match_workflow_file_to_github_name(
        self, github_name: str, file_names: list[str]
    ) -> str:
        """
        Match GitHub workflow name to workflow file name.

        Args:
            github_name: Name from GitHub API
            file_names: List of workflow file names

        Returns:
            Matching file name or empty string if no match
        """
        # Direct match
        if github_name in file_names:
            return github_name

        # Try matching without extension
        github_base = github_name.lower().replace(" ", "-").replace("_", "-")

        for file_name in file_names:
            file_base = file_name.lower()
            # Remove .yml/.yaml extension
            if file_base.endswith(".yml"):
                file_base = file_base[:-4]
            elif file_base.endswith(".yaml"):
                file_base = file_base[:-5]

            # Try various matching strategies
            if (
                file_base == github_base
                or github_base in file_base
                or file_base in github_base
                or file_base.replace("-", "") == github_base.replace("-", "")
            ):
                return file_name

        # If no match found, return the first file name as fallback
        return file_names[0] if file_names else ""

    def _generate_all_repositories_section(self, data: dict[str, Any]) -> str:
        """Generate combined repositories table showing all Gerrit projects."""
        all_repos = data.get("summaries", {}).get("all_repositories", [])

        if not all_repos:
            return "## 📊 All Gerrit Repositories\n\nNo repositories found."

        # Get configuration for definitions
        current_threshold = self.config.get("activity_thresholds", {}).get(
            "current_days", 365
        )
        active_threshold = self.config.get("activity_thresholds", {}).get(
            "active_days", 1095
        )

        lines = [
            "## 📊 Gerrit Projects",
            "",
            "| Gerrit Project | Commits | LOC | Contributors | Days Inactive | Last Commit Date | Status |",
            "|----------------|---------|---------|--------------|---------------|------------------|--------|",
        ]

        for repo in all_repos:
            name = repo.get("gerrit_project", "Unknown")
            commits_1y = repo.get("commit_counts", {}).get("last_365_days", 0)
            loc_1y = repo.get("loc_stats", {}).get("last_365_days", {}).get("net", 0)
            contributors_1y = repo.get("unique_contributors", {}).get(
                "last_365_days", 0
            )
            days_since = repo.get("days_since_last_commit")
            if days_since is None:
                days_since = 999999  # Very large number for repos with no commits
            activity_status = repo.get("activity_status", "inactive")

            age_str = self._format_age(days_since)

            # Map activity status to display format (emoji only)
            status_map = {"current": "✅", "active": "☑️", "inactive": "🛑"}
            status = status_map.get(activity_status, "🛑")

            # Format days inactive
            days_inactive_str = f"{days_since:,}" if days_since < 999999 else "N/A"

            lines.append(
                f"| {name} | {commits_1y} | {int(loc_1y):+d} | {contributors_1y} | {days_inactive_str} | {age_str} | {status} |"
            )

        lines.extend(["", f"**Total:** {len(all_repos)} repositories"])
        return "\n".join(lines)

    def _generate_no_commit_repositories_section(self, data: dict[str, Any]) -> str:
        """Generate repositories with no commits section."""
        no_commit_repos = data.get("summaries", {}).get("no_commit_repositories", [])

        if not no_commit_repos:
            return ""  # Skip output entirely if no data

        lines = [
            "## 📝 Gerrit Projects with No Apparent Commits",
            "",
            "**WARNING:** All Gerrit projects/repositories should contain at least one commit, due to the initial repository creation automation writing initial template and configuration files. The report generation and parsing logic may need checking/debugging for the projects/repositories below.",
            "",
            "| Gerrit Project |",
            "|------------|",
        ]

        for repo in no_commit_repos:
            name = repo.get("gerrit_project", "Unknown")
            lines.append(f"| {name} |")

        lines.extend(
            [
                "",
                f"**Total:** {len(no_commit_repos)} Gerrit projects with no apparent commits",
            ]
        )
        return "\n".join(lines)

    def _determine_jenkins_job_status(self, job_data: dict[str, Any]) -> str:
        """
        Determine Jenkins job status for color coding.

        Now uses the standardized status and state fields if available, falls back to color/build interpretation.

        Args:
            job_data: Jenkins job data containing status, state, color and last_build info

        Returns:
            Status string: "success", "failure", "unstable", "building", "disabled", "unknown"
        """
        # Use standardized status if available
        if "status" in job_data and job_data["status"]:
            status = job_data["status"]
            # Check state for disabled jobs
            if "state" in job_data and job_data["state"] == "disabled":
                return "disabled"
            return status

        # Fallback to original color-based logic for compatibility
        color = job_data.get("color", "").lower()
        last_build = job_data.get("last_build", {})
        last_result = (last_build.get("result") or "").upper()

        # Handle animated colors (building)
        if color.endswith("_anime"):
            return "building"

        # Map Jenkins colors to status
        color_map = {
            "blue": "success",
            "green": "success",
            "red": "failure",
            "yellow": "unstable",
            "grey": "disabled",
            "disabled": "disabled",
            "aborted": "aborted",
        }

        # Try color first
        if color in color_map:
            return color_map[color]

        # Fallback to last build result
        result_map = {
            "SUCCESS": "success",
            "FAILURE": "failure",
            "UNSTABLE": "unstable",
            "ABORTED": "aborted",
        }

        if last_result in result_map:
            return result_map[last_result]

        return "unknown"

    def _determine_github_workflow_status(self, workflow_data: dict[str, Any]) -> str:
        """
        Determine GitHub workflow status for color coding.

        Now uses the standardized status field if available, falls back to conclusion/run_status interpretation.

        Args:
            workflow_data: Workflow data from GitHub API

        Returns:
            Status string: "success", "failure", "building", "no_runs", "unknown"
        """
        # Use standardized status if available (from workflow runs)
        # Note: "status" contains run results (success/failure), different from "state" (active/disabled)
        if (
            "status" in workflow_data
            and workflow_data["status"]
            and workflow_data["status"] != "unknown"
        ):
            status = workflow_data["status"]
            # Map building to in_progress for display compatibility
            if status == "building":
                return "in_progress"
            return status

        # Fallback to original logic for compatibility
        # Get the conclusion (final result) and run status (current execution state)
        conclusion = workflow_data.get("conclusion", "unknown")
        run_status = workflow_data.get("run_status", "unknown")

        # Handle in-progress workflows first
        if run_status in ("queued", "in_progress"):
            return "in_progress"

        # Handle completed workflows - map conclusion to our status
        if run_status == "completed":
            conclusion_map = {
                "success": "success",
                "failure": "failure",
                "neutral": "success",  # Treat neutral as success for display
                "cancelled": "cancelled",
                "skipped": "skipped",
                "timed_out": "failure",
                "action_required": "failure",
            }
            return conclusion_map.get(conclusion, "unknown")

        # Special case for no runs
        if conclusion == "no_runs":
            return "no_runs"

        return "unknown"

    def _apply_status_color_classes(
        self, item_name: str, status: str, item_type: str = "workflow"
    ) -> str:
        """
        Apply CSS classes for status color coding.

        Args:
            item_name: Name of the job/workflow
            status: Status string from determine_*_status functions
            item_type: "workflow" or "jenkins" for different styling if needed

        Returns:
            HTML string with appropriate CSS classes
        """
        # CSS class mapping for different statuses
        class_map = {
            "success": "status-success",
            "failure": "status-failure",
            "unstable": "status-warning",
            "building": "status-building",
            "in_progress": "status-in-progress",
            "disabled": "status-disabled",
            "aborted": "status-cancelled",
            "cancelled": "status-cancelled",
            "neutral": "status-neutral",
            "skipped": "status-skipped",
            "no_runs": "status-no-runs",
            "active": "status-success",
            "unknown": "status-unknown",
        }

        css_class = class_map.get(status, "status-unknown")
        return f'<span class="{css_class} {item_type}-status">{item_name}</span>'

    def _construct_github_workflow_url(
        self, gerrit_project: str, workflow_name: str
    ) -> str:
        """
        Construct GitHub source URL for a workflow file based on Gerrit project.

        Args:
            gerrit_project: Gerrit project name (e.g., "portal-ng/bff", "doc")
            workflow_name: Workflow file name (e.g., "ci.yaml")

        Returns:
            GitHub source URL for the workflow file
        """
        if not gerrit_project or not workflow_name:
            return ""

        # Convert Gerrit project name to GitHub repository name
        github_repo_name = self._gerrit_to_github_repo_name(gerrit_project)
        return f"https://github.com/onap/{github_repo_name}/blob/master/.github/workflows/{workflow_name}"

    def _construct_github_workflow_actions_url(
        self, gerrit_project: str, workflow_name: str
    ) -> str:
        """
        Construct GitHub Actions URL for a workflow based on Gerrit project.

        Args:
            gerrit_project: Gerrit project name (e.g., "portal-ng/bff", "doc")
            workflow_name: Workflow file name (e.g., "ci.yaml")

        Returns:
            GitHub Actions URL for the workflow
        """
        if not gerrit_project or not workflow_name:
            return ""

        # Convert Gerrit project name to GitHub repository name
        github_repo_name = self._gerrit_to_github_repo_name(gerrit_project)
        return f"https://github.com/onap/{github_repo_name}/actions/workflows/{workflow_name}"

    def _gerrit_to_github_repo_name(self, gerrit_project: str) -> str:
        """
        Convert Gerrit project name to GitHub repository name using ONAP naming conventions.

        Args:
            gerrit_project: Gerrit project name (e.g., "ccsdk/parent", "aai/babel")

        Returns:
            GitHub repository name (e.g., "ccsdk-parent", "aai-babel")
        """
        if not gerrit_project:
            return ""

        # Convert slashes to dashes for ONAP GitHub mirrors
        # e.g., "ccsdk/parent" -> "ccsdk-parent"
        #       "aai/babel" -> "aai-babel"
        #       "policy/apex-pdp" -> "policy-apex-pdp"
        return gerrit_project.replace("/", "-")

    def _generate_deployed_workflows_section(self, data: dict[str, Any]) -> str:
        """Generate deployed CI/CD jobs telemetry section with status color-coding."""
        repositories = data.get("repositories", [])

        if not repositories:
            return "## 🏁 Deployed CI/CD Jobs\n\nNo repositories found."

        # Collect repositories that have workflows or Jenkins jobs
        repos_with_cicd = []
        has_any_jenkins = False

        for repo in repositories:
            workflow_names = (
                repo.get("features", {}).get("workflows", {}).get("workflow_names", [])
            )
            jenkins_jobs = repo.get("jenkins", {}).get("jobs", [])
            jenkins_job_names = [
                job.get("name", "") for job in jenkins_jobs if job.get("name")
            ]

            if workflow_names or jenkins_job_names:
                repos_with_cicd.append(
                    {
                        "gerrit_project": repo.get("gerrit_project", "Unknown"),
                        "workflow_names": workflow_names,
                        "workflows_data": repo.get("features", {}).get(
                            "workflows", {}
                        ),  # Include workflow data for status
                        "features": repo.get(
                            "features", {}
                        ),  # Include full features data for github_mirror check
                        "jenkins_jobs": jenkins_jobs,  # Store full job data for status
                        "jenkins_job_names": jenkins_job_names,
                        "workflow_count": len(workflow_names),
                        "job_count": len(jenkins_job_names),
                    }
                )
                if jenkins_job_names:
                    has_any_jenkins = True

        if not repos_with_cicd:
            return "## 🏁 Deployed CI/CD Jobs\n\nNo CI/CD jobs detected in any repositories."

        # Calculate totals
        total_workflows = sum(repo["workflow_count"] for repo in repos_with_cicd)
        total_jenkins_jobs = sum(repo["job_count"] for repo in repos_with_cicd)

        # Build table header based on whether Jenkins jobs exist
        if has_any_jenkins:
            lines = [
                "## 🏁 Deployed CI/CD Jobs",
                "",
                f"**Total GitHub workflows:** {total_workflows}",
                f"**Total Jenkins jobs:** {total_jenkins_jobs}",
                "",
                "| Gerrit Project | GitHub Workflows | Workflow Count | Jenkins Jobs | Job Count |",
                "|----------------|-------------------|----------------|--------------|-----------|",
            ]
        else:
            lines = [
                "## 🏁 Deployed CI/CD Jobs",
                "",
                f"**Total GitHub workflows:** {total_workflows}",
                f"**Total Jenkins jobs:** {total_jenkins_jobs}",
                "",
                "| Gerrit Project | GitHub Workflows | Workflow Count | Job Count |",
                "|----------------|-------------------|----------------|-----------|",
            ]

        for repo in sorted(repos_with_cicd, key=lambda x: x["gerrit_project"]):
            name = repo["gerrit_project"]

            # Check if GitHub mirror exists for this repository
            github_mirror_info = repo.get("features", {}).get("github_mirror", {})

            # Add warning indicator if:
            # 1. Repository has GitHub workflows (so we'd be generating broken links)
            # 2. The mirror was explicitly checked on GitHub and not found (reason: "not_found_on_github")
            # Don't flag repos with "no_github_indicators" - they simply don't use GitHub at all
            has_workflows = len(repo.get("workflow_names", [])) > 0
            mirror_not_found = (
                github_mirror_info.get("exists") is False
                and github_mirror_info.get("reason") == "not_found_on_github"
            )

            if has_workflows and mirror_not_found:
                # Add warning symbol with CSS tooltip, but keep text normal color
                project_name = f'<span class="mirror-warning">⚠️<span class="tooltip-text">Not mirrored to GitHub</span></span> {name}'
                has_github_mirror = False
            else:
                project_name = name
                has_github_mirror = True

            # Build workflow names with color coding
            workflow_items = []
            workflows_data = repo.get("workflows_data", {})
            self.logger.debug(
                f"[workflows] Processing repo {name}: workflows_data keys={list(workflows_data.keys())}, has_runtime_status={workflows_data.get('has_runtime_status', 'MISSING')}, has_github_mirror={has_github_mirror}"
            )

            # Check if we have valid GitHub API data or should fall back to failure status
            has_github_api_data = workflows_data.get(
                "has_runtime_status", False
            ) and workflows_data.get("github_api_data", {}).get("workflows")

            if has_github_api_data:
                # Use GitHub API data for status-aware rendering
                github_workflows = workflows_data.get("github_api_data", {}).get(
                    "workflows", []
                )

                # Create a map of workflow file names to their execution status using path field
                workflow_status_map = {}
                import os

                for workflow in github_workflows:
                    workflow_path = workflow.get("path", "")
                    # Extract filename from path (e.g., ".github/workflows/ci.yaml" -> "ci.yaml")
                    if workflow_path:
                        file_name = os.path.basename(workflow_path)
                        if file_name in repo["workflow_names"]:
                            # Only process workflows that are enabled/active
                            if workflow.get("state") == "active":
                                status = self._determine_github_workflow_status(
                                    workflow
                                )
                                workflow_status_map[file_name] = status
                                self.logger.debug(
                                    f"[workflows] Path match status mapped: path={workflow_path} file={file_name} status={status}"
                                )
                            else:
                                # Disabled workflows get disabled status
                                workflow_status_map[file_name] = "disabled"
                                self.logger.debug(
                                    f"[workflows] Disabled workflow: path={workflow_path} file={file_name}"
                                )
                        else:
                            self.logger.debug(
                                f"[workflows] Path basename '{file_name}' not in local workflow_names {repo['workflow_names']} (repo={name})"
                            )

                # Fallback: attempt to map remaining workflows by GitHub display name when path-based mapping
                # did not cover all locally discovered workflow files (common with mirrored or renamed workflows)
                if github_workflows and len(workflow_status_map) < len(
                    repo["workflow_names"]
                ):
                    remaining = set(repo["workflow_names"]) - set(
                        workflow_status_map.keys()
                    )
                    if remaining:
                        self.logger.debug(
                            f"[workflows] Attempting name-based fallback mapping; unmapped local files: {sorted(remaining)} (repo={name})"
                        )
                        for workflow in github_workflows:
                            gh_name = workflow.get("name")
                            if not gh_name:
                                continue
                            matched_file = self._match_workflow_file_to_github_name(
                                gh_name, repo["workflow_names"]
                            )
                            if matched_file and matched_file not in workflow_status_map:
                                status = self._determine_github_workflow_status(
                                    workflow
                                )
                                workflow_status_map[matched_file] = status
                                self.logger.debug(
                                    f"[workflows] Fallback name match: github_name='{gh_name}' -> file='{matched_file}' status={status} (repo={name})"
                                )

                # If still nothing mapped, emit a single debug to aid diagnosis
                if (
                    github_workflows
                    and not workflow_status_map
                    and repo["workflow_names"]
                ):
                    self.logger.debug(
                        f"[workflows] No workflow runtime statuses mapped (possible API auth/visibility issue) repo={name} github_workflows={len(github_workflows)} local_files={repo['workflow_names']}"
                    )

                # If GitHub API returned no workflows but local files exist, assume API failure
                elif (
                    not github_workflows
                    and repo["workflow_names"]
                    and workflows_data.get("has_runtime_status", False)
                ):
                    self.logger.debug(
                        f"[workflows] GitHub API returned no workflows for {name}, defaulting to unknown status for local workflow files"
                    )
                    for workflow_name in repo["workflow_names"]:
                        workflow_status_map[workflow_name] = "unknown"

                # Build the list with status information and hyperlinks
                for workflow_name in sorted(repo["workflow_names"]):
                    status = workflow_status_map.get(workflow_name, "unknown")
                    colored_name = self._apply_status_color_classes(
                        workflow_name, status, "workflow"
                    )
                    self.logger.debug(
                        f"[workflows] Applied color to {workflow_name}: status={status}, colored_name={colored_name[:100]}..."
                    )

                    # Find the corresponding workflow data to get URLs
                    workflow_url = None
                    for workflow in github_workflows:
                        workflow_path = workflow.get("path", "")
                        if (
                            workflow_path
                            and os.path.basename(workflow_path) == workflow_name
                        ):
                            # Prefer workflow page URL for runs/status over source code URL
                            urls = workflow.get("urls", {})
                            workflow_url = urls.get("workflow_page")
                            break

                    # If no workflow URL found in GitHub API data, construct one using GitHub owner/repo info
                    if not workflow_url:
                        workflows_data = repo.get("workflows_data", {})
                        github_api_data = workflows_data.get("github_api_data", {})
                        github_owner = github_api_data.get("github_owner")
                        github_repo = github_api_data.get("github_repo")

                        if github_owner and github_repo:
                            # Use actual GitHub owner/repo from API data
                            workflow_url = f"https://github.com/{github_owner}/{github_repo}/actions/workflows/{workflow_name}"
                        elif repo.get("gerrit_project"):
                            # Fallback to constructed URL from Gerrit project
                            workflow_url = self._construct_github_workflow_actions_url(
                                repo["gerrit_project"], workflow_name
                            )

                    if workflow_url:
                        linked_name = f'<a href="{workflow_url}" target="_blank">{colored_name}</a>'
                        workflow_items.append(linked_name)
                    else:
                        workflow_items.append(colored_name)
            else:
                # Fallback when no GitHub API data is available
                workflows_data_workflows = workflows_data.get(
                    "github_api_data", {}
                ).get("workflows", [])
                for workflow_name in sorted(repo["workflow_names"]):
                    # For workflows that are expected to have status but GitHub API failed,
                    # default to unknown to indicate the monitoring is not working
                    default_status = "unknown"
                    colored_name = self._apply_status_color_classes(
                        workflow_name, default_status, "workflow"
                    )
                    self.logger.debug(
                        f"[workflows] Fallback color applied to {workflow_name}: status={default_status}, colored_name={colored_name[:100]}..."
                    )

                    # Try to find URL from workflows data even without runtime status
                    workflow_url = None
                    for workflow in workflows_data_workflows:
                        workflow_path = workflow.get("path", "")
                        if (
                            workflow_path
                            and os.path.basename(workflow_path) == workflow_name
                        ):
                            # Prefer workflow page URL for runs/status over source code URL
                            urls = workflow.get("urls", {})
                            workflow_url = urls.get("workflow_page")
                            break

                    # If no API URL, try to construct GitHub Actions URL using stored GitHub info
                    if not workflow_url:
                        workflows_data = repo.get("workflows_data", {})
                        github_api_data = workflows_data.get("github_api_data", {})
                        github_owner = github_api_data.get("github_owner")
                        github_repo = github_api_data.get("github_repo")

                        if github_owner and github_repo:
                            # Use actual GitHub owner/repo from API data
                            workflow_url = f"https://github.com/{github_owner}/{github_repo}/actions/workflows/{workflow_name}"
                        elif repo.get("gerrit_project"):
                            # Fallback to constructed URL from Gerrit project
                            workflow_url = self._construct_github_workflow_actions_url(
                                repo["gerrit_project"], workflow_name
                            )

                    # Only skip links/colors if the repo has workflows but mirror was not found on GitHub
                    if has_workflows and mirror_not_found:
                        # No GitHub mirror - just add plain text without links or color coding
                        workflow_items.append(workflow_name)
                    elif workflow_url:
                        linked_name = f'<a href="{workflow_url}" target="_blank">{colored_name}</a>'
                        workflow_items.append(linked_name)
                    else:
                        workflow_items.append(colored_name)

            workflow_names_str = "<br>".join(workflow_items) if workflow_items else ""

            # Build Jenkins job names with color coding based on status and hyperlinks
            jenkins_items = []
            for job in repo["jenkins_jobs"]:
                job_name = job.get("name", "Unknown")
                status = self._determine_jenkins_job_status(job)
                colored_name = self._apply_status_color_classes(
                    job_name, status, "jenkins"
                )

                # Get Jenkins job URL from URLs structure
                urls = job.get("urls", {})
                job_url = urls.get("job_page")

                if job_url:
                    linked_name = (
                        f'<a href="{job_url}" target="_blank">{colored_name}</a>'
                    )
                    jenkins_items.append(linked_name)
                else:
                    jenkins_items.append(colored_name)
            jenkins_names_str = "<br>".join(jenkins_items) if jenkins_items else ""

            workflow_count = repo["workflow_count"]
            job_count = repo["job_count"]

            if has_any_jenkins:
                lines.append(
                    f"| {project_name} | {workflow_names_str} | {workflow_count} | {jenkins_names_str} | {job_count} |"
                )
            else:
                lines.append(
                    f"| {project_name} | {workflow_names_str} | {workflow_count} | {job_count} |"
                )

        lines.extend(
            ["", f"**Total:** {len(repos_with_cicd)} repositories with CI/CD jobs"]
        )
        return "\n".join(lines)

    def _generate_contributors_section(self, data: dict[str, Any]) -> str:
        """Generate consolidated contributors table section."""
        top_commits = data.get("summaries", {}).get("top_contributors_commits", [])
        top_loc = data.get("summaries", {}).get("top_contributors_loc", [])
        total_authors = (
            data.get("summaries", {}).get("counts", {}).get("total_authors", 0)
        )

        sections = ["## 👥 Top Contributors (Last Year)"]
        sections.append(f"**Contributors Found:** {total_authors:,}")

        # Generate consolidated table with all contributors
        if top_commits or top_loc:
            sections.append(
                self._generate_consolidated_contributors_table(top_commits, top_loc)
            )
        else:
            sections.append("No contributor data available.")

        return "\n\n".join(sections)

    def _generate_consolidated_contributors_table(
        self, top_commits: list[dict[str, Any]], top_loc: list[dict[str, Any]]
    ) -> str:
        """Generate consolidated contributors table with commits, LOC, and average LOC per commit."""
        # Create a comprehensive list of all contributors from both lists
        contributors_dict = {}

        # Add contributors from commits list
        for contributor in top_commits:
            email = contributor.get("email", "")
            contributors_dict[email] = contributor.copy()

        # Merge data from LOC list
        for contributor in top_loc:
            email = contributor.get("email", "")
            if email in contributors_dict:
                # Update existing entry with LOC data
                contributors_dict[email].update(contributor)
            else:
                # Add new entry
                contributors_dict[email] = contributor.copy()

        # Convert back to list and sort by total activity (commits + normalized LOC)
        all_contributors = list(contributors_dict.values())

        # Sort by commits first, then by LOC as secondary sort
        all_contributors.sort(
            key=lambda x: (
                x.get("commits", {}).get("last_365_days", 0),
                x.get("lines_net", {}).get("last_365_days", 0),
            ),
            reverse=True,
        )

        if not all_contributors:
            return "No contributors found."

        # Create table headers
        lines = [
            "| Rank | Contributor | Commits | LOC | Δ LOC | Avg LOC/Commit | Repositories | Organization |",
            "|------|-------------|---------|-----|-------|----------------|--------------|--------------|",
        ]

        for i, contributor in enumerate(all_contributors, 1):
            name = contributor.get("name", "Unknown")
            email = contributor.get("email", "")
            domain = contributor.get("domain", "")
            commits_1y = contributor.get("commits", {}).get("last_365_days", 0)
            loc_1y = contributor.get("lines_net", {}).get("last_365_days", 0)
            lines_added_1y = contributor.get("lines_added", {}).get("last_365_days", 0)
            lines_removed_1y = contributor.get("lines_removed", {}).get(
                "last_365_days", 0
            )
            delta_loc_1y = abs(lines_added_1y) + abs(lines_removed_1y)
            repos_1y = contributor.get("repositories_count", {}).get("last_365_days", 0)

            # Calculate average LOC per commit
            if commits_1y > 0:
                avg_loc_per_commit = loc_1y / commits_1y
                avg_display = f"{avg_loc_per_commit:+.1f}"
            else:
                avg_display = "-"

            # Use just the name without email for privacy
            display_name = name

            org_display = domain if domain and domain != "unknown" else "-"

            lines.append(
                f"| {i} | {display_name} | {commits_1y} | {int(loc_1y):+d} | {delta_loc_1y} | {avg_display} | {repos_1y} | {org_display} |"
            )

        return "\n".join(lines)

    def _generate_contributors_table(
        self, contributors: list[dict[str, Any]], metric_type: str
    ) -> str:
        """Generate contributors table for commits or LOC."""
        if not contributors:
            return "No contributors found."

        if metric_type == "commits":
            lines = [
                "| Rank | Contributor | Commits | Repositories | Organization |",
                "|------|-------------|---------|--------------|--------------|",
            ]
        else:
            lines = [
                "| Rank | Contributor | LOC | Commits | Repositories | Organization |",
                "|------|-------------|---------|---------|--------------|--------------|",
            ]

        for i, contributor in enumerate(contributors, 1):
            name = contributor.get("name", "Unknown")
            email = contributor.get("email", "")
            domain = contributor.get("domain", "")
            commits_1y = contributor.get("commits", {}).get("last_365_days", 0)
            loc_1y = contributor.get("lines_net", {}).get("last_365_days", 0)
            repos_1y = contributor.get("repositories_count", {}).get("last_365_days", 0)

            # Use just the name without email for privacy
            display_name = name

            org_display = domain if domain and domain != "unknown" else "-"

            if metric_type == "commits":
                lines.append(
                    f"| {i} | {display_name} | {commits_1y} | {repos_1y} | {org_display} |"
                )
            else:
                lines.append(
                    f"| {i} | {display_name} | {int(loc_1y):+d} | {commits_1y} | {repos_1y} | {org_display} |"
                )

        return "\n".join(lines)

    def _generate_organizations_section(self, data: dict[str, Any]) -> str:
        """Generate organizations leaderboard section."""
        top_orgs = data.get("summaries", {}).get("top_organizations", [])

        if not top_orgs:
            return "## 🏢 Organizations\n\nNo organization data available."

        total_orgs = (
            data.get("summaries", {}).get("counts", {}).get("total_organizations", 0)
        )

        lines = ["## 🏢 Top Organizations (Last Year)"]
        lines.append(f"**Organizations Found:** {total_orgs:,}")
        lines.append("")
        lines.append(
            "| Rank | Organization | Contributors | Commits | LOC | Δ LOC | Avg LOC/Commit | Unique Repositories |"
        )
        lines.append(
            "|------|--------------|--------------|---------|-----|-------|----------------|---------------------|"
        )

        for i, org in enumerate(top_orgs, 1):
            domain = org.get("domain", "Unknown")
            contributors = org.get("contributor_count", 0)
            commits_1y = org.get("commits", {}).get("last_365_days", 0)
            loc_1y = org.get("lines_net", {}).get("last_365_days", 0)
            lines_added_1y = org.get("lines_added", {}).get("last_365_days", 0)
            lines_removed_1y = org.get("lines_removed", {}).get("last_365_days", 0)
            delta_loc_1y = abs(lines_added_1y) + abs(lines_removed_1y)
            repos_1y = org.get("repositories_count", {}).get("last_365_days", 0)

            # Calculate average LOC per commit
            if commits_1y > 0:
                avg_loc_per_commit = loc_1y / commits_1y
                avg_display = f"{avg_loc_per_commit:+.1f}"
            else:
                avg_display = "-"

            lines.append(
                f"| {i} | {domain} | {contributors} | {commits_1y} | {int(loc_1y):+d} | {delta_loc_1y} | {avg_display} | {repos_1y} |"
            )

        return "\n".join(lines)

    def _generate_feature_matrix_section(self, data: dict[str, Any]) -> str:
        """Generate repository feature matrix section."""
        repositories = data.get("repositories", [])

        if not repositories:
            return "## 🔧 Gerrit Project Feature Matrix\n\nNo projects analyzed."

        # Sort repositories by primary metric (commits in last year)
        sorted_repos = sorted(
            repositories,
            key=lambda r: r.get("commit_counts", {}).get("last_365_days", 0),
            reverse=True,
        )

        # Get activity thresholds for definition
        current_threshold = self.config.get("activity_thresholds", {}).get(
            "current_days", 365
        )
        active_threshold = self.config.get("activity_thresholds", {}).get(
            "active_days", 1095
        )

        lines = [
            "## 🔧 Gerrit Project Feature Matrix",
            "",
            "| Gerrit Project | Type | Dependabot | Pre-commit | ReadTheDocs | .gitreview | G2G | Status |",
            "|------------|------|------------|------------|-------------|------------|-----|--------|",
        ]

        for repo in sorted_repos:
            name = repo.get("gerrit_project", "Unknown")
            features = repo.get("features", {})
            activity_status = repo.get("activity_status", "inactive")

            # Extract feature status
            project_types = features.get("project_types", {})
            primary_type = project_types.get("primary_type", "unknown")

            dependabot = (
                "✅" if features.get("dependabot", {}).get("present", False) else "❌"
            )
            pre_commit = (
                "✅" if features.get("pre_commit", {}).get("present", False) else "❌"
            )
            readthedocs = (
                "✅" if features.get("readthedocs", {}).get("present", False) else "❌"
            )
            gitreview = (
                "✅" if features.get("gitreview", {}).get("present", False) else "❌"
            )
            g2g = "✅" if features.get("g2g", {}).get("present", False) else "❌"

            # Map activity status to display format (emoji only)
            status_map = {"current": "✅", "active": "☑️", "inactive": "🛑"}
            status = status_map.get(activity_status, "🛑")

            lines.append(
                f"| {name} | {primary_type} | {dependabot} | {pre_commit} | {readthedocs} | {gitreview} | {g2g} | {status} |"
            )

        return "\n".join(lines)

    def _generate_orphaned_jobs_section(self, data: dict[str, Any]) -> str:
        """Generate section for Jenkins jobs matched to archived/read-only Gerrit projects."""
        orphaned_data = data.get("orphaned_jenkins_jobs", {})

        if not orphaned_data or orphaned_data.get("total_orphaned_jobs", 0) == 0:
            return ""  # Don't show section if no orphaned jobs

        total_orphaned = orphaned_data.get("total_orphaned_jobs", 0)
        by_state = orphaned_data.get("by_state", {})
        jobs = orphaned_data.get("jobs", {})

        lines = [
            "## 🏚️ Orphaned Jenkins Jobs (Archived Projects)",
            "",
            f"**Total Orphaned Jobs:** {total_orphaned}",
            "",
            "These Jenkins jobs belong to archived or read-only Gerrit projects and should likely be removed:",
            "",
        ]

        # Summary by project state
        if by_state:
            lines.append("### Summary by Project State")
            lines.append("")
            for state, count in sorted(by_state.items()):
                lines.append(f"- **{state}:** {count} jobs")
            lines.append("")

        # Detailed table
        lines.extend(
            [
                "### Detailed Job Listing",
                "",
                "| Job Name | Gerrit Project | Project State | Match Score |",
                "|----------|----------------|---------------|-------------|",
            ]
        )

        # Sort jobs by project name for better organization
        sorted_jobs = sorted(jobs.items(), key=lambda x: x[1].get("project_name", ""))

        for job_name, job_info in sorted_jobs:
            project_name = job_info.get("project_name", "Unknown")
            state = job_info.get("state", "UNKNOWN")
            score = job_info.get("score", 0)

            # Color-code based on state
            if state == "READ_ONLY":
                state_display = f"🔒 {state}"
            elif state == "HIDDEN":
                state_display = f"👻 {state}"
            else:
                state_display = f"❓ {state}"

            lines.append(
                f"| `{job_name}` | `{project_name}` | {state_display} | {score} |"
            )

        lines.extend(
            [
                "",
                "**Recommendation:** Review these jobs and remove them if they are no longer needed, ",
                "since their associated Gerrit projects are archived or read-only.",
                "",
            ]
        )

        return "\n".join(lines)

    def _generate_appendix_section(self, data: dict[str, Any]) -> str:
        """Generate appendix with metadata and configuration."""
        # This method is no longer used - metadata section has been removed
        return ""

    def _convert_markdown_to_html(self, markdown_content: str) -> str:
        """Convert Markdown content to HTML with embedded CSS."""

        # Simple Markdown to HTML conversion
        html_body = self._simple_markdown_to_html(markdown_content)

        html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Gerrit Project Analysis Report</title>
    {self._get_datatable_css()}
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', sans-serif;
            line-height: 1.6;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background-color: #ffffff;
            color: #333333;
        }}

        h1, h2, h3 {{
            color: #2c3e50;
            margin-top: 2em;
            margin-bottom: 0.5em;
        }}

        h1 {{
            border-bottom: 3px solid #3498db;
            padding-bottom: 10px;
        }}

        h2 {{
            border-bottom: 2px solid #ecf0f1;
            padding-bottom: 5px;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 1em 0;
            font-size: 0.9em;
        }}

        th, td {{
            border: 1px solid #ddd;
            padding: 8px 12px;
            text-align: left;
        }}

        th {{
            background-color: #f8f9fa;
            font-weight: 600;
            color: #2c3e50;
        }}

        tr:nth-child(even) {{
            background-color: #f8f9fa;
        }}

        tr:hover {{
            background-color: #e8f4f8;
        }}

        code {{
            background-color: #f1f2f6;
            padding: 2px 4px;
            border-radius: 3px;
            font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
        }}

        .emoji {{
            font-size: 1.1em;
        }}

        .number {{
            font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
            font-weight: 500;
        }}

        .metadata {{
            background-color: #f8f9fa;
            padding: 15px;
            border-radius: 5px;
            margin: 1em 0;
            border-left: 4px solid #3498db;
        }}

        .footer {{
            text-align: center;
            margin-top: 3em;
            padding-top: 2em;
            border-top: 1px solid #ecf0f1;
            color: #7f8c8d;
        }}

        /* CI/CD Job Status Styling */
        .status-success {{
            color: #28a745;
            font-weight: 500;
        }}

        .status-failure {{
            color: #dc3545;
            font-weight: 500;
        }}

        .status-warning {{
            color: #ffc107;
            font-weight: 500;
        }}

        .status-building {{
            color: #007bff;
            font-weight: 500;
        }}

        .status-disabled {{
            color: #6c757d;
            font-style: italic;
        }}

        .status-cancelled {{
            color: #fd7e14;
            font-weight: 500;
        }}

        .status-unknown {{
            color: #6c757d;
        }}

        .status-in-progress {{
            color: #007bff;
            font-weight: 500;
        }}

        .status-neutral {{
            color: #6c757d;
            font-weight: 500;
        }}

        .status-skipped {{
            color: #6c757d;
            font-style: italic;
        }}

        .status-no-runs {{
            color: #6c757d;
            font-style: italic;
        }}

        /* Hover effects for better UX */
        .workflow-status:hover, .jenkins-status:hover {{
            text-decoration: underline;
            cursor: default;
        }}

        /* Tooltip for non-mirrored repositories */
        .mirror-warning {{
            cursor: help;
            position: relative;
            display: inline-block;
        }}

        .mirror-warning .tooltip-text {{
            visibility: hidden;
            width: 180px;
            background-color: #333;
            color: #fff;
            text-align: center;
            border-radius: 6px;
            padding: 8px;
            position: absolute;
            z-index: 1000;
            bottom: 125%;
            left: 50%;
            margin-left: -90px;
            opacity: 0;
            transition: opacity 0.3s;
            font-size: 13px;
            font-weight: normal;
            white-space: nowrap;
        }}

        .mirror-warning .tooltip-text::after {{
            content: "";
            position: absolute;
            top: 100%;
            left: 50%;
            margin-left: -5px;
            border-width: 5px;
            border-style: solid;
            border-color: #333 transparent transparent transparent;
        }}

        .mirror-warning:hover .tooltip-text {{
            visibility: visible;
            opacity: 1;
        }}

        /* Custom styles for Simple-DataTables integration */
        .dataTable-wrapper {{
            margin: 1em 0;
        }}

        .dataTable-top, .dataTable-bottom {{
            padding: 8px 0;
        }}

        .dataTable-search {{
            margin-bottom: 1em;
        }}

        .dataTable-search input {{
            padding: 8px 12px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 14px;
            width: 250px;
        }}

        .dataTable-selector select {{
            padding: 6px 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 14px;
        }}

        .dataTable-info {{
            color: #666;
            font-size: 14px;
        }}

        .dataTable-pagination a {{
            padding: 6px 12px;
            margin: 0 2px;
            border: 1px solid #ddd;
            border-radius: 4px;
            text-decoration: none;
            color: #2c3e50;
        }}

        .dataTable-pagination a:hover {{
            background-color: #e8f4f8;
        }}

        .dataTable-pagination a.active {{
            background-color: #3498db;
            color: white;
            border-color: #3498db;
        }}

        /* Custom column widths for specific tables */
        .feature-matrix-table th:nth-child(1) {{ width: 30%; }} /* Gerrit Project */
        .feature-matrix-table th:nth-child(2) {{ width: 12%; }} /* Type */
        .feature-matrix-table th:nth-child(3) {{ width: 12%; }} /* Dependabot */
        .feature-matrix-table th:nth-child(4) {{ width: 12%; }} /* Pre-commit */
        .feature-matrix-table th:nth-child(5) {{ width: 12%; }} /* ReadTheDocs */
        .feature-matrix-table th:nth-child(6) {{ width: 12%; }} /* .gitreview */
        .feature-matrix-table th:nth-child(7) {{ width: 10%; }} /* Status */

        /* CI/CD Jobs table - handles both 4 and 5 column layouts */
        .cicd-jobs-table th:nth-child(1) {{ width: 20%; }} /* Gerrit Project */
        .cicd-jobs-table th:nth-child(2) {{ width: 30%; }} /* GitHub Workflows */
        .cicd-jobs-table th:nth-child(3) {{ width: 15%; }} /* Workflow Count */
        .cicd-jobs-table th:nth-child(4) {{ width: 35%; }} /* Job Count (4-col) or Jenkins Jobs (5-col) */
        .cicd-jobs-table th:nth-child(5) {{ width: 15%; }} /* Job Count (5-col only) */
    </style>
</head>
<body>
    {html_body}
    {self._get_datatable_js()}
</body>
</html>"""

        return html_template

    def _simple_markdown_to_html(self, markdown: str) -> str:
        """Simple Markdown to HTML conversion for tables and headers."""
        import re

        html_lines = []
        lines = markdown.split("\n")
        in_table = False

        i = 0
        while i < len(lines):
            line = lines[i]

            # Headers
            if line.startswith("# "):
                content = line[2:].strip()
                html_lines.append(f'<h1 id="{self._slugify(content)}">{content}</h1>')
            elif line.startswith("## "):
                content = line[3:].strip()
                html_lines.append(f'<h2 id="{self._slugify(content)}">{content}</h2>')
            elif line.startswith("### "):
                content = line[4:].strip()
                html_lines.append(f'<h3 id="{self._slugify(content)}">{content}</h3>')

            # Tables
            elif "|" in line and line.strip():
                if not in_table:
                    # Check if this table will have headers by looking ahead
                    has_headers = i + 1 < len(lines) and re.match(
                        r"^\|[\s\-\|]+\|$", lines[i + 1].strip()
                    )
                    # Only add sortable class if feature is enabled and table has headers
                    sortable_enabled = self.config.get("html_tables", {}).get(
                        "sortable", True
                    )

                    # Check if this is the feature matrix table or combined repositories table by looking for specific headers
                    is_feature_matrix = False
                    is_cicd_jobs = False
                    is_all_repositories = False
                    is_global_summary = False
                    if has_headers and i < len(lines):
                        table_header = line.lower()
                        if (
                            "gerrit project" in table_header
                            and "dependabot" in table_header
                            and "pre-commit" in table_header
                        ):
                            is_feature_matrix = True
                        elif "gerrit project" in table_header and (
                            "github workflows" in table_header
                            or "jenkins jobs" in table_header
                        ):
                            is_cicd_jobs = True
                        elif (
                            "gerrit project" in table_header
                            and "commits" in table_header
                            and "status" in table_header
                        ):
                            is_all_repositories = True
                        elif (
                            "metric" in table_header
                            and "count" in table_header
                            and "percentage" in table_header
                        ):
                            is_global_summary = True

                    table_class = (
                        ' class="sortable"'
                        if (has_headers and sortable_enabled)
                        else ""
                    )
                    if is_feature_matrix:
                        table_class = (
                            ' class="sortable no-pagination feature-matrix-table"'
                        )
                    elif is_cicd_jobs:
                        table_class = ' class="sortable no-pagination cicd-jobs-table"'
                    elif is_all_repositories:
                        table_class = ' class="sortable"'
                    elif is_global_summary:
                        table_class = ' class="no-search no-pagination"'

                    html_lines.append(f"<table{table_class}>")
                    in_table = True

                # Check if this is a header separator line
                if re.match(r"^\|[\s\-\|]+\|$", line.strip()):
                    # Skip separator line
                    pass
                else:
                    # Regular table row
                    cells = [
                        cell.strip() for cell in line.split("|")[1:-1]
                    ]  # Remove empty first/last

                    # Determine if this is likely a header row (check next line)
                    is_header = i + 1 < len(lines) and re.match(
                        r"^\|[\s\-\|]+\|$", lines[i + 1].strip()
                    )

                    if is_header:
                        html_lines.append("<thead><tr>")
                        for cell in cells:
                            html_lines.append(f"<th>{cell}</th>")
                        html_lines.append("</tr></thead><tbody>")
                    else:
                        html_lines.append("<tr>")
                        for cell in cells:
                            html_lines.append(f"<td>{cell}</td>")
                        html_lines.append("</tr>")

            # End table when we hit a non-table line
            elif in_table and not ("|" in line and line.strip()):
                html_lines.append("</tbody></table>")
                in_table = False
                # Process this line normally
                if line.strip():
                    html_lines.append(f"<p>{line}</p>")
                else:
                    html_lines.append("")

            # Regular paragraphs
            elif line.strip() and not in_table:
                # Bold text
                line = re.sub(r"\*\*(.*?)\*\*", r"<strong>\1</strong>", line)
                # Code blocks
                line = re.sub(r"`(.*?)`", r"<code>\1</code>", line)
                html_lines.append(f"<p>{line}</p>")

            # Empty lines
            else:
                if not in_table:
                    html_lines.append("")

            i += 1

        # Close table if still open
        if in_table:
            html_lines.append("</tbody></table>")

        return "\n".join(html_lines)

    def _get_datatable_css(self) -> str:
        """Get Simple-DataTables CSS if sorting is enabled."""
        if not self.config.get("html_tables", {}).get("sortable", True):
            return ""

        return """
    <!-- Simple-DataTables CSS -->
    <link href="https://cdn.jsdelivr.net/npm/simple-datatables@latest/dist/style.css" rel="stylesheet" type="text/css">
    """

    def _get_datatable_js(self) -> str:
        """Get Simple-DataTables JavaScript if sorting is enabled."""
        if not self.config.get("html_tables", {}).get("sortable", True):
            return ""

        min_rows = self.config.get("html_tables", {}).get("min_rows_for_sorting", 3)
        searchable = str(
            self.config.get("html_tables", {}).get("searchable", True)
        ).lower()
        sortable = str(self.config.get("html_tables", {}).get("sortable", True)).lower()
        pagination = str(
            self.config.get("html_tables", {}).get("pagination", True)
        ).lower()
        per_page = self.config.get("html_tables", {}).get("entries_per_page", 50)
        page_options = self.config.get("html_tables", {}).get(
            "page_size_options", [20, 50, 100, 200]
        )

        return f"""
    <!-- Simple-DataTables JavaScript -->
    <script src="https://cdn.jsdelivr.net/npm/simple-datatables@latest" type="text/javascript"></script>
    <script>
        // Initialize Simple-DataTables on all tables with the sortable class
        document.addEventListener('DOMContentLoaded', function() {{
            const tables = document.querySelectorAll('table.sortable');
            tables.forEach(function(table) {{
                // Skip tables that are too small to benefit from sorting
                const rows = table.querySelectorAll('tbody tr');
                if (rows.length < {min_rows}) {{
                    return;
                }}

                // Check if this table should have pagination disabled
                const noPagination = table.classList.contains('no-pagination');
                const noSearch = table.classList.contains('no-search');
                const usePagination = noPagination ? false : {pagination};
                const useSearch = noSearch ? false : {searchable};

                new simpleDatatables.DataTable(table, {{
                    searchable: useSearch,
                    sortable: {sortable},
                    paging: usePagination,
                    perPage: {per_page},
                    perPageSelect: {page_options},
                    classes: {{
                        active: "active",
                        disabled: "disabled"
                    }},
                    labels: {{
                        placeholder: "Search repositories, contributors, etc...",
                        perPage: "entries per page",
                        noRows: "No entries found",
                        info: "Showing {{start}} to {{end}} of {{rows}} entries"
                    }}
                }});
            }});
        }});
    </script>"""

    def _slugify(self, text: str) -> str:
        """Convert text to URL-friendly slug."""
        import re

        # Remove emojis and special chars, convert to lowercase
        slug = re.sub(r"[^\w\s-]", "", text).strip().lower()
        slug = re.sub(r"[\s_-]+", "-", slug)
        return slug

    def _format_number(self, num: Union[int, float], signed: bool = False) -> str:
        """Format number with K/M/B abbreviation."""
        if not isinstance(num, (int, float)):
            return "0"

        # Handle negative numbers
        is_negative = num < 0
        abs_num = abs(num)

        if abs_num >= 1_000_000_000:
            formatted = f"{abs_num / 1_000_000_000:.1f}B"
        elif abs_num >= 1_000_000:
            formatted = f"{abs_num / 1_000_000:.1f}M"
        elif abs_num >= 1_000:
            formatted = f"{abs_num / 1_000:.1f}K"
        else:
            formatted = str(int(abs_num))

        # Add sign
        if is_negative:
            formatted = f"-{formatted}"
        elif signed and num > 0:
            formatted = f"+{formatted}"

        return formatted

    def _format_age(self, days: int) -> str:
        """Format age in days to actual date."""
        from datetime import datetime, timedelta

        if days is None or days == 999999:
            return "Unknown"

        # Calculate actual date
        date = datetime.now() - timedelta(days=days)
        return date.strftime("%Y-%m-%d")


# =============================================================================
# PACKAGING AND ZIP CREATION (Phase 5)
# =============================================================================


def save_resolved_config(config: Dict[str, Any], output_path: Path) -> None:
    """Save the resolved configuration to a JSON file."""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, default=str)


def create_report_bundle(
    project_output_dir: Path, project: str, logger: logging.Logger
) -> Path:
    """
    Package all report artifacts into a ZIP file.

    Bundles JSON, Markdown, HTML, and resolved config files.
    """
    logger.info(f"Creating report bundle for project {project}")

    zip_path = project_output_dir / f"{project}_report_bundle.zip"

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        # Add all files in the project output directory (except the ZIP itself)
        for file_path in project_output_dir.iterdir():
            if file_path.is_file() and file_path != zip_path:
                # Add to ZIP with relative path
                arcname = f"reports/{project}/{file_path.name}"
                zipf.write(file_path, arcname)
                logger.debug(f"Added {file_path.name} to ZIP")

    logger.info(f"Report bundle created: {zip_path}")
    return zip_path


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================


def format_number(value: Union[int, float], config: Dict[str, Any]) -> str:
    """Format numbers with optional abbreviation."""
    render_config = config.get("render", {})

    if not render_config.get("abbreviate_large_numbers", True):
        return str(value)

    threshold = render_config.get("large_number_threshold", 10000)

    if value >= threshold:
        if value >= 1_000_000:
            return f"{value / 1_000_000:.1f}M"
        elif value >= 1_000:
            return f"{value / 1_000:.1f}k"

    return str(value)


def format_age_days(days: int) -> str:
    """Format age in days to actual date."""
    from datetime import datetime, timedelta

    if days is None or days == 0:
        return datetime.now().strftime("%Y-%m-%d")

    # Calculate actual date
    date = datetime.now() - timedelta(days=days)
    return date.strftime("%Y-%m-%d")


def safe_git_command(
    cmd: list[str], cwd: Path | None, logger: logging.Logger
) -> tuple[bool, str]:
    """
    Execute a git command safely with error handling.

    Returns:
        (success: bool, output_or_error: str)
    """
    try:
        git_result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
        )
        return (
            git_result.returncode == 0,
            git_result.stdout.strip() or git_result.stderr.strip(),
        )
    except subprocess.CalledProcessError as e:
        logger.warning(f"Git command failed in {cwd}: {' '.join(cmd)} - {e.stderr}")
        return False, e.stderr
    except subprocess.TimeoutExpired:
        logger.error(f"Git command timed out in {cwd}: {' '.join(cmd)}")
        return False, "Command timed out"
    except Exception as e:
        logger.error(f"Unexpected error running git command in {cwd}: {e}")
        return False, str(e)


# =============================================================================
# MAIN ORCHESTRATION AND CLI ENTRY POINT
# =============================================================================


class RepositoryReporter:
    """Main orchestrator for repository reporting."""

    def __init__(self, config: dict[str, Any], logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger
        self.git_collector = GitDataCollector(config, {}, logger)
        self.feature_registry = FeatureRegistry(config, logger)
        self.aggregator = DataAggregator(config, logger)
        self.renderer = ReportRenderer(config, logger)
        self.info_master_temp_dir: Optional[str] = None

    def _cleanup_info_master_repo(self) -> None:
        """Clean up the temporary info-master repository directory."""
        if self.info_master_temp_dir and os.path.exists(self.info_master_temp_dir):
            try:
                self.logger.info(
                    f"Cleaning up info-master repository at {self.info_master_temp_dir}"
                )
                shutil.rmtree(self.info_master_temp_dir)
                self.logger.info("Successfully cleaned up info-master repository")
            except Exception as e:
                self.logger.warning(f"Failed to clean up info-master repository: {e}")

    def _clone_info_master_repo(self) -> Optional[Path]:
        """
        Clone the info-master repository for additional context data.

        Returns the path to the cloned repository in a temporary directory,
        or None if cloning failed.
        """
        # Create a temporary directory for info-master
        self.info_master_temp_dir = tempfile.mkdtemp(prefix="info-master-")
        info_master_path = Path(self.info_master_temp_dir) / "info-master"
        info_master_url = "ssh://modesevenindustrialsolutions@gerrit.linuxfoundation.org:29418/releng/info-master"

        self.logger.info(
            f"Cloning info-master repository to temporary location: {info_master_path}"
        )
        success, output = safe_git_command(
            ["git", "clone", info_master_url, str(info_master_path)],
            Path(self.info_master_temp_dir),
            self.logger,
        )

        if success:
            api_stats.record_info_master(True)
            self.logger.info("✅ Successfully cloned info-master repository")
            # Register cleanup handler
            atexit.register(self._cleanup_info_master_repo)
            return info_master_path
        else:
            error_msg = f"Clone failed: {output[:200]}" if output else "Clone failed"
            api_stats.record_info_master(False, error_msg)
            self.logger.error(f"❌ Failed to clone info-master repository: {output}")
            # Clean up the temp directory if clone failed
            if os.path.exists(self.info_master_temp_dir):
                shutil.rmtree(self.info_master_temp_dir)
            self.info_master_temp_dir = None
            return None

    def analyze_repositories(self, repos_path: Path) -> dict[str, Any]:
        """
        Main analysis workflow.

        TODO: Coordinate all phases
        """
        # Resolve to absolute path for consistent handling
        repos_path_abs = repos_path.resolve()
        self.logger.info(f"Starting repository analysis in {repos_path_abs}")

        # Clone info-master repository for additional context
        # This is cloned to a temporary directory to avoid it appearing in the report
        info_master_path = self._clone_info_master_repo()
        if info_master_path:
            self.logger.info(f"Info-master repository available at: {info_master_path}")
        else:
            self.logger.warning(
                "Info-master repository not available - continuing without it"
            )

        # Initialize data structure
        report_data = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "project": self.config["project"],
            "config_digest": compute_config_digest(self.config),
            "script_version": SCRIPT_VERSION,
            "time_windows": setup_time_windows(self.config),
            "repositories": [],
            "authors": [],
            "organizations": [],
            "summaries": {},
            "errors": [],
        }

        # Update git collector with time windows

        self.git_collector.time_windows = cast(
            dict[str, dict[str, Any]], report_data["time_windows"]
        )

        # Update git collector with repos_path for relative path calculation
        self.git_collector.repos_path = repos_path_abs

        # Find all repository directories
        repo_dirs = self._discover_repositories(repos_path_abs)
        self.logger.info(f"Found {len(repo_dirs)} repositories to analyze")

        # Analyze repositories (with concurrency)
        repo_metrics = self._analyze_repositories_parallel(repo_dirs)

        # Extract successful metrics and errors
        successful_repos = []
        for metrics in repo_metrics:
            if "error" in metrics:
                cast(list[dict[str, Any]], report_data["errors"]).append(metrics)
            else:
                # Extract the repository record with embedded author data
                successful_repos.append(metrics["repository"])

        report_data["repositories"] = successful_repos

        # Aggregate data (pass repository records directly)
        report_data["authors"] = self.aggregator.compute_author_rollups(
            successful_repos
        )
        report_data["organizations"] = self.aggregator.compute_org_rollups(
            report_data["authors"]
        )
        report_data["summaries"] = self.aggregator.aggregate_global_data(
            successful_repos
        )

        # Log comprehensive Jenkins job allocation summary for auditing
        if (
            self.git_collector.jenkins_client
            and self.git_collector._jenkins_initialized
        ):
            allocation_summary = self.git_collector.get_jenkins_job_allocation_summary()

            self.logger.info(f"Jenkins job allocation summary:")
            self.logger.info(
                f"  Total jobs: {allocation_summary['total_jenkins_jobs']}"
            )
            self.logger.info(f"  Allocated: {allocation_summary['allocated_jobs']}")
            self.logger.info(f"  Unallocated: {allocation_summary['unallocated_jobs']}")
            self.logger.info(
                f"  Allocation rate: {allocation_summary['allocation_percentage']}%"
            )

            # Validate allocation and report any issues
            validation_issues = self.git_collector.validate_jenkins_job_allocation()
            if validation_issues:
                self.logger.error("CRITICAL: Jenkins job allocation issues detected:")
                for issue in validation_issues:
                    self.logger.error(f"  - {issue}")

                # Infrastructure jobs are not fatal - only log as warning
                self.logger.warning(
                    "Some Jenkins jobs could not be allocated, but continuing with report generation"
                )

                # Get final counts for reporting
                allocation_summary = (
                    self.git_collector.get_jenkins_job_allocation_summary()
                )
                orphaned_summary = (
                    self.git_collector.get_orphaned_jenkins_jobs_summary()
                )

                total_jobs = allocation_summary.get("total_jenkins_jobs", 0)
                allocated_jobs = allocation_summary.get("allocated_jobs", 0)
                orphaned_jobs = orphaned_summary.get("total_orphaned_jobs", 0)

                self.logger.info(
                    f"Final Jenkins job allocation: {allocated_jobs}/{total_jobs} active, {orphaned_jobs} orphaned"
                )
            else:
                self.logger.info("Jenkins job allocation validation: No issues found")

            # Add allocation data to report for debugging
            report_data["jenkins_allocation"] = allocation_summary

            # Add orphaned jobs data to report
            orphaned_summary = self.git_collector.get_orphaned_jenkins_jobs_summary()
            report_data["orphaned_jenkins_jobs"] = orphaned_summary
            if orphaned_summary["total_orphaned_jobs"] > 0:
                self.logger.info(
                    f"Found {orphaned_summary['total_orphaned_jobs']} Jenkins jobs belonging to archived Gerrit projects"
                )
                for state, count in orphaned_summary["by_state"].items():
                    self.logger.info(f"  - {count} jobs for {state} projects")

        self.logger.info(
            f"Analysis complete: {len(report_data['repositories'])} repositories, {len(report_data['errors'])} errors"
        )

        return report_data

    def generate_reports(self, repos_path: Path, output_dir: Path) -> dict[str, Path]:
        """
        Generate complete reports (JSON, Markdown, HTML, ZIP).

        Returns paths to generated files.
        """
        # Ensure output directory exists
        output_dir.mkdir(parents=True, exist_ok=True)

        # Analyze repositories
        report_data = self.analyze_repositories(repos_path)

        # Define output paths
        project = self.config["project"]
        json_path = output_dir / "report_raw.json"
        markdown_path = output_dir / "report.md"
        html_path = output_dir / "report.html"
        config_path = output_dir / "config_resolved.json"

        generated_files = {}

        # Generate JSON report
        self.renderer.render_json_report(report_data, json_path)
        generated_files["json"] = json_path

        # Generate Markdown report
        markdown_content = self.renderer.render_markdown_report(
            report_data, markdown_path
        )
        generated_files["markdown"] = markdown_path

        # Generate HTML report (if not disabled)
        if not self.config.get("output", {}).get("no_html", False):
            self.renderer.render_html_report(markdown_content, html_path)
            generated_files["html"] = html_path

        # Save resolved configuration
        save_resolved_config(self.config, config_path)
        generated_files["config"] = config_path

        # Create ZIP bundle (if not disabled)
        if not self.config.get("output", {}).get("no_zip", False):
            zip_path = self.renderer.package_zip_report(output_dir, project)
            generated_files["zip"] = zip_path

        return generated_files

    def _discover_repositories(self, repos_path: Path) -> list[Path]:
        """Find all repository directories recursively with no artificial depth limit."""
        if not repos_path.exists():
            raise FileNotFoundError(f"Repository path does not exist: {repos_path}")

        self.logger.info(f"Discovering repositories recursively under: {repos_path}")

        repo_dirs: list[Path] = []
        access_errors = 0

        # Use rglob to discover all .git directories without a depth limit
        try:
            for git_dir in repos_path.rglob(".git"):
                try:
                    if git_dir.exists():
                        repo_dir = git_dir.parent

                        # Use relative path from repos_path for clean logging (fallback to absolute)
                        try:
                            rel_path = str(repo_dir.relative_to(repos_path))
                        except ValueError:
                            rel_path = str(repo_dir)

                        self.logger.debug(f"Found git repository: {rel_path}")

                        # Validate against Gerrit API cache if available
                        if getattr(self.git_collector, "gerrit_projects_cache", None):
                            if rel_path in self.git_collector.gerrit_projects_cache:
                                self.logger.debug(
                                    f"Verified {rel_path} exists in Gerrit"
                                )
                            else:
                                self.logger.warning(
                                    f"Repository {rel_path} not found in Gerrit API cache"
                                )

                        repo_dirs.append(repo_dir)
                except (PermissionError, OSError) as e:
                    access_errors += 1
                    self.logger.debug(
                        f"Cannot access potential repository at {git_dir}: {e}"
                    )
        except (PermissionError, OSError) as e:
            self.logger.warning(f"Error during repository discovery: {e}")

        # Deduplicate and sort results by path depth (deepest first) to ensure
        # child projects get processed before parent projects for Jenkins job allocation
        unique_repos = list({p.resolve() for p in repo_dirs})
        unique_repos.sort(key=lambda p: (-len(p.parts), str(p)))

        self.logger.info(f"Discovered {len(unique_repos)} git repositories")
        if access_errors:
            self.logger.debug(
                f"Encountered {access_errors} access errors during discovery"
            )

        return unique_repos

    def _analyze_repositories_parallel(
        self, repo_dirs: list[Path]
    ) -> list[dict[str, Any]]:
        """Analyze repositories with optional concurrency."""
        max_workers = self.config.get("performance", {}).get("max_workers", 8)

        if max_workers == 1:
            # Sequential processing
            return [self._analyze_single_repository(repo_dir) for repo_dir in repo_dirs]

        # Concurrent processing
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_repo = {
                executor.submit(self._analyze_single_repository, repo_dir): repo_dir
                for repo_dir in repo_dirs
            }

            for future in concurrent.futures.as_completed(future_to_repo):
                repo_dir = future_to_repo[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    self.logger.error(f"Failed to analyze {repo_dir.name}: {e}")
                    results.append(
                        {
                            "error": str(e),
                            "repo": repo_dir.name,
                            "category": "analysis_failure",
                        }
                    )

        return results

    def _analyze_single_repository(self, repo_path: Path) -> dict[str, Any]:
        """Analyze a single repository."""
        try:
            self.logger.debug(f"Analyzing repository: {repo_path.name}")

            # Collect Git metrics
            repo_metrics = self.git_collector.collect_repo_git_metrics(repo_path)

            # Scan features
            repo_features = self.feature_registry.detect_features(repo_path)
            repo_metrics["repository"]["features"] = repo_features

            return repo_metrics

        except Exception as e:
            self.logger.error(f"Error analyzing {repo_path.name}: {e}")
            return {
                "error": str(e),
                "repo": repo_path.name,
                "category": "repository_analysis",
            }


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Generate comprehensive repository analysis reports",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --project onap --repos-path /path/to/repos
  %(prog)s --project oran-sc --config-dir ./config --output-dir ./reports
  %(prog)s --project test --repos-path ./test-repos --no-html --verbose
        """,
    )

    # Required arguments
    parser.add_argument(
        "--project",
        required=True,
        help="Project name (used for config override and output naming)",
    )
    parser.add_argument(
        "--repos-path",
        required=True,
        type=Path,
        help="Path to directory containing cloned repositories",
    )

    # Optional configuration
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=DEFAULT_CONFIG_DIR,
        help=f"Configuration directory (default: {DEFAULT_CONFIG_DIR})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )

    # Output options
    parser.add_argument(
        "--no-html", action="store_true", help="Skip HTML report generation"
    )
    parser.add_argument(
        "--no-zip", action="store_true", help="Skip ZIP bundle creation"
    )

    # Behavioral options
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose logging"
    )
    parser.add_argument(
        "--cache", action="store_true", help="Enable caching of git metrics"
    )
    parser.add_argument(
        "--validate-only", action="store_true", help="Validate configuration and exit"
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override log level from configuration",
    )

    return parser.parse_args()


def write_config_to_step_summary(config: dict[str, Any], project: str) -> None:
    """Write configuration information to GitHub Step Summary."""
    step_summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if not step_summary_file:
        return

    try:
        current_days = config.get("activity_thresholds", {}).get("current_days", "N/A")
        active_days = config.get("activity_thresholds", {}).get("active_days", "N/A")

        with open(step_summary_file, "a") as f:
            f.write(f"\n## 📊 Configuration for {project}\n\n")
            f.write(f"- **Activity Thresholds:**\n")
            f.write(f"  - ✅ Current: {current_days} days\n")
            f.write(f"  - ☑️ Active: {active_days} days\n")
            f.write(f"  - 🛑 Inactive: {active_days}+ days\n")
            f.write(
                f"- **Schema Version:** {config.get('schema_version', 'Unknown')}\n"
            )
            f.write(
                f"- **Config Digest:** `{compute_config_digest(config)[:12]}...`\n\n"
            )
            
            # Validate GitHub API prerequisites
            github_api_enabled = config.get("extensions", {}).get("github_api", {}).get("enabled", False)
            github_token = config.get("extensions", {}).get("github_api", {}).get("token") or os.environ.get("CLASSIC_READ_ONLY_PAT_TOKEN")
            github_org = (
                os.environ.get("GITHUB_ORG", "") or
                config.get("github", "") or
                config.get("extensions", {}).get("github_api", {}).get("github_org", "")
            )
            
            f.write("### 🔧 GitHub API Integration Status\n\n")
            
            if github_api_enabled:
                f.write("- **Enabled:** ✅ Yes\n")
                
                # Check for token
                if github_token:
                    f.write("- **Token:** ✅ Present (CLASSIC_READ_ONLY_PAT_TOKEN)\n")
                else:
                    f.write("- **Token:** ❌ **MISSING** - Set `CLASSIC_READ_ONLY_PAT_TOKEN` secret\n")
                
                # Check for github org and show source
                if github_org:
                    if os.environ.get("GITHUB_ORG"):
                        f.write(f"- **GitHub Organization:** ✅ `{github_org}` (from GITHUB_ORG environment variable)\n")
                    elif config.get("github"):
                        f.write(f"- **GitHub Organization:** ✅ `{github_org}` (from config)\n")
                    else:
                        f.write(f"- **GitHub Organization:** ✅ `{github_org}` (from extensions config)\n")
                else:
                    f.write("- **GitHub Organization:** ❌ **NOT CONFIGURED**\n")
                    f.write("\n> **⚠️ WARNING:** GitHub organization not configured!\n")
                    f.write("> \n")
                    f.write(f"> Add `github` field to the `{project}` entry in `PROJECTS_JSON` variable.\n")
                    f.write("> \n")
                    f.write("> **Impact:** GitHub workflow status will NOT be queried.\n\n")
                
                # Overall status
                if github_token and github_org:
                    f.write("\n**Status:** ✅ GitHub API integration fully configured\n\n")
                else:
                    f.write("\n**Status:** ❌ GitHub API integration **DISABLED** due to missing prerequisites\n\n")
            else:
                f.write("- **Enabled:** ❌ No (disabled in configuration)\n\n")
                
    except Exception as e:
        # Silently fail - step summary is nice-to-have, not critical
        pass


def main() -> int:
    """Main entry point."""
    try:
        # Parse arguments
        args = parse_arguments()

        # Load configuration
        try:
            config = load_configuration(args.config_dir, args.project)
        except Exception as e:
            print(f"ERROR: Failed to load configuration: {e}", file=sys.stderr)
            return 1

        # Derive GitHub organization from repos_path if not configured
        github_org = (
            os.environ.get("GITHUB_ORG", "") or
            config.get("github", "") or
            config.get("extensions", {}).get("github_api", {}).get("github_org", "")
        )
        
        if not github_org:
            # Try to derive from repos_path (e.g., "./gerrit.onap.org" -> "onap")
            repos_path_str = str(args.repos_path)
            for part in args.repos_path.parts:
                part_lower = part.lower()
                if 'gerrit.' in part_lower or 'git.' in part_lower:
                    # Extract org from hostname
                    if part_lower.startswith('gerrit.'):
                        remaining = part[len('gerrit.'):]
                    elif part_lower.startswith('git.'):
                        remaining = part[len('git.'):]
                    else:
                        continue
                    
                    # Remove TLD (.org, .io, etc.)
                    parts = remaining.split('.')
                    if len(parts) >= 2:
                        github_org = '.'.join(parts[:-1])
                        print(f"ℹ️  Derived GitHub organization '{github_org}' from repository path", file=sys.stderr)
                        # Store in config so FeatureRegistry can use it
                        config["github"] = github_org
                        break

        # Override log level if specified
        if args.log_level:
            config.setdefault("logging", {})["level"] = args.log_level
        elif args.verbose:
            config.setdefault("logging", {})["level"] = "DEBUG"

        # Setup logging
        log_config = config.get("logging", {})
        logger = setup_logging(
            level=log_config.get("level", "INFO"),
            include_timestamps=log_config.get("include_timestamps", True),
        )

        logger.info(f"Repository Reporting System v{SCRIPT_VERSION}")
        logger.info(f"Project: {args.project}")
        logger.info(f"Configuration digest: {compute_config_digest(config)[:12]}...")

        # Write configuration to GitHub Step Summary
        write_config_to_step_summary(config, args.project)

        # Validate-only mode
        if args.validate_only:
            logger.info("Configuration validation successful")
            print(f"✅ Configuration valid for project '{args.project}'")
            print(f"   - Schema version: {config.get('schema_version', 'Unknown')}")
            print(f"   - Time windows: {list(config.get('time_windows', {}).keys())}")
            print(
                f"   - Features enabled: {len(config.get('features', {}).get('enabled', []))}"
            )
            return 0

        # Create output directory
        args.output_dir.mkdir(parents=True, exist_ok=True)
        project_output_dir = args.output_dir / args.project
        project_output_dir.mkdir(parents=True, exist_ok=True)

        # Initialize reporter
        reporter = RepositoryReporter(config, logger)

        # Analyze repositories
        report_data = reporter.analyze_repositories(args.repos_path)

        # Generate outputs
        json_path = project_output_dir / "report_raw.json"
        md_path = project_output_dir / "report.md"
        html_path = project_output_dir / "report.html"
        config_path = project_output_dir / "config_resolved.json"

        # Write JSON report
        reporter.renderer.render_json_report(report_data, json_path)

        # Generate Markdown report
        markdown_content = reporter.renderer.render_markdown_report(
            report_data, md_path
        )

        # Generate HTML report (unless disabled)
        if not args.no_html:
            reporter.renderer.render_html_report(markdown_content, html_path)

        # Write resolved configuration
        save_resolved_config(config, config_path)

        # Create ZIP bundle (unless disabled)
        if not args.no_zip:
            zip_path = create_report_bundle(project_output_dir, args.project, logger)

        # Print summary
        repo_count = len(report_data["repositories"])
        error_count = len(report_data["errors"])

        print(f"\n✅ Report generation completed successfully!")
        print(f"   - Analyzed: {repo_count} repositories")
        print(f"   - Errors: {error_count}")
        print(f"   - Output directory: {project_output_dir}")

        if error_count > 0:
            print(f"   - Check {json_path} for error details")

        # Print API statistics
        api_stats_output = api_stats.format_console_output()
        if api_stats_output:
            print(api_stats_output)

        # Write API statistics to GitHub Step Summary
        api_stats.write_to_step_summary()

        return 0

    except KeyboardInterrupt:
        print("\n❌ Operation cancelled by user", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"❌ Unexpected error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
