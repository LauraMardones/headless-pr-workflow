#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HPW_BIN="${HPW_BIN:-$SCRIPT_DIR/hpw}"

usage() {
    cat <<'EOF'
Usage: session-summary.sh --command <implement|review|merge|cleanup> \
  [--issue <N>] [--pr <N>] [--head <sha>] \
  --checks <text> --blockers <text> [--next <action>] \
  [--next-step <line>] [--deviation <text>]...
  --next for implement: review (default), implementation or finding
  (finding: no PR, the result is a finding; --head names the commit worked from)
  --next-step (implement and review only): the PO's `Next step: ...` line made by
  `hpw next-step render`; printed as its own line after Next:
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
next_step=""
deviations=()

while [[ $# -gt 0 ]]; do
    flag="$1"
    case "$flag" in
        --help)
            usage
            exit 0
            ;;
        --command|--issue|--pr|--head|--checks|--blockers|--next|--next-step)
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
                --next-step) next_step="$2" ;;
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
        allowed_next="review implementation finding"
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

if [[ -n "$next_step" ]]; then
    case "$command_value" in
        implement|review) ;;
        *) fail "--next-step is not valid for $command_value." ;;
    esac
    [[ "$next_step" != *$'\n'* ]] || fail "--next-step must be one line."
    [[ "$next_step" == "Next step: "* ]] || \
        fail "--next-step must start with 'Next step: ' (a lettered 'Next step <letter>:' line is not accepted)."
    if ! check_err=$("$HPW_BIN" next-step check --line "$next_step" 2>&1 >/dev/null); then
        fail "--next-step is rejected: ${check_err:-hpw next-step check failed}"
    fi
fi

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
[[ -z "$next_step" ]] || printf '%s\n' "$next_step"

if [[ ${#deviations[@]} -gt 0 ]]; then
    deviation_text="${deviations[0]}"
    for ((i = 1; i < ${#deviations[@]}; i++)); do
        deviation_text+="; ${deviations[$i]}"
    done
    printf 'Deviation: %s\n' "$deviation_text"
fi
