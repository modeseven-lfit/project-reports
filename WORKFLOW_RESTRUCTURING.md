# Workflow Restructuring Documentation

## Overview

This document describes the restructuring of the `reporting.yaml` workflow to eliminate race conditions when publishing reports and triggering GitHub Pages deployments.

## Problem Statement

Previously, each of the 8 matrix jobs in `reporting.yaml` would:

1. Individually push reports to the `gerrit-reports` repository
2. Trigger the `pages.yaml` workflow via push to `main`
3. Result in 8 simultaneous pages deployments that would cancel each other

## Solution

### Part 1: Modify `gerrit-reports/.github/workflows/pages.yaml`

**Status: ✅ COMPLETE**

Changed the trigger from:

```yaml
on:
  push:
    branches:
      - main
  workflow_dispatch:
```

To:

```yaml
on:
  workflow_dispatch:
```

This prevents automatic triggering on every push to `main`.

### Part 2: Restructure `project-reports/.github/workflows/reporting.yaml`

**Status: 🔄 IN PROGRESS**

#### Changes Required

1. **Remove the "Publish report to gerrit-reports repository" step from the `analyze` job**
   - Currently at lines 307-462
   - This step runs once per matrix job and causes race conditions
   - Keep only the artifact upload step

2. **Add a new `publish` job after the `analyze` job**
   - Runs after all matrix jobs complete
   - Downloads all report artifacts
   - Publishes them in a single batch commit
   - Uses the new script: `.github/scripts/publish-reports.sh`

3. **Update the `summary` job**
   - Change dependency from `needs: [verify, analyze]` to `needs: [verify, publish]`
   - Add `actions: write` permission
   - Add step to trigger `pages.yaml` via workflow_dispatch

## New Job Definitions

### New `publish` Job

Insert this job between the `analyze` and `summary` jobs:

```yaml
  publish:
    name: "Publish Reports to gerrit-reports"
    needs: [verify, analyze]
    if: always()
    runs-on: ubuntu-latest
    permissions:
      contents: read
    timeout-minutes: 15
    steps:
      # Harden the runner used by this workflow
      # yamllint disable-line rule:line-length
      - uses: step-security/harden-runner@ec9f2d5744a09debf3a187a3f4f675c53b671911 # v2.13.0
        with:
          egress-policy: "audit"

      - name: "Checkout repository"
        # yamllint disable-line rule:line-length
        uses: actions/checkout@08c6903cd8c0fde910a37f88322edcfb5dd907a8 # v5.0.0

      - name: "Download all report artifacts"
        # yamllint disable-line rule:line-length
        uses: actions/download-artifact@018cc2cf5baa6db3ef3c5f8a56943fffe632ef53 # v6.0.0
        with:
          pattern: reports-*
          path: ./downloaded-reports
        continue-on-error: true

      - name: "Publish all reports in batch"
        env:
          REPORTS_TOKEN: ${{ secrets.GERRIT_REPORTS_PAT_TOKEN }}
          REPORTS_REPO: modeseven-lfit/gerrit-reports
          ARTIFACTS_DIR: ./downloaded-reports
        shell: bash
        run: |
          bash .github/scripts/publish-reports.sh
```

### Updated `summary` Job

Modify the existing `summary` job to:

1. Change the `needs` line from:

```yaml
    needs: [verify, analyze]
```

To:

```yaml
    needs: [verify, publish]
```

2. Add `actions: write` permission:

```yaml
    permissions:
      contents: read
      actions: write
```

3. Add a new step at the end to trigger GitHub Pages:

```yaml
      - name: "Trigger GitHub Pages update"
        if: success()
        env:
          REPORTS_TOKEN: ${{ secrets.GERRIT_REPORTS_PAT_TOKEN }}
          REPORTS_REPO: modeseven-lfit/gerrit-reports
        shell: bash
        run: |
          if [ -z "${REPORTS_TOKEN}" ]; then
            echo "::warning::GERRIT_REPORTS_PAT_TOKEN secret not set; cannot trigger pages workflow."
            echo "Pages will need to be updated manually."
            exit 0
          fi

          echo "Triggering GitHub Pages workflow in ${REPORTS_REPO}..."

          response=$(curl -sS -X POST \
            -H "Authorization: Bearer ${REPORTS_TOKEN}" \
            -H "Accept: application/vnd.github+json" \
            -H "X-GitHub-Api-Version: 2022-11-28" \
            "https://api.github.com/repos/${REPORTS_REPO}/actions/workflows/pages.yaml/dispatches" \
            -d '{"ref":"main"}')

          if [ $? -eq 0 ]; then
            echo "✅ Successfully triggered GitHub Pages workflow"
            echo "" >> "$GITHUB_STEP_SUMMARY"
            echo "### 🌐 GitHub Pages Update" >> "$GITHUB_STEP_SUMMARY"
            echo "GitHub Pages workflow has been triggered to update the reports site." >> "$GITHUB_STEP_SUMMARY"
            echo "View the deployment at: https://github.com/${REPORTS_REPO}/actions/workflows/pages.yaml" >> "$GITHUB_STEP_SUMMARY"
          else
            echo "::error::Failed to trigger GitHub Pages workflow"
            echo "::error::Ensure GERRIT_REPORTS_PAT_TOKEN has 'actions: write' permission for ${REPORTS_REPO}"
            exit 1
          fi
```

## New Workflow Flow

After changes, the workflow will operate as follows:

1. **verify** job: Validates configuration ✅
2. **analyze** job (matrix): 8 parallel jobs generate reports and upload as artifacts ✅
3. **publish** job: Runs once after all matrix jobs, downloads all artifacts, publishes in single commit 🆕
4. **summary** job: Generates summary and triggers GitHub Pages workflow once 🆕

## Benefits

1. **Eliminates race conditions**: Only one commit to `gerrit-reports` per workflow run
2. **Eliminates redundant Pages deployments**: Pages workflow triggered once after all reports published
3. **Resilient to partial failures**: If some matrix jobs fail, successful reports are still published
4. **Cleaner git history**: One consolidated commit instead of 8 separate commits
5. **Faster overall execution**: No retry/rebase logic needed across multiple jobs

## Token Requirements

The `GERRIT_REPORTS_PAT_TOKEN` secret must have the following permissions for the `modeseven-lfit/gerrit-reports` repository:

- ✅ **Contents**: Read and write (already required, unchanged)
- 🆕 **Actions**: Write (newly required for workflow_dispatch trigger)

## Manual Steps to Complete

1. Edit `.github/workflows/reporting.yaml`
2. Delete lines 307-462 (the entire "Publish report to gerrit-reports repository" step from the `analyze` job)
3. Insert the new `publish` job definition (shown above) after the `analyze` job
4. Update the `summary` job as described above
5. Commit and push changes
6. Verify the PAT token has `actions: write` permission

## Testing

After implementation, trigger the workflow and verify:

1. ✅ All matrix jobs complete and upload artifacts
2. ✅ Publish job downloads artifacts and creates single commit
3. ✅ Summary job triggers pages workflow
4. ✅ Pages workflow runs once and completes successfully
5. ✅ Reports are accessible via GitHub Pages
