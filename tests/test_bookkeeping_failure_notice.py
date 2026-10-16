"""The failure notification of the Codex review bookkeeping job (issue #330)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from datetime import datetime, timezone

import pytest

from headless_pr_workflow import bookkeeping_failure_notice as notice
from tests.script_tools import BASH

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
TODAY = "2026-10-14T03:00:00Z"
YESTERDAY = "2026-10-13T23:59:00Z"


def run(conclusion, created=TODAY, event="schedule"):
    return {"conclusion": conclusion, "createdAt": created, "event": event}


@pytest.mark.parametrize(
    ("history", "expected"),
    [
        ([], True),
        ([run("success")], True),
        ([run("failure")], False),
        ([run("failure", YESTERDAY)], True),
        ([run("cancelled"), run("success")], True),
        ([run("cancelled"), run("failure")], False),
        ([run("skipped"), run(""), run("failure", YESTERDAY)], True),
        ([run("failure", f"2026-10-14T{h:02d}:00:00Z") for h in range(11, 0, -1)], False),
        ([run("failure", TODAY), run("success", YESTERDAY)], False),
        ([run("failure", event="workflow_dispatch")], True),
        ([run("success", event="workflow_dispatch"), run("failure")], False),
        ([run("failure", event="workflow_dispatch"), run("failure", YESTERDAY)], True),
    ],
)
def test_repeat_rule(history, expected):
    assert notice.should_notify(history, NOW) is expected


def test_message_names_pr_step_and_error():
    text = notice.build_description([{"pr": 12, "step": "read PR", "error": "boom"}])
    assert "PR #12" in text and "`read PR`" in text and "boom" in text


def test_message_for_no_pr():
    assert "no PR read yet" in notice.build_description([{"pr": None, "step": "read arguments", "error": "bad"}])


def test_message_lists_three_and_counts_the_rest():
    errors = [{"pr": n, "step": "read PR", "error": "x"} for n in range(1, 6)]
    text = notice.build_description(errors)
    assert "PR #3" in text and "PR #4" not in text and "and 2 more" in text


def test_token_is_masked_and_text_is_cut():
    text = notice.build_description([{"pr": 1, "step": "s", "error": "bad ghp_ABCdef123456 " + "y" * 500}])
    assert "ghp_" not in text and "***" in text
    assert len(text) < 400


@pytest.fixture
def fake_slack(tmp_path, monkeypatch):
    log = tmp_path / "calls.log"
    script = tmp_path / "slack.sh"
    if os.name == "nt":  # a bare "bash" is the WSL launcher in System32 there; run the fake with Git Bash
        real_run = subprocess.run
        monkeypatch.setattr(notice.subprocess, "run",
                            lambda argv, **kw: real_run([BASH, *argv[1:]] if argv[0] == "bash" else argv, **kw))

    def make(exit_code=0):
        script.write_text(f'#!/usr/bin/env bash\necho "$1 $2" >> "{log.as_posix()}"\nexit {exit_code}\n',
                          encoding="utf-8", newline="\n")
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        return script

    make.log = log
    return make


def env_for(tmp_path, script, errors=None, dry_run=False):
    errors_file = tmp_path / "errors.json"
    if errors is not None:
        errors_file.write_text(json.dumps({"dry_run": dry_run, "errors": errors}))
    return {"ERRORS_FILE": str(errors_file), "SLACK_NOTIFY_SCRIPT": str(script), "RUN_URL": "https://x/runs/9",
            "REPOSITORY": "o/r", "DEFAULT_BRANCH": "main"}


def calls(fake_slack):
    return fake_slack.log.read_text().splitlines() if fake_slack.log.exists() else []


ERR = [{"pr": 5, "step": "read PR", "error": "boom"}]


def test_sends_one_dispatcher_error_with_run_link(tmp_path, fake_slack):
    env = env_for(tmp_path, fake_slack(), ERR)
    assert notice.notify(env, now=NOW, history_reader=lambda *a: [run("success")]) == 0
    [call] = calls(fake_slack)
    assert call.startswith("dispatcher_error ")
    context = json.loads(call.split(" ", 1)[1])
    assert context["last_action"] == "https://x/runs/9" and "PR #5" in context["error_description"]


def test_same_day_streak_sends_nothing(tmp_path, fake_slack):
    env = env_for(tmp_path, fake_slack(), ERR)
    notice.notify(env, now=NOW, history_reader=lambda *a: [run("failure")])
    assert calls(fake_slack) == []


def test_dry_run_sends_nothing(tmp_path, fake_slack):
    env = env_for(tmp_path, fake_slack(), ERR, dry_run=True)
    notice.notify(env, now=NOW, history_reader=lambda *a: [])
    assert calls(fake_slack) == []


def test_failure_before_the_command_reported_still_sends(tmp_path, fake_slack):
    env = env_for(tmp_path, fake_slack(), None)
    notice.notify(env, now=NOW, history_reader=lambda *a: [])
    assert "failed before it reported" in calls(fake_slack)[0]


def test_unreadable_history_sends_once_with_warning(tmp_path, fake_slack, capsys):
    def unreadable(*args):
        raise RuntimeError("no")

    env = env_for(tmp_path, fake_slack(), ERR)
    assert notice.notify(env, now=NOW, history_reader=unreadable) == 0
    assert len(calls(fake_slack)) == 1
    assert "::warning::" in capsys.readouterr().out


def test_failed_slack_call_warns_and_does_not_fail(tmp_path, fake_slack, capsys):
    env = env_for(tmp_path, fake_slack(exit_code=1), ERR)
    assert notice.notify(env, now=NOW, history_reader=lambda *a: []) == 0
    assert "The PO was not notified" in capsys.readouterr().out


def test_missing_slack_script_warns(tmp_path, capsys):
    env = env_for(tmp_path, tmp_path / "absent.sh", ERR)
    assert notice.notify(env, now=NOW, history_reader=lambda *a: []) == 0
    assert "The PO was not notified" in capsys.readouterr().out


def test_history_covers_runs_of_every_ref(monkeypatch):
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return type("R", (), {"returncode": 0, "stdout": "[]"})()

    monkeypatch.setattr(notice.subprocess, "run", fake_run)
    notice.fetch_history("o/r", {})
    assert "--branch" not in seen["cmd"]
    assert "conclusion,createdAt,event" in seen["cmd"]
