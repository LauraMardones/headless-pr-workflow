"""The ``debounce`` step of ``scripts/dispatcher-workflow-step.sh`` (issue #653).

The step runs against a fake ``gh`` that returns a runs list; ``DEBOUNCE_SECONDS=0``.
"""

from __future__ import annotations

import json

import pytest

from tests.dispatcher_fakes import needs_tools
from tests.test_dispatcher_workflow_step import Sandbox

pytestmark = needs_tools

FAKE_GH = """#!/usr/bin/env bash
echo "gh $*" >> "$STEP_LOG"
case "$*" in
  *"/actions/workflows/"*) [ -n "${RUNS_FAIL:-}" ] && exit 1; cat "$RUNS_FILE" ;;
  *"/actions/runs/"*) [ -n "${OWN_FAIL:-}" ] && exit 1; echo "2026-11-24T10:00:00Z" ;;
esac
"""


def run_of(id_, event="issues", base=None, conclusion=None):
    return {"id": id_, "event": event, "conclusion": conclusion,
            "pull_requests": [{"base": {"ref": base}}] if base else []}


def debounce(tmp_path, me, runs, **env):
    sb = Sandbox(tmp_path)
    (sb.bin / "gh").write_text(FAKE_GH, newline="\n")
    runs_file = tmp_path / "runs.json"
    runs_file.write_text(json.dumps(runs if isinstance(runs, str) else {"workflow_runs": runs}))
    proc = sb.run("debounce", RUN_ID=str(me), DEBOUNCE_SECONDS="0", DEFAULT_BRANCH="main",
                  RUNS_FILE=runs_file.as_posix(), **env)
    assert proc.returncode == 0, proc.stderr
    return sb.outputs()["latest"], sb


@pytest.mark.parametrize("event", ["issues", "issue_comment", "workflow_run"])
def test_a_newer_default_branch_event_run_skips_this_writer(tmp_path, event):
    assert debounce(tmp_path, 5, [run_of(5), run_of(6, event)])[0] == "false"


@pytest.mark.parametrize("event", ["pull_request_target", "pull_request_review"])
def test_a_newer_pr_run_on_the_default_base_skips_this_writer(tmp_path, event):
    assert debounce(tmp_path, 5, [run_of(6, event, "main")])[0] == "false"


def test_no_newer_run_writes(tmp_path):
    assert debounce(tmp_path, 5, [run_of(4), run_of(5)])[0] == "true"


@pytest.mark.parametrize("newer", [run_of(6, "pull_request_target", "release"), run_of(6, "pull_request_target"),
                                   run_of(6, "schedule"), run_of(6, "issues", conclusion="cancelled")])
def test_a_newer_run_that_cannot_write_does_not_count(tmp_path, newer):
    assert debounce(tmp_path, 5, [newer])[0] == "true"


def test_an_older_id_never_counts(tmp_path):
    assert debounce(tmp_path, 5, [run_of(3), run_of(4, "issue_comment")])[0] == "true"


@pytest.mark.parametrize("env,runs", [({"RUNS_FAIL": "1"}, []), ({}, "not json"), ({"OWN_FAIL": "1"}, [run_of(6)])])
def test_unreadable_answers_fail_open(tmp_path, env, runs):
    assert debounce(tmp_path, 5, runs, **env)[0] == "true"


def test_a_burst_of_14_runs_leaves_exactly_the_newest_writing(tmp_path):
    burst = [run_of(i, "issues" if i % 2 else "issue_comment") for i in range(1, 15)]
    latest = [debounce(tmp_path / f"r{i}", i, burst)[0] for i in range(1, 15) if (tmp_path / f"r{i}").mkdir()
              or True]
    assert latest.count("true") == 1 and latest[-1] == "true"


def test_the_request_names_the_run_and_the_workflow_file(tmp_path):
    _, sb = debounce(tmp_path, 5, [])
    calls = " ".join(sb.calls())
    assert "repos/o/r/actions/runs/5" in calls and "actions/workflows/board-status.yml/runs?created=%3E%3D2026-11-24T10:00:00Z" in calls


def test_a_missing_run_id_is_invalid_usage(tmp_path):
    sb = Sandbox(tmp_path)
    assert sb.run("debounce").returncode == 2
