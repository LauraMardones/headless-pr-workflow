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
#                    evidence-pending copy, issue #348)
#     tried          conflicting; a conflict-round attempt marker holds for
#                    the current head
#     blocked        conflicting; the story has an open blocker
#     owed           conflicting, and none of the above: a conflict round is owed
#   Open blocker: a "## Blocked Declaration" by the dispatcher, the PO, a
#   repository member or a Bot, with no later standalone `/unblock` line by the
#   PO. A dispatcher blocker whose hidden marker names a different PR head is
#   not open: it belongs to an older head (the marker_holds rule), so a new
#   head allows a new attempt (issue #409 AC 6).
#   Markers count only on comments of the dispatcher's own account; the
#   findings marker only on comments of the bookkeeping job's account.

conflict_round_rule() {
    jq -rs --arg bot "$1" --arg bk "$2" --arg po "$3" '
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
          def holds($marker):
            newest($story; $bot; $marker) as $t | $t != null and (unblocked_after($t) | not);
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
        | newest($story; $bot; "<!-- hpw:dispatcher action=fix-round-attempt pr=" + $n + " head=" + $head + " -->") as $attempt
        | ($moves != null and ($attempt == null or unblocked_after($attempt) or $moves > $attempt)) as $fix_due
        | (any($prc[]; (.user.login // "") == $bot and ((.body // "") | ascii_downcase
              | contains("<!-- hpw:dispatcher action=merge-owner pr=" + $n + " head=" + $head + " -->")
                or contains("<!-- hpw:dispatcher action=evidence-pending item=merge-owner head=" + $head + " ")))) as $owned
        | if ($pr.state // "open") != "open" or ($pr.draft // false) then "not-open"
          elif $fix_due then "fix-round-due"
          elif ($pr.mergeable == false) or ($pr.mergeable_state == "dirty") then
              if holds("<!-- hpw:dispatcher action=conflict-round-attempt pr=" + $n + " head=" + $head + " -->") then "tried"
              elif open_blocker then "blocked"
              else "owed" end
          elif $pr.mergeable == null then "unknown"
          elif $owned then "clean"
          else "ownerless" end'
}
