#!/usr/bin/env bash
# scripts/lib/in-flight.sh
#
# Finds the work in flight from GitHub facts, never from a board status (issue
# #475, ADR-010). scripts/dispatcher-poll.sh and the resume pass of
# scripts/dispatcher-invoke.sh both call it, so they cannot disagree.
#
# This file reads the facts; the rule that decides from them is
# `hpw dispatcher in-flight plan` (src/headless_pr_workflow/dispatcher/in_flight.py,
# ADR-014: a rule that decides from GitHub facts is written in Python). It reads
# no marker itself: the rule checks each marker against the account given.
#
# Source this file after scripts/lib/review-routing.sh (CODEX_REVIEW_LOOP_ROUTING),
# define the two readers below, set the inputs, and call:
#
#   inflight_compute        → 0 and INFLIGHT_JSON set, or 1 (a read failed)
#
# Readers (the caller defines them; each prints one JSON array, exit != 0 on a
# failed read):
#   inflight_get_all <path>   every page of a REST list ("repos/<r>/pulls?state=open"; the
#                             reader adds per_page and page)
#   inflight_get_page <path>  the first page only (the 100 most recently updated closed PRs)
# Inputs:
#   REPO, HPW_BIN, INFLIGHT_BOT (the dispatcher's login), INFLIGHT_BK (the
#   bookkeeping job's login), INFLIGHT_PO (the PO's login), MERGE_GATE_SCRIPT
#   (scripts/merge-gate-summary; overridable for tests).
#
# For each open Story, Task or Bug the *linked PR* is the first open PR whose
# body says Closes, Fixes or Resolves #<n>. INFLIGHT_JSON is the rule's output:
#   merge        non-draft linked PR whose merge gates pass on the current head,
#                ignoring the mergeability gate (policy, checks, approval,
#                threads and head)
#   finish       no open linked PR, a merged linked PR among the closed PRs read
#   fix_round    review-loop story (one executor: label with a
#                CODEX_REVIEW_LOOP_ROUTING row) whose PR head has the
#                bookkeeping job's findings marker (fix_round_due in
#                dispatcher-invoke.sh); resume_fix_round then decides on an
#                attempt already made, the round maximum and a pending hand-off
#   hand_off     review-loop story, non-draft linked PR, hand-off-pending marker
#                without hand-off-done
#   review       any other story with a non-draft linked PR (not in the merge step)
#   in_implementation  numbers for `scheduler next --in-implementation`: a draft
#                linked PR, or a fix round due
#   in_flight    numbers for `scheduler next --in-flight`: every other story
#                with an open linked PR, and every one with an open
#                dependency, external, conflict or resource blocker that no
#                later PO /unblock line resolves
# Each step entry is {number, title, executors, pr, head} (pr and head are
# null when there is no PR); the two number lists are plain arrays.
#
# A gate script that cannot be run or read counts as not passing, so the story
# stays in the review step. A comment list that cannot be read adds no fix
# round, hand-off or blocker for that story and logs a warning.

INFLIGHT_JSON=""

# The linked-PR rule (today's rule in both scripts).
INFLIGHT_JQ_LINKED='
def linked($list; $n):
    [ $list[] | select((.body // "") | ascii_downcase
        | test("(closes|fixes|resolves)[[:space:]]+#\($n)\\b")) ][0];
'

# inflight_gates_pass <pr> <head> — 0 when policy, checks, approval, threads and
# head pass in the gate line of scripts/merge-gate-summary; mergeability is left
# to the merge step itself. 1 for a failing gate, an unreadable one, or a line
# for another head.
inflight_gates_pass() {
    local pr="$1" head="${2,,}" line rc=0 gate_head
    local script="${MERGE_GATE_SCRIPT:-}"
    [[ -n "$script" && -n "$head" ]] || return 1
    line=$("$script" --pr "$pr" --repo "$REPO" 2>/dev/null) || rc=$?
    [[ $rc -le 1 ]] || return 1
    line=$(grep -m1 '^Merge gate:' <<< "$line") || return 1
    local re='policy=pass\([A-Za-z_]+\), checks=(pass|absent-ok), approval=(formal|codex-review|separate-session), threads=none, mergeable=[^,]+, head=([0-9a-fA-F]{7})$'
    [[ "$line" =~ $re ]] || return 1
    gate_head="${BASH_REMATCH[3],,}"
    [[ "$head" == "$gate_head"* ]]
}

inflight_compute() {
    local prs issues closed stories row n comments_count executors review_loop
    local pr_json pr_number pr_head pr_draft merged gates story_comments pr_comments
    local tmp rows out rc=0
    INFLIGHT_JSON=""
    prs=$(inflight_get_all "repos/$REPO/pulls?state=open") || {
        echo "[IN-FLIGHT] unreadable: the open pull requests could not be read" >&2; return 1; }
    issues=$(inflight_get_all "repos/$REPO/issues?state=open") || {
        echo "[IN-FLIGHT] unreadable: the open issues could not be read" >&2; return 1; }
    closed=$(inflight_get_page "repos/$REPO/pulls?state=closed&sort=updated&direction=desc&per_page=100") || {
        echo "[IN-FLIGHT] Warning: the closed pull requests could not be read; no merged PR is finished in this run" >&2
        closed='[]'; }
    stories=$(jq -c '[.[] | select(has("pull_request") | not)
        | select(([.labels[]?.name] | any(IN("type:story", "type:task", "type:bug")))
                 or ((.type.name? // "") | ascii_downcase | IN("story", "task", "bug")))
        | {number, title: (.title // ""), comments: (.comments // 0),
           executors: [.labels[]?.name | select(startswith("executor:")) | ltrimstr("executor:")]}]
        | sort_by(.number) | .[]' <<< "$issues") \
        || { echo "[IN-FLIGHT] unreadable: the open issues are not a list" >&2; return 1; }

    tmp=$(mktemp -d) || { echo "[IN-FLIGHT] unreadable: no temporary directory" >&2; return 1; }
    rows="$tmp/rows.jsonl"
    : > "$rows"
    while IFS= read -r row; do
        [[ -n "$row" ]] || continue
        n=$(jq -r '.number' <<< "$row")
        comments_count=$(jq -r '.comments' <<< "$row")
        executors=$(jq -c '.executors' <<< "$row")
        review_loop=false
        if [[ "$(jq 'length' <<< "$executors")" -eq 1 ]]; then
            [[ -n "${CODEX_REVIEW_LOOP_ROUTING[$(jq -r '.[0]' <<< "$executors")]:-}" ]] && review_loop=true
        fi
        pr_json=$(jq -c --argjson n "$n" "$INFLIGHT_JQ_LINKED"'linked(.; $n) // empty' <<< "$prs")
        pr_number=$(jq -r '.number // empty' <<< "$pr_json" 2>/dev/null || true)
        pr_head=$(jq -r '.head.sha // empty' <<< "$pr_json" 2>/dev/null || true)
        pr_draft=$(jq -r '.draft // false' <<< "$pr_json" 2>/dev/null || echo false)
        merged=false gates=false story_comments=null pr_comments=null

        if [[ -z "$pr_number" ]]; then
            merged=$(jq -r --argjson n "$n" 'any(.[]; .merged_at != null
                and ((.body // "") | ascii_downcase | test("(closes|fixes|resolves)[[:space:]]+#\($n)\\b")))' \
                <<< "$closed") || merged=false
            # A story with comments may carry an open blocker: it is in flight until /unblock.
            if [[ "$merged" != "true" && "$comments_count" -gt 0 ]]; then
                story_comments=$(inflight_get_all "repos/$REPO/issues/$n/comments") || {
                    echo "[IN-FLIGHT] Warning: could not read the comments of #$n; its blockers are not checked in this run" >&2
                    story_comments=null; }
            fi
        else
            if $review_loop && [[ -n "$INFLIGHT_BOT" ]]; then
                if ! story_comments=$(inflight_get_all "repos/$REPO/issues/$n/comments") \
                        || ! pr_comments=$(inflight_get_all "repos/$REPO/issues/$pr_number/comments"); then
                    echo "[IN-FLIGHT] Warning: could not read the comments of #$n or PR #$pr_number; no fix round or hand-off is found for it in this run" >&2
                    story_comments=null pr_comments=null
                fi
            fi
            if [[ "$pr_draft" != "true" ]] && inflight_gates_pass "$pr_number" "$pr_head"; then
                gates=true
            fi
        fi
        # Comment lists go through stdin, not the argument list, so a long thread fits.
        printf '%s\n%s\n' "$story_comments" "$pr_comments" | jq -sc --argjson base "$row" \
            --argjson rl "$review_loop" --argjson merged "$merged" --argjson gates "$gates" \
            --arg pr "$pr_number" --arg head "$pr_head" --argjson draft "${pr_draft:-false}" '
            {number: $base.number, title: $base.title, executors: $base.executors, review_loop: $rl,
             pr: (if $pr == "" then null else {number: ($pr | tonumber), draft: $draft, head: $head} end),
             merged: $merged, gates_pass: $gates, story_comments: .[0], pr_comments: .[1]}' >> "$rows" \
            || { echo "[IN-FLIGHT] unreadable: the facts of #$n could not be put together" >&2; rm -rf "$tmp"; return 1; }
    done <<< "$stories"

    jq -s '.' "$rows" > "$tmp/rows.json" || { rm -rf "$tmp"; return 1; }
    out=$(DECISION_BLOCKER_PO_LOGIN="$INFLIGHT_PO" "$HPW_BIN" dispatcher in-flight plan --repo "$REPO" \
        --input "$tmp/rows.json" --dispatcher "$INFLIGHT_BOT" --bookkeeping "$INFLIGHT_BK") || rc=$?
    rm -rf "$tmp"
    if [[ $rc -ne 0 ]] || ! jq -e '.result == "read"' <<< "$out" >/dev/null 2>&1; then
        echo "[IN-FLIGHT] unreadable: hpw dispatcher in-flight plan exited $rc ($(jq -r '.error // "no result"' <<< "$out" 2>/dev/null || echo "no result"))" >&2
        return 1
    fi
    INFLIGHT_JSON=$(jq -c '{merge, finish, fix_round, hand_off, review, in_implementation, in_flight}' <<< "$out") || return 1
}

# inflight_scheduler_lists — sets IN_IMPLEMENTATION_LIST and IN_FLIGHT_LIST (comma-separated
# issue numbers, "" for none) from INFLIGHT_JSON, for scheduler_next.
inflight_scheduler_lists() {
    IN_IMPLEMENTATION_LIST=$(jq -r '.in_implementation | map(tostring) | join(",")' <<< "$INFLIGHT_JSON")
    IN_FLIGHT_LIST=$(jq -r '.in_flight | map(tostring) | join(",")' <<< "$INFLIGHT_JSON")
}
