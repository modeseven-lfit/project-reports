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
```

### GitHub Actions Integration

```yaml
steps:
  - name: "Generate Repository Report"
    run: |
      python3 generate_reports.py \
        --project "${{ matrix.project }}" \
        --repos-path "./${{ matrix.server }}" \
        --config-dir "./configuration" \
        --output-dir "./reports" \
        --verbose
```

## ⚙️ Configuration

Project-specific configurations in `configuration/` directory:

```yaml
# Example: O-RAN-SC.config
project: "O-RAN-SC"
activity_threshold_days: 548
time_windows:
  last_30_days: 30
  last_365_days: 365
gerrit:
  enabled: true
  host: "gerrit.o-ran-sc.org"
html_tables:
  sortable: true
  searchable: true
```

## 📁 Output Structure

```text
reports/
  <PROJECT>/
    ├── report_raw.json           # Complete dataset
    ├── report.md                 # Markdown report
    ├── report.html               # Interactive HTML
    ├── config_resolved.json      # Applied configuration
    └── <PROJECT>_report_bundle.zip
```

## 🛠️ Dependencies

- Python 3.8+
- PyYAML
- httpx (for Gerrit API)

Install with:

```bash
pip install -r requirements.txt
```

## 📚 Documentation

- [**Workflow Integration Guide**](WORKFLOW_INTEGRATION.md) - CI/CD setup and
  configuration
- [**Workflow Setup Guide**](WORKFLOW_SETUP.md) - Getting started with
  GitHub Actions

## 🎯 Use Cases

- **Project Health Monitoring** - Track activity trends and contributor
  engagement
- **Resource Planning** - Identify active vs inactive repositories
- **Community Insights** - Understand contributor patterns and organizational
  involvement
- **Release Planning** - Analyze development velocity and feature adoption
- **Compliance Reporting** - Generate comprehensive project status reports
