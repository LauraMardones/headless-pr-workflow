"""One implementation of the new-work admission rules (issue #557, Epic #519).

The rules live in ``dispatcher/admission.py`` (and ``scheduler.load_limits``). The scheduler, the new-work pre-flight
of ``scripts/dispatcher-invoke.sh`` and the shared limits check call them; each test below sends the same facts through
two entries and asserts the same decision, so a change that makes one entry decide differently fails here.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from headless_pr_workflow.dispatcher import cli
from headless_pr_workflow.dispatcher.admission import (
    check_start,
    classify_executable_type,
    executor_labels,
    known_type,
)
from headless_pr_workflow.dispatcher.scheduler import Limits, choose_next

ROOT = Path(__file__).resolve().parent.parent
BODY = "## Files affected\n- `a.sh`\n- `b.sh` (note)\n"


def hpw(*args: str, stdin: str | None = None, env: dict[str, str] | None = None):
    import os

    return subprocess.run(["python", "-m", "headless_pr_workflow.dispatcher", *args], input=stdin, text=True,
                          capture_output=True, env={**os.environ, **(env or {})}, cwd=ROOT)


# ── executable type ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("labels, native, executable, detail", [
    (["type:story"], "", True, "type:story"),
    (["type:bug", "executor:codex"], "", True, "type:bug"),
    (["type:epic"], "", False, "detected type: type:epic"),
    (["type:story", "type:task"], "", False, "unsupported type: conflicting type labels type:story type:task"),
    (["type:chore"], "", False, "unsupported type: type:chore"),
    ([], "Bug", True, "native issue type 'Bug'"),
    (["type:epic"], "Bug", False, "detected type: type:epic"),
    ([], "Epic", False, "unsupported type: native issue type 'Epic'"),
    ([], "", False, "type missing: no type: label or native issue type"),
])
def test_type_rule_gives_the_same_answer_through_the_rule_and_the_cli(labels, native, executable, detail):
    assert classify_executable_type(labels, native) == (executable, detail)
    proc = hpw("admission", "type", "--labels", " ".join(labels), "--native", native)
    out = json.loads(proc.stdout)
    assert (out["ok"], out["detail"]) == (executable, detail)
    assert proc.returncode == (0 if executable else 1)


def test_known_type_takes_the_first_known_type_label():
    assert known_type(["x", "type:chore", "type:feature", "type:story"]) == "feature"
    assert known_type(["type:chore"]) is None


# ── executor routing ────────────────────────────────────────────────────────


def test_executor_labels_keep_order_and_count():
    assert executor_labels(["type:story", "executor:codex", "executor:claude-code-opus"]) == ["codex", "claude-code-opus"]
    out = json.loads(hpw("admission", "executor", "--labels", "executor:a executor:b").stdout)
    assert (out["first"], out["count"]) == ("a", 2)
    none = hpw("admission", "executor", "--labels", "type:story")
    assert json.loads(none.stdout)["count"] == 0 and none.returncode == 1


# ── WIP limit and file overlap: scheduler and pre-flight entry ──────────────


def _issue(number, body, **extra):
    return {"number": number, "type": "story", "epic": 1, "feature": 2, "ready": True, "refined": True,
            "stale": False, "files_affected": None, "body": body, "prio": "prio:P2", **extra}


def _facts(target_body, active):
    issues = [_issue(10, target_body, files_affected=_paths(target_body)),
              {"number": 2, "type": "feature", "epic": 1, "refined": True, "stale": False, "prio": "prio:P2"}]
    issues += [_issue(n, b, files_affected=_paths(b), ready=False) for n, b in active]
    return {"epics": [{"number": 1, "spec": {"state": "merged", "committed_at": "2026-01-01T00:00:00Z"},
                       "record": {"state": "current"}, "prio": "prio:P2", "refinement": {}, "decision_blocker": {}}],
            "issues": issues}


def _paths(body):
    from headless_pr_workflow.dispatcher.facts import files_affected

    return files_affected(body)


def _scheduler(target_body, active, limit):
    facts = _facts(target_body, active)
    decision = choose_next(facts, in_implementation=[n for n, _ in active],
                           limits=Limits(limit, 0, 0))
    skipped = {s["issue"]: s["reason"] for s in decision.skipped}
    return skipped.get(10, "pass")


def _preflight(target_body, active, limit):
    items = [{"number": n, "title": f"S{n}", "body": b} for n, b in active]
    proc = hpw("admission", "start", stdin=json.dumps({"target_body": target_body, "active": items, "limit": limit}))
    return json.loads(proc.stdout)


@pytest.mark.parametrize("active, limit, result, scheduler_reason", [
    ([], 2, "pass", "pass"),
    ([(20, "## Files affected\n- `c.sh`\n")], 2, "pass", "pass"),
    ([(20, "## Files affected\n- `c.sh`\n"), (21, "## Files affected\n- `d.sh`\n")], 2, "wip-limit", "wip-limit"),
    ([(20, "## Files affected\n- `b.sh`\n")], 2, "file-overlap", "file-overlap #20 b.sh"),
    ([(20, "no section")], 2, "pass", "pass"),
])
def test_scheduler_and_preflight_decide_the_same_start(active, limit, result, scheduler_reason):
    assert _preflight(BODY, active, limit)["result"] == result
    assert _scheduler(BODY, active, limit) == scheduler_reason


def test_a_target_without_files_section_skips_the_overlap_check_in_both_entries():
    active = [(20, "## Files affected\n- `a.sh`\n")]
    assert check_start("no section", [{"number": n, "body": b} for n, b in active], 3)["result"] == "pass"
    assert _scheduler("no section", active, 3) == "pass"


def test_preflight_lists_every_overlap_with_owner_and_title():
    out = check_start(BODY, [{"number": 20, "title": "T", "body": "## Files affected\n- `a.sh`\n- `b.sh`\n"}], 3)
    assert out["overlaps"] == [{"path": "a.sh", "number": 20, "title": "T"}, {"path": "b.sh", "number": 20, "title": "T"}]


def test_unreadable_preflight_input_fails_closed():
    proc = hpw("admission", "start", stdin="not json")
    assert proc.returncode == 3 and json.loads(proc.stdout)["result"] == "unreadable"
    proc = hpw("admission", "start", stdin=json.dumps({"target_body": "", "active": [], "limit": 0}))
    assert proc.returncode == 3


# ── limits loading ──────────────────────────────────────────────────────────


def test_limits_cli_and_shell_shim_agree_on_valid_and_invalid_values():
    good = {"WIP_LIMIT_IMPLEMENTATION": "08", "READY_BUFFER_MIN": "1", "READY_BUFFER_MAX": "2"}
    out = json.loads(hpw("admission", "limits", env=good).stdout)
    assert out["limits"] == {"WIP_LIMIT_IMPLEMENTATION": 8, "READY_BUFFER_MIN": 1, "READY_BUFFER_MAX": 2}
    bad = hpw("admission", "limits", env={**good, "WIP_LIMIT_IMPLEMENTATION": "0"})
    assert bad.returncode == 1
    assert '[LIMITS ERROR] WIP_LIMIT_IMPLEMENTATION must be 1 or more (value: "0")' in bad.stderr


def test_cli_entry_points_are_registered():
    assert "admission" in cli._BLOCKS
