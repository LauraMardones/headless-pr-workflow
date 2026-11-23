#!/usr/bin/env bash
# scripts/lib/conflict-round.sh
#
# The one rule that decides what a review-loop story's linked PR needs
# (issue #449). scripts/dispatcher-poll.sh and the resume pass of
# scripts/dispatcher-invoke.sh both call it, so they cannot disagree. A stopped
# conflict round is never resumed: the rule reads only GitHub facts as they are
# now, and nothing records an open round.
#
# conflict_round_rule <dispatcher-login> <bookkeeping-login> <po-login>
#   Reads three JSON values on stdin, in this order: the PR as GitHub returns
#   it from repos/<repo>/pulls/<n>, the story's comments (one array, all
#   authors) and the PR's conversation comments (one array, all authors).
#   Prints one word:
#     not-open       the PR is closed or a draft
#     fix-round-due  the bookkeeping job's move-in-implementation marker names
#                    the current head and no fix-round attempt marker holds for
#                    it (no marker, a later PO /unblock, or a later findings
#                    marker supersedes it)
#     unknown        GitHub has not computed mergeability yet (mergeable null)
#     clean          not conflicting; the head has merge-owner evidence
#     ownerless      not conflicting; the head has no merge-owner evidence
#                    (the dispatcher's merge-owner marker or its
#                    evidence-pending copy, issue #348), and the dispatcher
#                    has not yet posted the missing-merge-owner blocker for it
#     ownerless-reported
#                    as ownerless, but that blocker is already posted for the
#                    head (it is posted once per head, even after /unblock)
#     tried          a conflict-round attempt marker holds for the current
#                    head: the PR conflicts, or it is clean but its head has no
#                    merge-owner evidence (the round's blocker already alerted
#                    the PO, for example for a pytest check that was not green)
#     blocked        conflicting; the story has an open blocker
#     owed           conflicting, and none of the above: a conflict round is owed
#   Open blocker: a "## Blocked Declaration" by the dispatcher, the PO, a
#   repository member or a Bot, with no later standalone `/unblock` line by the
#   PO. A dispatcher blocker whose hidden marker names a different PR head is
#   not open: it belongs to an older head (the marker_holds rule), so a new
#   head allows a new attempt (issue #409 AC 6).
#   Markers count only on comments of the dispatcher's own account; the
#   findings marker only on comments of the bookkeeping job's account.
#   Whether a story marker holds (the conflict-round and fix-round attempt
#   markers) is decided by the one marker-holds rule, `hpw dispatcher marker
#   decide` (marker.py, issue #628): the newest marker by created_at holds
#   unless a later standalone PO /unblock line follows it. When that call
#   fails, the rule prints no word and fails, which both callers read as
#   unreadable.

# conflict_marker_decide <login> <po-login> <marker>
#   Reads the story's comments on stdin and prints the `marker decide` JSON
#   line when it decides (holds or not); fails on a read or argument error.
conflict_marker_decide() {
    local out rc
    out=$("${HPW_BIN:-$(dirname "${BASH_SOURCE[0]}")/../hpw}" dispatcher marker decide \
        --login "$1" --po-login "$2" --marker "$3")
    rc=$?
    [[ $rc -eq 0 || $rc -eq 1 ]] || return 1
    printf '%s\n' "$out"
}

conflict_round_rule() {
    local input n head story tried fix_attempt
    input=$(jq -cs '.') || return 1
    if jq -e '(.[0].state // "open") != "open" or (.[0].draft // false)' <<< "$input" >/dev/null; then
        echo "not-open"
        return 0
    fi
    n=$(jq -r '.[0].number | tostring' <<< "$input") || return 1
    head=$(jq -r '(.[0].head.sha // "") | ascii_downcase' <<< "$input") || return 1
    story=$(jq -c '.[1] // []' <<< "$input") || return 1
    tried=$(conflict_marker_decide "$1" "$3" \
        "<!-- hpw:dispatcher action=conflict-round-attempt pr=$n head=$head -->" <<< "$story") || return 1
    fix_attempt=$(conflict_marker_decide "$1" "$3" \
        "<!-- hpw:dispatcher action=fix-round-attempt pr=$n head=$head -->" <<< "$story") || return 1
    jq -r --arg bot "$1" --arg bk "$2" --arg po "$3" --argjson tried "$tried" --argjson attempt "$fix_attempt" '
        .[0] as $pr | (.[1] // []) as $story | (.[2] // []) as $prc
        | (($pr.head.sha // "") | ascii_downcase) as $head
        | ($pr.number | tostring) as $n
        | def unblocked_after($t):
            any($story[]; (.user.login // "") == $po and .created_at > $t
                and ((.body // "") | split("\n") | any(test("^\\s*/unblock\\s*$"))));
          def newest($list; $login; $marker):
            [$list[] | select((.user.login // "") == $login
                              and ((.body // "") | ascii_downcase | contains($marker)))
             | .created_at] | max;
          def stale_for_head:
            (.user.login // "") == $bot
            and ([(.body // "") | match("<!-- hpw:dispatcher [^>]*\\bhead=([0-9a-fA-F]+)"; "g")
                  | .captures[0].string | ascii_downcase] as $heads
                 | ($heads | length) > 0 and (any($heads[]; . == $head) | not));
          def open_blocker:
            any($story[];
                ((.body // "") | test("(^|\n)## Blocked Declaration"))
                and ((.user.login // "") == $bot or (.user.login // "") == $po
                     or ((.author_association // "") | IN("OWNER", "MEMBER", "COLLABORATOR"))
                     or (.user.type // "") == "Bot")
                and (stale_for_head | not)
                and (unblocked_after(.created_at) | not));
          newest($prc; $bk; "<!-- hpw:codex-review-bookkeeping action=move-in-implementation head=" + $head + " ") as $moves
        | ($moves != null and ($attempt.result != "holds" or $moves > $attempt.created_at)) as $fix_due
        | (any($prc[]; (.user.login // "") == $bot and ((.body // "") | ascii_downcase
              | contains("<!-- hpw:dispatcher action=merge-owner pr=" + $n + " head=" + $head + " -->")
                or contains("<!-- hpw:dispatcher action=evidence-pending item=merge-owner head=" + $head + " ")))) as $owned
        | if $fix_due then "fix-round-due"
          elif ($pr.mergeable == false) or ($pr.mergeable_state == "dirty") then
              if $tried.result == "holds" then "tried"
              elif open_blocker then "blocked"
              else "owed" end
          elif $pr.mergeable == null then "unknown"
          elif $owned then "clean"
          elif $tried.result == "holds" then "tried"
          elif newest($story; $bot; "<!-- hpw:dispatcher action=merge-owner-missing pr=" + $n + " head=" + $head + " -->") != null
              then "ownerless-reported"
          else "ownerless" end' <<< "$input"
}
