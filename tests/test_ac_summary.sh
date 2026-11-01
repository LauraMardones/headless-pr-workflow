#!/usr/bin/env bash
# Deterministic tests for scripts/ac-summary.sh (issue #211).
# Usage: bash tests/test_ac_summary.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
AC_SUMMARY="$REPO_ROOT/scripts/ac-summary.sh"

PASS=0
FAIL=0
TMP_ROOT=$(mktemp -d)
trap 'rm -rf "$TMP_ROOT"' EXIT

assert_equals() {
    local label="$1" expected="$2" actual="$3"
    if [[ "$actual" == "$expected" ]]; then
        printf 'PASS: %s\n' "$label"
        PASS=$((PASS + 1))
    else
        printf 'FAIL: %s\n  Expected: %q\n  Actual:   %q\n' \
            "$label" "$expected" "$actual"
        FAIL=$((FAIL + 1))
    fi
}

assert_contains() {
    local label="$1" haystack="$2" needle="$3"
    if [[ "$haystack" == *"$needle"* ]]; then
        printf 'PASS: %s\n' "$label"
        PASS=$((PASS + 1))
    else
        printf 'FAIL: %s\n  Expected to contain: %q\n  Actual: %q\n' \
            "$label" "$needle" "$haystack"
        FAIL=$((FAIL + 1))
    fi
}

run_case() {
    local name="$1" body="$2"
    shift 2
    local case_dir="$TMP_ROOT/$name"
    mkdir -p "$case_dir/bin"
    printf '%s' "$body" > "$case_dir/body"
    : > "$case_dir/args"
    cat > "$case_dir/bin/gh" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$GH_STUB_ARGS"
if [[ "${GH_STUB_EXIT:-0}" -ne 0 ]]; then
    printf 'stub gh failure\n' >&2
    exit "$GH_STUB_EXIT"
fi
cat "$GH_STUB_BODY"
STUB
    chmod +x "$case_dir/bin/gh"

    local stdout_file="$case_dir/stdout" stderr_file="$case_dir/stderr" status=0
    PATH="$case_dir/bin:$PATH" \
        GH_STUB_BODY="$case_dir/body" \
        GH_STUB_ARGS="$case_dir/args" \
        GH_STUB_EXIT="${GH_STUB_EXIT:-0}" \
        "$AC_SUMMARY" "$@" >"$stdout_file" 2>"$stderr_file" || status=$?

    CASE_STATUS=$status
    CASE_STDOUT=$(cat "$stdout_file")
    CASE_STDERR=$(cat "$stderr_file")
    CASE_ARGS=$(cat "$case_dir/args")
}

body=$'Intro\n## Acceptance Criteria\n- [ ] first\n- [x] second\nplain prose\n'
run_case ac_only "$body" --issue 12
assert_equals "AC-only exits 0" 0 "$CASE_STATUS"
assert_equals "AC-only emits checklist lines only" $'- [ ] first\n- [x] second' "$CASE_STDOUT"
assert_equals "AC-only leaves stderr empty" "" "$CASE_STDERR"

body=$'## Definition of Done\n- [X] shipped\n  - [ ] nested unchanged\n'
run_case dod_only "$body" --issue 3
assert_equals "DoD-only exits 0" 0 "$CASE_STATUS"
assert_equals "DoD-only preserves source lines" $'- [X] shipped\n  - [ ] nested unchanged' "$CASE_STDOUT"

body=$'## Acceptance Criteria\n- [ ] AC one\n## Notes\n- [ ] excluded\n## Definition of Done\n- [x] DoD one\n'
run_case both "$body" --issue 99
assert_equals "both sections retain source order" $'- [ ] AC one\n- [x] DoD one' "$CASE_STDOUT"
assert_equals "next level-two heading stops extraction" 0 "$CASE_STATUS"

body=$'## Acceptance Criteria\nprose only\n- ordinary bullet\n## Definition of Done\nmore prose\n'
run_case empty_sections "$body" --issue 8
assert_equals "recognized empty sections exit 2" 2 "$CASE_STATUS"
assert_equals "recognized empty sections keep stdout empty" "" "$CASE_STDOUT"

body=$'## Notes\n- [ ] not criteria\n### Acceptance Criteria\n- [ ] wrong heading level\n'
run_case no_sections "$body" --issue 8
assert_equals "no recognized section exits 2" 2 "$CASE_STATUS"
assert_equals "no recognized section keeps stdout empty" "" "$CASE_STDOUT"

run_case repo_forwarding $'## Acceptance Criteria\n- [ ] yes\n' \
    --issue 42 --repo octo/example
assert_equals "--repo invocation exits 0" 0 "$CASE_STATUS"
assert_equals "gh receives exact expected arguments" \
    $'issue\nview\n42\n--repo\nocto/example\n--json\nbody\n--jq\n.body' "$CASE_ARGS"

run_case missing_issue "" --repo octo/example
assert_equals "missing --issue exits 1" 1 "$CASE_STATUS"
assert_contains "missing --issue diagnoses positive integer" "$CASE_STDERR" "positive integer"
assert_equals "invalid arguments do not call gh" "" "$CASE_ARGS"

run_case invalid_issue "" --issue 0
assert_equals "non-positive --issue exits 1" 1 "$CASE_STATUS"
assert_equals "invalid issue keeps stdout empty" "" "$CASE_STDOUT"

GH_STUB_EXIT=17 run_case gh_failure "" --issue 7
unset GH_STUB_EXIT
assert_equals "failed gh lookup exits 1" 1 "$CASE_STATUS"
assert_contains "failed gh lookup retains gh diagnostic" "$CASE_STDERR" "stub gh failure"
assert_contains "failed gh lookup adds context" "$CASE_STDERR" "failed to read issue #7"
assert_equals "failed gh lookup keeps stdout empty" "" "$CASE_STDOUT"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
