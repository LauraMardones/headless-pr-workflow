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

Result words and exit codes: ``read`` (0), ``unreadable`` (3: the input file
cannot be read or is not a list of rows).
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence, cast

from .facts import _Facts
from .gateway import Gateway
from .marker import dispatcher_marker
from .status import other_blocker_open
from .write import Result, _author, _po_unblocked_after

BLOCK = "in-flight"
OPERATION = "plan"

# The marker the codex-review-bookkeeping job writes when it records findings (review_policy.py).
FINDINGS_MARKER = "<!-- hpw:codex-review-bookkeeping action=move-in-implementation head="
STEPS = ("merge", "finish", "fix_round", "hand_off", "review")


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


def _entry(row: Mapping[str, Any]) -> dict[str, Any]:
    pr = row.get("pr") or {}
    return {"number": int(row["number"]), "title": str(row.get("title") or ""),
            "executors": list(row.get("executors") or []),
            "pr": int(pr["number"]) if pr.get("number") is not None else None,
            "head": pr.get("head") or None}  # fmt: skip


def plan_in_flight(rows: Sequence[Mapping[str, Any]], *, dispatcher: str, bookkeeping: str, po: str) -> dict[str, Any]:
    """The resume steps and the scheduler lists. Pure: the same rows give the same plan.

    A row: ``number``, ``title``, ``executors``, ``review_loop`` (one ``executor:`` label with a
    ``CODEX_REVIEW_LOOP_ROUTING`` row), ``pr`` (the linked open PR ``{"number", "draft", "head"}``, or
    ``None``), ``merged`` (no open linked PR, and a merged linked PR among the closed PRs read),
    ``gates_pass`` (the merge gates of the non-draft PR pass on its head, ignoring mergeability), and
    ``story_comments`` / ``pr_comments`` (``None`` when not read). An empty login finds no marker of it.
    """

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
            elif story_comments is not None and other_blocker_open(trust, list(story_comments)):
                flight.add(number)  # an open non-decision blocker: never started again until /unblock
            continue
        draft = bool(pr.get("draft"))
        head = str(pr.get("head") or "")
        if draft:
            implementing.add(number)
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
            continue
        flight.add(number)
        steps["merge" if row.get("gates_pass") else "review"].append(_entry(row))
    return {**steps, "in_implementation": sorted(implementing), "in_flight": sorted(flight - implementing)}


def plan(rows: Any, *, dispatcher: str, bookkeeping: str, po: str) -> Result:
    if not isinstance(rows, list) or not all(isinstance(r, dict) and "number" in r for r in rows):
        return Result(OPERATION, "unreadable", error="input: not a list of rows with a number", block=BLOCK)
    try:
        fields = plan_in_flight(rows, dispatcher=dispatcher, bookkeeping=bookkeeping, po=po)
    except (TypeError, ValueError, KeyError) as exc:
        return Result(OPERATION, "unreadable", error=f"input: {exc}", block=BLOCK)
    return Result(OPERATION, "read", fields=fields, block=BLOCK)
