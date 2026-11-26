#!/usr/bin/env bash
# Prove what the GitHub App's installation token (in GH_TOKEN) can do (issue #664, F3.1).
#
# Usage:
#   scripts/app-token-proof.sh --repo <owner/repo> [--pr <n>]
#
# Runs four calls and prints one line each on stdout, always in this order:
#   [APP PROOF] <call>: ok
#   [APP PROOF] <call>: refused <HTTP status> <message>
#   [APP PROOF] <call>: skipped <reason>      (review-requests only)
# Calls: board-read, board-write, branch-protection, review-requests.
# board-write writes the project's own shortDescription back (never a Status write).
# Diagnostics go to stderr; neither stream holds GH_TOKEN (it is masked before printing).
#
# Exit codes:
#   0  The four calls ran, whatever each answered.
#   2  Arguments were invalid.

# No `set -e`: a refused call is a result, not a failure.
set -uo pipefail

usage() {
    echo "Usage: app-token-proof.sh --repo <owner/repo> [--pr <n>]" >&2
}

repo=""
pr=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            repo="$2"
            shift 2
            ;;
        --pr)
            [[ $# -ge 2 ]] || { usage; exit 2; }
            pr="$2"
            shift 2
            ;;
        *)
            usage
            exit 2
            ;;
    esac
done

if [[ ! "$repo" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
    usage
    exit 2
fi
if [[ -n "$pr" && ! "$pr" =~ ^[0-9]+$ ]]; then
    usage
    exit 2
fi

owner="${repo%%/*}"
name="${repo#*/}"

errfile="$(mktemp)"
trap 'rm -f "$errfile"' EXIT

# The same query as BOARD_ITEMS_QUERY in src/headless_pr_workflow/dispatcher/gateway.py
# (tests/test_app_token_proof.py keeps the two equal).
BOARD_QUERY='query($owner: String!, $repo: String!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    projectsV2(first: 1) {
      nodes {
        id
        fields(first: 30) {
          nodes { ... on ProjectV2SingleSelectField { id name options { id name } } }
        }
        items(first: 100, after: $cursor) {
          pageInfo { hasNextPage endCursor }
          nodes {
            id
            content { __typename ... on Issue { number state repository { nameWithOwner } labels(first: 50) { nodes { name } } } }
            fieldValueByName(name: "Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
          }
        }
      }
    }
  }
}'

DESCRIPTION_QUERY='query($owner: String!, $repo: String!) {
  repository(owner: $owner, name: $repo) {
    projectsV2(first: 1) { nodes { id shortDescription } }
  }
}'

DESCRIPTION_MUTATION='mutation($projectId: ID!, $shortDescription: String!) {
  updateProjectV2(input: { projectId: $projectId, shortDescription: $shortDescription }) {
    projectV2 { id }
  }
}'

# Set by call_gh: the answer of the last gh call.
out=""
err=""
rc=0

call_gh() {
    out="$(gh "$@" 2>"$errfile")"
    rc=$?
    err="$(<"$errfile")"
}

# Print `<text>` without the token, on one line, at most 200 characters.
clean() {
    local text="$1"
    text="${text%%$'\n'*}"
    text="${text//$'\r'/}"
    if [[ -n "${GH_TOKEN:-}" ]]; then
        text="${text//"$GH_TOKEN"/***}"
    fi
    printf '%s' "${text:0:200}"
}

report() {
    printf '[APP PROOF] %s: %s\n' "$1" "$2"
}

# `refused <status> <message>` from the last call_gh answer.
refused() {
    local call="$1" line status message
    line="$(printf '%s\n' "$err" | sed -n '/[^[:space:]]/{p;q;}')"
    line="${line#gh: }"
    if [[ "$line" =~ \(HTTP\ ([0-9]{3})\) ]]; then
        status="${BASH_REMATCH[1]}"
        message="${line/ (HTTP $status)/}"
    else
        message="$(printf '%s' "$out" | jq -r '.errors[0].message // empty' 2>/dev/null | sed -n '1p')"
        if [[ -n "$message" ]]; then
            status="200"
        else
            status="unknown"
            message="$line"
        fi
    fi
    [[ -n "$message" ]] || message="no message"
    report "$call" "refused $status $(clean "$message")"
}

# A GraphQL answer under HTTP 200 may carry `errors`; a refusal then.
graphql_failed() {
    [[ $rc -ne 0 ]] && return 0
    printf '%s' "$out" | jq -e '(.errors // []) | length > 0' >/dev/null 2>&1
}

board_read() {
    call_gh api graphql -f "query=$BOARD_QUERY" -f "owner=$owner" -f "repo=$name"
    if graphql_failed; then
        refused board-read
    elif printf '%s' "$out" | jq -e '.data.repository.projectsV2.nodes[0].id' >/dev/null 2>&1; then
        report board-read ok
    else
        report board-read "refused 200 no Projects v2 board is visible on $repo"
    fi
}

board_write() {
    call_gh api graphql -f "query=$DESCRIPTION_QUERY" -f "owner=$owner" -f "repo=$name"
    if graphql_failed; then
        refused board-write
        return
    fi
    local project_id description
    project_id="$(printf '%s' "$out" | jq -r '.data.repository.projectsV2.nodes[0].id // empty' 2>/dev/null)"
    if [[ -z "$project_id" ]]; then
        report board-write "refused 200 no Projects v2 board is visible on $repo"
        return
    fi
    # A null description is written back as the empty string.
    description="$(printf '%s' "$out" | jq -r '.data.repository.projectsV2.nodes[0].shortDescription // ""' 2>/dev/null)"
    call_gh api graphql -f "query=$DESCRIPTION_MUTATION" -f "projectId=$project_id" -f "shortDescription=$description"
    if graphql_failed; then
        refused board-write
    else
        report board-write ok
    fi
}

branch_protection() {
    call_gh api "repos/$repo/branches/main/protection"
    if [[ $rc -ne 0 ]]; then
        refused branch-protection
    else
        report branch-protection ok
    fi
}

review_requests() {
    local number="$pr"
    if [[ -z "$number" ]]; then
        call_gh pr list --repo "$repo" --state open --limit 1 --json number --jq '.[0].number // empty'
        if [[ $rc -ne 0 ]]; then
            refused review-requests
            return
        fi
        number="$out"
        if [[ ! "$number" =~ ^[0-9]+$ ]]; then
            report review-requests "skipped no open PR in $repo and no --pr given"
            return
        fi
    fi
    call_gh pr view "$number" --repo "$repo" --json reviewRequests
    if [[ $rc -ne 0 ]]; then
        refused review-requests
    else
        report review-requests ok
    fi
}

board_read
board_write
branch_protection
review_requests
exit 0
