#!/usr/bin/env bash
# scripts/lib/claude-session.sh
#
# Shared helper for every Claude Code process the dispatcher starts on the
# PO's subscription token (ADR-012, issue #314): the allowance probe in
# scripts/dispatcher-allowance.sh and the sessions started by
# invoke_executor_command() in scripts/dispatcher-invoke.sh.
#
# Source it; it defines functions only and changes no shell options.
#
#   claude_clean_token <value>
#       Prints <value> with every whitespace character removed. A secret saved
#       with a trailing space or line break otherwise fails with an
#       unexplained 401 (ADR-012 trial).
#
#   claude_scrubbed_exec <clean-token> <command> [args...]
#       Runs <command> with CLAUDE_CODE_OAUTH_TOKEN set to <clean-token> and
#       with every environment variable whose name starts with ANTHROPIC_
#       removed, so Claude Code can only sign in with the subscription token:
#       no metered key, no other auth token and no redirected base URL.
#       Returns the command's exit code.
#
# The scrub works on the name prefix, so no key name is written in scripts/
# (the F308.2 test in tests/test_dispatcher_metered_path_removed.py checks that).

claude_clean_token() {
    printf '%s' "${1:-}" | tr -d '[:space:]'
}

claude_scrubbed_exec() {
    local token="$1"; shift
    local -a unset_args=()
    local name
    while IFS= read -r name; do
        [[ -n "$name" ]] && unset_args+=(-u "$name")
    done < <(compgen -e -X '!ANTHROPIC_*' || true)
    env "${unset_args[@]}" CLAUDE_CODE_OAUTH_TOKEN="$token" "$@"
}

#   claude_limit_stop <events.jsonl>
#       Decides whether a finished session was stopped by a provider usage
#       limit (issue #316). <events.jsonl> holds one stream-json object per
#       line. Returns 0 for a limit stop and prints the reset time as Unix
#       seconds on stdout, or nothing when the stream has none; returns 1 for
#       everything else, printing nothing. A limit stop is a final `result`
#       that is not a success and carries `rate_limit` or `rate_limit_error`
#       in `error`, `subtype`, `error_type` or `error_category` (or
#       `api_error_status` 429), or a `rate_limit_event` whose
#       `rate_limit_info.status` is "rejected" when the stream has no
#       successful result. The reset time is `rate_limit_info.resetsAt` of the
#       last rejected event, else the latest `unifiedWindows.*.resetsAt` of a
#       window at or above utilization 1. Used by the dispatcher's sessions;
#       refinement sessions (#319) reuse it.

claude_limit_stop() {
    jq -rs '
        (map(select(.type == "result")) | last) as $res
        | (map(select(.type == "rate_limit_event")) ) as $events
        | ($events | map(select(.rate_limit_info.status? == "rejected")) | last) as $rej
        | (($res != null)
           and ($res.is_error == true or $res.subtype != "success")
           and (([$res.error, $res.subtype, $res.error_type, $res.error_category]
                  | any(. == "rate_limit" or . == "rate_limit_error"))
                or $res.api_error_status == 429)) as $by_result
        | (($rej != null)
           and (($res == null) or ($res.is_error == true or $res.subtype != "success"))) as $by_event
        | if ($by_result or $by_event) then
            ( ( [$rej.rate_limit_info.resetsAt?] | map(select(type == "number")) )
              + ( ($events | last | .rate_limit_info.unifiedWindows? // {})
                  | [.[]? | select(type == "object" and (.utilization | type) == "number" and .utilization >= 1)
                          | .resetsAt | select(type == "number")] | (if length > 0 then [max] else [] end) )
            ) as $r
            | "limit:\($r | first // "")"
          else "none" end' "$1" 2>/dev/null | {
        local out
        read -r out
        case "$out" in
            "limit:"*) printf '%s\n' "${out#limit:}" | sed 's/\.[0-9]*$//'; return 0 ;;
            *) return 1 ;;
        esac
    }
}
