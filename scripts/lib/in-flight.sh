#!/usr/bin/env bash
# scripts/lib/in-flight.sh
#
# Finds the work in flight from GitHub facts, never from a board status (issue
# #475, ADR-010). scripts/dispatcher-poll.sh and the resume pass of
# scripts/dispatcher-invoke.sh both call it, so they cannot disagree.
#
# Source this file after scripts/lib/review-routing.sh and
# scripts/lib/conflict-round.sh, define the two readers below, set the
# inputs, and call:
#
#   inflight_compute        → 0 and INFLIGHT_JSON set, or 1 (a read failed)
#
# Readers (the caller defines them; each prints one JSON array, exit != 0 on a
# failed read):
#   inflight_get_all <path>   every page of a REST list ("repos/<r>/pulls?state=open"; the
#                             reader adds per_page and page)
#   inflight_get_page <path>  the first page only (the 100 most recently updated closed PRs)
# Inputs:
#   REPO, INFLIGHT_BOT (the dispatcher's login), INFLIGHT_BK (the bookkeeping
#   job's login), INFLIGHT_PO (the PO's login), MERGE_GATE_SCRIPT
#   (scripts/merge-gate-summary; overridable for tests).
#
# For each open Story, Task or Bug the *linked PR* is the first open PR whose
# body says Closes, Fixes or Resolves #<n>. INFLIGHT_JSON is one object:
#   merge        non-draft linked PR whose merge gates pass on the current head,
#                ignoring the mergeability gate (policy, checks, approval,
#                threads and head)
#   finish       no open linked PR, a merged linked PR among the closed PRs read
#   fix_round    review-loop story (one executor: label with a
#                CODEX_REVIEW_LOOP_ROUTING row) whose PR head has the
#                bookkeeping job's findings marker and no fix-round attempt
#   hand_off     review-loop story, non-draft linked PR, hand-off-pending marker
#                without hand-off-done
#   review       any other story with a non-draft linked PR (not in the merge step)
#   in_implementation  numbers for `scheduler next --in-implementation`: a draft
#                linked PR, or a fix round due
#   in_flight    numbers for `scheduler next --in-flight`: every other story
#                with an open linked PR, and every one with an open
#                dependency, external, conflict or resource blocker that no
#                later PO /unblock line resolves
# Each list entry is {number, title, executors, pr, head} (pr and head are
# null when there is no PR); the two number lists are plain arrays.
#
# A gate script that cannot be run or read counts as not passing, so the story
# stays in the review step. A comment list that cannot be read adds no entry
# for that story and logs a warning.

INFLIGHT_JSON=""

# The linked-PR rule shared by the jq programs below.
INFLIGHT_JQ_DEFS='
def linked($list; $n):
    [ $list[] | select((.body // "") | ascii_downcase
        | test("(closes|fixes|resolves)[[:space:]]+#\($n)\\b")) ][0];
def trusted($po):
    (.user.login // "") == $po
    or ((.author_association // "") | IN("OWNER", "MEMBER", "COLLABORATOR"))
    or (.user.type // "") == "Bot";
def declaration: (.body // "") | test("(^|\n)## Blocked Declaration");
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
    local re='policy=pass\([A-Za-z_]+\), checks=(pass|absent-ok), approval=(formal|codex-review|separate-session), threads=none, mergeable=[A-Za-z-]+, head=([0-9a-fA-F]{7})$'
    [[ "$line" =~ $re ]] || return 1
    gate_head="${BASH_REMATCH[3],,}"
    [[ "$head" == "$gate_head"* ]]
}

# inflight_row <number> <title> <executors-json> <pr-or-empty> <head-or-empty>
inflight_row() {
    jq -nc --argjson n "$1" --arg t "$2" --argjson e "$3" --arg pr "$4" --arg h "$5" \
        '{number: $n, title: $t, executors: $e, pr: (if $pr == "" then null else ($pr | tonumber) end),
          head: (if $h == "" then null else $h end)}'
}

inflight_compute() {
    local prs issues closed stories row n title labels comments_count executors review_loop
    local pr_json pr_number pr_head pr_draft merged story_comments pr_comments word
    local -a merge=() finish=() fix_round=() hand_off=() review=() impl=() flight=()
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
        | {number, title, comments: (.comments // 0), labels: [.labels[]?.name]}] | sort_by(.number) | .[]' <<< "$issues") \
        || { echo "[IN-FLIGHT] unreadable: the open issues are not a list" >&2; return 1; }

    while IFS= read -r row; do
        [[ -n "$row" ]] || continue
        n=$(jq -r '.number' <<< "$row"); title=$(jq -r '.title // ""' <<< "$row")
        comments_count=$(jq -r '.comments' <<< "$row")
        executors=$(jq -c '[.labels[] | select(startswith("executor:")) | ltrimstr("executor:")]' <<< "$row")
        review_loop=false
        if [[ "$(jq 'length' <<< "$executors")" -eq 1 ]]; then
            [[ -n "${CODEX_REVIEW_LOOP_ROUTING[$(jq -r '.[0]' <<< "$executors")]:-}" ]] && review_loop=true
        fi
        pr_json=$(jq -c --argjson n "$n" "$INFLIGHT_JQ_DEFS"'linked(.; $n) // empty' <<< "$prs")
        pr_number=$(jq -r '.number // empty' <<< "$pr_json" 2>/dev/null || true)
        pr_head=$(jq -r '.head.sha // empty' <<< "$pr_json" 2>/dev/null || true)
        pr_draft=$(jq -r '.draft // false' <<< "$pr_json" 2>/dev/null || echo false)

        if [[ -z "$pr_number" ]]; then
            merged=$(jq -r --argjson n "$n" 'any(.[]; .merged_at != null
                and ((.body // "") | ascii_downcase | test("(closes|fixes|resolves)[[:space:]]+#\($n)\\b")))' \
                <<< "$closed")
            if [[ "$merged" == "true" ]]; then
                finish+=("$(inflight_row "$n" "$title" "$executors" "" "")")
                continue
            fi
            [[ "$comments_count" -gt 0 ]] || continue
            if story_comments=$(inflight_get_all "repos/$REPO/issues/$n/comments"); then
                if [[ "$(jq -r --arg po "$INFLIGHT_PO" "$INFLIGHT_JQ_DEFS"'
                    def resolves: (.body // "") as $b | ($b | test("(^|\n)## Blocked Declaration") | not)
                        and ($b | test("(^|\n)[ \t]*Unblocked\\b(?![ \t]+when\\b)")
                             or (split("\n") | any(test("^\\s*/unblock\\s*$"))));
                    ([.[] | select(trusted($po) and declaration
                            and ((.body // "") | capture("(^|\n)[ \t]*Type:[ \t]*(?<t>[A-Za-z]+)") | .t | ascii_downcase
                                 | IN("dependency", "external", "conflict", "resource")))
                      | .created_at] | max) as $d
                    | $d != null and (any(.[]; trusted($po) and .created_at > $d and resolves) | not)' \
                    <<< "$story_comments" 2>/dev/null)" == "true" ]]; then
                    flight+=("$n")
                fi
            else
                echo "[IN-FLIGHT] Warning: could not read the comments of #$n; its blockers are not checked in this run" >&2
            fi
            continue
        fi

        if [[ "$pr_draft" == "true" ]]; then
            impl+=("$n")
        fi
        word=""
        if $review_loop && [[ -n "$INFLIGHT_BOT" ]]; then
            if story_comments=$(inflight_get_all "repos/$REPO/issues/$n/comments") \
                    && pr_comments=$(inflight_get_all "repos/$REPO/issues/$pr_number/comments"); then
                word=$(printf '%s\n%s\n%s\n' "$(jq -c '.draft = false | .state = "open"' <<< "$pr_json")" \
                    "$story_comments" "$pr_comments" \
                    | conflict_round_rule "$INFLIGHT_BOT" "$INFLIGHT_BK" "$INFLIGHT_PO") || word=""
                if [[ "$word" == "fix-round-due" ]]; then
                    fix_round+=("$(inflight_row "$n" "$title" "$executors" "$pr_number" "$pr_head")")
                    [[ "$pr_draft" == "true" ]] || impl+=("$n")
                    continue
                fi
                if [[ "$pr_draft" != "true" ]] && [[ "$(jq -r --arg bot "$INFLIGHT_BOT" --arg po "$INFLIGHT_PO" \
                        --arg pend "<!-- hpw:dispatcher action=hand-off-pending pr=$pr_number head=" \
                        --arg done "<!-- hpw:dispatcher action=hand-off-done pr=$pr_number -->" '
                    ([.[] | select((.user.login // "") == $bot and ((.body // "") | contains($pend))) | .created_at] | max) as $t
                    | $t != null
                      and (any(.[]; (.user.login // "") == $po and .created_at > $t
                                    and ((.body // "") | split("\n") | any(test("^\\s*/unblock\\s*$")))) | not)
                      and (any(.[]; (.user.login // "") == $bot and .created_at > $t
                                    and ((.body // "") | contains($done))) | not)' <<< "$story_comments")" == "true" ]]; then
                    hand_off+=("$(inflight_row "$n" "$title" "$executors" "$pr_number" "$pr_head")")
                    impl+=("$n")
                    continue
                fi
            else
                echo "[IN-FLIGHT] Warning: could not read the comments of #$n or PR #$pr_number; no fix round or hand-off is found for it in this run" >&2
            fi
        fi
        if [[ "$pr_draft" == "true" ]]; then
            continue
        fi
        flight+=("$n")
        if inflight_gates_pass "$pr_number" "$pr_head"; then
            merge+=("$(inflight_row "$n" "$title" "$executors" "$pr_number" "$pr_head")")
        else
            review+=("$(inflight_row "$n" "$title" "$executors" "$pr_number" "$pr_head")")
        fi
    done <<< "$stories"

    INFLIGHT_JSON=$(jq -nc \
        --argjson merge "$(printf '%s\n' ${merge[@]+"${merge[@]}"} | jq -sc 'map(select(. != null))')" \
        --argjson finish "$(printf '%s\n' ${finish[@]+"${finish[@]}"} | jq -sc 'map(select(. != null))')" \
        --argjson fix_round "$(printf '%s\n' ${fix_round[@]+"${fix_round[@]}"} | jq -sc 'map(select(. != null))')" \
        --argjson hand_off "$(printf '%s\n' ${hand_off[@]+"${hand_off[@]}"} | jq -sc 'map(select(. != null))')" \
        --argjson review "$(printf '%s\n' ${review[@]+"${review[@]}"} | jq -sc 'map(select(. != null))')" \
        --argjson impl "$(printf '%s\n' ${impl[@]+"${impl[@]}"} | jq -sc 'map(select(. != null) | tonumber) | unique')" \
        --argjson flight "$(printf '%s\n' ${flight[@]+"${flight[@]}"} | jq -sc 'map(select(. != null) | tonumber) | unique')" \
        '{merge: $merge, finish: $finish, fix_round: $fix_round, hand_off: $hand_off, review: $review,
          in_implementation: $impl, in_flight: ($flight - $impl)}') || return 1
}

# inflight_scheduler_lists — sets IN_IMPLEMENTATION_LIST and IN_FLIGHT_LIST (comma-separated
# issue numbers, "" for none) from INFLIGHT_JSON, for scheduler_next.
inflight_scheduler_lists() {
    IN_IMPLEMENTATION_LIST=$(jq -r '.in_implementation | map(tostring) | join(",")' <<< "$INFLIGHT_JSON")
    IN_FLIGHT_LIST=$(jq -r '.in_flight | map(tostring) | join(",")' <<< "$INFLIGHT_JSON")
}
