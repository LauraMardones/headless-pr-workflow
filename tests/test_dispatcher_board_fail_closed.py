"""The steps that wrote a board status reach their end state without one (issue #478, AC 3).

Until #478 these steps wrote a status through the verified-write block and
failed closed when the board refused it (issue #432, AC 5). Since #478 the
dispatcher writes no board status at all: the workflow's "Write the board
status" step (``hpw dispatcher status sync``) is the only writer. Each case
drives the real ``scripts/dispatcher-invoke.sh`` with the fakes in
``tests/dispatcher_fakes.py``, makes the fake board refuse the status the step
used to write, and checks the step's real outcome and that no status was
written.
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


def no_board_write(d: Dispatcher, proc) -> None:
    assert d.board_writes == []
    assert "[BOARD ERROR]" not in proc.stderr
    assert "updateProjectV2ItemFieldValue" not in "\n".join(d._log("gh.log"))


@needs_tools
def test_finish_step_runs_cleanup_from_the_merged_pr_without_writing_done(d):
    d.add_story(STORY, "Mergeable")
    d.add_pr(PR, STORY, state="closed", merged=True)
    d.fail_status_write("Done")  # a board that refuses "Done" changes nothing now

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.phases == [f"cleanup {STORY}"]
    assert d.status(STORY) == "Mergeable"
    no_board_write(d, proc)


@needs_tools
def test_failed_cleanup_without_evidence_waits_for_the_next_run_and_writes_no_status(d):
    d.add_story(STORY, "Mergeable")
    d.add_pr(PR, STORY, state="closed", merged=True)
    d.fail("cleanup")  # no closing summary and no pending copy on the story
    d.fail_status_write("Mergeable")

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"[RESUME] #{STORY}: the cleanup closing summary is not on GitHub; the next run reruns cleanup" in proc.stderr
    no_board_write(d, proc)

    # The story is still open with its merged PR: the finish step of the next run reruns cleanup.
    d.fail("cleanup", False)
    later = d.resume()
    assert later.returncode == 0, later.stdout + later.stderr
    assert d.phases == [f"cleanup {STORY}", f"cleanup {STORY}"]
    no_board_write(d, later)


@needs_tools
def test_a_quiet_story_in_implementation_is_not_rolled_back(d):
    """The stale rollback is gone: no status write and no Recovery Comment; the ready story starts."""
    d.add_story(700, "Ready to implement")
    d.add_pr(970, 700)
    d.add_story(STORY, "In implementation")  # old updatedAt, no hand-off note: the old stale case
    d.add_pr(PR, STORY)
    d.add_comment(PR, f"<!-- hpw:dispatcher action=merge-owner pr={PR} head={HEAD} -->")
    d.fail_status_write("Ready to implement")

    proc = d.run_issue(700, env={"CODEX_FIX_ROUND_MAX": "1"})

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "STALE:" not in proc.stdout
    assert d.posted(STORY) == [] and d.status(STORY) == "In implementation"
    assert d.sessions == ["implement 700"]
    no_board_write(d, proc)


@needs_tools
@pytest.mark.parametrize("seq", ["1", "2"])  # 1: reserve skip, 2: allowance error
def test_a_refused_start_at_the_allowance_writes_no_status_and_is_handled(d, seq):
    d.add_story(STORY, "Ready to implement")
    d.fail_status_write("Ready to implement")

    proc = d.run_issue(STORY, seq=seq)

    assert proc.returncode == (0 if seq == "1" else 1)
    assert "the scheduler may choose the story again on a later poll" in proc.stdout + proc.stderr
    assert d.sessions == [] and d.slack_calls == []  # DISPATCH_HANDLED: no dispatcher_error
    no_board_write(d, proc)


@needs_tools
def test_implement_starts_without_an_in_implementation_write(d):
    d.add_story(STORY, "Ready to implement")
    d.add_pr(PR, STORY)
    d.fail_status_write("In implementation")

    proc = d.run_issue(STORY)

    assert d.sessions == [f"implement {STORY}"]
    no_board_write(d, proc)
