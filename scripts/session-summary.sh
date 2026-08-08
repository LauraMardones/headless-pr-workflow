#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage: session-summary.sh --command <implement|review|merge|cleanup> \
  [--issue <N>] [--pr <N>] [--head <sha>] \
  --checks <text> --blockers <text> [--next <action>] \
  [--deviation <text>]...
EOF
}

fail() {
    printf 'Error: %s\n' "$1" >&2
    usage >&2
    exit 1
}

require_value() {
    local flag="$1"
    local count="$2"
    local value="${3-}"
    [[ "$count" -ge 2 && -n "$value" && "$value" != --* ]] || \
        fail "$flag requires a non-empty value."
}

declare -A seen=()
command_value=""
issue=""
pr=""
head=""
checks=""
blockers=""
next=""
deviations=()

while [[ $# -gt 0 ]]; do
    flag="$1"
    case "$flag" in
        --help)
            usage
            exit 0
            ;;
        --command|--issue|--pr|--head|--checks|--blockers|--next)
            require_value "$flag" "$#" "${2-}"
            [[ -z "${seen[$flag]:-}" ]] || fail "$flag may be supplied only once."
            seen[$flag]=1
            case "$flag" in
                --command) command_value="$2" ;;
                --issue) issue="$2" ;;
                --pr) pr="$2" ;;
                --head) head="$2" ;;
                --checks) checks="$2" ;;
                --blockers) blockers="$2" ;;
                --next) next="$2" ;;
            esac
            shift 2
            ;;
        --deviation)
            require_value "$flag" "$#" "${2-}"
            deviations+=("$2")
            shift 2
            ;;
        *)
            fail "Unknown flag: $flag"
            ;;
    esac
done

[[ -n "$command_value" ]] || fail "--command is required."
[[ -n "$checks" ]] || fail "--checks is required."
[[ -n "$blockers" ]] || fail "--blockers is required."
[[ -n "$issue" || -n "$pr" ]] || fail "at least one of --issue or --pr is required."

[[ "$issue" != \#* ]] || fail "--issue must be supplied without a leading #."
[[ "$pr" != \#* ]] || fail "--pr must be supplied without a leading #."

case "$command_value" in
    implement)
        default_next="review"
        allowed_next="review"
        ;;
    review)
        default_next="merge"
        allowed_next="merge implementation"
        ;;
    merge)
        default_next="cleanup"
        allowed_next="cleanup"
        ;;
    cleanup)
        default_next="done"
        allowed_next="done"
        ;;
    *)
        fail "invalid --command value: $command_value"
        ;;
esac

if [[ "$command_value" != "cleanup" && -z "$head" ]]; then
    fail "--head is required for $command_value."
fi

next="${next:-$default_next}"
case " $allowed_next " in
    *" $next "*) ;;
    *) fail "--next '$next' is not valid for $command_value." ;;
esac

if [[ -n "$issue" && -n "$pr" ]]; then
    identifier="#${issue} / #${pr}"
elif [[ -n "$issue" ]]; then
    identifier="#${issue}"
else
    identifier="#${pr}"
fi

printf '## Session Summary\n'
printf 'Command: %s\n' "$command_value"
printf 'Issue/PR: %s\n' "$identifier"
[[ -z "$head" ]] || printf 'Head: %s\n' "$head"
printf 'Checks: %s\n' "$checks"
printf 'Blockers: %s\n' "$blockers"
printf 'Next: %s\n' "$next"

if [[ ${#deviations[@]} -gt 0 ]]; then
    deviation_text="${deviations[0]}"
    for ((i = 1; i < ${#deviations[@]}; i++)); do
        deviation_text+="; ${deviations[$i]}"
    done
    printf 'Deviation: %s\n' "$deviation_text"
fi
