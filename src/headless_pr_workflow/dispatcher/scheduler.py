"""Scheduler rule: ``hpw dispatcher scheduler next`` (issue #473, ADR-010, ADR-014).

Chooses the dispatcher's next new-work action from the facts of ``hpw dispatcher
facts read``: which Epic, Feature or story to refine, or which ready story to
start implementing (steps 2 to 6 of #460 → "Order of actions in a run"; step 1,
work in flight, stays with the dispatcher). ``choose_next`` is pure: it gets no
gateway and writes nothing. ``next_action`` reads the facts with ``read_facts``
unless they are given.

Result words and exit codes: ``refine`` and ``implement`` (0), ``idle`` (1:
nothing to do), ``unreadable`` (3: the facts or the limits cannot be used).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .facts import STORY_TYPES, read_facts
from .gateway import Gateway
from .write import EXIT_NO, EXIT_OK, EXIT_UNREADABLE, Result, log

BLOCK = "scheduler"
OPERATION = "next"

LIMIT_NAMES = ("WIP_LIMIT_IMPLEMENTATION", "READY_BUFFER_MIN", "READY_BUFFER_MAX")

_EXIT_BY_RESULT = {"refine": EXIT_OK, "implement": EXIT_OK, "idle": EXIT_NO, "unreadable": EXIT_UNREADABLE}
_PRIO = re.compile(r"^prio:P(\d+)$")
_PREFIX = re.compile(r"^F(\d+)$")
_NO_RANK = 1_000_000


class InvalidLimits(Exception):
    """``str(exc)`` names each variable that cannot be used and its value."""


@dataclass(frozen=True)
class Limits:
    wip: int
    buffer_min: int
    buffer_max: int

    def to_dict(self) -> dict[str, int]:
        return {"WIP_LIMIT_IMPLEMENTATION": self.wip, "READY_BUFFER_MIN": self.buffer_min,
                "READY_BUFFER_MAX": self.buffer_max}  # fmt: skip


def load_limits(env: Mapping[str, str]) -> Limits:
    """The three limits, checked by the rules of ``load_limits`` in scripts/lib/limits.sh."""

    problems: list[str] = []
    values: dict[str, int] = {}
    for name in LIMIT_NAMES:
        raw = env.get(name)
        if not raw:
            shown = "unset" if raw is None else '""'
            problems.append(f"{name} is not set or is empty (value: {shown})")
        elif not re.fullmatch(r"[0-9]+", raw):
            problems.append(f'{name} is not a whole number (value: "{raw}")')
        elif name == "WIP_LIMIT_IMPLEMENTATION" and int(raw) < 1:
            problems.append(f'{name} must be 1 or more (value: "{raw}")')
        else:
            values[name] = int(raw)
    if problems:
        raise InvalidLimits("; ".join(problems))
    if values["READY_BUFFER_MIN"] > values["READY_BUFFER_MAX"]:
        raise InvalidLimits(f"READY_BUFFER_MIN ({values['READY_BUFFER_MIN']}) is greater than "
                            f"READY_BUFFER_MAX ({values['READY_BUFFER_MAX']})")  # fmt: skip
    return Limits(values["WIP_LIMIT_IMPLEMENTATION"], values["READY_BUFFER_MIN"], values["READY_BUFFER_MAX"])


@dataclass
class Decision:
    action: dict[str, Any] | None
    skipped: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def result(self) -> str:
        return self.action["kind"] if self.action else "idle"


@dataclass
class SchedulerResult(Result):
    @property
    def exit_code(self) -> int:
        return _EXIT_BY_RESULT.get(self.result, EXIT_NO)


class _Candidate:
    def __init__(self, entry: dict[str, Any], kind: str, epic: int, step: int, group: int, reason: str,
                 key: tuple) -> None:  # fmt: skip
        self.entry, self.kind, self.epic, self.step, self.group, self.reason, self.key = (
            entry, kind, epic, step, group, reason, key)  # fmt: skip

    def action(self) -> dict[str, Any]:
        return {"kind": self.kind, "issue": self.entry["number"], "type": self.entry["type"], "epic": self.epic,
                "step": self.step, "reason": self.reason}  # fmt: skip


def _prio_rank(label: Any) -> int:
    found = _PRIO.match(str(label or ""))
    return int(found.group(1)) if found else _NO_RANK


def _prefix_rank(prefix: Any) -> int:
    found = _PREFIX.match(str(prefix or "").upper())
    return int(found.group(1)) if found else _NO_RANK


def _refine_gate(entry: dict[str, Any], spec: dict[str, Any]) -> str | None:
    """``unblocked`` for a resume, a skip reason, or ``None`` when the rule may refine the issue."""

    refinement = entry.get("refinement") or {}
    blocker = entry.get("decision_blocker") or {}
    if blocker.get("state") == "open":
        return "open-decision-blocker"
    summary_at = str(refinement.get("created_at") or "")
    unblocked_at = str(blocker.get("unblocked_at") or "") if blocker.get("state") == "unblocked" else ""
    if unblocked_at and summary_at <= unblocked_at:
        return "unblocked"
    if (summary_at and refinement.get("refine_check") != "pass" and summary_at > str(spec.get("committed_at") or "")
            and summary_at > unblocked_at):  # fmt: skip
        return "last-refinement-failed"
    return None


def choose_next(facts: Mapping[str, Any], *, in_implementation: Sequence[int], limits: Limits) -> Decision:
    """The next action from the facts (the ``read`` output of ``hpw dispatcher facts read``). No GitHub call."""

    active = set(in_implementation)
    epics = {int(e["number"]): e for e in facts.get("epics") or []}
    issues = {int(i["number"]): i for i in facts.get("issues") or []}
    stories = [i for i in issues.values() if i.get("type") in STORY_TYPES]
    skipped: dict[int, str] = {}
    notes: list[str] = []
    candidates: list[_Candidate] = []

    def skip(number: int, reason: str) -> None:
        skipped.setdefault(number, reason)

    def epic_key(epic: dict[str, Any]) -> tuple:
        spec = epic.get("spec") or {}
        merged = spec.get("state") == "merged"
        return _prio_rank(epic.get("prio")), (0, str(spec.get("committed_at") or "")) if merged else (1, "")

    def key(entry: dict[str, Any], epic: dict[str, Any] | None) -> tuple:
        if entry.get("type") == "feature":
            prefix = entry.get("criterion_prefix")
        else:
            prefix = (issues.get(entry.get("feature") or 0) or {}).get("criterion_prefix")
        outer = epic_key(epic) if epic else (_NO_RANK, (1, ""))
        return (*outer, _prio_rank(entry.get("prio")), _prefix_rank(prefix), int(entry["number"]))

    def add(entry: dict[str, Any], kind: str, epic: int, step: int, group: int, reason: str, sort_key: tuple) -> None:
        candidates.append(_Candidate(entry, kind, epic, step, group, reason, sort_key))

    # ── Epics: steps 2 and 3 ─────────────────────────────────────────────────
    ready_epics: set[int] = set()  # Epics whose Features and stories may be refined
    for number, epic in sorted(epics.items()):
        spec = epic.get("spec") or {}
        if spec.get("state") != "merged":
            skip(number, f"no-merged-spec: {spec.get('reason')}")
            continue
        record = epic.get("record") or {}
        if record.get("state") == "unreadable":
            skip(number, str(record.get("reason") or "unreadable: record"))
            continue
        if record.get("state") == "current":
            ready_epics.add(number)
        if epic.get("refinement") is None or epic.get("decision_blocker") is None:
            skip(number, "unreadable: comments")
            continue
        entry = {**epic, "type": "epic"}
        gate = _refine_gate(entry, spec)
        if gate == "unblocked":
            add(entry, "refine", number, 2, 3, "unblocked", key(entry, epic))
        elif record.get("state") == "current":
            continue
        elif gate:
            skip(number, gate)
        elif record.get("state") in ("stale", "invalid"):
            add(entry, "refine", number, 2, 0, "scope-change", key(entry, epic))
        elif record.get("state") == "missing":
            add(entry, "refine", number, 3, 0, "new-spec", key(entry, epic))

    # ── Features and stories: refinement at steps 2, 4 and 6 ─────────────────
    ready = sum(1 for s in stories if s.get("ready") and s["number"] not in active)
    waiting = sum(1 for s in stories if s.get("refined") and not s.get("ready") and s["number"] not in active)
    buffer_open = ready < limits.buffer_min and ready + waiting < limits.buffer_max

    for number, entry in sorted(issues.items()):
        kind = entry.get("type")
        unreadable = next((r for r in entry.get("reasons") or [] if str(r).startswith("unreadable:")), None)
        if unreadable:
            skip(number, unreadable)
            continue
        epic_number = entry.get("epic")
        epic = epics.get(epic_number) if isinstance(epic_number, int) else None
        if epic is None:
            continue  # no-parent or parent-not-open-epic: nothing to schedule
        if entry.get("refined") or (epic.get("spec") or {}).get("state") != "merged":
            continue  # nothing to refine, or listed once under its Epic
        if number in active and not entry.get("stale"):
            continue  # in implementation: only a stale record is refined here; the rest is work in flight (step 1)
        if epic_number not in ready_epics:
            skip(number, "epic-not-refined")
            continue
        if kind in STORY_TYPES:
            feature_number = entry.get("feature")
            if not feature_number:
                skip(number, "no-feature")
                continue
            feature = issues.get(feature_number) or {}
            if not feature.get("refined") or feature.get("stale"):
                skip(number, f"feature-not-refined #{feature_number}")
                continue
        sort_key = key(entry, epic)
        gate = _refine_gate(entry, epic.get("spec") or {})
        if number in active:
            if gate in ("open-decision-blocker", "last-refinement-failed"):
                skip(number, gate)
            else:
                add(entry, "refine", epic_number, 2, 2, "stale-record", sort_key)
        elif gate == "unblocked":
            add(entry, "refine", epic_number, 2, 3, "unblocked", sort_key)
        elif gate:
            skip(number, gate)
        elif kind == "feature":
            if entry.get("stale"):
                add(entry, "refine", epic_number, 2, 1, "stale-record", sort_key)
            else:
                add(entry, "refine", epic_number, 4, 0, "not-refined", sort_key)
        elif not buffer_open:
            skip(number, "buffer-full")
        elif entry.get("stale"):
            add(entry, "refine", epic_number, 6, 0, "stale-record", sort_key)
        else:
            add(entry, "refine", epic_number, 6, 1, "not-refined", sort_key)

    # ── Implementation start: step 5 ─────────────────────────────────────────
    starts = [s for s in stories if s.get("ready") and not s.get("stale") and s["number"] not in active]
    if starts and len(active) >= limits.wip:
        for entry in starts:
            skip(entry["number"], "wip-limit")
    elif starts:
        running = sorted(active)

        def note(number: int) -> None:
            if f"files-affected-missing #{number}" not in notes:
                notes.append(f"files-affected-missing #{number}")

        for entry in sorted(starts, key=lambda s: key(s, epics.get(s.get("epic")))):
            mine = entry.get("files_affected")
            if mine is None:
                note(entry["number"])
            overlap = None
            for other in running:
                theirs = (issues.get(other) or {}).get("files_affected")
                if theirs is None:
                    note(other)
                    continue
                if mine is None or overlap:
                    continue
                path = next((p for p in mine if p in theirs), None)
                if path is not None:
                    overlap = f"file-overlap #{other} {path}"
            if overlap:
                skip(entry["number"], overlap)
            else:
                add(entry, "implement", entry.get("epic"), 5, 0, "ready",
                    key(entry, epics.get(entry.get("epic"))))  # fmt: skip

    chosen = min(candidates, key=lambda c: (c.step, c.group, c.key), default=None)
    return Decision(
        action=chosen.action() if chosen else None,
        skipped=[{"issue": n, "reason": r} for n, r in sorted(skipped.items())],
        notes=notes,
        counts={"ready": ready, "waiting": waiting, "in_implementation": len(active)},
    )


def _unreadable(error: str, limits: Limits | None) -> SchedulerResult:
    log(f"[SCHEDULER] unreadable: {error}")
    return SchedulerResult(OPERATION, "unreadable", error=error, block=BLOCK, fields={
        "action": None, "skipped": [], "notes": [], "counts": None,
        "limits": limits.to_dict() if limits else None})  # fmt: skip


def _usable(facts: Any) -> str | None:
    if not isinstance(facts, dict):
        return "the facts are not a JSON object"
    if facts.get("result") != "read":
        return f"the facts result is {facts.get('result')!r}, not 'read'" + (
            f": {facts['error']}" if facts.get("error") else "")  # fmt: skip
    for name in ("epics", "issues"):
        items = facts.get(name)
        if not isinstance(items, list) or not all(isinstance(i, dict) and isinstance(i.get("number"), int)
                                                  for i in items):  # fmt: skip
            return f"the facts have no usable {name} list"
    return None


def next_action(gateway: Gateway | None, repo: str, *, in_implementation: Sequence[int],
                env: Mapping[str, str] | None = None, facts: Mapping[str, Any] | None = None) -> Result:  # fmt: skip
    """Load the limits, read the facts (unless given) and print-ready the decision."""

    env = os.environ if env is None else env
    try:
        limits = load_limits(env)
    except InvalidLimits as exc:
        return _unreadable(f"limits: {exc}", None)
    if facts is None:
        if gateway is None:
            return _unreadable("facts: no gateway and no facts", limits)
        facts = read_facts(gateway, repo, env=env).to_dict()
    problem = _usable(facts)
    if problem:
        return _unreadable(f"facts: {problem}", limits)
    decision = choose_next(facts, in_implementation=in_implementation, limits=limits)
    action = decision.action
    if action:
        log(f"[SCHEDULER] {action['kind']} #{action['issue']} step={action['step']} reason={action['reason']}")
    else:
        log("[SCHEDULER] idle")
    for entry in decision.skipped:
        log(f"[SCHEDULER] skip #{entry['issue']} {entry['reason']}")
    for text in decision.notes:
        log(f"[SCHEDULER] note {text}")
    return SchedulerResult(OPERATION, decision.result, block=BLOCK, fields={
        "action": action, "skipped": decision.skipped, "notes": decision.notes, "counts": decision.counts,
        "limits": limits.to_dict()})  # fmt: skip
