"""WIP and queue limits are repository variables, not hardcoded (issue #358).

The shared check (``scripts/lib/limits.sh``) on its own, the real
``scripts/dispatcher-invoke.sh`` driven with the fakes in
``tests/dispatcher_fakes.py``, the real ``scripts/flow-review.sh`` against a fake
``curl``, and the workflow file and scripts as text.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.dispatcher_fakes import BASH, Dispatcher, needs_tools
from tests.script_tools import script_path, windows_env

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "scripts" / "lib" / "limits.sh"
FLOW_REVIEW = REPO_ROOT / "scripts" / "flow-review.sh"
POLL = REPO_ROOT / "scripts" / "dispatcher-poll.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "dispatcher.yml"
VARIABLES = ("WIP_LIMIT_IMPLEMENTATION", "READY_BUFFER_MIN", "READY_BUFFER_MAX")
GOOD = {"WIP_LIMIT_IMPLEMENTATION": "2", "READY_BUFFER_MIN": "3", "READY_BUFFER_MAX": "5"}
STORY = 500

pytestmark = needs_tools


def load(**values):
    """Source the shared check with exactly these variables; print what it set."""
    env = {k: v for k, v in os.environ.items() if k not in VARIABLES}
    env.update({k: v for k, v in values.items() if v is not None})
    script = f'source "{LIB.as_posix()}"; load_limits && echo "$WIP_LIMIT_IMPLEMENTATION $READY_BUFFER_MIN $READY_BUFFER_MAX"'
    return subprocess.run([BASH, "-c", script], env=env, capture_output=True, text=True)


# ── the shared check ────────────────────────────────────────────────────────


@pytest.mark.parametrize("wip", ["1", "3", "08"])
def test_valid_values_are_accepted(wip):
    proc = load(**{**GOOD, "WIP_LIMIT_IMPLEMENTATION": wip})
    assert proc.returncode == 0
    assert proc.stdout.split() == [str(int(wip)), "3", "5"]


def test_buffer_min_of_zero_is_accepted_and_min_may_equal_max():
    assert load(WIP_LIMIT_IMPLEMENTATION="2", READY_BUFFER_MIN="0", READY_BUFFER_MAX="0").returncode == 0
    assert load(WIP_LIMIT_IMPLEMENTATION="2", READY_BUFFER_MIN="4", READY_BUFFER_MAX="4").returncode == 0


@pytest.mark.parametrize("name", VARIABLES)
@pytest.mark.parametrize("value", [None, ""])
def test_unset_or_empty_variable_names_the_variable(name, value):
    proc = load(**{**GOOD, name: value})
    assert proc.returncode == 1
    assert proc.stdout == ""
    assert f"[LIMITS ERROR] {name} is not set or is empty" in proc.stderr


@pytest.mark.parametrize("value", ["abc", "0", "-1", "2.5", " 2", "1e3"])
def test_invalid_wip_limit_names_variable_and_value(value):
    proc = load(**{**GOOD, "WIP_LIMIT_IMPLEMENTATION": value})
    assert proc.returncode == 1
    assert "[LIMITS ERROR] WIP_LIMIT_IMPLEMENTATION" in proc.stderr
    assert f'"{value}"' in proc.stderr


@pytest.mark.parametrize("name", ["READY_BUFFER_MIN", "READY_BUFFER_MAX"])
@pytest.mark.parametrize("value", ["abc", "-1", "2.5"])
def test_invalid_buffer_value_fails(name, value):
    proc = load(**{**GOOD, name: value})
    assert proc.returncode == 1
    assert f"[LIMITS ERROR] {name} is not a whole number" in proc.stderr
    assert f'"{value}"' in proc.stderr


def test_buffer_min_greater_than_max_fails():
    proc = load(**{**GOOD, "READY_BUFFER_MIN": "6"})
    assert proc.returncode == 1
    assert "READY_BUFFER_MIN (6) is greater than READY_BUFFER_MAX (5)" in proc.stderr


# ── the dispatcher pre-flight ───────────────────────────────────────────────


@pytest.fixture
def d(tmp_path):
    return Dispatcher(tmp_path)


def add_active_stories(d, count):
    """Stories "In implementation" that are not stale (updated in the future), so none is rolled back."""
    for n in range(count):
        d.add_story(600 + n, "In implementation", body="## Files affected\n- `other/file.txt`\n")
    board = d._read("board.json")
    for item in board["items"]:
        item["updatedAt"] = "2999-01-01T00:00:00Z"
    d._write("board.json", board)


def preflight(d, active, **env):
    add_active_stories(d, active)
    d.add_story(STORY, "Ready for implementation", body="## Files affected\n- `scripts/x.sh`\n")
    return d.run_issue(STORY, "--dry-run", env=env)


def test_ac1_limit_3_blocks_at_three_and_passes_at_two(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    blocked = preflight(Dispatcher(tmp_path / "a"), 3, WIP_LIMIT_IMPLEMENTATION="3")
    assert "FAIL: WIP limit reached (3 >= 3)" in blocked.stdout
    assert blocked.returncode != 0
    passed = preflight(Dispatcher(tmp_path / "b"), 2, WIP_LIMIT_IMPLEMENTATION="3")
    assert "STALE" not in passed.stdout and "STALE" not in blocked.stdout
    assert "Active WIP count: 2 (limit: 3)" in passed.stdout
    assert "2 active / 3 limit" in passed.stdout
    assert "FAIL" not in passed.stdout


def test_ac1_blocker_text_shows_the_configured_limit(tmp_path):
    d = Dispatcher(tmp_path)
    add_active_stories(d, 3)
    d.add_story(STORY, "Ready for implementation", body="## Files affected\n- `scripts/x.sh`\n")
    proc = d.run_issue(STORY, env={"WIP_LIMIT_IMPLEMENTATION": "3"})
    assert proc.returncode != 0
    assert any('Fewer than 3 stories are in "In implementation"' in c for c in d.posted(STORY))


@pytest.mark.parametrize("name", VARIABLES)
def test_ac5_unset_variable_starts_nothing_and_writes_nothing(d, name):
    d.add_story(STORY, "Ready for implementation")
    proc = d.run_issue(STORY, env={name: ""})
    assert proc.returncode == 1
    assert f"[LIMITS ERROR] {name} is not set or is empty" in proc.stderr
    assert d.sessions == [] and d.board_writes == []
    assert d.posted(STORY) == []
    assert d.slack_calls == []
    assert d.status(STORY) == "Ready for implementation"


@pytest.mark.parametrize("value", ["abc", "0", "-1", "2.5"])
def test_ac3_invalid_wip_limit_starts_nothing(d, value):
    d.add_story(STORY, "Ready for implementation")
    proc = d.run_issue(STORY, env={"WIP_LIMIT_IMPLEMENTATION": value})
    assert proc.returncode == 1
    assert "WIP_LIMIT_IMPLEMENTATION" in proc.stderr and f'"{value}"' in proc.stderr
    assert d.sessions == [] and d.board_writes == [] and d.slack_calls == []


def test_ac4_invalid_buffer_pair_starts_nothing(d):
    d.add_story(STORY, "Ready for implementation")
    proc = d.run_issue(STORY, env={"READY_BUFFER_MIN": "9"})
    assert proc.returncode == 1
    assert "READY_BUFFER_MIN (9) is greater than READY_BUFFER_MAX (5)" in proc.stderr
    assert d.sessions == [] and d.board_writes == [] and d.slack_calls == []


# ── the flow review ─────────────────────────────────────────────────────────


def flow_review(tmp_path, in_implementation, ready=1, env=None, args=()):
    def node(n, status):
        return {"content": {"number": n, "title": f"Story {n}", "state": "OPEN", "closedAt": None,
                            "updatedAt": "2999-01-01T00:00:00Z", "labels": {"nodes": []}},
                "fieldValues": {"nodes": [{"name": status, "field": {"name": "Status"}}]}}
    nodes = [node(100 + i, "In implementation") for i in range(in_implementation)]
    nodes += [node(200 + i, "Ready for implementation") for i in range(ready)]
    projects = {"data": {"repository": {"projectsV2": {"nodes": [{"number": 3, "title": "board"}]}}}}
    items = {"data": {"repository": {"projectsV2": {"nodes": [{"items": {"nodes": nodes}}]}}}}
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    (tmp_path / "projects.json").write_text(json.dumps(projects), encoding="utf-8")
    (tmp_path / "items.json").write_text(json.dumps(items), encoding="utf-8")
    curl = bin_dir / "curl"
    curl.write_text('#!/usr/bin/env bash\nif [[ "$*" == *"projectsV2(first:10)"* ]]; then cat "$MOCK/projects.json"; '
                    'else cat "$MOCK/items.json"; fi\n', encoding="utf-8", newline="\n")
    curl.chmod(0o755)
    full = {"PATH": script_path(bin_dir.as_posix()), "GH_TOKEN": "fake", "HOME": tmp_path.as_posix(),
            "MOCK": tmp_path.as_posix(), **windows_env(), **(env if env is not None else {})}
    return subprocess.run([BASH, FLOW_REVIEW.as_posix(), "--repo", "o/r", *args], env=full,
                          capture_output=True, text=True, encoding="utf-8", timeout=120)


def test_ac2_limit_3_changes_report_violation_and_status(tmp_path):
    three = flow_review(tmp_path / "a", 3, env={**GOOD, "WIP_LIMIT_IMPLEMENTATION": "3"})
    assert three.returncode == 0, three.stderr
    assert "WIP count: 3/3" in three.stdout and "WIP limit exceeded" not in three.stdout
    assert "Yellow" in three.stdout
    four = flow_review(tmp_path / "b", 4, env={**GOOD, "WIP_LIMIT_IMPLEMENTATION": "3"})
    assert "WIP count: 4/3" in four.stdout and "WIP limit exceeded" in four.stdout and "Red" in four.stdout
    two = flow_review(tmp_path / "c", 2, env={**GOOD, "WIP_LIMIT_IMPLEMENTATION": "3"})
    assert "WIP count: 2/3" in two.stdout and "Green" in two.stdout and "Yellow" not in two.stdout
    zero_ready = flow_review(tmp_path / "d", 3, ready=0, env={**GOOD, "WIP_LIMIT_IMPLEMENTATION": "3"})
    assert "Red" in zero_ready.stdout


@pytest.mark.parametrize("env", [
    {k: v for k, v in GOOD.items() if k != "WIP_LIMIT_IMPLEMENTATION"},
    {**GOOD, "READY_BUFFER_MAX": ""},
    {**GOOD, "WIP_LIMIT_IMPLEMENTATION": "abc"},
])
def test_ac3_ac5_flow_review_reports_the_error_instead_of_a_status(tmp_path, env):
    proc = flow_review(tmp_path, 2, env=env)
    assert proc.returncode == 2
    assert proc.stdout == ""
    assert "[LIMITS ERROR]" in proc.stderr


def test_flow_review_json_is_also_an_error_without_the_variable(tmp_path):
    proc = flow_review(tmp_path, 2, env={}, args=("--json",))
    assert proc.returncode == 2 and proc.stdout == ""


def test_poll_does_not_hide_the_flow_review_error(tmp_path):
    board = {"data": {"repository": {"projectsV2": {"nodes": [{"number": 3, "title": "board", "items": {
        "pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}}]}}}}
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "board.json").write_text(json.dumps(board), encoding="utf-8")
    curl = bin_dir / "curl"
    curl.write_text('#!/usr/bin/env bash\ncat "$BOARD_FILE"\n', encoding="utf-8", newline="\n")
    curl.chmod(0o755)
    env = {"PATH": script_path(bin_dir.as_posix()), "GH_TOKEN": "fake", "HOME": tmp_path.as_posix(),
           "BOARD_FILE": (tmp_path / "board.json").as_posix(),
           "DISPATCHER_STATE_DIR": (tmp_path / "state").as_posix(), **windows_env()}
    proc = subprocess.run([BASH, POLL.as_posix(), "--repo", "o/r", "--dry-run"], env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 2
    assert "[LIMITS ERROR] WIP_LIMIT_IMPLEMENTATION is not set or is empty" in proc.stderr


# ── nothing hardcoded; the workflow passes the variables ────────────────────


def test_ac6_no_script_holds_a_limit_number_or_default():
    patterns = [r"\bWIP_LIMIT\s*=\s*\d", r"WIP_COUNT\"?\s*(-gt|-ge|-eq)\s*\"?\d", r"WIP_COUNT\}/\d",
                r"(WIP_LIMIT_IMPLEMENTATION|READY_BUFFER_M(IN|AX))[:]?-\d"]
    for script in sorted((REPO_ROOT / "scripts").rglob("*.sh")):
        text = script.read_text(encoding="utf-8")
        for pattern in patterns:
            assert not re.search(pattern, text), f"{script.name}: {pattern}"


def test_workflow_passes_the_variables_to_the_poll_and_invoke_steps():
    text = WORKFLOW.read_text(encoding="utf-8")
    for step_id in ("poll", "invoke"):
        block = text.split(f"id: {step_id}\n", 1)[1].split("\n      - name:", 1)[0]
        for name in VARIABLES:
            assert f"{name}: ${{{{ vars.{name} }}}}" in block, (step_id, name)
