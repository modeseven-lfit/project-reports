<!--
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 The Linux Foundation
-->

# Reporting Workflow Setup

This document describes the setup and usage of the GitHub CI/CD reporting
workflow for analyzing Linux Foundation projects hosted on Gerrit servers.

## Overview

The reporting workflow (`reporting.yaml`) provides:

1. **Run automatically** every Monday at 7:00 AM UTC
2. **Support manual execution** via GitHub's workflow dispatch
3. **Process projects** in parallel using matrix jobs
4. **Clone entire Gerrit servers** using the `gerrit-clone-action`
5. **Perform analytics** on the downloaded repositories
6. **Generate comprehensive reports** with results and summaries

## Configuration

### GitHub Secret: GERRIT_REPORTS_PAT_TOKEN

The workflow requires a GitHub Personal Access Token (PAT) to query workflow
status across all organizations.

**⚠️ IMPORTANT**: You **MUST use a Classic Personal Access Token**, not a
fine-grained token, because fine-grained tokens work with a single
organization and cannot span the Linux Foundation organizations
needed for cross-org reporting.

**Required Scopes**:

- `repo` (or at least `repo:status`)
- `actions:read`

For detailed instructions on creating and configuring the token, see:
**[GitHub Token Requirements Documentation](./GITHUB_TOKEN_REQUIREMENTS.md)**

#### Quick Setup

1. Go to <https://github.com/settings/tokens>
2. Click "Generate new token" → "Generate new token (classic)"
3. Select scopes: `repo` and `actions:read`
4. Generate and copy the token
5. Go to your repository's **Settings** → **Secrets and variables** → **Actions**
6. Click "New repository secret"
7. Name: `GERRIT_REPORTS_PAT_TOKEN`
8. Value: Paste your token
9. Click "Add secret"

### GitHub Variable: PROJECTS_JSON

The workflow requires a GitHub repository variable called `PROJECTS_JSON` that
contains an array of project configurations:

```json
[
  { "project": "O-RAN-SC", "server": "gerrit.o-ran-sc.org" },
  { "project": "ONAP", "server": "gerrit.onap.org" },
  { "project": "Opendaylight", "server": "git.opendaylight.org" }
]
```text
#### Setting up the Variable

1. Go to your repository's **Settings** → **Secrets and variables** →
   **Actions**
2. Click **Variables** tab
3. Click **New repository variable**
4. Name: `PROJECTS_JSON`
5. Value: The JSON array above (customize as needed)

## Workflow Structure

### Jobs

1. **verify**: Checks the `PROJECTS_JSON` configuration
2. **analyze**: Clones repositories and performs analysis
3. **summary**: Generates a final summary report/results

### Security Features

- **Hardened runners** using `step-security/harden-runner`
- **Minimal permissions** with explicit permission grants
- **Input validation** to prevent configuration errors

## Analysis Script

The workflow uses `scripts/analyze-repos.py`, which provides:

- Repository counting and validation
- Clone manifest processing
- Basic project information logging
- GitHub Step Summary integration
- JSON output generation

### Repository Structure Analysis

Before running the Python analysis script, the workflow automatically:

- **Lists directory structure** of cloned repositories
- **Calculates disk usage** using `du -sh` for the entire clone
- **Identifies largest repositories** (top 10 by size)
- **Provides summary statistics** including file/directory counts
- **Reports average repository size** across all cloned repos
- **Adds detailed breakdown** to GitHub Step Summary

This provides visibility into:

- Total data downloaded from each Gerrit server
- Storage requirements for the analysis
- Repository size distribution across projects
- Potential outliers or unusually large repositories

### Extending the Analysis

To add more analytics capabilities, update the `analyze-repos.py` script.
The script accepts these arguments:

- `--project`: Project name (e.g., "O-RAN-SC")
- `--server`: Gerrit server hostname
- `--repos-path`: Path to cloned repositories

## Artifacts

The workflow generates the following artifacts:

- **Clone manifests**: JSON files with repository clone results
- **Analysis outputs**: Per-project analysis results in JSON format
- **GitHub Step Summaries**: Rich markdown summaries including:
  - Repository structure analysis with disk usage
  - Clone statistics and largest repositories
  - Detailed analysis results from the Python script

Artifacts have a 30-day retention period and are available for download from
the workflow run page.

## Manual Execution

To run the workflow manually:

1. Go to **Actions** tab in your repository
2. Select **📊 Project Reports** workflow
3. Click **Run workflow**
4. Click **Run workflow** button (uses the current branch)

## Monitoring

The workflow includes comprehensive logging and error handling:

- **Input validation**: Ensures `PROJECTS_JSON` is properly formatted
- **Clone monitoring**: Tracks success/failure rates for repository cloning
- **Structure analysis**: Validates cloned data and provides disk usage metrics
- **Error reporting**: Clear error messages and status indicators
- **Timeout protection**: Jobs have reasonable timeout limits

## Customization

### Adding New Projects

1. Update the `PROJECTS_JSON` variable with the new project information
2. The workflow will automatically include the new project in the next run

### Modifying Clone Behavior

Edit the `gerrit-clone-action` parameters in the workflow:

- `threads`: Number of concurrent clone operations
- `clone-timeout`: Timeout per repository clone
- `skip-archived`: Whether to skip archived repositories
- `depth`: Create shallow clones with specified depth

### Changing the Schedule

Change the `cron` expression in the workflow file:

```yaml
schedule:
  # Run every Monday at 7:00 AM UTC
  - cron: '0 7 * * 1'
```text
## Troubleshooting

### Common Issues

1. **Missing PROJECTS_JSON**: Ensure the repository variable exists properly
2. **Invalid JSON**: Check JSON syntax using online tools
3. **Gerrit connectivity**: Check that Gerrit servers are accessible
4. **Permission errors**: Verify repository permissions and secrets
5. **Grey workflow status in reports**: Check `GERRIT_REPORTS_PAT_TOKEN` exists
   and is a Classic PAT with required scopes (see [GitHub Token Requirements](./GITHUB_TOKEN_REQUIREMENTS.md))

### Debugging

1. Check the workflow logs in the Actions tab
2. Review the GitHub Step Summary for detailed information
3. Download and examine the generated artifacts
4. Test the Python script locally with sample data
5. For GitHub API issues, see [GitHub API Error Logging](./GITHUB_API_ERROR_LOGGING.md)
6. For token configuration issues, see [GitHub Token Requirements](./GITHUB_TOKEN_REQUIREMENTS.md)

## Future Enhancements

Planned improvements include:

- **Advanced repository analysis** (code quality, activity metrics)
- **Historical tracking** and trend analysis
- **Custom reporting formats** (HTML, PDF)
- **Integration with external tools** and databases
- **Notification systems** for significant changes
