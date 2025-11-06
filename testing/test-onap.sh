#!/bin/bash

# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2025 The Linux Foundation

# Test script to generate ONAP report locally for development/testing
# This script runs the report generator on the ONAP test data

set -e  # Exit on any error

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Configuration
PROJECT_NAME="ONAP"
REPOS_PATH="$SCRIPT_DIR/ONAP"
CONFIG_DIR="$PROJECT_ROOT/configuration"
OUTPUT_DIR="$SCRIPT_DIR/reports"
GENERATE_SCRIPT="$PROJECT_ROOT/generate_reports.py"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if required environment variables are set
check_tokens() {
    log_info "Checking required GitHub tokens..."

    # Try to source token from testing/github_token file if it exists
    local token_file="$SCRIPT_DIR/github_token"
    if [[ -f "$token_file" ]]; then
        log_info "Found token file at $token_file"
        # shellcheck source=/dev/null
        source "$token_file"
        log_success "Sourced tokens from $token_file"
    fi

    local missing_tokens=false

    if [[ -z "${CLASSIC_READ_ONLY_PAT_TOKEN:-}" ]]; then
        log_error "CLASSIC_READ_ONLY_PAT_TOKEN environment variable is not set"
        log_error "This token is required for GitHub API queries (workflow status, repo metadata)"
        missing_tokens=true
    else
        log_success "CLASSIC_READ_ONLY_PAT_TOKEN is set"
    fi

    if [[ -z "${GERRIT_REPORTS_PAT_TOKEN:-}" ]]; then
        log_error "GERRIT_REPORTS_PAT_TOKEN environment variable is not set"
        log_error "This token is required for publishing reports to gerrit-reports repository"
        missing_tokens=true
    else
        log_success "GERRIT_REPORTS_PAT_TOKEN is set"
    fi

    if [[ "$missing_tokens" == true ]]; then
        echo
        log_error "Missing required GitHub tokens!"
        echo
        echo "Please set the following environment variables before running this script:"
        echo
        echo "Option 1: Create a token file (recommended for local development):"
        echo "  echo 'export CLASSIC_READ_ONLY_PAT_TOKEN=\"your-token\"' > testing/github_token"
        echo "  echo 'export GERRIT_REPORTS_PAT_TOKEN=\"your-token\"' >> testing/github_token"
        echo
        echo "Option 2: Set environment variables directly:"
        echo "  export CLASSIC_READ_ONLY_PAT_TOKEN='your-classic-pat-token'"
        echo "  export GERRIT_REPORTS_PAT_TOKEN='your-fine-grained-pat-token'"
        echo
        echo "For more information, see:"
        echo "  - SETUP.md"
        echo "  - TWO_TOKEN_SETUP.md"
        echo
        exit 1
    fi

    log_success "All required tokens are set"
}

# Check if Python 3 is available
check_python() {
    if ! command -v python3 &> /dev/null; then
        log_error "python3 is not installed or not in PATH"
        exit 1
    fi

    log_info "Using Python: $(python3 --version)"
}

# Check if required files exist
check_files() {
    log_info "Checking required files..."

    if [[ ! -f "$GENERATE_SCRIPT" ]]; then
        log_error "generate_reports.py not found at: $GENERATE_SCRIPT"
        exit 1
    fi

    if [[ ! -d "$CONFIG_DIR" ]]; then
        log_error "Configuration directory not found at: $CONFIG_DIR"
        exit 1
    fi

    if [[ ! -d "$REPOS_PATH" ]]; then
        log_warning "ONAP repository directory not found at: $REPOS_PATH"
        log_warning "Creating empty directory for testing..."
        mkdir -p "$REPOS_PATH"
    fi

    log_success "All required files found"
}

# Install Python dependencies if needed
install_dependencies() {
    if [[ -f "$PROJECT_ROOT/requirements.txt" ]]; then
        log_info "Installing Python dependencies..."

        # Check if we're in a virtual environment
        if [[ -z "${VIRTUAL_ENV:-}" ]]; then
            # Check if project has a venv directory
            if [[ -d "$PROJECT_ROOT/venv" ]]; then
                log_info "Activating existing virtual environment..."
                # shellcheck source=/dev/null
                source "$PROJECT_ROOT/venv/bin/activate"
            else
                log_info "Creating new virtual environment..."
                python3 -m venv "$PROJECT_ROOT/venv"
                # shellcheck source=/dev/null
                source "$PROJECT_ROOT/venv/bin/activate"
            fi
        fi

        # Install dependencies in the virtual environment
        python3 -m pip install -r "$PROJECT_ROOT/requirements.txt" --quiet
        log_success "Dependencies installed in virtual environment"
    else
        log_warning "No requirements.txt found, skipping dependency installation"
    fi
}

# Clean previous output
clean_output() {
    if [[ -d "$OUTPUT_DIR" ]]; then
        log_info "Cleaning previous output directory..."
        rm -rf "$OUTPUT_DIR"
    fi
    mkdir -p "$OUTPUT_DIR"
}

# Run the report generator
run_generator() {
    log_info "Starting ONAP report generation..."
    log_info "Project: $PROJECT_NAME"
    log_info "Repos path: $REPOS_PATH"
    log_info "Config dir: $CONFIG_DIR"
    log_info "Output dir: $OUTPUT_DIR"
    echo

    # Ensure we're using the right Python environment
    if [[ -n "${VIRTUAL_ENV:-}" ]]; then
        log_info "Using virtual environment: $VIRTUAL_ENV"
    elif [[ -d "$PROJECT_ROOT/venv" ]]; then
        log_info "Activating virtual environment..."
        # shellcheck source=/dev/null
        source "$PROJECT_ROOT/venv/bin/activate"
    fi

    # Run with verbose output
    python3 "$GENERATE_SCRIPT" \
        --project "$PROJECT_NAME" \
        --repos-path "$REPOS_PATH" \
        --config-dir "$CONFIG_DIR" \
        --output-dir "$OUTPUT_DIR" \
        --verbose

    local exit_code=$?

    if [[ $exit_code -eq 0 ]]; then
        log_success "Report generation completed successfully!"
        return 0
    else
        log_error "Report generation failed with exit code: $exit_code"
        return $exit_code
    fi
}

# Display results
show_results() {
    log_info "Generated files:"

    if [[ -d "$OUTPUT_DIR/$PROJECT_NAME" ]]; then
        find "$OUTPUT_DIR/$PROJECT_NAME" -type f | while read -r file; do
            local size
            local relative_path
            size=$(stat -f%z "$file" 2>/dev/null || stat -c%s "$file" 2>/dev/null || echo "unknown")
            relative_path=${file#"$OUTPUT_DIR/"}
            echo "  $relative_path (${size} bytes)"
        done

        echo
        log_success "Report files are available in: $OUTPUT_DIR/$PROJECT_NAME/"

        # Check for HTML report
        if [[ -f "$OUTPUT_DIR/$PROJECT_NAME/report.html" ]]; then
            log_info "To view the HTML report, open: $OUTPUT_DIR/$PROJECT_NAME/report.html"
        fi

        # Check for ZIP bundle
        if [[ -f "$OUTPUT_DIR/$PROJECT_NAME/${PROJECT_NAME}_report_bundle.zip" ]]; then
            log_info "Complete report bundle: $OUTPUT_DIR/$PROJECT_NAME/${PROJECT_NAME}_report_bundle.zip"
        fi
    else
        log_warning "No output directory found at: $OUTPUT_DIR/$PROJECT_NAME"
    fi
}

# Help function
show_help() {
    echo "Usage: $0 [OPTIONS]"
    echo
    echo "Generate ONAP project report for local testing/development"
    echo
    echo "Options:"
    echo "  -h, --help     Show this help message"
    echo "  -c, --clean    Only clean output directory and exit"
    echo "  -v, --validate Only validate configuration and exit"
    echo "  --no-deps      Skip dependency installation"
    echo
    echo "Required Environment Variables:"
    echo "  CLASSIC_READ_ONLY_PAT_TOKEN    Classic PAT for GitHub API queries"
    echo "  GERRIT_REPORTS_PAT_TOKEN       Fine-grained PAT for report publishing"
    echo
    echo "Optional Environment Variables:"
    echo "  REPOS_PATH     Override default repository path"
    echo "  OUTPUT_DIR     Override default output directory"
    echo
    echo "Examples:"
    echo "  export CLASSIC_READ_ONLY_PAT_TOKEN='ghp_xxx...'"
    echo "  export GERRIT_REPORTS_PAT_TOKEN='github_pat_xxx...'"
    echo "  $0                    # Generate report with default settings"
    echo "  $0 --clean           # Clean output directory only"
    echo "  $0 --validate        # Validate configuration only"
    echo "  REPOS_PATH=/path/to/repos $0  # Use custom repos path"
}

# Parse command line arguments
CLEAN_ONLY=false
VALIDATE_ONLY=false
SKIP_DEPS=false

while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            show_help
            exit 0
            ;;
        -c|--clean)
            CLEAN_ONLY=true
            shift
            ;;
        -v|--validate)
            VALIDATE_ONLY=true
            shift
            ;;
        --no-deps)
            SKIP_DEPS=true
            shift
            ;;
        *)
            log_error "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Override paths from environment if set
if [[ -n "${REPOS_PATH_ENV:-}" ]]; then
    REPOS_PATH="$REPOS_PATH_ENV"
fi

if [[ -n "${OUTPUT_DIR_ENV:-}" ]]; then
    OUTPUT_DIR="$OUTPUT_DIR_ENV"
fi

# Main execution
main() {
    log_info "ONAP Report Generator Test Script"
    log_info "================================="
    echo

    # Always check tokens first
    check_tokens

    # Then check Python and files
    check_python
    check_files

    # Handle special modes
    if [[ "$CLEAN_ONLY" == true ]]; then
        clean_output
        log_success "Output directory cleaned"
        exit 0
    fi

    if [[ "$VALIDATE_ONLY" == true ]]; then
        log_info "Validating configuration..."

        # Ensure we have dependencies for validation
        if [[ "$SKIP_DEPS" != true ]]; then
            install_dependencies
        fi

        python3 "$GENERATE_SCRIPT" \
            --project "$PROJECT_NAME" \
            --repos-path "$REPOS_PATH" \
            --config-dir "$CONFIG_DIR" \
            --output-dir "$OUTPUT_DIR" \
            --validate-only
        exit $?
    fi

    # Normal execution
    if [[ "$SKIP_DEPS" != true ]]; then
        install_dependencies
    fi

    clean_output

    if run_generator; then
        echo
        show_results
        echo
        log_success "Test completed successfully!"
        log_info "You can now iterate on code changes and re-run this script for immediate feedback."
    else
        echo
        log_error "Test failed!"
        exit 1
    fi
}

# Execute main function
main "$@"
