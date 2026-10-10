"""Dispatcher call site of the subscription allowance check (issue #317).

Drives the real ``scripts/dispatcher-invoke.sh`` end to end with a fake ``gh``
(board + REST), a fake ``curl`` (executor API and Slack webhook) and a stub
allowance script on ``ALLOWANCE_SCRIPT`` whose exit codes follow a sequence,
one per session start. The fakes mirror tests/test_dispatcher_invoke_non_executable.sh.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INVOKE = REPO_ROOT / "scripts" / "dispatcher-invoke.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None or shutil.which("timeout") is None,
    reason="requires bash, jq and timeout",
)

FAKE_GH = r"""#!/usr/bin/env bash
set -euo pipefail
echo "gh $*" >> "$MOCK_DIR/gh.log"
[[ "${1:-}" == "api" ]] || { echo "fake gh: unsupported: $*" >&2; exit 1; }
shift
endpoint="$1"; shift
query="" item_id="" option_id="" method="GET" body=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -f) case "$2" in
                query=*)    query="${2#query=}" ;;
                itemId=*)   item_id="${2#itemId=}" ;;
                optionId=*) option_id="${2#optionId=}" ;;
                body=*)     body="${2#body=}" ;;
            esac
            shift 2 ;;
        -X) method="$2"; shift 2 ;;
        *)  shift ;;
    esac
done

if [[ "$endpoint" == "graphql" ]]; then
    if [[ "$query" == *mutation* ]]; then
        echo "MUTATION item=$item_id option=$option_id" >> "$MOCK_DIR/gh.log"
        name=$(jq -r --arg o "$option_id" '.options | to_entries[] | select(.value == $o) | .key' "$MOCK_DIR/board.json")
        jq --arg i "$item_id" --arg n "$name" '. + {($i): $n}' "$MOCK_DIR/state.json" > "$MOCK_DIR/state.tmp"
        mv "$MOCK_DIR/state.tmp" "$MOCK_DIR/state.json"
        echo '{}'
        exit 0
    fi
    jq --slurpfile st "$MOCK_DIR/state.json" '
        . as $b |
        {data:{repository:{projectsV2:{nodes:[{
            id: "PROJ",
            fields: {nodes: [{id: "STATUS_FIELD", name: "Status",
                              options: [$b.options | to_entries[] | {id: .value, name: .key}]}]},
            items: {
                pageInfo: {hasNextPage: false, endCursor: null},
                nodes: [$b.items[] | {
                    id: .id,
                    updatedAt: "2026-09-27T00:00:00Z",
                    content: {number: .number, title: .title, body: (.body // ""),
                              updatedAt: "2026-09-27T00:00:00Z"},
                    fieldValues: {nodes: [{name: ($st[0][.id] // .status),
                                           field: {name: "Status"}}]}
                }]
            }
        }]}}}}' "$MOCK_DIR/board.json"
    exit 0
fi

path="${endpoint%%\?*}"
case "$path" in
    repos/*/issues/*/comments)
        if [[ "$method" == "POST" ]]; then
            echo "POST $path" >> "$MOCK_DIR/gh.log"
            jq -n --arg p "$path" --arg b "$body" '{path: $p, body: $b}' -c >> "$MOCK_DIR/comments.jsonl"
            echo '{}'
        else
            echo '[]'
        fi ;;
    repos/*/issues/*)
        n="${path##*/}"
        [[ -f "$MOCK_DIR/issue_$n.json" ]] || { echo "fake gh: no issue $n" >&2; exit 1; }
        cat "$MOCK_DIR/issue_$n.json" ;;
    repos/*/pulls)
        cat "$MOCK_DIR/pulls.json" ;;
    *)
        echo '[]' ;;
esac
"""

FAKE_CURL = r"""#!/usr/bin/env bash
set -euo pipefail
outfile="" url=""
args=("$@")
i=0
while [[ $i -lt ${#args[@]} ]]; do
    case "${args[$i]}" in
        -o) i=$((i+1)); outfile="${args[$i]}" ;;
        http*://*) url="${args[$i]}" ;;
    esac
    i=$((i+1))
done
echo "url=$url" >> "$MOCK_DIR/curl.log"
if [[ "$url" == *anthropic* ]]; then
    printf '%s' '{"stop_reason":"end_turn","content":[{"type":"text","text":"done"}],"usage":{"input_tokens":1,"output_tokens":1}}' > "$outfile"
    echo -n "200"
elif [[ "$url" == *openai* ]]; then
    printf '%s' '{"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"done"}}]}' > "$outfile"
    echo -n "200"
fi
"""

# Exit codes come from $ALLOWANCE_SEQ (comma-separated), one per call; the
# call count lives in $MOCK_DIR/allowance.count.
FAKE_ALLOWANCE = r"""#!/usr/bin/env bash
n=$(cat "$MOCK_DIR/allowance.count" 2>/dev/null || echo 0)
echo $((n + 1)) > "$MOCK_DIR/allowance.count"
IFS=',' read -ra seq <<< "${ALLOWANCE_SEQ:-0}"
rc="${seq[$n]:-0}"
case "$rc" in
  0) echo '{"decision":"start","source":"probe","reason":"","five_hour":{"utilization":0.21,"remaining_percent":79,"reserve_percent":10,"resets_at":"2026-10-10T15:00:00Z"},"weekly":{"utilization":0.29,"remaining_percent":71,"reserve_percent":20,"resets_at":"2026-10-14T08:00:00Z"},"api_key_source":"none","overage_status":"rejected"}'
     echo "[RESERVE] five-hour 79% left (reserve 10%), weekly 71% left (reserve 20%) — start" >&2 ;;
  1) echo '{"decision":"refuse","source":"probe","reason":"weekly 15% left, below reserve 20%; resets 2026-10-14T08:00Z","five_hour":{"utilization":0.21,"remaining_percent":79,"reserve_percent":10,"resets_at":"2026-10-10T15:00:00Z"},"weekly":{"utilization":0.85,"remaining_percent":15,"reserve_percent":20,"resets_at":"2026-10-14T08:00:00Z"},"api_key_source":"none","overage_status":"rejected"}'
     echo "[RESERVE SKIP] weekly 15% left, below reserve 20%; resets 2026-10-14T08:00Z" >&2 ;;
  *) echo '{"decision":"error","source":"probe","reason":"no rate_limit_event in the probe output","five_hour":{"utilization":null,"remaining_percent":null,"reserve_percent":10,"resets_at":null},"weekly":{"utilization":null,"remaining_percent":null,"reserve_percent":20,"resets_at":null},"api_key_source":"none","overage_status":null}'
     echo "[ALLOWANCE ERROR] no rate_limit_event in the probe output — starting nothing" >&2 ;;
esac
exit "$rc"
"""

ISSUE = 500
PR = 900


class Dispatcher:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.bin = tmp / "bin"
        self.bin.mkdir()
        for name, text in (("gh", FAKE_GH), ("curl", FAKE_CURL), ("allowance.sh", FAKE_ALLOWANCE)):
            path = self.bin / name
            path.write_text(text)
            path.chmod(0o755)
        self.mock = tmp / "mock"
        self.mock.mkdir()
        (self.mock / "budget").mkdir()
        (self.mock / "state.json").write_text("{}")
        (self.mock / "gh.log").write_text("")
        (self.mock / "curl.log").write_text("")
        (self.mock / "github_output").write_text("")
        board = {
            "items": [{"id": f"ITEM_{ISSUE}", "number": ISSUE, "title": "Story",
                       "body": "", "status": "Ready for implementation"}],
            "options": {"Ready for implementation": "OPT_READY", "In implementation": "OPT_IMPL",
                        "In review": "OPT_REVIEW", "Ready to merge": "OPT_MERGE", "Done": "OPT_DONE"},
        }
        (self.mock / "board.json").write_text(json.dumps(board))
        (self.mock / f"issue_{ISSUE}.json").write_text(json.dumps({
            "number": ISSUE, "title": "Story", "body": "",
            "labels": [{"name": "type:story"}, {"name": "executor:claude-code-sonnet"}],
        }))
        (self.mock / "pulls.json").write_text(json.dumps([{
            "number": PR, "body": f"Closes #{ISSUE}", "updated_at": "2026-09-27T00:00:00Z",
            "head": {"ref": "x"},
        }]))

    def run(self, seq: str, *extra: str):
        env = {
            "HOME": str(self.tmp),
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "MOCK_DIR": str(self.mock),
            "GH_TOKEN": "fake-token",
            "GITHUB_OUTPUT": str(self.mock / "github_output"),
            "ANTHROPIC_API_KEY": "fake-anthropic",
            "OPENAI_API_KEY_CODEX": "fake-openai",
            "SLACK_WEBHOOK_URL": "https://hooks.slack.test/T000/B000",
            "BUDGET_COUNTER_DIR": str(self.mock / "budget"),
            "BUDGET_DAILY_HAIKU": "10000000", "BUDGET_DAILY_SONNET": "10000000",
            "BUDGET_DAILY_OPUS": "10000000", "BUDGET_DAILY_CODEX": "10000000",
            "AGENT_MAX_TURNS": "3", "AGENT_MAX_WALLCLOCK_SECONDS": "600",
            "ALLOWANCE_SCRIPT": str(self.bin / "allowance.sh"),
            "ALLOWANCE_SEQ": seq,
        }
        return subprocess.run(
            ["bash", str(INVOKE), "--repo", "o/r", "--issue", str(ISSUE), *extra],
            env=env, capture_output=True, text=True, timeout=120,
        )

    def read(self, name: str) -> str:
        path = self.mock / name
        return path.read_text() if path.exists() else ""

    @property
    def allowance_calls(self) -> int:
        return int(self.read("allowance.count") or 0)

    @property
    def executor_calls(self) -> list[str]:
        return [l for l in self.read("curl.log").splitlines()
                if "anthropic" in l or "openai" in l]

    @property
    def slack_calls(self) -> list[str]:
        return [l for l in self.read("curl.log").splitlines() if "hooks.slack" in l]

    @property
    def comments(self) -> list[dict]:
        return [json.loads(l) for l in self.read("comments.jsonl").splitlines()]

    @property
    def status(self) -> str:
        return json.loads(self.read("state.json")).get(f"ITEM_{ISSUE}", "Ready for implementation")


@pytest.fixture
def d(tmp_path):
    return Dispatcher(tmp_path)


def test_start_runs_every_session(d):
    proc = d.run("0,0,0,0")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.allowance_calls == 4  # /implement, /review, /merge, /cleanup
    assert len(d.executor_calls) == 4
    assert d.comments == []
    assert f"Story #{ISSUE} completed (Done)." in proc.stdout


def test_refuse_before_implement_skips_quietly(d):
    proc = d.run("1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.allowance_calls == 1
    assert d.executor_calls == []
    assert d.comments == []
    assert "[RESERVE SKIP]" in proc.stdout
    assert d.status == "Ready for implementation"
    assert d.slack_calls == []


def test_refuse_before_review_comments_once(d):
    proc = d.run("0,1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.allowance_calls == 2
    assert len(d.executor_calls) == 1  # /implement only; /review not started
    assert len(d.comments) == 1
    comment = d.comments[0]
    assert comment["path"].endswith(f"/issues/{ISSUE}/comments")
    body = comment["body"]
    assert "/review was not started" in body
    assert "weekly window: 15% left, reserve 20%, resets 2026-10-14T08:00:00Z" in body
    assert "five-hour window" not in body  # only the window below the reserve
    assert d.slack_calls == []


@pytest.mark.parametrize("seq,step", [("0,0,1", "merge"), ("0,0,0,1", "cleanup")])
def test_refuse_before_merge_or_cleanup_comments_once(d, seq, step):
    proc = d.run(seq)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(d.comments) == 1
    assert f"/{step} was not started" in d.comments[0]["body"]


def test_error_before_implement_fails_without_slack(d):
    proc = d.run("2")
    assert proc.returncode != 0
    assert d.executor_calls == []
    assert "[ALLOWANCE ERROR]" in proc.stderr
    assert d.slack_calls == []  # no dispatcher_error notification
    assert d.comments == []
    assert d.status == "Ready for implementation"


def test_error_after_implement_comments_with_reason(d):
    proc = d.run("0,2")
    assert proc.returncode != 0
    assert len(d.executor_calls) == 1
    assert d.slack_calls == []
    assert len(d.comments) == 1
    body = d.comments[0]["body"]
    assert "/review was not started" in body
    assert "could not be read" in body
    assert "no rate_limit_event in the probe output" in body


def test_dry_run_logs_and_does_not_run_check(d):
    proc = d.run("2", "--dry-run")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[DRY RUN] Would check subscription allowance before /implement" in proc.stdout
    assert d.allowance_calls == 0
