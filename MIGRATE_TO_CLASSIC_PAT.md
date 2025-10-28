<!--
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 The Linux Foundation
-->

# Migration Guide: Fine-Grained to Classic PAT

## Overview

This guide helps you migrate from a fine-grained Personal Access
Token (PAT) to a Classic PAT for the gerrit-reports
cross-organization analytics workflow.

**Why migrate?** Fine-grained tokens work with a single organization
and cannot span all organizations like ONAP, OpenDaylight,
O-RAN-SC, etc., which enables cross-org reporting.

---

## Pre-Migration Checklist

Before you begin, verify that migration is necessary:

- [ ] Reports show grey/unknown workflow status
- [ ] GitHub Actions logs show 403 Forbidden errors
- [ ] You're using a fine-grained token
- [ ] You need to query all GitHub organizations

If any of these apply, proceed with migration.

---

## Migration Steps

### Step 1: Identify Current Token Type

**Check in GitHub Settings**:

1. Go to <https://github.com/settings/tokens>
2. Look at your active tokens
3. Check the "Type" column:
   - "Fine-grained" = needs migration
   - "Classic" = already correct

**Check in Repository Secrets** (if needed):

1. Go to repository → Settings → Secrets and variables → Actions
2. Note which secret is in use (typically `GERRIT_REPORTS_PAT_TOKEN`)
3. You cannot view the token value, but you can verify it exists

### Step 2: Create New Classic PAT

Follow these steps to create a Classic Personal Access Token:

1. **Navigate to Token Creation**
   - Go to <https://github.com/settings/tokens>
   - Click "Generate new token" → **"Generate new token (classic)"**
   - ⚠️ **Important**: Do NOT select "Fine-grained token"

2. **Configure Token**
   - **Note**: `Gerrit Reports - LF Cross-Org Analytics (Classic)`
   - **Expiry**: Select 90 days (recommended)
   - Set a calendar reminder for token rotation

3. **Select Scopes**
   - ✅ Check `repo` (Full control of private repositories)
   - ✅ Check `actions:read` (Read workflow data)
   - ⚠️ Optional: `read:org`, `read:user`

4. **Generate and Save**
   - Click "Generate token" at the bottom
   - **⚠️ CRITICAL**: Copy the token at once
   - Store in password manager or secure location
   - You won't be able to see it again!

### Step 3: Test New Token

Before updating the workflow, test the new token:

```bash
# Replace YOUR_NEW_TOKEN with the token you created

# 1. Verify token scopes
curl -H "Authorization: token YOUR_NEW_TOKEN" \
  -I https://api.github.com/user \
  | grep x-oauth-scopes

# Expected output: x-oauth-scopes: actions:read, repo
```text
```bash
# 2. Test workflow API access
curl -H "Authorization: token YOUR_NEW_TOKEN" \
  https://api.github.com/repos/onap/aai-aai-common/actions/workflows

# Expected: JSON response with workflow data (not 401/403)
```text
```bash
# 3. Test multi-org access
# Test ONAP
curl -H "Authorization: token YOUR_NEW_TOKEN" \
  https://api.github.com/orgs/onap/repos | head -n 20

# Test OpenDaylight
curl -H "Authorization: token YOUR_NEW_TOKEN" \
  https://api.github.com/orgs/opendaylight/repos | head -n 20

# Test O-RAN-SC
curl -H "Authorization: token YOUR_NEW_TOKEN" \
  https://api.github.com/orgs/o-ran-sc/repos | head -n 20

# Expected: JSON responses from all organizations
```text
If all tests pass, proceed to Step 4.

### Step 4: Update GitHub Repository Secret

1. **Navigate to Repository Secrets**
   - Go to your repository (e.g., `modeseven-lfit/project-reports`)
   - Click Settings → Secrets and variables → Actions

2. **Update Existing Secret**
   - Find `GERRIT_REPORTS_PAT_TOKEN` in the list
   - Click the pencil icon (Update) next to it
   - Paste your new Classic PAT
   - Click "Update secret"

   **OR Create New Secret** (if it doesn't exist):
   - Click "New repository secret"
   - Name: `GERRIT_REPORTS_PAT_TOKEN`
   - Secret: Paste your new Classic PAT
   - Click "Add secret"

### Step 5: Verify Workflow Configuration

Ensure your workflow uses the correct secret:

**Check `.github/workflows/reporting.yaml`**:

```yaml
env:
  GITHUB_TOKEN: ${{ secrets.GERRIT_REPORTS_PAT_TOKEN }}
```text
If the workflow uses a different secret name, update either:

- The secret name in repository settings (to match workflow), OR
- The workflow file (to match secret name)

### Step 6: Run Test Workflow

1. **Trigger Manual Workflow Run**
   - Go to Actions tab
   - Select "📊 Project Reports" workflow
   - Click "Run workflow"
   - Select branch and click "Run workflow"

2. **Check Execution**
   - Watch the workflow run in real-time
   - Check for any authentication errors
   - Review the GitHub Step Summary

3. **Verify Success Indicators**
   - ✅ All jobs complete without errors
   - ✅ No 401/403 errors in logs
   - ✅ GitHub Step Summary shows workflow data
   - ✅ Reports show colored status (not grey)

### Step 7: Revoke Old Token

**After verifying the new token works**:

1. Go to <https://github.com/settings/tokens>
2. Find your old fine-grained token
3. Click "Delete" or "Revoke"
4. Confirm deletion

⚠️ **Warning**: Do NOT revoke the old token before verifying the new one works!

### Step 8: Set Rotation Reminder

Create a reminder to rotate this token before it expires:

- **Calendar event**: 7 days before token expiry
- **Event title**: "Rotate Gerrit Reports GitHub PAT"
- **Event description**: Link to this guide

---

## Verification Checklist

After migration, verify everything works:

- [ ] New Classic PAT created with correct scopes
- [ ] Token tested manually with API calls
- [ ] Repository secret updated with new token
- [ ] Workflow configuration verified
- [ ] Test workflow run completed without errors
- [ ] Reports show colored workflow status (not grey)
- [ ] No authentication errors in logs
- [ ] Old token revoked
- [ ] Calendar reminder set for rotation

---

## Troubleshooting

### Issue: Token tests fail with 403 errors

**Possible causes**:

- Token account not a member of target organizations
- Organization has restricted API access
- Token scopes incorrect

**Solutions**:

1. Verify you're a member of all target orgs (ONAP, OpenDaylight, etc.)
2. Check organization settings for API restrictions
3. Verify token has both `repo` and `actions:read` scopes

### Issue: Workflow still shows grey status after migration

**Possible causes**:

- Token not properly saved in secret
- Workflow using wrong secret name
- Cache or timing issue

**Solutions**:

1. Re-verify secret value in repository settings
2. Check workflow YAML uses correct secret name
3. Re-run workflow manually
4. Check workflow logs for specific error messages

### Issue: Token works for some orgs but not others

**Possible causes**:

- Not a member of all organizations
- Different access levels across organizations

**Solutions**:

1. Request membership in missing organizations
2. Verify you have at least read access to target repos
3. Contact organization admins if necessary

---

## Rollback Plan

If you need to rollback to the old token:

1. **Restore Old Secret**
   - Update `GERRIT_REPORTS_PAT_TOKEN` with old token value
   - (You did save the old token, right?)

2. **Re-run Workflow**
   - Trigger manual workflow run
   - Verify it works as before

3. **Investigate Issues**
   - Review migration steps
   - Check token configuration
   - Review error logs

---

## Post-Migration Best Practices

### Security

- ✅ Store token in password manager
- ✅ Never commit token to Git
- ✅ Rotate token every 90 days
- ✅ Use minimal required scopes
- ✅ Track token usage in GitHub settings

### Monitoring

- ✅ Set up calendar reminders for rotation
- ✅ Track workflow runs for errors
- ✅ Review GitHub Step Summaries on a schedule
- ✅ Check rate limits periodically

### Documentation

- ✅ Document which token is in use
- ✅ Note token expiry date
- ✅ Keep migration guide accessible
- ✅ Update team documentation

---

## Migration Timeline

**Recommended timeline for migration**:

| Phase | Duration | Activities |
|-------|----------|------------|
| Planning | 15 min | Review this guide, prepare calendar |
| Creation | 5 min | Create new Classic PAT |
| Testing | 10 min | Test token with API calls |
| Deployment | 5 min | Update repository secret |
| Verification | 15 min | Run test workflow, verify results |
| Cleanup | 5 min | Revoke old token, set reminders |
| **Total** | **~1 hour** | Including buffer time |

---

## FAQ

### Q: Can I keep both tokens active during migration?

**A**: Yes! This works best.
Keep the old token active until
you have verified the new Classic PAT
works.

### Q: Will this affect running workflows?

**A**: No. Workflows use the secret
value at runtime, so updating the
secret affects new workflow runs.

### Q: What if I by mistake revoked the old token?

**A**: Generate a new Classic PAT at
once and update the secret.
The workflow will work once the new
token is in place.

### Q: Do I need to update all secrets?

**A**: If you have all
repositories using the same token.
Each repository has its own secrets.

### Q: Can I use the same Classic PAT for all workflows?

**A**: Yes, as long as all workflows
need the same scopes.
For security, consider using separate
tokens per use case.

### Q: What happens when the token expires?

**A**: Workflows will fail with 401
authentication errors.
You will need to generate a new token
and update the secret.

---

## Related Documentation

- [GitHub Token Requirements](./GITHUB_TOKEN_REQUIREMENTS.md) - Complete token documentation
- [GitHub PAT Quick Reference](./GITHUB_PAT_QUICK_REFERENCE.md) - Fast setup guide
- [GitHub API Error Logging](./GITHUB_API_ERROR_LOGGING.md) - Troubleshooting errors
- [Setup Guide](./SETUP.md) - In summary workflow setup

---

## Support

If you encounter issues during migration:

1. Review troubleshooting section above
2. Check workflow logs in GitHub Actions
3. Test token manually using curl commands
4. Review [GITHUB_TOKEN_REQUIREMENTS.md](./GITHUB_TOKEN_REQUIREMENTS.md)
5. Open an issue with error logs (redact token values!)

---

**Migration Status Template** (copy to your notes):

```text
Migration Date: ___________
Old Token Type: Fine-grained
New Token Type: Classic
Token Note: Gerrit Reports - LF Cross-Org Analytics (Classic)
Token Expiry: ___________
Rotation Reminder Set: ☐ Yes ☐ No
Migration Successful: ☐ Yes ☐ No
Old Token Revoked: ☐ Yes ☐ No
```text
