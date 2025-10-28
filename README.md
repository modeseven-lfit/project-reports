<!--
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 The Linux Foundation
-->

# 📊 Repository Reporting System

Comprehensive multi-repository analysis tool for Linux Foundation projects,
providing detailed insights into Git activity, contributor patterns, and
development practices.

## 🚀 Features

- **📈 Git Analytics**: Commit activity, lines of code, contributor metrics
  across configurable time windows
- **🔍 Feature Detection**: Automatic detection of CI/CD workflows,
  documentation, dependency management
- **👥 Contributor Intelligence**: Author and organization analysis with
  domain mapping
- **🏷️ Repository Classification**: Activity-based categorization and
  aging analysis
- **🌐 Gerrit API Integration**: Project metadata fetching for enhanced
  reporting
- **📊 Interactive HTML Tables**: Sortable, filterable, and searchable
  data tables
- **📋 Multi-Format Output**: JSON (data), Markdown (readable),
  HTML (interactive), ZIP (complete)

## 📖 Usage

### Command Line

```bash
python3 generate_reports.py \
  --project "O-RAN-SC" \
  --repos-path "./gerrit.o-ran-sc.org" \
  --config-dir "./configuration" \
  --output-dir "./reports" \
  --verbose
```text
### GitHub Actions Integration

```yaml
steps:
  - name: "Generate Repository Report"
    run: |
      python3 generate_reports.py \
        --project "${{ matrix.project }}" \
        --repos-path "./${{ matrix.gerrit }}" \
        --config-dir "./configuration" \
        --output-dir "./reports" \
        --verbose
```text
## ⚙️ Configuration

Project-specific configurations in `configuration/` directory:

```yaml
# Example: O-RAN-SC.config
project: "O-RAN-SC"
activity_thresholds:
  current_days: 365
  active_days: 1095
time_windows:
  last_30_days: 30
  last_365_days: 365
gerrit:
  enabled: true
  host: "gerrit.o-ran-sc.org"
html_tables:
  sortable: true
  searchable: true
```text
## 📁 Output Structure

```text
reports/
  <PROJECT>/
    ├── report_raw.json           # Complete dataset
    ├── report.md                 # Markdown report
    ├── report.html               # Interactive HTML
    ├── config_resolved.json      # Applied configuration
    └── <PROJECT>_report_bundle.zip
```text
## 📊 Activity Status System

The reporting system uses a unified three-tier activity classification:

- **✅ Current**: Gerrit projects with commits within the last 365 days
- **☑️ Active**: Gerrit projects with no commits between 365-1095 days
- **🛑 Inactive**: Gerrit projects with no commits in 1095+ days

### Configuration

Configure thresholds in your project config file:

```yaml
activity_thresholds:
  current_days: 365      # ✅ Current threshold
  active_days: 1095      # ☑️ Active threshold (365-1095 range)
  # Anything > 1095 days = 🛑 Inactive
```text
This unified system replaces the previous separate "activity status" and
"age categories" with a single, clear classification that appears consistently
across all reports and tables.

## 🛠️ Dependencies

- Python 3.8+
- PyYAML
- httpx (for Gerrit API)

Install with:

```bash
pip install -r requirements.txt
```text
## 📚 Documentation

### Setup & Configuration

- [**Setup Guide**](SETUP.md) - Complete workflow setup and
  configuration (includes setup for **two required tokens**)
- [**GitHub Token Requirements**](GITHUB_TOKEN_REQUIREMENTS.md) -
  Detailed guide for configuring Classic and Fine-grained PATs

### Troubleshooting & Debugging

- [**API Statistics**](API_STATISTICS.md) - Understanding external
  API call tracking and error reporting
- [**GitHub API Error Logging**](GITHUB_API_ERROR_LOGGING.md) -
  Debugging API authentication and permission issues

### Legacy Guides

- [**Workflow Integration Guide**](WORKFLOW_INTEGRATION.md) - CI/CD setup and
  configuration
- [**Workflow Setup Guide**](WORKFLOW_SETUP.md) - Getting started with
  GitHub Actions

## 🎯 Use Cases

- **Project Health Monitoring** - Track activity trends and contributor
  engagement
- **Resource Planning** - Identify current, active, and inactive repositories
- **Community Insights** - Understand contributor patterns and organizational
  involvement
- **Release Planning** - Analyze development velocity and feature adoption
- **Compliance Reporting** - Generate comprehensive project status reports
