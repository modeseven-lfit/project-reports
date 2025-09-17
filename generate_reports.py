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
import re
import subprocess
import sys
import tempfile
import threading
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union, DefaultDict

try:
    import yaml  # type: ignore
except ImportError:
    print("ERROR: PyYAML is required. Install with: pip install PyYAML", file=sys.stderr)
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

def deep_merge_dicts(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
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

def load_configuration(config_dir: Path, project: str) -> Dict[str, Any]:
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

def compute_time_windows(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
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
# GIT DATA COLLECTION (Phase 2 - TODO)
# =============================================================================

class GitDataCollector:
    """Handles Git repository analysis and metric collection."""

    def __init__(self, config: Dict[str, Any], time_windows: Dict[str, Dict[str, Any]], logger: logging.Logger):
        self.config = config
        self.time_windows = time_windows
        self.logger = logger
        self.cache_enabled = config.get("performance", {}).get("cache", False)
        self.cache_dir = None
        if self.cache_enabled:
            self.cache_dir = Path(tempfile.gettempdir()) / "repo_reporting_cache"
            self.cache_dir.mkdir(exist_ok=True)

    def collect_repo_git_metrics(self, repo_path: Path) -> Dict[str, Any]:
        """
        Extract Git metrics for a single repository across all time windows.

        Uses git log --numstat --date=iso --pretty=format for unified traversal.
        Single pass filtering commits into all time windows.
        Collects: timestamps, author name/email, added/removed lines.
        Returns structured metrics or error descriptor.
        """
        repo_name = repo_path.name
        self.logger.debug(f"Collecting Git metrics for {repo_name}")

        # Initialize metrics structure
        metrics: Dict[str, Any] = {
            "repository": {
                "name": repo_name,
                "path": str(repo_path),
                "last_commit_timestamp": None,
                "days_since_last_commit": None,
                "is_active": False,
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
                    self.logger.debug(f"Using cached metrics for {repo_name}")
                    return cached_metrics

            # Get git log with numstat in a single command
            git_command = [
                "git", "log", "--numstat", "--date=iso", "--pretty=format:%H|%ad|%an|%ae|%s"
            ]

            # Limit history if configured
            max_history_years = self.config.get("data_quality", {}).get("max_history_years", 10)
            if max_history_years > 0:
                since_date = datetime.datetime.now() - datetime.timedelta(days=max_history_years * 365)
                git_command.extend(["--since", since_date.strftime("%Y-%m-%d")])

            success, output = safe_git_command(git_command, repo_path, self.logger)
            if not success:
                metrics["errors"].append(f"Git command failed: {output}")
                return metrics

            # Parse git log output
            commits_data = self._parse_git_log_output(output, repo_name)

            # Process commits into time windows
            for commit_data in commits_data:
                self._process_commit_into_metrics(commit_data, metrics)

            # Finalize repository metrics
            self._finalize_repo_metrics(metrics, repo_name)

            # Convert sets to counts for JSON serialization
            repo_data = metrics["repository"]
            unique_contributors = repo_data["unique_contributors"]
            for window in self.time_windows:
                contributor_set = unique_contributors[window]
                assert isinstance(contributor_set, set)
                unique_contributors[window] = len(contributor_set)

            self.logger.debug(f"Collected {len(commits_data)} commits for {repo_name}")

            # Save to cache if enabled
            if self.cache_enabled:
                self._save_to_cache(repo_path, metrics)

        except Exception as e:
            self.logger.error(f"Error collecting Git metrics for {repo_name}: {e}")
            errors_list = metrics["errors"]
            assert isinstance(errors_list, list)
            errors_list.append(f"Unexpected error: {str(e)}")

        return metrics

    def bucket_commit_into_windows(self, commit_datetime: datetime.datetime, windows: Dict[str, Dict[str, Any]]) -> List[str]:
        """
        Determine which time windows a commit falls into.

        A commit belongs to a window if it occurred after the window's start time.
        """
        matching_windows = []
        commit_timestamp = commit_datetime.timestamp()

        for window_name, window_data in windows.items():
            if commit_timestamp >= window_data["start_timestamp"]:
                matching_windows.append(window_name)

        return matching_windows

    def normalize_author_identity(self, name: str, email: str) -> Dict[str, str]:
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

        return normalized

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

    def _process_commit_into_metrics(self, commit_data: Dict[str, Any], metrics: Dict[str, Any]) -> None:
        """Process a single commit into the metrics structure."""
        commit_windows = self.bucket_commit_into_windows(commit_data["date"], self.time_windows)

        # Normalize author identity
        author_info = self.normalize_author_identity(commit_data["author_name"], commit_data["author_email"])
        author_email = author_info["email"]

        # Calculate LOC changes for this commit
        total_added = sum(f["added"] for f in commit_data["files_changed"])
        total_removed = sum(f["removed"] for f in commit_data["files_changed"])
        net_lines = total_added - total_removed

        # Update repository metrics for each matching window
        for window in commit_windows:
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
        for window in commit_windows:
            author_metrics["commit_counts"][window] += 1
            author_metrics["loc_stats"][window]["added"] += total_added
            author_metrics["loc_stats"][window]["removed"] += total_removed
            author_metrics["loc_stats"][window]["net"] += net_lines
            author_metrics["repositories"][window].add(metrics["repository"]["name"])

    def _finalize_repo_metrics(self, metrics: Dict[str, Any], repo_name: str) -> None:
        """Finalize repository metrics after processing all commits."""
        repo_metrics = metrics["repository"]

        # Find the most recent commit to determine activity status
        if any(count > 0 for count in repo_metrics["commit_counts"].values()):
            # Find last commit date by looking at git log with limit 1
            git_command = ["git", "log", "-1", "--date=iso", "--pretty=format:%ad"]
            success, output = safe_git_command(git_command, Path(repo_metrics["path"]), self.logger)

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

                    # Determine if repository is active
                    activity_threshold = self.config.get("activity_threshold_days", 365)
                    repo_metrics["is_active"] = days_since <= activity_threshold

                except ValueError as e:
                    self.logger.warning(f"Could not parse last commit date for {repo_name}: {e}")

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
                "commits": author_data["commit_counts"],
                "lines_added": {window: author_data["loc_stats"][window]["added"] for window in self.time_windows},
                "lines_removed": {window: author_data["loc_stats"][window]["removed"] for window in self.time_windows},
                "lines_net": {window: author_data["loc_stats"][window]["net"] for window in self.time_windows},
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
            return f"{repo_path.name}_{head_hash}_{windows_key}"

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
                self.logger.warning(f"Invalid cache structure for {repo_path.name}")
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

    def _save_to_cache(self, repo_path: Path, metrics: Dict[str, Any]) -> None:
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

    def __init__(self, config: Dict[str, Any], logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.checks: Dict[str, Any] = {}
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

    def scan_repository_features(self, repo_path: Path) -> Dict[str, Any]:
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

    def _check_dependabot(self, repo_path: Path) -> Dict[str, Any]:
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

    def _check_github2gerrit_workflow(self, repo_path: Path) -> Dict[str, Any]:
        """Check for GitHub to Gerrit workflow patterns."""
        workflows_dir = repo_path / ".github" / "workflows"
        if not workflows_dir.exists():
            return {"present": False, "workflows": []}

        gerrit_patterns = [
            "gerrit", "review", "submit", "replication",
            "github2gerrit", "gerrit-review", "gerrit-submit"
        ]

        matching_workflows: List[Dict[str, str]] = []
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

    def _check_pre_commit(self, repo_path: Path) -> Dict[str, Any]:
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

        result = {
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
                    result["repos_count"] = repos_count  # type: ignore
            except (IOError, UnicodeDecodeError):
                pass

        return result

    def _check_readthedocs(self, repo_path: Path) -> Dict[str, Any]:
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

    def _check_sonatype_config(self, repo_path: Path) -> Dict[str, Any]:
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

    def _check_project_types(self, repo_path: Path) -> Dict[str, Any]:
        """Detect project types based on configuration files."""
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

        return {
            "detected_types": [t["type"] for t in detected_types],
            "primary_type": primary_type,
            "details": detected_types
        }

    def _check_workflows(self, repo_path: Path) -> Dict[str, Any]:
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

        return {
            "count": len(workflow_files),
            "classified": classified,
            "files": workflow_files
        }

    def _analyze_workflow_file(self, workflow_file: Path, verify_patterns: List[str], merge_patterns: List[str]) -> Dict[str, Any]:
        """Analyze a single workflow file for classification."""
        workflow_info: Dict[str, Any] = {
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

# =============================================================================
# AGGREGATION AND RANKING (Phase 4 - TODO)
# =============================================================================

class DataAggregator:
    """Handles aggregation of repository data into global summaries."""

    def __init__(self, config: Dict[str, Any], logger: logging.Logger):
        self.config = config
        self.logger = logger

    def aggregate_global_data(self, repo_metrics: List[Dict[str, Any]]) -> Dict[str, Any]:
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

        # Configuration values
        activity_threshold_days = self.config.get("activity_threshold_days", 365)
        very_old_years = self.config.get("age_buckets", {}).get("very_old_years", 3)
        old_years = self.config.get("age_buckets", {}).get("old_years", 1)
        top_n = self.config.get("output", {}).get("top_n_repos", 30)
        bottom_n = self.config.get("output", {}).get("bottom_n_repos", 30)

        # Primary time window for rankings (usually last_365_days)
        primary_window = "last_365_days"

        # Classify repositories by activity
        active_repos = []
        inactive_repos = []
        very_old_repos = []
        old_repos = []
        recent_inactive_repos = []

        total_commits = 0

        for repo in repo_metrics:
            days_since_last = repo.get("days_since_last_commit")
            if days_since_last is None:
                days_since_last = float('inf')  # Treat None as very old
            is_active = days_since_last <= activity_threshold_days

            # Count total commits
            total_commits += repo.get("commits", {}).get(primary_window, 0)

            if is_active:
                active_repos.append(repo)
            else:
                inactive_repos.append(repo)

                # Categorize inactive repositories by age
                days_to_years = 365.25
                if days_since_last == float('inf'):
                    # Repositories with no commits go to very old
                    very_old_repos.append(repo)
                else:
                    age_years = days_since_last / days_to_years
                    if age_years > very_old_years:
                        very_old_repos.append(repo)
                    elif age_years > old_years:
                        old_repos.append(repo)
                    else:
                        recent_inactive_repos.append(repo)

        # Aggregate author and organization data
        self.logger.info("Computing author rollups")
        authors = self.compute_author_rollups(repo_metrics)

        self.logger.info("Computing organization rollups")
        organizations = self.compute_org_rollups(authors)

        # Build top active repositories (sorted by commits in primary window)
        top_active = self.rank_entities(
            active_repos,
            f"commits.{primary_window}",
            reverse=True,
            limit=top_n
        )

        # Build least active repositories (inactive sorted by days since last commit, descending)
        least_active = self.rank_entities(
            inactive_repos,
            "days_since_last_commit",
            reverse=True,
            limit=bottom_n
        )

        # Build contributor leaderboards
        top_contributors_commits = self.rank_entities(
            authors,
            f"commits.{primary_window}",
            reverse=True,
            limit=top_n
        )

        top_contributors_loc = self.rank_entities(
            authors,
            f"lines_net.{primary_window}",
            reverse=True,
            limit=top_n
        )

        # Build organization leaderboard
        top_organizations = self.rank_entities(
            organizations,
            f"commits.{primary_window}",
            reverse=True,
            limit=top_n
        )

        # Build comprehensive summaries
        summaries = {
            "counts": {
                "total_repositories": len(repo_metrics),
                "active_repositories": len(active_repos),
                "inactive_repositories": len(inactive_repos),
                "total_commits": total_commits,
                "total_authors": len(authors),
                "total_organizations": len(organizations),
            },
            "activity_distribution": {
                "very_old": [{"name": r.get("name", ""), "days_since_last_commit": r.get("days_since_last_commit") if r.get("days_since_last_commit") is not None else 999999} for r in very_old_repos],
                "old": [{"name": r.get("name", ""), "days_since_last_commit": r.get("days_since_last_commit") if r.get("days_since_last_commit") is not None else 999999} for r in old_repos],
                "recent_inactive": [{"name": r.get("name", ""), "days_since_last_commit": r.get("days_since_last_commit") if r.get("days_since_last_commit") is not None else 999999} for r in recent_inactive_repos],
            },
            "top_active_repositories": top_active,
            "least_active_repositories": least_active,
            "top_contributors_commits": top_contributors_commits,
            "top_contributors_loc": top_contributors_loc,
            "top_organizations": top_organizations,
        }

        self.logger.info(f"Aggregation complete: {len(active_repos)} active, {len(inactive_repos)} inactive repositories")
        self.logger.info(f"Found {len(authors)} authors across {len(organizations)} organizations")

        return summaries

    def compute_author_rollups(self, repo_metrics: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Aggregate author metrics across all repositories.

        Merges author data by email address, summing metrics across all repos
        and tracking unique repositories touched per time window.
        """
        from collections import defaultdict
        author_aggregates: DefaultDict[str, Dict[str, Any]] = defaultdict(lambda: {
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
            repo_name = repo.get("name", "unknown")

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
                    author_aggregates[email]["domain"] = email.split("@")[-1] if "@" in email else ""

                # Aggregate metrics for each time window
                for window_name in author.get("commits", {}):
                    author_aggregates[email]["repositories_touched"][window_name].add(repo_name)
                    author_aggregates[email]["commits"][window_name] += author.get("commits", {}).get(window_name, 0)
                    author_aggregates[email]["lines_added"][window_name] += author.get("lines_added", {}).get(window_name, 0)
                    author_aggregates[email]["lines_removed"][window_name] += author.get("lines_removed", {}).get(window_name, 0)
                    author_aggregates[email]["lines_net"][window_name] += author.get("lines_net", {}).get(window_name, 0)

        # Convert to list format and finalize repository counts
        authors = []
        for email, data in author_aggregates.items():
            author_record = {
                "name": data["name"],
                "email": data["email"],
                "username": data["username"],
                "domain": data["domain"],
                "commits": dict(data["commits"]),
                "lines_added": dict(data["lines_added"]),
                "lines_removed": dict(data["lines_removed"]),
                "lines_net": dict(data["lines_net"]),
                "repositories_count": {
                    window: len(repos) for window, repos in data["repositories_touched"].items()
                },
            }
            authors.append(author_record)

        self.logger.info(f"Aggregated {len(authors)} unique authors across repositories")
        return authors

    def compute_org_rollups(self, authors: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Aggregate organization metrics from author data.

        Groups authors by email domain and aggregates their contributions.
        """
        from collections import defaultdict
        org_aggregates: DefaultDict[str, Dict[str, Any]] = defaultdict(lambda: {
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
            org_aggregates[domain]["contributors"].add(author.get("email", ""))

            # Sum metrics across all time windows
            for window_name in author.get("commits", {}):
                org_aggregates[domain]["commits"][window_name] += author.get("commits", {}).get(window_name, 0)
                org_aggregates[domain]["lines_added"][window_name] += author.get("lines_added", {}).get(window_name, 0)
                org_aggregates[domain]["lines_removed"][window_name] += author.get("lines_removed", {}).get(window_name, 0)
                org_aggregates[domain]["lines_net"][window_name] += author.get("lines_net", {}).get(window_name, 0)

                # Track repositories (approximate - we don't have per-author repo mapping here)
                repo_count = author.get("repositories_count", {}).get(window_name, 0)
                if repo_count > 0:
                    # This is an approximation - we can't perfectly reconstruct which repos
                    # Just use a placeholder set expansion
                    for i in range(repo_count):
                        org_aggregates[domain]["repositories_count"][window_name].add(f"repo_{i}_{author.get('email', '')}")

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

    def rank_entities(self, entities: List[Dict[str, Any]], sort_key: str, reverse: bool = True, limit: Optional[int] = None) -> List[Dict[str, Any]]:
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
                return value
            else:
                return entity.get(sort_key, 0)

        def get_name(entity):
            """Extract name for tie-breaking."""
            return entity.get("name") or entity.get("domain") or entity.get("email") or ""

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
    """Handles rendering of reports in various formats."""

    def __init__(self, config: Dict[str, Any], logger: logging.Logger):
        self.config = config
        self.logger = logger

    def render_json_report(self, data: Dict[str, Any], output_path: Path) -> None:
        """
        Write the canonical JSON report.

        TODO: Implement in Phase 5
        """
        self.logger.info(f"Writing JSON report to {output_path}")

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)

    def render_markdown_report(self, data: Dict[str, Any], output_path: Path) -> str:
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

    def _generate_markdown_content(self, data: Dict[str, Any]) -> str:
        """Generate complete Markdown content from JSON data."""
        include_sections = self.config.get("output", {}).get("include_sections", {})

        sections = []

        # Title and metadata
        sections.append(self._generate_title_section(data))

        # Global summary
        sections.append(self._generate_summary_section(data))

        # Activity distribution
        if include_sections.get("inactive_distributions", True):
            sections.append(self._generate_activity_distribution_section(data))

        # Top active repositories
        sections.append(self._generate_top_repositories_section(data))

        # Least active repositories
        sections.append(self._generate_least_active_repositories_section(data))

        # Contributors
        if include_sections.get("contributors", True):
            sections.append(self._generate_contributors_section(data))

        # Organizations
        if include_sections.get("organizations", True):
            sections.append(self._generate_organizations_section(data))

        # Repository feature matrix
        if include_sections.get("repo_feature_matrix", True):
            sections.append(self._generate_feature_matrix_section(data))

        # Appendix
        sections.append(self._generate_appendix_section(data))

        return "\n\n".join(sections)

    def _generate_title_section(self, data: Dict[str, Any]) -> str:
        """Generate title and metadata section."""
        project = data.get("project", "Repository Analysis")
        generated_at = data.get("generated_at", "")
        total_repos = data.get("summaries", {}).get("counts", {}).get("total_repositories", 0)
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

        return f"""# 📊 Repository Analysis Report: {project}

**Generated:** {formatted_time}
**Repositories Analyzed:** {total_repos:,} ({active_repos:,} active)
**Contributors Found:** {total_authors:,}
**Schema Version:** {data.get("schema_version", "1.0.0")}"""

    def _generate_summary_section(self, data: Dict[str, Any]) -> str:
        """Generate global summary statistics section."""
        counts = data.get("summaries", {}).get("counts", {})

        total_repos = counts.get("total_repositories", 0)
        active_repos = counts.get("active_repositories", 0)
        inactive_repos = counts.get("inactive_repositories", 0)
        total_commits = counts.get("total_commits", 0)
        total_authors = counts.get("total_authors", 0)
        total_orgs = counts.get("total_organizations", 0)

        # Calculate percentages
        active_pct = (active_repos / total_repos * 100) if total_repos > 0 else 0
        inactive_pct = (inactive_repos / total_repos * 100) if total_repos > 0 else 0

        return f"""## 📈 Global Summary

| Metric | Count | Percentage |
|--------|-------|------------|
| **Total Repositories** | {self._format_number(total_repos)} | 100% |
| **Active Repositories** | {self._format_number(active_repos)} | {active_pct:.1f}% |
| **Inactive Repositories** | {self._format_number(inactive_repos)} | {inactive_pct:.1f}% |
| **Total Contributors** | {self._format_number(total_authors)} | - |
| **Organizations** | {self._format_number(total_orgs)} | - |
| **Total Commits** | {self._format_number(total_commits)} | - |"""

    def _generate_activity_distribution_section(self, data: Dict[str, Any]) -> str:
        """Generate activity distribution section."""
        activity_dist = data.get("summaries", {}).get("activity_distribution", {})

        very_old = activity_dist.get("very_old", [])
        old = activity_dist.get("old", [])
        recent_inactive = activity_dist.get("recent_inactive", [])

        if not (very_old or old or recent_inactive):
            return "## 📅 Activity Distribution\n\nAll repositories are currently active! 🎉"

        sections = ["## 📅 Activity Distribution"]

        if very_old:
            sections.append("### 🔴 Very Old (>3 years inactive)")
            sections.append(self._generate_activity_table(very_old))

        if old:
            sections.append("### 🟡 Old (1-3 years inactive)")
            sections.append(self._generate_activity_table(old))

        if recent_inactive:
            sections.append("### ⚠️ Recent Inactive (<1 year)")
            sections.append(self._generate_activity_table(recent_inactive))

        return "\n\n".join(sections)

    def _generate_activity_table(self, repos: List[Dict]) -> str:
        """Generate activity table for inactive repositories."""
        if not repos:
            return "*No repositories in this category.*"

        # Sort by days since last commit (descending)
        def sort_key(x):
            days = x.get("days_since_last_commit")
            return days if days is not None else 999999
        sorted_repos = sorted(repos, key=sort_key, reverse=True)

        lines = ["| Repository | Days Inactive | Last Activity |",
                 "|------------|---------------|---------------|"]

        for repo in sorted_repos[:20]:  # Limit to top 20
            name = repo.get("name", "Unknown")
            days = repo.get("days_since_last_commit")
            if days is None:
                days = 999999  # Very large number for repos with no commits
            age_str = self._format_age(days)
            lines.append(f"| {name} | {days:,} | {age_str} |")

        if len(repos) > 20:
            lines.append(f"| ... | ... | *({len(repos) - 20:,} more repositories)* |")

        return "\n".join(lines)

    def _generate_top_repositories_section(self, data: Dict[str, Any]) -> str:
        """Generate top active repositories section."""
        top_repos = data.get("summaries", {}).get("top_active_repositories", [])

        if not top_repos:
            return "## 🏆 Top Active Repositories\n\n*No active repositories found.*"

        lines = ["## 🏆 Top Active Repositories",
                 "",
                 "| Repository | Commits (1Y) | Net LOC (1Y) | Contributors | Last Activity | Status |",
                 "|------------|--------------|--------------|--------------|---------------|--------|"]

        for repo in top_repos:
            name = repo.get("name", "Unknown")
            commits_1y = repo.get("commit_counts", {}).get("last_365_days", 0)
            loc_1y = repo.get("loc_stats", {}).get("last_365_days", {}).get("net", 0)
            contributors_1y = repo.get("unique_contributors", {}).get("last_365_days", 0)
            days_since = repo.get("days_since_last_commit")
            if days_since is None:
                days_since = 999999  # Very large number for repos with no commits
            is_active = repo.get("is_active", False)

            age_str = self._format_age(days_since)
            status = "✅" if is_active else "⚠️"

            lines.append(f"| {name} | {self._format_number(commits_1y)} | {self._format_number(loc_1y, signed=True)} | {contributors_1y} | {age_str} | {status} |")

        return "\n".join(lines)

    def _generate_least_active_repositories_section(self, data: Dict[str, Any]) -> str:
        """Generate least active repositories section."""
        least_active = data.get("summaries", {}).get("least_active_repositories", [])

        if not least_active:
            return "## 📉 Least Active Repositories\n\n*All repositories are active!*"

        lines = ["## 📉 Least Active Repositories",
                 "",
                 "| Repository | Days Inactive | Last Commits (1Y) | Last Activity | Age Category |",
                 "|------------|---------------|-------------------|---------------|--------------|"]

        for repo in least_active:
            name = repo.get("name", "Unknown")
            days_since = repo.get("days_since_last_commit")
            if days_since is None:
                days_since = 999999  # Very large number for repos with no commits
            commits_1y = repo.get("commit_counts", {}).get("last_365_days", 0)
            age_str = self._format_age(days_since)

            # Categorize by age
            if days_since > (3 * 365):
                category = "🔴 Very Old"
            elif days_since > 365:
                category = "🟡 Old"
            else:
                category = "⚠️ Recent"

            lines.append(f"| {name} | {days_since:,} | {commits_1y} | {age_str} | {category} |")

        return "\n".join(lines)

    def _generate_contributors_section(self, data: Dict[str, Any]) -> str:
        """Generate contributors leaderboards section."""
        top_commits = data.get("summaries", {}).get("top_contributors_commits", [])
        top_loc = data.get("summaries", {}).get("top_contributors_loc", [])

        sections = ["## 👥 Top Contributors"]

        # Top by commits
        if top_commits:
            sections.append("### 🏅 Most Active by Commits (Last Year)")
            sections.append(self._generate_contributors_table(top_commits, "commits"))

        # Top by lines of code
        if top_loc:
            sections.append("### 📝 Most Active by Lines of Code (Last Year)")
            sections.append(self._generate_contributors_table(top_loc, "lines"))

        if not top_commits and not top_loc:
            sections.append("*No contributor data available.*")

        return "\n\n".join(sections)

    def _generate_contributors_table(self, contributors: List[Dict], metric_type: str) -> str:
        """Generate contributors table for commits or LOC."""
        if not contributors:
            return "*No contributors found.*"

        if metric_type == "commits":
            lines = ["| Rank | Contributor | Commits (1Y) | Repositories | Organization |",
                     "|------|-------------|--------------|--------------|--------------|"]
        else:
            lines = ["| Rank | Contributor | Net LOC (1Y) | Commits (1Y) | Repositories | Organization |",
                     "|------|-------------|---------------|--------------|--------------|--------------|"]

        for i, contributor in enumerate(contributors, 1):
            name = contributor.get("name", "Unknown")
            email = contributor.get("email", "")
            domain = contributor.get("domain", "")
            commits_1y = contributor.get("commits", {}).get("last_365_days", 0)
            loc_1y = contributor.get("lines_net", {}).get("last_365_days", 0)
            repos_1y = contributor.get("repositories_count", {}).get("last_365_days", 0)

            # Mask email for privacy (show first part + domain)
            if email and "@" in email:
                username = email.split("@")[0]
                display_name = f"{name} ({username}@...)"
            else:
                display_name = name

            org_display = domain if domain and domain != "unknown" else "-"

            if metric_type == "commits":
                lines.append(f"| {i} | {display_name} | {self._format_number(commits_1y)} | {repos_1y} | {org_display} |")
            else:
                lines.append(f"| {i} | {display_name} | {self._format_number(loc_1y, signed=True)} | {self._format_number(commits_1y)} | {repos_1y} | {org_display} |")

        return "\n".join(lines)

    def _generate_organizations_section(self, data: Dict[str, Any]) -> str:
        """Generate organizations leaderboard section."""
        top_orgs = data.get("summaries", {}).get("top_organizations", [])

        if not top_orgs:
            return "## 🏢 Organizations\n\n*No organization data available.*"

        lines = ["## 🏢 Top Organizations (Last Year)",
                 "",
                 "| Rank | Organization | Contributors | Commits | Net LOC | Repositories |",
                 "|------|--------------|--------------|---------|---------|--------------|"]

        for i, org in enumerate(top_orgs, 1):
            domain = org.get("domain", "Unknown")
            contributors = org.get("contributor_count", 0)
            commits_1y = org.get("commits", {}).get("last_365_days", 0)
            loc_1y = org.get("lines_net", {}).get("last_365_days", 0)
            repos_1y = org.get("repositories_count", {}).get("last_365_days", 0)

            lines.append(f"| {i} | {domain} | {contributors} | {self._format_number(commits_1y)} | {self._format_number(loc_1y, signed=True)} | {repos_1y} |")

        return "\n".join(lines)

    def _generate_feature_matrix_section(self, data: Dict[str, Any]) -> str:
        """Generate repository feature matrix section."""
        repositories = data.get("repositories", [])

        if not repositories:
            return "## 🔧 Repository Features\n\n*No repositories analyzed.*"

        # Sort repositories by primary metric (commits in last year)
        sorted_repos = sorted(repositories,
                            key=lambda x: x.get("commit_counts", {}).get("last_365_days", 0),
                            reverse=True)

        lines = ["## 🔧 Repository Feature Matrix",
                 "",
                 "| Repository | Type | Dependabot | Pre-commit | ReadTheDocs | Workflows | Active |",
                 "|------------|------|------------|------------|-------------|-----------|--------|"]

        for repo in sorted_repos:
            name = repo.get("name", "Unknown")
            features = repo.get("features", {})
            is_active = repo.get("is_active", False)

            # Extract feature status
            project_types = features.get("project_types", {})
            primary_type = project_types.get("primary_type", "unknown")

            dependabot = "✅" if features.get("dependabot", {}).get("present", False) else "❌"
            pre_commit = "✅" if features.get("pre_commit", {}).get("present", False) else "❌"
            readthedocs = "✅" if features.get("readthedocs", {}).get("present", False) else "❌"

            workflows = features.get("workflows", {}).get("count", 0)
            workflow_display = f"{workflows}" if workflows > 0 else "❌"

            status = "✅" if is_active else "⚠️"

            lines.append(f"| {name} | {primary_type} | {dependabot} | {pre_commit} | {readthedocs} | {workflow_display} | {status} |")

        return "\n".join(lines)

    def _generate_appendix_section(self, data: Dict[str, Any]) -> str:
        """Generate appendix with metadata and configuration."""
        config_digest = data.get("config_digest", "")[:12]
        schema_version = data.get("schema_version", "1.0.0")
        script_version = data.get("script_version", "1.0.0")

        time_windows = data.get("time_windows", {})
        window_info = []
        for name, window in time_windows.items():
            days = window.get("days", 0)
            window_info.append(f"- **{name}**: {days} days")

        windows_text = "\n".join(window_info) if window_info else "- Default time windows"

        return f"""## 📋 Report Metadata

**Configuration Digest:** `{config_digest}...`
**Schema Version:** {schema_version}
**Script Version:** {script_version}

**Time Windows:**
{windows_text}

**Report Generation:** This report was generated by the Repository Reporting System, analyzing Git repository data and features to provide comprehensive insights into project activity, contributor patterns, and development practices.

---
*Generated with ❤️ by Repository Reporting System*"""

    def _convert_markdown_to_html(self, markdown_content: str) -> str:
        """Convert Markdown content to HTML with embedded CSS."""

        # Simple Markdown to HTML conversion
        html_body = self._simple_markdown_to_html(markdown_content)

        html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Repository Analysis Report</title>
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
    </style>
</head>
<body>
    {html_body}
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
                    html_lines.append('<table>')
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
        """Format age in days to human readable string."""
        if days == 0:
            return "Today"
        elif days == 1:
            return "1 day ago"
        elif days < 7:
            return f"{days} days ago"
        elif days < 30:
            weeks = days // 7
            return f"{weeks} week{'s' if weeks != 1 else ''} ago"
        elif days < 365:
            months = days // 30
            return f"{months} month{'s' if months != 1 else ''} ago"
        else:
            years = days // 365
            return f"{years} year{'s' if years != 1 else ''} ago"



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
    """Format age in days to human-readable string."""
    if days == 0:
        return "today"
    elif days == 1:
        return "1 day ago"
    elif days < 30:
        return f"{days} days ago"
    elif days < 365:
        months = days // 30
        return f"{months} month{'s' if months != 1 else ''} ago"
    else:
        years = days // 365
        return f"{years} year{'s' if years != 1 else ''} ago"

def safe_git_command(command: List[str], repo_path: Path, logger: logging.Logger) -> Tuple[bool, str]:
    """
    Execute a git command safely with error handling.

    Returns:
        (success: bool, output_or_error: str)
    """
    try:
        result = subprocess.run(
            command,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
            check=True
        )
        return True, result.stdout
    except subprocess.CalledProcessError as e:
        logger.warning(f"Git command failed in {repo_path}: {' '.join(command)} - {e.stderr}")
        return False, e.stderr
    except subprocess.TimeoutExpired:
        logger.warning(f"Git command timed out in {repo_path}: {' '.join(command)}")
        return False, "Command timed out"
    except Exception as e:
        logger.warning(f"Unexpected error running git command in {repo_path}: {e}")
        return False, str(e)

# =============================================================================
# MAIN ORCHESTRATION AND CLI ENTRY POINT
# =============================================================================

class RepositoryReporter:
    """Main orchestrator for repository reporting."""

    def __init__(self, config: Dict[str, Any], logger: logging.Logger):
        self.config = config
        self.logger = logger
        self.git_collector = GitDataCollector(config, {}, logger)
        self.feature_registry = FeatureRegistry(config, logger)
        self.aggregator = DataAggregator(config, logger)
        self.renderer = ReportRenderer(config, logger)

    def analyze_repositories(self, repos_path: Path) -> Dict[str, Any]:
        """
        Main analysis workflow.

        TODO: Coordinate all phases
        """
        self.logger.info(f"Starting repository analysis in {repos_path}")

        # Initialize data structure
        report_data = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "project": self.config["project"],
            "config_digest": compute_config_digest(self.config),
            "script_version": SCRIPT_VERSION,
            "time_windows": compute_time_windows(self.config),
            "repositories": [],
            "authors": [],
            "organizations": [],
            "summaries": {},
            "errors": []
        }

        # Update git collector with time windows
        self.git_collector.time_windows = report_data["time_windows"]

        # Find all repository directories
        repo_dirs = self._discover_repositories(repos_path)
        self.logger.info(f"Found {len(repo_dirs)} repositories to analyze")

        # Analyze repositories (with concurrency)
        repo_metrics = self._analyze_repositories_concurrent(repo_dirs)

        # Extract successful metrics and errors
        successful_repos = []
        for metrics in repo_metrics:
            if "error" in metrics:
                report_data["errors"].append(metrics)
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

    def generate_reports(self, repos_path: Path, output_dir: Path) -> Dict[str, Path]:
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

    def _discover_repositories(self, repos_path: Path) -> List[Path]:
        """Find all repository directories."""
        if not repos_path.exists():
            raise FileNotFoundError(f"Repository path does not exist: {repos_path}")

        repo_dirs = []
        for item in repos_path.iterdir():
            if item.is_dir() and not item.name.startswith('.'):
                # Check if it's a git repository
                git_dir = item / ".git"
                if git_dir.exists():
                    repo_dirs.append(item)
                else:
                    self.logger.debug(f"Skipping non-git directory: {item.name}")

        return sorted(repo_dirs)

    def _analyze_repositories_concurrent(self, repo_dirs: List[Path]) -> List[Dict[str, Any]]:
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

    def _analyze_single_repository(self, repo_dir: Path) -> Dict[str, Any]:
        """Analyze a single repository."""
        try:
            self.logger.debug(f"Analyzing repository: {repo_dir.name}")

            # Collect Git metrics
            git_metrics = self.git_collector.collect_repo_git_metrics(repo_dir)

            # Scan features
            features = self.feature_registry.scan_repository_features(repo_dir)
            git_metrics["repository"]["features"] = features

            return git_metrics

        except Exception as e:
            self.logger.error(f"Error analyzing {repo_dir.name}: {e}")
            return {
                "error": str(e),
                "repo": repo_dir.name,
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
