<!--
SPDX-License-Identifier: Apache-2.0
SPDX-FileCopyrightText: 2025 The Linux Foundation
-->

# INFO.yaml Committer Report - Quick Start Guide

## 5-Minute Setup

### Step 1: Clone info-master Repository

```bash
git clone https://gerrit.linuxfoundation.org/infra/releng/info-master testing/info-master
```

### Step 2: Configure Your Project

Add to your `configuration/myproject.config`:

```yaml
# Enable INFO.yaml committer report
info_yaml:
  enabled: true
  local_path: "testing/info-master"
```

### Step 3: Generate Report

```bash
./generate_reports.py \
  --project myproject \
  --repos /path/to/your/repositories \
  --output reports/
```

### Step 4: View Results

Open `reports/myproject/report.html` and scroll to the
**"📋 Committer INFO.yaml Report"** section.

---

## What You'll See

### Report Format

```text
## 📋 Committer INFO.yaml Report

### gerrit.onap.org
| Project | Creation Date | Lifecycle State | Project Lead | Committers |
|---------|---------------|-----------------|--------------|------------|
| aaf-authz | 2017-07-12 | Incubation | Jonathan Gathman | John Doe ✅
|         |               |                 |              | Jane Smith ☑️
|         |               |                 |              | Bob Jones 🛑
```

### Committer Color Codes

- 🟢 **Green (✅ Current)**: Active in last 365 days
- 🟠 **Orange (☑️ Active)**: Active 365-1095 days ago
- 🔴 **Red (🛑 Inactive)**: No activity in 1095+ days
- ⚫ **Gray (❓ Unknown)**: No matching Git data

### Project Name Links

- **Blue hyperlink**: Valid issue tracker URL (clickable)
- **Red text**: Broken issue tracker URL (hover for error)
- **Plain text**: No issue tracker configured

---

## Common Scenarios

### Scenario 1: Development/Testing

Use existing local clone to avoid repeated cloning:

```yaml
info_yaml:
  enabled: true
  local_path: "testing/info-master"
  validate_urls: false  # Skip validation for speed
```

### Scenario 2: Production Reports

Clone fresh copy and validate URLs:

```yaml
info_yaml:
  enabled: true
  clone_url: "https://gerrit.linuxfoundation.org/infra/releng/info-master"
  validate_urls: true
  url_timeout: 10.0
```

### Scenario 3: Custom Activity Windows

More aggressive thresholds for active projects:

```yaml
info_yaml:
  enabled: true
  local_path: "testing/info-master"
  activity_windows:
    current: 180   # 6 months for "current"
    active: 730    # 2 years for "active"
```

### Scenario 4: Conservative Thresholds

Longer windows for mature/stable projects:

```yaml
info_yaml:
  enabled: true
  local_path: "testing/info-master"
  activity_windows:
    current: 730   # 2 years for "current"
    active: 1825   # 5 years for "active"
```

---

## Troubleshooting

### Problem: No INFO.yaml section in report

**Solution:**

```yaml
# Check configuration
info_yaml:
  enabled: true  # Must be true!
  local_path: "testing/info-master"  # Path must exist
```

**Verify:**

```bash
ls -la testing/info-master/.git
# Should show git directory
```

### Problem: All committers show as "Unknown" (gray)

**Causes:**

- Email mismatch between INFO.yaml and Git commits
- Repository name mismatch
- No Git data collected

**Solutions:**

1. Check INFO.yaml email matches Git:

   ```bash
   # In INFO.yaml
   email: 'john.doe@example.com'

   # In Git history
   git log --format='%ae' | grep john.doe
   ```

2. Verify repository associations in INFO.yaml:

   ```yaml
   repositories:
     - 'correct-repo-name'
   ```

3. Check Git data collection logs:

   ```bash
   ./generate_reports.py --project myproject --repos /path --log-level DEBUG
   ```

### Problem: URL validation failing

**Quick fix - Disable validation:**

```yaml
info_yaml:
  enabled: true
  local_path: "testing/info-master"
  validate_urls: false
```

**Permanent fix - Increase timeout:**

```yaml
info_yaml:
  validate_urls: true
  url_timeout: 30.0  # Increase from default 10s
```

### Problem: Report generation slow

**Fast mode configuration:**

```yaml
info_yaml:
  enabled: true
  local_path: "testing/info-master"
  validate_urls: false  # Biggest speedup

performance:
  max_workers: 16  # Parallel processing
  cache: true      # Cache Git data
```

---

## Testing the Feature

Run the test suite:

```bash
python test_info_yaml_collector.py
```

Expected output:

```text
✅ PASS: INFO.yaml Collection
✅ PASS: INFO.yaml Parsing
✅ PASS: Committer Enrichment
✅ PASS: URL Validation

Total: 4/4 tests passed
🎉 All tests passed!
```

---

## Example: Complete Configuration

```yaml
# configuration/myproject.config

project: myproject

# Enable INFO.yaml report
info_yaml:
  enabled: true
  local_path: "testing/info-master"

  # Customize activity thresholds
  activity_windows:
    current: 365    # Green if active within 1 year
    active: 1095    # Orange if active within 3 years
    # Red if inactive longer than 3 years

  # URL validation settings
  validate_urls: true
  url_timeout: 10.0

# Other report settings
output:
  include_sections:
    contributors: true
    organizations: true
    repo_feature_matrix: true
    all_repositories: true

time_windows:
  last_30_days: 30
  last_90_days: 90
  last_365_days: 365
  last_3_years: 1095

performance:
  max_workers: 8
  cache: false

html_tables:
  sortable: true
  searchable: true
  pagination: true
  entries_per_page: 50
```

---

## Use Cases

### 1. Identify Inactive Projects

Filter the report table to find projects with:

- All red (inactive) committers
- "Archived" lifecycle state
- Broken issue tracker links

**Action**: Consider project archival or committer list updates

### 2. Find Active Projects for New Contributors

Look for:

- Green committers (active community)
- "Incubation" or "Mature" lifecycle
- Valid issue tracker link

**Action**: Direct new contributors to these projects

### 3. Audit Committer Lists

Review projects where:

- Project lead is red (inactive)
- Most committers are orange/red
- Company diversity is low

**Action**: Recommend TSC review or committer elections

### 4. Track Community Health

Generate reports monthly and compare:

- Number of green vs red committers over time
- Projects transitioning from active to inactive
- New projects with active communities

**Action**: Identify trends and intervention opportunities

---

## Next Steps

1. **Read Full Documentation**: `docs/INFO_YAML_REPORT.md`
2. **Review Configuration**: `configuration/template.config`
3. **Run Tests**: `python test_info_yaml_collector.py`
4. **Generate First Report**: Follow Step 3 above
5. **Customize**: Adjust activity windows for your needs

---

## FAQ

**Q: How often should I update info-master?**
A: Weekly for active projects, monthly for stable projects.

**Q: Can I use my own INFO.yaml repository?**
A: Yes, set `local_path` to your repository location.

**Q: What if a project has no matching Git data?**
A: Committers will show as gray (unknown). Check repository name mapping.

**Q: Can I customize the colors?**
A: Colors are hardcoded but status thresholds are configurable via `activity_windows`.

**Q: Does this work with GitHub repositories?**
A: Yes, as long as Git commit history is available.

**Q: How do I disable this feature?**
A: Set `info_yaml.enabled: false` in your configuration.

---

## Support

- **Documentation**: `docs/INFO_YAML_REPORT.md`
- **Tests**: `test_info_yaml_collector.py`
- **Configuration**: `configuration/template.config`
- **Issues**: Open an issue on the project repository

---

**Last Updated**: January 2025
**Version**: 1.0.0
**Estimated Setup Time**: 5 minutes
