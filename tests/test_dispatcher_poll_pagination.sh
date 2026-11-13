#!/usr/bin/env bash
# tests/test_dispatcher_poll_pagination.sh
#
# Regression test for issue #248: dispatcher-poll.sh read only the first 100
# items and silently missed work added after that page (e.g. #240). Since
# issue #475 the poll reads no board: it finds work in flight from the open
# issues and pull requests, read page by page through the REST API. This test
# runs the actual script against a mocked `curl` and proves the paginated read:
#   1. surfaces a story that exists only on page 2 of the open issues, whose
#      linked PR exists only on page 2 of the open pull requests,
#   2. asks for page 2 of each list after a full page 1 (100 entries), and
#   3. stops after the short page 2: no page 3 request.
#
# Usage: bash tests/test_dispatcher_poll_pagination.sh
# Requires: bash 4+, jq, python 3

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
# The WIP and queue limits have no default in the scripts (issue #358).
export WIP_LIMIT_IMPLEMENTATION=2 READY_BUFFER_MIN=3 READY_BUFFER_MAX=5

PASS=0
FAIL=0

GLOBAL_TMP=$(mktemp -d)
trap 'rm -rf "$GLOBAL_TMP"' EXIT

command -v jq >/dev/null 2>&1 || { echo "SKIP: jq not available"; exit 0; }
PYTHON=$(command -v python3 || command -v python || true)
[[ -n "$PYTHON" ]] || { echo "SKIP: python not available"; exit 0; }

# ─── Mock curl: the REST lists dispatcher-poll.sh reads ──────────────────────
#
# Logs every URL to CALL_LOG.
#   issues?state=open  page 1: 100 Done-free issues without a type: label;
#                      page 2: story #240 (executor:codex)
#   pulls?state=open   page 1: 100 PRs that close no issue; page 2: PR #340 closes #240
#   anything else      an empty list, or the empty board for flow-review.sh
FAKE_BIN=$(mktemp -d "$GLOBAL_TMP/bin.XXXXXX")
CALL_LOG="$GLOBAL_TMP/curl-calls.log"
: > "$CALL_LOG"

cat > "$FAKE_BIN/curl" << 'CURLMOCK'
#!/usr/bin/env bash
url="${@: -1}"
echo "$url" >> "$MOCK_CALL_LOG"
case "$url" in
    *"/issues?state=open"*"page=1")
        jq -nc '[range(1; 101) | {number: ., title: "Note \(.)", labels: [], comments: 0}]' ;;
    *"/issues?state=open"*"page=2")
        echo '[{"number": 240, "title": "Story: Define delivery-baseline", "comments": 0,
                "labels": [{"name": "type:story"}, {"name": "executor:codex"}]}]' ;;
    *"/pulls?state=open"*"page=1")
        jq -nc '[range(1000; 1100) | {number: ., draft: false, body: "no link", head: {sha: "abcdef0"}}]' ;;
    *"/pulls?state=open"*"page=2")
        echo '[{"number": 340, "draft": false, "body": "Closes #240", "head": {"sha": "1234567"}}]' ;;
    *graphql*)
        echo '{"data":{"repository":{"projectsV2":{"nodes":[{"number":3,"title":"board","items":{"pageInfo":{"hasNextPage":false,"endCursor":null},"nodes":[]}}]}}}}' ;;
    *)
        echo '[]' ;;
esac
exit 0
CURLMOCK
chmod +x "$FAKE_BIN/curl"

# The merge gates of PR #340 do not pass: the story is in the review step.
cat > "$FAKE_BIN/gate" << 'GATEMOCK'
#!/usr/bin/env bash
echo "Merge gate: policy=pass(present_non_required), checks=pass, approval=none, threads=none, mergeable=yes, head=1234567"
exit 1
GATEMOCK
chmod +x "$FAKE_BIN/gate"

# The scheduler (issue #474) on HPW_BIN: idle, with no GitHub call; the
# in-flight rule (issue #475) runs for real; no account is known.
cat > "$FAKE_BIN/hpw" << HPWMOCK
#!/usr/bin/env bash
if [[ "\${1:-} \${2:-}" == "dispatcher in-flight" ]]; then
    shift; PYTHONPATH="$REPO_ROOT/src" exec "$PYTHON" -m headless_pr_workflow.dispatcher "\$@"
fi
if [[ "\${1:-} \${2:-}" == "dispatcher scheduler" ]]; then
    echo "[SCHEDULER] idle" >&2
    echo '{"ok":true,"block":"scheduler","operation":"next","result":"idle","action":null,"skipped":[]}'
    exit 1
fi
exit 1
HPWMOCK
chmod +x "$FAKE_BIN/hpw"

# ─── Test: run the real script end to end against the mock ───────────────────

test_pagination_surfaces_page_two_item() {
    local poll_output
    poll_output=$(
        PATH="$FAKE_BIN:$PATH" \
        MOCK_CALL_LOG="$CALL_LOG" \
        GH_TOKEN="fake-token" \
        HPW_BIN="$FAKE_BIN/hpw" \
        MERGE_GATE_SCRIPT="$FAKE_BIN/gate" \
        bash "$REPO_ROOT/scripts/dispatcher-poll.sh" --repo "acme/example" 2>"$GLOBAL_TMP/err.log"
    )

    # 1. A story and its PR that exist only on page 2 are surfaced.
    local found_number
    found_number=$(echo "$poll_output" | jq -r '.review_due[0].number // empty')
    if [[ "$found_number" == "240" ]]; then
        echo "PASS: story present only on page 2 (#240) is surfaced"
        PASS=$(( PASS + 1 ))
    else
        echo "FAIL: expected #240 in review_due, got: $poll_output $(cat "$GLOBAL_TMP/err.log")"
        FAIL=$(( FAIL + 1 ))
    fi

    # 2. Page 2 of each list was asked for after a full page 1.
    local list
    for list in "issues?state=open" "pulls?state=open"; do
        if grep -F "/$list" "$CALL_LOG" | grep -q 'page=2$'; then
            echo "PASS: page 2 of $list was read"
            PASS=$(( PASS + 1 ))
        else
            echo "FAIL: no page-2 request for $list"
            FAIL=$(( FAIL + 1 ))
        fi
        # 3. The short page 2 ends the list: no page 3.
        if grep -F "/$list" "$CALL_LOG" | grep -q 'page=3$'; then
            echo "FAIL: $list was read past its short page 2"
            FAIL=$(( FAIL + 1 ))
        else
            echo "PASS: $list stopped after its short page 2"
            PASS=$(( PASS + 1 ))
        fi
    done
}

echo "Running dispatcher-poll pagination regression tests..."
echo ""

test_pagination_surfaces_page_two_item

echo ""
echo "Results: $PASS pass, $FAIL fail"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
