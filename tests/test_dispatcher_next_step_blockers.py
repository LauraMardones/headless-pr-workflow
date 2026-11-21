"""Dispatcher Blocked Declarations name the PO's next step (issue #606, Epic #566).

Drives the real ``scripts/dispatcher-invoke.sh`` with the fakes in
``tests/dispatcher_fakes.py``; the fake ``hpw`` renders ``hpw next-step render``
with the real module. Every body is read back with the shared parser of #605.
"""

from __future__ import annotations

import json

import pytest

from headless_pr_workflow.next_step import NextStepError, parse, parse_declaration
from tests.dispatcher_fakes import Dispatcher, needs_tools

pytestmark = needs_tools

STORY = 500
PR = 900
HEAD = "a" * 40
CONFLICTING = {"mergeable": False, "mergeable_state": "dirty"}
SESSION = "Claude Code or Codex"


@pytest.fixture
def d(tmp_path):
    return Dispatcher(tmp_path)


def blockers(d: Dispatcher) -> list[str]:
    return [b for b in d.posted(STORY) if b.startswith("## Blocked Declaration")]


def step_of(body: str):
    """The one plain next step of a declaration, read by the shared parser."""
    steps = parse_declaration(body)
    assert list(steps) == [None], body
    return steps[None]


def po_context(d: Dispatcher) -> dict:
    [event] = [e for e in d.queued_events if e["event"] == "po_blocker"]
    return json.loads(event["context"])


def test_wip_limit_conflict_blocker_names_implement_and_registers_no_event(d):
    d.add_story(501, "In implementation")
    d.add_pr(901, 501, draft=True)
    board = d._read("board.json")
    for item in board["items"]:
        item["updatedAt"] = "2999-01-01T00:00:00Z"
    d._write("board.json", board)
    d.add_story(STORY, "Ready to implement")

    d.run_issue(STORY, env={"WIP_LIMIT_IMPLEMENTATION": "1"})

    [body] = blockers(d)
    step = step_of(body)
    assert (step.command, step.number, step.where) == ("/implement", STORY, SESSION)
    assert "the story starts" in step.then
    assert "Type: conflict" in body and "Unblocked when: Fewer than 1 stories" in body
    assert d.queued_events == []  # a conflict blocker was never a po_blocker


def test_mid_cycle_blocker_names_the_failed_command_and_the_unblock_reply(d):
    d.add_story(STORY, "Ready to implement")
    d.fail("implement")

    d.run_issue(STORY)

    [body] = blockers(d)
    step = step_of(body)
    assert (step.command, step.number, step.where) == ("/implement", STORY, SESSION)
    assert f"/unblock on #{STORY}" in step.then and "resumes" in step.then
    assert f"Unblocked when: /implement completes successfully for #{STORY} with exit code 0" in body
    assert "Type: external" in body and "Declared by:" in body
    assert po_context(d)["next_step"] == step.text.removeprefix("Next step: ")


def test_mid_cycle_blocker_with_its_own_condition_passes_its_own_next_step(d):
    d.add_story(STORY, "Ready to implement")
    d.add_pr(PR, STORY, draft=True, **CONFLICTING)

    d.run_issue(STORY)

    [body] = blockers(d)
    step = step_of(body)
    assert "merge conflict with main" in body
    assert (step.command, step.number, step.where) == ("/implement", STORY, SESSION)
    assert "conflict with main" in step.then and "/unblock" in step.then


def test_fix_round_maximum_blocker_names_a_command_never_fixed_by_hand(d):
    d.add_story(STORY, "In implementation")
    d.add_pr(PR, STORY)
    for head in ("1" * 40, "2" * 40, HEAD):
        d.add_findings(PR, head)

    d.resume(env={"CODEX_FIX_ROUND_MAX": "2"})

    [body] = blockers(d)
    step = step_of(body)
    assert (step.command, step.number, step.where) == ("/implement", STORY, SESSION)
    assert "CODEX_FIX_ROUND_MAX" in step.then and "/unblock" in step.then
    assert "fixed by hand" not in step.text
    assert "Type: resource" in body and "Unblocked when: the findings on PR #900 are fixed by hand" in body
    assert po_context(d)["next_step"].startswith("run `/implement 500`")


def test_evidence_give_up_blocker_names_the_by_hand_evidence_and_the_unblock_reply(d):
    d.add_story(STORY, "Mergeable")
    d.add_pr(PR, STORY)
    d.fail("cleanup")

    for _ in range(4):
        d.resume()

    [body] = blockers(d)
    step = step_of(body)
    assert (step.command, step.number, step.where) == ("/unblock", STORY, "a reply on GitHub")
    assert "by hand" in step.then and "dispatcher continues" in step.then
    assert "Type: external" in body and "Unblocked when: the evidence" in body


def test_merge_conflict_blocker_names_the_resolution_and_the_unblock_reply(d):
    d.add_story(STORY, "Mergeable", labels=["type:story", "executor:codex"])
    d.add_pr(PR, STORY, head=HEAD, **CONFLICTING)

    d.resume()

    [body] = blockers(d)
    step = step_of(body)
    assert (step.command, step.number, step.where) == ("/unblock", STORY, "a reply on GitHub")
    assert f"resolves the conflict in {SESSION} on the branch of PR #{PR}" in step.then
    assert "Unblocked when: the PR branch is merged with main and the conflict resolved, or the PO posts /unblock" in body


def test_in_merge_owner_blocker_matches_merge_takeover_merge_then_cleanup(d):
    d.add_story(STORY, "Mergeable")
    d.add_pr(PR, STORY, head="b" * 40, owned=False)

    d.resume()

    [body] = blockers(d)
    step = step_of(body)
    assert (step.command, step.number, step.where) == ("/merge", PR, SESSION)
    assert f"/cleanup {PR}" in step.then and "Merge Takeover" in step.then
    assert f"/merge {PR} and then runs /cleanup {PR}" in body  # the unchanged Unblocked when
    assert po_context(d)["next_step"] == step.text.removeprefix("Next step: ")


def test_other_merge_owner_blocker_names_pr_takeover(d):
    d.add_story(STORY, "In review", executor="claude-code-opus")
    d.add_pr(PR, STORY, head="b" * 40)

    d.resume()

    [body] = blockers(d)
    step = step_of(body)
    assert (step.command, step.number, step.where) == ("hpw pr-takeover", PR, SESSION)
    assert "Merge Takeover" in step.then and "Type: resource" in body
    assert f"Unblocked when: the PO follows docs/TAKEOVER-RULES.md → Merge Takeover for PR #{PR}" in body


def test_a_declaration_without_a_part_fails_the_shared_parser():
    with pytest.raises(NextStepError):
        parse("Next step: run `/implement` in Claude Code or Codex; then the story starts.")
    with pytest.raises(NextStepError):
        parse("Next step: run `/implement 5` in Claude Code or Codex.")


def test_no_blocker_writes_the_codex_review_trigger_phrase(d):
    from headless_pr_workflow.review_policy import CODEX_REVIEW_TRIGGER_PHRASE

    d.add_story(STORY, "Ready to implement")
    d.fail("implement")
    d.run_issue(STORY)

    assert all(CODEX_REVIEW_TRIGGER_PHRASE.lower() not in b.lower() for b in d.posted(STORY))
