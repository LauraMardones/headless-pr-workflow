"""The one source of the PO's next-step text (issue #605, Epic #566).

A next step names three parts: the command with its issue or PR number,
where to run it, and what happens afterwards. ``render`` builds the text
from the parts and rejects an empty or invalid part; ``parse`` reads the
parts back from a rendered line, so the producers' tests and the E4 check
(``tests/test_next_step_kinds_check.py``) use one parser.

Two line shapes, chosen by the place:

- a session command: ``Next step: run `/spec 418` in Claude Code or Codex; then <outcome>.``
- a reply on GitHub: ``Next step: reply `/unblock` on #605 on GitHub; then <outcome>.``

A reply is written on the issue or PR, so its command keeps the exact text
the PO posts (``/unblock`` must be a line of its own, docs/PROJECT-STATUS.md
→ Resolution) and the number names where to post it. A ``Type: decision``
declaration carries one ``Next step <letter>:`` line per option.

The text never contains the Codex review trigger phrase (AGENTS.md → Codex
Review Requests): a next step names the command that posts it.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .review_policy import CODEX_REVIEW_TRIGGER_PHRASE


CLAUDE_CODE = "Claude Code"
CODEX = "Codex"
GITHUB_REPLY = "a reply on GitHub"
PLACES: tuple[str, ...] = (CLAUDE_CODE, CODEX, GITHUB_REPLY)
# Two places join only as session places: a command written in a reply on
# GitHub is not run in a session, so it does not pair with one.
SESSION_PLACES: tuple[str, ...] = (CLAUDE_CODE, CODEX)

LABEL = "Next step"
_LABEL_RE = r"Next step(?: (?P<option>[a-z]))?:"
_SESSION_LINE = re.compile(
    rf"^{_LABEL_RE} run `(?P<command>[^`\n]+) (?P<number>[1-9][0-9]*)` in (?P<where>[^;\n]+); then (?P<then>[^\n]+)\.$"
)
_REPLY_LINE = re.compile(
    rf"^{_LABEL_RE} reply `(?P<command>[^`\n]+)` on #(?P<number>[1-9][0-9]*) on GitHub; then (?P<then>[^\n]+)\.$"
)
_LINE_START = re.compile(rf"^{_LABEL_RE}")


class NextStepError(ValueError):
    """A part is missing or invalid; the message names the part."""


@dataclass(frozen=True)
class NextStep:
    command: str
    number: int
    where: str
    then: str
    option: str | None = None

    @property
    def text(self) -> str:
        label = f"{LABEL} {self.option}:" if self.option else f"{LABEL}:"
        if self.where == GITHUB_REPLY:
            return f"{label} reply `{self.command}` on #{self.number} on GitHub; then {self.then}."
        return f"{label} run `{self.command} {self.number}` in {self.where}; then {self.then}."

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        if self.option is None:
            del data["option"]
        data["text"] = self.text
        return data


def normalize_where(where: str) -> str:
    """Return the allowed place, or raise: one of PLACES, or two session places joined by "or"."""
    value = " ".join((where or "").split())
    if not value:
        raise NextStepError("where to run it is missing")
    if value in PLACES:
        return value
    parts = value.split(" or ")
    if len(parts) == 2 and all(p in SESSION_PLACES for p in parts) and parts[0] != parts[1]:
        return value
    allowed = ", ".join(f"`{p}`" for p in PLACES)
    raise NextStepError(f"where to run it must be one of {allowed}, or `Claude Code or Codex`; got `{value}`")


def build(command: str, number: int | str, where: str, then: str, option: str | None = None) -> NextStep:
    """Check every part and return the next step; raise NextStepError naming a missing or invalid part."""
    command = " ".join((command or "").split())
    if not command:
        raise NextStepError("the command is missing")
    if "`" in command:
        raise NextStepError("the command must not contain a backtick")
    try:
        number = int(str(number).strip())
    except ValueError:
        raise NextStepError("the issue or PR number is missing") from None
    if number < 1:
        raise NextStepError("the issue or PR number is missing")
    where = normalize_where(where)
    then = " ".join((then or "").split()).rstrip(".").rstrip()
    if not then:
        raise NextStepError("what happens afterwards is missing")
    if option is not None and not re.fullmatch(r"[a-z]", option):
        raise NextStepError("the option must be one lower-case letter")
    for part in (command, then):
        if CODEX_REVIEW_TRIGGER_PHRASE in part.lower():
            raise NextStepError("a next step names the command that requests a Codex review, never its trigger phrase")
    return NextStep(command=command, number=number, where=where, then=then, option=option)


def render(command: str, number: int | str, where: str, then: str, option: str | None = None) -> str:
    """The one-line next-step text; raise NextStepError on a missing or invalid part."""
    return build(command, number, where, then, option).text


def parse(line: str) -> NextStep:
    """Read the three parts back from one rendered line; raise NextStepError when a part is missing."""
    line = (line or "").strip()
    match = _SESSION_LINE.match(line)
    if match is None:
        match = _REPLY_LINE.match(line)
        where = GITHUB_REPLY
    else:
        where = match.group("where")
    if match is None:
        if not _LINE_START.match(line):
            raise NextStepError("the line is not a next step")
        raise NextStepError(
            "the next step lacks a part: it must name the command with its number, where to run it and what happens afterwards"
        )
    step = build(match.group("command"), match.group("number"), where, match.group("then"), match.group("option"))
    if step.text != line:
        raise NextStepError("the next step is not in the rendered form")
    return step


def parse_declaration(body: str) -> dict[str | None, NextStep]:
    """Every ``Next step:`` and ``Next step <letter>:`` line of a comment, by option letter (None for the plain field)."""
    steps: dict[str | None, NextStep] = {}
    for raw in (body or "").replace("\r", "").split("\n"):
        if _LINE_START.match(raw.strip()):
            step = parse(raw)
            if step.option in steps:
                raise NextStepError(f"the next step for option {step.option or '(none)'} appears twice")
            steps[step.option] = step
    return steps
