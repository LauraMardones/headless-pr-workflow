"""Dispatcher call site of the subscription allowance check (issue #317).

Drives the real ``scripts/dispatcher-invoke.sh`` end to end with the fakes in
``tests/dispatcher_fakes.py``: a fake ``gh`` (board + REST), a fake ``curl``
(executor API and Slack webhook) and a stub allowance script on
``ALLOWANCE_SCRIPT`` whose exit codes follow a sequence, one per session start.

A Claude-implemented story runs one session (/implement), then is handed to the
Codex review loop (issue #293); tests/test_dispatcher_review_loop.py covers
the allowance check at its resume entries. A Codex-implemented story "In review"
runs one /review session in the resume pass (issue #315), then the script-only
/merge and /cleanup (issue #264), which check no allowance.
"""

from __future__ import annotations

import pytest

from tests.dispatcher_fakes import Dispatcher, needs_tools

pytestmark = needs_tools

ISSUE = 500
PR = 900


@pytest.fixture
def d(tmp_path):
    """A Claude-implemented story ready for /implement."""
    dispatcher = Dispatcher(tmp_path)
    dispatcher.add_story(ISSUE, "Ready for implementation")
    dispatcher.add_pr(PR, ISSUE)
    return dispatcher


@pytest.fixture
def r(tmp_path):
    """A Codex-implemented story \"In review\": the resume pass runs /review, which moves it to \"In merge\"."""
    dispatcher = Dispatcher(tmp_path)
    dispatcher.add_story(ISSUE, "In review", executor="codex")
    dispatcher.add_pr(PR, ISSUE)
    dispatcher.session_sets_status("review", ISSUE, "In merge")
    return dispatcher


def test_review_start_runs_the_session_then_the_scripts(r):
    proc = r.resume(seq="0,0")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert r.allowance_calls == 1  # /review; /merge and /cleanup start no session (#264)
    assert r.sessions == [f"review {PR}"]
    assert r.phases == [f"merge {PR}", f"cleanup {ISSUE}"]
    # the only comment is the per-head record of the completed /review
    assert [c for c in r.posted(ISSUE) if "action=review-attempt" not in c] == []
    assert r.status(ISSUE) == "Done"


def test_claude_story_checks_allowance_once_then_hands_off(tmp_path):
    d = Dispatcher(tmp_path)
    d.add_story(ISSUE, "Ready for implementation", executor="claude-code-sonnet")
    d.add_pr(PR, ISSUE)
    proc = d.run_issue(ISSUE, seq="0,1")  # a second check would refuse and comment
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.allowance_calls == 1
    assert d.sessions == [f"implement {ISSUE}"]
    assert d.posted(ISSUE) == []


def test_refuse_before_implement_skips_quietly(d):
    proc = d.run_issue(ISSUE, seq="1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.allowance_calls == 1
    assert d.sessions == []
    assert d.posted(ISSUE) == []
    assert "[RESERVE SKIP]" in proc.stdout
    assert d.status(ISSUE) == "Ready for implementation"
    assert d.slack_calls == []


def test_refuse_before_review_comments_once(r):
    proc = r.resume(seq="1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert r.allowance_calls == 1
    assert r.sessions == []  # /review not started
    comments = r.posted(ISSUE)
    assert len(comments) == 1
    body = comments[0]
    assert "/review was not started" in body
    assert "weekly window: 15% left, reserve 20%, resets 2026-10-14T08:00:00Z" in body
    assert "five-hour window" not in body  # only the window below the reserve
    assert r.slack_calls == []
    assert r.phases == []


def test_merge_and_cleanup_never_consult_the_allowance(r):
    proc = r.resume(seq="0,1,1")  # a refusal after /review would block a session
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert r.allowance_calls == 1
    assert r.phases == [f"merge {PR}", f"cleanup {ISSUE}"]
    # the only comment is the per-head record of the completed /review
    assert [c for c in r.posted(ISSUE) if "action=review-attempt" not in c] == []
    assert r.status(ISSUE) == "Done"


def test_error_before_implement_fails_without_slack(d):
    proc = d.run_issue(ISSUE, seq="2")
    assert proc.returncode != 0
    assert d.sessions == []
    assert "[ALLOWANCE ERROR]" in proc.stderr
    assert d.slack_calls == []  # no dispatcher_error notification
    assert d.posted(ISSUE) == []
    assert d.status(ISSUE) == "Ready for implementation"


def test_error_before_review_comments_with_reason(r):
    proc = r.resume(seq="2")
    assert proc.returncode != 0
    assert r.sessions == []
    assert r.slack_calls == []
    comments = r.posted(ISSUE)
    assert len(comments) == 1
    body = comments[0]
    assert "/review was not started" in body
    assert "could not be read" in body
    assert "no rate_limit_event in the probe output" in body


def test_dry_run_logs_and_does_not_run_check(d):
    proc = d.run_issue(ISSUE, "--dry-run", seq="2")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[DRY RUN] Would check subscription allowance before /implement" in proc.stdout
    assert d.allowance_calls == 0
