# GitHub API Configuration Guide

## Overview

The reporting system can query GitHub's API to fetch real-time workflow status for repositories that are mirrored from Gerrit to GitHub. This provides live status indicators (success/failure) for CI/CD workflows in the generated reports.

## Prerequisites

For GitHub API integration to work, you need:

1. **GitHub Personal Access Token** (Classic) - **Required**
2. **GitHub Organization Mapping** (for Gerrit-mirrored repos) - **Optional** (auto-detected if not provided)

## Configuration Methods

There are two ways to configure GitHub API integration:

### Method 1: PROJECTS_JSON Variable (Recommended for explicit mapping)

**Optional:** Add `github_org` to each project entry in the `PROJECTS_JSON` repository variable:

```json
[
  {
    "project": "ONAP",
    "gerrit": "gerrit.onap.org",
    "jenkins": "jenkins.onap.org",
    "github_org": "onap"
  },
  {
    "project": "OpenDaylight",
    "gerrit": "git.opendaylight.org",
    "jenkins": "jenkins.opendaylight.org",
    "github_org": "opendaylight"
  }
]
```

### Method 2: Project Configuration Files

Add to your project's `.config` file:

```yaml
extensions:
  github_api:
    enabled: true
    github_org: "onap"  # GitHub organization name
    timeout: 30.0
```

**Note:** If `github_org` is not specified, the system will automatically attempt to derive it from the Gerrit hostname (see Auto-Detection below).

## Auto-Detection of GitHub Organization

If `github_org` is not explicitly configured, the system will automatically derive it from the Gerrit hostname:

### Auto-Detection Examples

| Gerrit Host | Derived GitHub Org |
|-------------|-------------------|
| `gerrit.onap.org` | `onap` |
| `gerrit.o-ran-sc.org` | `o-ran-sc` |
| `git.opendaylight.org` | `opendaylight` |
| `gerrit.fd.io` | `fd` |
| `gerrit.lfbroadband.org` | `lfbroadband` |

### Auto-Detection Algorithm

1. Extracts the hostname from the repository path (e.g., `gerrit.onap.org`)
2. Removes the `gerrit.` or `git.` prefix
3. Removes the top-level domain (`.org`, `.io`, `.com`, etc.)
4. The remaining part becomes the GitHub organization

### Feedback in Workflow Runs

When auto-detection is used:

**Success:**
```
> ✅ GitHub organization derived successfully: `onap` for repository `aai-babel`
```

**Failure:**
```
> ❌ GitHub API query failed using derived organization `onap` for `aai-babel`
> 
> Error: 404 Not Found
> 
> Possible causes:
> - Repository may not exist on GitHub as `onap/aai-babel`
> - Repository naming may differ between Gerrit and GitHub
> - Add explicit `github_org` mapping to PROJECTS_JSON to override auto-detection
```

### When to Use Explicit Mapping

Use explicit `github_org` configuration when:
- Auto-detection derives the wrong organization name
- Your Gerrit hostname doesn't match your GitHub organization
- Repository naming differs significantly between Gerrit and GitHub

## How It Works

### Repository Path Mapping

When the reporting system analyzes a Gerrit repository, it maps the local path to a GitHub repository:

```
Gerrit Clone Path              → GitHub Repository
./gerrit.onap.org/aai/babel   → onap/aai-babel
./git.opendaylight.org/netconf → opendaylight/netconf
```

The mapping works as follows:

1. **Extract repository components** after the Gerrit host directory
2. **Join multi-level paths** with hyphens
3. **Prepend GitHub organization** from configuration

### Examples

| Gerrit Path | github_org | GitHub Repo |
|------------|------------|-------------|
| `./gerrit.onap.org/aai/babel` | `onap` | `onap/aai-babel` |
| `./gerrit.onap.org/policy/engine` | `onap` | `onap/policy-engine` |
| `./git.opendaylight.org/netconf` | `opendaylight` | `opendaylight/netconf` |

## Setting Up GitHub Token

### 1. Create a Classic Personal Access Token

1. Go to GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Click "Generate new token (classic)"
3. Give it a descriptive name: `project-reports-readonly`
4. Select scopes:
   - ✅ `repo` (or just `public_repo` for public repos)
   - ✅ `workflow` (to read workflow status)
5. Set expiration (recommended: 90 days or no expiration for automation)
6. Generate and copy the token

### 2. Add Token to GitHub Secrets

1. Go to your repository → Settings → Secrets and variables → Actions
2. Click "New repository secret"
3. Name: `CLASSIC_READ_ONLY_PAT_TOKEN`
4. Value: Paste the token
5. Click "Add secret"

## Validation

The reporting workflow will validate GitHub API configuration and display the status in the workflow run summary:

### ✅ Fully Configured (Explicit)

```
### 🔧 GitHub API Integration Status

- **Enabled:** ✅ Yes
- **Token:** ✅ Present (CLASSIC_READ_ONLY_PAT_TOKEN)
- **GitHub Organization:** ✅ `onap`

**Status:** ✅ GitHub API integration fully configured
```

### ⚠️ Auto-Detection Enabled

```
### 🔧 GitHub API Integration Status

- **Enabled:** ✅ Yes
- **Token:** ✅ Present (CLASSIC_READ_ONLY_PAT_TOKEN)
- **GitHub Organization:** ⚠️ **Will attempt auto-detection from Gerrit hostname**

> ℹ️ INFO: GitHub organization not explicitly configured.
> 
> The system will attempt to derive the GitHub organization from the Gerrit hostname.
> 
> Examples of auto-detection:
> - `gerrit.onap.org` → `onap`
> - `gerrit.o-ran-sc.org` → `o-ran-sc`
> - `git.opendaylight.org` → `opendaylight`

**Status:** ⚠️ GitHub API will attempt auto-detection (check logs for results)
```

### ❌ Missing Token

```
### 🔧 GitHub API Integration Status

- **Enabled:** ✅ Yes
- **Token:** ❌ **MISSING** - Set `CLASSIC_READ_ONLY_PAT_TOKEN` secret
- **GitHub Organization:** ✅ `onap`

**Status:** ❌ GitHub API integration DISABLED due to missing token
```

## Troubleshooting

### No GitHub API Statistics in Output

**Symptoms:**
- Workflow runs successfully
- Reports are generated
- No "External API Statistics" section in workflow summary
- No workflow status colors in report.html

**Causes:**
1. Token not set or expired
2. Auto-detected GitHub organization name doesn't match actual GitHub org
3. Repository naming differs between Gerrit and GitHub

**Solution:**
Check the workflow run summary for the "🔧 GitHub API Integration Status" section and address any ❌ items.

### 401 Authentication Failed

**Symptoms:**
```
❌ GitHub API Authentication Failed for `onap/aai-babel`
The GitHub token is invalid or has expired.
```

**Solution:**
1. Verify `CLASSIC_READ_ONLY_PAT_TOKEN` secret is set
2. Check token hasn't expired
3. Regenerate token if needed

### 403 Permission Denied

**Symptoms:**
```
⚠️ GitHub API Permission Denied for `onap/policy-engine`
```

**Solution:**
1. Verify token has `repo` (or `public_repo`) scope
2. Verify token has `workflow` scope
3. Check if repository is private and token has access

### Wrong Repository Name

**Symptoms:**
- 404 errors for some repositories
- GitHub API queries fail for specific repos

**Cause:**
Repository naming mismatch between Gerrit and GitHub.

**Example:**
- Gerrit: `aai/babel`
- GitHub: `onap/aai-babel` ✅
- GitHub: `onap/babel` ❌ (wrong)

**Solution:**
1. Verify the GitHub repository names match the expected pattern (the system joins multi-level Gerrit paths with hyphens)
2. Add explicit `github_org` mapping to PROJECTS_JSON if auto-detection is incorrect
3. Check workflow run summary for auto-detection messages

## Disabling GitHub API Integration

To disable GitHub API queries:

### In PROJECTS_JSON
Simply omit the `github_org` field (auto-detection will be skipped if GitHub API is disabled in config):
```json
{
  "project": "ONAP",
  "gerrit": "gerrit.onap.org",
  "jenkins": "jenkins.onap.org"
}
```

### In Configuration File
```yaml
extensions:
  github_api:
    enabled: false
```

## API Rate Limits

GitHub API has rate limits:
- **Authenticated:** 5,000 requests/hour
- **Unauthenticated:** 60 requests/hour

The reporting system:
- Makes ~2-3 API calls per repository with workflows
- Respects rate limits and retries on 429 errors
- Reports API statistics in workflow summary

For large projects (100+ repositories), this should stay well under limits.

## Security Best Practices

1. **Use Classic PAT with minimal scopes**
   - Only `public_repo` for public repositories
   - Add `repo` only if you need private repos

2. **Rotate tokens regularly**
   - Set 90-day expiration
   - Update secret before expiration

3. **Use repository secrets, not environment secrets**
   - Limits exposure to this repository only

4. **Monitor token usage**
   - Check "External API Statistics" in workflow runs
   - Watch for unusual patterns

## Quick Start Guide

### Option A: Use Auto-Detection (Easiest)

1. **Set up GitHub token:**
   ```bash
   gh secret set CLASSIC_READ_ONLY_PAT_TOKEN
   ```
   
2. **Run workflow** - Auto-detection will derive GitHub org from Gerrit hostname

3. **Check workflow summary** for auto-detection results

### Option B: Explicit Configuration

1. **Update PROJECTS_JSON variable:**
   ```bash
   gh variable set PROJECTS_JSON --body '[
     {
       "project": "ONAP",
       "gerrit": "gerrit.onap.org",
       "jenkins": "jenkins.onap.org",
       "github_org": "onap"
     }
   ]'
   ```

2. **Verify token is set:**
   ```bash
   gh secret list | grep CLASSIC_READ_ONLY_PAT_TOKEN
   ```

3. **Trigger workflow and check summary:**
   - Look for "🔧 GitHub API Integration Status"
   - Verify all items are ✅

4. **Review generated report:**
   - Workflow names should have colored status indicators
   - Green (✅) = success
   - Red (❌) = failure
   - Yellow (⚠️) = other states

5. **If using auto-detection, verify successful derivation:**
   - Check for `✅ GitHub organization derived successfully` messages
   - If you see `❌ GitHub API query failed`, add explicit `github_org` mapping

## Support

For issues or questions:
1. Check workflow run summary for validation errors
2. Review this documentation
3. Open an issue in the project-reports repository