#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 The Linux Foundation

"""
Repository Analysis Script

This is a placeholder script that performs basic analytics on Gerrit repositories.
It will be expanded later to include more comprehensive analysis features.
"""

import argparse
import json
import os
import sys
from pathlib import Path


def write_to_summary(content):
    """Write content to GitHub Step Summary if available."""
    if "GITHUB_STEP_SUMMARY" in os.environ:
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(content)


def validate_repos_path(repos_path):
    """Validate that the repository path exists."""
    if not repos_path.exists():
        error_msg = f"❌ Repository path does not exist: {repos_path}"
        print(error_msg, file=sys.stderr)
        write_to_summary("- **Status:** ❌ Failed - Repository path not found\n\n")
        sys.exit(1)


def process_manifest(manifest_path):
    """Process the clone manifest file if it exists."""
    if not manifest_path.exists():
        print("ℹ️  No clone manifest found")
        write_to_summary("- **Manifest Status:** ℹ️  No manifest file found\n")
        return

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest_data = json.load(f)

        total_repos = manifest_data.get("total", 0)
        successful_clones = manifest_data.get("succeeded", 0)
        failed_clones = manifest_data.get("failed", 0)

        print("📋 Clone Summary:")
        print(f"   - Total repositories: {total_repos}")
        print(f"   - Successfully cloned: {successful_clones}")
        print(f"   - Failed clones: {failed_clones}")

        write_to_summary(
            f"- **Total Repositories:** {total_repos}\n"
            f"- **Successfully Cloned:** {successful_clones}\n"
            f"- **Failed Clones:** {failed_clones}\n"
        )
    except (json.JSONDecodeError, IOError) as e:
        print(f"⚠️  Warning: Could not read clone manifest: {e}")
        write_to_summary("- **Manifest Status:** ⚠️  Could not read manifest file\n")


def count_repositories(repos_path):
    """Count and display repository directories."""
    try:
        repo_dirs = [d for d in repos_path.iterdir() if d.is_dir() and d.name != ".git"]
        actual_repo_count = len(repo_dirs)

        print(f"📂 Found {actual_repo_count} repository directories")
        write_to_summary(f"- **Repository Directories Found:** {actual_repo_count}\n")

        # List first few repositories as examples
        if repo_dirs:
            print("📝 Sample repositories:")
            sample_repos = repo_dirs[:5]  # Show first 5 repos
            for repo_dir in sample_repos:
                print(f"   - {repo_dir.name}")

            if len(repo_dirs) > 5:
                remaining = len(repo_dirs) - 5
                print(f"   ... and {remaining} more")

            write_to_summary("\n### Sample Repositories:\n")
            for repo_dir in sample_repos:
                write_to_summary(f"- `{repo_dir.name}`\n")
            if len(repo_dirs) > 5:
                remaining = len(repo_dirs) - 5
                write_to_summary(f"- *... and {remaining} more repositories*\n")

        return actual_repo_count
    except OSError as e:
        print(f"❌ Error accessing repository directory: {e}", file=sys.stderr)
        write_to_summary("- **Status:** ❌ Error accessing repository directory\n\n")
        sys.exit(1)


def save_analysis_output(args, repo_count):
    """Save analysis results to output file."""
    analysis_output = {
        "project": args.project,
        "server": args.server,
        "analysis_timestamp": "placeholder_timestamp",
        "repository_count": repo_count,
        "status": "completed",
        "notes": "This is a placeholder analysis script",
    }

    output_file = f"analysis-output-{args.project}.json"
    try:
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(analysis_output, f, indent=2)
        print(f"📄 Analysis output saved to: {output_file}")

        write_to_summary(
            f"- **Output File:** `{output_file}`\n"
            "- **Status:** ✅ Analysis completed successfully\n\n"
        )
    except IOError as e:
        print(f"❌ Error writing analysis output: {e}", file=sys.stderr)
        write_to_summary("- **Status:** ❌ Failed to write analysis output\n\n")
        sys.exit(1)


def main():
    """Main entry point for the repository analysis script."""
    parser = argparse.ArgumentParser(
        description="Analyze Gerrit repositories for a given project"
    )
    parser.add_argument(
        "--project",
        required=True,
        help="Project name (e.g., O-RAN-SC, ONAP, Opendaylight)",
    )
    parser.add_argument(
        "--server",
        required=True,
        help="Gerrit server hostname (e.g., gerrit.o-ran-sc.org)",
    )
    parser.add_argument(
        "--repos-path", required=True, help="Path to the cloned repositories directory"
    )

    args = parser.parse_args()

    # Print basic information
    print(f"🔍 Analyzing repositories for project: {args.project}")
    print(f"📡 Gerrit server: {args.server}")
    print(f"📁 Repository path: {args.repos_path}")

    # Write to GitHub Step Summary
    write_to_summary(
        f"## 📊 Analysis Results for {args.project}\n\n"
        f"- **Project:** {args.project}\n"
        f"- **Gerrit Server:** {args.server}\n"
        f"- **Repository Path:** {args.repos_path}\n"
    )

    # Validate repository path
    repos_path = Path(args.repos_path)
    validate_repos_path(repos_path)

    # Process clone manifest
    manifest_path = repos_path / "clone-manifest.json"
    process_manifest(manifest_path)

    # Count repositories
    repo_count = count_repositories(repos_path)

    # Save analysis output
    save_analysis_output(args, repo_count)

    print("✅ Analysis completed successfully!")


if __name__ == "__main__":
    main()
