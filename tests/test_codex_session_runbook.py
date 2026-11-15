"""Codex sessions run the HPW /refine and /implement contracts from the worker invocation (issue #535).

No model session runs: the worker runs against the in-memory GitHub of ``tests/test_codex_worker.py``, the
scheduler rule against fixed facts, and the runbook is read as text.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from headless_pr_workflow.dispatcher import codex_worker, scheduler
from headless_pr_workflow.dispatcher.codex_worker import claim, finish
from headless_pr_workflow.dispatcher.gateway import GatewayError
from tests.test_codex_worker import CONFIG, H1, REPO, Gate, WorkerGitHub, gh, settings  # noqa: F401

DOC = (Path(__file__).resolve().parents[1] / "docs" / "DISPATCHER-CONFIG.md").read_text(encoding="utf-8")
LIMITS = scheduler.Limits(wip=2, buffer_min=2, buffer_max=4)


def facts(*stories: dict) -> dict:
    epic = {"number": 1, "prio": None, "spec": {"state": "merged", "committed_at": "2026-01-01T00:00:00Z"},
            "record": {"state": "current"}, "refinement": {"refine_check": "pass", "created_at": "2026-01-02T00:00:00Z"},
            "decision_blocker": {"state": "none"}, "stale": False}  # fmt: skip
    feature = {"number": 2, "type": "feature", "epic": 1, "feature": None, "refined": True, "stale": False,
               "reasons": [], "criterion_prefix": "F1", "refinement": {}, "decision_blocker": {"state": "none"},
               "prio": None, "executor": None}  # fmt: skip
    rows = [{"type": "story", "epic": 1, "feature": 2, "refined": False, "ready": False, "stale": False,
             "reasons": [], "refinement": {"refine_check": None, "created_at": None},
             "decision_blocker": {"state": "none"}, "prio": None, "executor": None, **s} for s in stories]  # fmt: skip
    return {"result": "read", "epics": [epic], "issues": [feature, *rows]}


def due(*stories: dict, executor: str = "claude"):
    return scheduler.choose_next(facts(*stories), in_implementation=[], limits=LIMITS, executor=executor)


# ── AC 4: the scheduler ───────────────────────────────────────────────────────


def test_actions_scheduler_skips_refinement_of_a_codex_story_but_not_a_claude_one():
    decision = due({"number": 10, "executor": "executor:codex"}, {"number": 11, "executor": "executor:claude-code-opus"})
    assert decision.action and decision.action["issue"] == 11
    assert {"issue": 10, "reason": "codex-assigned"} in decision.skipped


def test_actions_scheduler_is_idle_when_only_a_codex_story_is_due():
    decision = due({"number": 10, "executor": "executor:codex"})
    assert decision.result == "idle"


def test_codex_refine_due_lists_only_codex_stories_in_order():
    rows = facts({"number": 12, "executor": "executor:codex"}, {"number": 11, "executor": "executor:claude-code-opus"},
                 {"number": 10, "executor": "executor:codex"})  # fmt: skip
    assert scheduler.codex_refine_due(rows, in_implementation=[], limits=LIMITS) == [10, 12]


def test_codex_refine_due_leaves_features_and_epics_to_claude():
    rows = facts({"number": 10, "executor": "executor:codex"})
    rows["issues"][0].update(refined=False, executor="executor:codex")  # the Feature is due, and is not a story
    assert scheduler.codex_refine_due(rows, in_implementation=[], limits=LIMITS) == []


# ── AC 1, 4: the worker queues refine ─────────────────────────────────────────


def test_claim_queues_a_due_codex_story_with_command_refine(gh, settings):  # noqa: F811
    gh.story(10, "Backlog", "type:story", "executor:codex")
    gh.story(11, "Backlog", "type:story", "executor:codex")
    result = claim(gh, REPO, owner="po", run="r1", eligibility=Gate(), now=lambda: gh.at, config=CONFIG,
                   settings=settings, env={}, refine_due=lambda: [10])  # fmt: skip
    call = result.fields["invocation"]
    assert result.result == "claimed" and call["issue"] == 10 and call["command"] == "refine"
    assert call["command_file"] == ".claude/commands/refine.md" and call["branch_prefix"].startswith("codex/issue-10")


def test_claim_without_a_refine_source_queues_no_refinement(gh, settings):  # noqa: F811
    gh.story(10, "Backlog", "type:story", "executor:codex")
    result = claim(gh, REPO, owner="po", run="r1", eligibility=Gate(), now=lambda: gh.at, config=CONFIG,
                   settings=settings, env={})  # fmt: skip
    assert result.result == "idle"


def test_claim_does_not_queue_a_due_story_of_another_executor_or_a_feature(gh, settings):  # noqa: F811
    gh.story(10, "Backlog", "type:story", "executor:claude-code-opus")
    gh.story(11, "Backlog", "type:feature", "executor:codex")
    result = claim(gh, REPO, owner="po", run="r1", eligibility=Gate(), now=lambda: gh.at, config=CONFIG,
                   settings=settings, env={}, refine_due=lambda: [10, 11])  # fmt: skip
    assert result.result == "idle"


def test_claim_is_unreadable_when_refinement_facts_cannot_be_read(gh, settings):  # noqa: F811
    def broken():
        raise GatewayError("read refinement facts", "HTTP 502")

    gh.story(5, "Ready to implement")
    result = claim(gh, REPO, owner="po", run="r1", eligibility=Gate(), now=lambda: gh.at, config=CONFIG,
                   settings=settings, env={}, refine_due=broken)  # fmt: skip
    assert result.result == "unreadable" and gh.writes() == []


def test_a_claimed_refinement_ends_done_or_waits_for_a_decision(gh, settings):  # noqa: F811
    gh.story(10, "Backlog", "type:story", "executor:codex")
    run = lambda: claim(gh, REPO, owner="po", run="r1", eligibility=Gate(), now=lambda: gh.at, config=CONFIG,  # noqa: E731
                        settings=settings, env={}, refine_due=lambda: [10])  # fmt: skip
    assert run().result == "claimed"
    ended = finish(gh, REPO, 10, 1, "r1", result="wait", reason="decision-blocker", head=None, until=None,
                   config=CONFIG, settings=settings)  # fmt: skip
    assert ended.result == "written"
    end = [m for m in gh.marks(10) if m.action == "end"][0]
    assert end.fields["result"] == "wait" and end.fields["reason"] == "decision-blocker"


def test_scheduler_refine_due_queues_nothing_when_the_limits_are_unusable(gh):  # noqa: F811
    assert codex_worker.scheduler_refine_due(gh, REPO, {})() == []


def test_the_api_key_refusal_still_applies_to_a_refine_claim(gh, settings):  # noqa: F811
    gh.story(10, "Backlog", "type:story", "executor:codex")
    result = claim(gh, REPO, owner="po", run="r1", eligibility=Gate(), now=lambda: gh.at, config=CONFIG,
                   settings=settings, env={"OPENAI_API_KEY": "x"}, refine_due=lambda: [10])  # fmt: skip
    assert result.result == "refused" and result.fields["reason"] == "api-key-set"


# ── AC 2, 3, 5, 6: the runbook text ───────────────────────────────────────────


@pytest.mark.parametrize("needle", [
    "### Session runbook", "`command: refine`", "`command: implement`", "branch_prefix", "invocation.resume",
    "open no second branch or PR", "gh-graphql-missing", "required-check-unreadable", "decision-blocker",
    "`refine-check`, `ac-summary` and the required `pytest` check are never skipped",
    "no Codex review trigger comment", "`finish --result done --head <full-sha>`", "No `OPENAI_API_KEY`",
    "codex-assigned", "`refine` |",
])  # fmt: skip
def test_the_docs_describe_the_runbook(needle):
    assert needle in DOC


# ── a finished refinement is not an implementation handoff ────────────────────


def test_a_finished_refinement_does_not_suppress_the_first_implementation_claim(gh, settings):  # noqa: F811
    gh.story(10, "Backlog", "type:story", "executor:codex")
    first = claim(gh, REPO, owner="po", run="r1", eligibility=Gate(), now=lambda: gh.at, config=CONFIG,
                  settings=settings, env={}, refine_due=lambda: [10])  # fmt: skip
    assert first.fields["invocation"]["command"] == "refine"
    branch = first.fields["invocation"]["branch_prefix"] + "refine"
    gh.branches[branch] = H1  # the refinement session published its branch at the main SHA
    finish(gh, REPO, 10, 1, "r1", result="done", reason="refined", head=H1, until=None, config=CONFIG,
           settings=settings)  # fmt: skip
    gh.cards.clear()
    gh.story(10, "Ready to implement", "type:story", "executor:codex")
    second = claim(gh, REPO, owner="po", run="r2", eligibility=Gate(), now=lambda: gh.at, config=CONFIG,
                   settings=settings, env={})  # fmt: skip
    assert second.result == "claimed" and second.fields["invocation"]["command"] == "implement"


def test_a_finished_refinement_does_not_suppress_a_later_re_refinement(gh, settings):  # noqa: F811
    gh.story(10, "Backlog", "type:story", "executor:codex")
    first = claim(gh, REPO, owner="po", run="r1", eligibility=Gate(), now=lambda: gh.at, config=CONFIG,
                  settings=settings, env={}, refine_due=lambda: [10])  # fmt: skip
    gh.branches[first.fields["invocation"]["branch_prefix"] + "refine"] = H1
    finish(gh, REPO, 10, 1, "r1", result="done", reason="refined", head=H1, until=None, config=CONFIG,
           settings=settings)  # fmt: skip
    second = claim(gh, REPO, owner="po", run="r2", eligibility=Gate(), now=lambda: gh.at, config=CONFIG,
                   settings=settings, env={}, refine_due=lambda: [10])  # fmt: skip
    assert second.result == "claimed" and second.fields["invocation"]["command"] == "refine"


def test_a_due_refinement_is_queued_for_a_story_already_in_implementation(gh, settings):  # noqa: F811
    gh.story(10, "In implementation", "type:story", "executor:codex")
    result = claim(gh, REPO, owner="po", run="r1", eligibility=Gate(), now=lambda: gh.at, config=CONFIG,
                   settings=settings, env={}, refine_due=lambda: [10])  # fmt: skip
    assert result.result == "claimed" and result.fields["invocation"]["command"] == "refine"


# ── work in flight reaches the refinement rule, as on the Actions path ────────

LIMIT_ENV = {"WIP_LIMIT_IMPLEMENTATION": "2", "READY_BUFFER_MIN": "2", "READY_BUFFER_MAX": "4",
             "DECISION_BLOCKER_PO_LOGIN": "po"}  # fmt: skip


def stale_story_and_full_buffer() -> dict:
    """Codex story #10 has a stale record; two ready stories fill the ready buffer."""

    return facts({"number": 10, "executor": "executor:codex", "stale": True},
                 {"number": 20, "refined": True, "ready": True}, {"number": 21, "refined": True, "ready": True})  # fmt: skip


@pytest.fixture
def read_facts_returns(monkeypatch):
    def use(rows: dict) -> None:
        class Read:
            result = "read"
            error = None

            def to_dict(self) -> dict:
                return rows

        monkeypatch.setattr("headless_pr_workflow.dispatcher.facts.read_facts", lambda *a, **k: Read())

    return use


def test_a_stale_story_in_review_is_due_for_refinement_whatever_the_ready_buffer(gh, read_facts_returns):  # noqa: F811
    read_facts_returns(stale_story_and_full_buffer())
    gh.story(10, "In review", "type:story", "executor:codex")
    assert codex_worker.scheduler_refine_due(gh, REPO, LIMIT_ENV)() == []  # no PR yet: the buffer is full
    gh.add_pr(30, "codex/issue-10-x", H1, body="Closes #10")  # a non-draft PR in review
    assert codex_worker.scheduler_refine_due(gh, REPO, LIMIT_ENV)() == [10]


def test_work_in_flight_lists_linked_prs_and_open_blockers_as_the_actions_path(gh):  # noqa: F811
    rows = facts({"number": 10}, {"number": 11}, {"number": 12}, {"number": 13})
    gh.add_pr(30, "codex/issue-10-x", H1, body="Closes #10")
    gh.add_pr(31, "claude/issue-11-x", H1, body="Fixes #11", state="closed")  # a closed PR is not in flight
    gh.add(12, "## Blocked Declaration\nType: external\nReason: waiting", login="po")
    gh.add(13, "## Blocked Declaration\nType: external\nReason: waiting", login="po")
    gh.at += timedelta(minutes=1)
    gh.add(13, "/unblock", login="po")  # a later PO /unblock lifts the blocker
    assert codex_worker.work_in_flight(gh, rows, LIMIT_ENV) == [10, 12]


def test_work_in_flight_fails_closed_when_the_open_prs_cannot_be_read(gh, monkeypatch):  # noqa: F811
    def broken():
        raise GatewayError("read open PRs", "HTTP 502")

    monkeypatch.setattr(gh, "list_open_prs", broken)
    with pytest.raises(GatewayError):
        codex_worker.work_in_flight(gh, facts({"number": 10}), LIMIT_ENV)
