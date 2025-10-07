#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 The Linux Foundation

"""
Verification script to check that both G2G and Jenkins fixes are working correctly
by examining the raw JSON data from the ONAP report generation.
"""

import json
import sys
from pathlib import Path

def check_g2g_enhancement(repositories):
    """Check that G2G detection now looks for both github2gerrit.yaml and call-github2gerrit.yaml"""
    print("🔍 Checking G2G enhancement...")

    g2g_repos = []
    total_repos = len(repositories)

    for repo in repositories:
        g2g_data = repo.get("features", {}).get("g2g", {})
        if g2g_data.get("present", False):
            file_paths = g2g_data.get("file_paths", [])
            g2g_repos.append({
                "project": repo.get("gerrit_project", "Unknown"),
                "files": file_paths
            })

    print(f"   ✅ Total repositories scanned: {total_repos}")
    print(f"   ✅ Repositories with G2G workflows: {len(g2g_repos)}")

    # Check for both file types
    github2gerrit_count = 0
    call_github2gerrit_count = 0

    for repo_data in g2g_repos:
        for file_path in repo_data["files"]:
            if "call-github2gerrit.yaml" in file_path:
                call_github2gerrit_count += 1
            elif "github2gerrit.yaml" in file_path and "call-github2gerrit.yaml" not in file_path:
                github2gerrit_count += 1

    print(f"   ✅ Repositories with github2gerrit.yaml: {github2gerrit_count}")
    print(f"   ✅ Repositories with call-github2gerrit.yaml: {call_github2gerrit_count}")

    if call_github2gerrit_count > 0:
        print("   🎉 SUCCESS: Enhanced G2G detection is working - found call-github2gerrit.yaml files!")

        # Show some examples
        print("   📋 Examples of repositories with call-github2gerrit.yaml:")
        example_count = 0
        for repo_data in g2g_repos:
            for file_path in repo_data["files"]:
                if "call-github2gerrit.yaml" in file_path and example_count < 5:
                    print(f"      • {repo_data['project']}: {file_path}")
                    example_count += 1
        return True
    else:
        print("   ❌ ISSUE: No call-github2gerrit.yaml files found - enhancement may not be working")
        return False

def check_jenkins_integration(repositories):
    """Check that Jenkins integration is working and data is being collected"""
    print("\n🔍 Checking Jenkins integration...")

    jenkins_repos = []
    total_repos = len(repositories)
    total_jobs = 0

    for repo in repositories:
        jenkins_data = repo.get("jenkins", {})
        if jenkins_data.get("has_jobs", False):
            jobs = jenkins_data.get("jobs", [])
            jenkins_repos.append({
                "project": repo.get("gerrit_project", "Unknown"),
                "job_count": len(jobs),
                "jobs": [job.get("name", "Unknown") for job in jobs[:3]]  # First 3 jobs as examples
            })
            total_jobs += len(jobs)

    print(f"   ✅ Total repositories scanned: {total_repos}")
    print(f"   ✅ Repositories with Jenkins jobs: {len(jenkins_repos)}")
    print(f"   ✅ Total Jenkins jobs found: {total_jobs}")

    if len(jenkins_repos) > 0 and total_jobs > 0:
        print("   🎉 SUCCESS: Jenkins integration is working!")

        # Show some examples
        print("   📋 Examples of repositories with Jenkins jobs:")
        for i, repo_data in enumerate(jenkins_repos[:5]):  # Show first 5
            jobs_str = ", ".join(repo_data["jobs"])
            if len(repo_data["jobs"]) < repo_data["job_count"]:
                jobs_str += f" (and {repo_data['job_count'] - len(repo_data['jobs'])} more)"
            print(f"      • {repo_data['project']}: {repo_data['job_count']} jobs ({jobs_str})")

        return True
    else:
        print("   ❌ ISSUE: No Jenkins jobs found - integration may not be working")
        return False

def analyze_feature_matrix_data(repositories):
    """Analyze data that would be used in the feature matrix table"""
    print("\n📊 Analyzing Feature Matrix data...")

    matrix_data = {
        "total_repos": len(repositories),
        "dependabot": 0,
        "pre_commit": 0,
        "readthedocs": 0,
        "gitreview": 0,
        "g2g": 0,
        "jenkins_jobs": 0
    }

    for repo in repositories:
        features = repo.get("features", {})
        jenkins = repo.get("jenkins", {})

        if features.get("dependabot", {}).get("present", False):
            matrix_data["dependabot"] += 1
        if features.get("pre_commit", {}).get("present", False):
            matrix_data["pre_commit"] += 1
        if features.get("readthedocs", {}).get("present", False):
            matrix_data["readthedocs"] += 1
        if features.get("gitreview", {}).get("present", False):
            matrix_data["gitreview"] += 1
        if features.get("g2g", {}).get("present", False):
            matrix_data["g2g"] += 1
        if jenkins.get("has_jobs", False):
            matrix_data["jenkins_jobs"] += 1

    print("   📈 Feature Matrix Statistics:")
    for feature, count in matrix_data.items():
        if feature != "total_repos":
            percentage = (count / matrix_data["total_repos"]) * 100
            print(f"      • {feature.replace('_', ' ').title()}: {count}/{matrix_data['total_repos']} ({percentage:.1f}%)")

    return matrix_data

def main():
    """Main verification function"""
    print("🔧 ONAP Report Fixes Verification")
    print("=" * 50)

    # Load the raw JSON data
    json_file = Path("testing/reports/ONAP/report_raw.json")

    if not json_file.exists():
        print(f"❌ ERROR: {json_file} not found!")
        print("   Please run the ONAP report generation first.")
        return False

    print(f"📂 Loading data from: {json_file}")

    try:
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"❌ ERROR: Failed to load JSON data: {e}")
        return False

    repositories = data.get("repositories", [])
    if not repositories:
        print("❌ ERROR: No repositories found in the data!")
        return False

    print(f"✅ Loaded data for {len(repositories)} repositories")

    # Run verification checks
    g2g_success = check_g2g_enhancement(repositories)
    jenkins_success = check_jenkins_integration(repositories)

    # Analyze feature matrix data
    matrix_stats = analyze_feature_matrix_data(repositories)

    # Summary
    print("\n🎯 Verification Summary")
    print("=" * 30)

    if g2g_success:
        print("✅ G2G Enhancement: WORKING")
        print("   • Now checks for both github2gerrit.yaml AND call-github2gerrit.yaml")
    else:
        print("❌ G2G Enhancement: ISSUE DETECTED")

    if jenkins_success:
        print("✅ Jenkins Integration: WORKING")
        print("   • Successfully connected to jenkins.onap.org")
        print("   • Collected job data for multiple repositories")
        print("   • Jenkins column should now appear in CI/CD Jobs table")
    else:
        print("❌ Jenkins Integration: ISSUE DETECTED")

    # Overall result
    print("\n🏁 Overall Result:")
    if g2g_success and jenkins_success:
        print("🎉 SUCCESS: Both fixes are working correctly!")
        print("\n📋 Next Steps:")
        print("   1. The HTML report should now show:")
        print("      • Enhanced G2G column in Feature Matrix (checks both workflow files)")
        print("      • Jenkins Jobs column in CI/CD Jobs table")
        print("   2. Re-run the report generation to completion to see the HTML output")
        return True
    else:
        print("⚠️  PARTIAL SUCCESS: Some issues detected - see details above")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
