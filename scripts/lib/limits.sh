#!/usr/bin/env bash
# scripts/lib/limits.sh
#
# The shared check of the WIP and queue limits (issue #358). The PO sets them in
# three repository variables; no script holds a number or a default for them, so
# every script that reads a limit sees the same value:
#
#   WIP_LIMIT_IMPLEMENTATION  most stories in "In implementation" (whole number, 1 or more)
#   READY_BUFFER_MIN          fewest stories kept in "Ready for implementation" (0 or more)
#   READY_BUFFER_MAX          most stories kept in "Ready for implementation" (MIN or more)
#
# Source this file and call load_limits. On success it sets the three variables
# to their decimal values ("08" becomes 8). On a missing, empty or invalid value
# it logs one [LIMITS ERROR] line per problem to stderr, names the variable and
# the value, and returns 1; the caller starts nothing, like [USAGE LIMIT ERROR].
#
# Requires: bash 4+

load_limits() {
    local failed=false name value
    for name in WIP_LIMIT_IMPLEMENTATION READY_BUFFER_MIN READY_BUFFER_MAX; do
        value="${!name-}"
        if [[ -z "$value" ]]; then
            echo "[LIMITS ERROR] $name is not set or is empty — starting nothing" >&2
            failed=true
        elif [[ ! "$value" =~ ^[0-9]+$ ]]; then
            echo "[LIMITS ERROR] $name is not a whole number (value: \"$value\") — starting nothing" >&2
            failed=true
        else
            value=$((10#$value))
            if [[ "$name" == "WIP_LIMIT_IMPLEMENTATION" && $value -lt 1 ]]; then
                echo "[LIMITS ERROR] $name must be 1 or more (value: \"${!name}\") — starting nothing" >&2
                failed=true
            else
                printf -v "$name" '%s' "$value"
            fi
        fi
    done
    $failed && return 1
    if (( READY_BUFFER_MIN > READY_BUFFER_MAX )); then
        echo "[LIMITS ERROR] READY_BUFFER_MIN ($READY_BUFFER_MIN) is greater than READY_BUFFER_MAX ($READY_BUFFER_MAX) — starting nothing" >&2
        return 1
    fi
    return 0
}
