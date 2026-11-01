"""Fakes for tests that drive the real ``scripts/dispatcher-invoke.sh`` end to end.

A fake ``gh`` (board, issues, pull requests, comments), a fake ``claude``
on ``CLAUDE_BIN`` (Claude sessions, issue #314), a fake ``curl`` (the Slack
webhook) and a stub allowance script on
``ALLOWANCE_SCRIPT``. All state lives in files under ``MOCK_DIR``, so a test
can run the dispatcher several times against the same GitHub state. Issues and
PRs carry a fixed old activity date, so a story "In implementation" is always
past the stale threshold.

The fake ``claude`` reads the command a session was started for from the task
prompt (the first line of ``.claude/commands/<command>.md``) and logs it to
``sessions.log`` with the provider (``claude-code``). A test can
make a command fail (``fail``) or give it a side effect on the fake GitHub
state (``on_session``), such as a new PR head. The fake ``claude`` also keeps
the argv, stdin and environment of every call (``claude_calls``), and
``claude_stream`` replaces its default stream-json output.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.script_tools import BASH, missing_tools, requires, script_path, windows_env

REPO_ROOT = Path(__file__).resolve().parent.parent
INVOKE = REPO_ROOT / "scripts" / "dispatcher-invoke.sh"

TOOLS = ("bash", "jq", "timeout")
HAVE_TOOLS = not missing_tools(TOOLS)
needs_tools = requires(*TOOLS)

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
endpoint="" jq_expr="" query="" item_id="" option_id="" method="GET" body="" label_value=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -f) case "$2" in
                query=*)    query="${2#query=}" ;;
                itemId=*)   item_id="${2#itemId=}" ;;
                optionId=*) option_id="${2#optionId=}" ;;
                body=*)     body="${2#body=}" ;;
                labels\[\]=*) label_value="${2#*=}" ;;
                name=*)     label_value="${2#name=}" ;;
            esac
            shift 2 ;;
        -X) method="$2"; shift 2 ;;
        --jq) jq_expr="$2"; shift 2 ;;
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
        [[ -f "$MOCK_DIR/fail_option_$option_id" ]] && { echo "fake gh: forced mutation failure" >&2; exit 1; }
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
                    updatedAt: (.updatedAt // "2026-09-27T00:00:00Z"),
                    content: {number: .number, title: .title, body: (.body // ""),
                              updatedAt: (.updatedAt // "2026-09-27T00:00:00Z")},
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
            # fail_posts_<n> holds how many comment writes on #<n> still fail (issue #348)
            if [[ -f "$MOCK_DIR/fail_posts_$n" ]]; then
                left=$(cat "$MOCK_DIR/fail_posts_$n")
                if (( left > 0 )); then
                    echo $((left - 1)) > "$MOCK_DIR/fail_posts_$n"
                    echo "fake gh: forced comment write failure" >&2; exit 1
                fi
            fi
            jq --arg b "$body" --arg t "$(tick)" --arg u "$MOCK_BOT" \
                '. + [{user: {login: $u}, created_at: $t, body: $b, posted: true}]' "$file" > "$file.tmp"
            mv "$file.tmp" "$file"
            jq -c '.[-1] | {created_at}' "$file"
        else
            cat "$file"
        fi ;;
    repos/*/pulls/[0-9]*)  # one pull request, with --jq applied as the real gh does
        n="${path##*/}"
        [[ "$n" =~ ^[0-9]+$ ]] || { echo '[]'; exit 0; }
        [[ -f "$MOCK_DIR/fail_pr_read" ]] && { echo "fake gh: forced pull request read failure" >&2; exit 1; }
        pr=$(jq --argjson n "$n" '.[] | select(.number == $n)' "$MOCK_DIR/pulls.json")
        [[ -n "$pr" ]] || { echo "fake gh: no pull request $n" >&2; exit 1; }
        if [[ -n "$jq_expr" ]]; then jq -r "$jq_expr" <<< "$pr"; else echo "$pr"; fi ;;
    repos/*/issues/*/labels|repos/*/issues/*/labels/*)  # add or remove one label (issue #348)
        n="${path#*/issues/}"; n="${n%%/*}"
        file="$MOCK_DIR/issue_$n.json"
        [[ -f "$file" ]] || { echo "fake gh: no issue $n" >&2; exit 1; }
        [[ -f "$MOCK_DIR/fail_labels" ]] && { echo "fake gh: forced label write failure" >&2; exit 1; }
        echo "$method $path" >> "$MOCK_DIR/labels.log"
        if [[ "$method" == "DELETE" ]]; then
            name="${path##*/labels/}"; name="${name//%3A/:}"
            jq --arg l "$name" '.labels |= map(select(.name != $l))' "$file" > "$file.tmp"
        else
            name="${label_value:-}"
            # as GitHub: adding a label the repository does not have is a 404
            grep -qxF "$name" "$MOCK_DIR/repo_labels" 2>/dev/null \
                || { echo "fake gh: HTTP 404: label $name not found" >&2; exit 1; }
            jq --arg l "$name" '.labels |= ((. // []) + [{name: $l}] | unique_by(.name))' "$file" > "$file.tmp"
        fi
        mv "$file.tmp" "$file"
        echo '[]' ;;
    repos/*/labels)  # create a repository label; 422 when it already exists (issue #348)
        echo "$method $path $label_value" >> "$MOCK_DIR/labels.log"
        if grep -qxF "$label_value" "$MOCK_DIR/repo_labels" 2>/dev/null; then
            echo "fake gh: HTTP 422: label already exists" >&2; exit 1
        fi
        echo "$label_value" >> "$MOCK_DIR/repo_labels"
        echo '{}' ;;
    repos/*/issues/*)
        n="${path##*/}"
        [[ -f "$MOCK_DIR/issue_$n.json" ]] || { echo "fake gh: no issue $n" >&2; exit 1; }
        cat "$MOCK_DIR/issue_$n.json" ;;
    repos/*/issues)  # GET /repos/{r}/issues?labels=<one label>, with --jq applied as the real gh does
        want=$(sed -E 's/.*[?&]labels=([^&]*).*/\1/; s/%3A/:/g' <<< "$endpoint")
        all=$(for f in "$MOCK_DIR"/issue_*.json; do [[ -f "$f" ]] && cat "$f"; done | jq -s --arg l "$want" '[.[] | select(any(.labels[]?; .name == $l))]')
        if [[ -n "$jq_expr" ]]; then jq -r "$jq_expr" <<< "$all"; else echo "$all"; fi ;;
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
url=""
for arg in "$@"; do
    case "$arg" in
        http*://*) url="$arg" ;;
    esac
done
echo "other $url" >> "$MOCK_DIR/curl.log"
if [[ "$url" == *hooks.slack* ]]; then
    echo "slack" >> "$MOCK_DIR/seq.log"
    [[ -f "$MOCK_DIR/fail_slack" ]] && exit 22
fi
exit 0
"""

# Claude Code on CLAUDE_BIN (issue #314). Call n keeps its argv (one per line),
# stdin and environment in $MOCK_DIR/claude_<n>.{argv,stdin,env}. Output is
# $MOCK_DIR/claude_stream.jsonl with exit code $MOCK_DIR/claude_exit when the
# test wrote one, else a successful session with one tool call. A command
# marked failing ends with an error result and exit 1.
FAKE_CLAUDE = r"""#!/usr/bin/env bash
set -uo pipefail
n=$(cat "$MOCK_DIR/claude.count" 2>/dev/null || echo 0)
n=$((n + 1))
echo "$n" > "$MOCK_DIR/claude.count"
printf '%s\n' "$@" > "$MOCK_DIR/claude_$n.argv"
cat > "$MOCK_DIR/claude_$n.stdin"
env > "$MOCK_DIR/claude_$n.env"
first_line=$(head -n 1 "$MOCK_DIR/claude_$n.stdin")
case "$first_line" in
    "Implement issue #"*) command=implement ;;
    "Review PR #"*)       command=review ;;
    "Merge PR #"*)        command=merge ;;
    "Clean up"*)          command=cleanup ;;
    *)                    command=unknown ;;
esac
target=$(sed -E 's/^[^#]*#([0-9]+).*/\1/' <<< "$first_line")
echo "session $command $target claude-code" >> "$MOCK_DIR/sessions.log"
echo "session $command $target" >> "$MOCK_DIR/seq.log"
if [[ -f "$MOCK_DIR/claude_sleep" ]]; then
    sleep "$(cat "$MOCK_DIR/claude_sleep")"
fi
if [[ -f "$MOCK_DIR/fail_$command" ]]; then
    echo '{"type":"system","subtype":"init","apiKeySource":"none","model":"fake"}'
    echo '{"type":"result","subtype":"error_during_execution","is_error":true,"num_turns":1,"duration_ms":1000,"usage":{"input_tokens":1,"output_tokens":1}}'
    exit 1
fi
[[ -f "$MOCK_DIR/on_$command.sh" ]] && bash "$MOCK_DIR/on_$command.sh"
if [[ -f "$MOCK_DIR/claude_stream.jsonl" ]]; then
    cat "$MOCK_DIR/claude_stream.jsonl"
    exit "$(cat "$MOCK_DIR/claude_exit" 2>/dev/null || echo 0)"
fi
echo '{"type":"system","subtype":"init","apiKeySource":"none","model":"fake"}'
echo '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"git status"}}]}}'
echo '{"type":"result","subtype":"success","is_error":false,"num_turns":2,"duration_ms":3000,"result":"done","usage":{"input_tokens":10,"output_tokens":5}}'
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


# Stand-ins for scripts/dispatcher-merge.sh and dispatcher-cleanup.sh (issue
# #264), on MERGE_SCRIPT / CLEANUP_SCRIPT. They log "<phase> <target>" to
# phases.log and fail when $MOCK_DIR/fail_<phase> exists. $1 is the flag, $2
# the PR (merge) or issue (cleanup) number.
FAKE_PHASE = r"""#!/usr/bin/env bash
echo "PHASE_NAME $2" >> "$MOCK_DIR/phases.log"
echo "PHASE_NAME $2" >> "$MOCK_DIR/seq.log"
[[ -f "$MOCK_DIR/on_PHASE_NAME.sh" ]] && bash "$MOCK_DIR/on_PHASE_NAME.sh"
[[ -f "$MOCK_DIR/fail_PHASE_NAME" ]] && { echo "fake PHASE_NAME script failure" >&2; exit 1; }
if [[ "PHASE_NAME" == merge && ! -f "$MOCK_DIR/no_confirmation" ]]; then  # the confirmation the real script posts
    f="$MOCK_DIR/comments_$2.json"; [[ -f "$f" ]] || echo '[]' > "$f"
    jq --arg b "$MOCK_BOT" '. + [{user: {login: $b}, body: "Merge gate: pass head=fake\n\n## Session Summary\nCommand: merge", created_at: "2026-10-13T09:31:00Z"}]' "$f" > "$f.tmp" && mv "$f.tmp" "$f"
fi
exit 0
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
        for name, text in (("gh", FAKE_GH), ("curl", FAKE_CURL), ("claude", FAKE_CLAUDE),
                           ("allowance.sh", FAKE_ALLOWANCE),
                           ("merge.sh", FAKE_PHASE.replace("PHASE_NAME", "merge")),
                           ("cleanup.sh", FAKE_PHASE.replace("PHASE_NAME", "cleanup"))):
            path = self.bin / name
            path.write_text(text, encoding="utf-8", newline="\n")
            path.chmod(0o755)
        self.mock = tmp / "mock"
        self.mock.mkdir()
        (self.mock / "usage").mkdir()
        self._write("state.json", {})
        self._write("pulls.json", [])
        self._write("board.json", {"items": [], "options": options or BOARD_OPTIONS})
        for name in ("gh.log", "curl.log", "sessions.log", "phases.log", "github_output", "mutations.log", "seq.log"):
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
               state: str = "open", merged: bool = False, **extra) -> None:
        pulls = [pr for pr in self._read("pulls.json") if pr["number"] != number]
        pulls.append({"number": number, "body": f"Closes #{issue}", "state": state, "draft": draft,
                      "merged_at": "2026-10-13T09:30:00Z" if merged else None,
                      "updated_at": "2026-09-27T00:00:00Z", "head": {"ref": "branch", "sha": head}, **extra})
        self._write("pulls.json", pulls)
        if merged:  # a PR merged by an earlier run carries the dispatcher's confirmation
            self.add_comment(number, "Merge gate: pass head=fake\n\n## Session Summary\nCommand: merge")

    def add_comment(self, target: int, body: str, login: str = BOT, at: str | None = None) -> None:
        tick = int((self.mock / "clock").read_text()) + 1 if (self.mock / "clock").exists() else 1
        (self.mock / "clock").write_text(str(tick))
        comments = self._read(f"comments_{target}.json", [])
        comments.append({"user": {"login": login}, "author_association": "COLLABORATOR", "body": body,
                         "created_at": at or f"2026-10-13T10:{tick // 60:02d}:{tick % 60:02d}Z"})
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
        """Bash run by the fake executors when a ``command`` session starts (``$MOCK_DIR`` is set)."""
        (self.mock / f"on_{command}.sh").write_text(script, encoding="utf-8", newline="\n")

    def session_sets_status(self, command: str, number: int, status: str) -> None:
        self.on_session(command, f"""jq '. + {{"ITEM_{number}": "{status}"}}' "$MOCK_DIR/state.json" > "$MOCK_DIR/s.tmp" && mv "$MOCK_DIR/s.tmp" "$MOCK_DIR/state.json"\n""")

    def fail_status_write(self, status: str, failing: bool = True) -> None:
        """Make the board write of ``status`` fail (a GraphQL error)."""
        path = self.mock / f"fail_option_{self._read('board.json')['options'][status]}"
        path.write_text("") if failing else path.unlink(missing_ok=True)

    def fail_pr_read(self, failing: bool = True) -> None:
        """Make the read of a single pull request (``pulls/<n>``) fail (issue #381)."""
        path = self.mock / "fail_pr_read"
        path.write_text("") if failing else path.unlink(missing_ok=True)

    def fail_comment_writes(self, target: int, times: int) -> None:
        """The next ``times`` comment writes on issue or PR ``target`` fail (an HTTP 500)."""
        (self.mock / f"fail_posts_{target}").write_text(str(times), encoding="utf-8")

    def fail(self, command: str, failing: bool = True) -> None:
        """Make a /implement or /review session, or the /merge or /cleanup script, fail."""
        path = self.mock / f"fail_{command}"
        path.write_text("") if failing else path.unlink(missing_ok=True)

    def claude_stream(self, *events, exit_code: int = 0) -> None:
        """Make the fake ``claude`` print these stream-json lines (dicts or raw strings) and exit."""
        lines = [e if isinstance(e, str) else json.dumps(e) for e in events]
        (self.mock / "claude_stream.jsonl").write_text("".join(f"{line}\n" for line in lines),
                                                       encoding="utf-8", newline="\n")
        (self.mock / "claude_exit").write_text(str(exit_code))

    def claude_sleeps(self, seconds: int) -> None:
        (self.mock / "claude_sleep").write_text(str(seconds))

    # ── running ─────────────────────────────────────────────────────────────

    def run(self, *args: str, seq: str = "0", env: dict[str, str] | None = None):
        full_env = {
            "HOME": self.tmp.as_posix(),
            "PATH": script_path(self.bin.as_posix()),
            "MOCK_DIR": self.mock.as_posix(),
            "MOCK_BOT": BOT,
            "LC_ALL": "C.UTF-8",  # grep -P in the dispatcher needs a UTF-8 or unibyte locale
            "GH_TOKEN": "fake-token",
            "GITHUB_OUTPUT": (self.mock / "github_output").as_posix(),
            "CLAUDE_CODE_OAUTH_TOKEN": "fake-subscription-token",
            "CLAUDE_BIN": (self.bin / "claude").as_posix(),
            "SLACK_WEBHOOK_URL": "https://hooks.slack.test/T000/B000",
            "DISPATCHER_STATE_DIR": (self.mock / "state").as_posix(),
            "USAGE_LIMIT_STATE_DIR": (self.mock / "usage").as_posix(),
            "USAGE_LIMIT_SESSIONS_PER_DAY": "1000",
            "WIP_LIMIT_IMPLEMENTATION": "2", "READY_BUFFER_MIN": "3", "READY_BUFFER_MAX": "5",
            "AGENT_MAX_TURNS": "3", "AGENT_MAX_WALLCLOCK_SECONDS": "600",
            "ALLOWANCE_SCRIPT": (self.bin / "allowance.sh").as_posix(),
            "ALLOWANCE_SEQ": seq,
            "MERGE_SCRIPT": (self.bin / "merge.sh").as_posix(),
            "CLEANUP_SCRIPT": (self.bin / "cleanup.sh").as_posix(),
        }
        full_env.update(windows_env())
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
        return [" ".join(line.split()[1:3]) for line in self._log("sessions.log")]

    @property
    def sequence(self) -> list[str]:
        """Slack calls (``slack``), phases and sessions in one order (issue #349)."""
        return self._log("seq.log")

    @property
    def phases(self) -> list[str]:
        """Script-only phases run (issue #264), in order, as ``"<merge|cleanup> <target>"``."""
        return self._log("phases.log")

    @property
    def session_providers(self) -> list[str]:
        """Sessions started, in order, as ``"<command> <provider>"``."""
        return [" ".join(line.split()[1::2]) for line in self._log("sessions.log")]

    @property
    def claude_calls(self) -> list[dict]:
        """Every fake ``claude`` call, in order: ``argv`` (list), ``stdin`` and ``env`` (dict)."""
        calls = []
        count = int((self.mock / "claude.count").read_text()) if (self.mock / "claude.count").exists() else 0
        for n in range(1, count + 1):
            env = {}
            for line in (self.mock / f"claude_{n}.env").read_text(encoding="utf-8").splitlines():
                key, sep, value = line.partition("=")
                if sep:
                    env[key] = value
            calls.append({
                "argv": (self.mock / f"claude_{n}.argv").read_text(encoding="utf-8").splitlines(),
                "stdin": (self.mock / f"claude_{n}.stdin").read_bytes().decode("utf-8"),
                "env": env,
            })
        return calls

    @property
    def queued_events(self) -> list[dict]:
        """Actionable notifications queued for scripts/dispatcher-notify-retry.sh (issue #295)."""
        path = self.mock / "state" / "o" / "r" / "actionable-events"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    @property
    def slack_calls(self) -> list[str]:
        return [line for line in self._log("curl.log") if "hooks.slack" in line]

    @property
    def allowance_calls(self) -> int:
        path = self.mock / "allowance.count"
        return int(path.read_text()) if path.exists() else 0

    @property
    def usage_count(self) -> int:
        """Sessions counted today by the usage limit."""
        path = self.mock / "usage" / "sessions"
        return int(path.read_text().strip().split(":")[1]) if path.exists() else 0

    @property
    def usage_output(self) -> str:
        return (self.mock / "github_output").read_text()

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
