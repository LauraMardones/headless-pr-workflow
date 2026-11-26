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

``decide_open_blocker`` (issue #646) is the one rule "open blocker" that the
copies listed under ``open_blocker`` in ``rules.py`` will call (#649 switches
them). The declaration types, the declarers to leave out and the conflict
round's head filter are caller inputs. ``hpw dispatcher session-blocker
decide`` runs it over comments a caller already read (``--input``), ``open``
over the story's comments read from GitHub. Both give ``read`` (0; ``outcome``
``open`` or ``none``) or ``unreadable`` (3; ``outcome`` ``unreadable``: the
input or the comments could not be read, so the caller fails closed).
"""

from __future__ import annotations

import os
import re
from typing import Any, Iterable, Mapping

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
_DECLARATION = re.compile(r"^## Blocked Declaration", re.M)
_MARKER_HEAD = re.compile(r"<!-- hpw:dispatcher [^>]*\bhead=([0-9a-fA-F]+)")


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


def _declared_by_any(declared_by: str, declarers: Iterable[str]) -> bool:
    name = declared_by.strip().lower()
    return any(name == value.lower() or name.startswith(f"{value.lower()} ") for value in declarers)


def _stale_for_head(comment: dict[str, Any], bot: str, head: str) -> bool:
    """A declaration of ``bot`` whose dispatcher markers all name another head (the conflict round's filter)."""

    if not (bot and head and str((comment.get("user") or {}).get("login") or "") == bot):
        return False
    heads = [h.lower() for h in _MARKER_HEAD.findall(str(comment.get("body") or ""))]
    return bool(heads) and head.lower() not in heads


def decide_open_blocker(
    facts: _Facts, comments: list[dict[str, Any]], *, types: Iterable[str] | None = BLOCKED_TYPES,
    exclude_declarers: Iterable[str] = (), bot: str = "", head: str = "", refine_pass_at: str = "",
) -> dict[str, Any] | None:
    """The newest open Blocked Declaration as ``{type, declared_by, url, created_at}``, or ``None`` (issue #646).

    A declaration counts when its comment is trusted (``facts.trusted``, or the ``bot`` login), has a
    ``## Blocked Declaration`` heading and a ``Type:`` in ``types`` (``None``: any declaration, with or
    without a ``Type:`` line), and its ``Declared by:`` is not one of ``exclude_declarers``. An automation
    declaration counts unless the caller leaves it out (#556 → Decisions, case (7)). With ``bot`` and
    ``head``, a declaration of ``bot`` whose dispatcher markers all name another head does not count.

    It stays open until a later standalone PO ``/unblock`` line lifts it (every type), or, for every
    type except ``decision``, a later trusted ``Unblocked`` comment (``PROJECT-STATUS.md`` → Resolution).
    ``refine_pass_at``: the time of a passing refine Session Summary, which lifts an earlier
    ``dependency`` declaration (#529). Pure: no gateway, no model, no write.
    """

    wanted = None if types is None else {t.lower() for t in types}
    excluded = list(exclude_declarers)
    found: dict[str, Any] | None = None
    for comment in comments:
        body = str(comment.get("body") or "")
        trusted = facts.trusted(comment) or bool(bot) and str((comment.get("user") or {}).get("login") or "") == bot
        if not (trusted and _DECLARATION.search(body)):
            continue
        type_match, by_match = _TYPE_LINE.search(body), _DECLARED_BY.search(body)
        kind = type_match.group(1).lower() if type_match else None
        declared_by = by_match.group(1) if by_match else None
        if wanted is not None and kind not in wanted:
            continue
        if (declared_by and excluded and _declared_by_any(declared_by, excluded)) or _stale_for_head(comment, bot, head):
            continue
        since = str(comment.get("created_at") or "")
        if facts.po_login and _po_unblocked_after(comments, facts.po_login, since):
            continue
        if kind != "decision" and any(
            facts.trusted(c) and str(c.get("created_at") or "") > since and _lifts(str(c.get("body") or ""))
            for c in comments
        ):
            continue
        if kind == "dependency" and refine_pass_at and refine_pass_at > since:
            continue
        if found is None or since >= found["created_at"]:
            found = {"type": kind, "declared_by": declared_by, "url": comment.get("html_url"), "created_at": since}
    return found


def _open_result(operation: str, blocker: dict[str, Any] | None) -> Result:
    if blocker is None:
        return Result(operation, "read", fields={"outcome": "none"}, block=BLOCK)
    return Result(operation, "read", fields={"outcome": "open", **blocker}, block=BLOCK)


def _unreadable(operation: str, error: str) -> Result:
    return Result(operation, "unreadable", error=error, fields={"outcome": "unreadable"}, block=BLOCK)


def decide_open_blocker_result(facts: _Facts, comments: Any, **inputs: Any) -> Result:
    """``session-blocker decide``: the rule over comments a caller already read; a non-list input is unreadable."""

    if not isinstance(comments, list) or not all(isinstance(c, dict) for c in comments):
        return _unreadable("decide", "input: the comments are not a JSON array of objects")
    return _open_result("decide", decide_open_blocker(facts, comments, **inputs))


def read_open_blocker(gateway: Gateway, story: int, *, env: Mapping[str, str] | None = None, **inputs: Any) -> Result:
    """``session-blocker open``: the rule over the story's comments; a failed read is unreadable (fail closed)."""

    env = os.environ if env is None else env
    try:
        comments = gateway.list_comments(story)
    except GatewayError as exc:
        log(f"[SESSION-BLOCKER] open: unreadable: {exc}")
        return _unreadable("open", str(exc))
    return _open_result("open", decide_open_blocker(_Facts(gateway, env), list(comments), **inputs))
