"""Block 2: markers under the dispatcher's own account (ADR-014 → Decision → 3 → Block 2, issue #433).

A marker counts only on a comment whose ``user.login`` is the account that
should have written it. A login that cannot be resolved, or comments that cannot
be read, give ``unreadable`` (exit 3) and never match any author.

Result words and exit codes:

- ``holds`` → ``holds`` (0), ``absent``, ``unblocked``, ``cancelled`` (1), ``unreadable`` (3)
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


def marker_holds(
    gateway: Gateway, number: int, marker: str, *, cancel: str = "", author: str = DEFAULT_AUTHOR,
    settings: Settings | None = None, env: Mapping[str, str] | None = None,
) -> Result:
    """The ``marker_holds`` rule of #262/#293: the newest own comment carrying ``marker`` holds
    unless a later PO ``/unblock`` line, or a later own comment carrying ``cancel``, follows it."""

    settings = settings or Settings.from_env(env)
    try:
        comments, own = _own_comments(gateway, number, author, env)
    except Unreadable as exc:
        return Result("holds", "unreadable", error=str(exc), fields=_decided_by(None), block=BLOCK)
    held = [c for c in own if _contains(c, marker)]
    if not held:
        return Result("holds", "absent", fields=_decided_by(None), block=BLOCK)
    newest = held[-1]
    since = str(newest.get("created_at") or "")
    if _po_unblocked_after(comments, settings.po_login, since):
        return Result("holds", "unblocked", fields=_decided_by(newest), block=BLOCK)
    if cancel:
        for c in own:
            if str(c.get("created_at") or "") > since and _contains(c, cancel):
                return Result("holds", "cancelled", fields=_decided_by(c), block=BLOCK)
    return Result("holds", "holds", fields=_decided_by(newest), block=BLOCK)


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
