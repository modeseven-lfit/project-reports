#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 The Linux Foundation

"""
Test script for Phase 3 functionality of the Repository Reporting System.

This script validates:
- Dependabot configuration detection
- GitHub to Gerrit workflow pattern detection
- Pre-commit configuration detection
- ReadTheDocs configuration detection
- Sonatype configuration detection
- Project type detection and classification
- GitHub workflow analysis and classification
- Feature registry extensibility
"""

import sys
import tempfile
import shutil
import json
from pathlib import Path
from typing import Dict, Any

# Add the project root to Python path to import our module
sys.path.insert(0, str(Path(__file__).parent))

try:
    from generate_reports import (
        FeatureRegistry,
        setup_logging,
        DEFAULT_TIME_WINDOWS
    )
except ImportError as e:
    print(f"ERROR: Failed to import from generate_reports.py: {e}")
    print("Make sure generate_reports.py is in the same directory as this test script.")
    sys.exit(1)

def create_test_config():
    """Create a test configuration for the feature registry."""
    return {
        "features": {
            "enabled": [
                "dependabot",
                "github2gerrit_workflow",
                "pre_commit",
                "readthedocs",
                "sonatype_config",
                "project_types",
                "workflows"
            ]
        },
        "workflows": {
            "classify": {
                "verify": ["verify", "test", "ci", "check"],
                "merge": ["merge", "release", "deploy", "publish"]
            }
        }
    }

def create_test_repository(files_to_create: Dict[str, str]) -> Path:
    """Create a temporary repository with specified files."""
    temp_dir = Path(tempfile.mkdtemp())

    for file_path, content in files_to_create.items():
        full_path = temp_dir / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)

        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)

    return temp_dir

def test_dependabot_detection():
    """Test Dependabot configuration detection."""
    print("Testing Dependabot detection...")

    config = create_test_config()
    logger = setup_logging("DEBUG", False)
    registry = FeatureRegistry(config, logger)

    # Test with Dependabot config present (.yml)
    dependabot_config_yml = """version: 2
updates:
  - package-ecosystem: "npm"
    directory: "/"
    schedule:
      interval: "daily"
  - package-ecosystem: "pip"
    directory: "/python"
    schedule:
      interval: "weekly"
"""

    repo_with_dependabot = create_test_repository({
        ".github/dependabot.yml": dependabot_config_yml,
        "package.json": '{"name": "test"}',
        "requirements.txt": "requests==2.28.0"
    })

    try:
        result = registry._check_dependabot(repo_with_dependabot)
        assert result["present"] is True, "Should detect Dependabot config"
        assert ".github/dependabot.yml" in result["files"], "Should find the config file"
    finally:
        shutil.rmtree(repo_with_dependabot)

    # Test with Dependabot config present (.yaml)
    repo_with_dependabot_yaml = create_test_repository({
        ".github/dependabot.yaml": dependabot_config_yml
    })

    try:
        result = registry._check_dependabot(repo_with_dependabot_yaml)
        assert result["present"] is True, "Should detect Dependabot YAML config"
        assert ".github/dependabot.yaml" in result["files"], "Should find the YAML config file"
    finally:
        shutil.rmtree(repo_with_dependabot_yaml)

    # Test without Dependabot config
    repo_without_dependabot = create_test_repository({
        "README.md": "# Test Project",
        "src/main.py": "print('hello')"
    })

    try:
        result = registry._check_dependabot(repo_without_dependabot)
        assert result["present"] is False, "Should not detect Dependabot when absent"
        assert len(result["files"]) == 0, "Should have no config files"
    finally:
        shutil.rmtree(repo_without_dependabot)

    print("  ✅ Dependabot detection works correctly")

def test_github2gerrit_workflow_detection():
    """Test GitHub to Gerrit workflow pattern detection."""
    print("Testing GitHub to Gerrit workflow detection...")

    config = create_test_config()
    logger = setup_logging("DEBUG", False)
    registry = FeatureRegistry(config, logger)

    # Test with Gerrit workflow present
    gerrit_workflow = """name: Gerrit Review
on: [push, pull_request]
jobs:
  gerrit-review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Submit to Gerrit
        run: |
          git review -R
          gerrit submit
"""

    repo_with_gerrit = create_test_repository({
        ".github/workflows/gerrit-review.yml": gerrit_workflow,
        ".github/workflows/ci.yml": """name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo "normal CI"
"""
    })

    try:
        result = registry._check_github2gerrit_workflow(repo_with_gerrit)
        assert result["present"] is True, "Should detect Gerrit workflow"
        assert len(result["workflows"]) > 0, "Should find matching workflows"

        # Check that it found the right file
        gerrit_files = [w["file"] for w in result["workflows"]]
        assert "gerrit-review.yml" in gerrit_files, "Should detect gerrit-review.yml"
    finally:
        shutil.rmtree(repo_with_gerrit)

    # Test without Gerrit workflows
    repo_without_gerrit = create_test_repository({
        ".github/workflows/ci.yml": """name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: echo "test"
"""
    })

    try:
        result = registry._check_github2gerrit_workflow(repo_without_gerrit)
        assert result["present"] is False, "Should not detect Gerrit when absent"
        assert len(result["workflows"]) == 0, "Should have no matching workflows"
    finally:
        shutil.rmtree(repo_without_gerrit)

    print("  ✅ GitHub to Gerrit workflow detection works correctly")

def test_precommit_detection():
    """Test pre-commit configuration detection."""
    print("Testing pre-commit detection...")

    config = create_test_config()
    logger = setup_logging("DEBUG", False)
    registry = FeatureRegistry(config, logger)

    # Test with pre-commit config present
    precommit_config = """repos:
  - repo: https://github.com/psf/black
    rev: 22.3.0
    hooks:
      - id: black
  - repo: https://github.com/pycqa/flake8
    rev: 4.0.1
    hooks:
      - id: flake8
  - repo: https://github.com/pycqa/isort
    rev: 5.10.1
    hooks:
      - id: isort
"""

    repo_with_precommit = create_test_repository({
        ".pre-commit-config.yaml": precommit_config,
        "setup.py": "from setuptools import setup; setup()"
    })

    try:
        result = registry._check_pre_commit(repo_with_precommit)
        assert result["present"] is True, "Should detect pre-commit config"
        assert result["config_file"] == ".pre-commit-config.yaml", "Should find the config file"
        assert "repos_count" in result, "Should extract repo count"
        assert result["repos_count"] == 3, "Should count 3 repos"
    finally:
        shutil.rmtree(repo_with_precommit)

    # Test with .yml extension
    repo_with_precommit_yml = create_test_repository({
        ".pre-commit-config.yml": precommit_config
    })

    try:
        result = registry._check_pre_commit(repo_with_precommit_yml)
        assert result["present"] is True, "Should detect pre-commit YML config"
        assert result["config_file"] == ".pre-commit-config.yml", "Should find the YML config file"
    finally:
        shutil.rmtree(repo_with_precommit_yml)

    # Test without pre-commit config
    repo_without_precommit = create_test_repository({
        "README.md": "# Test Project"
    })

    try:
        result = registry._check_pre_commit(repo_without_precommit)
        assert result["present"] is False, "Should not detect pre-commit when absent"
        assert result["config_file"] is None, "Should have no config file"
    finally:
        shutil.rmtree(repo_without_precommit)

    print("  ✅ Pre-commit detection works correctly")

def test_readthedocs_detection():
    """Test ReadTheDocs configuration detection."""
    print("Testing ReadTheDocs detection...")

    config = create_test_config()
    logger = setup_logging("DEBUG", False)
    registry = FeatureRegistry(config, logger)

    # Test with RTD config file
    rtd_config = """version: 2
build:
  os: ubuntu-20.04
  tools:
    python: "3.8"
python:
  install:
    - requirements: docs/requirements.txt
"""

    repo_with_rtd = create_test_repository({
        ".readthedocs.yml": rtd_config,
        "docs/requirements.txt": "sphinx\nsphinx-rtd-theme",
        "docs/conf.py": "# Sphinx configuration"
    })

    try:
        result = registry._check_readthedocs(repo_with_rtd)
        assert result["present"] is True, "Should detect RTD config"
        assert result["config_type"] == "readthedocs", "Should identify RTD config type"
        assert ".readthedocs.yml" in result["config_files"], "Should find RTD config file"
        assert "docs/conf.py" in result["config_files"], "Should also find Sphinx config"
    finally:
        shutil.rmtree(repo_with_rtd)

    # Test with only Sphinx config
    repo_with_sphinx = create_test_repository({
        "docs/conf.py": """# Sphinx configuration file
project = 'Test Project'
author = 'Test Author'
extensions = ['sphinx.ext.autodoc']
"""
    })

    try:
        result = registry._check_readthedocs(repo_with_sphinx)
        assert result["present"] is True, "Should detect Sphinx config"
        assert result["config_type"] == "sphinx", "Should identify Sphinx config type"
        assert "docs/conf.py" in result["config_files"], "Should find Sphinx config"
    finally:
        shutil.rmtree(repo_with_sphinx)

    # Test with MkDocs config
    mkdocs_config = """site_name: My Docs
nav:
  - Home: index.md
  - About: about.md
theme: material
"""

    repo_with_mkdocs = create_test_repository({
        "mkdocs.yml": mkdocs_config,
        "docs/index.md": "# Welcome"
    })

    try:
        result = registry._check_readthedocs(repo_with_mkdocs)
        assert result["present"] is True, "Should detect MkDocs config"
        assert result["config_type"] == "mkdocs", "Should identify MkDocs config type"
        assert "mkdocs.yml" in result["config_files"], "Should find MkDocs config"
    finally:
        shutil.rmtree(repo_with_mkdocs)

    # Test without documentation config
    repo_without_docs = create_test_repository({
        "README.md": "# No docs config"
    })

    try:
        result = registry._check_readthedocs(repo_without_docs)
        assert result["present"] is False, "Should not detect docs when absent"
        assert result["config_type"] is None, "Should have no config type"
        assert len(result["config_files"]) == 0, "Should have no config files"
    finally:
        shutil.rmtree(repo_without_docs)

    print("  ✅ ReadTheDocs detection works correctly")

def test_sonatype_detection():
    """Test Sonatype configuration detection."""
    print("Testing Sonatype detection...")

    config = create_test_config()
    logger = setup_logging("DEBUG", False)
    registry = FeatureRegistry(config, logger)

    # Test with Sonatype Lift config
    lift_config = """version: "1"
discovery:
  include:
    - "**/*.java"
    - "**/*.js"
  exclude:
    - "**/node_modules/**"
"""

    repo_with_sonatype = create_test_repository({
        ".sonatype-lift.yaml": lift_config,
        "src/Main.java": "public class Main { }"
    })

    try:
        result = registry._check_sonatype_config(repo_with_sonatype)
        assert result["present"] is True, "Should detect Sonatype config"
        assert ".sonatype-lift.yaml" in result["config_files"], "Should find Lift config"
    finally:
        shutil.rmtree(repo_with_sonatype)

    # Test with TOML config
    repo_with_lift_toml = create_test_repository({
        "lift.toml": """[discovery]
include = ["**/*.py"]
"""
    })

    try:
        result = registry._check_sonatype_config(repo_with_lift_toml)
        assert result["present"] is True, "Should detect TOML Lift config"
        assert "lift.toml" in result["config_files"], "Should find TOML config"
    finally:
        shutil.rmtree(repo_with_lift_toml)

    # Test without Sonatype config
    repo_without_sonatype = create_test_repository({
        "README.md": "# No Sonatype config"
    })

    try:
        result = registry._check_sonatype_config(repo_without_sonatype)
        assert result["present"] is False, "Should not detect Sonatype when absent"
        assert len(result["config_files"]) == 0, "Should have no config files"
    finally:
        shutil.rmtree(repo_without_sonatype)

    print("  ✅ Sonatype detection works correctly")

def test_project_type_detection():
    """Test project type detection and classification."""
    print("Testing project type detection...")

    config = create_test_config()
    logger = setup_logging("DEBUG", False)
    registry = FeatureRegistry(config, logger)

    # Test Python project
    repo_python = create_test_repository({
        "pyproject.toml": """[build-system]
requires = ["setuptools", "wheel"]
build-backend = "setuptools.build_meta"
""",
        "requirements.txt": "requests==2.28.0\nflask==2.0.1",
        "src/main.py": "print('hello')"
    })

    try:
        result = registry._check_project_types(repo_python)
        assert "python" in result["detected_types"], "Should detect Python project"
        assert result["primary_type"] == "python", "Should identify Python as primary type"

        # Check details
        python_detail = next(d for d in result["details"] if d["type"] == "python")
        assert "pyproject.toml" in python_detail["files"], "Should find pyproject.toml"
        assert "requirements.txt" in python_detail["files"], "Should find requirements.txt"
    finally:
        shutil.rmtree(repo_python)

    # Test Node.js project
    repo_node = create_test_repository({
        "package.json": """{
  "name": "test-project",
  "version": "1.0.0",
  "dependencies": {
    "express": "^4.18.0"
  }
}""",
        "src/index.js": "console.log('hello');"
    })

    try:
        result = registry._check_project_types(repo_node)
        assert "node" in result["detected_types"], "Should detect Node.js project"
        assert result["primary_type"] == "node", "Should identify Node as primary type"
    finally:
        shutil.rmtree(repo_node)

    # Test Maven Java project
    repo_maven = create_test_repository({
        "pom.xml": """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
    <modelVersion>4.0.0</modelVersion>
    <groupId>com.example</groupId>
    <artifactId>test-project</artifactId>
    <version>1.0.0</version>
</project>""",
        "src/main/java/Main.java": "public class Main { }"
    })

    try:
        result = registry._check_project_types(repo_maven)
        assert "maven" in result["detected_types"], "Should detect Maven project"
        assert result["primary_type"] == "maven", "Should identify Maven as primary type"
    finally:
        shutil.rmtree(repo_maven)

    # Test multi-language project (Python + Docker)
    repo_multi = create_test_repository({
        "Dockerfile": """FROM python:3.9
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "app.py"]
""",
        "requirements.txt": "flask==2.0.1",
        "app.py": "from flask import Flask\napp = Flask(__name__)"
    })

    try:
        result = registry._check_project_types(repo_multi)
        assert "python" in result["detected_types"], "Should detect Python in multi-lang project"
        assert "docker" in result["detected_types"], "Should detect Docker in multi-lang project"
        # Primary type should be the one with highest confidence (more files)
        assert result["primary_type"] in ["python", "docker"], "Should pick a primary type"
    finally:
        shutil.rmtree(repo_multi)

    # Test project with no recognized types
    repo_unknown = create_test_repository({
        "README.md": "# Unknown project type",
        "data.txt": "some data",
        "config.ini": "[section]\nkey=value"
    })

    try:
        result = registry._check_project_types(repo_unknown)
        assert len(result["detected_types"]) == 0, "Should detect no project types"
        assert result["primary_type"] is None, "Should have no primary type"
    finally:
        shutil.rmtree(repo_unknown)

    print("  ✅ Project type detection works correctly")

def test_workflow_analysis():
    """Test GitHub workflow analysis and classification."""
    print("Testing workflow analysis...")

    config = create_test_config()
    logger = setup_logging("DEBUG", False)
    registry = FeatureRegistry(config, logger)

    # Test with various workflow types
    verify_workflow = """name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Run tests
        run: pytest
  verify:
    runs-on: ubuntu-latest
    steps:
      - name: Verify build
        run: make verify
"""

    merge_workflow = """name: Release
on:
  push:
    tags:
      - 'v*'
jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Build and publish
        run: |
          npm run build
          npm publish
  deploy:
    runs-on: ubuntu-latest
    steps:
      - name: Deploy to production
        run: deploy.sh
"""

    other_workflow = """name: Weekly Cleanup
on:
  schedule:
    - cron: '0 0 * * 0'
jobs:
  cleanup:
    runs-on: ubuntu-latest
    steps:
      - name: Clean old artifacts
        run: cleanup.sh
"""

    repo_with_workflows = create_test_repository({
        ".github/workflows/ci.yml": verify_workflow,
        ".github/workflows/release.yml": merge_workflow,
        ".github/workflows/cleanup.yaml": other_workflow,
        "src/main.py": "print('hello')"
    })

    try:
        result = registry._check_workflows(repo_with_workflows)
        assert result["count"] == 3, "Should find 3 workflow files"
        assert result["classified"]["verify"] >= 1, "Should classify verify workflows"
        assert result["classified"]["merge"] >= 1, "Should classify merge workflows"
        assert result["classified"]["other"] >= 1, "Should classify other workflows"

        # Check workflow details
        workflow_names = [w["name"] for w in result["files"]]
        assert "ci.yml" in workflow_names, "Should find CI workflow"
        assert "release.yml" in workflow_names, "Should find release workflow"
        assert "cleanup.yaml" in workflow_names, "Should find cleanup workflow"

        # Check classifications
        ci_workflow = next(w for w in result["files"] if w["name"] == "ci.yml")
        assert ci_workflow["classification"] == "verify", "Should classify CI as verify"

        release_workflow = next(w for w in result["files"] if w["name"] == "release.yml")
        assert release_workflow["classification"] == "merge", "Should classify release as merge"

    finally:
        shutil.rmtree(repo_with_workflows)

    # Test repository with no workflows
    repo_no_workflows = create_test_repository({
        "README.md": "# No workflows"
    })

    try:
        result = registry._check_workflows(repo_no_workflows)
        assert result["count"] == 0, "Should find no workflows"
        assert result["classified"]["verify"] == 0, "Should have no verify workflows"
        assert result["classified"]["merge"] == 0, "Should have no merge workflows"
        assert result["classified"]["other"] == 0, "Should have no other workflows"
        assert len(result["files"]) == 0, "Should have empty files list"
    finally:
        shutil.rmtree(repo_no_workflows)

    print("  ✅ Workflow analysis works correctly")

def test_feature_registry_integration():
    """Test the feature registry integration and extensibility."""
    print("Testing feature registry integration...")

    config = create_test_config()
    logger = setup_logging("DEBUG", False)
    registry = FeatureRegistry(config, logger)

    # Test scanning a comprehensive repository
    comprehensive_repo = create_test_repository({
        # Dependabot
        ".github/dependabot.yml": "version: 2\nupdates: []",

        # Pre-commit
        ".pre-commit-config.yaml": "repos:\n  - repo: test",

        # ReadTheDocs
        ".readthedocs.yml": "version: 2",
        "docs/conf.py": "# Sphinx config",

        # Python project
        "pyproject.toml": "[build-system]\nrequires = []",
        "requirements.txt": "requests",

        # Docker
        "Dockerfile": "FROM python:3.9",

        # Workflows
        ".github/workflows/ci.yml": """name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: pytest
""",
        ".github/workflows/deploy.yml": """name: Deploy
on:
  push:
    branches: [main]
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - run: deploy.sh
"""
    })

    try:
        # Scan all features
        features = registry.scan_repository_features(comprehensive_repo)

        # Verify all expected features were detected
        assert features["dependabot"]["present"] is True, "Should detect Dependabot"
        assert features["pre_commit"]["present"] is True, "Should detect pre-commit"
        assert features["readthedocs"]["present"] is True, "Should detect ReadTheDocs"
        assert "python" in features["project_types"]["detected_types"], "Should detect Python"
        assert "docker" in features["project_types"]["detected_types"], "Should detect Docker"
        assert features["workflows"]["count"] == 2, "Should find 2 workflows"

        # Verify feature details
        assert len(features["dependabot"]["files"]) > 0, "Should have Dependabot files"
        assert features["readthedocs"]["config_type"] == "readthedocs", "Should identify RTD config"
        assert features["workflows"]["classified"]["verify"] >= 1, "Should classify CI workflow"
        assert features["workflows"]["classified"]["merge"] >= 1, "Should classify deploy workflow"

    finally:
        shutil.rmtree(comprehensive_repo)

    # Test with disabled features
    config_limited = create_test_config()
    config_limited["features"]["enabled"] = ["dependabot", "project_types"]  # Only enable some features

    registry_limited = FeatureRegistry(config_limited, logger)

    limited_repo = create_test_repository({
        ".github/dependabot.yml": "version: 2",
        ".pre-commit-config.yaml": "repos: []",
        "package.json": '{"name": "test"}'
    })

    try:
        features = registry_limited.scan_repository_features(limited_repo)

        # Should only scan enabled features
        assert "dependabot" in features, "Should scan enabled Dependabot"
        assert "project_types" in features, "Should scan enabled project types"
        assert "pre_commit" not in features, "Should not scan disabled pre-commit"

        # Enabled features should work
        assert features["dependabot"]["present"] is True, "Should detect Dependabot"
        assert "node" in features["project_types"]["detected_types"], "Should detect Node.js"

    finally:
        shutil.rmtree(limited_repo)

    # Test custom feature registration
    def custom_feature_check(repo_path):
        """Custom feature that checks for a specific file."""
        return {
            "present": (repo_path / "CUSTOM_FILE").exists(),
            "custom_data": "test"
        }

    registry.register("custom_feature", custom_feature_check)

    custom_repo = create_test_repository({
        "CUSTOM_FILE": "custom content",
        "README.md": "test"
    })

    try:
        # Add custom feature to enabled list
        config["features"]["enabled"].append("custom_feature")
        registry.config = config

        features = registry.scan_repository_features(custom_repo)

        assert "custom_feature" in features, "Should include custom feature"
        assert features["custom_feature"]["present"] is True, "Should detect custom feature"
        assert features["custom_feature"]["custom_data"] == "test", "Should include custom data"

    finally:
        shutil.rmtree(custom_repo)

    print("  ✅ Feature registry integration works correctly")

def run_all_tests():
    """Run all Phase 3 tests."""
    print("🧪 Running Phase 3 Tests for Repository Reporting System")
    print("   Testing: Feature Scanning & Registry")
    print("-" * 60)

    tests = [
        test_dependabot_detection,
        test_github2gerrit_workflow_detection,
        test_precommit_detection,
        test_readthedocs_detection,
        test_sonatype_detection,
        test_project_type_detection,
        test_workflow_analysis,
        test_feature_registry_integration
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
        print("🎉 All Phase 3 tests passed! Feature detection is working!")
        return True
    else:
        print("💥 Some tests failed. Please fix issues before proceeding to Phase 4.")
        return False

if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
