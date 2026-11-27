"""The note on a story the scheduler skips with ``no-feature`` (issue #616, Feature #460, F2.11).

``hpw dispatcher no-feature-note`` reads the scheduler's ``skipped`` list and posts one
comment on each ``no-feature`` story, through the verified write of Block 1, once per
issue: a comment of the dispatcher's own account that carries the marker
``action=no-feature-note`` (found with Block 2, ``marker list``) stops every later note,
also when the story has no ``Feature group:`` again. Comments that cannot be read write
nothing (unreadable, never absent). It writes no Slack event and starts no session.
"""

from __future__ import annotations

from typing import Any

from .gateway import Gateway
from .marker import DEFAULT_AUTHOR, dispatcher_marker, list_markers, marker_text
from .write import Result, Settings, write_comment

BLOCK = "no-feature-note"
ACTION = "no-feature-note"
REASON = "no-feature"
MARKER = dispatcher_marker(marker_text(ACTION, []))

NOTE_BODY = """## Not scheduled: no Feature group

The scheduler skipped this story with the reason `no-feature`: the story has no `Feature group:` line, so the scheduler does not refine it.

To fix it, add a `Feature group: #<n>` line to the issue body that names a Feature of the story's Epic. Group by capability and prefer an open Feature, as `/refine-epic` says.

This note is written once per issue."""


def note_text() -> str:
    return f"{NOTE_BODY}\n\n{MARKER}"


def no_feature_issues(skipped: Any) -> list[int] | None:
    """The issues of the ``no-feature`` entries, in order and without repeats; ``None`` when ``skipped`` is not a list of entries."""

    if not isinstance(skipped, list):
        return None
    issues: list[int] = []
    for entry in skipped:
        if not isinstance(entry, dict):
            return None
        number = entry.get("issue")
        if entry.get("reason") != REASON:
            continue
        if type(number) is not int or number < 1:
            return None
        if number not in issues:
            issues.append(number)
    return issues


def note_one(gateway: Gateway, number: int, *, dry_run: bool, settings: Settings) -> dict[str, Any]:
    """``{"issue", "result"[, "error"]}`` for one story; ``result`` is written, already, dry-run, failed or unreadable."""

    held = list_markers(gateway, number, marker=MARKER, author=DEFAULT_AUTHOR)
    if held.result != "read":
        return {"issue": number, "result": "unreadable", "error": held.error or "comments unreadable"}
    if held.fields.get("comments"):
        return {"issue": number, "result": "already"}
    outcome = write_comment(gateway, number, note_text(), dry_run=dry_run, settings=settings)
    if outcome.result in ("written", "already"):
        return {"issue": number, "result": "written" if outcome.result == "written" else "already"}
    if outcome.result == "dry-run":
        return {"issue": number, "result": "dry-run"}
    return {"issue": number, "result": "failed", "error": outcome.error or "write failed"}


def run(gateway: Gateway, skipped: Any, *, dry_run: bool = False, settings: Settings | None = None) -> Result:
    """Note every ``no-feature`` story; the Result holds ``stories``. Exit 0 all noted, 1 a write failed, 3 unreadable."""

    settings = settings or Settings.from_env()
    issues = no_feature_issues(skipped)
    if issues is None:
        return Result("write", "unreadable", error="the scheduler's skipped list is not a list of entries",
                      fields={"stories": []}, block=BLOCK)  # fmt: skip
    stories = [note_one(gateway, n, dry_run=dry_run, settings=settings) for n in issues]
    results = {s["result"] for s in stories}
    word = "unreadable" if "unreadable" in results else "failed" if "failed" in results else "written"
    if word == "written" and "dry-run" in results:
        word = "dry-run"
    return Result("write", word, fields={"stories": stories}, block=BLOCK)


def human_lines(stories: list[dict[str, Any]]) -> list[str]:
    words = {"written": "written", "already": "already there", "dry-run": "dry-run"}
    lines = []
    for s in stories:
        if s["result"] in words:
            lines.append(f"[NO-FEATURE NOTE] #{s['issue']}: {words[s['result']]}")
        else:
            lines.append(f"[NO-FEATURE NOTE] #{s['issue']}: {s['result']}: {s.get('error')}")
    return lines


