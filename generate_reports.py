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
import concurrent.futures
import copy
import datetime
import hashlib
import json
import logging
import os
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
    print("ERROR: PyYAML is required. Install with: pip install PyYAML", file=sys.stderr)
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
# LOGGING SETUP
# =============================================================================

def setup_logging(level: str = "INFO", include_timestamps: bool = True) -> logging.Logger:
    """Configure logging with structured format."""
    log_format = "[%(levelname)s]"
    if include_timestamps:
        log_format = "[%(asctime)s] " + log_format
    log_format += " %(message)s"

    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format=log_format,
        datefmt="%Y-%m-%d %H:%M:%S UTC" if include_timestamps else None
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
        with open(config_path, 'r', encoding='utf-8') as f:
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
    template_path = config_dir / "template.config"
    project_path = config_dir / f"{project}.config"

    # Load template (required)
    if not template_path.exists():
        raise FileNotFoundError(f"Template configuration not found: {template_path}")

    template_config = load_yaml_config(template_path)

    # Load project override (optional)
    project_config = load_yaml_config(project_path)

    # Deep merge
    merged_config = deep_merge_dicts(template_config, project_config)

    # Set project name
    merged_config["project"] = project

    return merged_config

def compute_config_digest(config: Dict[str, Any]) -> str:
    """Compute SHA256 digest of configuration for reproducibility tracking."""
    config_json = json.dumps(config, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(config_json.encode('utf-8')).hexdigest()

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
            test_paths = [redirect_path] + [p for p in self.COMMON_PATHS if p != redirect_path]
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

    def __init__(self, host: str, base_url: Optional[str] = None, timeout: float = 30.0):
        """Initialize Gerrit API client."""
        self.host = host
        self.timeout = timeout

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
            encoded_name = project_name.replace('/', '%2F')
            url = f"/projects/{encoded_name}?d"

            response = self.client.get(url)

            if response.status_code == 200:
                result = self._parse_json_response(response.text)
                return result
            elif response.status_code == 404:
                logging.debug(f"Project not found in Gerrit: {project_name}")
                return None
            else:
                logging.warning(f"Gerrit API returned {response.status_code} for project {project_name}")
                return None

        except Exception as e:
            logging.error(f"Exception fetching project info for {project_name}: {e}")
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
                result = self._parse_json_response(response.text)
                logging.info(f"Fetched {len(result)} projects from Gerrit")
                return result if isinstance(result, dict) else {}
            else:
                logging.error(f"Failed to fetch projects list: HTTP {response.status_code}")
                return {}

        except Exception as e:
            logging.error(f"Exception while fetching all projects: {e}")
            return {}


class JenkinsAPIClient:
    """Client for interacting with Jenkins REST API."""

    def __init__(self, host: str, timeout: float = 30.0):
        """Initialize Jenkins API client."""
        self.host = host
        self.timeout = timeout
        self.base_url = f"https://{host}"
        self.api_base_path = None  # Will be discovered

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
        if hasattr(self, 'client'):
            self.client.close()

    def _discover_api_base_path(self):
        """Discover the correct API base path for this Jenkins server."""
        # Common Jenkins API path patterns to try
        api_patterns = [
            "/api/json",
            "/releng/api/json",
            "/jenkins/api/json",
            "/ci/api/json",
            "/build/api/json"
        ]

        logging.info(f"Discovering Jenkins API base path for {self.host}")

        for pattern in api_patterns:
            try:
                test_url = f"{self.base_url}{pattern}?tree=jobs[name]"
                logging.debug(f"Testing Jenkins API path: {test_url}")

                response = self.client.get(test_url)
                if response.status_code == 200:
                    try:
                        data = response.json()
                        if "jobs" in data and isinstance(data["jobs"], list):
                            self.api_base_path = pattern
                            job_count = len(data["jobs"])
                            logging.info(f"Found working Jenkins API path: {pattern} ({job_count} jobs)")
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
        logging.warning(f"Could not discover Jenkins API path for {self.host}, using default: {self.api_base_path}")



    def get_all_jobs(self) -> dict[str, Any]:
        """Get all jobs from Jenkins."""
        if not self.api_base_path:
            logging.error(f"No valid API base path discovered for {self.host}")
            return {}

        try:
            url = f"{self.base_url}{self.api_base_path}?tree=jobs[name,url,color]"
            logging.info(f"Fetching Jenkins jobs from: {url}")
            response = self.client.get(url)

            logging.info(f"Jenkins API response: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                job_count = len(data.get("jobs", []))
                logging.info(f"Found {job_count} Jenkins jobs")
                return data
            else:
                logging.warning(f"Jenkins API returned {response.status_code} for {url}")
                logging.warning(f"Response text: {response.text[:500]}")
                return {}

        except Exception as e:
            logging.error(f"Exception fetching Jenkins jobs from {self.host}: {e}")
            return {}

    def get_jobs_for_project(self, project_name: str) -> list[dict[str, Any]]:
        """Get jobs related to a specific Gerrit project."""
        logging.debug(f"Looking for Jenkins jobs for project: {project_name}")
        all_jobs = self.get_all_jobs()
        project_jobs: list[dict[str, Any]] = []

        if "jobs" not in all_jobs:
            logging.debug(f"No 'jobs' key found in Jenkins API response for {project_name}")
            return project_jobs

        # Convert project name to job name format (replace / with -)
        project_job_name = project_name.replace("/", "-")
        logging.debug(f"Searching for Jenkins jobs matching pattern: {project_job_name}")

        total_jobs = len(all_jobs["jobs"])
        logging.debug(f"Checking {total_jobs} total Jenkins jobs for matches")

        for job in all_jobs["jobs"]:
            job_name = job.get("name", "")
            if project_job_name in job_name:
                logging.debug(f"Found matching Jenkins job: {job_name}")
                # Get detailed job info
                job_details = self.get_job_details(job_name)
                if job_details:
                    project_jobs.append(job_details)
                else:
                    logging.warning(f"Failed to get details for Jenkins job: {job_name}")

        logging.info(f"Found {len(project_jobs)} Jenkins jobs for project {project_name}")
        return project_jobs

    def get_job_details(self, job_name: str) -> dict[str, Any]:
        """Get detailed information about a specific job."""
        try:
            # Extract base path without /api/json suffix for job URLs
            base_path = self.api_base_path.replace('/api/json', '') if self.api_base_path else ''
            url = f"{self.base_url}{base_path}/job/{job_name}/api/json"
            response = self.client.get(url)

            if response.status_code == 200:
                job_data = response.json()

                # Get last build info
                last_build_info = self.get_last_build_info(job_name)

                return {
                    "name": job_name,
                    "url": job_data.get("url", ""),
                    "color": job_data.get("color", ""),
                    "buildable": job_data.get("buildable", False),
                    "description": job_data.get("description", ""),
                    "last_build": last_build_info
                }
            else:
                logging.debug(f"Jenkins job API returned {response.status_code} for {job_name}")
                return {}

        except Exception as e:
            logging.debug(f"Exception fetching job details for {job_name}: {e}")
            return {}

    def get_last_build_info(self, job_name: str) -> dict[str, Any]:
        """Get information about the last build of a job."""
        try:
            # Extract base path without /api/json suffix for job URLs
            base_path = self.api_base_path.replace('/api/json', '') if self.api_base_path else ''
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


# =============================================================================
# GIT DATA COLLECTION (Phase 2 - TODO)
# =============================================================================

class GitDataCollector:
    """Handles Git repository analysis and metric collection."""

    def __init__(self, config: dict[str, Any], time_windows: dict[str, dict[str, Any]], logger: logging.Logger) -> None:
        self.config = config
        self.time_windows = time_windows
        self.logger = logger
        self.cache_enabled = config.get("performance", {}).get("cache", False)
        self.cache_dir = None
        self.repos_path: Optional[Path] = None  # Will be set later for relative path calculation
        if self.cache_enabled:
            self.cache_dir = Path(tempfile.gettempdir()) / "repo_reporting_cache"
            self.cache_dir.mkdir(exist_ok=True)

        # Initialize Gerrit API client if configured
        self.gerrit_client = None
        self.gerrit_projects_cache: dict[str, dict[str, Any]] = {}  # Cache for all Gerrit project data
        gerrit_config = self.config.get("gerrit", {})

        # Initialize Jenkins API client if configured
        self.jenkins_client = None
        self.jenkins_jobs_cache: dict[str, list[dict[str, Any]]] = {}  # Cache for Jenkins job data

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
                    self.logger.error(f"Failed to initialize Gerrit API client for {host}: {e}")
            else:
                self.logger.error("Gerrit enabled but no host configured")

        # Initialize Jenkins client
        if jenkins_host:
            # Environment variable takes precedence - enables Jenkins integration
            timeout = jenkins_config.get("timeout", 30.0)
            try:
                self.jenkins_client = JenkinsAPIClient(jenkins_host, timeout)
                self.logger.info(f"Initialized Jenkins API client for {jenkins_host} (from environment)")
                # Test the connection
                test_jobs = self.jenkins_client.get_all_jobs()
                job_count = len(test_jobs.get("jobs", []))
                self.logger.info(f"Jenkins connection test: found {job_count} total jobs on {jenkins_host}")
            except Exception as e:
                self.logger.error(f"Failed to initialize Jenkins API client for {jenkins_host}: {e}")
                self.jenkins_client = None
        elif jenkins_config.get("enabled", False):
            # Fallback to config file (for backward compatibility)
            host = jenkins_config.get("host")
            timeout = jenkins_config.get("timeout", 30.0)

            if host:
                try:
                    self.jenkins_client = JenkinsAPIClient(host, timeout)
                    self.logger.info(f"Initialized Jenkins API client for {host} (from config)")
                except Exception as e:
                    self.logger.error(f"Failed to initialize Jenkins API client for {host}: {e}")
            else:
                self.logger.error("Jenkins enabled but no host configured")

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
                if part.startswith('gerrit-repos-'):
                    if i < len(path_parts) - 1:
                        project_path_parts = path_parts[i + 1:]
                        gerrit_project = '/'.join(project_path_parts)
                        self.logger.debug(f"Extracted Gerrit project from gerrit-repos pattern: {gerrit_project}")
                        return gerrit_project
                    break

            # Strategy 2: Look for hostname pattern (gerrit.domain.tld)
            for i, part in enumerate(path_parts):
                if '.' in part and any(tld in part for tld in ['.org', '.com', '.net', '.io']):
                    if i < len(path_parts) - 1:
                        project_path_parts = path_parts[i + 1:]
                        gerrit_project = '/'.join(project_path_parts)
                        self.logger.debug(f"Extracted Gerrit project from hostname pattern: {gerrit_project}")
                        return gerrit_project
                    break

            # Strategy 3: Look for organization root directories and extract relative path
            # Common organization names in paths
            org_names = ['onap', 'o-ran-sc', 'opendaylight', 'fdio', 'opnfv', 'agl']

            for i, part in enumerate(path_parts):
                if part.lower() in org_names:
                    # Found organization root, extract everything after it
                    if i < len(path_parts) - 1:
                        project_path_parts = path_parts[i + 1:]
                        gerrit_project = '/'.join(project_path_parts)
                        self.logger.debug(f"Extracted Gerrit project from organization root '{part}': {gerrit_project}")
                        return gerrit_project
                    break

            # Strategy 4: Check if any parent directories suggest hierarchical structure
            # Look for common Gerrit project patterns (2+ levels deep)
            # Filter out root directory from path_parts
            meaningful_parts = [part for part in path_parts if part and part != '/']
            if len(meaningful_parts) >= 3:
                # Take last 2-4 path components as potential project hierarchy
                for depth in range(4, 1, -1):  # Try 4, 3, 2 components
                    if len(meaningful_parts) >= depth:
                        potential_project = '/'.join(meaningful_parts[-depth:])
                        # Validate it looks_project

            # Fallback: use just the repository folder name
            self.logger.debug(f"No hierarchical structure detected, using folder name: {repo_path.name}")
            return repo_path.name

        except Exception as e:
            self.logger.warning(f"Error extracting Gerrit project from {repo_path}: {e}")
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
                if '.' in part and any(tld in part for tld in ['.org', '.com', '.net', '.io']):
                    hostname = part
                    if i < len(path_parts) - 1:
                        project_parts = path_parts[i + 1:]
                        gerrit_url = f"{hostname}/{'/'.join(project_parts)}"
                        self.logger.debug(f"Derived Gerrit URL: {gerrit_url}")
                        return gerrit_url
                    else:
                        return hostname

            # Fallback: construct generic URL with repo name only (avoid recursive issues)
            repo_name = repo_path.name
            fallback_url = f"unknown-gerrit-host/{repo_name}"
            self.logger.warning(f"Could not detect Gerrit hostname, using fallback: {fallback_url}")
            return fallback_url

        except Exception as e:
            self.logger.warning(f"Error deriving Gerrit URL from {repo_path}: {e}")
            return str(repo_path)

    def _extract_gerrit_host(self, repo_path: Path) -> str:
        """Extract the Gerrit hostname from the repository path."""
        try:
            path_parts = repo_path.parts
            for part in path_parts:
                if '.' in part and any(tld in part for tld in ['.org', '.com', '.net', '.io']):
                    return part
            return "unknown-gerrit-host"
        except Exception as e:
            self.logger.warning(f"Error extracting Gerrit host from {repo_path}: {e}")
            return "unknown-gerrit-host"



    def __del__(self):
        """Cleanup Gerrit client when GitDataCollector is destroyed."""
        if hasattr(self, 'gerrit_client') and self.gerrit_client:
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

        self.logger.debug(f"Collecting Git metrics for Gerrit project: {gerrit_project}")

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
                "total_commits_ever": 0,   # Total commits across all history
                "commit_counts": {window: 0 for window in self.time_windows},
                "loc_stats": {window: {"added": 0, "removed": 0, "net": 0} for window in self.time_windows},
                "unique_contributors": {window: set() for window in self.time_windows},  # type: ignore
                "features": {},
            },
            "authors": {},  # email -> author metrics
            "errors": []  # List[str]
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
                "git", "log", "--numstat", "--date=iso", "--pretty=format:%H|%ad|%an|%ae|%s"
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
                repo_data["jenkins"] = {
                    "jobs": jenkins_jobs,
                    "job_count": len(jenkins_jobs),
                    "has_jobs": len(jenkins_jobs) > 0
                }
            unique_contributors = repo_data["unique_contributors"]
            for window in self.time_windows:
                contributor_set = unique_contributors[window]
                assert isinstance(contributor_set, set)
                unique_contributors[window] = len(contributor_set)

            self.logger.debug(f"Collected {len(commits_data)} commits for {gerrit_project}")

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
        """Get Jenkins jobs for a specific repository."""
        if not self.jenkins_client:
            self.logger.debug(f"No Jenkins client available for {repo_name}")
            return []

        try:
            jobs = self.jenkins_client.get_jobs_for_project(repo_name)
            if jobs:
                self.logger.debug(f"Found {len(jobs)} Jenkins jobs for {repo_name}: {[job.get('name') for job in jobs]}")
            else:
                self.logger.debug(f"No Jenkins jobs found for {repo_name}")
            return jobs
        except Exception as e:
            self.logger.warning(f"Error fetching Jenkins jobs for {repo_name}: {e}")
            return []

    def bucket_commit_into_windows(self, commit_datetime: datetime.datetime, time_windows: dict[str, dict[str, Any]]) -> List[str]:
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
            unknown_placeholder = self.config.get("data_quality", {}).get("unknown_email_placeholder", "unknown@unknown")
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

    def _parse_git_log_output(self, git_output: str, repo_name: str) -> List[Dict[str, Any]]:
        """
        Parse git log output into structured commit data.

        Expected format from git log --numstat --date=iso --pretty=format:%H|%ad|%an|%ae|%s
        """
        commits = []
        lines = git_output.strip().split('\n')
        current_commit = None

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Check if this is a commit header line (contains |)
            if '|' in line and len(line.split('|')) >= 5:
                # Save previous commit if exists
                if current_commit:
                    commits.append(current_commit)

                # Parse commit header: hash|date|author_name|author_email|subject
                parts = line.split('|', 4)
                try:
                    commit_date = datetime.datetime.fromisoformat(parts[1].replace(' ', 'T'))
                    if commit_date.tzinfo is None:
                        commit_date = commit_date.replace(tzinfo=datetime.timezone.utc)
                except (ValueError, IndexError):
                    self.logger.warning(f"Invalid date format in {repo_name}: {parts[1] if len(parts) > 1 else 'unknown'}")
                    continue

                current_commit = {
                    "hash": parts[0],
                    "date": commit_date,
                    "author_name": parts[2],
                    "author_email": parts[3],
                    "subject": parts[4] if len(parts) > 4 else "",
                    "files_changed": []
                }
            else:
                # Parse numstat lines (format: added<tab>removed<tab>filename)
                parts = line.split('\t')
                if len(parts) >= 3 and current_commit:
                    try:
                        # Handle binary files (marked with -)
                        added = 0 if parts[0] == '-' else int(parts[0])
                        removed = 0 if parts[1] == '-' else int(parts[1])
                        filename = parts[2]

                        # Skip binary files if configured
                        if self.config.get("data_quality", {}).get("skip_binary_changes", True):
                            if parts[0] == '-' or parts[1] == '-':
                                continue

                        files_changed = current_commit["files_changed"]
                        assert isinstance(files_changed, list)
                        files_changed.append({
                            "filename": filename,
                            "added": added,
                            "removed": removed,
                        })
                    except (ValueError, IndexError):
                        # Skip malformed lines
                        continue

        # Don't forget the last commit
        if current_commit:
            commits.append(current_commit)

        return commits

    def _update_commit_metrics(self, commit: dict[str, Any], metrics: dict[str, Any]) -> None:
        """Process a single commit into the metrics structure."""
        applicable_windows = self.bucket_commit_into_windows(commit["date"], self.time_windows)

        # Normalize author identity
        norm_name, norm_email = self.normalize_author_identity(commit["author_name"], commit["author_email"])
        author_email = norm_email

        # Create author info dict for compatibility
        author_info = {
            "name": norm_name,
            "email": norm_email,
            "username": norm_name.split()[0] if norm_name else "",
            "domain": norm_email.split("@")[-1] if "@" in norm_email else ""
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
                "loc_stats": {window: {"added": 0, "removed": 0, "net": 0} for window in self.time_windows},
                "repositories": {window: set() for window in self.time_windows},  # type: ignore
            }

        # Update author metrics for each matching window
        author_metrics = metrics["authors"][author_email]
        for window in applicable_windows:
            author_metrics["commit_counts"][window] += 1
            author_metrics["loc_stats"][window]["added"] += total_added
            author_metrics["loc_stats"][window]["removed"] += total_removed
            author_metrics["loc_stats"][window]["net"] += net_lines
            author_metrics["repositories"][window].add(metrics["repository"]["gerrit_project"])

    def _finalize_repo_metrics(self, metrics: dict[str, Any], repo_name: str) -> None:
        """Finalize repository metrics after processing all commits."""
        repo_metrics = metrics["repository"]

        # Check if repository has any commits at all
        if repo_metrics.get("has_any_commits", False):
            # Repository has commits - find last commit date
            git_command = ["git", "log", "-1", "--date=iso", "--pretty=format:%ad"]
            success, output = safe_git_command(git_command, Path(repo_metrics["local_path"]), self.logger)

            if success and output.strip():
                try:
                    last_commit_date = datetime.datetime.fromisoformat(output.strip().replace(' ', 'T'))
                    if last_commit_date.tzinfo is None:
                        last_commit_date = last_commit_date.replace(tzinfo=datetime.timezone.utc)

                    repo_metrics["last_commit_timestamp"] = last_commit_date.isoformat()

                    # Calculate days since last commit
                    now = datetime.datetime.now(datetime.timezone.utc)
                    days_since = (now - last_commit_date).days
                    repo_metrics["days_since_last_commit"] = days_since

                    # Determine activity status using unified thresholds
                    current_threshold = self.config.get("activity_thresholds", {}).get("current_days", 365)
                    active_threshold = self.config.get("activity_thresholds", {}).get("active_days", 1095)

                    has_recent_commits = any(count > 0 for count in repo_metrics["commit_counts"].values())

                    if has_recent_commits and days_since <= current_threshold:
                        repo_metrics["activity_status"] = "current"
                    elif has_recent_commits and days_since <= active_threshold:
                        repo_metrics["activity_status"] = "active"
                    else:
                        repo_metrics["activity_status"] = "inactive"

                    # Log appropriate message based on activity
                    if any(count > 0 for count in repo_metrics["commit_counts"].values()):
                        self.logger.debug(f"Repository {repo_name} has {repo_metrics['total_commits_ever']} commits ({sum(repo_metrics['commit_counts'].values())} recent)")
                    else:
                        self.logger.debug(f"Repository {repo_name} has {repo_metrics['total_commits_ever']} commits (all historical, none recent)")

                except ValueError as e:
                    self.logger.warning(f"Could not parse last commit date for {repo_name}: {e}")
        else:
            # Truly no commits - empty repository
            self.logger.info(f"Repository {repo_name} has no commits")

        # Convert author repository sets to counts for JSON serialization
        for author_email, author_data in metrics["authors"].items():
            for window in self.time_windows:
                author_data["repositories"][window] = len(author_data["repositories"][window])

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
                "lines_added": {window: author_data["loc_stats"][window]["added"] for window in self.time_windows},
                "lines_removed": {window: author_data["loc_stats"][window]["removed"] for window in self.time_windows},
                "lines_net": {window: author_data["loc_stats"][window]["net"] for window in self.time_windows},
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
            windows_key = hashlib.sha256(json.dumps(self.time_windows, sort_keys=True).encode()).hexdigest()[:8]
            project_name = self._extract_gerrit_project(repo_path)
            # Replace path separators for cache key
            safe_project_name = project_name.replace('/', '_')
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

            with open(cache_path, 'r', encoding='utf-8') as f:
                cached_data = json.load(f)

            # Validate cache structure
            if not isinstance(cached_data, dict) or "repository" not in cached_data:
                project_name = self._extract_gerrit_project(repo_path)
                self.logger.warning(f"Invalid cache structure for {project_name}")
                return None

            # Check if cache is compatible with current time windows
            cached_windows = set(cached_data.get("repository", {}).get("commit_counts", {}).keys())
            current_windows = set(self.time_windows.keys())

            if cached_windows != current_windows:
                self.logger.debug(f"Cache invalidated for {repo_path.name}: time windows changed")
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

            with open(cache_path, 'w', encoding='utf-8') as f:
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
        self._register_default_checks()

    def register(self, feature_name: str, check_function):
        """Register a feature detection function."""
        self.checks[feature_name] = check_function

    def _register_default_checks(self):
        """Register all default feature detection checks."""
        self.register("dependabot", self._check_dependabot)
        self.register("github2gerrit_workflow", self._check_github2gerrit_workflow)
        self.register("pre_commit", self._check_pre_commit)
        self.register("readthedocs", self._check_readthedocs)
        self.register("sonatype_config", self._check_sonatype_config)
        self.register("project_types", self._check_project_types)
        self.register("workflows", self._check_workflows)
        self.register("gitreview", self._check_gitreview)

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
                    self.logger.warning(f"Feature check '{feature_name}' failed for {repo_path.name}: {e}")
                    results[feature_name] = {"error": str(e)}

        return results

    def _check_dependabot(self, repo_path: Path) -> dict[str, Any]:
        """Check for Dependabot configuration."""
        config_files = [
            ".github/dependabot.yml",
            ".github/dependabot.yaml"
        ]

        found_files = []
        for config_file in config_files:
            file_path = repo_path / config_file
            if file_path.exists():
                found_files.append(config_file)

        return {
            "present": len(found_files) > 0,
            "files": found_files
        }

    def _check_github2gerrit_workflow(self, repo_path: Path) -> dict[str, Any]:
        """Check for GitHub to Gerrit workflow patterns."""
        workflows_dir = repo_path / ".github" / "workflows"
        if not workflows_dir.exists():
            return {"present": False, "workflows": []}

        gerrit_patterns = [
            "gerrit", "review", "submit", "replication",
            "github2gerrit", "gerrit-review", "gerrit-submit"
        ]

        matching_workflows: list[dict[str, str]] = []
        try:
            for workflow_file in workflows_dir.glob("*.yml"):
                try:
                    with open(workflow_file, 'r', encoding='utf-8') as f:
                        content = f.read().lower()
                        for pattern in gerrit_patterns:
                            if pattern in content:
                                matching_workflows.append({  # type: ignore
                                    "file": workflow_file.name,
                                    "pattern": pattern
                                })
                                break
                except (IOError, UnicodeDecodeError):
                    continue

            # Also check .yaml files
            for workflow_file in workflows_dir.glob("*.yaml"):
                try:
                    with open(workflow_file, 'r', encoding='utf-8') as f:
                        content = f.read().lower()
                        for pattern in gerrit_patterns:
                            if pattern in content:
                                matching_workflows.append({  # type: ignore
                                    "file": workflow_file.name,
                                    "pattern": pattern
                                })
                                break
                except (IOError, UnicodeDecodeError):
                    continue

        except OSError:
            return {"present": False, "workflows": []}

        return {
            "present": len(matching_workflows) > 0,
            "workflows": matching_workflows
        }

    def _check_pre_commit(self, repo_path: Path) -> dict[str, Any]:
        """Check for pre-commit configuration."""
        config_files = [
            ".pre-commit-config.yaml",
            ".pre-commit-config.yml"
        ]

        found_config = None
        for config_file in config_files:
            file_path = repo_path / config_file
            if file_path.exists():
                found_config = config_file
                break

        result: dict[str, Any] = {
            "present": found_config is not None,
            "config_file": found_config
        }

        # If config exists, try to extract some basic info
        if found_config:
            try:
                config_path = repo_path / found_config
                with open(config_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # Count number of repos/hooks (basic analysis)
                    import re
                    repos_count = len(re.findall(r'^\s*-\s*repo:', content, re.MULTILINE))
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
            "readthedocs.yaml"
        ]

        sphinx_configs = [
            "docs/conf.py",
            "doc/conf.py",
            "documentation/conf.py"
        ]

        mkdocs_configs = [
            "mkdocs.yml",
            "mkdocs.yaml"
        ]

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
            "config_files": found_configs
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
            "sonatype-lift.yaml"
        ]

        found_configs = []
        for config in sonatype_configs:
            if (repo_path / config).exists():
                found_configs.append(config)

        return {
            "present": len(found_configs) > 0,
            "config_files": found_configs
        }

    def _check_project_types(self, repo_path: Path) -> dict[str, Any]:
        """Detect project types based on configuration files and repository characteristics."""
        repo_name = repo_path.name.lower()

        # Static classifications based on repository names
        if repo_name == "ci-management":
            return {
                "detected_types": ["jjb"],
                "primary_type": "jjb",
                "details": [{"type": "jjb", "files": ["repository_name"], "confidence": 100}]
            }

        project_types = {
            "maven": ["pom.xml"],
            "gradle": ["build.gradle", "build.gradle.kts", "gradle.properties", "settings.gradle"],
            "node": ["package.json"],
            "python": ["pyproject.toml", "requirements.txt", "setup.py", "setup.cfg", "Pipfile", "poetry.lock"],
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
            "kotlin": ["build.gradle.kts"]
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
                detected_types.append({
                    "type": project_type,
                    "files": matches,
                    "confidence": len(matches)
                })
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
                "details": [{"type": "documentation", "files": self._get_doc_indicators(repo_path), "confidence": 50}]
            }

        return {
            "detected_types": [t["type"] for t in detected_types],
            "primary_type": primary_type,
            "details": detected_types
        }

    def _is_documentation_repository(self, repo_path: Path) -> bool:
        """Determine if a repository is primarily for documentation (fallback only)."""
        repo_name = repo_path.name.lower()

        # Only classify as documentation if repository name strongly indicates it
        strong_doc_patterns = ["documentation", "manual", "wiki", "guide", "tutorial"]
        if any(repo_name == pattern or repo_name.endswith(f"-{pattern}") for pattern in strong_doc_patterns):
            return True

        # For repos named exactly "doc" or "docs"
        if repo_name in ["doc", "docs"]:
            return True

        # Check directory structure and file patterns - be more restrictive
        doc_indicators = self._get_doc_indicators(repo_path)
        return len(doc_indicators) >= 5  # Require more indicators for stronger confidence

    def _get_doc_indicators(self, repo_path: Path) -> list[str]:
        """Get list of documentation indicators found in the repository."""
        indicators = []

        # Check for common documentation files
        doc_files = [
            "README.md", "README.rst", "README.txt",
            "DOCS.md", "DOCUMENTATION.md",
            "index.md", "index.rst", "index.html",
            "sphinx.conf", "conf.py",  # Sphinx
            "mkdocs.yml", "_config.yml",  # MkDocs/Jekyll
            "Gemfile"  # Jekyll
        ]

        for doc_file in doc_files:
            if (repo_path / doc_file).exists():
                indicators.append(doc_file)

        # Check for documentation directories
        doc_dirs = ["docs", "doc", "documentation", "_docs", "manual", "guides", "tutorials"]
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
            "docusaurus.config.js"  # Docusaurus
        ]

        for generator in static_generators:
            if (repo_path / generator).exists():
                indicators.append(generator)

        return indicators

    def _check_workflows(self, repo_path: Path) -> dict[str, Any]:
        """Analyze GitHub workflows."""
        workflows_dir = repo_path / ".github" / "workflows"
        if not workflows_dir.exists():
            return {"count": 0, "classified": {"verify": 0, "merge": 0, "other": 0}, "files": []}

        # Get classification patterns from config
        workflow_config = self.config.get("workflows", {}).get("classify", {})
        verify_patterns = workflow_config.get("verify", ["verify", "test", "ci", "check"])
        merge_patterns = workflow_config.get("merge", ["merge", "release", "deploy", "publish"])

        workflow_files = []
        classified = {"verify": 0, "merge": 0, "other": 0}

        try:
            # Process .yml files
            for workflow_file in workflows_dir.glob("*.yml"):
                workflow_info = self._analyze_workflow_file(workflow_file, verify_patterns, merge_patterns)
                workflow_files.append(workflow_info)
                classified[workflow_info["classification"]] += 1

            # Process .yaml files
            for workflow_file in workflows_dir.glob("*.yaml"):
                workflow_info = self._analyze_workflow_file(workflow_file, verify_patterns, merge_patterns)
                workflow_files.append(workflow_info)
                classified[workflow_info["classification"]] += 1

        except OSError:
            return {"count": 0, "classified": {"verify": 0, "merge": 0, "other": 0}, "files": []}

        # Extract just the workflow names for telemetry
        workflow_names = [workflow_info["name"] for workflow_info in workflow_files]

        return {
            "count": len(workflow_files),
            "classified": classified,
            "files": workflow_files,
            "workflow_names": workflow_names
        }

    def _analyze_workflow_file(self, workflow_file: Path, verify_patterns: list[str], merge_patterns: list[str]) -> dict[str, Any]:
        """Analyze a single workflow file for classification."""
        workflow_info: dict[str, Any] = {
            "name": workflow_file.name,
            "classification": "other",
            "triggers": [],
            "jobs": 0
        }

        try:
            with open(workflow_file, 'r', encoding='utf-8') as f:
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
                    elif re.search(r'\b' + re.escape(pattern_lower) + r'\b', content):
                        verify_score += 1

                # Score merge patterns (filename matches count more)
                for pattern in merge_patterns:
                    pattern_lower = pattern.lower()
                    if pattern_lower in filename_lower:
                        merge_score += 3  # Higher weight for filename matches
                    elif re.search(r'\b' + re.escape(pattern_lower) + r'\b', content):
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
                trigger_matches = re.findall(r'on:\s*\n\s*-?\s*(\w+)', content)
                if trigger_matches:
                    workflow_info["triggers"] = trigger_matches
                else:
                    # Try alternative format
                    if 'on: push' in content:
                        triggers_list = workflow_info["triggers"]
                        assert isinstance(triggers_list, list)
                        triggers_list.append('push')
                    if 'on: pull_request' in content:
                        triggers_list = workflow_info["triggers"]
                        assert isinstance(triggers_list, list)
                        triggers_list.append('pull_request')

                # Count jobs
                job_matches = re.findall(r'^\s*(\w+):\s*$', content, re.MULTILINE)
                # Filter out common YAML keys that aren't jobs
                non_job_keys = {'on', 'env', 'defaults', 'jobs', 'name', 'run-name'}
                jobs = [job for job in job_matches if job not in non_job_keys and not job.startswith('step')]
                workflow_info["jobs"] = len(set(jobs))  # Remove duplicates

        except (IOError, UnicodeDecodeError):
            # File couldn't be read, return basic info
            pass

        return workflow_info

    def _check_gitreview(self, repo_path: Path) -> dict[str, Any]:
        """Check for .gitreview configuration file."""
        gitreview_file = repo_path / ".gitreview"

        if not gitreview_file.exists():
            return {
                "present": False,
                "file": None,
                "config": {}
            }

        # Parse .gitreview file content
        config = {}
        try:
            with open(gitreview_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        config[key.strip()] = value.strip()
        except (IOError, UnicodeDecodeError):
            # File exists but couldn't be read
            pass

        return {
            "present": True,
            "file": ".gitreview",
            "config": config
        }

# =============================================================================
# AGGREGATION AND RANKING (Phase 4 - TODO)
# =============================================================================

class DataAggregator:
    """Handles aggregation of repository data into global summaries."""

    def __init__(self, config: dict[str, Any], logger: logging.Logger) -> None:
        self.config = config
        self.logger = logger

    def aggregate_global_data(self, repo_metrics: list[dict[str, Any]]) -> dict[str, Any]:
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
        current_threshold = self.config.get("activity_thresholds", {}).get("current_days", 365)
        active_threshold = self.config.get("activity_thresholds", {}).get("active_days", 1095)


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
            total_lines_added += repo.get("loc_stats", {}).get(primary_window, {}).get("added", 0)

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
            limit=None  # No limit - show all repositories
        )

        # Keep separate lists for different activity statuses
        top_current = self.rank_entities(
            current_repos,
            f"commit_counts.{primary_window}",
            reverse=True,
            limit=None
        )

        top_active = self.rank_entities(
            active_repos,
            f"commit_counts.{primary_window}",
            reverse=True,
            limit=None
        )

        least_active = self.rank_entities(
            inactive_repos,
            "days_since_last_commit",
            reverse=True,
            limit=None
        )

        # Build contributor leaderboards
        top_contributors_commits = self.rank_entities(
            authors,
            f"commits.{primary_window}",
            reverse=True,
            limit=None
        )

        top_contributors_loc = self.rank_entities(
            authors,
            f"lines_net.{primary_window}",
            reverse=True,
            limit=None
        )

        # Build organization leaderboard
        top_organizations = self.rank_entities(
            organizations,
            f"commits.{primary_window}",
            reverse=True,
            limit=None
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
                "current": [{"gerrit_project": r.get("gerrit_project", "Unknown"), "days_since_last_commit": r.get("days_since_last_commit") if r.get("days_since_last_commit") is not None else 999999} for r in current_repos],
                "active": [{"gerrit_project": r.get("gerrit_project", "Unknown"), "days_since_last_commit": r.get("days_since_last_commit") if r.get("days_since_last_commit") is not None else 999999} for r in active_repos],
                "inactive": [{"gerrit_project": r.get("gerrit_project", "Unknown"), "days_since_last_commit": r.get("days_since_last_commit") if r.get("days_since_last_commit") is not None else 999999} for r in inactive_repos],
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

        self.logger.info(f"Aggregation complete: {len(current_repos)} current, {len(active_repos)} active, {len(inactive_repos)} inactive, {len(no_commit_repos)} no-commit repositories")
        self.logger.info(f"Found {len(authors)} authors across {len(organizations)} organizations")

        return summaries

    def _analyze_repository_commit_status(self, repo_metrics: list[dict[str, Any]]) -> None:
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
                if len(sample_no_commit_repos) < 3:  # Collect sample for detailed analysis
                    sample_no_commit_repos.append({
                        "gerrit_project": repo_name,
                        "commit_counts": commit_counts
                    })

        self.logger.info(f"Total repositories: {total_repos}")
        self.logger.info(f"Repositories with commits: {repos_with_commits}")
        self.logger.info(f"Repositories with NO commits: {repos_no_commits}")

        if sample_no_commit_repos:
            self.logger.info("Sample repositories with NO commits:")
            for repo in sample_no_commit_repos:
                self.logger.info(f"  - {repo['gerrit_project']}")

    def compute_author_rollups(self, repo_metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Aggregate author metrics across all repositories.

        Merges author data by email address, summing metrics across all repos
        and tracking unique repositories touched per time window.
        """
        from collections import defaultdict


        author_aggregates: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "name": "",
            "email": "",
            "username": "",
            "domain": "",
            "repositories_touched": defaultdict(set),
            "commits": defaultdict(int),
            "lines_added": defaultdict(int),
            "lines_removed": defaultdict(int),
            "lines_net": defaultdict(int),
        })

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
                    repos_set = cast(set[str], author_aggregates[email]["repositories_touched"][window_name])
                    repos_set.add(repo_name)
                    author_aggregates[email]["commits"][window_name] += author.get("commits", {}).get(window_name, 0)
                    author_aggregates[email]["lines_added"][window_name] += author.get("lines_added", {}).get(window_name, 0)
                    author_aggregates[email]["lines_removed"][window_name] += author.get("lines_removed", {}).get(window_name, 0)
                    author_aggregates[email]["lines_net"][window_name] += author.get("lines_net", {}).get(window_name, 0)

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
                    window: set(repos) for window, repos in data["repositories_touched"].items()
                },
                "repositories_count": {
                    window: len(repos) for window, repos in data["repositories_touched"].items()
                },
            }
            authors.append(author_record)

        self.logger.info(f"Aggregated {len(authors)} unique authors across repositories")
        return authors

    def compute_org_rollups(self, authors: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Aggregate organization metrics from author data.

        Groups authors by email domain and aggregates their contributions.
        """
        from collections import defaultdict


        org_aggregates: dict[str, dict[str, Any]] = defaultdict(lambda: {
            "domain": "",
            "contributor_count": 0,
            "contributors": set(),
            "commits": defaultdict(int),
            "lines_added": defaultdict(int),
            "lines_removed": defaultdict(int),
            "lines_net": defaultdict(int),
            "repositories_count": defaultdict(set),
        })

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
                org_aggregates[domain]["commits"][window_name] += author.get("commits", {}).get(window_name, 0)
                org_aggregates[domain]["lines_added"][window_name] += author.get("lines_added", {}).get(window_name, 0)
                org_aggregates[domain]["lines_removed"][window_name] += author.get("lines_removed", {}).get(window_name, 0)
                org_aggregates[domain]["lines_net"][window_name] += author.get("lines_net", {}).get(window_name, 0)

                # Track unique repositories per organization
                author_repos = author.get("repositories_touched", {}).get(window_name, set())
                if author_repos:
                    repos_set = cast(set[str], org_aggregates[domain]["repositories_count"][window_name])
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
                    window: len(repos) for window, repos in data["repositories_count"].items()
                },
            }
            organizations.append(org_record)

        self.logger.info(f"Aggregated {len(organizations)} organizations from author domains")
        return organizations

    def rank_entities(self, entities: list[dict[str, Any]], sort_key: str, reverse: bool = False, limit: int | None = None) -> list[dict[str, Any]]:
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
            return entity.get("name") or entity.get("gerrit_project") or entity.get("domain") or entity.get("email") or ""



        # Sort with primary metric (reverse if specified) and secondary name (always ascending)
        if reverse:
            sorted_entities = sorted(entities, key=lambda x: (-get_sort_value(x), get_name(x)))
        else:
            sorted_entities = sorted(entities, key=lambda x: (get_sort_value(x), get_name(x)))



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

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)

    def render_markdown_report(self, data: dict[str, Any], output_path: Path) -> str:
        """
        Generate Markdown report from JSON data.

        Creates structured Markdown with tables, emoji indicators, and formatted numbers.
        """
        self.logger.info(f"Generating Markdown report to {output_path}")

        markdown_content = self._generate_markdown_content(data)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(markdown_content)

        return markdown_content

    def render_html_report(self, markdown_content: str, output_path: Path) -> None:
        """
        Convert Markdown to HTML with embedded styling.

        Converts Markdown tables and formatting to proper HTML with CSS styling.
        """
        self.logger.info(f"Converting to HTML report at {output_path}")

        html_content = self._convert_markdown_to_html(markdown_content)

        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)

    def package_zip_report(self, output_dir: Path, project: str) -> Path:
        """
        Package all report outputs into a ZIP file.

        Creates a ZIP containing JSON, Markdown, HTML, and configuration files.
        """
        zip_path = output_dir / f"{project}_report_bundle.zip"
        self.logger.info(f"Creating ZIP package at {zip_path}")

        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
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

        # Footer
        sections.append("Generated with ❤️ by Release Engineering")

        # Filter out empty sections to avoid unnecessary whitespace
        non_empty_sections = [section for section in sections if section.strip()]
        return "\n\n".join(non_empty_sections)

    def _generate_title_section(self, data: dict[str, Any]) -> str:
        """Generate title and metadata section."""
        project = data.get("project", "Repository Analysis")
        generated_at = data.get("generated_at", "")
        total_repos = data.get("summaries", {}).get("counts", {}).get("total_repositories", 0)
        current_repos = data.get("summaries", {}).get("counts", {}).get("current_repositories", 0)
        active_repos = data.get("summaries", {}).get("counts", {}).get("active_repositories", 0)
        total_authors = data.get("summaries", {}).get("counts", {}).get("total_authors", 0)

        # Format timestamp
        if generated_at:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(generated_at.replace('Z', '+00:00'))
                formatted_time = dt.strftime("%B %d, %Y at %H:%M UTC")
            except:
                formatted_time = generated_at
        else:
            formatted_time = "Unknown"

        return f"""# 📊 Gerrit Project Analysis Report: {project}

**Generated:** {formatted_time}
**Gerrit Projects Analyzed:** {total_repos} ({current_repos} current, {active_repos} active)
**Contributors Found:** {total_authors:,}
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
        current_threshold = self.config.get("activity_thresholds", {}).get("current_days", 365)
        active_threshold = self.config.get("activity_thresholds", {}).get("active_days", 1095)

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
| Total Contributors | {self._format_number(total_authors)} | - |
| Organizations | {self._format_number(total_orgs)} | - |
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

        lines = ["| Repository | Days Inactive | Last Commit Date |",
                 "|------------|---------------|-------------------|"]

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

    def _generate_all_repositories_section(self, data: dict[str, Any]) -> str:
        """Generate combined repositories table showing all Gerrit projects."""
        all_repos = data.get("summaries", {}).get("all_repositories", [])

        if not all_repos:
            return "## 📊 All Gerrit Repositories\n\nNo repositories found."

        # Get configuration for definitions
        current_threshold = self.config.get("activity_thresholds", {}).get("current_days", 365)
        active_threshold = self.config.get("activity_thresholds", {}).get("active_days", 1095)

        lines = ["## 📊 Gerrit Projects",
                 "",
                 f"Complete list of all Gerrit repositories sorted by activity (commits in last year). Use column sorting to filter by different criteria.",
                 f"**Activity Status:** ✅ Current ☑️ Active 🛑 Inactive",
                 "",
                 "| Gerrit Project | Commits | LOC | Contributors | Days Inactive | Last Commit Date | Status |",
                 "|----------------|---------|---------|--------------|---------------|------------------|--------|"]

        for repo in all_repos:
            name = repo.get("gerrit_project", "Unknown")
            commits_1y = repo.get("commit_counts", {}).get("last_365_days", 0)
            loc_1y = repo.get("loc_stats", {}).get("last_365_days", {}).get("net", 0)
            contributors_1y = repo.get("unique_contributors", {}).get("last_365_days", 0)
            days_since = repo.get("days_since_last_commit")
            if days_since is None:
                days_since = 999999  # Very large number for repos with no commits
            activity_status = repo.get("activity_status", "inactive")

            age_str = self._format_age(days_since)

            # Map activity status to display format (emoji only)
            status_map = {
                "current": "✅",
                "active": "☑️",
                "inactive": "🛑"
            }
            status = status_map.get(activity_status, "🛑")

            # Format days inactive
            days_inactive_str = f"{days_since:,}" if days_since < 999999 else "N/A"

            lines.append(f"| {name} | {commits_1y} | {int(loc_1y):+d} | {contributors_1y} | {days_inactive_str} | {age_str} | {status} |")

        lines.extend(["", f"**Total:** {len(all_repos)} repositories"])
        return "\n".join(lines)

    def _generate_no_commit_repositories_section(self, data: dict[str, Any]) -> str:
        """Generate repositories with no commits section."""
        no_commit_repos = data.get("summaries", {}).get("no_commit_repositories", [])

        if not no_commit_repos:
            return ""  # Skip output entirely if no data

        lines = ["## 📝 Gerrit Projects with No Apparent Commits",
                 "",
                 "**WARNING:** All Gerrit projects/repositories should contain at least one commit, due to the initial repository creation automation writing initial template and configuration files. The report generation and parsing logic may need checking/debugging for the projects/repositories below.",
                 "",
                 "| Gerrit Project |",
                 "|------------|"]

        for repo in no_commit_repos:
            name = repo.get("gerrit_project", "Unknown")
            lines.append(f"| {name} |")

        lines.extend(["", f"**Total:** {len(no_commit_repos)} Gerrit projects with no apparent commits"])
        return "\n".join(lines)

    def _generate_deployed_workflows_section(self, data: dict[str, Any]) -> str:
        """Generate deployed CI/CD jobs telemetry section."""
        repositories = data.get("repositories", [])

        if not repositories:
            return "## 🏁 Deployed CI/CD Jobs\n\nNo repositories found."

        # Collect repositories that have workflows or Jenkins jobs
        repos_with_cicd = []
        has_any_jenkins = False

        for repo in repositories:
            workflow_names = repo.get("features", {}).get("workflows", {}).get("workflow_names", [])
            jenkins_jobs = repo.get("jenkins", {}).get("jobs", [])
            jenkins_job_names = [job.get("name", "") for job in jenkins_jobs if job.get("name")]

            if workflow_names or jenkins_job_names:
                repos_with_cicd.append({
                    "gerrit_project": repo.get("gerrit_project", "Unknown"),
                    "workflow_names": workflow_names,
                    "jenkins_job_names": jenkins_job_names,
                    "workflow_count": len(workflow_names),
                    "job_count": len(jenkins_job_names)
                })
                if jenkins_job_names:
                    has_any_jenkins = True

        if not repos_with_cicd:
            return "## 🏁 Deployed CI/CD Jobs\n\nNo CI/CD jobs detected in any repositories."

        # Build table header based on whether Jenkins jobs exist
        if has_any_jenkins:
            lines = ["## 🏁 Deployed CI/CD Jobs",
                     "",
                     "| Gerrit Project | GitHub Workflows | Workflow Count | Jenkins Jobs | Job Count |",
                     "|----------------|-------------------|----------------|--------------|-----------|"]
        else:
            lines = ["## 🏁 Deployed CI/CD Jobs",
                     "",
                     "| Gerrit Project | GitHub Workflows | Workflow Count | Job Count |",
                     "|----------------|-------------------|----------------|-----------|"]

        for repo in sorted(repos_with_cicd, key=lambda x: x["gerrit_project"]):
            name = repo["gerrit_project"]
            workflow_names_str = "<br>".join(sorted(repo["workflow_names"])) if repo["workflow_names"] else ""
            workflow_count = repo["workflow_count"]
            job_count = repo["job_count"]

            if has_any_jenkins:
                jenkins_names_str = "<br>".join(sorted(repo["jenkins_job_names"])) if repo["jenkins_job_names"] else ""
                lines.append(f"| {name} | {workflow_names_str} | {workflow_count} | {jenkins_names_str} | {job_count} |")
            else:
                lines.append(f"| {name} | {workflow_names_str} | {workflow_count} | {job_count} |")

        lines.extend(["", f"**Total:** {len(repos_with_cicd)} repositories with CI/CD jobs"])
        return "\n".join(lines)

    def _generate_contributors_section(self, data: dict[str, Any]) -> str:
        """Generate consolidated contributors table section."""
        top_commits = data.get("summaries", {}).get("top_contributors_commits", [])
        top_loc = data.get("summaries", {}).get("top_contributors_loc", [])

        sections = ["## 👥 Top Contributors (Last Year)"]

        # Generate consolidated table with all contributors
        if top_commits or top_loc:
            sections.append(self._generate_consolidated_contributors_table(top_commits, top_loc))
        else:
            sections.append("No contributor data available.")

        return "\n\n".join(sections)

    def _generate_consolidated_contributors_table(self, top_commits: list[dict[str, Any]], top_loc: list[dict[str, Any]]) -> str:
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
        all_contributors.sort(key=lambda x: (
            x.get("commits", {}).get("last_365_days", 0),
            x.get("lines_net", {}).get("last_365_days", 0)
        ), reverse=True)

        if not all_contributors:
            return "No contributors found."

        # Create table headers
        lines = [
            "| Rank | Contributor | Commits | LOC | Δ LOC | Avg LOC/Commit | Repositories | Organization |",
            "|------|-------------|---------|-----|-------|----------------|--------------|--------------|"
        ]

        for i, contributor in enumerate(all_contributors, 1):
            name = contributor.get("name", "Unknown")
            email = contributor.get("email", "")
            domain = contributor.get("domain", "")
            commits_1y = contributor.get("commits", {}).get("last_365_days", 0)
            loc_1y = contributor.get("lines_net", {}).get("last_365_days", 0)
            lines_added_1y = contributor.get("lines_added", {}).get("last_365_days", 0)
            lines_removed_1y = contributor.get("lines_removed", {}).get("last_365_days", 0)
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

            lines.append(f"| {i} | {display_name} | {commits_1y} | {int(loc_1y):+d} | {delta_loc_1y} | {avg_display} | {repos_1y} | {org_display} |")

        return "\n".join(lines)

    def _generate_contributors_table(self, contributors: list[dict[str, Any]], metric_type: str) -> str:
        """Generate contributors table for commits or LOC."""
        if not contributors:
            return "No contributors found."

        if metric_type == "commits":
            lines = ["| Rank | Contributor | Commits | Repositories | Organization |",
                     "|------|-------------|---------|--------------|--------------|"]
        else:
            lines = ["| Rank | Contributor | LOC | Commits | Repositories | Organization |",
                     "|------|-------------|---------|---------|--------------|--------------|"]

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
                lines.append(f"| {i} | {display_name} | {commits_1y} | {repos_1y} | {org_display} |")
            else:
                lines.append(f"| {i} | {display_name} | {int(loc_1y):+d} | {commits_1y} | {repos_1y} | {org_display} |")

        return "\n".join(lines)

    def _generate_organizations_section(self, data: dict[str, Any]) -> str:
        """Generate organizations leaderboard section."""
        top_orgs = data.get("summaries", {}).get("top_organizations", [])

        if not top_orgs:
            return "## 🏢 Organizations\n\nNo organization data available."

        lines = ["## 🏢 Top Organizations (Last Year)",
                 "",
                 "| Rank | Organization | Contributors | Commits | LOC | Δ LOC | Avg LOC/Commit | Unique Repositories |",
                 "|------|--------------|--------------|---------|-----|-------|----------------|---------------------|"]

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

            lines.append(f"| {i} | {domain} | {contributors} | {commits_1y} | {int(loc_1y):+d} | {delta_loc_1y} | {avg_display} | {repos_1y} |")

        return "\n".join(lines)

    def _generate_feature_matrix_section(self, data: dict[str, Any]) -> str:
        """Generate repository feature matrix section."""
        repositories = data.get("repositories", [])

        if not repositories:
            return "## 🔧 Gerrit Project Feature Matrix\n\nNo projects analyzed."

        # Sort repositories by primary metric (commits in last year)
        sorted_repos = sorted(repositories,
                             key=lambda r: r.get("commit_counts", {}).get("last_365_days", 0),
                             reverse=True)

        # Get activity thresholds for definition
        current_threshold = self.config.get("activity_thresholds", {}).get("current_days", 365)
        active_threshold = self.config.get("activity_thresholds", {}).get("active_days", 1095)

        lines = ["## 🔧 Gerrit Project Feature Matrix",
                 "",
                 f"Feature analysis for all Gerrit projects. Status: ✅ Current ☑️ Active 🛑 Inactive",
                 "",
                 "| Gerrit Project | Type | Dependabot | Pre-commit | ReadTheDocs | .gitreview | Status |",
                 "|------------|------|------------|------------|-------------|------------|--------|"]

        for repo in sorted_repos:
            name = repo.get("gerrit_project", "Unknown")
            features = repo.get("features", {})
            activity_status = repo.get("activity_status", "inactive")

            # Extract feature status
            project_types = features.get("project_types", {})
            primary_type = project_types.get("primary_type", "unknown")

            dependabot = "✅" if features.get("dependabot", {}).get("present", False) else "❌"
            pre_commit = "✅" if features.get("pre_commit", {}).get("present", False) else "❌"
            readthedocs = "✅" if features.get("readthedocs", {}).get("present", False) else "❌"
            gitreview = "✅" if features.get("gitreview", {}).get("present", False) else "❌"

            # Map activity status to display format (emoji only)
            status_map = {
                "current": "✅",
                "active": "☑️",
                "inactive": "🛑"
            }
            status = status_map.get(activity_status, "🛑")

            lines.append(f"| {name} | {primary_type} | {dependabot} | {pre_commit} | {readthedocs} | {gitreview} | {status} |")

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
        lines = markdown.split('\n')
        in_table = False

        i = 0
        while i < len(lines):
            line = lines[i]

            # Headers
            if line.startswith('# '):
                content = line[2:].strip()
                html_lines.append(f'<h1 id="{self._slugify(content)}">{content}</h1>')
            elif line.startswith('## '):
                content = line[3:].strip()
                html_lines.append(f'<h2 id="{self._slugify(content)}">{content}</h2>')
            elif line.startswith('### '):
                content = line[4:].strip()
                html_lines.append(f'<h3 id="{self._slugify(content)}">{content}</h3>')

            # Tables
            elif '|' in line and line.strip():
                if not in_table:
                    # Check if this table will have headers by looking ahead
                    has_headers = (i + 1 < len(lines) and
                                 re.match(r'^\|[\s\-\|]+\|$', lines[i + 1].strip()))
                    # Only add sortable class if feature is enabled and table has headers
                    sortable_enabled = self.config.get("html_tables", {}).get("sortable", True)

                    # Check if this is the feature matrix table or combined repositories table by looking for specific headers
                    is_feature_matrix = False
                    is_cicd_jobs = False
                    is_all_repositories = False
                    is_global_summary = False
                    if has_headers and i < len(lines):
                        table_header = line.lower()
                        if 'gerrit project' in table_header and 'dependabot' in table_header and 'pre-commit' in table_header:
                            is_feature_matrix = True
                        elif 'gerrit project' in table_header and ('github workflows' in table_header or 'jenkins jobs' in table_header):
                            is_cicd_jobs = True
                        elif 'gerrit project' in table_header and 'commits' in table_header and 'status' in table_header:
                            is_all_repositories = True
                        elif 'metric' in table_header and 'count' in table_header and 'percentage' in table_header:
                            is_global_summary = True

                    table_class = ' class="sortable"' if (has_headers and sortable_enabled) else ''
                    if is_feature_matrix:
                        table_class = ' class="sortable no-pagination feature-matrix-table"'
                    elif is_cicd_jobs:
                        table_class = ' class="sortable no-pagination cicd-jobs-table"'
                    elif is_all_repositories:
                        table_class = ' class="sortable"'
                    elif is_global_summary:
                        table_class = ' class="no-search no-pagination"'

                    html_lines.append(f'<table{table_class}>')
                    in_table = True

                # Check if this is a header separator line
                if re.match(r'^\|[\s\-\|]+\|$', line.strip()):
                    # Skip separator line
                    pass
                else:
                    # Regular table row
                    cells = [cell.strip() for cell in line.split('|')[1:-1]]  # Remove empty first/last

                    # Determine if this is likely a header row (check next line)
                    is_header = (i + 1 < len(lines) and
                               re.match(r'^\|[\s\-\|]+\|$', lines[i + 1].strip()))

                    if is_header:
                        html_lines.append('<thead><tr>')
                        for cell in cells:
                            html_lines.append(f'<th>{cell}</th>')
                        html_lines.append('</tr></thead><tbody>')
                    else:
                        html_lines.append('<tr>')
                        for cell in cells:
                            html_lines.append(f'<td>{cell}</td>')
                        html_lines.append('</tr>')

            # End table when we hit a non-table line
            elif in_table and not ('|' in line and line.strip()):
                html_lines.append('</tbody></table>')
                in_table = False
                # Process this line normally
                if line.strip():
                    html_lines.append(f'<p>{line}</p>')
                else:
                    html_lines.append('')

            # Regular paragraphs
            elif line.strip() and not in_table:
                # Bold text
                line = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', line)
                # Code blocks
                line = re.sub(r'`(.*?)`', r'<code>\1</code>', line)
                html_lines.append(f'<p>{line}</p>')

            # Empty lines
            else:
                if not in_table:
                    html_lines.append('')

            i += 1

        # Close table if still open
        if in_table:
            html_lines.append('</tbody></table>')

        return '\n'.join(html_lines)

    def _get_datatable_css(self) -> str:
        """Get Simple-DataTables CSS if sorting is enabled."""
        if not self.config.get("html_tables", {}).get("sortable", True):
            return ""

        return '''
    <!-- Simple-DataTables CSS -->
    <link href="https://cdn.jsdelivr.net/npm/simple-datatables@latest/dist/style.css" rel="stylesheet" type="text/css">
    '''

    def _get_datatable_js(self) -> str:
        """Get Simple-DataTables JavaScript if sorting is enabled."""
        if not self.config.get("html_tables", {}).get("sortable", True):
            return ""

        min_rows = self.config.get("html_tables", {}).get("min_rows_for_sorting", 3)
        searchable = str(self.config.get("html_tables", {}).get("searchable", True)).lower()
        sortable = str(self.config.get("html_tables", {}).get("sortable", True)).lower()
        pagination = str(self.config.get("html_tables", {}).get("pagination", True)).lower()
        per_page = self.config.get("html_tables", {}).get("entries_per_page", 50)
        page_options = self.config.get("html_tables", {}).get("page_size_options", [20, 50, 100, 200])

        return f'''
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
    </script>'''

    def _slugify(self, text: str) -> str:
        """Convert text to URL-friendly slug."""
        import re
        # Remove emojis and special chars, convert to lowercase
        slug = re.sub(r'[^\w\s-]', '', text).strip().lower()
        slug = re.sub(r'[\s_-]+', '-', slug)
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
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, default=str)

def create_report_bundle(project_output_dir: Path, project: str, logger: logging.Logger) -> Path:
    """
    Package all report artifacts into a ZIP file.

    Bundles JSON, Markdown, HTML, and resolved config files.
    """
    logger.info(f"Creating report bundle for project {project}")

    zip_path = project_output_dir / f"{project}_report_bundle.zip"

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
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
            return f"{value/1_000_000:.1f}M"
        elif value >= 1_000:
            return f"{value/1_000:.1f}k"

    return str(value)

def format_age_days(days: int) -> str:
    """Format age in days to actual date."""
    from datetime import datetime, timedelta
    if days is None or days == 0:
        return datetime.now().strftime("%Y-%m-%d")

    # Calculate actual date
    date = datetime.now() - timedelta(days=days)
    return date.strftime("%Y-%m-%d")

def safe_git_command(cmd: list[str], cwd: Path | None, logger: logging.Logger) -> tuple[bool, str]:
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
        return git_result.returncode == 0, git_result.stdout.strip() or git_result.stderr.strip()
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

    def _clone_info_master_repo(self, repos_path: Path) -> None:
        """Clone the info-master repository for additional context data."""
        info_master_path = repos_path / "info-master"
        info_master_url = "ssh://modesevenindustrialsolutions@gerrit.linuxfoundation.org:29418/releng/info-master"

        if info_master_path.exists():
            self.logger.info(f"Info-master repository already exists at {info_master_path}")
            # Update existing repository
            success, output = safe_git_command(["git", "pull"], info_master_path, self.logger)
            if success:
                self.logger.info("Successfully updated info-master repository")
            else:
                self.logger.warning(f"Failed to update info-master repository: {output}")
        else:
            self.logger.info(f"Cloning info-master repository to {info_master_path}")
            success, output = safe_git_command(
                ["git", "clone", info_master_url, str(info_master_path)],
                repos_path,
                self.logger
            )
            if success:
                self.logger.info("Successfully cloned info-master repository")
            else:
                self.logger.error(f"Failed to clone info-master repository: {output}")

    def analyze_repositories(self, repos_path: Path) -> dict[str, Any]:
        """
        Main analysis workflow.

        TODO: Coordinate all phases
        """
        # Resolve to absolute path for consistent handling
        repos_path_abs = repos_path.resolve()
        self.logger.info(f"Starting repository analysis in {repos_path_abs}")

        # Clone info-master repository for additional context
        self._clone_info_master_repo(repos_path_abs)

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
            "errors": []
        }

        # Update git collector with time windows

        self.git_collector.time_windows = cast(dict[str, dict[str, Any]], report_data["time_windows"])

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
        report_data["authors"] = self.aggregator.compute_author_rollups(successful_repos)
        report_data["organizations"] = self.aggregator.compute_org_rollups(report_data["authors"])
        report_data["summaries"] = self.aggregator.aggregate_global_data(successful_repos)

        self.logger.info(f"Analysis complete: {len(report_data['repositories'])} repositories, {len(report_data['errors'])} errors")

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
        markdown_content = self.renderer.render_markdown_report(report_data, markdown_path)
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
                                self.logger.debug(f"Verified {rel_path} exists in Gerrit")
                            else:
                                self.logger.warning(f"Repository {rel_path} not found in Gerrit API cache")

                        repo_dirs.append(repo_dir)
                except (PermissionError, OSError) as e:
                    access_errors += 1
                    self.logger.debug(f"Cannot access potential repository at {git_dir}: {e}")
        except (PermissionError, OSError) as e:
            self.logger.warning(f"Error during repository discovery: {e}")

        # Deduplicate and sort results
        unique_repos = sorted({p.resolve() for p in repo_dirs})

        self.logger.info(f"Discovered {len(unique_repos)} git repositories")
        if access_errors:
            self.logger.debug(f"Encountered {access_errors} access errors during discovery")

        return unique_repos

    def _analyze_repositories_parallel(self, repo_dirs: list[Path]) -> list[dict[str, Any]]:
        """Analyze repositories with optional concurrency."""
        max_workers = self.config.get("performance", {}).get("max_workers", 8)

        if max_workers == 1:
            # Sequential processing
            return [self._analyze_single_repository(repo_dir) for repo_dir in repo_dirs]

        # Concurrent processing
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_repo = {executor.submit(self._analyze_single_repository, repo_dir): repo_dir
                             for repo_dir in repo_dirs}

            for future in concurrent.futures.as_completed(future_to_repo):
                repo_dir = future_to_repo[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    self.logger.error(f"Failed to analyze {repo_dir.name}: {e}")
                    results.append({
                        "error": str(e),
                        "repo": repo_dir.name,
                        "category": "analysis_failure"
                    })

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
                "category": "repository_analysis"
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
        """
    )

    # Required arguments
    parser.add_argument(
        "--project",
        required=True,
        help="Project name (used for config override and output naming)"
    )
    parser.add_argument(
        "--repos-path",
        required=True,
        type=Path,
        help="Path to directory containing cloned repositories"
    )

    # Optional configuration
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=DEFAULT_CONFIG_DIR,
        help=f"Configuration directory (default: {DEFAULT_CONFIG_DIR})"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})"
    )

    # Output options
    parser.add_argument(
        "--no-html",
        action="store_true",
        help="Skip HTML report generation"
    )
    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="Skip ZIP bundle creation"
    )

    # Behavioral options
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging"
    )
    parser.add_argument(
        "--cache",
        action="store_true",
        help="Enable caching of git metrics"
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate configuration and exit"
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override log level from configuration"
    )


    return parser.parse_args()

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

        # Override log level if specified
        if args.log_level:
            config.setdefault("logging", {})["level"] = args.log_level
        elif args.verbose:
            config.setdefault("logging", {})["level"] = "DEBUG"



        # Setup logging
        log_config = config.get("logging", {})
        logger = setup_logging(
            level=log_config.get("level", "INFO"),
            include_timestamps=log_config.get("include_timestamps", True)
        )

        logger.info(f"Repository Reporting System v{SCRIPT_VERSION}")
        logger.info(f"Project: {args.project}")
        logger.info(f"Configuration digest: {compute_config_digest(config)[:12]}...")

        # Validate-only mode
        if args.validate_only:
            logger.info("Configuration validation successful")
            print(f"✅ Configuration valid for project '{args.project}'")
            print(f"   - Schema version: {config.get('schema_version', 'Unknown')}")
            print(f"   - Time windows: {list(config.get('time_windows', {}).keys())}")
            print(f"   - Features enabled: {len(config.get('features', {}).get('enabled', []))}")
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
        markdown_content = reporter.renderer.render_markdown_report(report_data, md_path)

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

        return 0

    except KeyboardInterrupt:
        print("\n❌ Operation cancelled by user", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"❌ Unexpected error: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
