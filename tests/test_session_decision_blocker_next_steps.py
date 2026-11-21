"""Session decision blockers name the next step for each option (issue #608, Epic #566)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from headless_pr_workflow.next_step import parse_declaration, render


ROOT = Path(__file__).resolve().parents[1]
COMMANDS = ROOT / ".claude" / "commands"

STATEMENTS = (
    "one `Next step <letter>:` line per option",
    "in the order of `Options`",
    "a declaration of any other type carries `Next step:`",
    "`hpw next-step render --command <command> --number <n> --where <where> --then <outcome>`",
    "never by hand",
    "the command with its number, where to run it and what happens afterwards",
    "names `/unblock` (a reply on GitHub) as its command",
)
WAIVER_A = "Next step A: a reply on GitHub that names the findings as waived plus `/unblock`, then `/implement <n>` continues"
WAIVER_B = "Next step B: `/unblock` with B chosen, then `/implement <n>` in `Claude Code or Codex` fixes the findings in this story"


def _text(name: str) -> str:
    return (COMMANDS / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["refine.md", "implement.md", "review.md"])
def test_command_instructs_a_next_step_per_option(name: str) -> None:
    text = _text(name)
    for statement in STATEMENTS:
        assert statement in text, f"{name} lacks: {statement}"
    assert "Resume instruction" not in text


def test_refine_names_the_spec_command_for_a_scope_option() -> None:
    text = _text("refine.md")
    assert "names `/spec <epic number>` in `Claude Code or Codex`" in text
    assert "option a) `/spec 418`" in text


@pytest.mark.parametrize("name", ["refine-epic.md", "refine-feature.md", "refine-story.md", "refine-task.md", "refine-bug.md"])
def test_refine_variants_rely_on_the_shared_rule(name: str) -> None:
    text = _text(name)
    assert "Resume instruction" not in text
    assert "refine.md" in text


@pytest.mark.parametrize("name", ["implement.md", "review.md"])
def test_waiver_blocker_names_a_next_step_for_each_option(name: str) -> None:
    text = _text(name)
    assert WAIVER_A in text
    assert WAIVER_B in text


def test_command_files_never_write_the_codex_trigger_phrase_in_a_next_step() -> None:
    from headless_pr_workflow.review_policy import CODEX_REVIEW_TRIGGER_PHRASE

    for name in ("refine.md", "implement.md", "review.md"):
        for line in _text(name).splitlines():
            if "Next step" in line:
                assert CODEX_REVIEW_TRIGGER_PHRASE not in line.lower()


def _declaration(steps: list[str]) -> str:
    return "\n".join(
        [
            "## Blocked Declaration",
            "Type: decision",
            "Checked: docs",
            "Question: Waive the findings?",
            "Options: A) waive — tracked in follow-ups",
            "B) fix here — larger story",
            "Recommendation: A, tracked",
            *steps,
            "Unblocked when: the PO replies",
        ]
    )


def _waiver_steps() -> list[str]:
    return [
        render("/unblock", 620, "a reply on GitHub", "the PO's reply names the findings as waived and /implement 608 continues", option="a"),
        render("/implement", 608, "Claude Code or Codex", "the findings are fixed in this story after /unblock with B chosen", option="b"),
    ]


def test_waiver_declaration_round_trips_through_the_shared_parser() -> None:
    steps = parse_declaration(_declaration(_waiver_steps()))
    assert set(steps) == {"a", "b"}
    a, b = steps["a"], steps["b"]
    assert (a.command, a.number, a.where) == ("/unblock", 620, "a reply on GitHub")
    assert (b.command, b.number, b.where) == ("/implement", 608, "Claude Code or Codex")
    assert a.then and b.then and a.then != b.then


@pytest.mark.parametrize("part", ["number", "where", "then"])
def test_a_waiver_step_missing_a_part_fails_the_parser(part: str) -> None:
    from headless_pr_workflow.next_step import NextStepError

    good = _waiver_steps()[1]
    broken = {
        "number": good.replace("`/implement 608`", "`/implement`"),
        "where": good.replace(" in Claude Code or Codex", ""),
        "then": good.split("; then")[0] + ".",
    }[part]
    with pytest.raises(NextStepError):
        parse_declaration(_declaration([_waiver_steps()[0], broken]))


def test_545_case_names_the_spec_command_and_a_distinct_step_per_other_option() -> None:
    option_a = render("/spec", 418, "Claude Code or Codex", "the PO merges the spec PR and the next /refine 545 continues", option="a")
    option_b = render("/unblock", 545, "a reply on GitHub", "the next /refine 545 continues with the answer", option="b")
    steps = parse_declaration(_declaration([option_a, option_b]))
    assert (steps["a"].command, steps["a"].number, steps["a"].where) == ("/spec", 418, "Claude Code or Codex")
    assert steps["a"].then != steps["b"].then
    assert steps["b"].where == "a reply on GitHub"


def _context(body: str) -> dict[str, str]:
    script = f'source "{ROOT}/scripts/lib/decision-blocker.sh"; decision_blocker_context "$1" "Story" "https://example.test/i/1"'
    out = subprocess.run(["bash", "-c", script, "_", body], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_decision_blocker_context_returns_next_steps_in_order_and_options_without_them() -> None:
    steps = _waiver_steps()
    ctx = _context(_declaration(steps))
    assert ctx["next_steps"].split("\n") == steps
    assert "Next step" not in ctx["options"]
    assert ctx["options"] == "A) waive — tracked in follow-ups\nB) fix here — larger story"
    assert ctx["unblocked_when"] == "the PO replies"


def test_decision_blocker_context_without_next_steps_gives_an_empty_field() -> None:
    assert _context(_declaration([]))["next_steps"] == ""


def test_decision_blocker_context_keeps_a_single_next_step_line() -> None:
    line = render("/unblock", 608, "a reply on GitHub", "the dispatcher resumes")
    assert _context(_declaration([line]))["next_steps"] == line


def test_decision_blocker_context_passes_the_body_only_as_data() -> None:
    body = _declaration(["Next step a: $(touch /tmp/hpw-608-injected) `x` \"q\""])
    ctx = _context(body)
    assert ctx["next_steps"].startswith("Next step a: $(touch")
    assert not Path("/tmp/hpw-608-injected").exists()
