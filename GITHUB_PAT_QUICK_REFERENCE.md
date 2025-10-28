<!--
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 The Linux Foundation
-->

# GitHub Classic PAT Quick Reference

## For Gerrit Reports Cross-Organization Analytics

---

## ⚡ Quick Setup (5 minutes)

### 1. Create Classic PAT

🔗 **Go to**: <https://github.com/settings/tokens>

**Steps**:

1. Click **"Generate new token"** → **"Generate new token (classic)"**
2. **Note**: `Gerrit Reports - LF Cross-Org Analytics`
3. **Expires**: 90 days (set reminder!)
4. **Select scopes**:
   - ✅ `repo`
   - ✅ `actions:read`
5. Click **"Generate token"**
6. **⚠️ COPY TOKEN NOW** (you can't see it again!)

### 2. Add to GitHub Repository

**Steps**:

1. Go to repository → **Settings** → **Secrets and variables** →
   **Actions**
2. Click **"New repository secret"**
3. **Name**: `GERRIT_REPORTS_PAT_TOKEN`
4. **Secret**: Paste token
5. Click **"Add secret"**

---

## ✅ Required Scopes

| Scope | Purpose |
|-------|---------|
| `repo` | Read repository metadata & workflows |
| `actions:read` | Read workflow runs & status |

**Optional**: `read:org`, `read:user`

---

## ❌ Common Mistakes

### ❌ DO NOT use Fine-Grained Tokens

**Why**: Limited to single organization, cannot span
ONAP/OpenDaylight/O-RAN-SC/etc.

### ❌ DO NOT use default `${{ secrets.GITHUB_TOKEN }}`

**Why**: Lacks `actions:read` scope, works in current repo alone

### ❌ DO NOT skip setting token expiry

**Why**: Token will stop working without warning

---

## 🧪 Test Your Token

```bash
# Check scopes
curl -H "Authorization: token YOUR_TOKEN" \
  -I https://api.github.com/user | grep x-oauth-scopes

# Expected: x-oauth-scopes: actions:read, repo
```

```bash
# Test workflow access
curl -H "Authorization: token YOUR_TOKEN" \
  https://api.github.com/repos/onap/aai-aai-common/actions/workflows

# Expected: JSON with workflow data (not 401/403)
```

---

## 🔍 Troubleshooting

| Problem | Solution |
|---------|----------|
| Grey workflow status in reports | Check token is Classic PAT |
| 401 Unauthorized errors | Token expired or invalid - regenerate |
| 403 Forbidden errors | Missing scopes or not org member |
| Works locally, fails in CI | Check secret exists |

---

## 📚 Full Documentation

See [GITHUB_TOKEN_REQUIREMENTS.md](./GITHUB_TOKEN_REQUIREMENTS.md) for:

- Classic vs Fine-Grained comparison
- Security best practices
- Bot account setup
- Detailed troubleshooting
- FAQ

---

## 🔐 Security Checklist

- [ ] Classic token (not fine-grained)
- [ ] Expires in 90 days
- [ ] Required scopes selected
- [ ] Token stored in GitHub Secrets
- [ ] Calendar reminder for rotation
- [ ] Token tested with API calls

---

## 📅 Maintenance

**Token Rotation Schedule**: Every 90 days

**Process**:

1. Generate new Classic PAT (same scopes)
2. Update `GERRIT_REPORTS_PAT_TOKEN` secret
3. Verify workflow runs without errors
4. Revoke old token
5. Set reminder for next rotation

---

**Need Help?**
See [GITHUB_TOKEN_REQUIREMENTS.md](./GITHUB_TOKEN_REQUIREMENTS.md)
