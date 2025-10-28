<!--
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 The Linux Foundation
-->

# GitHub Token Requirements for Cross-Organization Reporting

## Overview

The gerrit-reports project queries GitHub API to
retrieve workflow status information for repositories across
all Linux Foundation organizations.
This requires a properly configured Personal Access Token (PAT) with specific permissions.

You **MUST use a Classic Personal Access
Token**, not a fine-grained token, for multi-organization reporting.

---

## Classic vs. Fine-Grained Tokens

### Why Classic Tokens Work Best

GitHub offers two types of Personal Access Tokens:

1. **Classic Tokens** (recommended for this project)

- ✅ Can access **all organizations** a user is a member of
- ✅ Simple to configure with broad scope-based permissions
- ✅ Works seamlessly for cross-organization analytics
- ⚠️ Requires careful scope selection to reduce permissions

1. **Fine-Grained Tokens**
   (NOT suitable for this project)

- ❌ Limited to a **single organization** per token
- ❌ Cannot span all organizations
- ❌ Would require managing all tokens and complex workflow logic
- ✅ More granular permissions (but impractical for our use case)

### The Multi-Organization Challenge

The gerrit-reports workflow needs to query GitHub APIs
across organizations such as:

- `onap` (ONAP project repositories)
- `opendaylight` (OpenDaylight project repositories)
- `o-ran-sc` (O-RAN SC
  repositories)
- `lfnetworking` (LF Networking
  repositories)
- And others...

**Fine-grained tokens cannot access
all organizations**, making them
unsuitable for this cross-organization
reporting scenario.

---

## Required Token Scopes (Classic PAT)

When creating a Classic Personal Access Token for gerrit-reports,
you need the following scopes:

### Required Scopes

| Scope | Purpose | Required? |
|-------|---------|-----------|
| `repo` | Read repository metadata and access workflow data | ✅ **Required** |
| `actions:read` | Read workflow definitions and run statuses | ✅ **Required** |

### Alternative Minimal Scope

If you want to reduce permissions, you can use:

- `repo:status` (instead of full `repo`)
- `actions:read`

The `repo` scope works best for better compatibility and future enhancements.

### Optional But Recommended Scopes

| Scope | Purpose | Recommended? |
|-------|---------|---------------|
| `read:org` | Read organization membership and team data | ⚠️ Optional |
| `read:user` | Read user profile data | ⚠️ Optional |

---

## How to Create a Classic Personal Access Token

### Step-by-Step Instructions

1. **Navigate to GitHub Settings**

- Go to
- <https://github.com/settings/tokens>
  - Or: Click your profile picture →
     Settings → Developer settings →
     Personal access tokens →
     Tokens (classic)

1. **Generate New Token (Classic)**

- Click "Generate new token" → "Generate new token (classic)"
- **Important**: Do NOT choose "Fine-grained token"

1. **Configure Token Settings**

- **Note**: Enter a descriptive name like
  `Gerrit Reports - LF Cross-Org Analytics`
- **Expiry**: Choose an appropriate expiry period
  - Recommendation: 90 days or custom duration
  - Set a calendar reminder to rotate the token before expiry

1. **Select Required Scopes**

- Check the following boxes:
  - ✅ `repo` (Full control of private repositories)
    - This automatically includes all sub-scopes like `repo:status`
  - ✅ `actions:read` (under "Workflows" section)
- Optionally check:
  - `read:org` (Read org and team membership, read org projects)
  - `read:user` (Read user profile data)

1. **Generate and Copy Token**

- Click "Generate token" at the bottom
- **⚠️ CRITICAL**: Copy the token at once - you won't be able to see it again!
- Store it securely (e.g., password manager)

---

## Security Best Practices

### Token Management

1. **Use Expiry Dates**

- Always set an expiry date on tokens
- Recommended: 90 days max
- Set up monitoring/reminders for token rotation

1. **Least Privilege Principle**

- Grant the minimal required scopes
- Use `repo:status` instead of full `repo` if possible
- Avoid unnecessary scopes like `write` or `admin` permissions

1. **Secure Storage**

- Never commit tokens to Git repositories
- Use GitHub Secrets for CI/CD workflows
- Use password managers or secure vaults for local development

1. **Token Rotation**

- Rotate tokens on a schedule (every 90 days recommended)
- At once revoke tokens if compromised
- Update all dependent systems when rotating

### Using a Dedicated Bot Account (Optional)

For production deployments, consider creating a dedicated GitHub bot account:

**Advantages**:

- Isolates token permissions from personal accounts
- Easier to manage and rotate tokens
- Clearer audit trails
- No impact if the account owner leaves the organization

**Steps**:

1. Create a new GitHub account (e.g., `lf-gerrit-reports-bot`)
2. Add the bot account to all relevant organizations with read access
3. Generate a Classic PAT from the bot account
4. Use the bot account's token in CI/CD workflows

**Cost**: The bot account must have appropriate
organization membership, which may require licenses
depending on organization settings.

---

## Setting Up the Token in GitHub Actions

### Add Token to Repository Secrets

1. **Navigate to Repository Settings**

- Go to your repository → Settings → Secrets and variables → Actions

1. **Add New Repository Secret**

- Click "New repository secret"
- **Name**: `GERRIT_REPORTS_PAT_TOKEN`
- **Secret**: Paste your Classic PAT
- Click "Add secret"

1. **Verify Workflow Configuration**

   The workflow should already work to use this secret:

   ```yaml
   env:
     GITHUB_TOKEN: ${{ secrets.GERRIT_REPORTS_PAT_TOKEN }}
   ```

### Important Notes

- ⚠️ **Do NOT use** the default
  `${{ secrets.GITHUB_TOKEN }}` provided by
  GitHub Actions
  - The default token does NOT have `actions:read` scope
  - The default token works in
    current repository
  - It cannot access other organizations or repositories

---

## Troubleshooting

### Problem: Workflows Show as Grey/Unknown in Reports

**Symptoms**:

- HTML reports display grey circles instead of green/red/yellow status indicators
- No workflow status information available

**Root Cause**:

- GitHub API authentication failure (401)
- GitHub API permission denied (403)
- Fine-grained token used instead of classic token
- Token missing required scopes

**Solution**:

1. Check GitHub Actions workflow logs for error messages
2. Verify you're using a **Classic PAT**, not fine-grained
3. Verify token has `repo` and `actions:read` scopes
4. Test token manually (see below)

### Problem: API Calls Fail for Some Organizations

**Symptoms**:

- Works for some repositories but not others
- 403 Forbidden errors in logs

**Root Cause**:

- Token account not member of target organizations
- Organization has restricted API access

**Solution**:

1. Verify the token's account is a member of all target organizations
2. Check organization settings for API access restrictions
3. For bot accounts, ensure proper organization membership

### Problem: Token Expired or Invalid

**Symptoms**:

- 401 Unauthorized errors in logs
- "Bad credentials" error messages

**Solution**:

1. Check token expiry date in GitHub settings
2. Generate a new token following the steps above
3. Update the `GERRIT_REPORTS_PAT_TOKEN` secret
4. Re-run the workflow

---

## Testing Your Token

### Manual API Testing

Test your token before using it in workflows:

#### 1. Check Token Scopes

```bash
curl -H "Authorization: token YOUR_TOKEN_HERE" \
  -I https://api.github.com/user \
  | grep x-oauth-scopes
```text
Expected output should include:

```text
x-oauth-scopes: actions:read, repo
```text
#### 2. Test Workflow API Access

```bash
curl -H "Authorization: token YOUR_TOKEN_HERE" \
  https://api.github.com/repos/onap/aai-aai-common/actions/workflows
```text
Expected: JSON response with workflow definitions

#### 3. Check Rate Limits

```bash
curl -H "Authorization: token YOUR_TOKEN_HERE" \
  https://api.github.com/rate_limit
```text
Expected: JSON showing your rate limit status (5000/hour for authenticated requests)

#### 4. Test Cross-Organization Access

```bash
# Test ONAP organization
curl -H "Authorization: token YOUR_TOKEN_HERE" \
  https://api.github.com/orgs/onap/repos

# Test OpenDaylight organization
curl -H "Authorization: token YOUR_TOKEN_HERE" \
  https://api.github.com/orgs/opendaylight/repos
```text
Expected: JSON responses with repository lists from each organization

### GitHub CLI Testing

If you have GitHub CLI installed:

```bash
# Authenticate with your token
gh auth login --with-token < token.txt

# Test workflow access
gh api repos/onap/aai-aai-common/actions/workflows

# Check rate limit
gh api rate_limit
```text
---

## Token Checklist

Before deploying your token, verify:

- [ ] Token is a **Classic Personal Access Token** (not fine-grained)
- [ ] Token has `repo` scope selected
- [ ] Token has `actions:read` scope selected
- [ ] Token has an appropriate expiry date set
- [ ] Token lives securely (password manager or GitHub Secrets)
- [ ] Token account is a member of all target organizations
- [ ] Token has run manually with API calls
- [ ] `GERRIT_REPORTS_PAT_TOKEN` secret exists in repository settings
- [ ] Calendar reminder set for token rotation before expiry

---

## FAQ

### Q: Why can't I use fine-grained tokens?

**A**: Fine-grained tokens work with a single
organization.

### Q: Is it safe to use a classic token with broad scopes?

**A**: Yes, when following security best practices:

- Use read scopes (`repo`, `actions:read`)
- Set expiry dates (90 days recommended)
- Store securely in GitHub Secrets
- Consider using a dedicated bot account
- Rotate on a schedule

### Q: What if my organization requires fine-grained tokens?

**A**: You have two options:

1. Request an exception for cross-organization analytics use case
2. Use multi-token support (complex, not recommended)
  - Create one fine-grained token per organization
  - Update workflow to map tokens to organizations
  - Maintain token-to-org configuration

### Q: How do I know if my token is working?

**A**: Check the GitHub Actions workflow logs and Step Summary:

- ✅ **Success**: Colored workflow status indicators in reports
- ❌ **Failure**: Error messages in logs, grey indicators in reports
- Use manual API testing (see "Testing Your Token" above)

### Q: Can I use the default GITHUB_TOKEN from GitHub Actions?

**A**: No. The default `${{ secrets.GITHUB_TOKEN }}`:

- Does NOT have `actions:read`
  scope
- Scoped to the current repository
- Cannot access other organizations
You must use a Personal Access Token.

---

## Related Documentation

- [GitHub API Error Logging]
  (./GITHUB_API_ERROR_LOGGING.md) - Detailed error
  logging and troubleshooting
- [Setup Guide](./SETUP.md) - Workflow setup and configuration
- [GitHub API Documentation]
  (https://docs.github.com/en/rest) - Official
  GitHub API reference
- [GitHub PAT Documentation]
  (https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens)
  - Official token management guide

---

## Support

If you encounter issues with token configuration:

1. Review the troubleshooting section above
2. Check workflow logs in GitHub Actions
3. Review the GitHub Step Summary for specific error messages
4. Test your token manually using the commands in "Testing Your Token"
5.
Open an issue in the repository with relevant log excerpts (redact token values!)
