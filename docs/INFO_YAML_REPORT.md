<!--
SPDX-License-Identifier: Apache-2.0
SPDX-FileCopyrightText: 2025 The Linux Foundation
-->

# INFO.yaml Committer Report

## Overview

The INFO.yaml Committer Report feature generates comprehensive reports about
project committers based on INFO.yaml files stored in the Linux Foundation's
`info-master` repository. This report provides visibility into:

- Project metadata (creation date, lifecycle state)
- Project leadership
- Committer activity levels
- Issue tracker link validation

## Features

### 1. Project Information Collection

The system automatically scans the `info-master` repository and collects data
from all INFO.yaml files, including:

- **Project name** and creation date
- **Lifecycle state** (Incubation, Mature, Archived, etc.)
- **Project lead** information
- **Committers list** with contact details
- **Issue tracker** URLs
- **Repository** associations

### 2. Committer Activity Colorization

Each committer's name is color-coded based on their Git commit activity
within the project:

<!-- markdownlint-disable MD013 -->
| Color  | Status      | Description                      | Time Window    |
|--------|-------------|----------------------------------|----------------|
| 🟢 Green | ✅ Current | Commits within last 365 days    | 0-365 days     |
| 🟠 Orange | ☑️ Active  | Commits 365-1095 days ago       | 365-1095 days  |
| 🔴 Red   | 🛑 Inactive | No commits in 1095+ days        | 1095+ days     |
| ⚫ Gray  | ❓ Unknown | No matching Git data found      | N/A            |
<!-- markdownlint-enable MD013 -->

The color coding helps identify:

- **Active contributors** who are currently engaged
- **Recently active contributors** who may be scaling back
- **Inactive contributors** who haven't contributed recently
- **Missing data** where Git history doesn't match INFO.yaml records

### 3. Issue Tracker Validation

The system validates each project's issue tracker URL:

- ✅ **Valid URLs**: Project name becomes a clickable hyperlink
- ⚠️ **Broken URLs**: Project name displayed in red with tooltip showing error
- 📍 **Missing URLs**: Project name displayed as plain text

Validation includes:

- HTTP status code checking
- Redirect following (e.g., HTTP → HTTPS)
- Connection timeout handling
- Error categorization (404, timeout, connection refused, etc.)

### 4. Hierarchical Organization

The report organizes projects by Gerrit server:

```text
## 📋 Committer INFO.yaml Report

### gerrit.onap.org
```

<!-- markdownlint-disable MD013 -->
| Project | Creation Date | Lifecycle State | Project Lead | Committers |
|---------|---------------|-----------------|--------------|------------|
| aaf-authz | 2017-07-12 | Incubation | Jonathan Gathman | (colored list) |
| cli | 2017-06-22 | Incubation | Dan Xu | (colored list) |
<!-- markdownlint-enable MD013 -->

```text
### gerrit.o-ran-sc.org
```

<!-- markdownlint-disable MD013 -->
| Project | Creation Date | Lifecycle State | Project Lead | Committers |
|---------|---------------|-----------------|--------------|------------|
| (projects...) | ... | ... | ... | ... |
<!-- markdownlint-enable MD013 -->

## Configuration

### Basic Configuration

Add to your project configuration file (e.g., `configuration/myproject.config`):

```yaml
info_yaml:
  # Enable INFO.yaml committer report generation
  enabled: true

  # Use existing local path (recommended for development/testing)
  local_path: "testing/info-master"

  # Activity time windows (in days)
  activity_windows:
    current: 365    # Green - commits within last year
    active: 1095    # Orange - commits within last 3 years
```

### Advanced Configuration

```yaml
info_yaml:
  enabled: true

  # Option 1: Use existing local repository
  local_path: "/path/to/local/info-master"

  # Option 2: Clone from remote URL (used if local_path not available)
  clone_url: "https://gerrit.linuxfoundation.org/infra/releng/info-master"

  # Customize activity time windows
  activity_windows:
    current: 180     # More aggressive: 6 months for "current"
    active: 730      # More aggressive: 2 years for "active"

  # Issue tracker URL validation settings
  validate_urls: true
  url_timeout: 10.0  # Timeout in seconds
```

### Project-Specific Time Windows

Different projects can have different activity thresholds:

```yaml
# Conservative project (longer windows)
info_yaml:
  activity_windows:
    current: 730     # 2 years for current
    active: 1825     # 5 years for active

# Aggressive project (shorter windows)
info_yaml:
  activity_windows:
    current: 180     # 6 months for current
    active: 365      # 1 year for active
```

## Data Collection Process

### 1. Repository Cloning/Access

The system either:

- Uses an existing local `info-master` repository (if `local_path` is configured)
- Clones the repository to a temporary directory (cleaned up after report generation)

### 2. INFO.yaml Parsing

For each `INFO.yaml` file found:

```yaml
project: 'example-project'
project_creation_date: '2020-01-15'
lifecycle_state: 'Incubation'
project_lead:
  name: 'John Doe'
  email: 'john.doe@example.com'
  company: 'Example Corp'
  id: 'johnd'
committers:
  - name: 'Jane Smith'
    email: 'jane.smith@example.com'
    id: 'janes'
  - name: 'Bob Johnson'
    email: 'bob.j@example.com'
    id: 'bobj'
issue_tracking:
  type: 'jira'
  url: 'https://jira.example.org/projects/PROJ'
repositories:
  - 'project-repo-1'
  - 'project-repo-2'
```

### 3. Git Data Enrichment

The system matches committers to Git commit history:

1. **Email matching**: Primary method - matches committer email to Git author email
2. **Name matching**: Fallback - matches committer name to Git author name
3. **Activity calculation**: Determines time since last commit for each committer
4. **Color assignment**: Applies color based on activity windows

### 4. URL Validation

For each project's issue tracker URL:

```python
# Pseudo-code validation process
if url exists:
    try:
        response = httpx.get(url, follow_redirects=True, timeout=10.0)
        if response.status_code < 400:
            status = "valid"
        else:
            status = "broken"
            error = f"HTTP {response.status_code}"
    except:
        status = "broken"
        error = "Connection failed / Timeout"
```

## Report Output

### HTML Output

The report appears as a new section at the bottom of the HTML report:

```html
<h2>📋 Committer INFO.yaml Report</h2>
<h3>gerrit.onap.org</h3>
<table class="sortable">
  <thead>
    <tr>
      <th>Project</th>
      <th>Creation Date</th>
      <th>Lifecycle State</th>
      <th>Project Lead</th>
      <th>Committers</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><a href="https://jira.onap.org/projects/AAF">aaf-authz</a></td>
      <td>2017-07-12</td>
      <td>Incubation</td>
      <td>Jonathan Gathman</td>
      <td>
        <span style="color: green;" title="✅ Current">John Doe</span><br>
        <span style="color: orange;" title="☑️ Active">Jane Smith</span><br>
        <span style="color: red;" title="🛑 Inactive">Bob Jones</span>
      </td>
    </tr>
  </tbody>
</table>
```

### Tooltips

Hovering over colored committer names shows detailed status:

- ✅ **Current**: "commits within last 365 days"
- ☑️ **Active**: "commits between 365-1095 days"
- 🛑 **Inactive**: "no commits in 1095+ days"
- ❓ **Unknown**: "Unknown activity status"

Hovering over red project names shows:

- ⚠️ "Broken project issue-tracker link: [error message]"

## Use Cases

### 1. Committer Activity Audit

Identify projects with inactive committer lists:

- Filter table by lifecycle state
- Look for projects with mostly red (inactive) committers
- Consider committer list updates or project archival

### 2. Project Health Assessment

Evaluate project vitality:

- **Healthy**: Mostly green committers, valid issue tracker
- **Warning**: Mix of orange/red committers
- **Concern**: All red committers, broken issue tracker
- **Archived**: Lifecycle state = "Archived" with red committers

### 3. Onboarding New Contributors

Help new contributors find active projects:

- Look for projects with green committers
- Valid issue tracker links for reporting bugs
- Active lifecycle states (Incubation, Mature)

### 4. TSC/Project Lead Review

Support governance activities:

- Identify projects needing committer promotions
- Find stale committer lists that need updating
- Validate issue tracker links are current

### 5. Community Engagement Metrics

Track community health across Gerrit servers:

- Count active vs. inactive committers per server
- Identify servers with higher engagement
- Compare lifecycle states across organizations

## Troubleshooting

### Issue: No INFO.yaml data in report

**Causes:**

- `info_yaml.enabled` is `false` in configuration
- `local_path` doesn't exist and clone failed
- No INFO.yaml files found in repository

**Solutions:**

```yaml
# Verify configuration
info_yaml:
  enabled: true
  local_path: "testing/info-master"  # Verify this path exists
```

### Issue: All committers show as "Unknown" (gray)

**Causes:**

- Git repository data not matching INFO.yaml
- Email addresses don't match between INFO.yaml and Git commits
- Repository associations are incorrect

**Solutions:**

1. Check repository name matching in INFO.yaml
2. Verify committer emails in INFO.yaml match Git commits
3. Review Git data collection logs for errors

### Issue: Issue tracker validation failing

**Causes:**

- Network connectivity issues
- Firewall blocking outbound HTTPS
- JIRA/issue tracker temporarily down

**Solutions:**

```yaml
# Disable URL validation if needed
info_yaml:
  validate_urls: false
```

### Issue: Report generation slow

**Causes:**

- Large number of INFO.yaml files (1000+)
- URL validation for many projects
- Network latency

**Solutions:**

```yaml
# Speed up by disabling URL validation
info_yaml:
  validate_urls: false
  url_timeout: 5.0  # Reduce timeout
```

## Data Structure

### Internal Data Model

```python
{
    "project_name": "example-project",
    "gerrit_server": "gerrit.onap.org",
    "project_path": "example/project",
    "creation_date": "2020-01-15",
    "lifecycle_state": "Incubation",
    "project_lead": {
        "name": "John Doe",
        "email": "john.doe@example.com",
        "company": "Example Corp",
        "id": "johnd"
    },
    "committers": [
        {
            "name": "Jane Smith",
            "email": "jane.smith@example.com",
            "company": "Example Corp",
            "id": "janes",
            "activity_status": "current",
            "activity_color": "green"
        }
    ],
    "issue_tracking": {
        "type": "jira",
        "url": "https://jira.example.org/projects/PROJ"
    },
    "issue_tracker_valid": True,
    "issue_tracker_error": "",
    "has_git_data": True
}
```

## Future Enhancements

Potential improvements for future versions:

1. **Export to CSV**: Allow downloading committer data as spreadsheet
2. **Activity graphs**: Visual timeline of committer activity
3. **Committer ranking**: Sort by activity level across projects
4. **Change detection**: Compare INFO.yaml changes over time
5. **Email validation**: Verify committer email addresses are valid
6. **Company analytics**: Aggregate committer activity by company
7. **Automated alerts**: Notify project leads of inactive committers
8. **Integration with LFX**: Sync with LFX Insights data
9. **Historical trends**: Track committer activity changes over releases

## Related Documentation

- [Main README](../README.md) - Overall project documentation
- [Configuration Guide](../README.md#configuration) - Configuration system details
- [Report Sections](../README.md#report-sections) - All available report sections

## Support

For issues or questions about the INFO.yaml Committer Report:

1. Check this documentation
2. Review the configuration examples
3. Check logs for error messages
4. Open an issue on the project repository

---

**Last Updated**: 2025-01-XX
**Feature Version**: 1.0.0
**Minimum Required Script Version**: 1.0.0
