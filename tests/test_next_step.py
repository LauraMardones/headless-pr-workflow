"""The shared next-step source (issue #605): ``headless_pr_workflow.next_step``,
``hpw next-step render`` and the ``Next step`` boundary of
``scripts/lib/decision-blocker.sh``."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from headless_pr_workflow.cli import main as hpw_main
from headless_pr_workflow.next_step import (
    PLACES,
    NextStep,
    NextStepError,
    build,
    parse,
    parse_declaration,
    render,
)
from headless_pr_workflow.review_policy import CODEX_REVIEW_TRIGGER_PHRASE
from tests.script_tools import BASH, host_env, requires


ROOT = Path(__file__).resolve().parents[1]
PARTS = {"command": "/implement", "number": 605, "where": "Claude Code or Codex", "then": "the story starts"}


def test_render_names_the_three_parts() -> None:
    assert render(**PARTS) == "Next step: run `/implement 605` in Claude Code or Codex; then the story starts."


def test_a_reply_on_github_names_the_exact_reply_and_where_to_post_it() -> None:
    text = render("/unblock", 605, "a reply on GitHub", "the dispatcher resumes the story.")

    assert text == "Next step: reply `/unblock` on #605 on GitHub; then the dispatcher resumes the story."


@pytest.mark.parametrize(
    ("part", "value", "message"),
    [
        ("command", "", "the command is missing"),
        ("command", "   ", "the command is missing"),
        ("number", "", "the issue or PR number is missing"),
        ("number", 0, "the issue or PR number is missing"),
        ("number", "abc", "the issue or PR number is missing"),
        ("where", "", "where to run it is missing"),
        ("then", "", "what happens afterwards is missing"),
        ("then", " . ", "what happens afterwards is missing"),
    ],
)
def test_render_rejects_each_missing_part(part: str, value: object, message: str) -> None:
    with pytest.raises(NextStepError, match=message):
        render(**{**PARTS, part: value})


@pytest.mark.parametrize("where", list(PLACES) + ["Claude Code or Codex", "Codex or Claude Code"])
def test_allowed_places(where: str) -> None:
    assert build(**{**PARTS, "where": where}).where == where


@pytest.mark.parametrize(
    "where",
    ["Slack", "a terminal", "Claude Code or a reply on GitHub", "Codex or Codex", "Claude Code, Codex or a reply on GitHub"],
)
def test_a_place_outside_the_allowed_three_is_rejected(where: str) -> None:
    with pytest.raises(NextStepError, match="where to run it must be one of"):
        render(**{**PARTS, "where": where})


@pytest.mark.parametrize("part", ["command", "then"])
@pytest.mark.parametrize("phrase", [CODEX_REVIEW_TRIGGER_PHRASE, CODEX_REVIEW_TRIGGER_PHRASE.upper()])
def test_a_text_with_the_codex_review_trigger_phrase_is_rejected(part: str, phrase: str) -> None:
    with pytest.raises(NextStepError, match="never its trigger phrase"):
        render(**{**PARTS, part: f"post {phrase} on the PR"})


def test_parse_rejects_a_text_with_the_codex_review_trigger_phrase() -> None:
    line = f"Next step: run `/implement 605` in Codex; then post {CODEX_REVIEW_TRIGGER_PHRASE}."

    with pytest.raises(NextStepError, match="never its trigger phrase"):
        parse(line)


@pytest.mark.parametrize("where", ["Claude Code", "Codex", "Claude Code or Codex", "a reply on GitHub"])
@pytest.mark.parametrize("option", [None, "a"])
def test_round_trip(where: str, option: str | None) -> None:
    step = build("hpw pr-takeover", 12, where, "the merge continues", option)

    assert parse(step.text) == step


@pytest.mark.parametrize(
    "line",
    [
        "Next step: run `/implement` in Codex; then the story starts.",
        "Next step: run `/implement 605`; then the story starts.",
        "Next step: run `/implement 605` in Codex.",
        "Next step: run `/implement 605` in Slack; then the story starts.",
        "Next step: reply `/unblock` on GitHub; then the story starts.",
        "Next step: n/a",
    ],
    ids=["no-number", "no-place", "no-outcome", "bad-place", "reply-no-number", "n/a"],
)
def test_parse_fails_for_each_missing_part(line: str) -> None:
    with pytest.raises(NextStepError):
        parse(line)


def test_parse_rejects_a_line_that_is_no_next_step() -> None:
    with pytest.raises(NextStepError, match="not a next step"):
        parse("Resume instruction: n/a")


# --- the #545 case: a decision blocker names a next step per option ----------

def _decision_declaration_545() -> str:
    step_a = build("/spec", 418, "Claude Code or Codex",
                   "the PO merges the spec PR and the next `/refine 545` continues", option="a")
    step_b = build("/unblock", 545, "a reply on GitHub",
                   "the next `/refine 545` continues with the current spec", option="b")
    return "\n".join([
        "## Blocked Declaration",
        "Type: decision",
        "Checked: docs/PROJECT-STATUS.md, docs/decisions/ADR-*.md, Epic #418 → Decisions; no existing decision found",
        "Question: Does the answer change the spec of Epic #418?",
        "Options: a) change the spec — the story waits for a spec PR",
        "b) keep the spec — refinement continues",
        "Recommendation: a — the answer changes an acceptance criterion.",
        "Declared by: claude",
        "Blocks: #545 — Story",
        "Unblocked when: the PO replies with a decision and a line that is exactly `/unblock`",
        "Owner: PO",
        "State of in-progress work: none",
        step_a.text,
        step_b.text,
    ])


def test_545_decision_blocker_names_spec_418_in_claude_code_for_option_a() -> None:
    steps = parse_declaration(_decision_declaration_545())

    assert set(steps) == {"a", "b"}
    assert steps["a"] == NextStep("/spec", 418, "Claude Code or Codex",
                                  "the PO merges the spec PR and the next `/refine 545` continues", "a")
    assert "`/spec 418`" in steps["a"].text and "Claude Code" in steps["a"].text
    for letter, step in steps.items():
        assert parse(step.text) == step
        assert step.text.startswith(f"Next step {letter}: ")


def test_parse_declaration_fails_when_an_option_step_lacks_a_part() -> None:
    body = _decision_declaration_545().replace(" in Claude Code or Codex", "")

    with pytest.raises(NextStepError, match="lacks a part"):
        parse_declaration(body)


def test_parse_declaration_rejects_a_repeated_option() -> None:
    body = _decision_declaration_545()
    line_a = body.splitlines()[-2]

    with pytest.raises(NextStepError, match="appears twice"):
        parse_declaration(body + "\n" + line_a)


# --- hpw next-step render ------------------------------------------------------

def _render_args(**overrides: str) -> list[str]:
    parts = {"--command": "/implement", "--number": "605", "--where": "Claude Code or Codex", "--then": "the story starts"}
    parts.update(overrides)
    args = ["next-step", "render"]
    for flag, value in parts.items():
        if value is not None:
            args += [flag, value]
    return args


def test_cli_prints_the_same_text_as_the_function(capsys: pytest.CaptureFixture[str]) -> None:
    assert hpw_main(_render_args()) == 0

    assert capsys.readouterr().out == render(**PARTS) + "\n"


def test_cli_json_prints_the_three_parts(capsys: pytest.CaptureFixture[str]) -> None:
    assert hpw_main(_render_args() + ["--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data == {**PARTS, "text": render(**PARTS)}


@pytest.mark.parametrize(
    ("flag", "message"),
    [
        ("--command", "the command is missing"),
        ("--number", "the issue or PR number is missing"),
        ("--where", "where to run it is missing"),
        ("--then", "what happens afterwards is missing"),
    ],
)
def test_cli_exits_1_with_a_stderr_message_on_a_missing_part(
    flag: str, message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert hpw_main(_render_args(**{flag: None})) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert message in captured.err


def test_cli_exits_1_on_a_place_that_is_not_allowed(capsys: pytest.CaptureFixture[str]) -> None:
    assert hpw_main(_render_args(**{"--where": "Slack"})) == 1

    assert "where to run it must be one of" in capsys.readouterr().err


@pytest.mark.parametrize("args", [["next-step", "draw"], ["next-step"], _render_args() + ["--colour", "red"]])
def test_cli_exits_2_on_a_usage_error(args: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        hpw_main(args)

    assert exc.value.code == 2


_SHELL_CALLER = r"""
set -u
hpw="$REPO/scripts/hpw"
step=$("$hpw" next-step render --command /spec --number 418 --where "Claude Code or Codex" \
    --then "the PO merges the spec PR")
printf 'text=%s\n' "$step"
"$hpw" next-step render --command /spec --number 418 --where "Claude Code" --then "" 2>err.txt
printf 'missing=%s\n' "$?"
printf 'err=%s\n' "$(cat err.txt)"
"$hpw" next-step render --command /unblock --number 605 --where "a reply on GitHub" --then "x" --json \
    | jq -r '[.command, (.number | tostring), .where, .then] | join("|")'
"""


@requires("bash", "jq")
def test_bash_caller_gets_the_text_and_the_exit_codes(tmp_path: Path) -> None:
    env = {**host_env(), "REPO": ROOT.as_posix()}

    result = subprocess.run([BASH, "-c", _SHELL_CALLER], cwd=tmp_path, env=env, capture_output=True,
                            text=True, timeout=120)

    lines = result.stdout.splitlines()
    assert result.returncode == 0, result.stderr
    assert lines[0] == "text=" + render("/spec", 418, "Claude Code or Codex", "the PO merges the spec PR")
    assert lines[1] == "missing=1"
    assert lines[2] == "err=next-step: what happens afterwards is missing"
    assert lines[3] == "/unblock|605|a reply on GitHub|x"


# --- scripts/lib/decision-blocker.sh: Options ends before a Next step ---------

_CONTEXT_CALLER = r"""
set -eu
. "$REPO/scripts/lib/decision-blocker.sh"
decision_blocker_context "$(cat "$BODY")" "Story" "https://example.test/545"
"""


@requires("bash", "jq")
def test_decision_blocker_context_options_stop_before_a_next_step_line(tmp_path: Path) -> None:
    body = tmp_path / "body.txt"
    body.write_text(_decision_declaration_545(), encoding="utf-8")
    env = {**host_env(), "REPO": ROOT.as_posix(), "BODY": body.as_posix()}

    result = subprocess.run([BASH, "-c", _CONTEXT_CALLER], cwd=tmp_path, env=env, capture_output=True,
                            text=True, timeout=60)

    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)
    assert context["options"] == (
        "a) change the spec — the story waits for a spec PR\nb) keep the spec — refinement continues"
    )
    assert context["recommendation"] == "a — the answer changes an acceptance criterion."
    # Issue #608: the next steps are their own field, never part of another one.
    assert context["next_steps"].startswith("Next step a:")
    assert "Next step" not in json.dumps({k: v for k, v in context.items() if k != "next_steps"})


@requires("bash", "jq")
def test_decision_blocker_context_options_stop_before_a_plain_next_step_line(tmp_path: Path) -> None:
    body = tmp_path / "body.txt"
    body.write_text(
        "## Blocked Declaration\nType: decision\nOptions: a) one — x\nb) two — y\n"
        + render("/unblock", 545, "a reply on GitHub", "refinement continues") + "\n",
        encoding="utf-8",
    )
    env = {**host_env(), "REPO": ROOT.as_posix(), "BODY": body.as_posix()}

    result = subprocess.run([BASH, "-c", _CONTEXT_CALLER], cwd=tmp_path, env=env, capture_output=True,
                            text=True, timeout=60)

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["options"] == "a) one — x\nb) two — y"
