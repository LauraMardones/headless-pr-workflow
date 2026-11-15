"""The Codex review and fix-round cycle, end to end with fakes (issue #575).

One story runs the whole cycle: the Codex worker (``codex_worker``, #536) hands off, the dispatcher
(``scripts/dispatcher-invoke.sh``, #574) reviews the PR head with Claude, the review finds blockers, the worker
claims a bounded fix round on the same PR and branch, the new head gets its own review, and a review without
blockers leaves the story to merge. The worker side uses the fakes of ``tests/test_codex_worker.py``, the
dispatcher side those of ``tests/dispatcher_fakes.py``; each side keeps its own board and PR facts, which the
test sets to what GitHub would show between the two. No model session runs. The cycle is described in
``docs/DISPATCHER-CONFIG.md`` and ``docs/HEADLESS-PR-WORKFLOW.md``; a production defect found here belongs to #536 or #574.
"""

from __future__ import annotations

import pytest

from headless_pr_workflow.dispatcher.codex_worker import finish, record_progress
from tests.dispatcher_fakes import Dispatcher, needs_tools
from tests.test_codex_worker import (
    BRANCH, CONFIG, H1, H2, H3, REPO, REVIEW_SUMMARY, Gate, WorkerGitHub, blockers, claims, do_claim, gh, handed_off,
    settings,
)

__all__ = ["gh", "settings"]  # the fixtures of tests/test_codex_worker.py

H4 = "4" * 40
STORY = 5
PR = 12


@pytest.fixture
def d(tmp_path):
    return Dispatcher(tmp_path)


def worker_hands_off(gh, settings, head, *, attempt, run, fix=False):
    """One worker attempt: claim, push ``head`` on PR #12, record it and end with ``done``."""
    if fix:
        gh.cards.clear()
        gh.story(STORY, "In implementation")
    else:
        gh.story(STORY, "Ready to implement")
    claimed = do_claim(gh, settings, run=run)
    assert claimed.result == "claimed", claimed
    assert claimed.fields["invocation"]["attempt"] == attempt
    gh.branches[BRANCH] = head
    gh.add_pr(PR, BRANCH, head, body=f"Closes #{STORY}")
    pushed = record_progress(gh, REPO, STORY, attempt, run, branch=BRANCH, head=head, pr=PR, settings=settings,
                             env={}, config=CONFIG, now=lambda: gh.at)
    done = finish(gh, REPO, STORY, attempt, run, result="done", reason="handoff", head=head, settings=settings,
                  env={}, config=CONFIG, now=lambda: gh.at)
    assert (pushed.result, done.result) == ("written", "written")
    return claimed


def dispatcher_marks(d, head, attempt):
    """What the worker's ``claim`` and ``done`` end leave on the story, as the dispatcher reads them."""
    d.add_comment(STORY, f"<!-- hpw:dispatcher action=codex-claim issue={STORY} attempt={attempt} owner=codex "
                         f"run=r{attempt} command=implement pr={PR} branch={BRANCH} head={head} -->")
    d.add_comment(STORY, f"<!-- hpw:dispatcher action=codex-end issue={STORY} attempt={attempt} run=r{attempt} "
                         f"result=done reason= head={head} until= by= -->")


@needs_tools
def test_ac1_the_cycle_from_worker_done_to_merge_with_one_review_per_head(gh, settings, d):
    # 1. The worker implements and hands off head H1; the board moves the story to In review.
    worker_hands_off(gh, settings, H1, attempt=1, run="r1")
    d.add_story(STORY, "In review", executor="codex")
    d.add_pr(PR, STORY, head=H1, draft=False, owned=True)
    dispatcher_marks(d, H1, 1)

    # 2. The dispatcher reviews H1 with Claude, once; the review finds blockers and returns the story.
    d.session_sets_status("review", STORY, "In implementation")
    first = d.resume()
    assert first.returncode == 0, first.stdout + first.stderr
    assert d.sessions == [f"review {PR}"] and d.status(STORY) == "In implementation"
    assert f"action=review-attempt pr={PR} head={H1}" in d.posted(STORY)[-1]

    # 3. The blocking review on H1 gives the worker a fix round on the same PR and branch.
    gh.cards.clear()
    gh.story(STORY, "In implementation")
    gh.review(PR, H1)
    gate = Gate()
    fix = do_claim(gh, settings, run="r2", gate=gate)
    assert fix.result == "claimed"
    invocation = fix.fields["invocation"]
    assert invocation["command"] == "implement" and invocation["attempt"] == 2
    assert invocation["resume"] == {"pr": PR, "branch": BRANCH, "head": H1}
    assert invocation["fix_round"] == {"round": 1, "max": 3, "pr": PR, "reviewed_head": H1}

    # 4. The fix round pushes H2 on the same branch and hands off; there is still one PR and one branch.
    gh.branches[BRANCH] = H2
    gh.prs[PR]["head"]["sha"] = H2
    record_progress(gh, REPO, STORY, 2, "r2", branch=BRANCH, head=H2, pr=PR, settings=settings, env={},
                    config=CONFIG, now=lambda: gh.at)
    finish(gh, REPO, STORY, 2, "r2", result="done", reason="handoff", head=H2, settings=settings, env={},
           config=CONFIG, now=lambda: gh.at)
    assert sorted(gh.prs) == [PR] and sorted(gh.branches) == [BRANCH]
    gh.cards.clear()
    gh.story(STORY, "In review")
    assert do_claim(gh, settings, run="r3").result == "idle"  # the new head waits for its own review

    # 5. The dispatcher reviews the new head H2 once; no blockers leaves the story to merge, and merge runs.
    d.set_status(STORY, "In review")
    d.add_pr(PR, STORY, head=H2, draft=False, owned=True)
    dispatcher_marks(d, H2, 2)
    d.session_sets_status("review", STORY, "Mergeable")
    second = d.resume()
    assert second.returncode == 0, second.stdout + second.stderr
    assert d.sessions == [f"review {PR}", f"review {PR}"]
    assert f"action=review-attempt pr={PR} head={H2}" in d.posted(STORY)[-1]
    assert d.phases == [f"merge {PR}", f"cleanup {STORY}"]
    assert d.board_writes == []

    # No Claude /implement ran for the Codex story at any point of the cycle.
    assert not any(s.startswith("implement") for s in d.sessions)
    assert all(call["stdin"].startswith(f"Review PR #{PR}") for call in d.claude_calls)


def test_ac2_above_the_maximum_no_session_starts_and_one_resource_blocker_is_posted(gh, settings):
    # Three fix rounds ran (reviews on H1..H3 each led to one); H4 is blocked a fourth time.
    handed_off(gh, H4, fix=H3)
    for head in (H1, H2, H3, H4):
        gh.review(PR, head)
    gate = Gate()
    result = do_claim(gh, settings, run="r2", gate=gate)
    assert result.result == "waiting" and gate.calls == []
    assert result.fields["skipped"] == [{"issue": STORY, "reason": "fix-round-max", "round": 4, "max": 3,
                                         "evidence": "written"}]
    [blocker] = blockers(gh)
    assert "Type: resource" in blocker["body"] and f"PR #{PR}" in blocker["body"]
    assert len(claims(gh)) == 1  # only the first attempt's claim: no session started
    again = do_claim(gh, settings, run="r3", gate=gate)
    assert again.fields["skipped"][0]["evidence"] == "already" and len(blockers(gh)) == 1 and gate.calls == []


def test_ac2_the_maximum_defaults_to_three_and_a_raised_maximum_allows_the_next_round(gh, settings):
    from dataclasses import replace

    from headless_pr_workflow.dispatcher.codex_worker import Config, claim

    assert Config.from_env({}).fix_round_max == 3
    handed_off(gh, H4, fix=H3)
    for head in (H1, H2, H3, H4):
        gh.review(PR, head)
    result = claim(gh, REPO, owner="po-laptop", run="r2", eligibility=Gate(), now=lambda: gh.at,
                   config=replace(CONFIG, fix_round_max=4), settings=settings, env={})
    assert result.result == "claimed" and result.fields["invocation"]["fix_round"]["round"] == 4


@needs_tools
@pytest.mark.parametrize("status", ["Ready to implement", "In implementation", "In review", "Mergeable", "Blocked"])
def test_ac2_no_claude_implement_for_an_executor_codex_story_in_any_status(d, status):
    d.add_story(STORY, status, executor="codex")
    if status != "Ready to implement":
        d.add_pr(PR, STORY, head=H1, draft=(status == "In implementation"), owned=True)
    dispatcher_marks(d, H1, 1)
    d.schedule(("implement", STORY))
    for proc in (d.resume(), d.run_issue(STORY)):
        assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not any(s.startswith("implement") for s in d.sessions)
    assert all(call["stdin"].startswith(f"Review PR #{PR}") for call in d.claude_calls)
