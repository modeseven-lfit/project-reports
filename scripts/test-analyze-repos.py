#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 The Linux Foundation

"""
Test script for analyze-repos.py

This script creates mock repository data and tests the analysis functionality
to ensure the script works correctly before being used in the CI/CD workflow.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def create_mock_data(base_path):
    """Create mock repository structure and manifest for testing."""
    # Create mock repository directories
    repo_names = [
        "project-alpha",
        "project-beta",
        "project-gamma",
        "common-utils",
        "documentation",
    ]

    repos_path = Path(base_path) / "gerrit-repos"
    repos_path.mkdir(exist_ok=True)

    print(f"Creating mock repositories in: {repos_path}")

    # Create mock repositories with some content
    for repo_name in repo_names:
        repo_dir = repos_path / repo_name
        repo_dir.mkdir(exist_ok=True)

        # Create some mock files
        (repo_dir / "README.md").write_text(
            f"# {repo_name}\n\nMock repository for testing."
        )
        (repo_dir / "src").mkdir(exist_ok=True)
        (repo_dir / "src" / "main.py").write_text(f'print("Hello from {repo_name}")')
        (repo_dir / "docs").mkdir(exist_ok=True)
        (repo_dir / "docs" / "api.md").write_text("# API Documentation")

    # Create mock clone manifest
    manifest_data = {
        "total": len(repo_names),
        "succeeded": len(repo_names) - 1,  # Simulate one failure
        "failed": 1,
        "repositories": {
            repo: {"status": "success" if repo != "documentation" else "failed"}
            for repo in repo_names
        },
    }

    manifest_path = repos_path / "clone-manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest_data, f, indent=2)

    print(f"Created {len(repo_names)} mock repositories")
    print(f"Created clone manifest: {manifest_path}")

    return repos_path


def run_analysis_script(
    repos_path, project_name="TEST-PROJECT", server="test.gerrit.example.com"
):
    """Run the analyze-repos.py script with mock data."""
    script_path = Path(__file__).parent / "analyze-repos.py"

    if not script_path.exists():
        print(f"❌ Analysis script not found: {script_path}")
        return False

    # Set up environment for GitHub Step Summary simulation
    summary_file = repos_path.parent / "github_step_summary.md"
    env = os.environ.copy()
    env["GITHUB_STEP_SUMMARY"] = str(summary_file)

    # Run the analysis script
    cmd = [
        sys.executable,
        str(script_path),
        "--project",
        project_name,
        "--server",
        server,
        "--repos-path",
        str(repos_path),
    ]

    print(f"Running command: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            cwd=repos_path.parent,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        print("📤 Script output:")
        print(result.stdout)

        if result.stderr:
            print("⚠️  Script errors:")
            print(result.stderr)

        # Show GitHub Step Summary content
        if summary_file.exists():
            print("\n📋 GitHub Step Summary content:")
            print("=" * 50)
            print(summary_file.read_text())
            print("=" * 50)

        # Check for output file
        output_file = repos_path.parent / f"analysis-output-{project_name}.json"
        if output_file.exists():
            print(f"\n📄 Analysis output file created: {output_file}")
            output_data = json.loads(output_file.read_text())
            print("Output content:")
            print(json.dumps(output_data, indent=2))

        return result.returncode == 0

    except subprocess.TimeoutExpired:
        print("❌ Script execution timed out")
        return False
    except Exception as e:
        print(f"❌ Error running script: {e}")
        return False


def test_disk_usage_commands():
    """Test the disk usage commands that will be used in the workflow."""
    with tempfile.TemporaryDirectory() as temp_dir:
        test_path = Path(temp_dir)
        repos_path = create_mock_data(test_path)

        print("\n🔍 Testing disk usage commands:")

        # Test du -sh command
        try:
            result = subprocess.run(
                ["du", "-sh", str(repos_path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                total_size = result.stdout.strip().split("\t")[0]
                print(f"✅ Total size: {total_size}")
            else:
                print("❌ du -sh command failed")
                return False
        except Exception as e:
            print(f"❌ Error running du -sh: {e}")
            return False

        # Test find commands
        try:
            # Count directories
            result = subprocess.run(
                ["find", str(repos_path), "-type", "d"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                dir_count = len(result.stdout.strip().split("\n"))
                print(f"✅ Directory count: {dir_count}")

            # Count files
            result = subprocess.run(
                ["find", str(repos_path), "-type", "f"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                file_count = len(result.stdout.strip().split("\n"))
                print(f"✅ File count: {file_count}")

        except Exception as e:
            print(f"❌ Error running find commands: {e}")
            return False

        return True


def main():
    """Main test function."""
    print("🧪 Testing analyze-repos.py script")
    print("=" * 50)

    # Test disk usage commands first
    if not test_disk_usage_commands():
        print("❌ Disk usage command tests failed")
        sys.exit(1)

    # Create temporary directory for testing
    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"\n📁 Using temporary directory: {temp_dir}")

        # Create mock data
        repos_path = create_mock_data(temp_dir)

        # Test the analysis script
        success = run_analysis_script(repos_path)

        if success:
            print("\n✅ All tests passed!")
        else:
            print("\n❌ Tests failed!")
            sys.exit(1)


if __name__ == "__main__":
    main()
