#!/usr/bin/env bash
# Fixture tests for scripts/dispatcher-change-check.sh (issue #212).
set -uo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
CHECKER="$REPO_ROOT/scripts/dispatcher-change-check.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
passes=0
failures=0

pass() { printf 'PASS: %s\n' "$1"; passes=$((passes + 1)); }
fail() { printf 'FAIL: %s\n' "$1"; failures=$((failures + 1)); }

run_check() {
    local out=$1 err=$2
    shift 2
    set +e
    "$CHECKER" "$@" >"$out" 2>"$err"
    RUN_RC=$?
    set -e
}

assert_rc() { local name=$1 expected=$2; [[ $RUN_RC -eq $expected ]] && pass "$name" || fail "$name (expected $expected, got $RUN_RC)"; }
assert_has() { local name=$1 file=$2 text=$3; grep -Fq -- "$text" "$file" && pass "$name" || fail "$name (missing: $text)"; }
assert_lines() { local name=$1 file=$2 expected=$3; local actual; actual=$(wc -l <"$file" | tr -d ' '); [[ $actual -eq $expected ]] && pass "$name" || fail "$name (expected $expected lines, got $actual)"; }

write_clean() {
    local file=$1
    cat >"$file" <<'FIXTURE'
#!/usr/bin/env bash
DRY_RUN=false
DISPATCH_HANDLED=false
notify_slack() {
    if $DRY_RUN; then
        echo '{"dry_run":true}'
    else
        bash "$(dirname "$0")/slack-notify.sh" event '{}' || true
    fi
}
if blocker_condition; then
    # expected blocker path
    DISPATCH_HANDLED=true
    exit 1
fi
FIXTURE
    chmod +x "$file"
}

# Explicit selection: clean file exercises all five checks.
clean="$TMP/dispatcher-clean.sh"
write_clean "$clean"
run_check "$TMP/out" "$TMP/err" --files "$clean"
assert_rc 'clean fixture exits zero' 0
assert_lines 'clean fixture emits five results' "$TMP/out" 5
for id in executable slack-guard dry-run stdout-json dispatch-handled; do
    assert_has "clean fixture passes $id" "$TMP/out" "PASS $clean $id -"
done

# Whitespace survives array handling as a single path.
spaced="$TMP/dispatcher-with space.sh"
write_clean "$spaced"
run_check "$TMP/out" "$TMP/err" --files "$spaced"
assert_rc 'path containing whitespace exits zero' 0
assert_has 'path containing whitespace is preserved' "$TMP/out" "PASS $spaced executable -"
assert_lines 'path containing whitespace still emits five results' "$TMP/out" 5

# Missing failure guard.
unguarded="$TMP/dispatcher-unguarded.sh"
write_clean "$unguarded"
sed -i 's/ || true$//' "$unguarded"
run_check "$TMP/out" "$TMP/err" --files "$unguarded"
assert_rc 'unguarded call exits one' 1
assert_has 'unguarded call is reported' "$TMP/out" "FAIL $unguarded slack-guard -"

# A Slack call directly in the dry-run arm is not suppressed.
broken_dry="$TMP/dispatcher-broken-dry.sh"
cat >"$broken_dry" <<'FIXTURE'
#!/usr/bin/env bash
DRY_RUN=true
notify_slack() {
    if $DRY_RUN; then
        bash ./slack-notify.sh event '{}' || true
    fi
}
FIXTURE
chmod +x "$broken_dry"
run_check "$TMP/out" "$TMP/err" --files "$broken_dry"
assert_rc 'broken dry-run exits one' 1
assert_has 'broken dry-run is reported' "$TMP/out" "FAIL $broken_dry dry-run -"

# Untracked fixture without executable mode.
not_exec="$TMP/dispatcher-not-executable.sh"
printf '#!/usr/bin/env bash\n' >"$not_exec"
chmod 644 "$not_exec"
run_check "$TMP/out" "$TMP/err" --files "$not_exec"
assert_rc 'non-executable fixture exits one' 1
assert_has 'non-executable fixture is reported' "$TMP/out" "FAIL $not_exec executable -"

# Expected failure must mark dispatch handled.
unhandled="$TMP/dispatcher-unhandled.sh"
cat >"$unhandled" <<'FIXTURE'
#!/usr/bin/env bash
DISPATCH_HANDLED=false
if blocker_condition; then
    # expected blocker path
    exit 1
fi
FIXTURE
chmod +x "$unhandled"
run_check "$TMP/out" "$TMP/err" --files "$unhandled"
assert_rc 'unhandled expected exit exits one' 1
assert_has 'unhandled expected exit is reported' "$TMP/out" "FAIL $unhandled dispatch-handled -"

# Non-JSON literal stdout in a notification function.
bad_stdout="$TMP/dispatcher-bad-stdout.sh"
cat >"$bad_stdout" <<'FIXTURE'
#!/usr/bin/env bash
DRY_RUN=false
notify_slack() {
    if $DRY_RUN; then
        return 0
    fi
    echo 'not json'
    bash ./slack-notify.sh event '{}' || true
}
FIXTURE
chmod +x "$bad_stdout"
run_check "$TMP/out" "$TMP/err" --files "$bad_stdout"
assert_rc 'invalid notification stdout exits one' 1
assert_has 'invalid notification stdout is reported' "$TMP/out" "FAIL $bad_stdout stdout-json -"

# Non-applicable checks are SKIP, while executable still applies.
minimal="$TMP/dispatcher-minimal.sh"
printf '#!/usr/bin/env bash\necho harmless >&2\n' >"$minimal"
chmod +x "$minimal"
run_check "$TMP/out" "$TMP/err" --files "$minimal"
assert_rc 'minimal dispatcher exits zero' 0
assert_has 'slack guard can skip' "$TMP/out" "SKIP $minimal slack-guard -"
assert_has 'dispatch handled can skip' "$TMP/out" "SKIP $minimal dispatch-handled -"

# An unrelated changed set has the exact five global SKIPs.
printf 'notes\n' >"$TMP/readme.txt"
run_check "$TMP/out" "$TMP/err" --files "$TMP/readme.txt"
assert_rc 'no-op file set exits zero' 0
assert_lines 'no-op file set emits five global results' "$TMP/out" 5
[[ $(grep -c '^SKIP - ' "$TMP/out") -eq 5 ]] && pass 'no-op results are all SKIP' || fail 'no-op results are not all SKIP'

# Input and discovery errors are distinct from policy failures.
run_check "$TMP/out" "$TMP/err" --files
assert_rc 'empty --files exits two' 2
assert_has 'empty --files diagnoses stderr' "$TMP/err" '--files requires at least one path'
run_check "$TMP/out" "$TMP/err" --unknown
assert_rc 'unknown option exits two' 2
run_check "$TMP/out" "$TMP/err" --files "$TMP/dispatcher-missing.sh"
assert_rc 'missing eligible file exits two' 2
assert_has 'missing eligible file diagnoses stderr' "$TMP/err" 'eligible file is missing or unreadable'

# Default discovery uses git diff --name-only main and tracked Git mode 100755.
gitrepo="$TMP/repo"
mkdir "$gitrepo"
(
    cd "$gitrepo"
    git init -q -b main
    git config user.email test@example.com
    git config user.name Test
    mkdir scripts
    write_clean scripts/dispatcher-default.sh
    git add scripts/dispatcher-default.sh
    git commit -qm base
    printf '# changed\n' >>scripts/dispatcher-default.sh
    run_check "$TMP/out" "$TMP/err"
    printf '%s' "$RUN_RC" >"$TMP/default-rc"
)
RUN_RC=$(cat "$TMP/default-rc")
assert_rc 'default Git discovery exits zero' 0
assert_has 'default Git discovery finds changed dispatcher' "$TMP/out" 'PASS scripts/dispatcher-default.sh executable - Git mode is 100755'

nogit="$TMP/not-a-repo"
mkdir "$nogit"
(
    cd "$nogit"
    run_check "$TMP/out" "$TMP/err"
    printf '%s' "$RUN_RC" >"$TMP/nogit-rc"
)
RUN_RC=$(cat "$TMP/nogit-rc")
assert_rc 'failed diff discovery exits two' 2
assert_has 'failed diff discovery diagnoses stderr' "$TMP/err" "cannot resolve Git ref 'main'"

# Help is successful and remains on stdout.
run_check "$TMP/out" "$TMP/err" --help
assert_rc 'help exits zero' 0
assert_has 'help documents usage' "$TMP/out" 'Usage: bash scripts/dispatcher-change-check.sh'

printf '\n%d passed, %d failed\n' "$passes" "$failures"
[[ $failures -eq 0 ]]
