#!/usr/bin/env bash
# Extract Markdown checklist lines from an issue's Acceptance Criteria and
# Definition of Done sections without modifying the issue.
#
# Usage:
#   scripts/ac-summary.sh --issue <N> [--repo <owner/repo>]
#
# Output:
#   Matching checklist lines are written unchanged to stdout. Diagnostics and
#   usage errors are written to stderr.
#
# Exit codes:
#   0  One or more checklist lines were emitted.
#   1  Arguments were invalid or `gh issue view` failed.
#   2  The issue was read successfully, but no matching checklist was found.

set -euo pipefail

usage() {
    cat >&2 <<'USAGE'
Usage: ac-summary.sh --issue <N> [--repo <owner/repo>]
USAGE
}

issue=""
repo=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --issue)
            if [[ -n "$issue" || $# -lt 2 || -z "$2" ]]; then
                usage
                exit 1
            fi
            issue="$2"
            shift 2
            ;;
        --repo)
            if [[ -n "$repo" || $# -lt 2 || -z "$2" ]]; then
                usage
                exit 1
            fi
            repo="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'Error: unknown argument: %s\n' "$1" >&2
            usage
            exit 1
            ;;
    esac
done

if [[ ! "$issue" =~ ^[1-9][0-9]*$ ]]; then
    printf 'Error: --issue must be a positive integer.\n' >&2
    usage
    exit 1
fi

command=(gh issue view "$issue")
if [[ -n "$repo" ]]; then
    command+=(--repo "$repo")
fi
command+=(--json body --jq .body)

if ! issue_body=$("${command[@]}"); then
    printf 'Error: failed to read issue #%s.\n' "$issue" >&2
    exit 1
fi

printf '%s\n' "$issue_body" | awk '
    /^##([[:space:]]|$)/ {
        in_target = ($0 == "## Acceptance Criteria" || $0 == "## Definition of Done")
        next
    }
    in_target && /^[[:space:]]*- \[[ xX]\]/ {
        print
        found = 1
    }
    END {
        if (!found) {
            exit 2
        }
    }
'
