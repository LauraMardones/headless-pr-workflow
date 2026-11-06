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

# Explicit selection: clean file exercises all eight checks (verified-write: #432, own-marker: #433, saved-state: #434).
clean="$TMP/dispatcher-clean.sh"
write_clean "$clean"
run_check "$TMP/out" "$TMP/err" --files "$clean"
assert_rc 'clean fixture exits zero' 0
assert_lines 'clean fixture emits eight results' "$TMP/out" 8
for id in executable slack-guard dry-run stdout-json dispatch-handled verified-write own-marker saved-state; do
    assert_has "clean fixture passes $id" "$TMP/out" "PASS $clean $id -"
done

# Whitespace survives array handling as a single path.
spaced="$TMP/dispatcher-with space.sh"
write_clean "$spaced"
run_check "$TMP/out" "$TMP/err" --files "$spaced"
assert_rc 'path containing whitespace exits zero' 0
assert_has 'path containing whitespace is preserved' "$TMP/out" "PASS $spaced executable -"
assert_lines 'path containing whitespace still emits eight results' "$TMP/out" 8

# Missing failure guard.
unguarded="$TMP/dispatcher-unguarded.sh"
write_clean "$unguarded"
sed -i 's/ || true$//' "$unguarded"
run_check "$TMP/out" "$TMP/err" --files "$unguarded"
assert_rc 'unguarded call exits one' 1
assert_has 'unguarded call is reported' "$TMP/out" "FAIL $unguarded slack-guard -"

# Backslash continuations remain one logical command for guard analysis.
multiline_safe="$TMP/dispatcher-multiline-safe.sh"
write_clean "$multiline_safe"
sed -i 's/ || true$/ || \\/' "$multiline_safe"
sed -i '/slack-notify.sh/a\            true' "$multiline_safe"
run_check "$TMP/out" "$TMP/err" --files "$multiline_safe"
assert_rc 'multiline true guard exits zero' 0
assert_has 'multiline true guard passes' "$TMP/out" "PASS $multiline_safe slack-guard -"

multiline_unsafe="$TMP/dispatcher-multiline-unsafe.sh"
write_clean "$multiline_unsafe"
sed -i 's/ || true$/ || \\/' "$multiline_unsafe"
sed -i '/slack-notify.sh/a\            false' "$multiline_unsafe"
run_check "$TMP/out" "$TMP/err" --files "$multiline_unsafe"
assert_rc 'unsafe multiline guard exits one' 1
assert_has 'unsafe multiline guard is reported' "$TMP/out" "FAIL $multiline_unsafe slack-guard -"
assert_has 'unsafe multiline call remains visible to dry-run check' "$TMP/out" "$multiline_unsafe dry-run -"
assert_has 'unsafe multiline call remains visible to stdout check' "$TMP/out" "$multiline_unsafe stdout-json -"

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

# An unrelated changed set has the exact eight global SKIPs.
printf 'notes\n' >"$TMP/readme.txt"
run_check "$TMP/out" "$TMP/err" --files "$TMP/readme.txt"
assert_rc 'no-op file set exits zero' 0
assert_lines 'no-op file set emits eight global results' "$TMP/out" 8
[[ $(grep -c '^SKIP - ' "$TMP/out") -eq 8 ]] && pass 'no-op results are all SKIP' || fail 'no-op results are not all SKIP'

# verified-write (issue #432, ADR-014 -> Decision -> 5): each hand-rolled GitHub
# write form fails; the same write inside a shim of the block passes.
write_form() {  # write_form <file> <function> <line>
    printf '#!/usr/bin/env bash\n%s() {\n    %s\n}\n' "$2" "$3" >"$1"
    chmod +x "$1"
}
forms=(
    'gh api "repos/$REPO/issues/$n/comments" -X POST -f "x=1"'
    'gh api "repos/$REPO/issues/$n/labels/x" --method DELETE'
    'gh api "repos/$REPO/issues/$n/comments" -f body="$body"'
    'gh api "repos/$REPO/issues/$n/comments" --input "$file"'
    "gh api graphql -f query='mutation(\$id: ID!) { x }'"
    'gh issue comment "$n" --body x'
    'gh issue edit "$n" --add-label x'
    'gh issue close "$n"'
    'gh issue reopen "$n"'
    'gh pr ready "$n"'
    'gh pr merge "$n" --squash'
    'gh pr review "$n" --approve'
    'gh pr comment "$n" --body x'
    'gh label create x'
    'curl -s -X POST "https://api.github.com/repos/$REPO/issues/$n/comments" --data "$json"'
)
i=0
for form in "${forms[@]}"; do
    i=$((i + 1))
    bad="$TMP/dispatcher-write-$i.sh"
    write_form "$bad" notify_story "$form"
    run_check "$TMP/out" "$TMP/err" --files "$bad"
    assert_rc "verified-write fails on: $form" 1
    assert_has "verified-write names the file and check: $form" "$TMP/out" "FAIL $bad verified-write -"
    shim="$TMP/dispatcher-shim-$i.sh"
    write_form "$shim" post_comment "$form"
    run_check "$TMP/out" "$TMP/err" --files "$shim"
    assert_rc "verified-write passes inside a shim: $form" 0
    assert_has "verified-write PASS inside a shim: $form" "$TMP/out" "PASS $shim verified-write -"
done

# A read, a comment line and a message that names a mutation are not writes.
reads="$TMP/dispatcher-reads.sh"
cat >"$reads" <<'FIXTURE'
#!/usr/bin/env bash
read_story() {
    # gh api "repos/$REPO/issues/$n/comments" -X POST is how it was done before
    gh api --paginate "repos/$REPO/issues/$n/comments?per_page=100"
    gh api graphql -f query='query { viewer { login } }'
    echo "No GitHub mutation was made."
}
FIXTURE
chmod +x "$reads"
run_check "$TMP/out" "$TMP/err" --files "$reads"
assert_rc 'reads exit zero' 0
assert_has 'reads pass verified-write' "$TMP/out" "PASS $reads verified-write -"

# The allowlist: the read-only GraphQL query of dispatcher-poll.sh is sent with curl -X POST.
allowed="$TMP/dispatcher-allowed.sh"
write_form "$allowed" graphql 'curl -sf -X POST --data "$payload" "$GRAPHQL"'
run_check "$TMP/out" "$TMP/err" --files "$allowed"
assert_rc 'allowlisted function exits zero' 0
assert_has 'allowlisted function passes verified-write' "$TMP/out" "PASS $allowed verified-write -"

# A library file the dispatcher sources gets only the verified-write and own-marker rules.
mkdir -p "$TMP/lib"
lib_bad="$TMP/lib/helper.sh"
write_form "$lib_bad" helper 'gh api "repos/$REPO/issues/$n/comments" -X POST'
run_check "$TMP/out" "$TMP/err" --files "$lib_bad"
assert_rc 'library write exits one' 1
assert_has 'library write is reported' "$TMP/out" "FAIL $lib_bad verified-write -"
assert_has 'library file skips the other checks' "$TMP/out" "SKIP $lib_bad executable -"

# Every current dispatcher script and library passes verified-write.
run_check "$TMP/out" "$TMP/err" --files "$REPO_ROOT"/scripts/dispatcher-*.sh "$REPO_ROOT"/scripts/lib/*.sh
if grep -q ' verified-write - ' "$TMP/out" && ! grep -q '^FAIL .* verified-write - ' "$TMP/out"; then
    pass 'current dispatcher scripts pass verified-write'
else
    fail 'current dispatcher scripts pass verified-write'
fi

# own-marker (issue #433, ADR-014 -> Decision -> 5): each hand-rolled marker form
# fails; the same line inside a shim of Block 2 or an allowlisted function passes.
marker_forms=(
    'echo "<!-- hpw:dispatcher action=x -->"'
    'echo "<!-- hpw:codex-review-bookkeeping action=y -->"'
    'login=$(gh api user | jq -r .login)'
    'login=$(gh api user --jq ".login")'
    'login=$(curl -sf "$REST/user" | jq -r .login)'
    'jq -e --arg bot "$bot" "any(.[]; (.user.login // \"\") == \$bot)" <<< "$comments"'
    'jq -e --arg m "$DECISION_BLOCKER_CLAIM_MARKER" "any(.[]; .body | contains(\$m))" <<< "$comments"'
    'jq -e --arg m "$DECISION_BLOCKER_RESUME_MARKER" "any(.[]; .body | contains(\$m))" <<< "$comments"'
)
i=0
for form in "${marker_forms[@]}"; do
    i=$((i + 1))
    bad="$TMP/dispatcher-marker-$i.sh"
    write_form "$bad" read_owner "$form"
    run_check "$TMP/out" "$TMP/err" --files "$bad"
    assert_rc "own-marker fails on: $form" 1
    assert_has "own-marker names the file and check: $form" "$TMP/out" "FAIL $bad own-marker -"
    shim="$TMP/dispatcher-marker-shim-$i.sh"
    write_form "$shim" marker_holds "$form"
    run_check "$TMP/out" "$TMP/err" --files "$shim"
    assert_has "own-marker PASS inside a shim: $form" "$TMP/out" "PASS $shim own-marker -"
    allowed_fn="$TMP/dispatcher-marker-allowed-$i.sh"
    write_form "$allowed_fn" latest_decision_blocker_json "$form"
    run_check "$TMP/out" "$TMP/err" --files "$allowed_fn"
    assert_has "own-marker PASS in an allowlisted function: $form" "$TMP/out" "PASS $allowed_fn own-marker -"
done

# The same forms fail in a library file, and a read through the block is clean.
lib_marker="$TMP/lib/marker-helper.sh"
write_form "$lib_marker" helper 'echo "<!-- hpw:dispatcher action=x -->"'
run_check "$TMP/out" "$TMP/err" --files "$lib_marker"
assert_rc 'library marker exits one' 1
assert_has 'library marker is reported' "$TMP/out" "FAIL $lib_marker own-marker -"
through_block="$TMP/dispatcher-through-block.sh"
cat >"$through_block" <<'FIXTURE'
#!/usr/bin/env bash
read_owner() {
    # the literal "hpw:dispatcher" in a comment is not a marker read; gh api user was the old way
    "$HPW_BIN" dispatcher marker holds --repo "$REPO" --number "$n" --marker "action=merge-owner pr=$n head=$sha -->"
    gh api "repos/$REPO/users/x"
    jq -r '.user.type' <<< "$comment"
}
FIXTURE
chmod +x "$through_block"
run_check "$TMP/out" "$TMP/err" --files "$through_block"
assert_rc 'a read through the block exits zero' 0
assert_has 'a read through the block passes own-marker' "$TMP/out" "PASS $through_block own-marker -"

# Every current dispatcher script and library passes own-marker.
run_check "$TMP/out" "$TMP/err" --files "$REPO_ROOT"/scripts/dispatcher-*.sh "$REPO_ROOT"/scripts/lib/*.sh
if grep -q ' own-marker - ' "$TMP/out" && ! grep -q '^FAIL .* own-marker - ' "$TMP/out"; then
    pass 'current dispatcher scripts pass own-marker'
else
    fail 'current dispatcher scripts pass own-marker'
fi

# saved-state (issue #434, ADR-014 -> Decision -> 5): each hand-rolled read or
# write of saved state fails; the same line inside a shim of Block 3, or through
# the block, passes.
state_forms=(
    'echo x > .dispatcher-state/o/r/notified-refinement'
    'echo x >> "$STATE_DIR/o/r/actionable-events"'
    'cp "$EVENTS" "$STATE_FILE"'
    'mv -f "$STATE_FILE.tmp" "$STATE_FILE"'
    'rm -f "$HOLD_FILE"'
    'n=$(cat "$SESSIONS_FILE")'
    'IFS=: read -r d n < "$SESSIONS_FILE"'
    'mkdir -p .dispatcher-usage'
    'mkdir -p "$USAGE_LIMIT_STATE_DIR"'
    'printf x > "${NOTIFIED_FILE}"'
    'cat .dispatcher-allowance/hold.json'
)
i=0
for form in "${state_forms[@]}"; do
    i=$((i + 1))
    bad="$TMP/dispatcher-state-$i.sh"
    write_form "$bad" save_it "$form"
    run_check "$TMP/out" "$TMP/err" --files "$bad"
    assert_rc "saved-state fails on: $form" 1
    assert_has "saved-state names the file and check: $form" "$TMP/out" "FAIL $bad saved-state -"
    shim="$TMP/dispatcher-state-shim-$i.sh"
    write_form "$shim" save_state "$form"
    run_check "$TMP/out" "$TMP/err" --files "$shim"
    assert_has "saved-state PASS inside a shim: $form" "$TMP/out" "PASS $shim saved-state -"
done
lib_state="$TMP/lib/state-helper.sh"
write_form "$lib_state" helper 'cp "$EVENTS" "$STATE_FILE"'
run_check "$TMP/out" "$TMP/err" --files "$lib_state"
assert_rc 'library saved state exits one' 1
assert_has 'library saved state is reported' "$TMP/out" "FAIL $lib_state saved-state -"
state_ok="$TMP/dispatcher-state-ok.sh"
cat >"$state_ok" <<'FIXTURE'
#!/usr/bin/env bash
STATE_DIR="${DISPATCHER_STATE_DIR:-$REPO_ROOT/.dispatcher-state}"
NEXT_NOTIFIED_FILE="$TMP/notified-next"
echo "$n" >> "$NEXT_NOTIFIED_FILE"
echo "[POLL] Warning: cannot read refinement notification state $STATE_DIR" >&2
DISPATCHER_STATE_DIR="$STATE_DIR" "$HPW_BIN" dispatcher state read --name notified-refinement --repo "$REPO"
FIXTURE
chmod +x "$state_ok"
run_check "$TMP/out" "$TMP/err" --files "$state_ok"
assert_rc 'state read through the block exits zero' 0
assert_has 'state read through the block passes saved-state' "$TMP/out" "PASS $state_ok saved-state -"

# The dispatcher workflow: cache keys, saves and gh in a run: step.
wf_dir="$TMP/wf/.github/workflows"
mkdir -p "$wf_dir"
write_workflow() {  # write_workflow <file> <key> <if> <run>
    cat >"$1" <<FIXTURE
jobs:
  dispatch:
    steps:
      - name: Save state
        if: $3
        uses: actions/cache/save@v4
        with:
          path: .dispatcher-state
          key: $2

      - name: Run a step
        run: $4
FIXTURE
}
good_key='dispatcher-state-${{ github.run_id }}-${{ github.run_attempt }}'
good_if='${{ always() && steps.guard.outputs.enabled == '"'true'"' }}'
wf_good="$wf_dir/dispatcher.yml"
write_workflow "$wf_good" "$good_key" "$good_if" 'bash scripts/dispatcher-workflow-step.sh poll'
run_check "$TMP/out" "$TMP/err" --files "$wf_good"
assert_rc 'a compliant workflow exits zero' 0
assert_has 'a compliant workflow passes saved-state' "$TMP/out" "PASS $wf_good saved-state -"
assert_has 'the workflow is an eligible file' "$TMP/out" "SKIP $wf_good executable -"
mkdir -p "$TMP/wf2/.github/workflows" "$TMP/wf3/.github/workflows" "$TMP/wf4/.github/workflows" "$TMP/wf5/.github/workflows"
wf_key="$TMP/wf2/.github/workflows/dispatcher.yml"
write_workflow "$wf_key" 'dispatcher-state-${{ github.run_id }}' "$good_if" 'bash scripts/dispatcher-workflow-step.sh poll'
run_check "$TMP/out" "$TMP/err" --files "$wf_key"
assert_rc 'a cache save key without run_attempt exits one' 1
assert_has 'a cache save key without run_attempt is reported' "$TMP/out" "no github.run_attempt"
wf_if="$TMP/wf3/.github/workflows/dispatcher.yml"
write_workflow "$wf_if" "$good_key" '${{ steps.poll.outcome == '"'success'"' }}' 'bash scripts/dispatcher-workflow-step.sh poll'
run_check "$TMP/out" "$TMP/err" --files "$wf_if"
assert_rc 'a state save without always() exits one' 1
assert_has 'a state save without always() is reported' "$TMP/out" "has no always()"
wf_gh="$TMP/wf4/.github/workflows/dispatcher.yml"
write_workflow "$wf_gh" "$good_key" "$good_if" 'gh auth setup-git'
run_check "$TMP/out" "$TMP/err" --files "$wf_gh"
assert_rc 'a run: step that calls gh exits one' 1
assert_has 'a run: step that calls gh is reported' "$TMP/out" "calls gh directly"
wf_gh_block="$TMP/wf5/.github/workflows/dispatcher.yml"
write_workflow "$wf_gh_block" "$good_key" "$good_if" '|
          echo start
          gh api "repos/$REPO/issues"'
run_check "$TMP/out" "$TMP/err" --files "$wf_gh_block"
assert_rc 'a multi-line run: step that calls gh exits one' 1

# The repository's own scripts, libraries and workflow pass saved-state.
run_check "$TMP/out" "$TMP/err" --files "$REPO_ROOT"/scripts/dispatcher-*.sh "$REPO_ROOT"/scripts/lib/*.sh "$REPO_ROOT/.github/workflows/dispatcher.yml"
if grep -q ' saved-state - ' "$TMP/out" && ! grep -q '^FAIL .* saved-state - ' "$TMP/out"; then
    pass 'current dispatcher scripts and workflow pass saved-state'
else
    fail 'current dispatcher scripts and workflow pass saved-state'
fi

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
