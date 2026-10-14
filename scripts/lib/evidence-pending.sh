#!/usr/bin/env bash
# scripts/lib/evidence-pending.sh
#
# The "evidence pending" comment of issue #348. When the dispatcher's merge or
# cleanup phase cannot write a comment that is durable evidence, it writes one
# small comment that carries the exact text as a hidden payload. A later
# dispatcher run (scripts/dispatcher-invoke.sh, evidence_retry) posts that text
# once and then treats the item as done. The comment is trusted only when it
# was written by the dispatcher's own account.
#
# evidence_pending_comment <item> <head|-> <payload>
#   Prints the comment body: one visible line, then the hidden marker
#   `<!-- hpw:dispatcher action=evidence-pending item=<item> head=<head>
#   payload=<base64> -->`. The payload is base64 on one line, so it never holds
#   the `-->` that would end the marker, and the exact text never appears in
#   plain form (a pending comment is therefore never mistaken for the evidence).
#   <head> is the PR head the evidence belongs to, or `-` when it has none.

evidence_pending_comment() {
    local item="$1" head="${2:--}" payload="$3" b64
    b64=$(printf '%s' "$payload" | base64 | tr -d '\n')
    printf '%s\n\n<!-- hpw:dispatcher action=evidence-pending item=%s head=%s payload=%s -->\n' \
        "Dispatcher: a required GitHub comment ($item) could not be written. The dispatcher retries it on a later run; nothing is needed from you unless a blocker is posted." \
        "$item" "$head" "$b64"
}
