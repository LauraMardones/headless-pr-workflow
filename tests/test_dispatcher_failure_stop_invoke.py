"""The failure stop in the real ``scripts/dispatcher-invoke.sh`` (issue #503).

The block itself is covered by ``tests/test_dispatcher_failure_stop.py``. Here the
invoke script runs against the fakes of ``tests/dispatcher_fakes.py``: sessions
fail, the stop is recorded in comments, and later runs start nothing until the
PO posts ``/unblock``. The last test checks the ``failure_stop`` Slack payload.

The fake GitHub stamps comments with a fixed October 2026 clock, so
``FAILURE_STOP_NOW`` fixes the clock of the 24-hour window to match.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from headless_pr_workflow.dispatcher import failure_stop
from tests.dispatcher_fakes import BASH, PO, Dispatcher, needs_tools
from tests.script_tools import host_env

REPO_ROOT = Path(__file__).resolve().parent.parent
NOTIFY = REPO_ROOT / "scripts" / "slack-notify.sh"
NOW = "2026-10-13T12:00:00Z"

pytestmark = needs_tools


def env(threshold: str = "2", **extra: str) -> dict[str, str]:
    return {"FAILED_SESSIONS_STOP_THRESHOLD": threshold, "FAILURE_STOP_NOW": NOW, **extra}


@pytest.fixture
def d(tmp_path):
    """Stories 11, 12 and 13, each ready for /implement."""
    dispatcher = Dispatcher(tmp_path)
    for number in (11, 12, 13):
        dispatcher.add_story(number, "Ready to implement")
        dispatcher.add_pr(number + 100, number)
    return dispatcher


def stop_events(d):
    return [e for e in d.queued_events if e["event"] == "failure_stop"]


def fail_twice(d, threshold: str = "2"):
    """Two failed /implement sessions on stories 11 and 12 (the second reaches a threshold of 2)."""
    d.fail("implement")
    for number in (11, 12):
        proc = d.run_issue(number, env=env(threshold))
        assert proc.returncode == 1, proc.stdout + proc.stderr
    d.fail("implement", failing=False)


# ── AC 1 and 2: the record and one message ──────────────────────────────────


def test_ac1_a_failed_session_leaves_the_session_failed_marker_on_its_story(d):
    d.fail("implement")
    d.run_issue(11, env=env())

    [body] = [b for b in d.posted(11) if "action=session-failed" in b]
    assert "issue=11 step=implement" in body
    assert "action=failure-stop" not in body
    assert stop_events(d) == []


def test_ac2_the_failure_that_reaches_the_threshold_stops_and_sends_one_message(d):
    fail_twice(d)

    assert any("action=failure-stop" in body for body in d.posted(12))
    assert not any("action=failure-stop" in body for body in d.posted(11))
    [event] = stop_events(d)
    context = json.loads(event["context"]) if isinstance(event["context"], str) else event["context"]
    assert [s["issue"] for s in context["sessions"]] == [11, 12]
    assert context["threshold"] == 2 and context["window_hours"] == 24


def test_ac3_while_the_stop_holds_no_session_starts_and_nothing_is_spent(d):
    fail_twice(d)
    sessions, count, probes = list(d.sessions), d.usage_count, d.allowance_calls

    proc = d.run_issue(13, env=env())

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[FAILURE STOP] holds" in proc.stdout
    assert d.sessions == sessions and d.usage_count == count and d.allowance_calls == probes
    assert len(stop_events(d)) == 1  # a run that finds the stop holding sends no second message


def test_ac3_the_resume_pass_and_the_scheduler_start_nothing_either(d):
    fail_twice(d)
    sessions = list(d.sessions)

    proc = d.resume(env=env())

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[FAILURE STOP] holds" in proc.stdout
    assert d.sessions == sessions and d.scheduler_calls == []


# ── AC 4: a lost cache changes nothing ──────────────────────────────────────


def test_ac4_the_stop_holds_when_the_usage_and_state_directories_are_gone(d):
    fail_twice(d)
    for name in ("usage", "state"):
        shutil.rmtree(d.mock / name, ignore_errors=True)
    sessions = list(d.sessions)

    proc = d.run_issue(13, env=env())

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[FAILURE STOP] holds" in proc.stdout
    assert d.sessions == sessions


# ── AC 5: the PO's /unblock restarts ────────────────────────────────────────


def test_ac5_the_pos_unblock_on_the_stop_issue_starts_sessions_again(d):
    fail_twice(d)
    d.add_comment(12, "/unblock", login=PO)

    proc = d.run_issue(13, env=env())

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "implement 13" in d.sessions
    assert "[FAILURE STOP] holds" not in proc.stdout


@pytest.mark.parametrize("login,body", [
    ("someone-else", "/unblock"),
    (PO, "/unblock when the token is fixed"),
    (PO, "please /unblock"),
])
def test_ac5_an_unblock_by_another_author_or_not_a_standalone_line_lifts_nothing(d, login, body):
    fail_twice(d)
    d.add_comment(12, body, login=login)
    sessions = list(d.sessions)

    proc = d.run_issue(13, env=env())

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[FAILURE STOP] holds" in proc.stdout
    assert d.sessions == sessions


def test_ac5_failures_before_the_unblock_do_not_count_toward_the_next_stop(d):
    fail_twice(d)
    d.add_comment(12, "/unblock", login=PO)
    d.fail("implement")

    d.run_issue(13, env=env())  # one failure after the /unblock: 1 of 2

    assert "implement 13" in d.sessions
    assert not any("action=failure-stop" in body for body in d.posted(13))
    assert len(stop_events(d)) == 1


# ── AC 6: the setting is read on every run ──────────────────────────────────


@pytest.mark.parametrize("value", ["", "abc", "0", "-1", "2.5", "3 "])
def test_ac6_an_invalid_threshold_starts_nothing_and_fails_the_job(d, value):
    proc = d.run_issue(11, env=env(value))

    assert proc.returncode != 0
    assert "[FAILURE STOP ERROR]" in proc.stderr
    assert d.sessions == []
    assert d.slack_calls == [] and d.queued_events == []  # no dispatcher_error message


def test_ac6_a_changed_threshold_takes_effect_on_the_next_run(d):
    d.fail("implement")
    for number in (11, 12):
        d.run_issue(number, env=env("3"))  # 2 of 3: no stop
    assert stop_events(d) == []

    d.run_issue(13, env=env("3"))  # 3 of 3: the stop

    assert len(stop_events(d)) == 1
    assert d.sessions == ["implement 11", "implement 12", "implement 13"]


# ── AC 7: only the dispatcher's own markers count ───────────────────────────


def test_ac7_markers_pasted_by_another_author_neither_count_nor_stop(d):
    d.add_story(21, "Ready to implement")
    for number in (21, 22):
        pasted = (failure_stop.failed_marker(number, "implement", "https://github.com/o/r/actions/runs/1")
                  + "\n" + failure_stop.stop_marker(number, 2))
        d.add_comment(21, pasted, login="someone-else")

    proc = d.run_issue(11, env=env())

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "implement 11" in d.sessions
    assert "[FAILURE STOP] holds" not in proc.stdout


# ── dry run ─────────────────────────────────────────────────────────────────


def test_dry_run_records_counts_and_sends_nothing(d):
    d.fail("implement")

    proc = d.run_issue(11, "--dry-run", env=env())

    assert "[DRY RUN] Would check failure stop before /implement" in proc.stdout
    assert d.sessions == [] and d.posted(11) == [] and d.queued_events == [] and d.slack_calls == []


# ── the Slack payload ───────────────────────────────────────────────────────


def test_failure_stop_event_payload(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$MOCK_OUT"\nexit "${MOCK_RC:-0}"\n')
    curl.chmod(0o755)
    out = tmp_path / "curl.out"
    base = {**host_env(bin_dir.as_posix()), "MOCK_OUT": str(out), "SLACK_WEBHOOK_URL": "https://hooks.slack.test/x"}
    ctx = json.dumps({
        "threshold": 3, "window_hours": 24, "settings_url": "https://example.test/vars",
        "issue_url": "https://example.test/issues/12",
        "sessions": [
            {"issue": 11, "title": "Story 11", "issue_url": "https://example.test/issues/11", "step": "implement",
             "run_url": "https://example.test/runs/1"},
            {"issue": 12, "title": "Story 12", "issue_url": "https://example.test/issues/12", "step": "review",
             "run_url": "https://example.test/runs/2"},
        ],
        "restart": "Fix the cause first. Next step: reply `/unblock` on #12 on GitHub; then the dispatcher starts "
                   "sessions again on its next run. https://example.test/issues/12",
    })

    proc = subprocess.run([BASH, NOTIFY.as_posix(), "failure_stop", ctx], env=base, capture_output=True, text=True)

    assert proc.returncode == 0, proc.stderr
    payload = out.read_text()
    for expected in ("3 sessions failed within 24 hours", "Story 11", "https://example.test/runs/1", "/review",
                     "Story 12", "https://example.test/runs/2", "/unblock", "https://example.test/vars"):
        assert expected in payload, expected
    assert "Unrecognised" not in proc.stderr
    failing = subprocess.run([BASH, NOTIFY.as_posix(), "failure_stop", ctx], env={**base, "MOCK_RC": "22"},
                             capture_output=True, text=True)
    assert failing.returncode != 0  # a failed delivery fails the script, so the #295 queue tries again
