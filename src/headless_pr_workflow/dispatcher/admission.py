"""The rules that decide whether and how a story may start (issue #557, Epic #519, ADR-014).

One implementation of each rule, called by the scheduler (``scheduler.choose_next``), the new-work pre-flight of
``scripts/dispatcher-invoke.sh`` and the resume pass through ``hpw dispatcher admission``, and by the poll through
``scripts/lib/in-flight.sh``. Pure functions: no GitHub call, no clock.

* executable type: ``classify_executable_type`` (the ``type:`` label wins; the native issue type is the fallback)
* executor routing: ``executor_labels`` (every ``executor:`` label, in order; the first one routes, a review-loop
  story has exactly one)
* WIP limit and file overlap: ``wip_reached`` and ``overlapping_paths``
* limits loading: ``scheduler.load_limits`` (the rule lives in the scheduler; ``hpw dispatcher admission limits``
  is its entry for Bash)
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .facts import KNOWN_TYPES, STORY_TYPES, files_affected

__all__ = ["check_start", "classify_executable_type", "executor_labels", "known_type", "overlapping_paths", "wip_reached"]


def known_type(labels: Iterable[str]) -> str | None:
    """The first ``type:`` label that names an Epic, Feature, Story, Task or Bug."""

    kinds = [label.split(":", 1)[1] for label in labels if label.startswith("type:")]
    return next((k for k in kinds if k in KNOWN_TYPES), None)


def classify_executable_type(labels: Iterable[str], native: str | None = "") -> tuple[bool, str]:
    """Whether an issue is an executable Story, Task or Bug, and the text that says why.

    The ``type:`` label wins; the native GitHub issue type is only a fallback when there is no ``type:`` label.
    More than one ``type:`` label is a conflict and not executable. The title and the ``executor:`` label are never
    read.
    """

    type_labels = [label for label in labels if label.startswith("type:")]
    if len(type_labels) > 1:
        return False, f"unsupported type: conflicting type labels {' '.join(type_labels)}"
    if type_labels:
        kind = type_labels[0][len("type:"):]
        if kind in STORY_TYPES:
            return True, type_labels[0]
        if kind in ("epic", "feature"):
            return False, f"detected type: {type_labels[0]}"
        return False, f"unsupported type: {type_labels[0]}"
    if native:
        if native.lower() in STORY_TYPES:
            return True, f"native issue type '{native}'"
        return False, f"unsupported type: native issue type '{native}'"
    return False, "type missing: no type: label or native issue type"


def executor_labels(labels: Iterable[str]) -> list[str]:
    """The suffix of every ``executor:`` label, in label order."""

    return [label[len("executor:"):] for label in labels if label.startswith("executor:")]


def wip_reached(in_implementation: int, limit: int) -> bool:
    """No new /implement once the stories in implementation, the target not among them, reach the limit."""

    return in_implementation >= limit


def overlapping_paths(mine: Sequence[str], theirs: Sequence[str]) -> list[str]:
    """The paths in ``mine`` that ``theirs`` also lists, in the order of ``mine``."""

    return [path for path in mine if path in theirs]


def check_start(target_body: str, active: Sequence[Mapping[str, Any]], limit: int) -> dict[str, Any]:
    """The pre-flight of one story against the stories with work in implementation (``active``, target left out).

    ``active`` items carry ``number``, ``title`` and ``body``. ``result`` is ``pass``, ``wip-limit`` or
    ``file-overlap``; a missing ``## Files affected`` section on the target skips the overlap check.
    """

    out: dict[str, Any] = {"count": len(active), "limit": limit, "target_files": None, "overlaps": []}
    if wip_reached(len(active), limit):
        return {**out, "result": "wip-limit"}
    mine = files_affected(target_body)
    out["target_files"] = mine
    for item in active:
        theirs = files_affected(str(item.get("body") or ""))
        if mine and theirs:
            out["overlaps"] += [{"path": path, "number": item.get("number"), "title": item.get("title") or ""}
                                for path in overlapping_paths(mine, theirs)]  # fmt: skip
    return {**out, "result": "file-overlap" if out["overlaps"] else "pass"}
