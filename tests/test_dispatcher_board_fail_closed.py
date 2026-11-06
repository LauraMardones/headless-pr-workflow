"""Board writes that used to only warn now fail closed (issue #432, AC 5).

Drives the real ``scripts/dispatcher-invoke.sh`` with the fakes in
``tests/dispatcher_fakes.py``; the board write goes through the real
verified-write block. Each case makes the fake board refuse one status and
checks that the step stops and leaves a state a later run retries.
"""

from __future__ import annotations

import pytest

from tests.dispatcher_fakes import Dispatcher, needs_tools

STORY = 500
PR = 900
HEAD = "a" * 40


@pytest.fixture
def d(tmp_path):
    return Dispatcher(tmp_path)


def board_error(status: str) -> str:
    return f'[BOARD ERROR] #{STORY}: could not set "{status}"'


@needs_tools
def test_refused_done_runs_no_cleanup_and_leaves_the_story_in_merge(d):
    d.add_story(STORY, "In merge")
    d.add_pr(PR, STORY, state="closed", merged=True)
    d.fail_status_write("Done")

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert board_error("Done") in proc.stderr
    assert "cleanup" not in " ".join(d.phases)
    assert d.status(STORY) == "In merge" and d.board_writes == []

    # The board works again: the merged-PR resume finishes the story.
    d.fail_status_write("Done", False)
    later = d.resume()
    assert later.returncode == 0, later.stdout + later.stderr
    assert d.phases == [f"cleanup {STORY}"] and d.status(STORY) == "Done"


# A board write tries 3 times: the landed write's read-back and the next two
# reads fail with 3 failed reads; the write-back of the old status fails too
# with 6.
@needs_tools
def test_unconfirmed_done_is_written_back_to_in_merge_and_cleanup_waits(d):
    d.add_story(STORY, "In merge")
    d.add_pr(PR, STORY, state="closed", merged=True)
    d.lose_status_read_back("Done", 3)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert board_error("Done") in proc.stderr
    assert d.board_writes == [f"{STORY}=Done", f"{STORY}=In merge"]
    assert "cleanup" not in " ".join(d.phases) and d.status(STORY) == "In merge"

    later = d.resume()
    assert later.returncode == 0, later.stdout + later.stderr
    assert d.phases == [f"cleanup {STORY}"] and d.status(STORY) == "Done"


@needs_tools
def test_unconfirmed_done_that_cannot_be_written_back_still_runs_cleanup(d):
    """Codex review of #452: a Done story is never resumed, so /cleanup must not wait for a later run."""
    d.add_story(STORY, "In merge")
    d.add_pr(PR, STORY, state="closed", merged=True)
    d.lose_status_read_back("Done", 6)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert board_error("Done") in proc.stderr and board_error("In merge") in proc.stderr
    assert '"In merge" could not be written back; /cleanup runs now' in proc.stderr
    assert d.phases == [f"cleanup {STORY}"] and d.status(STORY) == "Done"


@needs_tools
def test_refused_in_merge_after_a_failed_cleanup_returns_non_zero(d):
    d.add_story(STORY, "In merge")
    d.add_pr(PR, STORY, state="closed", merged=True)
    d.fail("cleanup")  # no closing summary and no pending copy on the story
    d.fail_status_write("In merge")

    proc = d.resume()

    assert proc.returncode != 0
    assert board_error("In merge") in proc.stderr
    assert d.board_writes == [f"{STORY}=Done"]


def stale_story(d: Dispatcher) -> None:
    """A story "In implementation" with no activity and no handoff note, next to a ready story."""
    d.add_story(700, "Ready for implementation")
    d.add_pr(970, 700)
    d.add_story(STORY, "In implementation")
    d.add_pr(PR, STORY)
    d.add_comment(PR, f"<!-- hpw:dispatcher action=merge-owner pr={PR} head={HEAD} -->")


@needs_tools
def test_refused_stale_rollback_posts_no_recovery_comment(d):
    stale_story(d)
    d.fail_status_write("Ready for implementation")

    proc = d.run_issue(700, env={"CODEX_FIX_ROUND_MAX": "1"})

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert board_error("Ready for implementation") in proc.stderr
    assert f'#{STORY}: the rollback to "Ready for implementation" was not written; no Recovery Comment' in proc.stderr
    assert d.posted(STORY) == [] and d.status(STORY) == "In implementation"
    assert d.sessions == ["implement 700"]  # the story is not counted as recovered or picked up

    # The board works again: the next run rolls it back and comments once.
    d.fail_status_write("Ready for implementation", False)
    d.run_issue(700, env={"CODEX_FIX_ROUND_MAX": "1"})
    assert [c for c in d.posted(STORY) if "## Recovery Comment" in c][:1]
    assert len([c for c in d.posted(STORY) if "## Recovery Comment" in c]) == 1


@needs_tools
def test_unconfirmed_stale_rollback_is_written_back_without_a_recovery_comment(d):
    stale_story(d)
    d.lose_status_read_back("Ready for implementation", 3)

    proc = d.run_issue(700, env={"CODEX_FIX_ROUND_MAX": "1"})

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"{STORY}=Ready for implementation" in d.board_writes
    assert f"{STORY}=In implementation" in d.board_writes
    assert d.posted(STORY) == [] and d.status(STORY) == "In implementation"


@needs_tools
def test_unconfirmed_stale_rollback_that_cannot_be_written_back_posts_the_recovery_comment(d):
    stale_story(d)
    d.lose_status_read_back("Ready for implementation", 6)

    proc = d.run_issue(700, env={"CODEX_FIX_ROUND_MAX": "1"})

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert board_error("Ready for implementation") in proc.stderr and board_error("In implementation") in proc.stderr
    # The rollback landed and nothing was written back; the story is ready again.
    assert [w for w in d.board_writes if w.startswith(f"{STORY}=")][0] == f"{STORY}=Ready for implementation"
    assert len([c for c in d.posted(STORY) if "## Recovery Comment" in c]) == 1


@needs_tools
def test_stale_rollback_writes_the_status_before_the_recovery_comment(d):
    stale_story(d)

    proc = d.run_issue(700, env={"CODEX_FIX_ROUND_MAX": "1"})

    assert proc.returncode == 0, proc.stdout + proc.stderr
    log = d._log("gh.log")
    write = next(i for i, line in enumerate(log) if f"itemId=ITEM_{STORY}" in line and "optionId=OPT_READY" in line)
    comment = next(i for i, line in enumerate(log) if f"repos/o/r/issues/{STORY}/comments -X POST" in line)
    assert write < comment
    assert "## Recovery Comment" in d.posted(STORY)[0]


@needs_tools
@pytest.mark.parametrize("seq", ["1", "2"])  # 1: reserve skip, 2: allowance error
def test_refused_ready_at_the_allowance_starts_no_session_and_is_handled(d, seq):
    d.add_story(STORY, "Ready for implementation")
    d.fail_status_write("Ready for implementation")

    proc = d.run_issue(STORY, seq=seq)

    assert proc.returncode == (0 if seq == "1" else 1)
    assert board_error("Ready for implementation") in proc.stderr
    assert d.sessions == [] and d.slack_calls == []  # DISPATCH_HANDLED: no dispatcher_error


@needs_tools
def test_refused_in_implementation_is_best_effort_and_logged(d):
    """ADR-014 -> Decision -> 7 lists this write as best-effort (an existing test needs the session)."""
    d.add_story(STORY, "Ready for implementation")
    d.add_pr(PR, STORY)
    d.fail_status_write("In implementation")

    proc = d.run_issue(STORY)

    assert board_error("In implementation") in proc.stderr
    assert d.sessions == [f"implement {STORY}"]
