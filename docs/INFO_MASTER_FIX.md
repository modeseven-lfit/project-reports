<!--
SPDX-License-Identifier: Apache-2.0
SPDX-FileCopyrightText: 2025 The Linux Foundation
-->

# Info-Master Repository Handling Fix

## Problem Summary

After adding the INFO.yaml committer table feature in commit `ef41e90`,
the GitHub Actions workflow was failing with the error:

```text
Info-Master Repository
❌ Clone failed: Local path not found or not a git repo
```

## Root Cause

There was a mismatch between how the workflow and the Python script
handled the info-master repository:

### Previous Workflow (PR #18)

- The GitHub Actions workflow cloned info-master to `./info-master` in the runner
- Used SSH authentication with fallback to HTTPS

### New Feature (Commit ef41e90)

- Added INFO.yaml parsing capability to generate committer tables
- Configuration template had hardcoded `local_path: "testing/info-master"`
- This path was intended for local development/testing only

### The Conflict

1. **In CI/CD**: Workflow clones to `./info-master`
2. **In Python script**: Configuration says to look for
   `testing/info-master`
3. **Result**: Script can't find the repository → clone fails → no
   committer table

## Understanding the Context

The `testing/info-master` directory in the local repository was a
**reference clone** for development purposes. It was meant to:

- Allow developers to see the structure of the info-master repository
- Test INFO.yaml parsing locally without cloning each time
- Understand what INFO.yaml files look like

It was **NOT** meant to be used in the CI/CD pipeline. The local copy was
only for development reference purposes.

## Solution Implemented

### 1. Updated Configuration Template (`configuration/template.config`)

**Before:**

```yaml
info_yaml:
  enabled: true
  local_path: "testing/info-master"  # Path to cloned info-master repository
  clone_url: "https://gerrit.linuxfoundation.org/infra/releng/info-master"
```

**After:**

```yaml
info_yaml:
  enabled: true
  # Uncomment and set this for local development only - do not use in CI/CD
  # local_path: "testing/info-master"  # Path to cloned info-master repository

  # Option 2: Clone from remote URL (used if local_path is not set or doesn't exist)
  # In CI/CD, the workflow clones to ./info-master and the script will find it automatically
  clone_url: "https://gerrit.linuxfoundation.org/infra/releng/info-master"
```

**Change**: Commented out `local_path` since it's only for local dev

### 2. Enhanced Python Script Logic (`generate_reports.py`)

Updated `_clone_info_master_repo()` method to check multiple sources in
priority order:

```python
def _clone_info_master_repo(self) -> Optional[Path]:
    """
    Clone the info-master repository for additional context data.

    Priority order:
    1. Environment variable INFO_MASTER_PATH
    2. Config setting info_yaml.local_path
    3. Check for ./info-master (created by CI workflow)
    4. Clone to temporary directory
    """
```

**New Logic Flow:**

1. **Check `INFO_MASTER_PATH` environment variable** (highest priority)
   - Allows explicit override via environment
   - Useful for special CI/CD scenarios

2. **Check config `local_path` setting**
   - For local development/testing
   - Falls through with warning if path doesn't exist (doesn't fail)

3. **Check for `./info-master` directory** ⭐ **NEW**
   - This is what the GitHub Actions workflow creates
   - Automatically detected without configuration
   - No temp directory cleanup needed

4. **Clone to temporary directory** (fallback)
   - Creates temp directory with `mkdtemp`
   - Clones from `clone_url` in config
   - Registers cleanup handler via `atexit`

### 3. Improved Workflow Logging

Added status reporting to `GITHUB_STEP_SUMMARY`:

```yaml
- name: "Clone info-master repository"
  run: |
    # ... clone logic ...

    # Add info-master status to workflow summary
    {
      echo ""
      echo "## Info-Master Repository"
      echo ""
      if [ -d "./info-master/.git" ]; then
        echo "✅ Clone successful: $(pwd)/info-master"
        echo ""
        echo "Clone method: ${SSH_AVAILABLE:-false}"
        echo ""
        yaml_count=$(find ./info-master -name "INFO.yaml" | wc -l)
        echo "📊 Found ${yaml_count} INFO.yaml files"
      else
        echo "❌ Clone failed: Local path not found or not a git repo"
      fi
    } >> "$GITHUB_STEP_SUMMARY"
```

## How It Works Now

### In GitHub Actions (CI/CD)

1. Workflow clones info-master to `./info-master`
2. Python script runs with no `local_path` in config
3. Script checks for `./info-master` → finds it ✅
4. Uses the workflow-cloned repository
5. No cleanup needed (workflow manages it)

### In Local Development

1. Developer can uncomment `local_path: "testing/info-master"` in their local config
2. Script finds and uses the local clone
3. No need to clone on each run
4. Faster development iteration

### In Edge Cases

1. If neither exists, script clones to temp directory
2. Works standalone without workflow
3. Cleans up temp directory on exit

## Testing

To verify the fix works:

1. **CI/CD**: Run the workflow and check `GITHUB_STEP_SUMMARY` for:

   ```text
   ## Info-Master Repository
   ✅ Clone successful: /home/runner/work/.../info-master
   Clone method: true/false
   📊 Found [N] INFO.yaml files
   ```

2. **Locally with testing clone**:

   ```bash
   # Uncomment local_path in your config
   vim configuration/myproject.config
   # Run script
   python3 generate_reports.py --project "MyProject" --repos-path "./gerrit.example.org"
   ```

3. **Locally without testing clone**:

   ```bash
   # Keep local_path commented
   # Run script - should auto-detect ./info-master if present, or clone to temp
   python3 generate_reports.py --project "MyProject" --repos-path "./gerrit.example.org"
   ```

## Key Takeaways

1. **Separation of Concerns**: Local dev tools (testing/) should not be
   referenced in production configs
2. **Graceful Degradation**: Script now tries multiple methods before
   failing
3. **Convention over Configuration**: Checking `./info-master` as a
   standard location reduces config needs
4. **Better Logging**: Workflow summary now clearly shows info-master status
5. **Backward Compatible**: Existing local dev setups still work with config override

## Related Files

- `configuration/template.config` - Configuration template
- `generate_reports.py` - Main script with `_clone_info_master_repo()` method
- `.github/workflows/reporting.yaml` - Workflow that clones info-master
- `testing/info-master/` - Local reference clone (dev only, not used in
  CI)

## Related Commits

- **PR #18**: Added SSH authentication for info-master cloning in
  workflow
- **Commit ef41e90**: Added INFO.yaml committer table feature
- **This fix**: Makes info-master cloning work correctly in all environments
