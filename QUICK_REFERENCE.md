# Quick Reference: GitHub API Configuration

## TL;DR

### Minimal Setup
```bash
# 1. Set required secret
gh secret set CLASSIC_READ_ONLY_PAT_TOKEN

# 2. Run workflow - auto-detection will handle GitHub org mapping
```

### With Explicit GitHub Org Mapping
```bash
# Update PROJECTS_JSON with github field
gh variable set PROJECTS_JSON --body '[
  {
    "project": "ONAP",
    "gerrit": "gerrit.onap.org",
    "jenkins": "jenkins.onap.org",
    "github": "onap"
  }
]'
```

## Required Secrets

| Secret | Required? | Purpose |
|--------|-----------|---------|
| `CLASSIC_READ_ONLY_PAT_TOKEN` | ✅ Yes | GitHub API access for workflow status |
| `GERRIT_REPORTS_PAT_TOKEN` | ✅ Yes | Publishing reports to gerrit-reports repo |
| `LF_GERRIT_INFO_MASTER_SSH_KEY` | ⚠️ Optional | SSH access to info-master (HTTPS fallback available) |

## Auto-Detection

GitHub organization is automatically derived from Gerrit hostname:

| Gerrit Host | Auto-Detected Org | Correct? |
|-------------|-------------------|----------|
| `gerrit.onap.org` | `onap` | ✅ Yes |
| `gerrit.o-ran-sc.org` | `o-ran-sc` | ✅ Yes |
| `git.opendaylight.org` | `opendaylight` | ✅ Yes |
| `gerrit.fd.io` | `fd` | ❌ No (should be `fdio`) |
| `gerrit.automotivelinux.org` | `automotivelinux` | ❌ No (should be `automotive-grade-linux`) |

## Override Auto-Detection

For projects where auto-detection is wrong, add explicit `github` field:

```json
{
  "project": "FDio",
  "gerrit": "gerrit.fd.io",
  "github": "fdio"  // Overrides auto-detected "fd"
}
```

## Validation Checklist

When you run the workflow, check GITHUB_STEP_SUMMARY for:

### ✅ All Good
```
🔐 Secrets Validation
- ✅ CLASSIC_READ_ONLY_PAT_TOKEN: Present
- ✅ GERRIT_REPORTS_PAT_TOKEN: Present
- ✅ LF_GERRIT_INFO_MASTER_SSH_KEY: Present

🔧 GitHub API Integration Status
- Enabled: ✅ Yes
- Token: ✅ Present
- GitHub Organization: ✅ onap
Status: ✅ GitHub API integration fully configured
```

### ⚠️ Using Auto-Detection
```
🔧 GitHub API Integration Status
- Enabled: ✅ Yes
- Token: ✅ Present
- GitHub Organization: ⚠️ Will attempt auto-detection
Status: ⚠️ GitHub API will attempt auto-detection (check logs)
```

### ❌ Missing Prerequisites
```
🔐 Secrets Validation
- ❌ CLASSIC_READ_ONLY_PAT_TOKEN: MISSING
```

## Runtime Messages

### Success
```
> ✅ GitHub organization derived successfully: `onap` for repository `aai-babel`
```

### Failure
```
> ❌ GitHub API query failed using derived organization `onap` for `aai-babel`
> Error: 404 Not Found
> 
> Add explicit `github` mapping to PROJECTS_JSON to override auto-detection
```

## Troubleshooting

### No GitHub API Statistics in Summary?
1. Check secrets validation - is CLASSIC_READ_ONLY_PAT_TOKEN set?
2. Check for auto-detection failure messages
3. Add explicit `github` field to PROJECTS_JSON

### 404 Errors?
- GitHub org name may not match
- Repository may not exist on GitHub
- Add explicit `github` mapping

### No Workflow Status Colors in Report?
- Check GITHUB_STEP_SUMMARY for GitHub API status
- Verify token has `repo` and `workflow` scopes
- Check for error messages in workflow logs

## Common Patterns

### Projects with Standard Naming
```json
{
  "project": "ONAP",
  "gerrit": "gerrit.onap.org"
  // No github field needed - auto-detects to "onap"
}
```

### Projects with Non-Standard Naming
```json
{
  "project": "FDio",
  "gerrit": "gerrit.fd.io",
  "github": "fdio"  // Explicit mapping required
}
```

### Projects Without GitHub Presence
```json
{
  "project": "LF Broadband",
  "gerrit": "gerrit.lfbroadband.org"
  // No github field - will attempt auto-detection and report failure
}
```

## Creating GitHub PAT

1. GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Generate new token (classic)
3. Name: `project-reports-readonly`
4. Scopes:
   - ✅ `repo` (or `public_repo` for public repos only)
   - ✅ `workflow`
5. Expiration: 90 days or No expiration
6. Generate token
7. Add to repository secrets as `CLASSIC_READ_ONLY_PAT_TOKEN`

## Quick Commands

```bash
# Check if secrets are set
gh secret list

# Set GitHub token
gh secret set CLASSIC_READ_ONLY_PAT_TOKEN

# View PROJECTS_JSON
gh variable get PROJECTS_JSON | jq '.'

# Update PROJECTS_JSON
gh variable set PROJECTS_JSON --body "$(cat projects.json)"

# Trigger workflow manually
gh workflow run reporting.yaml
```

## See Also

- **Full Documentation:** `docs/GITHUB_API_CONFIGURATION.md`
- **Change Summary:** `CHANGES_SUMMARY.md`
- **Workflow Restructuring:** `WORKFLOW_RESTRUCTURING.md`
