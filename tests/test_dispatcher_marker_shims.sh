#!/usr/bin/env bash
# Tests for the Block 2 shims in scripts/dispatcher-invoke.sh (issue #433, ADR-014).
# The real marker_block, marker_holds and resolve_dispatcher_login are extracted
# from the script (it cannot be sourced) and run against a fake HPW_BIN.
set -uo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
SCRIPT="$REPO_ROOT/scripts/dispatcher-invoke.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
passes=0; failures=0
pass() { printf 'PASS: %s\n' "$1"; passes=$((passes + 1)); }
fail() { printf 'FAIL: %s\n' "$1"; failures=$((failures + 1)); }

command -v jq >/dev/null 2>&1 || { echo 'SKIP: jq not available'; exit 0; }

extract() { sed -n "/^$1() {/,/^}/p" "$SCRIPT"; }

cat >"$TMP/hpw" <<'FAKE'
#!/usr/bin/env bash
# fake hpw: `dispatcher marker holds` exits with $FAKE_HOLDS_RC; whoami with $FAKE_WHOAMI_RC.
echo "$*" >> "$FAKE_LOG"
case "$3" in
  holds) echo '{"ok": false, "block": "marker", "operation": "holds", "result": "x", "error": "boom"}'; exit "${FAKE_HOLDS_RC:-0}" ;;
  whoami) [[ "${FAKE_WHOAMI_RC:-0}" -eq 0 ]] && echo '{"dispatcher": "bot"}'; exit "${FAKE_WHOAMI_RC:-0}" ;;
esac
FAKE
chmod +x "$TMP/hpw"

run() {  # run <holds-rc> <body...>
    local rc=$1; shift
    : >"$TMP/log"
    ( export REPO=o/r HPW_BIN="$TMP/hpw" FAKE_LOG="$TMP/log" FAKE_HOLDS_RC=$rc DECISION_BLOCKER_PO_LOGIN=po
      DISPATCHER_LOGIN="" CODEX_BOOKKEEPING_LOGIN=""
      eval "$(extract marker_block)"; eval "$(extract marker_holds)"; eval "$(extract resolve_dispatcher_login)"
      "$@" ) 2>"$TMP/err"
}

run 0 marker_holds 5 "m" 0; [[ $? -eq 0 ]] && pass 'holds (exit 0) holds' || fail 'holds (exit 0) holds'
run 1 marker_holds 5 "m" 0; [[ $? -eq 1 ]] && pass 'does not hold (exit 1) is 1' || fail 'does not hold (exit 1) is 1'
run 3 marker_holds 5 "m" 0; [[ $? -eq 0 ]] && pass 'unreadable with on-read-error 0 holds (a caller that stops on a held marker)' || fail 'unreadable with 0'
run 3 marker_holds 5 "m" 1; [[ $? -eq 1 ]] && pass 'unreadable with on-read-error 1 does not hold (a caller that acts on a held marker)' || fail 'unreadable with 1'
run 2 marker_holds 5 "m" 1; [[ $? -eq 1 ]] && pass 'invalid-argument exit from the block is a read error' || fail 'exit 2 from the block'
run 0 marker_holds 5 "m"; [[ $? -eq 2 ]] && pass 'no <on-read-error> is a usage error (exit 2)' || fail 'missing <on-read-error>'
grep -q 'on-read-error' "$TMP/err" && pass 'usage error names the missing argument' || fail 'usage error names the argument'
[[ ! -s "$TMP/log" ]] && pass 'usage error calls no block' || fail 'usage error calls no block'
run 0 marker_holds 5 "m" 2; [[ $? -eq 2 ]] && pass 'on-read-error must be 0 or 1' || fail 'on-read-error 2'
run 0 marker_holds 5 "m" 0 "cancel"; grep -q -- '--cancel=cancel' "$TMP/log" && pass 'cancel marker is passed to the block' || fail 'cancel marker is passed'
run 0 marker_holds 5 "<!-- x -->" 0; grep -q -- '--marker=<!-- x -->' "$TMP/log" && pass 'marker is passed as --marker=<text>' || fail 'marker is passed'

( export REPO=o/r HPW_BIN="$TMP/hpw" FAKE_LOG="$TMP/log" FAKE_WHOAMI_RC=3 DISPATCHER_LOGIN="" CODEX_BOOKKEEPING_LOGIN=""
  eval "$(extract marker_block)"; eval "$(extract resolve_dispatcher_login)"; resolve_dispatcher_login ) 2>/dev/null
[[ $? -ne 0 ]] && pass 'an unresolvable login fails resolve_dispatcher_login' || fail 'unresolvable login'
( export REPO=o/r HPW_BIN="$TMP/hpw" FAKE_LOG="$TMP/log" DISPATCHER_LOGIN="" CODEX_BOOKKEEPING_LOGIN=""
  eval "$(extract marker_block)"; eval "$(extract resolve_dispatcher_login)"; resolve_dispatcher_login && [[ "$DISPATCHER_LOGIN" == bot ]] ) 2>/dev/null
[[ $? -eq 0 ]] && pass 'resolve_dispatcher_login reads whoami' || fail 'resolve_dispatcher_login reads whoami'

# Every call site passes <on-read-error> explicitly.
bad=$(grep -n 'marker_holds "' "$SCRIPT" | grep -vE 'marker_holds "[^"]*" "([^"]|"[^"]*")*" [01]( |;|\\|$)|^[0-9]+:#' | grep -v 'marker_holds()' || true)
[[ -z "$bad" ]] && pass 'every marker_holds call passes <on-read-error>' || fail "marker_holds call without <on-read-error>: $bad"

printf '%d passed, %d failed\n' "$passes" "$failures"
[[ $failures -eq 0 ]]
