#!/usr/bin/env bash
# scripts/lib/limits.sh
#
# The shared check of the WIP and queue limits (issue #358). The PO sets them in
# three repository variables; no script holds a number or a default for them, so
# every script that reads a limit sees the same value:
#
#   WIP_LIMIT_IMPLEMENTATION  most stories in "In implementation" (whole number, 1 or more)
#   READY_BUFFER_MIN          fewest stories kept in "Ready to implement" (0 or more)
#   READY_BUFFER_MAX          most stories kept in "Ready to implement" (MIN or more)
#
# Source this file and call read_limits. On success it sets the three variables
# to their decimal values ("08" becomes 8). On a missing, empty or invalid value
# it logs one [LIMITS ERROR] line per problem to stderr, names the variable and
# the value, and returns 1; the caller starts nothing, like [USAGE LIMIT ERROR].
#
# The check itself is `load_limits` in src/headless_pr_workflow/dispatcher/scheduler.py
# (issue #557); `hpw dispatcher admission limits` runs it and this function only
# reads its JSON. There is no rule here.
#
# Requires: bash 4+, jq, HPW_BIN (defaults to scripts/hpw)

read_limits() {
    local hpw="${HPW_BIN:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/hpw}" out
    # The error lines go to this function's stderr; a failure to run or read
    # the result fails closed like an invalid value.
    out=$("$hpw" dispatcher admission limits) || return 1
    WIP_LIMIT_IMPLEMENTATION=$(jq -er '.limits.WIP_LIMIT_IMPLEMENTATION' <<< "$out") \
        && READY_BUFFER_MIN=$(jq -er '.limits.READY_BUFFER_MIN' <<< "$out") \
        && READY_BUFFER_MAX=$(jq -er '.limits.READY_BUFFER_MAX' <<< "$out") \
        || { echo "[LIMITS ERROR] the limits could not be read — starting nothing" >&2; return 1; }
    return 0
}
