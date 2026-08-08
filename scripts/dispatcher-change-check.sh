#!/usr/bin/env bash
# Deterministic, read-only checks for dispatcher and Slack shell changes.
#
# Supported static patterns:
# - Slack calls are executable logical commands containing a path ending in
#   slack-notify.sh; comments and the adapter itself are excluded.
# - A call is dry-run safe when it is in the else arm of an if containing
#   DRY_RUN, or follows a DRY_RUN branch that returns before the call.
# - stdout-json inspects echo/printf commands in a function containing a Slack
#   call. Stderr writes are exempt.
# - dispatch-handled inspects literal `exit 1` paths marked expected/blocker in
#   their preceding context. Helper/unexpected failure exits are excluded.
#
# Results: PASS|FAIL <path-or-> <check-id> - <reason>
# Exit 0: all applicable checks pass (including all-SKIP)
# Exit 1: at least one policy check fails
# Exit 2: invalid arguments, unreadable input, or diff discovery failure

set -uo pipefail

usage() {
    cat <<'USAGE'
Usage: bash scripts/dispatcher-change-check.sh [--files <file1> [file2 ...]]

Checks changed dispatcher-*.sh and slack-notify.sh files. With no --files,
paths come from `git diff --name-only main`.

Exit codes: 0 checks pass/all skip; 1 policy failure; 2 input error.
USAGE
}

die() { printf 'dispatcher-change-check: %s\n' "$*" >&2; exit 2; }

CHECKS=(executable slack-guard dry-run stdout-json dispatch-handled)
files=()
case "${1-}" in
    --help) [[ $# -eq 1 ]] || die '--help does not accept arguments'; usage; exit 0 ;;
    --files) shift; [[ $# -gt 0 ]] || die '--files requires at least one path'; files=("$@") ;;
    '')
        mapfile -t files < <(git diff --name-only main) || die 'could not discover changed files from main'
        # Process substitution hides command status, so independently verify the ref/diff.
        git rev-parse --verify main >/dev/null 2>&1 || die "cannot resolve Git ref 'main'"
        git diff --quiet main -- . >/dev/null 2>&1
        rc=$?
        [[ $rc -le 1 ]] || die 'could not discover changed files from main'
        ;;
    *) die "unknown option or argument: ${1}" ;;
esac

eligible=()
for file in "${files[@]}"; do
    base=${file##*/}
    if [[ $base == dispatcher-*.sh || $base == slack-notify.sh ]]; then
        [[ -f $file && -r $file ]] || die "eligible file is missing or unreadable: $file"
        eligible+=("$file")
    fi
done

if [[ ${#eligible[@]} -eq 0 ]]; then
    for check in "${CHECKS[@]}"; do
        printf 'SKIP - %s - no eligible dispatcher or Slack files\n' "$check"
    done
    exit 0
fi

failed=0
result() {
    local status=$1 file=$2 check=$3 reason=$4
    printf '%s %s %s - %s\n' "$status" "$file" "$check" "$reason"
    [[ $status != FAIL ]] || failed=1
}

# Join backslash-continued source lines. This is intentionally not a full parser.
logical_source() {
    awk '
      { sub(/\r$/, "") }
      pending { line=line $0; pending=0 }
      !pending { line=$0 }
      /\\[[:space:]]*$/ { sub(/\\[[:space:]]*$/, "", line); pending=1; next }
      { print line; line="" }
      END { if (line != "") print line }
    ' "$1"
}

for file in "${eligible[@]}"; do
    base=${file##*/}

    mode=$(git ls-files --stage -- "$file" 2>/dev/null | awk 'NR==1 {print $1}')
    if [[ -n $mode ]]; then
        [[ $mode == 100755 ]] && result PASS "$file" executable 'Git mode is 100755' \
            || result FAIL "$file" executable "Git mode is ${mode}, expected 100755"
    elif [[ -x $file ]]; then
        result PASS "$file" executable 'untracked fixture is executable'
    else
        result FAIL "$file" executable 'untracked file is not executable'
    fi

    tmp=$(mktemp)
    logical_source "$file" >"$tmp"
    calls=$(awk '
      /^[[:space:]]*#/ { next }
      /(^|[[:space:]"'"''])([^[:space:]"'"'']*\/)?slack-notify\.sh([[:space:]"'"'']|$)/ { print NR ":" $0 }
    ' "$tmp")
    if [[ $base == slack-notify.sh || -z $calls ]]; then
        result SKIP "$file" slack-guard 'no direct Slack adapter calls'
    elif awk -F: '{ sub(/^[^:]*:/, ""); if ($0 !~ /\|\|[[:space:]]*true([[:space:]]*(#.*)?)?$/) bad=1 } END { exit bad }' <<<"$calls"; then
        result PASS "$file" slack-guard 'all direct Slack calls end with || true'
    else
        result FAIL "$file" slack-guard 'a direct Slack call is missing same-command || true'
    fi

    if [[ $base == slack-notify.sh || -z $calls ]]; then
        result SKIP "$file" dry-run 'no dispatcher Slack call to suppress'
    elif ! grep -q 'DRY_RUN' "$tmp"; then
        result SKIP "$file" dry-run 'file has no dry-run path'
    elif awk '
      { lines[NR]=$0 }
      /slack-notify\.sh/ && $0 !~ /^[[:space:]]*#/ { call[NR]=1 }
      END {
        for (n in call) {
          safe=0; seen_else=0
          for (i=n-1; i>=1 && i>=n-40; i--) {
            if (lines[i] ~ /^[[:space:]]*else([[:space:]]|$)/) seen_else=1
            if (lines[i] ~ /^[[:space:]]*if .*DRY_RUN/) {
              if (seen_else) safe=1
              for (j=i+1; j<n; j++) if (lines[j] ~ /^[[:space:]]*(return|exit)([[:space:]]|$)/) safe=1
              break
            }
            if (lines[i] ~ /^[[:space:]]*}[[:space:]]*$/) break
          }
          if (!safe) bad=1
        }
        exit bad
      }
    ' "$tmp"; then
        result PASS "$file" dry-run 'all Slack calls are suppressed by a supported dry-run guard'
    else
        result FAIL "$file" dry-run 'Slack suppression cannot be established for the supported dry-run patterns'
    fi

    if [[ $base == slack-notify.sh || -z $calls ]]; then
        result SKIP "$file" stdout-json 'no notification path with a direct Slack call'
    elif awk '
      function inspect(   i) {
        if (!has_call) return
        applicable=1
        for (i=start; i<=end; i++) {
          if (src[i] ~ /^[[:space:]]*(echo|printf)([[:space:]]|$)/ &&
              src[i] !~ /({|>&2|1>&2|\/dev\/stderr)/) bad=1
        }
      }
      { src[NR]=$0 }
      /^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*[[:space:]]*\(\)[[:space:]]*\{/ { inspect(); start=NR; has_call=0 }
      start && /slack-notify\.sh/ && $0 !~ /^[[:space:]]*#/ { has_call=1 }
      start && /^[[:space:]]*}[[:space:]]*$/ { end=NR; inspect(); start=0; has_call=0 }
      END { if (start) { end=NR; inspect() }; if (!applicable) exit 2; exit bad }
    ' "$tmp"; then
        result PASS "$file" stdout-json 'notification-path stdout literals contain {'
    else
        rc=$?
        if [[ $rc -eq 2 ]]; then
            result SKIP "$file" stdout-json 'no supported notification function contains stdout output'
        else
            result FAIL "$file" stdout-json 'notification-path stdout literal does not contain {'
        fi
    fi

    if ! grep -q 'DISPATCH_HANDLED' "$tmp"; then
        result SKIP "$file" dispatch-handled 'file does not use DISPATCH_HANDLED policy'
    else
        set +e
        awk '
          { src[NR]=$0 }
          END {
            for (n=1; n<=NR; n++) if (src[n] ~ /^[[:space:]]*exit[[:space:]]+1([[:space:]]*(#.*)?)?$/) {
              expected=0; handled=0
              for (i=n-1; i>=1 && i>=n-12; i--) {
                low=tolower(src[i])
                if (low ~ /(expected|blocker)/) expected=1
                if (src[i] ~ /DISPATCH_HANDLED[[:space:]]*=[[:space:]]*true/) handled=1
                if (src[i] ~ /^[[:space:]]*(else|fi|}[[:space:]]*)$/) break
              }
              if (expected) { applicable=1; if (!handled) bad=1 }
            }
            if (!applicable) exit 2
            exit bad
          }
        ' "$tmp"
        rc=$?
        set -e
        if [[ $rc -eq 0 ]]; then
            result PASS "$file" dispatch-handled 'expected exit 1 paths set DISPATCH_HANDLED=true'
        elif [[ $rc -eq 2 ]]; then
            result SKIP "$file" dispatch-handled 'no expected literal exit 1 paths matched'
        else
            result FAIL "$file" dispatch-handled 'an expected exit 1 path lacks DISPATCH_HANDLED=true'
        fi
    fi
    rm -f "$tmp"
done

exit "$failed"
