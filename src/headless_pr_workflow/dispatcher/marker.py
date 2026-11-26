"""Block 2: markers under the dispatcher's own account (ADR-014 → Decision → 3 → Block 2, issue #433).

A marker counts only on a comment whose ``user.login`` is the account that
should have written it. A login that cannot be resolved, or comments that cannot
be read, give ``unreadable`` (exit 3) and never match any author.

Result words and exit codes:

- ``holds`` → ``holds`` (0), ``absent``, ``unblocked``, ``cancelled`` (1), ``unreadable`` (3)
- ``decide`` → as ``holds``, over comments read from ``--input`` instead of GitHub (issue #628)
- ``merge-owner`` → ``holds`` (0), ``absent`` (1), ``unreadable`` (3): merge-owner evidence for a head (issue #647)
- ``merge-owner-decide`` → as ``merge-owner``, over comments read from ``--input`` instead of GitHub
- ``list``, ``whoami`` → ``read`` (0) or ``unreadable`` (3)
- ``write`` → Block 1's words (``written``, ``already``, ``dry-run``; ``failed`` is exit 1)
"""

from __future__ import annotations

import os
import re
from typing import Any, Mapping

from .gateway import Gateway, GatewayError
from .write import (
    DEFAULT_ATTEMPTS,
    Result,
    Settings,
    _po_unblocked_after,
    _author,
    write_comment,
)

BLOCK = "marker"
AUTHORS = ("dispatcher", "bookkeeping")
DEFAULT_AUTHOR = "dispatcher"

_WORD = re.compile(r"^[A-Za-z0-9_.-]+$")
_VALUE = re.compile(r"^[^\s]+$")


def dispatcher_marker(text: str) -> str:
    """The hidden marker, format unchanged since #293."""

    return f"<!-- hpw:dispatcher {text} -->"


class InvalidMarker(ValueError):
    pass


def marker_text(action: str, fields: list[tuple[str, str]]) -> str:
    """``action=<action> key=value ...``; raises ``InvalidMarker`` for a part that would break the format."""

    if not _WORD.match(action or ""):
        raise InvalidMarker("--action must be a word such as merge-owner")
    parts = [f"action={action}"]
    for key, value in fields:
        if not _WORD.match(key or ""):
            raise InvalidMarker(f"--field key must be a word: {key!r}")
        if not _VALUE.match(value or "") or "-->" in value:
            raise InvalidMarker(f"--field value must be one token without whitespace or '-->': {key}={value!r}")
        parts.append(f"{key}={value}")
    return " ".join(parts)


class Unreadable(Exception):
    """The login or the comments could not be read."""


def resolve_login(gateway: Gateway, author: str, env: Mapping[str, str] | None = None) -> str:
    """The login whose comments count as ``author``'s; raises ``Unreadable``."""

    env = os.environ if env is None else env
    if author == "bookkeeping":
        configured = (env.get("CODEX_BOOKKEEPING_LOGIN") or "").strip()
        if configured:
            return configured
    try:
        login = gateway.viewer_login()
    except GatewayError as exc:
        raise Unreadable(str(exc)) from None
    if not login or not login.strip():
        raise Unreadable("resolve dispatcher login: the login behind GH_TOKEN could not be read")
    return login.strip()


def _own_comments(gateway: Gateway, number: int, author: str, env: Mapping[str, str] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(all comments, comments of the author's account)``; raises ``Unreadable``."""

    login = resolve_login(gateway, author, env)
    try:
        comments = gateway.list_comments(number)
    except GatewayError as exc:
        raise Unreadable(str(exc)) from None
    return comments, [c for c in comments if _author(c) == login]


def _contains(comment: Mapping[str, Any], text: str) -> bool:
    return text in str(comment.get("body") or "")


def _decided_by(comment: Mapping[str, Any] | None) -> dict[str, Any]:
    comment = comment or {}
    return {"url": comment.get("html_url") or comment.get("url"), "created_at": comment.get("created_at")}


def _newest(comments: list[dict[str, Any]]) -> dict[str, Any]:
    """The comment with the newest ``created_at``; on a tie the one listed last (issue #628)."""

    return max(enumerate(comments), key=lambda item: (str(item[1].get("created_at") or ""), item[0]))[1]


def decide_holds(
    comments: list[dict[str, Any]], login: str, marker: str, *, po_login: str, cancel: str = "",
) -> tuple[str, dict[str, Any] | None]:
    """The one ``marker_holds`` rule of #262/#293 (issue #628): ``(word, deciding comment)``.

    The newest comment of ``login`` that carries ``marker`` (by ``created_at``, not list
    order) holds unless a later standalone PO ``/unblock`` line (``unblocked``), or a later
    comment of ``login`` carrying ``cancel`` (``cancelled``), follows it; with no such
    comment the word is ``absent``. ``comments`` are all authors' comments.
    """

    own = [c for c in comments if _author(c) == login]
    held = [c for c in own if _contains(c, marker)]
    if not held:
        return "absent", None
    newest = _newest(held)
    since = str(newest.get("created_at") or "")
    if _po_unblocked_after(comments, po_login, since):
        return "unblocked", newest
    if cancel:
        later = [c for c in own if str(c.get("created_at") or "") > since and _contains(c, cancel)]
        if later:
            return "cancelled", _newest(later)
    return "holds", newest


def marker_holds(
    gateway: Gateway, number: int, marker: str, *, cancel: str = "", author: str = DEFAULT_AUTHOR,
    settings: Settings | None = None, env: Mapping[str, str] | None = None,
) -> Result:
    """``decide_holds`` over the comments of ``number``, as the account of ``author``."""

    settings = settings or Settings.from_env(env)
    try:
        login = resolve_login(gateway, author, env)
        comments = gateway.list_comments(number)
    except (Unreadable, GatewayError) as exc:
        return Result("holds", "unreadable", error=str(exc), fields=_decided_by(None), block=BLOCK)
    word, comment = decide_holds(comments, login, marker, po_login=settings.po_login, cancel=cancel)
    return Result("holds", word, fields=_decided_by(comment), block=BLOCK)


def decide_marker(
    comments: Any, login: str, marker: str, *, po_login: str, cancel: str = "",
) -> Result:
    """``marker decide``: ``decide_holds`` over comments a caller already read (``conflict_round_rule``).

    Comments that are not a list of objects, or an empty login, give ``unreadable``.
    """

    if not isinstance(comments, list) or not all(isinstance(c, dict) for c in comments):
        return Result("decide", "unreadable", error="input: the comments are not a JSON array of objects",
                      fields=_decided_by(None), block=BLOCK)  # fmt: skip
    if not login.strip():
        return Result("decide", "unreadable", error="the login is empty", fields=_decided_by(None), block=BLOCK)
    word, comment = decide_holds(comments, login.strip(), marker, po_login=po_login, cancel=cancel)
    return Result("decide", word, fields=_decided_by(comment), block=BLOCK)


def merge_owner_marker(pr: int, head: str) -> str:
    """The merge-owner marker ``record_merge_owner`` posts for ``head`` on PR ``pr``."""

    return dispatcher_marker(f"action=merge-owner pr={pr} head={head}")


def merge_owner_pending_prefix(head: str) -> str:
    """The start of the evidence-pending copy of the merge-owner marker (issue #348) for ``head``."""

    return f"<!-- hpw:dispatcher action=evidence-pending item=merge-owner head={head} "


def decide_merge_owner(
    comments: list[dict[str, Any]], login: str, pr: int, head: str,
) -> tuple[str, str | None, dict[str, Any] | None]:
    """The one merge-owner-evidence rule (issue #647): ``(word, evidence, deciding comment)``.

    The head has merge-owner evidence (``holds``) when a comment of ``login`` on PR ``pr``
    carries the merge-owner marker for exactly ``head`` (``evidence`` ``marker``) or, with no
    such comment, its evidence-pending copy (``evidence`` ``evidence-pending``, decision 5 of
    #556). A later PO ``/unblock`` does not void it (decision 6 of #556). The match ignores
    case, as ``conflict_round_rule`` does. With neither the word is ``absent``. ``comments``
    are the PR's comments of all authors.
    """

    head = head.strip().lower()
    own = [c for c in comments if _author(c) == login]
    for evidence, text in (("marker", merge_owner_marker(pr, head)), ("evidence-pending", merge_owner_pending_prefix(head))):
        found = [c for c in own if text in str(c.get("body") or "").lower()]
        if found:
            return "holds", evidence, _newest(found)
    return "absent", None, None


def _merge_owner_result(operation: str, word: str, evidence: str | None, comment: dict[str, Any] | None,
                        head: str | None, error: str | None = None) -> Result:  # fmt: skip
    return Result(operation, word, error=error, block=BLOCK,
                  fields={"head": head, "evidence": evidence, **_decided_by(comment)})  # fmt: skip


def merge_owner_evidence(
    gateway: Gateway, pr: int, head: str = "", *, env: Mapping[str, str] | None = None,
) -> Result:
    """``decide_merge_owner`` over the comments of PR ``pr``, as the dispatcher's account.

    Without ``head`` the PR's current head is read. A PR, login or comment list that cannot
    be read gives ``unreadable`` (fail closed: no merge-owner evidence).
    """

    try:
        if not head.strip():
            head = str(((gateway.pull_request(pr).get("head") or {}).get("sha")) or "").strip()
            if not head:
                raise Unreadable(f"read PR #{pr}: the head SHA could not be read")
        login = resolve_login(gateway, DEFAULT_AUTHOR, env)
        comments = gateway.list_comments(pr)
    except (Unreadable, GatewayError) as exc:
        return _merge_owner_result("merge-owner", "unreadable", None, None, head.strip().lower() or None, str(exc))
    word, evidence, comment = decide_merge_owner(comments, login, pr, head)
    return _merge_owner_result("merge-owner", word, evidence, comment, head.strip().lower())


def decide_merge_owner_result(comments: Any, login: str, pr: int, head: str) -> Result:
    """``marker merge-owner-decide``: ``decide_merge_owner`` over comments a caller already read.

    Comments that are not a list of objects, or an empty login, give ``unreadable``.
    """

    head = head.strip().lower()
    if not isinstance(comments, list) or not all(isinstance(c, dict) for c in comments):
        return _merge_owner_result("merge-owner-decide", "unreadable", None, None, head,
                                   "input: the comments are not a JSON array of objects")  # fmt: skip
    if not login.strip():
        return _merge_owner_result("merge-owner-decide", "unreadable", None, None, head, "the login is empty")
    word, evidence, comment = decide_merge_owner(comments, login.strip(), pr, head)
    return _merge_owner_result("merge-owner-decide", word, evidence, comment, head)


def list_markers(
    gateway: Gateway, number: int, *, marker: str = "", author: str = DEFAULT_AUTHOR,
    env: Mapping[str, str] | None = None,
) -> Result:
    try:
        _, own = _own_comments(gateway, number, author, env)
    except Unreadable as exc:
        return Result("list", "unreadable", error=str(exc), fields={"comments": []}, block=BLOCK)
    rows = [
        {"id": c.get("id"), "created_at": c.get("created_at"), "body": str(c.get("body") or "")}
        for c in own if not marker or _contains(c, marker)
    ]
    return Result("list", "read", fields={"comments": rows}, block=BLOCK)


def whoami(gateway: Gateway, env: Mapping[str, str] | None = None) -> Result:
    try:
        dispatcher = resolve_login(gateway, "dispatcher", env)
        bookkeeping = resolve_login(gateway, "bookkeeping", env)
    except Unreadable as exc:
        return Result("whoami", "unreadable", error=str(exc), fields={"dispatcher": None, "bookkeeping": None}, block=BLOCK)
    return Result("whoami", "read", fields={"dispatcher": dispatcher, "bookkeeping": bookkeeping}, block=BLOCK)


def write_marker(
    gateway: Gateway, number: int, action: str, fields: list[tuple[str, str]], *, body: str = "",
    once: bool = False, attempts: int = DEFAULT_ATTEMPTS, dry_run: bool = False,
    settings: Settings | None = None, env: Mapping[str, str] | None = None,
) -> Result:
    """Post the marker (after ``body``, when given) through Block 1.

    With ``once``, nothing is posted when the same marker already holds; a marker
    state that cannot be read posts nothing either (``failed``).
    """

    settings = settings or Settings.from_env(env)
    marker = dispatcher_marker(marker_text(action, fields))
    text = f"{body.rstrip()}\n\n{marker}" if body.strip() else marker
    if once and not dry_run:
        state = marker_holds(gateway, number, marker, settings=settings, env=env)
        if state.result == "holds":
            return Result("write", "already", fields={"number": number, **state.fields, "attempts": 0}, block=BLOCK)
        if state.result == "unreadable":
            return Result("write", "failed", error=f"read markers: {state.error}",
                          fields={"number": number, "url": None, "created_at": None, "attempts": 0}, block=BLOCK)  # fmt: skip
    outcome = write_comment(gateway, number, text, attempts=attempts, dry_run=dry_run, settings=settings)
    return Result("write", outcome.result, error=outcome.error, fields=outcome.fields, block=BLOCK)
