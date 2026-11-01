#!/usr/bin/env bash
# scripts/dispatcher-notify-retry.sh
#
# Delivery of the Slack notifications that need PO action (issue #295):
# decision_blocker, po_blocker, feature_closure_confirmation,
# epic_closure_approval and dispatch_trigger_stopped (issue #339).
# Each is retried every dispatcher cycle until it is delivered or the PO no
# longer needs to act. A failed delivery never aborts the dispatcher.
#
# Usage:
#   bash scripts/dispatcher-notify-retry.sh register --repo <owner/repo> \
#       --event <event> --issue <n> [--since <iso8601>] --context <json> [--dry-run]
#   bash scripts/dispatcher-notify-retry.sh run --repo <owner/repo> [--dry-run] [--drain]
#
# register  Records an actionable event as pending (idempotent per event key).
#           Called by dispatcher-invoke.sh where it used to send directly.
# run       One delivery cycle: drops resolved events, sends every undelivered
#           one, records the result. Run by the workflow on every cycle, also
#           when no story is ready or resumable.
#
# run --drain  An extra delivery attempt that dispatcher-invoke.sh makes before
#           it chains to the next story, so an alert is not held back by hours
#           of chained sessions. It records a success but never counts a
#           failure, posts a comment or exits 1: the bound is counted once per
#           cycle, by the workflow's own run.
#
# Event key: <event>:<issue>:<since>. For decision_blocker, <since> is the
# (and po_blocker, issue #359) the created_at of the "## Blocked Declaration" comment, so a new blocker on the
# same issue after an earlier one was resolved is a new event.
#
# Resolved (record dropped, even if never delivered):
#   decision_blocker,     the issue is closed, or the PO ($DECISION_BLOCKER_PO_LOGIN)
#   po_blocker            posted a standalone "/unblock" line after <since>
#   closure events        the Feature or Epic issue is closed
#   dispatch_trigger_stopped  (issue #339, registered with --issue 0 and --since
#                         the creation time of the newest dispatched run, or
#                         "none") a workflow_dispatch run of dispatcher.yml on
#                         main created after <since> exists. A failed lookup
#                         counts as unresolved. No comment is posted for it
#                         (there is no issue); a persistent failure only turns
#                         the run red.
# A dropped record can be registered again (then it is dropped again by the
# resolution check before any send), so a delivered event stays suppressed
# while unresolved and only while unresolved.
#
# State (issue #257 mechanism, no second store): one JSON object per line in
# $DISPATCHER_STATE_DIR/<owner>/<repo>/actionable-events, restored and saved by
# actions/cache in .github/workflows/dispatcher.yml, written atomically and
# only after slack-notify.sh exits 0. Missing state starts empty, silently.
# Malformed lines are dropped with a warning while valid lines are kept, and a
# state path that is a directory is moved aside. A state file that exists as a
# regular file but cannot be read (issue #352) is never overwritten or removed:
# the events in it stay recoverable. run sends nothing and exits 1 (run --drain
# only warns and exits 0); register delivers the new event directly and never
# writes a state file holding only that event.
#
# Persistent failure: each cycle in which an undelivered event fails to send
# adds 1 to its failed_cycles. At 3 the run exits 1 (red) and one comment is
# posted on the target issue naming the event and the first failed attempt.
# Later failing cycles stay red but add no comment.
#
# Exit codes: 0 all fine or failures still within the bound; 1 a delivery has
# failed for FAILURE_BOUND cycles in a row; 2 invocation error.
# Log lines go to stderr as [NOTIFY]; stdout stays empty.

set -uo pipefail

FAILURE_BOUND=3
DECISION_BLOCKER_PO_LOGIN="${DECISION_BLOCKER_PO_LOGIN:-LauraMardones}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STATE_DIR="${DISPATCHER_STATE_DIR:-$REPO_ROOT/.dispatcher-state}"

die() { echo "Error: $*" >&2; exit 2; }

[[ $# -ge 1 ]] || die "usage: $0 register|run --repo <owner/repo> ..."
MODE="$1"; shift
[[ "$MODE" == register || "$MODE" == run ]] || die "unknown mode: $MODE"

REPO="" EVENT="" ISSUE="" SINCE="" CONTEXT="" DRY_RUN=false DRAIN=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo)    [[ $# -ge 2 ]] || die "--repo requires a value"; REPO="$2"; shift 2 ;;
        --event)   [[ $# -ge 2 ]] || die "--event requires a value"; EVENT="$2"; shift 2 ;;
        --issue)   [[ $# -ge 2 ]] || die "--issue requires a value"; ISSUE="$2"; shift 2 ;;
        --since)   [[ $# -ge 2 ]] || die "--since requires a value"; SINCE="$2"; shift 2 ;;
        --context) [[ $# -ge 2 ]] || die "--context requires a value"; CONTEXT="$2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        --drain)   DRAIN=true; shift ;;
        *) die "unknown flag: $1" ;;
    esac
done
[[ "$REPO" == */* ]] || die "--repo <owner/repo> is required"
command -v jq >/dev/null 2>&1 || die "'jq' is required"

OWNER="${REPO%%/*}"
REPO_NAME="${REPO##*/}"
STATE_FILE="$STATE_DIR/$OWNER/$REPO_NAME/actionable-events"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
EVENTS="$TMP_DIR/events"   # working copy: one JSON object per line

# ─── State ────────────────────────────────────────────────────────────────────

# load_state returns 0 when the state is loaded (or missing, or a non-regular
# path that save_state sets aside) and 2 when the state file is a regular file
# that cannot be read. The caller must not save over it then (issue #352).
load_state() {
    : > "$EVENTS"
    [[ -e "$STATE_FILE" ]] || return 0
    local copy="$TMP_DIR/state-copy"
    if ! cp "$STATE_FILE" "$copy" 2>/dev/null; then
        if [[ -f "$STATE_FILE" ]]; then
            return 2
        fi
        echo "[NOTIFY] Warning: cannot read actionable event state $STATE_FILE; continuing with empty state" >&2
        return 0
    fi
    # Keep every line that is an object carrying the fields the cycle relies
    # on; drop the rest. A malformed line must not discard a valid pending
    # record next to it, since a closure event is not registered again.
    jq -R -c 'fromjson? | select(type == "object"
            and (.key|type) == "string" and (.event|type) == "string"
            and (.issue|type) == "number" and (.context|type) == "string"
            and (.delivered|type) == "boolean" and (.failed_cycles|type) == "number")' \
        "$copy" > "$EVENTS" 2>/dev/null || : > "$EVENTS"
    local total kept
    total="$(grep -c . "$copy" || true)"
    kept="$(grep -c . "$EVENTS" || true)"
    if [[ "$total" != "$kept" ]]; then
        echo "[NOTIFY] Warning: malformed lines in actionable event state $STATE_FILE; dropped $((total - kept)), kept $kept" >&2
    fi
}

# save_state returns 1 when the state cannot be written, so a caller never
# treats an event as recorded when it exists only in the temporary copy.
save_state() {
    $DRY_RUN && return 0
    # mv would move the temporary file into a directory-valued state path and
    # report success while every later load still fails; set such a path aside.
    if [[ -e "$STATE_FILE" && ! -f "$STATE_FILE" ]]; then
        rm -rf "$STATE_FILE.unreadable" 2>/dev/null || true
        if mv -f "$STATE_FILE" "$STATE_FILE.unreadable" 2>/dev/null; then
            echo "[NOTIFY] Warning: $STATE_FILE is not a regular file; moved aside to $STATE_FILE.unreadable" >&2
        fi
    fi
    if ! { mkdir -p "$(dirname "$STATE_FILE")" \
            && cp "$EVENTS" "$STATE_FILE.tmp.$$" \
            && mv -f "$STATE_FILE.tmp.$$" "$STATE_FILE"; } 2>/dev/null; then
        rm -f "$STATE_FILE.tmp.$$" 2>/dev/null || true
        echo "[NOTIFY] Warning: cannot write actionable event state $STATE_FILE" >&2
        return 1
    fi
}

# ─── register ─────────────────────────────────────────────────────────────────

if [[ "$MODE" == register ]]; then
    [[ -n "$EVENT" && "$ISSUE" =~ ^[0-9]+$ && -n "$CONTEXT" ]] \
        || die "register needs --event, a numeric --issue and --context"
    jq -e 'type == "object"' <<< "$CONTEXT" >/dev/null 2>&1 || die "--context must be a JSON object"
    KEY="$EVENT:$ISSUE:$SINCE"
    if $DRY_RUN; then
        echo "[DRY RUN] Would register actionable event: $KEY" >&2
        exit 0
    fi
    STATE_UNREADABLE=false
    load_state || STATE_UNREADABLE=true
    if $STATE_UNREADABLE; then
        echo "[NOTIFY] Warning: cannot read actionable event state $STATE_FILE; not changing it, delivering directly: $KEY" >&2
    elif jq -e --arg k "$KEY" 'select(.key == $k)' "$EVENTS" 2>/dev/null | grep -q .; then
        exit 0
    fi
    jq -nc --arg k "$KEY" --arg e "$EVENT" --argjson i "$ISSUE" --arg s "$SINCE" --arg c "$CONTEXT" \
        '{key:$k, event:$e, issue:$i, since:$s, context:$c, delivered:false,
          failed_cycles:0, first_failed_at:"", comment_posted:false}' >> "$EVENTS"
    if $STATE_UNREADABLE || ! save_state; then
        # The record cannot be kept for a later cycle, so deliver it now rather
        # than lose a one-time event; a failed delivery fails the call.
        if SLACK_WEBHOOK_URL="${SLACK_WEBHOOK_URL:-}"                 bash "$SCRIPT_DIR/slack-notify.sh" "$EVENT" "$CONTEXT" >/dev/null; then
            echo "[NOTIFY] State not usable; delivered synchronously: $KEY" >&2
            exit 0
        fi
        echo "[NOTIFY] Error: cannot record or deliver actionable event $KEY" >&2
        exit 1
    fi
    echo "[NOTIFY] Registered actionable event: $KEY" >&2
    exit 0
fi

# ─── run ──────────────────────────────────────────────────────────────────────

# resolved <event> <issue> <since>: 0 when the PO no longer needs to act.
# A failed lookup counts as unresolved so the event is never lost to an API error.
resolved() {
    local event="$1" issue="$2" since="$3" state comments newest
    if [[ "$event" == dispatch_trigger_stopped ]]; then
        newest=$(gh run list --repo "$REPO" --workflow dispatcher.yml --branch main \
            --event workflow_dispatch --limit 1 --json createdAt --jq '.[0].createdAt // ""' 2>/dev/null) \
            || return 1
        [[ -n "$newest" ]] || return 1
        [[ "$since" == none || "$newest" > "$since" ]]
        return
    fi
    state=$(gh api "repos/$REPO/issues/$issue" --jq '.state' 2>/dev/null) || state=""
    [[ "$state" == closed ]] && return 0
    [[ "$event" == decision_blocker || "$event" == po_blocker ]] || return 1
    comments=$(gh api --paginate "repos/$REPO/issues/$issue/comments?per_page=100" 2>/dev/null | jq -s 'add // []' 2>/dev/null) \
        || return 1
    [[ "$(jq -r --arg login "$DECISION_BLOCKER_PO_LOGIN" --arg since "$since" \
        '[.[] | select((.user.login // "") == $login and .created_at > $since
              and ((.body // "") | split("\n") | any(test("^\\s*/unblock\\s*$"))))] | length > 0' \
        <<< "$comments" 2>/dev/null)" == true ]]
}

if ! load_state; then
    # Saving now would overwrite the events in the file (issue #352).
    if $DRAIN; then
        echo "[NOTIFY] Warning: cannot read actionable event state $STATE_FILE; drain sends nothing and leaves it unchanged" >&2
        exit 0
    fi
    echo "[NOTIFY] Error: cannot read actionable event state $STATE_FILE; nothing sent, file left unchanged" >&2
    exit 1
fi
NEXT="$TMP_DIR/next"
: > "$NEXT"
RED=false

while IFS= read -r rec; do
    [[ -n "$rec" ]] || continue
    key=$(jq -r '.key' <<< "$rec")
    event=$(jq -r '.event' <<< "$rec")
    issue=$(jq -r '.issue' <<< "$rec")
    since=$(jq -r '.since // ""' <<< "$rec")
    delivered=$(jq -r '.delivered' <<< "$rec")

    if resolved "$event" "$issue" "$since"; then
        echo "[NOTIFY] Resolved, dropping: $key" >&2
        continue
    fi
    if [[ "$delivered" == true ]]; then
        echo "$rec" >> "$NEXT"
        continue
    fi

    context=$(jq -r '.context' <<< "$rec")
    if $DRY_RUN; then
        echo "[DRY RUN] Would notify Slack: $event $context" >&2
        echo "$rec" >> "$NEXT"
        continue
    fi

    if SLACK_WEBHOOK_URL="${SLACK_WEBHOOK_URL:-}" \
            bash "$SCRIPT_DIR/slack-notify.sh" "$event" "$context" >/dev/null; then
        echo "[NOTIFY] Delivered: $key" >&2
        jq -c '.delivered = true' <<< "$rec" >> "$NEXT"
        continue
    fi

    if $DRAIN; then
        echo "[NOTIFY] Warning: Slack delivery failed for $key during drain; the cycle's retry step will try again" >&2
        echo "$rec" >> "$NEXT"
        continue
    fi

    now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    rec=$(jq -c --arg now "$now" \
        '.failed_cycles += 1 | .first_failed_at = (if (.first_failed_at // "") == "" then $now else .first_failed_at end)' <<< "$rec")
    cycles=$(jq -r '.failed_cycles' <<< "$rec")
    echo "[NOTIFY] Warning: Slack delivery failed for $key (failed cycle $cycles of $FAILURE_BOUND); will retry next cycle" >&2
    if (( cycles >= FAILURE_BOUND )); then
        RED=true
        if [[ "$event" != dispatch_trigger_stopped && "$(jq -r '.comment_posted' <<< "$rec")" != true ]]; then
            first=$(jq -r '.first_failed_at' <<< "$rec")
            body="## Slack notification not delivered
The Slack notification \`$event\` for #$issue could not be delivered for $cycles dispatcher cycles in a row (first failed attempt: $first). The dispatcher keeps retrying and Slack will get it as soon as delivery recovers; this issue needs the PO's attention in the meantime. Check the \`SLACK_WEBHOOK_URL\` secret and the Slack webhook."
            if gh api "repos/$REPO/issues/$issue/comments" -X POST -f body="$body" >/dev/null 2>&1; then
                rec=$(jq -c '.comment_posted = true' <<< "$rec")
                echo "[NOTIFY] Posted delivery-failure comment on #$issue" >&2
            else
                echo "[NOTIFY] Warning: cannot post delivery-failure comment on #$issue; will retry next cycle" >&2
            fi
        fi
    fi
    echo "$rec" >> "$NEXT"
done < "$EVENTS"

cp "$NEXT" "$EVENTS"
if ! save_state; then
    echo "[NOTIFY] Error: actionable event state could not be saved; delivery progress and failure counts are lost for this cycle" >&2
    exit 1
fi

if $RED; then
    echo "[NOTIFY] Error: an actionable Slack notification failed for $FAILURE_BOUND or more cycles in a row" >&2
    exit 1
fi
exit 0
