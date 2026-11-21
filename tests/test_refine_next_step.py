"""Refine Session Summaries name the PO's next step (issue #612, Epic #566).

The refine rule lives in ``.claude/commands/refine*.md`` and the tests read
those texts. The steps themselves come from the shared source, rendered
through the ``hpw next-step render`` CLI and read back by the shared parser.
"""

import re
from pathlib import Path

import pytest

from headless_pr_workflow.cli import main
from headless_pr_workflow.next_step import NextStepError, parse


ROOT = Path(__file__).resolve().parents[1]
COMMANDS = ("refine", "refine-epic", "refine-feature", "refine-story", "refine-task", "refine-bug")
DECISION_PREFIX = "Needs PO decision — "


def read(name: str) -> str:
    return (ROOT / ".claude" / "commands" / f"{name}.md").read_text(encoding="utf-8")


def render_cli(capsys, command: str, number: int, where: str, then: str) -> str:
    code = main(["next-step", "render", "--command", command, "--number", str(number),
                 "--where", where, "--then", then])
    assert code == 0
    return capsys.readouterr().out.strip()


def refine_next_action(rendered: str, answered: bool) -> str:
    """The `Next action:` line the refine rule produces from a rendered step."""
    value = rendered.removeprefix("Next step: ")
    return f"Next action: {value if answered else DECISION_PREFIX + value}"


@pytest.mark.parametrize("name", COMMANDS)
def test_open_decision_blocker_names_the_reply_and_the_next_run(name: str) -> None:
    text = read(name)

    assert "Next action: Needs PO decision — <next step>" in text
    assert "hpw next-step render --command /unblock" in text
    assert "Decision <letter>" in text and "a line `/unblock`" in text
    assert "a reply on GitHub" in text
    assert "the dispatcher's next run starts `/refine <n>`" in text


@pytest.mark.parametrize("name", COMMANDS)
def test_scope_answer_without_spec_pr_names_spec_command_place_and_follow_up(name: str) -> None:
    text = read(name)

    assert "--command /spec --number <epic>" in text
    assert '--where "Claude Code or Codex"' in text
    assert "the PO merges the spec PR and the next `/refine <n>` resumes" in text


@pytest.mark.parametrize("name", COMMANDS)
def test_scope_answer_with_spec_pr_open_names_review_and_merge_on_github(name: str) -> None:
    text = read(name)

    assert "the PO's review and merge of spec PR #<pr> on GitHub" in text
    assert "--number <pr>" in text
    assert "the next `/refine <n>` resumes" in text


@pytest.mark.parametrize("name", COMMANDS)
def test_after_an_answer_the_next_action_never_reads_needs_po_decision(name: str) -> None:
    text = read(name)

    assert re.search(r"with no \"Needs PO decision\", after the PO's answer", text)


def test_refine_rule_forbids_needs_po_decision_after_an_answer() -> None:
    assert 'After an answer, `Next action:` never reads "Needs PO decision"' in read("refine")


@pytest.mark.parametrize("name", COMMANDS)
def test_flow_owned_outcomes_and_checks_line_are_unchanged(name: str) -> None:
    text = read(name)

    assert "`Next action: Ready for implementation`" in text
    assert "`Next action: Refined; blocked by #X`" in text
    assert "`Checks run:` line carries the result: `refine-check — pass`" in text or name == "refine"


def test_issue_545_replay_names_spec_418_where_and_what_follows(capsys) -> None:
    # `Decision a` + `/unblock` on #545: the answer changes scope, no spec PR is open.
    rendered = render_cli(
        capsys, "/spec", 418, "Claude Code or Codex",
        "the PO merges the spec PR and the next `/refine 545` resumes",
    )
    step = parse(rendered)
    line = refine_next_action(rendered, answered=True)

    assert step.command == "/spec" and step.number == 418
    assert step.where == "Claude Code or Codex"
    assert step.then == "the PO merges the spec PR and the next `/refine 545` resumes"
    assert "Needs PO decision" not in line
    assert line.startswith("Next action: run `/spec 418` in Claude Code or Codex")
    assert parse("Next step: " + line.removeprefix("Next action: ")) == step


def test_open_decision_step_is_read_back_by_the_shared_parser(capsys) -> None:
    rendered = render_cli(capsys, "/unblock", 545, "a reply on GitHub", "the dispatcher's next run starts `/refine 545`")
    line = refine_next_action(rendered, answered=False)
    step = parse("Next step: " + line.removeprefix(f"Next action: {DECISION_PREFIX}"))

    assert line.startswith("Next action: Needs PO decision — reply `/unblock` on #545 on GitHub")
    assert step.command == "/unblock" and step.number == 545 and step.where == "a reply on GitHub"


def test_spec_pr_open_step_is_read_back_and_names_the_pr(capsys) -> None:
    rendered = render_cli(capsys, "review and merge the spec PR", 640, "a reply on GitHub", "the next `/refine 545` resumes")
    line = refine_next_action(rendered, answered=True)

    assert "Needs PO decision" not in line
    assert parse(rendered).number == 640


def test_a_needs_po_decision_line_after_an_answer_is_not_a_next_step() -> None:
    with pytest.raises(NextStepError):
        parse("Next step: Needs PO decision")


def test_dispatcher_facts_reads_no_next_action_value() -> None:
    facts = (ROOT / "src" / "headless_pr_workflow" / "dispatcher" / "facts.py").read_text(encoding="utf-8")

    assert "Next action" not in facts
    assert "_CHECK_RESULT" in facts
