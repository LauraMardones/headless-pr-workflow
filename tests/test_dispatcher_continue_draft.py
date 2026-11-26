"""A draft PR left by a failed or silent /implement session is continued; a failed session's head gets an owner (issue #578).

Drives the real ``scripts/dispatcher-invoke.sh`` with the fakes in ``tests/dispatcher_fakes.py``.
The pure rule is tested in ``tests/test_dispatcher_in_flight.py``.
"""

from __future__ import annotations

import pytest

from tests.dispatcher_fakes import PO, Dispatcher, needs_tools

pytestmark = needs_tools

STORY = 500
PR = 900
HEAD = "a" * 40
NEW_HEAD = "b" * 40
EXTERNAL = "## Blocked Declaration\nType: external\nDeclared by: dispatcher\nBlocks: #500"
ATTEMPT = f"<!-- hpw:dispatcher action=continue-attempt pr={PR} head={HEAD} -->"


def owner_marker(head: str) -> str:
    return f"<!-- hpw:dispatcher action=merge-owner pr={PR} head={head} -->"


def push(head: str) -> str:
    """A session script that moves the PR head, as a pushed commit does."""

    return (f"jq 'map(if .number == {PR} then .head.sha = \"{head}\" else . end)' \"$MOCK_DIR/pulls.json\" "
            f"> \"$MOCK_DIR/p.tmp\" && mv \"$MOCK_DIR/p.tmp\" \"$MOCK_DIR/pulls.json\"\n")


def ready() -> str:
    return (f"jq 'map(if .number == {PR} then .draft = false else . end)' \"$MOCK_DIR/pulls.json\" "
            f"> \"$MOCK_DIR/p.tmp\" && mv \"$MOCK_DIR/p.tmp\" \"$MOCK_DIR/pulls.json\"\n")


@pytest.fixture
def d(tmp_path):
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "In implementation")
    d.add_pr(PR, STORY, draft=True)
    d.set_pytest(HEAD, "success", completed_at="2026-10-13T10:05:00Z")  # far older than the wait
    # A continued session ends as /implement does: the PR is Ready for review.
    d.on_session("implement", ready())
    return d


def marked(d, marker: str) -> bool:
    return any(marker in c for c in d.posted(PR) + d.posted(STORY))


def test_ac1_ac4_a_failed_session_is_continued_after_the_po_unblock_once_per_head(d):
    d.add_comment(STORY, EXTERNAL)

    proc = d.resume()  # before the /unblock: nothing starts
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [] or all("implement" not in s for s in d.sessions)

    d.add_comment(STORY, "/unblock", login=PO)
    proc = d.resume()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert [s for s in d.sessions if s.startswith("implement")] == [f"implement {STORY}"] or len(d.sessions) == 1
    assert f"[RESUME] #{STORY}: continue /implement on PR #{PR} (failed session after /unblock)" in proc.stdout
    assert any(ATTEMPT in c for c in d.posted(STORY))
    assert len(d.pull_requests) == 1  # no new PR

    proc = d.resume()  # the same head is not continued again (AC 4)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(d.sessions) == 1 and "continue /implement" not in proc.stdout


def test_ac2_a_silent_stop_is_continued_with_no_po_action(d):
    proc = d.resume()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(d.sessions) == 1
    assert f"[RESUME] #{STORY}: continue /implement on PR #{PR} (silent stop after 60 minutes)" in proc.stdout


@pytest.mark.parametrize("check", ["failure", "pending", "absent"])
def test_ac2_a_draft_pr_without_a_green_check_is_not_continued(d, check):
    d.set_pytest(HEAD, check)
    proc = d.resume()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [] and "[RESUME] " not in proc.stdout


def test_ac3_a_parked_draft_pr_holds_no_wip_place(d):
    """The #606 case: a draft PR with no session at work no longer makes the scheduler skip an overlapping story."""

    d.set_pytest(HEAD, "pending")
    proc = d.resume()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == []


def test_ac5_a_failed_continued_session_fails_the_run_and_posts_the_blocker(d):
    d.fail("implement")
    proc = d.resume()
    assert proc.returncode != 0
    assert any(c.startswith("## Blocked Declaration") and "Type: external" in c for c in d.posted(STORY))
    assert any(ATTEMPT in c for c in d.posted(STORY))  # the attempt is recorded first, whatever the result


def test_ac6_an_invalid_wait_stops_the_run_with_no_blocker(d):
    proc = d.resume(env={"CONTINUE_DRAFT_AFTER_MINUTES": "soon"})
    assert proc.returncode == 1
    assert "CONTINUE_DRAFT_AFTER_MINUTES must be a whole number of one or more" in proc.stderr
    assert d.sessions == [] and not [c for c in d.posted(STORY) if c.startswith("## Blocked Declaration")]


def test_ac10_a_failed_continued_session_that_pushed_a_head_leaves_the_owner_marker_after_the_blocker(d):
    d.fail("implement")
    d.on_failed_session("implement", push(NEW_HEAD))
    proc = d.resume()
    assert proc.returncode != 0
    story = d.posted(STORY)
    assert any(c.startswith("## Blocked Declaration") for c in story)
    assert any(owner_marker(NEW_HEAD) in c for c in d.posted(PR))
    assert not any(owner_marker(HEAD) in c for c in d.posted(PR))


def test_ac10_a_failed_session_that_pushed_nothing_leaves_no_owner_marker(d):
    d.fail("implement")
    proc = d.resume()
    assert proc.returncode != 0
    assert not any("action=merge-owner " in c for c in d.posted(PR))


def test_ac10_a_failed_fix_round_that_pushed_a_head_leaves_the_owner_marker(tmp_path):
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "In implementation")
    d.add_pr(PR, STORY, draft=True)
    d.add_findings(PR, HEAD)
    d.fail("implement")
    d.on_failed_session("implement", push(NEW_HEAD))
    proc = d.resume()
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert any(owner_marker(NEW_HEAD) in c for c in d.posted(PR))


def test_ac10_a_failed_conflict_round_that_pushed_a_head_leaves_the_owner_marker(tmp_path):
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "In review")
    d.add_pr(PR, STORY, draft=False, owned=True, mergeable=False, mergeable_state="dirty")
    d.set_gates(PR, "conflicting")
    d.fail("implement")
    d.on_failed_session("implement", push(NEW_HEAD))
    proc = d.resume()
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert any(owner_marker(NEW_HEAD) in c for c in d.posted(PR))


def test_ac11_a_head_with_no_dispatcher_session_behind_it_still_gets_the_merge_owner_missing_blocker(tmp_path):
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "Mergeable")
    d.add_pr(PR, STORY, head=NEW_HEAD, owned=False)
    proc = d.resume()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"PR #{PR} head {NEW_HEAD[:7]} has no merge-owner evidence; blocker posted" in proc.stdout
    assert d.phases == []
