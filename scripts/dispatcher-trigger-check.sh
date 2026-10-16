#!/usr/bin/env bash
# scripts/dispatcher-trigger-check.sh
#
# Stop alert for the dispatcher trigger (issue #339, ADR-013 → Decision 4).
# Reads the creation time of the newest workflow_dispatch run of dispatcher.yml
# on main. When it is more than --threshold-minutes (default 15) old, or no
# such run exists, queues one dispatch_trigger_stopped event through
# scripts/dispatcher-notify-retry.sh (the #295 queue); that script sends it,
# retries it, and drops it once a newer dispatched run exists.
#
# Staleness uses the run's creation time, never its finish time or conclusion,
# so a long executor session or a run replaced in the concurrency group
# (cancelled) does not look like a stopped trigger.
#
# Usage:
#   bash scripts/dispatcher-trigger-check.sh --repo <owner/repo> [--dry-run] [--threshold-minutes 15]
#
# The event key is the creation time of the newest dispatched run (or "none"),
# so each gap notifies once. With --dry-run (the workflow passes it on a
# non-default ref) the check only reports and never queues.
#
# Exit codes: 0 fresh, queued, already known, or the API read failed (warning;
# the check never fails the run); 1 the event could neither be recorded nor
# delivered; 2 usage error.
# Log lines go to stderr as [TRIGGER]; stdout stays empty.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
die() { echo "Error: $*" >&2; exit 2; }

REPO="" DRY_RUN=false THRESHOLD=15
while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo) [[ $# -ge 2 ]] || die "--repo requires a value"; REPO="$2"; shift 2 ;;
        --threshold-minutes) [[ $# -ge 2 ]] || die "--threshold-minutes requires a value"; THRESHOLD="$2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        *) die "unknown flag: $1" ;;
    esac
done
[[ "$REPO" == */* ]] || die "--repo <owner/repo> is required"
[[ "$THRESHOLD" =~ ^[0-9]+$ ]] || die "--threshold-minutes must be a whole number"
command -v jq >/dev/null 2>&1 || die "'jq' is required"

CHECK_COMMAND="gh run list --workflow dispatcher.yml --branch main --event workflow_dispatch --limit 5"
DOCS_URL="https://github.com/$REPO/blob/main/docs/DISPATCHER-CONFIG.md#dispatcher-trigger"

if ! out=$(gh run list --repo "$REPO" --workflow dispatcher.yml --branch main \
        --event workflow_dispatch --limit 1 --json databaseId,createdAt 2>&1); then
    echo "[TRIGGER] Warning: cannot read the newest dispatched run; no stop check this cycle: $(head -c 300 <<< "$out" | tr '\n' ' ')" >&2
    exit 0
fi
if ! jq -e 'type == "array"' <<< "$out" >/dev/null 2>&1; then
    echo "[TRIGGER] Warning: unreadable dispatched-run list; no stop check this cycle" >&2
    exit 0
fi

created=$(jq -r '.[0].createdAt // ""' <<< "$out")
run_id=$(jq -r '.[0].databaseId // ""' <<< "$out")
now_s=$(date -u +%s)

age_min="" key="none" age_text="none"
if [[ -n "$created" ]]; then
    created_s=$(date -u -d "$created" +%s 2>/dev/null) || {
        echo "[TRIGGER] Warning: unreadable createdAt '$created'; no stop check this cycle" >&2
        exit 0
    }
    age_min=$(( (now_s - created_s) / 60 ))
    key="$created"
    age_text="$age_min minutes"
fi

if [[ -n "$created" && "$age_min" -le "$THRESHOLD" ]]; then
    echo "[TRIGGER] Fresh: newest dispatched run $run_id was created $age_min minutes ago (threshold $THRESHOLD)" >&2
    exit 0
fi

ctx=$(jq -nc --arg age "$age_text" --arg at "${created:-none}" --arg cmd "$CHECK_COMMAND" --arg url "$DOCS_URL" \
    '{age:$age, last_created_at:$at, check_command:$cmd, docs_url:$url}')

if $DRY_RUN; then
    echo "[TRIGGER] Would queue: dispatch trigger stopped, newest dispatched run age $age_text (dispatch_trigger_stopped:0:$key)" >&2
    exit 0
fi

echo "[TRIGGER] Dispatch trigger stopped: newest dispatched run age $age_text (run ${run_id:-none})" >&2
bash "$SCRIPT_DIR/dispatcher-notify-retry.sh" register --repo "$REPO" \
    --event dispatch_trigger_stopped --issue 0 --since "$key" --context "$ctx"
rc=$?
if (( rc != 0 )); then
    echo "[TRIGGER] Error: dispatch_trigger_stopped could not be queued or delivered" >&2
    exit 1
fi
exit 0
