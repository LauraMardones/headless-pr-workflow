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
