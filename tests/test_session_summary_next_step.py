"""`scripts/session-summary.sh --next-step` against its readers and the command texts (issue #613)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from headless_pr_workflow.dispatcher import codex_worker, session_blocker
from headless_pr_workflow.review_policy import CODEX_REVIEW_TRIGGER_PHRASE
from tests.test_pr_context_script import load_script
from tests.script_tools import host_env, requires

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "session-summary.sh"
STEP = "Next step: reply `/unblock` on #620 on GitHub; then the dispatcher resumes /review 620 with Decision A."

CASES = [("implement", "review"), ("implement", "implementation"), ("implement", "finding"),
         ("review", "merge"), ("review", "implementation")]


def summary(command: str, nxt: str, step: str | None) -> str:
    args = ["bash", str(SCRIPT), "--command", command, "--pr", "620", "--head", "abc1234",
            "--checks", "pass", "--blockers", "none", "--next", nxt]
    if command == "implement":
        args += ["--issue", "613"]
    if step:
        args += ["--next-step", step]
    env = {**host_env(), "HPW_BIN": str(ROOT / "scripts" / "hpw")}
    return subprocess.run(args, env=env, capture_output=True, text=True, check=True, timeout=60).stdout


@requires("bash")
@pytest.mark.parametrize(("command", "nxt"), CASES)
def test_readers_give_the_same_result_with_and_without_the_line(command: str, nxt: str) -> None:
    plain, stepped = summary(command, nxt, None), summary(command, nxt, STEP)

    assert STEP in stepped.splitlines() and STEP not in plain
    assert bool(codex_worker._SUMMARY_NEXT.search(plain)) == bool(codex_worker._SUMMARY_NEXT.search(stepped))
    plain_match = session_blocker._SUMMARY_NEXT.search(plain)
    stepped_match = session_blocker._SUMMARY_NEXT.search(stepped)
    assert plain_match and stepped_match and plain_match.group(1) == stepped_match.group(1) == nxt
    context = load_script()
    comment = lambda body: [{"createdAt": "2026-01-01T00:00:00Z", "body": body}]  # noqa: E731
    with_line = context.latest_summary(comment(stepped))
    without_line = context.latest_summary(comment(plain))
    assert with_line is not None and without_line is not None
    assert [line for line in with_line if line != STEP] == without_line


@pytest.mark.parametrize("name", ["implement", "review"])
def test_command_text_tells_the_session_to_pass_the_rendered_next_step(name: str) -> None:
    text = (ROOT / ".claude" / "commands" / f"{name}.md").read_text(encoding="utf-8")

    assert "--next-step" in text
    assert "hpw next-step render" in text
    assert "Decision <letter>" in text
    assert "`/unblock`" in text
    assert CODEX_REVIEW_TRIGGER_PHRASE.lower() not in text.lower()
