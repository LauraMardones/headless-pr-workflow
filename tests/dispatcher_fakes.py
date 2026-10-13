"""Fakes for tests that drive the real ``scripts/dispatcher-invoke.sh`` end to end.

A fake ``gh`` (board, issues, pull requests, comments), a fake ``curl``
(executor API and Slack webhook) and a stub allowance script on
``ALLOWANCE_SCRIPT``. All state lives in files under ``MOCK_DIR``, so a test
can run the dispatcher several times against the same GitHub state. Issues and
PRs carry a fixed old activity date, so a story "In implementation" is always
past the stale threshold.

The fake ``curl`` reads the command a session was started for from the task
prompt (the first line of ``.claude/commands/<command>.md``) and logs it. A
test can make a command fail (``fail``) or give it a side effect on the fake
GitHub state (``on_session``), such as a new PR head.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INVOKE = REPO_ROOT / "scripts" / "dispatcher-invoke.sh"

# ``subprocess`` would resolve a bare "bash" to the WSL launcher on Windows.
BASH = shutil.which("bash")
HAVE_TOOLS = all(shutil.which(tool) for tool in ("bash", "jq", "timeout"))

BOT = "hpw-bot"          # the account behind GH_TOKEN; the bookkeeping job posts with it too
PO = "LauraMardones"     # DECISION_BLOCKER_PO_LOGIN in dispatcher-invoke.sh

BOARD_OPTIONS = {
    "Ready for implementation": "OPT_READY",
    "In implementation": "OPT_IMPL",
    "In review": "OPT_REVIEW",
    "In merge": "OPT_MERGE",
    "Done": "OPT_DONE",
}

FAKE_GH = r"""#!/usr/bin/env bash
set -euo pipefail
echo "gh $*" >> "$MOCK_DIR/gh.log"
[[ "${1:-}" == "api" ]] || { echo "fake gh: unsupported: $*" >&2; exit 1; }
shift
endpoint="" query="" item_id="" option_id="" method="GET" body=""
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
        --paginate) shift ;;
        *)  [[ -z "$endpoint" ]] && endpoint="$1"; shift ;;
    esac
done

tick() {
    local n
    n=$(cat "$MOCK_DIR/clock" 2>/dev/null || echo 0)
    echo $((n + 1)) > "$MOCK_DIR/clock"
    printf '2026-10-13T10:%02d:%02dZ' $(( (n + 1) / 60 )) $(( (n + 1) % 60 ))
}

if [[ "$endpoint" == "graphql" ]]; then
    if [[ "$query" == *mutation* ]]; then
        name=$(jq -r --arg o "$option_id" '.options | to_entries[] | select(.value == $o) | .key' "$MOCK_DIR/board.json")
        echo "$item_id=$name" >> "$MOCK_DIR/mutations.log"
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
    user)
        echo "{\"login\": \"$MOCK_BOT\"}" ;;
    repos/*/issues/*/comments)
        n="${path%/comments}"; n="${n##*/}"
        file="$MOCK_DIR/comments_$n.json"
        [[ -f "$file" ]] || echo '[]' > "$file"
        if [[ "$method" == "POST" ]]; then
            jq --arg b "$body" --arg t "$(tick)" --arg u "$MOCK_BOT" \
                '. + [{user: {login: $u}, created_at: $t, body: $b, posted: true}]' "$file" > "$file.tmp"
            mv "$file.tmp" "$file"
            echo '{}'
        else
            cat "$file"
        fi ;;
    repos/*/issues/*)
        n="${path##*/}"
        [[ -f "$MOCK_DIR/issue_$n.json" ]] || { echo "fake gh: no issue $n" >&2; exit 1; }
        cat "$MOCK_DIR/issue_$n.json" ;;
    repos/*/pulls)
        if [[ "$endpoint" == *state=closed* ]]; then
            jq '[.[] | select(.state == "closed")]' "$MOCK_DIR/pulls.json"
        else
            jq '[.[] | select(.state == "open")]' "$MOCK_DIR/pulls.json"
        fi ;;
    *)
        echo '[]' ;;
esac
"""

FAKE_CURL = r"""#!/usr/bin/env bash
set -euo pipefail
outfile="" url="" data=""
args=("$@")
i=0
while [[ $i -lt ${#args[@]} ]]; do
    case "${args[$i]}" in
        -o) i=$((i+1)); outfile="${args[$i]}" ;;
        --data) i=$((i+1)); data="${args[$i]#@}" ;;
        http*://*) url="${args[$i]}" ;;
    esac
    i=$((i+1))
done
if [[ "$url" != *anthropic* && "$url" != *openai* ]]; then
    echo "other $url" >> "$MOCK_DIR/curl.log"
    exit 0
fi
provider=anthropic
[[ "$url" == *openai* ]] && provider=openai
# The task prompt is the last user message; its first line names the command.
first_line=$(jq -r '[.messages[] | select(.role == "user") | .content | strings] | (first // "") | split("\n")[0] // ""' "$data")
case "$first_line" in
    "Implement issue #"*) command=implement ;;
    "Review PR #"*)       command=review ;;
    "Merge PR #"*)        command=merge ;;
    "Clean up"*)          command=cleanup ;;
    *)                    command=unknown ;;
esac
target=$(sed -E 's/^[^#]*#([0-9]+).*/\1/' <<< "$first_line")
echo "session $command $target $provider" >> "$MOCK_DIR/curl.log"
if [[ -f "$MOCK_DIR/fail_$command" ]]; then
    printf '%s' '{"error":"forced failure"}' > "$outfile"
    echo -n "500"
    exit 0
fi
[[ -f "$MOCK_DIR/on_$command.sh" ]] && bash "$MOCK_DIR/on_$command.sh"
if [[ "$provider" == anthropic ]]; then
    printf '%s' '{"stop_reason":"end_turn","content":[{"type":"text","text":"done"}],"usage":{"input_tokens":1,"output_tokens":1}}' > "$outfile"
else
    printf '%s' '{"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":"done"}}]}' > "$outfile"
fi
echo -n "200"
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


def bookkeeping_marker(action: str, head: str) -> str:
    """The marker as the codex-review-bookkeeping job writes it (built by the job's own function)."""
    sys.path.insert(0, str(REPO_ROOT / "src"))
    from headless_pr_workflow.codex_review_bookkeeping import action_marker

    return action_marker(action, head, source="https://example.test/review")


class Dispatcher:
    def __init__(self, tmp: Path, options: dict[str, str] | None = None):
        self.tmp = tmp
        self.bin = tmp / "bin"
        self.bin.mkdir()
        for name, text in (("gh", FAKE_GH), ("curl", FAKE_CURL), ("allowance.sh", FAKE_ALLOWANCE)):
            path = self.bin / name
            path.write_text(text, encoding="utf-8", newline="\n")
            path.chmod(0o755)
        self.mock = tmp / "mock"
        self.mock.mkdir()
        (self.mock / "budget").mkdir()
        self._write("state.json", {})
        self._write("pulls.json", [])
        self._write("board.json", {"items": [], "options": options or BOARD_OPTIONS})
        for name in ("gh.log", "curl.log", "github_output", "mutations.log"):
            (self.mock / name).write_text("")

    # ── fake GitHub state ───────────────────────────────────────────────────

    def _write(self, name: str, value) -> None:
        (self.mock / name).write_text(json.dumps(value), encoding="utf-8", newline="\n")

    def _read(self, name: str, default=None):
        path = self.mock / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

    def add_story(self, number: int, status: str, executor: str | None = "claude-code-sonnet",
                  labels: list[str] | None = None, body: str = "") -> None:
        if labels is None:
            labels = ["type:story"] + ([f"executor:{executor}"] if executor else [])
        board = self._read("board.json")
        board["items"].append({"id": f"ITEM_{number}", "number": number, "title": f"Story {number}",
                               "body": body, "status": status})
        self._write("board.json", board)
        self._write(f"issue_{number}.json", {"number": number, "title": f"Story {number}", "body": body,
                                             "labels": [{"name": name} for name in labels]})

    def add_pr(self, number: int, issue: int, head: str = "a" * 40, draft: bool = False,
               state: str = "open", merged: bool = False) -> None:
        pulls = [pr for pr in self._read("pulls.json") if pr["number"] != number]
        pulls.append({"number": number, "body": f"Closes #{issue}", "state": state, "draft": draft,
                      "merged_at": "2026-10-13T09:30:00Z" if merged else None,
                      "updated_at": "2026-09-27T00:00:00Z", "head": {"ref": "branch", "sha": head}})
        self._write("pulls.json", pulls)

    def add_comment(self, target: int, body: str, login: str = BOT) -> None:
        tick = int((self.mock / "clock").read_text()) + 1 if (self.mock / "clock").exists() else 1
        (self.mock / "clock").write_text(str(tick))
        comments = self._read(f"comments_{target}.json", [])
        comments.append({"user": {"login": login}, "body": body,
                         "created_at": f"2026-10-13T10:{tick // 60:02d}:{tick % 60:02d}Z"})
        self._write(f"comments_{target}.json", comments)

    def add_findings(self, pr: int, head: str, login: str = BOT) -> None:
        """The review Session Summary the bookkeeping job posts after findings on ``head``."""
        self.add_comment(pr, "## Session Summary\nCommand: review\nNext action: implementation\n\n"
                         + bookkeeping_marker("move-in-implementation", head), login=login)

    def set_status(self, number: int, status: str) -> None:
        state = self._read("state.json")
        state[f"ITEM_{number}"] = status
        self._write("state.json", state)

    def on_session(self, command: str, script: str) -> None:
        """Bash run by the fake executor when a ``command`` session starts (``$MOCK_DIR`` is set)."""
        (self.mock / f"on_{command}.sh").write_text(script, encoding="utf-8", newline="\n")

    def session_sets_status(self, command: str, number: int, status: str) -> None:
        self.on_session(command, f"""jq '. + {{"ITEM_{number}": "{status}"}}' "$MOCK_DIR/state.json" > "$MOCK_DIR/s.tmp" && mv "$MOCK_DIR/s.tmp" "$MOCK_DIR/state.json"\n""")

    def fail(self, command: str, failing: bool = True) -> None:
        path = self.mock / f"fail_{command}"
        path.write_text("") if failing else path.unlink(missing_ok=True)

    # ── running ─────────────────────────────────────────────────────────────

    def run(self, *args: str, seq: str = "0", env: dict[str, str] | None = None):
        path = [self.bin.as_posix(), "/usr/bin", "/bin"]
        full_env = {
            "HOME": self.tmp.as_posix(),
            "PATH": ":".join(path),
            "MOCK_DIR": self.mock.as_posix(),
            "MOCK_BOT": BOT,
            "GH_TOKEN": "fake-token",
            "GITHUB_OUTPUT": (self.mock / "github_output").as_posix(),
            "ANTHROPIC_API_KEY": "fake-anthropic",
            "OPENAI_API_KEY_CODEX": "fake-openai",
            "SLACK_WEBHOOK_URL": "https://hooks.slack.test/T000/B000",
            "BUDGET_COUNTER_DIR": (self.mock / "budget").as_posix(),
            "BUDGET_DAILY_HAIKU": "10000000", "BUDGET_DAILY_SONNET": "10000000",
            "BUDGET_DAILY_OPUS": "10000000", "BUDGET_DAILY_CODEX": "10000000",
            "AGENT_MAX_TURNS": "3", "AGENT_MAX_WALLCLOCK_SECONDS": "600",
            "ALLOWANCE_SCRIPT": (self.bin / "allowance.sh").as_posix(),
            "ALLOWANCE_SEQ": seq,
        }
        if os.name == "nt":  # Git Bash: keep the host PATH (jq, coreutils) behind the fakes
            full_env["PATH"] = os.pathsep.join([str(self.bin), os.environ["PATH"]])
            full_env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
        full_env.update(env or {})
        (self.mock / "allowance.count").unlink(missing_ok=True)  # ALLOWANCE_SEQ counts per run
        return subprocess.run(
            [BASH, INVOKE.as_posix(), "--repo", "o/r", *args],
            env=full_env, capture_output=True, text=True, encoding="utf-8", timeout=300,
        )

    def run_issue(self, number: int, *args: str, **kwargs):
        return self.run("--issue", str(number), *args, **kwargs)

    def resume(self, *args: str, **kwargs):
        return self.run("--resume", *args, **kwargs)

    # ── observations ────────────────────────────────────────────────────────

    def _log(self, name: str) -> list[str]:
        path = self.mock / name
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    @property
    def sessions(self) -> list[str]:
        """Sessions started, in order, as ``"<command> <target>"``."""
        return [" ".join(line.split()[1:3]) for line in self._log("curl.log") if line.startswith("session ")]

    @property
    def session_providers(self) -> list[str]:
        return [" ".join(line.split()[1::2]) for line in self._log("curl.log") if line.startswith("session ")]

    @property
    def slack_calls(self) -> list[str]:
        return [line for line in self._log("curl.log") if "hooks.slack" in line]

    @property
    def allowance_calls(self) -> int:
        path = self.mock / "allowance.count"
        return int(path.read_text()) if path.exists() else 0

    @property
    def board_writes(self) -> list[str]:
        """Board status writes by the dispatcher, in order, as ``"<issue>=<status>"``."""
        return [line.removeprefix("ITEM_") for line in self._log("mutations.log")]

    def posted(self, target: int) -> list[str]:
        """Bodies of the comments the dispatcher posted on an issue or PR."""
        return [c["body"] for c in self._read(f"comments_{target}.json", []) if c.get("posted")]

    def status(self, number: int) -> str:
        item = next(i for i in self._read("board.json")["items"] if i["number"] == number)
        return self._read("state.json").get(f"ITEM_{number}", item["status"])
