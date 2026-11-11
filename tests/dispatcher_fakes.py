"""Fakes for tests that drive the real ``scripts/dispatcher-invoke.sh`` end to end.

A fake ``gh`` (board, issues, pull requests, comments), a fake ``claude``
on ``CLAUDE_BIN`` (Claude sessions, issue #314), a fake ``curl`` (the Slack
webhook) and a stub allowance script on
``ALLOWANCE_SCRIPT``. A fake ``hpw`` on ``HPW_BIN`` runs the real verified-write
block (``hpw dispatcher write``, issue #432) with ``gh`` started as ``bash <fake gh>``
(``tests/hpw_dispatcher_route.py``), so the block reads and writes the same fake
state on Linux and Windows. All state lives in files under ``MOCK_DIR``, so a test
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

The fake ``hpw`` answers ``hpw dispatcher scheduler next`` itself (issue #474): it
logs each call's arguments to ``scheduler.log`` (``scheduler_calls``) and ``scheduler``
to ``seq.log``, and prints the decisions a test set with ``schedule``, one per call,
then ``idle``. A decision is printed with the scheduler's own ``[SCHEDULER] ...``
stderr lines and exit code.

An opt-in fake ``git`` (``fake_git``, issue #439) keeps the pause path off the real
checkout: it logs every call (argv, one line each) to ``git.log`` (``git_calls``),
answers only ``rev-parse --abbrev-ref HEAD``, ``rev-list --count`` and
``status --porcelain`` with the configured unpushed count and dirty state, and fails
any other subcommand. ``destructive_calls`` is the guard over ``git.log`` and
``gh.log``: it lists every call that could delete, reset or move a story branch, or
close or re-point its PR.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from tests.hpw_dispatcher_route import fake_hpw_route
from tests.script_tools import BASH, missing_tools, requires, run_tree, script_path, windows_env

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

FAKE_SCHEDULER = r"""if [[ "${1:-} ${2:-} ${3:-}" == "dispatcher scheduler next" ]]; then
    echo "$*" >> "$MOCK_DIR/scheduler.log"
    echo "scheduler" >> "$MOCK_DIR/seq.log"
    n=$(( $(cat "$MOCK_DIR/scheduler.count" 2>/dev/null || echo 0) + 1 ))
    echo "$n" > "$MOCK_DIR/scheduler.count"
    if [[ -f "$MOCK_DIR/scheduler_facts.json" ]]; then  # the real rule on fixed facts
        shift
        PYTHONPATH="$HPW_FAKE_SRC" exec "$HPW_FAKE_PYTHON" -m headless_pr_workflow.dispatcher "$@" \
            --facts-file "$MOCK_DIR/scheduler_facts.json"
    fi
    if [[ -f "$MOCK_DIR/scheduler_$n.json" ]]; then
        cat "$MOCK_DIR/scheduler_$n.err" >&2
        cat "$MOCK_DIR/scheduler_$n.json"
        exit "$(cat "$MOCK_DIR/scheduler_$n.rc")"
    fi
    echo "[SCHEDULER] idle" >&2
    echo '{"ok":true,"block":"scheduler","operation":"next","result":"idle","action":null,"skipped":[],"notes":[]}'
    exit 1
fi
# The facts the resume pass reads before a fix round, conflict round, /review or /merge (#475),
# when a test set them (Dispatcher.set_facts); otherwise the real block runs on the fake gh.
if [[ "${1:-} ${2:-} ${3:-}" == "dispatcher facts read" && -f "$MOCK_DIR/facts_read.json" ]]; then
    echo "facts" >> "$MOCK_DIR/facts.log"
    cat "$MOCK_DIR/facts_read.json"
    exit "$(cat "$MOCK_DIR/facts_read.rc")"
fi
"""


def scheduler_output(result: str, issue: int | None = None, *, error: str = "",
                     skipped: list[dict] | None = None) -> tuple[str, str, int]:
    """The stdout, stderr and exit code of ``hpw dispatcher scheduler next`` for one decision (#473)."""
    skipped = skipped or []
    action = None
    lines = []
    if result in ("refine", "implement"):
        action = {"kind": result, "issue": issue, "type": "story", "epic": 306, "step": 1, "reason": "test"}
        lines.append(f"[SCHEDULER] {result} #{issue} step=1 reason=test")
    elif result == "idle":
        lines.append("[SCHEDULER] idle")
    else:
        lines.append(f"[SCHEDULER] unreadable: {error}")
    lines += [f"[SCHEDULER] skip #{e['issue']} {e['reason']}" for e in skipped]
    out = {"ok": result in ("refine", "implement"), "block": "scheduler", "operation": "next", "result": result,
           "action": action, "skipped": skipped, "notes": []}
    if result == "unreadable":
        out["error"] = error
    rc = {"refine": 0, "implement": 0, "idle": 1}.get(result, 3)
    return json.dumps(out), "".join(line + "\n" for line in lines), rc


FAKE_GIT = r"""#!/usr/bin/env bash
echo "git $*" >> "$MOCK_DIR/git.log"
while [[ "${1:-}" == "-C" ]]; do shift 2; done
case "${1:-}" in
    rev-parse) [[ "${2:-}" == "--abbrev-ref" && "${3:-}" == "HEAD" ]] && { echo "fake-checkout-branch"; exit 0; } ;;
    rev-list)  [[ "${2:-}" == "--count" ]] && { cat "$MOCK_DIR/git_unpushed"; exit 0; } ;;
    status)    [[ "${2:-}" == "--porcelain" ]] && { [[ -s "$MOCK_DIR/git_dirty" ]] && cat "$MOCK_DIR/git_dirty"; exit 0; } ;;
esac
echo "fake git: unsupported: $*" >&2
exit 1
"""

GIT_READ_ONLY = ("rev-parse", "rev-list", "status")


def destructive_calls(git_log: list[str], gh_log: list[str]) -> list[str]:
    """Calls in the ``git`` and ``gh`` logs that could delete, reset or move a story branch.

    ``git``: allow-list, so a new mutating subcommand fails without a deny-list update.
    ``gh``: a ``git/refs`` endpoint, or a PATCH of a pull request that sets ``state`` or ``head``.
    """
    found = []
    for line in git_log:
        words = line.split()[1:]
        while words[:1] == ["-C"]:
            words = words[2:]
        if not words or words[0] not in GIT_READ_ONLY:
            found.append(line)
    for line in gh_log:
        words = line.split()
        patches_pr = ("-X" in words and words[words.index("-X") + 1:][:1] == ["PATCH"]
                      and any("/pulls/" in w for w in words)
                      and any(w.startswith(("state=", "head=", "base=")) for w in words))
        if "git/refs" in line or patches_pr:
            found.append(line)
    return found


FAKE_GH = r"""#!/usr/bin/env bash
set -euo pipefail
echo "gh $*" >> "$MOCK_DIR/gh.log"
[[ "${1:-}" == "api" ]] || { echo "fake gh: unsupported: $*" >&2; exit 1; }
shift
endpoint="" jq_expr="" query="" item_id="" option_id="" method="GET" body="" label_value="" number=""
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
        -F) case "$2" in number=*) number="${2#number=}" ;; esac; shift 2 ;;
        --input)  # a JSON request body on stdin, as the write block sends a comment (#432)
            [[ "$2" == - ]] && { body=$(jq -j '.body + "x"'); body="${body%x}"; }
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
        if [[ -f "$MOCK_DIR/lost_option_$option_id" ]]; then  # the write landed; the next reads fail
            mv "$MOCK_DIR/lost_option_$option_id" "$MOCK_DIR/fail_item_reads"
        fi
        echo '{}'
        exit 0
    fi
    if [[ "$query" == *projectItems* ]]; then  # one issue's board item, as the write block reads it (#432)
        reads=$(cat "$MOCK_DIR/fail_item_reads" 2>/dev/null || echo 0)
        if [[ "$reads" -gt 0 ]]; then
            echo $((reads - 1)) > "$MOCK_DIR/fail_item_reads"
            echo "fake gh: forced board read failure" >&2
            exit 1
        fi
        jq --slurpfile st "$MOCK_DIR/state.json" --argjson n "$number" '
            . as $b |
            {data:{repository:{
                projectsV2:{nodes:[{id: "PROJ", fields: {nodes: [{id: "STATUS_FIELD", name: "Status",
                    options: [$b.options | to_entries[] | {id: .value, name: .key}]}]}}]},
                issue:{projectItems:{nodes:[$b.items[] | select(.number == $n) |
                    {id: .id, project: {id: "PROJ"}, fieldValueByName: {name: ($st[0][.id] // .status)}}]}}
            }}}' "$MOCK_DIR/board.json"
        exit 0
    fi
    # The Nth board read uses board_N.json when a test set one (Dispatcher.board_read_once).
    reads=$(( $(cat "$MOCK_DIR/board.reads" 2>/dev/null || echo 0) + 1 ))
    echo "$reads" > "$MOCK_DIR/board.reads"
    board_file="$MOCK_DIR/board.json"
    [[ -f "$MOCK_DIR/board_$reads.json" ]] && board_file="$MOCK_DIR/board_$reads.json"
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
        }]}}}}' "$board_file"
    exit 0
fi

path="${endpoint%%\?*}"
case "$path" in
    user)
        echo "{\"login\": \"$MOCK_BOT\"}" ;;
    repos/*/commits/*/check-runs)  # the pytest check of a head: green unless checks_<sha> says otherwise (#449)
        sha="${path#*/commits/}"; sha="${sha%%/*}"
        result=success
        [[ -f "$MOCK_DIR/checks_$sha" ]] && result=$(cat "$MOCK_DIR/checks_$sha")
        case "$result" in
            absent)  echo '{"total_count": 0, "check_runs": []}' ;;
            pending) echo '{"total_count": 1, "check_runs": [{"name": "pytest", "status": "in_progress", "conclusion": null, "started_at": "2026-10-13T10:00:00Z"}]}' ;;
            *)       echo "{\"total_count\": 1, \"check_runs\": [{\"name\": \"pytest\", \"status\": \"completed\", \"conclusion\": \"$result\", \"started_at\": \"2026-10-13T10:00:00Z\"}]}" ;;
        esac ;;
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
            jq -c '.[-1] | {created_at, user, body}' "$file"
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
        # Without labels=, the open issues (issue #475), each with its comment count as GitHub returns it.
        want=""
        [[ "$endpoint" == *labels=* ]] && want=$(sed -E 's/.*[?&]labels=([^&]*).*/\1/; s/%3A/:/g' <<< "$endpoint")
        all=$(for f in "$MOCK_DIR"/issue_*.json; do
                  [[ -f "$f" ]] || continue
                  num=$(jq -r '.number' "$f"); count=0
                  [[ -f "$MOCK_DIR/comments_$num.json" ]] && count=$(jq 'length' "$MOCK_DIR/comments_$num.json")
                  jq -c --argjson c "$count" '. + {comments: $c}' "$f"
              done | jq -s --arg l "$want" '[.[] | select(($l == "" and (.state // "open") == "open") or any(.labels[]?; .name == $l and $l != ""))]')
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

# Stands in for scripts/merge-gate-summary in the resume pass and the poll (issue #475). A gate
# file ``gate_<pr>`` sets the result (``pass``, ``fail``, ``unreadable``, ``conflicting``: all gates
# but mergeability pass); without one the gates pass only when the linked story's board status is
# "In merge", so a test written for the old board trigger keeps its meaning.
FAKE_GATE = r"""#!/usr/bin/env bash
pr=""
while [[ $# -gt 0 ]]; do case "$1" in --pr) pr="$2"; shift 2 ;; *) shift ;; esac; done
echo "gate $pr" >> "$MOCK_DIR/gates.log"
head=$(jq -r --argjson n "$pr" '[.[] | select(.number == $n)][0].head.sha[0:7] // "0000000"' "$MOCK_DIR/pulls.json")
mode=""
[[ -f "$MOCK_DIR/gate_$pr" ]] && mode=$(cat "$MOCK_DIR/gate_$pr")
if [[ -z "$mode" ]]; then
    issue=$(jq -r --argjson n "$pr" '[.[] | select(.number == $n)][0].body // "" | capture("[Cc]loses #(?<i>[0-9]+)").i // empty' "$MOCK_DIR/pulls.json")
    status=$(jq -r --arg i "ITEM_$issue" --slurpfile b "$MOCK_DIR/board.json" '.[$i] // ([$b[0].items[] | select(.id == $i)][0].status // "")' "$MOCK_DIR/state.json")
    mode=fail
    [[ "$status" == "In merge" ]] && mode=pass
fi
case "$mode" in
    pass)        echo "Merge gate: policy=pass(present_non_required), checks=pass, approval=separate-session, threads=none, mergeable=yes, head=$head" ;;
    conflicting) echo "Merge gate: policy=pass(present_non_required), checks=pass, approval=separate-session, threads=none, mergeable=no, head=$head"; exit 1 ;;
    unreadable)  echo "merge-gate-summary: could not read the pull request" >&2; exit 2 ;;
    *)           echo "Merge gate: policy=pass(present_non_required), checks=pass, approval=none, threads=none, mergeable=yes, head=$head"; exit 1 ;;
esac
"""

# The merge gates for the poll tests (issue #475), on MERGE_GATE_SCRIPT: $GATES_FILE maps a PR number
# to ``pass``, ``fail`` (the default), ``conflicting`` or ``unreadable``; the head comes from $PULLS_FILE.
POLL_FAKE_GATE = r"""#!/usr/bin/env bash
pr=""
while [[ $# -gt 0 ]]; do case "$1" in --pr) pr="$2"; shift 2 ;; *) shift ;; esac; done
head=$(jq -r --argjson n "$pr" '[.[] | select(.number == $n)][0].head.sha[0:7] // "0000000"' "$PULLS_FILE")
mode=$(jq -r --arg n "$pr" '.[$n] // "fail"' "$GATES_FILE" 2>/dev/null || echo fail)
case "$mode" in
    pass)        echo "Merge gate: policy=pass(present_non_required), checks=pass, approval=separate-session, threads=none, mergeable=yes, head=$head" ;;
    conflicting) echo "Merge gate: policy=pass(present_non_required), checks=pass, approval=separate-session, threads=none, mergeable=no, head=$head"; exit 1 ;;
    unreadable)  echo "merge-gate-summary: could not read the pull request" >&2; exit 2 ;;
    *)           echo "Merge gate: policy=pass(present_non_required), checks=pass, approval=none, threads=none, mergeable=yes, head=$head"; exit 1 ;;
esac
"""


def poll_fakes(tmp_path: Path, bin_dir: Path, issues: list[tuple], gates: dict[int, str] | None = None,
               comments: dict[int, list] | None = None) -> dict[str, str]:
    """The open issues and merge gates scripts/dispatcher-poll.sh reads (issue #475).

    An issue is ``(number, labels)``: a ``type:story`` label is added unless one of ``type:`` is
    given. ``comments`` only sets each issue's comment count, as GitHub's issue list does; the
    caller's fake ``curl`` serves the comments. Returns the environment for the poll: ``ISSUES_FILE``
    (the caller's ``curl`` serves it for ``issues?state=open``), ``GATES_FILE`` and ``MERGE_GATE_SCRIPT``.
    """
    rows = []
    for number, labels in issues:
        names = list(labels) if any(name.startswith("type:") for name in labels) else ["type:story", *labels]
        rows.append({"number": number, "title": f"Story {number}", "labels": [{"name": n} for n in names],
                     "comments": len((comments or {}).get(number, []))})
    (tmp_path / "issues.json").write_text(json.dumps(rows), encoding="utf-8")
    (tmp_path / "gates.json").write_text(json.dumps({str(k): v for k, v in (gates or {}).items()}), encoding="utf-8")
    gate = bin_dir / "gate.sh"
    gate.write_text(POLL_FAKE_GATE, encoding="utf-8", newline="\n")
    gate.chmod(0o755)
    return {"ISSUES_FILE": (tmp_path / "issues.json").as_posix(), "GATES_FILE": (tmp_path / "gates.json").as_posix(),
            "MERGE_GATE_SCRIPT": gate.as_posix()}


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
    "Act as the Tech Lead"*) command=refine ;;
    *)                    command=unknown ;;
esac
[[ "$command" == refine ]] && first_line=$(grep -m1 '^Refine GitHub issue #' "$MOCK_DIR/claude_$n.stdin")
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
if [[ "$command" == implement && "$(cat "$MOCK_DIR/claude_exit" 2>/dev/null || echo 0)" == 0 ]]; then
    # A stopped session has not completed the PR hand-off (Dispatcher.add_pr, #475).
    for f in "$MOCK_DIR"/opened_on_implement_*.json; do
        [[ -f "$f" ]] || continue
        jq -e --arg target "Closes #$target" '.body == $target' "$f" >/dev/null || continue
        jq --slurpfile pr "$f" '. + $pr' "$MOCK_DIR/pulls.json" > "$MOCK_DIR/pulls.tmp"
        mv "$MOCK_DIR/pulls.tmp" "$MOCK_DIR/pulls.json"; rm -f "$f"
    done
    for f in "$MOCK_DIR"/ready_on_implement_*; do
        [[ -f "$f" ]] || continue
        pr="${f##*_}"
        jq -e --argjson n "$pr" --arg target "Closes #$target" \
            'any(.[]; .number == $n and .body == $target)' "$MOCK_DIR/pulls.json" >/dev/null || continue
        rm -f "$f"
        jq --argjson n "$pr" 'map(if .number == $n then .draft = false else . end)' "$MOCK_DIR/pulls.json" > "$MOCK_DIR/pulls.tmp"
        mv "$MOCK_DIR/pulls.tmp" "$MOCK_DIR/pulls.json"
    done
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
if [[ -f "$MOCK_DIR/fail_PHASE_NAME" ]]; then  # the file may hold an exit code (default 1)
    echo "fake PHASE_NAME script failure" >&2
    rc=$(cat "$MOCK_DIR/fail_PHASE_NAME"); exit "${rc:-1}"
fi
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
                           ("hpw", "#!/usr/bin/env bash\n" + FAKE_SCHEDULER
                                   + fake_hpw_route(self.bin / "gh", BASH or "bash")
                                   + 'echo "fake hpw: unsupported: $*" >&2\nexit 1\n'),
                           ("gate.sh", FAKE_GATE),
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

    def schedule(self, *decisions) -> None:
        """The scheduler's decisions, one per ``hpw dispatcher scheduler next`` call, then ``idle``.

        Each decision is ``(result, issue, error, skipped)``, cut short as needed:
        ``("implement", 12)``, ``("refine", 5)``, ``("idle",)``, ``("unreadable", None, "facts: down")``.
        """
        for n, decision in enumerate(decisions, start=1):
            result, issue, error, skipped = (*decision, *(None, None, "", None)[len(decision):])
            out, err, rc = scheduler_output(result, issue, error=error, skipped=skipped)
            (self.mock / f"scheduler_{n}.json").write_text(out, encoding="utf-8", newline="\n")
            (self.mock / f"scheduler_{n}.err").write_text(err, encoding="utf-8", newline="\n")
            (self.mock / f"scheduler_{n}.rc").write_text(str(rc))

    def set_facts(self, facts: dict | str, rc: int = 0) -> None:
        """What ``hpw dispatcher facts read`` prints (a dict, or raw text) and its exit code (#475)."""
        text = facts if isinstance(facts, str) else json.dumps(facts)
        (self.mock / "facts_read.json").write_text(text, encoding="utf-8", newline="\n")
        (self.mock / "facts_read.rc").write_text(str(rc))

    def schedule_from_facts(self, facts: dict) -> None:
        """Run the real scheduler rule (#473) on these facts (``--facts-file``) for every call."""
        self._write("scheduler_facts.json", facts)

    def fake_git(self, unpushed: int = 0, dirty: bool = False) -> None:
        """Put a fake ``git`` first on ``PATH`` (issue #439): it logs calls to ``git.log``."""
        path = self.bin / "git"
        path.write_text(FAKE_GIT, encoding="utf-8", newline="\n")
        path.chmod(0o755)
        (self.mock / "git.log").write_text("")
        (self.mock / "git_unpushed").write_text(f"{unpushed}\n")
        (self.mock / "git_dirty").write_text(" M file\n" if dirty else "")

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

    def add_pr(self, number: int, issue: int, head: str = "a" * 40, draft: bool | None = None,
               state: str = "open", merged: bool = False, owned: bool | None = None,
               opened_by_implement: bool = False, **extra) -> None:
        """An open PR that closes ``issue``.

        ``draft=None`` (the default) follows the story's board status, as a real /implement leaves the
        PR: for a story "Ready for implementation" the PR is a draft until its next /implement session
        marks it ready; otherwise it is ready. The resume pass finds work from these PR facts (#475),
        so a ready PR on an unstarted story would put the story in review. ``opened_by_implement``
        keeps the PR away until the next /implement session opens it (ready), so the story has no
        work in flight before it starts.
        """
        board_item = next((i for i in self._read("board.json")["items"] if i["number"] == issue), None)
        if opened_by_implement:
            self._write(f"opened_on_implement_{number}.json",
                        {"number": number, "body": f"Closes #{issue}", "state": "open", "draft": False,
                         "merged_at": None, "updated_at": "2026-09-27T00:00:00Z",
                         "head": {"ref": "branch", "sha": head}, "mergeable": True, "mergeable_state": "clean",
                         **extra})
            return
        if draft is None:
            draft = bool(board_item) and self.status(issue) == "Ready for implementation" and state == "open"
            ready_file = self.mock / f"ready_on_implement_{number}"
            ready_file.write_text("") if draft else ready_file.unlink(missing_ok=True)
        pulls = [pr for pr in self._read("pulls.json") if pr["number"] != number]
        # A PR is clean unless the test says otherwise (``mergeable`` /
        # ``mergeable_state``); None is GitHub's "not computed yet" (issue #409).
        pulls.append({"number": number, "body": f"Closes #{issue}", "state": state, "draft": draft,
                      "merged_at": "2026-10-13T09:30:00Z" if merged else None,
                      "updated_at": "2026-09-27T00:00:00Z", "head": {"ref": "branch", "sha": head},
                      "mergeable": True, "mergeable_state": "clean", **extra})
        self._write("pulls.json", pulls)
        # A story "In merge" is merged by the dispatcher only for a head it owns
        # (issue #406): the PR carries the owner marker unless the test says
        # ``owned=False``.
        if owned is None:
            owned = bool(board_item) and board_item["status"] == "In merge"
        if owned and not merged:
            self.add_comment(number, f"<!-- hpw:dispatcher action=merge-owner pr={number} head={head} -->")
        if merged:  # a PR merged by an earlier run carries the dispatcher's confirmation
            self.add_comment(number, "Merge gate: pass head=fake\n\n## Session Summary\nCommand: merge")

    def close_issue(self, number: int) -> None:
        """Close the issue, as GitHub does when its PR merges: the work in flight reads open issues only (#475)."""
        issue = self._read(f"issue_{number}.json")
        issue["state"] = "closed"
        self._write(f"issue_{number}.json", issue)

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

    def board_read_once(self, read: int) -> None:
        """Keep the board as it is now for the ``read``-th project board read only; later reads see later changes."""
        self._write(f"board_{read}.json", self._read("board.json"))

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

    def lose_status_read_back(self, status: str, reads: int) -> None:
        """The next board write of ``status`` lands, then the next ``reads`` board item reads fail."""
        option = self._read("board.json")["options"][status]
        (self.mock / f"lost_option_{option}").write_text(str(reads), encoding="utf-8")

    def fail_pr_read(self, failing: bool = True) -> None:
        """Make the read of a single pull request (``pulls/<n>``) fail (issue #381)."""
        path = self.mock / "fail_pr_read"
        path.write_text("") if failing else path.unlink(missing_ok=True)

    def set_gates(self, pr: int, result: str) -> None:
        """The merge gates of PR ``pr`` for the resume pass (issue #475): ``pass``, ``fail``, ``unreadable``
        or ``conflicting`` (every gate but mergeability passes). The default follows the story's board status."""
        (self.mock / f"gate_{pr}").write_text(result, encoding="utf-8")

    def set_pytest(self, head: str, result: str) -> None:
        """The required pytest check of ``head``: success (the default), failure, pending or absent (#449)."""
        (self.mock / f"checks_{head}").write_text(result, encoding="utf-8")

    def fail_comment_writes(self, target: int, times: int) -> None:
        """The next ``times`` comment writes on issue or PR ``target`` fail (an HTTP 500)."""
        (self.mock / f"fail_posts_{target}").write_text(str(times), encoding="utf-8")

    def fail(self, command: str, failing: bool = True, exit_code: int | None = None) -> None:
        """Make a /implement or /review session, or the /merge or /cleanup script, fail.

        ``exit_code`` is the exit code of a failing /merge or /cleanup script (default 1)."""
        path = self.mock / f"fail_{command}"
        path.write_text("" if exit_code is None else str(exit_code)) if failing else path.unlink(missing_ok=True)

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
            "MERGE_GATE_SCRIPT": (self.bin / "gate.sh").as_posix(),
            "CLEANUP_SCRIPT": (self.bin / "cleanup.sh").as_posix(),
            # The verified-write block (#432) runs for real, with gh as `bash <fake gh>`.
            "HPW_BIN": (self.bin / "hpw").as_posix(),
            "HPW_FAKE_PYTHON": Path(sys.executable).as_posix(),
            "HPW_FAKE_SRC": (REPO_ROOT / "src").as_posix(),
            "COMMENT_RETRY_DELAY": "0",
        }
        full_env.update(windows_env())
        full_env.update(env or {})
        (self.mock / "allowance.count").unlink(missing_ok=True)  # ALLOWANCE_SEQ counts per run
        return run_tree(
            [BASH, INVOKE.as_posix(), "--repo", "o/r", *args],
            env=full_env, encoding="utf-8", timeout=300,
        )

    def run_issue(self, number: int, *args: str, **kwargs):
        return self.run("--issue", str(number), *args, **kwargs)

    def run_refine(self, number: int | str, *args: str, **kwargs):
        return self.run("--refine", str(number), *args, **kwargs)

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
    def scheduler_calls(self) -> list[str]:
        """The arguments of every ``hpw dispatcher scheduler next`` call, in order."""
        return self._log("scheduler.log")

    @property
    def git_calls(self) -> list[str]:
        """Calls to the opt-in fake ``git`` (``fake_git``), in order, as ``"git <argv>"``."""
        return self._log("git.log")

    @property
    def destructive_calls(self) -> list[str]:
        """Calls that could delete, reset or move a story branch or close its PR (issue #439)."""
        return destructive_calls(self.git_calls, self._log("gh.log"))

    @property
    def pull_requests(self) -> list[dict]:
        """The fake PRs as stored (number, state, head)."""
        return self._read("pulls.json")

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
