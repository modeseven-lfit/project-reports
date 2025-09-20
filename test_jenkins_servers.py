#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 The Linux Foundation

"""
Jenkins Server Connectivity Test Script

Tests each Jenkins server from the project JSON to verify API access
and debug any connectivity or URL construction issues.
"""

import json
import logging
import sys
from typing import Any, Dict, List

try:
    import httpx
except ImportError:
    print("ERROR: httpx is required. Install with: pip install httpx")
    sys.exit(1)

# Test data from the GitHub workflow JSON
JENKINS_SERVERS = [
    {"project": "O-RAN-SC", "jenkins": "jenkins.o-ran-sc.org"},
    {"project": "ONAP", "jenkins": "jenkins.onap.org"},
    {"project": "Opendaylight", "jenkins": "jenkins.opendaylight.org"},
    {"project": "AGL", "jenkins": "build.automotivelinux.org"},
    {"project": "FDio", "jenkins": "jenkins.fd.io"},
    {"project": "LF Broadband", "jenkins": "jenkins.lfbroadband.org"},
]

def test_jenkins_server(project: str, host: str, timeout: float = 30.0) -> Dict[str, Any]:
    """Test a single Jenkins server and return detailed results."""
    result = {
        "project": project,
        "host": host,
        "success": False,
        "status_code": None,
        "job_count": 0,
        "error": None,
        "response_time": None,
        "first_few_jobs": [],
        "api_url": None
    }

    # Try different URL patterns that Jenkins servers might use
    url_patterns = [
        f"https://{host}/api/json?tree=jobs[name,url,color]",
        f"http://{host}/api/json?tree=jobs[name,url,color]",
        f"https://{host}/jenkins/api/json?tree=jobs[name,url,color]",
        f"http://{host}/jenkins/api/json?tree=jobs[name,url,color]",
    ]

    for url in url_patterns:
        print(f"  Trying URL: {url}")
        result["api_url"] = url

        try:
            with httpx.Client(timeout=timeout) as client:
                import time
                start_time = time.time()
                response = client.get(url)
                end_time = time.time()

                result["response_time"] = round(end_time - start_time, 2)
                result["status_code"] = response.status_code

                print(f"    Status: {response.status_code} ({result['response_time']}s)")

                if response.status_code == 200:
                    try:
                        data = response.json()
                        if "jobs" in data and isinstance(data["jobs"], list):
                            result["success"] = True
                            result["job_count"] = len(data["jobs"])
                            # Get first few job names for debugging
                            result["first_few_jobs"] = [
                                job.get("name", "unnamed")
                                for job in data["jobs"][:5]
                            ]
                            print(f"    ✅ SUCCESS: Found {result['job_count']} jobs")
                            print(f"    Sample jobs: {result['first_few_jobs']}")
                            return result
                        else:
                            print(f"    ❌ Invalid JSON structure (no 'jobs' array)")
                            result["error"] = "Invalid JSON structure"
                    except json.JSONDecodeError as e:
                        print(f"    ❌ Invalid JSON response: {e}")
                        result["error"] = f"JSON decode error: {e}"
                elif response.status_code == 404:
                    print(f"    ❌ Not found (404)")
                elif response.status_code == 403:
                    print(f"    ❌ Access forbidden (403)")
                elif response.status_code >= 500:
                    print(f"    ❌ Server error ({response.status_code})")
                else:
                    print(f"    ❌ HTTP {response.status_code}")
                    if len(response.text) < 200:
                        print(f"    Response: {response.text}")

        except httpx.TimeoutException:
            print(f"    ❌ Timeout after {timeout}s")
            result["error"] = "Timeout"
        except httpx.ConnectError as e:
            print(f"    ❌ Connection failed: {e}")
            result["error"] = f"Connection error: {e}"
        except Exception as e:
            print(f"    ❌ Unexpected error: {e}")
            result["error"] = f"Unexpected error: {e}"

    # If we get here, none of the URL patterns worked
    if not result["success"]:
        result["error"] = result["error"] or "All URL patterns failed"

    return result


def test_job_matching(host: str, sample_gerrit_projects: List[str]) -> None:
    """Test job name matching logic for sample Gerrit project names."""
    print(f"\n🔍 Testing job matching logic for {host}")

    # First get all jobs
    try:
        url = f"https://{host}/api/json?tree=jobs[name,url,color]"
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url)
            if response.status_code == 200:
                data = response.json()
                all_jobs = [job.get("name", "") for job in data.get("jobs", [])]

                print(f"  Total jobs on server: {len(all_jobs)}")

                # Test matching for sample projects
                for gerrit_project in sample_gerrit_projects:
                    project_job_name = gerrit_project.replace("/", "-")
                    matches = [job for job in all_jobs if project_job_name in job]

                    print(f"  Gerrit project '{gerrit_project}' -> search pattern '{project_job_name}'")
                    if matches:
                        print(f"    ✅ Found {len(matches)} matching jobs: {matches[:3]}{'...' if len(matches) > 3 else ''}")
                    else:
                        print(f"    ❌ No matching jobs found")
                        # Show similar job names for debugging
                        similar = [job for job in all_jobs if any(word in job.lower() for word in gerrit_project.lower().split('/'))]
                        if similar:
                            print(f"    💡 Similar jobs: {similar[:3]}")
            else:
                print(f"  ❌ Failed to fetch jobs: HTTP {response.status_code}")
    except Exception as e:
        print(f"  ❌ Error testing job matching: {e}")


def main():
    """Main test function."""
    print("🧪 Jenkins Server Connectivity Test")
    print("=" * 50)

    results = []

    for server in JENKINS_SERVERS:
        project = server["project"]
        host = server["jenkins"]

        print(f"\n📡 Testing {project} ({host})")
        print("-" * 30)

        result = test_jenkins_server(project, host)
        results.append(result)

        if result["success"]:
            # Test job matching for successful servers
            sample_projects = [
                "example/project",
                "test-project",
                "integration/test",
                project.lower().replace(" ", "-")  # Use project name as sample
            ]
            test_job_matching(host, sample_projects)

    # Summary
    print("\n" + "=" * 50)
    print("📊 SUMMARY")
    print("=" * 50)

    successful = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]

    print(f"✅ Successful: {len(successful)}/{len(results)} servers")
    print(f"❌ Failed: {len(failed)}/{len(results)} servers")

    if successful:
        print("\n🎉 Working servers:")
        for result in successful:
            print(f"  • {result['project']}: {result['host']} ({result['job_count']} jobs)")

    if failed:
        print("\n💥 Failed servers:")
        for result in failed:
            print(f"  • {result['project']}: {result['host']} - {result['error']}")

    # Detailed JSON output for debugging
    print(f"\n📄 Detailed results (JSON):")
    print(json.dumps(results, indent=2))

    return 0 if len(failed) == 0 else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
