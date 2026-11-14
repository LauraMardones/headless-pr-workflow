"""Session-blocker rule: ``hpw dispatcher session-blocker read`` (issue #533, ADR-014).

Decides, for a story whose ``/implement`` session ended without a linked open PR,
whether the story has an open Blocked Declaration that the dispatcher did not
post. ``open_session_blocker`` is pure: it gets no gateway, uses no model and
writes nothing. ``read_session_blocker`` reads the story's comments once.

A declaration counts when its comment is trusted (``_Facts.trusted``), carries a
``Type:`` line of a Blocked Status Protocol type and a ``Declared by:`` line that
is not an automation value, and nothing lifts it: a PO ``/unblock`` line or a
trusted ``Unblocked`` comment after it. The newest such declaration is reported.

Result words and exit codes: ``read`` (0; ``outcome`` is ``open_blocker`` or
``none``), ``unreadable`` (3: the comments could not be read; the caller keeps
its own blocker, so the rule fails closed).
"""

from __future__ import annotations

import os
import re
from typing import Any, Mapping

from .facts import _Facts
from .gateway import Gateway, GatewayError
from .write import Result, _po_unblocked_after, log

BLOCK = "session-blocker"
OPERATION = "read"

BLOCKED_TYPES = ("decision", "dependency", "external", "conflict", "resource")
# The automation values of ``Declared by:``: DECLARED_BY of scripts/dispatcher-invoke.sh and of codex_review_bookkeeping.py.
AUTOMATION_DECLARERS = ("dispatcher", "codex-review-bookkeeping")
_TYPE_LINE = re.compile(r"^[ \t]*Type:\s*([A-Za-z]+)\b", re.M)
_DECLARED_BY = re.compile(r"^[ \t]*Declared by:[ \t]*(.+?)[ \t]*$", re.M)
_UNBLOCKED_LINE = re.compile(r"^\s*Unblocked\b(?!\s+when\b)", re.M | re.I)


def _automation(declared_by: str) -> bool:
    name = declared_by.strip().lower()
    return any(name == value or name.startswith(f"{value} ") for value in AUTOMATION_DECLARERS)


def _lifts(body: str) -> bool:
    """A trusted ``Unblocked`` comment that is not itself a declaration (its ``Unblocked when:`` field is no resolution)."""

    return "## Blocked Declaration" not in body and _UNBLOCKED_LINE.search(body) is not None


def open_session_blocker(facts: _Facts, comments: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The newest open non-dispatcher Blocked Declaration as ``{type, declared_by, url, created_at}``, or ``None``."""

    found: dict[str, Any] | None = None
    for comment in comments:
        body = str(comment.get("body") or "")
        type_match, by_match = _TYPE_LINE.search(body), _DECLARED_BY.search(body)
        if not (facts.trusted(comment) and "## Blocked Declaration" in body and type_match and by_match):
            continue
        kind, declared_by = type_match.group(1).lower(), by_match.group(1)
        if kind not in BLOCKED_TYPES or _automation(declared_by):
            continue
        since = str(comment.get("created_at") or "")
        if facts.po_login and _po_unblocked_after(comments, facts.po_login, since):
            continue
        if any(facts.trusted(c) and str(c.get("created_at") or "") > since and _lifts(str(c.get("body") or ""))
               for c in comments):
            continue
        if found is None or since >= found["created_at"]:
            found = {"type": kind, "declared_by": declared_by, "url": comment.get("html_url"), "created_at": since}
    return found


def read_session_blocker(gateway: Gateway, story: int, *, env: Mapping[str, str] | None = None) -> Result:
    env = os.environ if env is None else env
    try:
        comments = gateway.list_comments(story)
    except GatewayError as exc:
        log(f"[SESSION-BLOCKER] unreadable: {exc}")
        return Result(OPERATION, "unreadable", error=str(exc), fields={"outcome": "none"}, block=BLOCK)
    blocker = open_session_blocker(_Facts(gateway, env), list(comments))
    if blocker is None:
        return Result(OPERATION, "read", fields={"outcome": "none"}, block=BLOCK)
    return Result(OPERATION, "read", fields={"outcome": "open_blocker", **blocker}, block=BLOCK)
