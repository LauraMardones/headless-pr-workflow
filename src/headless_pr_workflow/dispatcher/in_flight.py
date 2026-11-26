"""In-flight rule: ``hpw dispatcher in-flight plan`` (issue #475, ADR-010, ADR-014).

Decides which resume step each open Story, Task or Bug is in, and the two lists
for ``hpw dispatcher scheduler next``, from facts the caller has read: the
linked PR, its merge gates, the comments of the story and the PR. No board
status is read. ``plan_in_flight`` is pure: it gets no gateway, uses no model
and writes nothing. ``scripts/lib/in-flight.sh`` reads the facts and calls it,
for ``scripts/dispatcher-poll.sh`` and the resume pass of
``scripts/dispatcher-invoke.sh``.

Markers count only on comments of the given accounts, as in Block 2
(``marker.py``): the dispatcher's own markers on the dispatcher's account, the
findings marker on the bookkeeping job's account, ``/unblock`` on the PO's.

``hpw dispatcher in-flight linked`` is the linked-PR rule (``facts.linked_pr``,
issue #556) for the Bash callers: ``scripts/lib/in-flight.sh`` and the
``find_linked_pr_json`` / ``find_merged_linked_pr`` shims of the invoke script.

Result words and exit codes: ``read`` (0), ``unreadable`` (3: the input file
cannot be read or is not a list of rows).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence, cast

from .facts import _Facts, linked_pr
from .gateway import Gateway
from .marker import decide_holds, dispatcher_marker
from .session_blocker import open_session_finding
from .status import other_blocker_open
from .write import Result, _author, _po_unblocked_after

BLOCK = "in-flight"
OPERATION = "plan"

# The marker the codex-review-bookkeeping job writes when it records findings (review_policy.py).
FINDINGS_MARKER = "<!-- hpw:codex-review-bookkeeping action=move-in-implementation head="
STEPS = ("merge", "finish", "fix_round", "hand_off", "review", "continue")
DEFAULT_CONTINUE_AFTER_MINUTES = 60  # CONTINUE_DRAFT_AFTER_MINUTES default (issue #578)
_DECLARED_BY_DISPATCHER = re.compile(r"^[ \t]*Declared by:[ \t]*dispatcher[ \t]*$", re.M | re.I)
_TYPE_EXTERNAL = re.compile(r"^[ \t]*Type:\s*external\b", re.M | re.I)


def hand_off_pending_prefix(pr: int) -> str:
    """``hand_off_pending_prefix`` of ``scripts/dispatcher-invoke.sh``: the pending marker up to ``head=``."""

    return dispatcher_marker(f"action=hand-off-pending pr={pr} head=").split("head=")[0]


def hand_off_done_marker(pr: int) -> str:
    return dispatcher_marker(f"action=hand-off-done pr={pr}")


def findings_on_head(pr_comments: Sequence[Mapping[str, Any]], head: str, bookkeeping: str) -> bool:
    """The bookkeeping job's findings marker names ``head`` (``fix_round_due`` of the invoke script)."""

    wanted = f"{FINDINGS_MARKER}{head.lower()} "
    return bool(head and bookkeeping) and any(
        _author(c) == bookkeeping and wanted in str(c.get("body") or "").lower() for c in pr_comments
    )


def hand_off_pending(story_comments: list[dict[str, Any]], pr: int, dispatcher: str, po: str) -> bool:
    """The newest own hand-off-pending marker for ``pr`` holds: no later PO ``/unblock``, no later done marker."""

    if not dispatcher:
        return False
    prefix, done = hand_off_pending_prefix(pr), hand_off_done_marker(pr)
    own = [c for c in story_comments if _author(c) == dispatcher]
    held = [c for c in own if prefix in str(c.get("body") or "")]
    if not held:
        return False
    since = max(str(c.get("created_at") or "") for c in held)
    if _po_unblocked_after(story_comments, po, since):
        return False
    return not any(str(c.get("created_at") or "") > since and done in str(c.get("body") or "") for c in own)


def continue_attempt_marker(pr: int, head: str) -> str:
    """``continue_attempt_marker`` of ``scripts/dispatcher-invoke.sh``: one attempt per PR head (issue #578)."""

    return dispatcher_marker(f"action=continue-attempt pr={pr} head={head}")


def _parse_time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def failed_session_unblocked(story_comments: list[dict[str, Any]], dispatcher: str, po: str) -> bool:
    """The newest dispatcher ``Type: external`` declaration is lifted by a standalone PO ``/unblock`` posted after it."""

    found = [c for c in story_comments if dispatcher and _author(c) == dispatcher
             and "## Blocked Declaration" in str(c.get("body") or "")
             and _TYPE_EXTERNAL.search(str(c.get("body") or ""))
             and _DECLARED_BY_DISPATCHER.search(str(c.get("body") or ""))]  # fmt: skip
    if not found:
        return False
    return _po_unblocked_after(story_comments, po, max(str(c.get("created_at") or "") for c in found))


def continue_reason(row: Mapping[str, Any], *, dispatcher: str, po: str, trust: _Facts,
                    after_minutes: int, now: datetime) -> str | None:
    """``"failed"`` or ``"silent"`` when the draft PR of ``row`` is continued, else ``None`` (issue #578, E10).

    Failed session: the newest dispatcher ``Type: external`` declaration is lifted by the PO's ``/unblock``.
    Silent stop: the newest ``pytest`` check run on the head completed with success at least ``after_minutes``
    ago (``green_completed_at``). Both need no other open blocker (decision or not) and no ``continue-attempt``
    marker for this PR head that no later ``/unblock`` lifted.
    """

    pr, comments = row.get("pr") or {}, row.get("story_comments")
    head = str(pr.get("head") or "")
    if not dispatcher or comments is None or not head or pr.get("number") is None:
        return None
    # An executor the Actions dispatcher cannot start (executor:codex has no provider row) stays with its own worker.
    if any(not str(label).startswith("claude-code-") for label in row.get("executors") or []):
        return None
    comments = list(comments)
    if trust.decision_blocker(comments)["state"] == "open" or other_blocker_open(trust, comments, po_unblock=True):
        return None
    if decide_holds(comments, dispatcher, continue_attempt_marker(int(pr["number"]), head), po_login=po)[0] == "holds":
        return None
    if failed_session_unblocked(comments, dispatcher, po):
        return "failed"
    green = _parse_time(row.get("green_completed_at"))
    if green is not None and now - green >= timedelta(minutes=after_minutes):
        return "silent"
    return None


def _entry(row: Mapping[str, Any]) -> dict[str, Any]:
    pr = row.get("pr") or {}
    return {"number": int(row["number"]), "title": str(row.get("title") or ""),
            "executors": list(row.get("executors") or []),
            "pr": int(pr["number"]) if pr.get("number") is not None else None,
            "head": pr.get("head") or None}  # fmt: skip


def plan_in_flight(rows: Sequence[Mapping[str, Any]], *, dispatcher: str, bookkeeping: str, po: str,
                   continue_after_minutes: int = DEFAULT_CONTINUE_AFTER_MINUTES,
                   now: datetime | None = None) -> dict[str, Any]:
    """The resume steps and the scheduler lists. Pure: the same rows give the same plan.

    A row: ``number``, ``title``, ``executors``, ``review_loop`` (one ``executor:`` label with a
    ``CODEX_REVIEW_LOOP_ROUTING`` row), ``pr`` (the linked open PR ``{"number", "draft", "head"}``, or
    ``None``), ``merged`` (no open linked PR, and a merged linked PR among the closed PRs read),
    ``gates_pass`` (the merge gates of the non-draft PR pass on its head, ignoring mergeability), and
    ``story_comments`` / ``pr_comments`` (``None`` when not read), and ``green_completed_at`` (the
    ``completed_at`` of the newest ``pytest`` check run on the PR head when it succeeded, else ``None``).
    An empty login finds no marker of it.

    A draft PR is in ``in_implementation`` only when a session is about to work on it: a fix round is due, or
    the ``continue`` step lists it (issue #578). Every other draft PR is in ``in_flight``: no WIP place, no
    file-overlap place, never started as new work.
    """

    now = now or datetime.now(timezone.utc)

    trust = _Facts(cast(Gateway, None), {"DECISION_BLOCKER_PO_LOGIN": po})
    steps: dict[str, list[dict[str, Any]]] = {name: [] for name in STEPS}
    implementing: set[int] = set()
    flight: set[int] = set()
    for row in sorted(rows, key=lambda r: int(r["number"])):
        number = int(row["number"])
        pr = row.get("pr")
        story_comments = row.get("story_comments")
        if not pr:
            if row.get("merged"):
                steps["finish"].append(_entry(row))
            elif story_comments is not None and other_blocker_open(trust, list(story_comments), po_unblock=True):
                flight.add(number)  # an open non-decision blocker: never started again until /unblock
            elif story_comments is not None and open_session_finding(trust, list(story_comments)) is not None:
                flight.add(number)  # a finding with no PR (issue #507): never started again until /unblock
            continue
        draft = bool(pr.get("draft"))
        head = str(pr.get("head") or "")
        if not draft and row.get("gates_pass"):
            # A ready PR that passes the current-head gates is in the merge step
            # even when an older fix or hand-off marker is still present.
            flight.add(number)
            steps["merge"].append(_entry(row))
            continue
        pr_comments = row.get("pr_comments")
        if row.get("review_loop") and dispatcher and story_comments is not None and pr_comments is not None:
            if findings_on_head(pr_comments, head, bookkeeping):
                steps["fix_round"].append(_entry(row))
                implementing.add(number)
                continue
            if not draft and hand_off_pending(list(story_comments), int(pr["number"]), dispatcher, po):
                steps["hand_off"].append(_entry(row))
                flight.add(number)  # a ready PR: in flight, not counted for the WIP limit (AC 8)
                continue
        if draft:
            reason = continue_reason(row, dispatcher=dispatcher, po=po, trust=trust,
                                     after_minutes=continue_after_minutes, now=now)
            if reason:
                steps["continue"].append({**_entry(row), "reason": reason, "minutes": continue_after_minutes})
                implementing.add(number)
            else:
                flight.add(number)  # no session at work: no WIP place, never started as new work (issue #578)
            continue
        flight.add(number)
        steps["review"].append(_entry(row))
    return {**steps, "in_implementation": sorted(implementing), "in_flight": sorted(flight - implementing)}


def plan(rows: Any, *, dispatcher: str, bookkeeping: str, po: str,
         continue_after_minutes: int = DEFAULT_CONTINUE_AFTER_MINUTES, now: datetime | None = None) -> Result:
    if not isinstance(rows, list) or not all(isinstance(r, dict) and "number" in r for r in rows):
        return Result(OPERATION, "unreadable", error="input: not a list of rows with a number", block=BLOCK)
    try:
        fields = plan_in_flight(rows, dispatcher=dispatcher, bookkeeping=bookkeeping, po=po,
                                continue_after_minutes=continue_after_minutes, now=now)
    except (TypeError, ValueError, KeyError) as exc:
        return Result(OPERATION, "unreadable", error=f"input: {exc}", block=BLOCK)
    return Result(OPERATION, "read", fields=fields, block=BLOCK)


def linked(data: Any) -> Result:
    """The linked open PR and the merged linked PR of each issue, from PR lists the caller has read.

    Input: ``{"issues": [n, ...], "open": [PR, ...], "closed": [PR, ...]}`` (``closed`` may be left out).
    Fields: ``linked`` maps each issue number (a string) to its linked open PR as GitHub returned it, or
    ``None``; ``merged`` maps it to the number of the first merged linked PR among ``closed``, or ``None``.
    """

    operation = "linked"
    try:
        issues = [int(n) for n in data["issues"]]
        open_prs, closed = data["open"], data.get("closed") or []
        if not isinstance(open_prs, list) or not isinstance(closed, list) \
                or not all(isinstance(p, dict) for p in [*open_prs, *closed]):
            raise ValueError("open and closed must be lists of PRs")
    except (TypeError, ValueError, KeyError) as exc:
        return Result(operation, "unreadable", error=f"input: {exc}", block=BLOCK)
    found = {str(n): linked_pr(open_prs, n) for n in issues}
    merged = {str(n): (lambda pr: int(pr["number"]) if pr else None)(linked_pr(closed, n, merged=True))
              for n in issues}  # fmt: skip
    return Result(operation, "read", fields={"linked": found, "merged": merged}, block=BLOCK)
