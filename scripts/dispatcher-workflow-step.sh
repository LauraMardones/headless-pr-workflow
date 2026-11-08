#!/usr/bin/env bash
# scripts/dispatcher-workflow-step.sh
#
# The decisions of the steps of .github/workflows/dispatcher.yml (issue #434,
# ADR-014 → Block 3, #368 → F6). Every `run:` of the workflow is one call to a
# script; this is the script for the steps that used to hold shell logic, so
# pytest reaches it. Step names, order, `if:` conditions and cache keys stay in
# the workflow.
#
# Usage: bash scripts/dispatcher-workflow-step.sh <step>
#
#   guard               DISPATCHER_ENABLED=true → output enabled=true, else enabled=false
#   scope               the state-writer rule: EVENT_NAME=schedule, or REF is the
#                       default branch (DEFAULT_BRANCH) → output state_writer=true
#   cache-date          TODAY=<UTC date> to $GITHUB_ENV (the restore key)
#   cache-save-date     SAVE_DAY=<UTC date> to $GITHUB_ENV (the save key)
#   git-auth            gh auth setup-git, so a session's plain git push authenticates
#   poll                dispatcher-poll.sh (--dry-run unless STATE_WRITER=true); outputs
#                       issue_number (the poll's next_issue: the scheduler's choice,
#                       issue #474) and resumable
#   invoke              dispatcher-invoke.sh (--dry-run unless STATE_WRITER=true) with
#                       --refine $REFINE_ISSUE when it is set (issue #319), else
#                       with --repo only: work in flight, then the scheduler's
#                       new-work decision, asked again with fresh facts (issue #474)
#   trigger-check       dispatcher-trigger-check.sh (--dry-run unless STATE_WRITER=true)
#   retry               dispatcher-notify-retry.sh run (--dry-run unless STATE_WRITER=true)
#   usage-limit-notify  the usage_limit_reached Slack notification, then mark-notified
#
# Environment: GITHUB_OUTPUT and GITHUB_ENV (files the runner provides), REPO
# (<owner/repo>, the steps pass github.repository), STATE_WRITER, REFINE_ISSUE,
# and the variables named per step above. Output goes to those files and to
# stderr; stdout stays empty.
#
# Exit codes: the exit code of the script a step runs; 2 for invalid usage.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() { echo "dispatcher-workflow-step: $*" >&2; exit 2; }

require_file_var() {  # require_file_var <name>
    [[ -n "${!1:-}" ]] || die "$1 is not set (the runner provides it)"
}

require_repo() { [[ "${REPO:-}" == */* ]] || die "REPO <owner/repo> is required"; }

# state_writer_flag — "--dry-run" unless this run is the state writer (ADR-014
# → Block 3, rule 3). Prints the flag or nothing.
dry_run_unless_writer() {
    if [[ "${STATE_WRITER:-}" != "true" ]]; then
        echo "--dry-run"
    fi
}

step_guard() {
    require_file_var GITHUB_OUTPUT
    if [[ "${DISPATCHER_ENABLED:-}" != "true" ]]; then
        echo "Dispatcher disabled" >&2
        echo "enabled=false" >> "$GITHUB_OUTPUT"
    else
        echo "enabled=true" >> "$GITHUB_OUTPUT"
    fi
}

# Actions caches are scoped by ref: a cache saved on a non-default ref is
# invisible to the scheduled run on the default branch. Only default-branch runs
# (every scheduled run, and manual runs dispatched on that branch) save state.
step_scope() {
    require_file_var GITHUB_OUTPUT
    local writer=false
    if [[ "${EVENT_NAME:-}" == "schedule" ]] \
        || { [[ -n "${DEFAULT_BRANCH:-}" ]] && [[ "${REF:-}" == "refs/heads/${DEFAULT_BRANCH}" ]]; }; then
        writer=true
    fi
    echo "state_writer=$writer" >> "$GITHUB_OUTPUT"
    if [[ "$writer" != "true" ]]; then
        echo "Non-default ref ${REF:-}: poll runs with --dry-run; dispatcher state is read-only" >&2
    fi
}

step_cache_date() {
    require_file_var GITHUB_ENV
    echo "TODAY=$(date -u +%Y-%m-%d)" >> "$GITHUB_ENV"
}

step_cache_save_date() {
    require_file_var GITHUB_ENV
    echo "SAVE_DAY=$(date -u +%Y-%m-%d)" >> "$GITHUB_ENV"
}

step_git_auth() {
    gh auth setup-git
}

step_poll() {
    require_file_var GITHUB_OUTPUT
    require_repo
    local args=(--repo "$REPO") flag poll_json next resumable
    flag=$(dry_run_unless_writer)
    [[ -z "$flag" ]] || args+=("$flag")
    poll_json=$(bash "$SCRIPT_DIR/dispatcher-poll.sh" "${args[@]}")
    # The poll decides what is due (issues #293, #315, #348, #409, #449) and
    # reports it; a poll that does not is a failed step.
    jq -e 'type == "object"' <<< "$poll_json" >/dev/null || die "the poll printed no JSON object"
    next=$(jq -r '.next_issue // empty' <<< "$poll_json")
    resumable=$(jq -r '.resumable | if type == "boolean" then tostring else error("resumable is missing") end' <<< "$poll_json")
    echo "issue_number=$next" >> "$GITHUB_OUTPUT"
    echo "resumable=$resumable" >> "$GITHUB_OUTPUT"
}

# The scheduled run handles work in flight first, then asks the scheduler again
# right before it starts new work, so the decision uses fresh facts (issue
# #474); the poll's choice only decides whether this step runs. The session
# count is saved to this ref's cache scope, so a run on a non-default ref is
# read-only (--dry-run) and cannot start sessions the next scheduled
# default-branch run would not see.
step_invoke() {
    require_repo
    local args=(--repo "$REPO") flag
    flag=$(dry_run_unless_writer)
    [[ -z "$flag" ]] || args+=("$flag")
    if [[ -n "${REFINE_ISSUE:-}" ]]; then
        bash "$SCRIPT_DIR/dispatcher-invoke.sh" "${args[@]}" --refine "$REFINE_ISSUE"
    else
        bash "$SCRIPT_DIR/dispatcher-invoke.sh" "${args[@]}"
    fi
}

step_trigger_check() {
    require_repo
    local args=(--repo "$REPO") flag
    flag=$(dry_run_unless_writer)
    [[ -z "$flag" ]] || args+=("$flag")
    bash "$SCRIPT_DIR/dispatcher-trigger-check.sh" "${args[@]}"
}

step_retry() {
    require_repo
    local args=(run --repo "$REPO") flag
    flag=$(dry_run_unless_writer)
    [[ -z "$flag" ]] || args+=("$flag")
    bash "$SCRIPT_DIR/dispatcher-notify-retry.sh" "${args[@]}"
}

# Issue #320: sent in the run in which the limit stopped a session. The marker
# is written only after a send without an error, so a failed delivery is tried
# again on the next run.
step_usage_limit_notify() {
    require_repo
    local context sent=false
    echo "[USAGE LIMIT NOTIFY] Sending usage_limit_reached Slack notification" >&2
    context=$(bash "$SCRIPT_DIR/dispatcher-usage-limit.sh" status \
        | jq -c --arg settings_url "https://github.com/${REPO}/settings/variables/actions" '. + {settings_url: $settings_url}')
    bash "$SCRIPT_DIR/slack-notify.sh" usage_limit_reached "$context" && sent=true || true
    if [[ "$sent" != "true" ]]; then
        # No marker: the notification is tried again on the next run, and the step is red.
        echo "[USAGE LIMIT NOTIFY] Error: the notification was not delivered" >&2
        return 1
    fi
    bash "$SCRIPT_DIR/dispatcher-usage-limit.sh" mark-notified
}

[[ $# -eq 1 ]] || die "usage: $0 guard|scope|cache-date|cache-save-date|git-auth|poll|invoke|trigger-check|retry|usage-limit-notify"
case "$1" in
    guard)              step_guard ;;
    scope)              step_scope ;;
    cache-date)         step_cache_date ;;
    cache-save-date)    step_cache_save_date ;;
    git-auth)           step_git_auth ;;
    poll)               step_poll ;;
    invoke)             step_invoke ;;
    trigger-check)      step_trigger_check ;;
    retry)              step_retry ;;
    usage-limit-notify) step_usage_limit_notify ;;
    *)                  die "unknown step: $1" ;;
esac
