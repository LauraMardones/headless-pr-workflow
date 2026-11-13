#!/usr/bin/env bash
# scripts/lib/scheduler.sh
#
# Asks the scheduler for the next new-work action (issue #474). The decision
# rule lives in `hpw dispatcher scheduler next` (issue #473, ADR-010); this file
# only calls it and reads its JSON, so dispatcher-poll.sh and
# dispatcher-invoke.sh act on the same result. There is no decision rule here.
#
# Source this file and call:
#
#   scheduler_next <in-implementation list> <in-flight list> [--dry-run]
#
# Both lists are comma-separated issue numbers ("" for none), found from pull
# request facts by scripts/lib/in-flight.sh (issue #475): <in-implementation
# list> is the stories with a draft linked PR or a fix round due (they count
# for the WIP limit and the file overlap), <in-flight list> every other story
# with work in flight (never started, not counted). The scheduler's own `[SCHEDULER] ...`
# lines (the action, idle, unreadable and every skipped entry, such as
# no-merged-spec) go to stderr unchanged, so they reach the run log.
#
# It sets:
#   SCHEDULER_RESULT  refine | implement | idle | unreadable
#   SCHEDULER_ISSUE   the action's issue number, or "" for idle and unreadable
#   SCHEDULER_ACTION  the action object as compact JSON, or "null"
#
# It always returns 0: an output it cannot read counts as unreadable (fail
# closed), and the caller starts no new work for it.
#
# Requires: bash 4+, jq, HPW_BIN and REPO set by the caller.

scheduler_next() {
    local in_impl="$1" in_flight="$2" out rc=0
    shift 2
    local -a args=(dispatcher scheduler next --repo "$REPO" --in-implementation "$in_impl"
                   --in-flight "$in_flight" "$@")
    out=$("$HPW_BIN" "${args[@]}") || rc=$?
    SCHEDULER_RESULT=$(jq -r 'if type == "object" then (.result // "") else "" end' <<< "$out" 2>/dev/null) \
        || SCHEDULER_RESULT=""
    SCHEDULER_ACTION=$(jq -c 'if type == "object" then (.action // null) else null end' <<< "$out" 2>/dev/null) \
        || SCHEDULER_ACTION="null"
    SCHEDULER_ISSUE=$(jq -r 'if type == "object" and .issue != null then .issue | tostring else "" end' \
        <<< "$SCHEDULER_ACTION" 2>/dev/null) || SCHEDULER_ISSUE=""
    case "$SCHEDULER_RESULT" in
        refine|implement)
            if [[ ! "$SCHEDULER_ISSUE" =~ ^[1-9][0-9]*$ ]] \
                    || [[ "$(jq -r '.kind // ""' <<< "$SCHEDULER_ACTION")" != "$SCHEDULER_RESULT" ]]; then
                echo "[SCHEDULER] unreadable: the $SCHEDULER_RESULT result has no usable action" >&2
                SCHEDULER_RESULT="unreadable"
            fi
            ;;
        idle|unreadable)
            ;;
        *)
            echo "[SCHEDULER] unreadable: hpw dispatcher scheduler next exited $rc without a result" >&2
            SCHEDULER_RESULT="unreadable"
            ;;
    esac
    if [[ "$SCHEDULER_RESULT" != "refine" && "$SCHEDULER_RESULT" != "implement" ]]; then
        SCHEDULER_ISSUE=""
        SCHEDULER_ACTION="null"
    fi
    return 0
}
