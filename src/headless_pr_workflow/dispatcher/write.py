"""Block 1: the verified GitHub write (ADR-014 → Decision → 3 → Block 1, issue #432).

Each operation writes, reads the result back from GitHub, retries, and fails
closed. A write that cannot be verified is ``failed`` (exit 1), never "could not
decide". A comment is posted at most once per call: before a retry, the block
looks for an identical comment of the dispatcher's own account that was not
there before the first attempt.

Result words and exit codes:

- ``written``, ``already``, ``dry-run`` → exit 0
- ``pending`` (the evidence-pending comment was left), ``failed`` → exit 1
- ``retry-pending`` adds ``none``, ``posted`` (exit 0), ``stale``, ``missing``
  (exit 1) and ``gave-up`` (exit 4: retrying stopped until the PO's ``/unblock``).
"""

from __future__ import annotations

import base64
import binascii
import os
import re
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .gateway import BoardItem, Gateway, GatewayError, mask_tokens

BLOCK = "write"
DEFAULT_ATTEMPTS = 3
DEFAULT_RETRY_DELAY = 2.0
DEFAULT_EVIDENCE_RETRY_MAX = 3
DEFAULT_PO_LOGIN = "LauraMardones"
EVIDENCE_PENDING_ATTEMPTS = 3

# A label the block may have to create before it can add it (the repository has
# no such label yet). Unknown labels are created with the default colour only.
KNOWN_LABELS = {
    "evidence:pending": ("d93f0b", "Dispatcher: a cleanup closing summary is still to be posted (#348)"),
}
DEFAULT_LABEL_COLOR = "ededed"

EXIT_OK = 0
EXIT_NO = 1
EXIT_INVALID = 2
EXIT_GAVE_UP = 4

_EXIT_BY_RESULT = {
    "written": EXIT_OK,
    "already": EXIT_OK,
    "dry-run": EXIT_OK,
    "none": EXIT_OK,
    "posted": EXIT_OK,
    "pending": EXIT_NO,
    "failed": EXIT_NO,
    "stale": EXIT_NO,
    "missing": EXIT_NO,
    "gave-up": EXIT_GAVE_UP,
}

_UNBLOCK_LINE = re.compile(r"^\s*/unblock\s*$")
_PENDING_FIELDS = re.compile(r"head=(\S+)\spayload=([A-Za-z0-9+/=]*)")


@dataclass
class Settings:
    """Bounds read from the environment, with the defaults of the bash loops."""

    retry_delay: float = DEFAULT_RETRY_DELAY
    evidence_retry_max: int = DEFAULT_EVIDENCE_RETRY_MAX
    po_login: str = DEFAULT_PO_LOGIN
    sleep: Callable[[float], None] = time.sleep

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env
        try:
            delay = float(env.get("COMMENT_RETRY_DELAY") or DEFAULT_RETRY_DELAY)
        except ValueError:
            delay = DEFAULT_RETRY_DELAY
        try:
            retry_max = int(env.get("EVIDENCE_RETRY_MAX") or DEFAULT_EVIDENCE_RETRY_MAX)
        except ValueError:
            retry_max = DEFAULT_EVIDENCE_RETRY_MAX
        return cls(
            retry_delay=max(delay, 0.0),
            evidence_retry_max=max(retry_max, 1),
            po_login=(env.get("DECISION_BLOCKER_PO_LOGIN") or DEFAULT_PO_LOGIN).strip(),
        )


@dataclass
class Result:
    """One JSON line on stdout (ADR-014 → Decision → 2)."""

    operation: str
    result: str
    error: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        return _EXIT_BY_RESULT.get(self.result, EXIT_NO)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"ok": self.exit_code == EXIT_OK, "block": BLOCK, "operation": self.operation,
                                "result": self.result}  # fmt: skip
        data.update(self.fields)
        data["error"] = mask_tokens(self.error) if self.error else None
        return data


def log(message: str) -> None:
    """One line for the dispatcher log (stderr); tokens masked."""

    print(mask_tokens(message), file=sys.stderr)


# ── evidence-pending comment (scripts/lib/evidence-pending.sh) ────────────────


def evidence_pending_comment(item: str, head: str, payload: str) -> str:
    """The body ``evidence_pending_comment`` of ``scripts/lib/evidence-pending.sh`` prints, as posted."""

    encoded = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    visible = (
        f"Dispatcher: a required GitHub comment ({item}) could not be written. The dispatcher retries it on a "
        "later run; nothing is needed from you unless a blocker is posted."
    )
    # The bash callers posted `$(evidence_pending_comment ...)`, which drops the final newline.
    return f"{visible}\n\n<!-- hpw:dispatcher action=evidence-pending item={item} head={head or '-'} payload={encoded} -->"


def dispatcher_marker(text: str) -> str:
    return f"<!-- hpw:dispatcher {text} -->"


def evidence_gave_up_marker(item: str, ref: str) -> str:
    return dispatcher_marker(f"action=evidence-gave-up item={item} ref={ref}")


def evidence_failed_marker(item: str, ref: str) -> str:
    return dispatcher_marker(f"action=evidence-retry-failed item={item} ref={ref}")


# ── helpers ───────────────────────────────────────────────────────────────────


def _normal(body: str) -> str:
    """A comment body as compared: line endings unified, outer whitespace dropped."""

    return body.replace("\r\n", "\n").strip()


def _author(comment: Mapping[str, Any]) -> str:
    return str((comment.get("user") or {}).get("login") or "")


def _key(comment: Mapping[str, Any]) -> tuple[str, str, str]:
    return (str(comment.get("id") or ""), str(comment.get("created_at") or ""), _normal(str(comment.get("body") or "")))


def _own_matches(comments: list[dict[str, Any]], login: str, body: str) -> list[dict[str, Any]]:
    wanted = _normal(body)
    return [c for c in comments if _author(c) == login and _normal(str(c.get("body") or "")) == wanted]


def _comment_fields(comment: Mapping[str, Any] | None, attempts: int) -> dict[str, Any]:
    comment = comment or {}
    return {
        "url": comment.get("html_url") or comment.get("url"),
        "created_at": comment.get("created_at"),
        "attempts": attempts,
    }


# ── comment ───────────────────────────────────────────────────────────────────


def write_comment(
    gateway: Gateway,
    number: int,
    body: str,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    evidence_item: str | None = None,
    evidence_head: str | None = None,
    dry_run: bool = False,
    settings: Settings | None = None,
) -> Result:
    settings = settings or Settings.from_env()
    if dry_run:
        return Result("comment", "dry-run", fields={"number": number, **_comment_fields(None, 0)})

    outcome = _post_verified(gateway, number, body, attempts, settings, once=evidence_item is not None)
    if outcome.result in ("written", "already") or evidence_item is None:
        return outcome

    # Durable evidence (#348): leave the exact text as an evidence-pending comment.
    log(f"[WRITE] the {evidence_item} comment on #{number} could not be written ({outcome.error}); leaving its evidence-pending copy")
    pending = evidence_pending_comment(evidence_item, evidence_head or "-", body)
    copy = _post_verified(gateway, number, pending, EVIDENCE_PENDING_ATTEMPTS, settings, once=True)
    if copy.result in ("written", "already"):
        return Result(
            "comment", "pending", error=outcome.error,
            fields={**_comment_fields(None, outcome.fields.get("attempts", attempts)),
                    "pending_url": copy.fields.get("url"), "pending_created_at": copy.fields.get("created_at")},
        )  # fmt: skip
    return Result(
        "comment", "failed",
        error=f"write evidence-pending: {copy.error}",
        fields=_comment_fields(None, outcome.fields.get("attempts", attempts)),
    )  # fmt: skip


def _post_verified(
    gateway: Gateway, number: int, body: str, attempts: int, settings: Settings, *, once: bool
) -> Result:
    """Post ``body`` on ``number`` and read it back, at most ``attempts`` times; never twice.

    With ``once``, an identical own comment that already exists is ``already``.
    """

    snapshot: set[tuple[str, str, str]] | None = None
    login: str | None = None
    error = "write comment: not attempted"
    used = 0
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            settings.sleep(settings.retry_delay)
        used = attempt
        try:
            if login is None:
                login = gateway.viewer_login()
            if snapshot is None:
                existing = _own_matches(gateway.list_comments(number), login, body)
                if once and existing:
                    return Result("comment", "already", fields=_comment_fields(existing[-1], 0))
                snapshot = {_key(c) for c in existing}
            else:
                # A write whose response was lost may still be on GitHub.
                found = [c for c in _own_matches(gateway.list_comments(number), login, body) if _key(c) not in snapshot]
                if found:
                    return Result("comment", "written", fields=_comment_fields(found[-1], attempt - 1))
        except GatewayError as exc:
            error = str(exc)
            continue
        try:
            response = gateway.post_comment(number, body)
        except GatewayError as exc:
            error = str(exc)
            continue
        try:
            found = [c for c in _own_matches(gateway.list_comments(number), login, body) if _key(c) not in snapshot]
        except GatewayError as exc:
            error = f"read back: {exc}"
            continue
        if found:
            comment = dict(found[-1])
            if not comment.get("html_url") and response.get("html_url"):
                comment["html_url"] = response["html_url"]
            return Result("comment", "written", fields=_comment_fields(comment, attempt))
        error = f"read back: no comment of {login} with the sent text on #{number}"
    return Result("comment", "failed", error=error, fields=_comment_fields(None, used))


# ── board status ──────────────────────────────────────────────────────────────


def write_board_status(
    gateway: Gateway,
    issue: int,
    status: str,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    dry_run: bool = False,
    settings: Settings | None = None,
) -> Result:
    settings = settings or Settings.from_env()
    if dry_run:
        return Result("board-status", "dry-run", fields={"issue": issue, "before": None, "after": status, "attempts": 0,
                                                         "sent": False})  # fmt: skip

    before: str | None = None
    after: str | None = None
    wrote = False
    # A status mutation was sent, whatever its response: a failed result with
    # ``sent`` may still have changed the board (issue #432).
    sent = False
    error = "write status: not attempted"
    used = 0
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            settings.sleep(settings.retry_delay)
        used = attempt
        try:
            item = gateway.board_item(issue)
        except GatewayError as exc:
            error = str(exc)
            continue
        if item.item_id is None:
            error = f"find item: issue #{issue} is not on the project board"
            break
        option = item.options.get(status)
        if option is None:
            error = f"resolve status: '{status}' is not a Status option (options: {', '.join(item.options)})"
            break
        if not wrote and before is None:
            before = item.status
        after = item.status
        if item.status == status:
            result = "written" if wrote else "already"
            return Result("board-status", result, fields={"issue": issue, "before": before, "after": after,
                                                          "attempts": attempt if wrote else 0, "sent": sent})  # fmt: skip
        sent = True
        try:
            gateway.set_board_status(item, option)
            wrote = True
        except GatewayError as exc:
            error = str(exc)
            continue
        try:
            after = gateway.board_item(issue).status
        except GatewayError as exc:
            error = f"read back: {exc}"
            continue
        if after == status:
            return Result("board-status", "written", fields={"issue": issue, "before": before, "after": after,
                                                             "attempts": attempt, "sent": sent})  # fmt: skip
        error = f"read back: wrote '{status}' for #{issue} but the board shows '{after or '(none)'}'"
    return Result("board-status", "failed", error=error, fields={"issue": issue, "before": before, "after": after,
                                                                 "attempts": used, "sent": sent})  # fmt: skip


# ── label ─────────────────────────────────────────────────────────────────────


def write_label(
    gateway: Gateway,
    number: int,
    name: str,
    *,
    add: bool,
    attempts: int = DEFAULT_ATTEMPTS,
    dry_run: bool = False,
    settings: Settings | None = None,
) -> Result:
    settings = settings or Settings.from_env()
    base = {"number": number, "label": name, "action": "add" if add else "remove"}
    if dry_run:
        return Result("label", "dry-run", fields={**base, "attempts": 0})

    wrote = False
    created = False
    error = "write label: not attempted"
    used = 0
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            settings.sleep(settings.retry_delay)
        used = attempt
        try:
            present = name in gateway.issue_labels(number)
        except GatewayError as exc:
            error = str(exc)
            continue
        if present == add:
            result = "written" if wrote else "already"
            return Result("label", result, fields={**base, "attempts": attempt if wrote else 0})
        try:
            if add:
                gateway.add_label(number, name)
            else:
                gateway.remove_label(number, name)
            wrote = True
        except GatewayError as exc:
            error = str(exc)
            if add and exc.not_found and not created:
                # Adding a label the repository does not have is a 404: create it once.
                color, description = KNOWN_LABELS.get(name, (DEFAULT_LABEL_COLOR, ""))
                try:
                    gateway.create_label(name, color, description)
                except GatewayError as create_error:
                    error = f"{exc}; {create_error}"
                created = True
            continue
        try:
            present = name in gateway.issue_labels(number)
        except GatewayError as exc:
            error = f"read back: {exc}"
            continue
        if present == add:
            return Result("label", "written", fields={**base, "attempts": attempt})
        state = "does not carry" if add else "still carries"
        error = f"read back: #{number} {state} the label {name}"
    return Result("label", "failed", error=error, fields={**base, "attempts": used})


# ── retry-pending ─────────────────────────────────────────────────────────────


def _po_unblocked_after(comments: list[dict[str, Any]], po_login: str, since: str) -> bool:
    return any(
        _author(c) == po_login
        and str(c.get("created_at") or "") > since
        and any(_UNBLOCK_LINE.match(line) for line in str(c.get("body") or "").split("\n"))
        for c in comments
    )


def _gave_up(comments: list[dict[str, Any]], login: str, marker: str, po_login: str) -> bool:
    """The give-up marker holds: the dispatcher's own newest marker comment has no later PO ``/unblock``."""

    held = [c for c in comments if _author(c) == login and marker in str(c.get("body") or "")]
    if not held:
        return False
    return not _po_unblocked_after(comments, po_login, str(held[-1].get("created_at") or ""))


def _failure_count(own: list[dict[str, Any]], item: str, ref: str) -> int:
    """Failed runs recorded since the last give-up marker (``evidence_failure_count``)."""

    gave = evidence_gave_up_marker(item, ref)
    failed = evidence_failed_marker(item, ref)
    since = max((str(c.get("created_at") or "") for c in own if gave in str(c.get("body") or "")), default="")
    return sum(1 for c in own if failed in str(c.get("body") or "") and str(c.get("created_at") or "") > since)


def retry_pending(
    gateway: Gateway,
    target: int,
    item: str,
    ref: str,
    story: int,
    *,
    expected_head: str | None = None,
    dry_run: bool = False,
    settings: Settings | None = None,
) -> Result:
    """Post the pending evidence of ``item`` on ``target`` once (``evidence_retry``, #348).

    On a failed write it records one failed run on ``story`` (``evidence_record_failure``):
    ``missing`` while runs remain, ``gave-up`` with ``blocker_due`` on the run that reaches
    ``EVIDENCE_RETRY_MAX``. The give-up blocker itself is posted by the bash caller.
    """

    settings = settings or Settings.from_env()
    base: dict[str, Any] = {"target": target, "item": item, "ref": ref, "story": story}
    try:
        login = gateway.viewer_login()
        own = [c for c in gateway.list_comments(target) if _author(c) == login]
    except GatewayError as exc:
        return Result("retry-pending", "missing", error=str(exc), fields={**base, "reason": "unreadable"})

    marker = f"action=evidence-pending item={item} "
    pending_comments = [c for c in own if marker in str(c.get("body") or "")]
    if not pending_comments:
        return Result("retry-pending", "none", fields=base)
    pending = pending_comments[-1]
    pending_at = str(pending.get("created_at") or "")
    base["pending_at"] = pending_at
    match = _PENDING_FIELDS.search(str(pending.get("body") or ""))
    if match is None:
        return Result("retry-pending", "none", fields={**base, "reason": "malformed"})
    head, encoded = match.group(1), match.group(2)
    base["head"] = head
    if expected_head and head.lower() != expected_head.lower():
        return Result("retry-pending", "stale", fields={**base, "reason": "other-head"})
    try:
        payload = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, ValueError):
        payload = ""
    if not payload:
        return Result("retry-pending", "none", fields={**base, "reason": "undecodable"})

    wanted = payload.strip()
    if any("action=evidence-pending" not in str(c.get("body") or "") and wanted in str(c.get("body") or "").strip()
           for c in own):  # fmt: skip
        return Result("retry-pending", "posted", fields={**base, "attempts": 0, "reason": "earlier"})

    try:
        story_comments = gateway.list_comments(story) if story != target else gateway.list_comments(target)
    except GatewayError as exc:
        return Result("retry-pending", "missing", error=str(exc), fields={**base, "reason": "unreadable"})
    if _gave_up(story_comments, login, evidence_gave_up_marker(item, ref), settings.po_login):
        return Result("retry-pending", "gave-up", fields={**base, "reason": "stopped", "blocker_due": False})

    if dry_run:
        return Result("retry-pending", "dry-run", fields={**base, "payload": payload})

    posted = _post_verified(gateway, target, payload, DEFAULT_ATTEMPTS, settings, once=False)
    if posted.result == "written":
        return Result("retry-pending", "posted", fields={**base, "attempts": posted.fields.get("attempts"), "reason": "now"})

    # One failed run: count it on the story with the dispatcher's own marker.
    story_own = [c for c in story_comments if _author(c) == login]
    run = _failure_count(story_own, item, ref) + 1
    what = f"the {item} evidence on #{target}"
    failure_body = (
        f"Dispatcher: {what} could not be written to GitHub (failed run {run} of {settings.evidence_retry_max}). "
        f"It is retried on the next run.\n\n{evidence_failed_marker(item, ref)}"
    )
    recorded = _post_verified(gateway, story, failure_body, DEFAULT_ATTEMPTS, settings, once=False)
    fields = {**base, "run": run, "failure_recorded": recorded.result == "written",
              "blocker_due": run >= settings.evidence_retry_max, "payload": payload,
              "attempts": posted.fields.get("attempts")}  # fmt: skip
    error = posted.error if recorded.result == "written" else f"{posted.error}; record failure: {recorded.error}"
    if run >= settings.evidence_retry_max:
        return Result("retry-pending", "gave-up", error=error, fields={**fields, "reason": "limit"})
    return Result("retry-pending", "missing", error=error, fields={**fields, "reason": "write-failed"})
