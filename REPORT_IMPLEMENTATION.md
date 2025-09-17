# REPORT_IMPLEMENTATION.md

## Overview

This document describes the six-phase implementation plan for a comprehensive repository reporting system that:

1. Collects rich Git & repository metadata.
2. Produces a machine-readable raw JSON dataset.
3. Generates a distilled Markdown report.
4. Generates an HTML rendering of the Markdown.
5. Bundles all artifacts (JSON, Markdown, HTML, resolved configuration) into a ZIP archive for download from a GitHub workflow run.
6. Supports configuration-driven customization (global template + per-project overrides) while keeping a **single Python script** (`generate_reports.py`) as the source of truth.

The system must be extensible, performance-conscious, and resilient to partial failures (e.g., inaccessible repositories).

---

## High-Level Principles

- **Single Script, Modular Internals:** One Python file with clearly separated functional regions (configuration, git data extraction, feature scanning, aggregation, rendering, packaging).
- **JSON as Canonical Source:** Markdown and HTML are “views” over the full JSON dataset. All possible metrics are included in the JSON, even if hidden in views.
- **Configuration-Driven Output:** Global `template.config` plus optional `<PROJECT>.config` overrides (deep merge).
- **Extensibility Hooks:** Pluggable feature detection registry; easy to add metrics/time windows/sections without refactoring.
- **Deterministic & Ordered:** All tables sorted by descending metric (ties broken consistently by name).
- **Graceful Degradation:** Errors logged per-repository; do not fail the entire run unless catastrophic.
- **Portability:** Depend on `git` CLI & lightweight Python libs (YAML, markdown). Avoid heavy frameworks.
- **Performance:** Single pass per repo for commit/LOC stats covering all time windows. Concurrency for repo iteration.

---

## Phase 1: Foundations, Architecture & Schema

### Goals

Define:

- Configuration model & merge strategy.
- JSON schema shape.
- Internal script structure.
- Time windows and default thresholds.
- Key metrics & naming conventions.

### Initial Metrics

| Category | Metrics |
|----------|---------|
| Repository | Name/path, last commit timestamp, days since last commit, active flag, commit counts per time window, LOC (added/removed/net) per time window, unique contributors per window, feature flags (presence), workflow classification counts, project type detection |
| Contributor | Name, email, derived username, domain, commits per window, LOC per window, repositories touched per window |
| Organization (by email domain) | Domain, contributor count, aggregated commits & LOC per window |
| Global Summary | Totals, active/inactive counts, bucketed inactivity ages, top/bottom repo lists, top contributors & organizations |

### Time Windows (default)

- `last_30_days` (30)
- `last_90_days` (90)
- `last_365_days` (365)
- `last_3_years` (1095)
(Extendable via config.)

### Activity / Age Thresholds

- Inactive if `days_since_last_commit > activity_threshold_days` (default 365).
- Age buckets:
  - Very old: `> very_old_years` (default 3)
  - Old: `> old_years` and `<= very_old_years` (default 1–3)
  - Recent inactive: `<= old_years` but still inactive.

### JSON Schema (Conceptual Excerpt)

```
{
  "schema_version": "1.0.0",
  "generated_at": "UTC_ISO8601",
  "project": "string",
  "config_digest": "sha256:...",
  "time_windows": {
    "last_30_days": {"days": 30, "start": "...", "end": "..."},
    ...
  },
  "repositories": [ { ... per repo metrics ... } ],
  "authors": [ { ... per contributor metrics ... } ],
  "organizations": [ { ... per domain metrics ... } ],
  "summaries": {
    "counts": {...},
    "activity_distribution": {...},
    "top_active_repositories": [...],
    "least_active_repositories": [...],
    "top_contributors_commits": [...],
    "top_contributors_loc": [...],
    "top_organizations": [...]
  },
  "errors": [ { "repo": "...", "error": "message" } ]
}
```

### Config File (YAML) – Example Keys

```
project: default
output:
  top_n_repos: 30
  bottom_n_repos: 30
  include_sections:
    contributors: true
    organizations: true
    repo_feature_matrix: true
    inactive_distributions: true
time_windows:
  last_30_days: 30
  last_90_days: 90
  last_365_days: 365
  last_3_years: 1095
activity_threshold_days: 365
age_buckets:
  very_old_years: 3
  old_years: 1
features:
  enabled:
    - dependabot
    - github2gerrit_workflow
    - pre_commit
    - readthedocs
    - sonatype_config
    - project_types
    - workflows
workflows:
  classify:
    verify: ["verify"]
    merge: ["merge"]
performance:
  max_workers: 8
  cache: false
render:
  show_net_lines: true
  show_added_removed: false
  emoji:
    active: "✅"
    inactive: "⚠️"
    missing: "❌"
```

### Deliverables

- `configuration/template.config` file.
- Architecture notes section in code header.
- Final list of metrics & naming conventions.

---

## Phase 2: Core Git Data Extraction

### Goals

Efficiently extract commit and LOC metrics per time window for each repository.

### Approach

- Use `git log --numstat --date=iso --pretty=...` for a unified traversal.
- Single pass filtering commits into all time windows (compare commit date vs boundary starts).
- Collect:
  - Commit timestamps
  - Author Name/Email
  - Added/removed lines (numeric)
- Store intermediate aggregated counters; do NOT retain full raw commit list unless configured.

### Author Normalization

- Email → lowercase.
- Username heuristic: local-part before `@` (configurable mapping extension later).
- Skip / mark empty or malformed emails.

### Concurrency

- Thread pool for repos; `max_workers` from config.
- Each repo function returns structured metrics or error descriptor.

### Caching (Optional)

- If enabled:
  - Compute HEAD commit hash.
  - If unchanged and cache file exists, reuse previous aggregated metrics.

### Deliverables

- Functions (inside single script):
  - `collect_repo_git_metrics(repo_path, time_windows) -> dict`
  - `bucket_commit_into_windows(commit_datetime, windows)`
  - `aggregate_author_stats(repo_metrics)`
- Data structures for intermediate author accumulation.

### Best Practices

- Use UTC consistently.
- Defensive parsing for numstat (skip binary markers `-`).
- Record error with repo name, do not raise unhandled exceptions.

---

## Phase 3: Repository Content & Feature Scanning

### Goals

Detect structural/project features and presence of standard config files.

### Feature Checks

| Feature | Detection Logic |
|---------|-----------------|
| dependabot | `.github/dependabot.yml|yaml` |
| github2gerrit_workflow | Any workflow file containing name substring or filename pattern (configurable) |
| pre_commit | `.pre-commit-config.yaml` |
| readthedocs | `readthedocs.yml|yaml` OR `docs/conf.py` (Sphinx heuristic) |
| sonatype_config | Placeholder set: `.sonatype-lift.yaml`, `lift.toml`, `lifecycle.json` (config-extensible) |
| project_types | Heuristics: Maven (`pom.xml`), Gradle (`build.gradle*`), Node (`package.json`), Python (`pyproject.toml`, `requirements.txt`, `setup.py`, `Pipfile`), Docker (`Dockerfile` root), Go (`go.mod`) |
| workflows | Count `.github/workflows/*.yml|yaml`classify by substrings (`verify`,`merge`, else other) |

### Extensibility

- Registry pattern:
  - List of tuples `(key, function)`
  - Each function returns a normalized result.
- Configuration key `features.enabled` filters which checks run.

### Deliverables

- `FEATURE_CHECKS` registry.
- Scan functions:
  - `scan_dependabot(repo_root)`
  - `scan_workflows(repo_root, classification_cfg)`
  - etc.
- Merged feature dict per repository.

### Best Practices

- Avoid reading entire large workflow files—grep small header or open once.
- Abstract filesystem queries behind helper functions for future caching.
- Keep detection logic stateless/pure.

---

## Phase 4: Aggregation, Ranking & Transformation

### Goals

Assemble all repo-level and author-level raw data into global summary structures and sorted leaderboards.

### Steps

1. Active/inactive classification using `activity_threshold_days`.
2. Build repo arrays for:
   - Top N by commits (365 days, or primary window from config).
   - Least active (inactive sorted by descending days since last commit).
3. Authors:
   - Merge per-repo author aggregates into global keyed by email.
   - For each author accumulate:
     - Commits per window
     - LOC per window
     - Set of repositories touched (size per window).
4. Organizations:
   - Group authors by domain.
   - Sum commits & LOC per window.
   - Count distinct contributors.
5. Age distribution buckets (very old / old / recent inactive).
6. Prepare canonical sorted lists for:
   - `top_contributors_commits`
   - `top_contributors_loc` (net lines)
   - `top_organizations`
7. Build repository feature matrix rows (ordered by chosen primary metric, fallback alphabetical).

### Sorting Conventions

- Primary descending metric (commits, lines).
- Secondary alphabetical by name for stability.
- Numeric comparisons only; no locale-specific formatting until rendering.

### Deliverables

- Functions:
  - `aggregate_global(repos, config)`
  - `compute_author_rollups(repos)`
  - `compute_org_rollups(authors)`
  - `rank_entities(...)`
  - `build_activity_distributions(...)`
- Completely filled JSON structure (before rendering).

### Best Practices

- Do not mutate original repo records during aggregation—derive new structures.
- Preserve numeric fidelity (no formatting).
- Include counts even if zero (avoid missing-key ambiguity).

---

## Phase 5: Output Generation (JSON, Markdown, HTML, ZIP)

### JSON Output

- File: `report_raw.json`
- Include `schema_version`, `generated_at`, `config_digest`.

### Markdown Sections (Config-Conditional)

1. Title & Metadata
2. Global Summary
3. Activity Distribution
4. Top Active Repositories
5. Least Active Repositories
6. Contributor Leaderboards (commits / LOC)
7. Organization Leaderboard
8. Repository Feature Matrix
9. Appendix: Configuration digest / run parameters (optional)

### Markdown Table Guidelines

- Pipe tables.
- Headers consistent.
- Large numbers optionally abbreviated (render layer only) e.g., 12_345 → `12.3k` (configurable).
- Emoji indicators:
  - Presence ✅ / missing ❌
  - Activity states (e.g., ⚠️ for inactive, 🔴 for very old optionally).

### HTML Generation

- Convert Markdown to HTML (library or minimal custom).
- Embed inline CSS (monospace for tables optional).
- Add anchor links (`id` attributes for headings).
- Ensure UTF-8 meta tag.

### ZIP Packaging

Structure inside ZIP:

```
reports/
  <project>/
    report_raw.json
    report.md
    report.html
    config_resolved.json
    (optional) logs.txt
```

Optional date stamping: `report_YYYYMMDD.md`.

### CLI Arguments

- `--project <name>`
- `--config-dir <path>`
- `--output-dir <path>`
- `--no-html`
- `--no-zip`
- `--verbose`
- `--cache`
- `--store-raw-commits` (future toggle)
- `--limit-top-n <int>` (override config)

### Deliverables

- Rendering functions:
  - `render_markdown(data, config)`
  - `render_html(markdown_str, config)`
  - `write_json(path, data)`
  - `package_zip(output_dir, project)`
- GitHub Actions example snippet (documented here for later integration).

### Best Practices

- Separate formatting utilities (e.g., `fmt_number`, `fmt_age`).
- Keep HTML semantic (use `<table>`, `<thead>`, `<tbody>`).
- Add HTML comment with build metadata at top.

---

## Phase 6: CI Workflow, Validation, Extensibility & Hardening

### GitHub Workflow

- Triggers:
  - `workflow_dispatch`
  - Scheduled (e.g., weekly `cron: "0 5 * * 1"`).
- Steps:
  1. Checkout
  2. Setup Python
  3. Install dependencies
  4. Run script
  5. Upload artifact ZIP

### Validation

- Optional JSON Schema file + validation step (future).
- `--validate-only` mode loads config & prints summary then exits.
- Check for missing required keys, log warnings.

### Logging

- Provide `--log-level` (INFO/DEBUG).
- Structured prefix `[LEVEL][timestamp] message`.
- Optionally log per repo start/end to measure slow spots.

### Error Handling

- Per repo:
  - Append to `errors` list in JSON with message & category.
- Continue on failure; aggregate totals exclude failed repos but record counts.

### Extensibility Hooks

- Feature detection registry: Append new functions without modifying core loops.
- Time windows: Add in config; dynamic iteration rather than hard-coded.
- Additional scanning categories: Security, dependency freshness, open issues (future; would require API tokens).
- Anonymization mode (strip emails) for compliance if needed.

### Documentation

- `REPORTING.md` (future doc) describing:
  - Adding new feature checks.
  - Schema field semantics.
  - Example config overrides.

### Future Enhancements (Deferred)

- GitHub API integration (PR count, issue activity).
- Language breakdown via `linguist` or `cloc`.
- Commit message quality heuristics.
- Trend charts (embed ASCII or simple SVG in HTML).

### Deliverables

- Working GitHub Action YAML (reference template).
- Logging refinements.
- Documentation for adding new checks.
- Optional schema validator stub.

---

## Internal Script Structure (Single File Layout)

Suggested section ordering with large banner comments:

1. Imports & Constants
2. Configuration Loading & Deep Merge
3. Time Window Computation
4. Git Data Collection
5. Feature Scanning & Registry
6. Aggregation (Repositories → Authors → Orgs → Summaries)
7. Rendering (Markdown / HTML)
8. Packaging (ZIP) & CLI Entry Point
9. Utility Functions (formatting, hashing, error helpers)

---

## Data Integrity & Quality Considerations

| Concern | Mitigation |
|---------|------------|
| Missing emails | Assign `unknown@unknown` & exclude from domain grouping |
| Duplicate identities (same person multiple emails) | Future: configurable alias map |
| Large repos slow traversal | Consider limiting to max years; caching HEAD hash |
| Binary file diff noise | Ignore lines where `numstat` shows `-` |
| Non-standard workflows | Make substring classification configurable |
| Extremely long repo names in tables | Truncate with ellipsis in render phase only |

---

## Testing Strategy (Pragmatic Initial Pass)

- Synthetic minimal repo (1 commit) test.
- Repo with zero commits (initialize but no commits).
- Repo with multiple authors & time window edges.
- Feature presence matrix test with stub files.
- Markdown generation smoke test (validate table alignment).
- HTML generation test (basic tag presence).

---

## Example CLI Usage

```
python generate_reports.py \
  --project onap \
  --config-dir configuration \
  --output-dir reports/onap \
  --verbose
```

Result:

```
reports/onap/
  report_raw.json
  report.md
  report.html
  config_resolved.json
  report_bundle.zip
```

---

## Performance Notes

- Main cost: `git log`.
- Optimization path:
  - Use largest window start boundary to set `--since`.
  - Single pass parse with streaming.
  - Consider optional parallelism control in config.

---

## Security & Privacy

- Avoid embedding raw emails in rendered public HTML if flagged to anonymize.
- Add config: `privacy.mask_emails: false|true`.
- If true:
  - Display hashed form (e.g., first 6 chars of SHA256 of email).
  - Keep full email only in raw JSON if allowed.

---

## Completion Criteria

- All six phases fully implemented.
- `generate_reports.py` runs end-to-end producing valid JSON, Markdown, and HTML for a known repository set.
- At least one project config override tested.
- ZIP artifact contains all expected files.
- Tables sorted correctly & reproducibly.
- Error list populated for intentionally broken test repo.

---

## Summary

This plan incrementally builds a robust reporting system with strong attention to:

- Data richness
- Extensibility
- Reproducibility
- Configurability
- Operational quality in CI

With the foundations in place, future enhancements (API-driven metrics, security scans, language stats) can be layered without restructuring the core architecture.

---

If you’d like the next step to be a scaffold of the actual `generate_reports.py` with placeholder sections and TODO markers, I can produce that immediately. Just let me know.
