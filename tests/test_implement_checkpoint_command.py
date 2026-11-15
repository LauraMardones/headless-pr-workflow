"""implement.md keeps the checkpoint, turn-budget and slice-read rules of issue #490."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMAND = (ROOT / ".claude/commands/implement.md").read_text(encoding="utf-8")


def step_14() -> str:
    start = COMMAND.index("14. Push commits to the same PR branch")
    return COMMAND[start:COMMAND.index("15. Before requesting review")]


def step_10() -> str:
    start = COMMAND.index("10. If a PR already exists:")
    return COMMAND[start:COMMAND.index("11. Implement only")]


def step_16() -> str:
    start = COMMAND.index("16. When implementation is complete")
    return COMMAND[start:COMMAND.index("17. If you are addressing")]


def test_checkpoint_push_rule() -> None:
    step = step_14()

    assert "after the first working edit" in step
    assert "after each later coherent group of edits" in step
    assert "before a long test run" in step
    assert "every 15 turns" in step
    assert "uncommitted changes" in step
    assert "no rebase and no force-push" in step


def test_turn_budget_and_stop_at_forty() -> None:
    step = step_14()

    assert "limit is 60 turns" in step
    assert "`AGENT_MAX_TURNS`" in step
    assert "about 40 turns" in step
    assert "stop starting new work" in step


def test_early_stop_pushes_keeps_draft_and_hands_off_for_implementation() -> None:
    step = step_14()

    assert "push the work first, keep the PR Draft" in step
    assert "`--next implementation`" in step
    assert "what is done and what is left" in step


def test_large_files_are_read_in_slices() -> None:
    step = step_14()

    assert "read large files in slices" in step
    assert "never read a whole large file" in step
    assert "`scripts/dispatcher-invoke.sh`" in step
    assert "`tests/dispatcher_fakes.py`" in step


def test_checkpoint_never_readies_or_requests_review() -> None:
    step = step_14()

    assert "never marks the PR Ready for review" in step
    assert "never posts a review request" in step
    assert "makes no Draft conversion" in step


def test_step_14_does_not_write_the_trigger_phrase() -> None:
    from headless_pr_workflow.review_policy import CODEX_REVIEW_TRIGGER_PHRASE

    assert CODEX_REVIEW_TRIGGER_PHRASE not in step_14()


def test_steps_10_and_16_text_still_present() -> None:
    assert "keep a PR that is Ready for review as it is for the whole round" in step_10()
    assert "do not convert it to Draft before pushing" in step_10()
    step = step_16()
    assert "only when the current head's `pytest` check is green" in step
    assert "`--next review` is only for the green, Ready-for-review completion path" in step
    assert "never rebase and never force-push" in step


def test_agent_max_turns_default_is_still_60() -> None:
    script = (ROOT / "scripts/dispatcher-invoke.sh").read_text(encoding="utf-8")

    assert re.search(r'^AGENT_MAX_TURNS="\$\{AGENT_MAX_TURNS:-60\}"', script, re.M)
    assert "limit is 60 turns" in step_14()
