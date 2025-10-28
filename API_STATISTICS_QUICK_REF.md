<!--
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 The Linux Foundation
-->

# API Statistics - Quick Reference

## What's New

The gerrit-reports system now tracks and reports statistics for all
external API calls:

✅ **GitHub API** - workflow queries
✅ **Gerrit API** - project information
✅ **Jenkins API** - job discovery
✅ **Info-Master** - repository clone status

---

## Where to Find Statistics

### Console Output (GitHub Actions Logs)

Look at the **end of the workflow run** for a section like:

```text
📊 GitHub API Statistics:
   ✅ Successful calls: 178
   ❌ Failed calls: 5
      • Error 403: 3
      • Error 404: 2
```

### GitHub Step Summary

Click on a workflow run → scroll to **📊 External API Statistics**
section

---

## Common Error Codes

| Code | API | Meaning | Fix |
|------|-----|---------|-----|
| 401 | GitHub | Token expired | Update `GERRIT_REPORTS_PAT_TOKEN` secret |
| 403 | GitHub | Missing permissions | Use Classic PAT |
| 404 | GitHub | Repo not found | Normal for some queries |
| 404 | Gerrit | Project not found | Normal |
| 500 | Any | Server error | Retry or check server status |

---

## Healthy Output Example

```text
📊 GitHub API Statistics:
   ✅ Successful calls: 178

📊 Gerrit API Statistics:
   ✅ Successful calls: 178

📊 Jenkins API Statistics:
   ✅ Successful calls: 1

📊 Info-Master Clone:
   ✅ Clone completed
```

**No errors = everything working!**

---

## Problem: All GitHub Calls Fail (401)

```text
📊 GitHub API Statistics:
   ✅ Successful calls: 0
   ❌ Failed calls: 178
      • Error 401: 178
```

**Fix**: Token expired or invalid

1. Settings → Secrets and variables → Actions
2. Update `GERRIT_REPORTS_PAT_TOKEN`
3. Use Classic PAT (not fine-grained)

---

## Problem: All GitHub Calls Fail (403)

```text
📊 GitHub API Statistics:
   ✅ Successful calls: 0
   ❌ Failed calls: 178
      • Error 403: 178
```

**Fix**: Missing permissions

1. Verify token is **Classic PAT**
2. Verify scopes: `repo` + `actions:read`
3. Verify account is member of target orgs

See: [GITHUB_TOKEN_REQUIREMENTS.md](./GITHUB_TOKEN_REQUIREMENTS.md)

---

## Problem: Info-Master Clone Failed

```text
📊 Info-Master Clone:
   ❌ Failed: Clone failed: Permission denied (publickey)
```

**Fix**: SSH key or connectivity issue

1. Check SSH keys configured
2. Check network connectivity to gerrit.linuxfoundation.org
3. Review workflow logs for details

---

## Full Documentation

- [API Statistics Guide](./API_STATISTICS.md) - Complete documentation
- [Changelog](./CHANGELOG_API_STATISTICS.md) - What changed
- [Token Requirements](./GITHUB_TOKEN_REQUIREMENTS.md) - Token setup

---

## Testing

```bash
# Run test suite
python3 test_api_statistics.py

# Expected output:
# 🎉 All tests passed!
```

---

## Tips

1. **No statistics section?** → No API calls made (integrations
   disabled)
2. **404 errors normal?** → Yes, for some GitHub/Gerrit queries
3. **Tracking across runs?** → Check Step Summary in each workflow run

---

**Need Help?** See [API_STATISTICS.md](./API_STATISTICS.md) for
detailed troubleshooting
