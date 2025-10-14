<!--
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 The Linux Foundation
-->

# GitHub API Error Logging and Reporting

## Overview

This document describes the error logging mechanisms for GitHub API requests, particularly focusing on authentication and permission failures that can cause workflow color coding issues.

## Current Error Logging Behavior

### Log Levels and Messages

The GitHub API client (`GitHubAPIClient` class) logs errors at different levels:

#### 1. **Authentication Failures (401 Unauthorized)**

**Location**: `get_repository_workflows()` and `get_workflow_runs_status()`

**Log Level**: `ERROR`

**Log Message**:
```
GitHub API authentication failed (401) for {owner}/{repo}: {response.text}
```

**GitHub Step Summary**: ✅ **NOW WRITTEN**
```markdown
❌ **GitHub API Authentication Failed** for `{owner}/{repo}`

The GitHub token is invalid or has expired.

**Action Required:** Update the `GITHUB_TOKEN` secret with a valid Personal Access Token.
```

**Return Value**: Empty list `[]` or `{"status": "auth_error"}`

---

#### 2. **Permission Denied (403 Forbidden)**

**Location**: `get_repository_workflows()` and `get_workflow_runs_status()`

**Log Level**: `ERROR`

**Log Message**:
```
GitHub API permission denied (403) for {owner}/{repo}: {response.text}
```

**GitHub Step Summary**: ✅ **NOW WRITTEN**
```markdown
⚠️ **GitHub API Permission Denied** for `{owner}/{repo}`

Error: {error_message_from_github}

**Likely Cause:** The GitHub token lacks required permissions.

**Required Scopes:**
- `repo` (or at least `repo:status`)
- `actions:read`

**To Fix:** Update your Personal Access Token with these scopes.
```

**Return Value**: Empty list `[]` or `{"status": "permission_error"}`

---

#### 3. **Repository Not Found (404)**

**Location**: `get_repository_workflows()`

**Log Level**: `DEBUG`

**Log Message**:
```
Repository {owner}/{repo} not found or no access
```

**Return Value**: Empty list `[]`

---

#### 4. **Other HTTP Errors (Non-200 status codes)**

**Location**: `get_repository_workflows()` and `get_workflow_runs_status()`

**Log Level**: `WARNING`

**Log Message**:
```
GitHub API returned {status_code} for workflows in {owner}/{repo}: {response.text}
GitHub API returned {status_code} for workflow {workflow_id} runs: {response.text}
```

**Return Value**: Empty list `[]` or `{"status": "api_error"}`

---

#### 5. **Network/Exception Errors**

**Location**: All API methods

**Log Level**: `ERROR`

**Log Message**:
```
Error fetching workflows for {owner}/{repo}: {exception}
Error fetching workflow runs for {owner}/{repo}/workflows/{workflow_id}: {exception}
```

**Return Value**: Empty list `[]` or `{"status": "error"}`

---

## Workflow Integration Error Handling

### In `FeatureRegistry._check_workflows()`

**Log Level**: `WARNING`

**Log Message**:
```
Failed to fetch GitHub workflow status for {repo_path}: {exception}
```

**Behavior**: Continues execution without GitHub API data; workflows will show as grey/unknown status.

---

## What Happens When API Calls Fail?

### Symptom: Grey/Unknown Workflow Status

When GitHub API calls fail due to authentication or permission issues:

1. **API client logs the error** (see levels above)
2. **Returns empty data** (empty lists or status dictionaries with error flags)
3. **Workflow check continues** with local file scanning only
4. **Result**: Workflows appear with **grey circles** (unknown status) instead of colored status indicators
5. **Step Summary now shows** prominent warnings about the authentication/permission issue

### How to Diagnose

#### Check GitHub Actions Logs

When running with `--verbose` flag (which sets `DEBUG` level):

```bash
python3 generate_reports.py \
  --project "ONAP" \
  --repos-path "./gerrit.onap.org" \
  --config-dir "./configuration" \
  --output-dir "./reports" \
  --verbose
```

Look for:
- `ERROR` messages about authentication (401) or permission (403)
- `WARNING` messages about API failures
- `DEBUG` messages about workflow processing

#### Check GitHub Step Summary

**New Feature**: After the enhancement, authentication and permission errors are now written to the GitHub Step Summary with:
- Clear error messages
- Specific error details from GitHub's API response
- Actionable instructions for fixing the issue
- Required token scopes listed

#### Check Report Output

If workflows show as grey/unknown in the HTML report but should have status:
- The GitHub API integration likely failed
- Check the logs for the specific repository
- Verify the `GITHUB_TOKEN` has correct permissions

---

## Required Token Permissions

For the GitHub API integration to work correctly, the token must have:

### Minimum Required Scopes

1. **`repo`** (or at least `repo:status`)
   - Allows reading repository metadata
   - Needed to verify repository exists

2. **`actions:read`**
   - Allows reading workflow definitions
   - Allows reading workflow run statuses
   - **This is the most commonly missing scope**

### How to Check Token Scopes

Using GitHub CLI:
```bash
gh api user -i | grep x-oauth-scopes
```

Using curl:
```bash
curl -H "Authorization: token YOUR_TOKEN" \
  -I https://api.github.com/user \
  | grep x-oauth-scopes
```

### How to Update Token Scopes

1. Go to GitHub Settings → Developer settings → Personal access tokens
2. Find your token (or create a new one)
3. Ensure these scopes are checked:
   - `repo` (full control of private repositories)
   - `read:org` (recommended but not required)
   - Under "Workflows": `actions:read`
4. Update token and save
5. Update the secret in GitHub Actions or your environment

---

## Environment Variables

### `GITHUB_TOKEN`

The GitHub API client checks for this environment variable:

```python
github_token = config.get("extensions", {}).get("github_api", {}).get("token") or os.environ.get("GITHUB_TOKEN")
```

**In GitHub Actions**, set this in the workflow YAML:

```yaml
env:
  GITHUB_TOKEN: ${{ secrets.GERRIT_REPORTS_PAT_TOKEN }}
```

**⚠️ Important**: The default `${{ secrets.GITHUB_TOKEN }}` provided by GitHub Actions **does NOT have `actions:read` scope by default**. You must use a Personal Access Token with proper scopes.

### `GITHUB_STEP_SUMMARY`

**New Feature**: The API client now writes to this file when permission errors occur:

```python
step_summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
if step_summary_file:
    with open(step_summary_file, "a") as f:
        f.write(error_message + "\n")
```

This is automatically set by GitHub Actions and displays in the workflow run summary.

---

## Troubleshooting Guide

### Problem: All workflows show as grey in reports

**Likely Cause**: GitHub API authentication/permission failure

**Steps to Diagnose**:

1. **Check if token exists**:
   ```bash
   echo $GITHUB_TOKEN
   ```

2. **Check GitHub Step Summary** in the workflow run for permission error warnings

3. **Check workflow logs** for ERROR messages about 401/403

4. **Verify token scopes** (see "Required Token Permissions" above)

5. **Test API access manually**:
   ```bash
   curl -H "Authorization: token $GITHUB_TOKEN" \
     https://api.github.com/repos/onap/aai-aai-common/actions/workflows
   ```

### Problem: Token works locally but not in CI

**Likely Cause**: Different tokens being used

**Solution**:
1. Verify `GERRIT_REPORTS_PAT_TOKEN` secret is set in GitHub repository settings
2. Verify the secret contains a token with `actions:read` scope
3. Check workflow YAML uses correct secret name

### Problem: Only some repositories fail

**Likely Cause**: Rate limiting or specific repository permissions

**Check**:
1. GitHub API rate limits: `curl -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/rate_limit`
2. Repository-specific access issues (private repos, org restrictions)

---

## Summary of Enhancements

### What Was Added

1. **Specific 401 and 403 error detection** with detailed logging
2. **GitHub Step Summary integration** for visible error reporting
3. **Response body logging** to capture GitHub's error messages
4. **Actionable error messages** with fix instructions
5. **Required scopes documentation** in error messages

### What This Solves

- **Visibility**: Errors are now immediately visible in GitHub Actions Step Summary
- **Actionability**: Clear instructions on what scopes are needed
- **Debugging**: Full error details logged for troubleshooting
- **User Experience**: No more silent failures with grey workflows

### Backward Compatibility

All changes are backward compatible:
- Existing log messages remain unchanged
- Step Summary writes are optional (only if `GITHUB_STEP_SUMMARY` is set)
- Returns same data structures as before
- No breaking changes to API or configuration
