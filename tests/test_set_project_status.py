"""``scripts/set-project-status`` writes a story's board status and reads it back (issue #318).

``gh`` is faked: a script on PATH that answers the board read and the status
mutation from a JSON state file, so each failure mode can be switched on.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "set-project-status"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(
    not all(shutil.which(t) for t in ("bash", "jq")), reason="requires bash and jq"
)

OPTIONS = {"Backlog": "O1", "In implementation": "O2", "In review": "O3", "Done": "O4"}

FAKE_GH = r"""#!/usr/bin/env bash
set -euo pipefail
echo "gh $*" >> "$MOCK_DIR/gh.log"
if [[ "$1" == "auth" ]]; then
    [[ -f "$MOCK_DIR/noauth" ]] && exit 1
    exit 0
fi
[[ "$1 $2" == "api graphql" ]] || { echo "fake gh: unsupported: $*" >&2; exit 1; }
[[ -f "$MOCK_DIR/http_fail" ]] && { echo "HTTP 403: Resource not accessible by personal access token" >&2; exit 1; }
query=""
shift 2
while [[ $# -gt 0 ]]; do
    case "$1" in
        -f) [[ "$2" == query=* ]] && query="${2#query=}"; shift 2 ;;
        -F) shift 2 ;;
        *) shift ;;
    esac
done
state="$MOCK_DIR/state.json"
if [[ "$query" == *mutation* ]]; then
    [[ -f "$MOCK_DIR/graphql_error" ]] && { echo '{"errors":[{"message":"Resource not accessible"}]}'; exit 0; }
    # the write: stale read-back keeps the old status
    if [[ ! -f "$MOCK_DIR/lost_write" ]]; then
        jq '.status = .next' "$state" > "$state.tmp" && mv "$state.tmp" "$state"
    fi
    echo '{"data":{}}'
    exit 0
fi
jq '{data: {repository: {
  projectsV2: {nodes: [{id: "P1", fields: {nodes: [{id: "F1", name: "Status",
     options: (.options | to_entries | map({id: .value, name: .key}))}]}}]},
  issue: {projectItems: {nodes: (if .on_board then [{id: "I1", project: {id: "P1"},
     fieldValueByName: (if .status then {name: .status} else null end)}] else [] end)}}}}}' "$state"
"""


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8", newline="\n")
    gh.chmod(0o755)
    mock = tmp_path / "mock"
    mock.mkdir()
    return {"mock": mock, "bin": bin_dir}


def set_state(env, status="Backlog", on_board=True):
    (env["mock"] / "state.json").write_text(
        json.dumps({"status": status, "next": None, "on_board": on_board, "options": OPTIONS}),
        encoding="utf-8",
    )


def run(env, *args, token="secret-token-value", extra=None):
    state_file = env["mock"] / "state.json"
    # the fake applies the requested status on a mutation: record it up front
    status = args[args.index("--status") + 1] if "--status" in args else None
    if state_file.exists():
        state = json.loads(state_file.read_text(encoding="utf-8"))
        state["next"] = status
        state_file.write_text(json.dumps(state), encoding="utf-8")
    environ = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN", "GITHUB_REPOSITORY")}
    environ["PATH"] = f"{env['bin']}{os.pathsep}{environ['PATH']}"
    environ["MOCK_DIR"] = str(env["mock"])
    if token:
        environ["GH_TOKEN"] = token
    environ.update(extra or {})
    return subprocess.run([BASH, str(SCRIPT), *args], capture_output=True, text=True, env=environ, check=False)


ARGS = ("--issue", "7", "--repo", "o/r", "--status")


def test_changes_status_and_reads_it_back(env):
    set_state(env, "Backlog")
    proc = run(env, *ARGS, "In review")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "Status: #7 Backlog -> In review"
    assert json.loads((env["mock"] / "state.json").read_text())["status"] == "In review"
    assert "secret-token-value" not in proc.stdout + proc.stderr + (env["mock"] / "gh.log").read_text()


def test_already_set_writes_nothing(env):
    set_state(env, "In review")
    proc = run(env, *ARGS, "In review")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "Status: #7 already In review"
    assert "mutation" not in (env["mock"] / "gh.log").read_text()


def test_dry_run_changes_nothing(env):
    set_state(env, "Backlog")
    proc = run(env, *ARGS, "Done", "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert "Would set #7" in proc.stdout and '"Done"' in proc.stdout
    assert json.loads((env["mock"] / "state.json").read_text())["status"] == "Backlog"


def test_no_token_exits_1(env):
    set_state(env)
    (env["mock"] / "noauth").write_text("")
    proc = run(env, *ARGS, "Done", token="")
    assert proc.returncode == 1
    assert "no GH_TOKEN" in proc.stderr and "PROJECT_TOKEN" in proc.stderr


def test_refused_call_exits_1_naming_scope(env):
    set_state(env)
    (env["mock"] / "http_fail").write_text("")
    proc = run(env, *ARGS, "Done")
    assert proc.returncode == 1
    assert "'project'" in proc.stderr and "PROJECT_TOKEN" in proc.stderr
    assert "secret-token-value" not in proc.stderr


def test_graphql_error_in_write_exits_1(env):
    set_state(env)
    (env["mock"] / "graphql_error").write_text("")
    proc = run(env, *ARGS, "Done")
    assert proc.returncode == 1
    assert "write status" in proc.stderr


def test_unknown_status_exits_1(env):
    set_state(env)
    proc = run(env, *ARGS, "Nonsense")
    assert proc.returncode == 1
    assert "'Nonsense' is not a Status option" in proc.stderr


def test_issue_not_on_board_exits_1(env):
    set_state(env, on_board=False)
    proc = run(env, *ARGS, "Done")
    assert proc.returncode == 1
    assert "not on the project board" in proc.stderr


def test_read_back_disagreeing_with_write_exits_1(env):
    set_state(env, "Backlog")
    (env["mock"] / "lost_write").write_text("")
    proc = run(env, *ARGS, "Done")
    assert proc.returncode == 1
    assert "read back" in proc.stderr and "'Backlog'" in proc.stderr


@pytest.mark.parametrize(
    "args",
    [
        ("--repo", "o/r", "--status", "Done"),
        ("--issue", "x", "--repo", "o/r", "--status", "Done"),
        ("--issue", "7", "--repo", "o/r"),
        ("--issue", "7", "--repo", "nope", "--status", "Done"),
        ("--issue", "7", "--repo", "o/r", "--status", "Done", "--bogus"),
    ],
)
def test_invalid_arguments_exit_2(env, args):
    set_state(env)
    assert run(env, *args).returncode == 2


# ── Session environment (AC10) ──────────────────────────────────────────────


def test_session_environment_keeps_gh_token_and_drops_anthropic_variables():
    helper = REPO_ROOT / "scripts" / "lib" / "claude-session.sh"
    environ = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_")}
    environ.update({"GH_TOKEN": "gh-pat", "ANTHROPIC_AUTH_TOKEN": "x", "ANTHROPIC_BASE_URL": "https://p.test"})
    environ["ANTHROPIC_" + "API_KEY"] = "y"
    proc = subprocess.run(
        [BASH, "-c", f'source "{helper.as_posix()}"; claude_scrubbed_exec tok env'],
        capture_output=True, text=True, env=environ, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    seen = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
    assert seen["GH_TOKEN"] == "gh-pat"
    assert [k for k in seen if k.startswith("ANTHROPIC_")] == []


# ── Command files name the command (AC6) ────────────────────────────────────

COMMAND = "scripts/set-project-status"
STATUS_TEXT = re.compile(r"board status|story status|Project/status field|Project status", re.IGNORECASE)


def test_every_command_that_writes_a_status_names_the_command():
    checked = []
    for path in sorted((REPO_ROOT / ".claude" / "commands").glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if path.name in ("spec.md", "verify-closure.md"):
            continue  # these never write a status
        if re.search(r"set the story status|move the GitHub Project/status field|Board status writes", text, re.IGNORECASE):
            checked.append(path.name)
            assert COMMAND in text, f"{path.name} writes a board status but does not name {COMMAND}"
    assert {"implement.md", "review.md", "merge.md", "refine-story.md", "refine-bug.md"} <= set(checked)


def test_documented_in_dispatcher_config():
    text = (REPO_ROOT / "docs" / "DISPATCHER-CONFIG.md").read_text(encoding="utf-8")
    assert COMMAND in text
