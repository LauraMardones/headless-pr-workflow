"""The failure stop (issue #503, Epic #160 spec → E8, Feature #560 → F1.4).

After ``FAILED_SESSIONS_STOP_THRESHOLD`` failed model sessions within
``WINDOW_HOURS``, the dispatcher starts no new session until the PO posts a
standalone ``/unblock`` line on the issue that carries the ``failure-stop``
marker. Everything is read from GitHub comments of the dispatcher's own account
(ADR-014 → Block 2), never from the runner cache, so a lost cache neither lifts
the stop nor resets the count.

- Each failed session leaves ``action=session-failed`` in the failure blocker
  comment the dispatcher posts on the story.
- The failure that brings the count to the threshold also leaves
  ``action=failure-stop`` in that comment.
- A stop holds while a ``failure-stop`` marker on an open issue has no later
  standalone PO ``/unblock`` line on the same issue (the ``marker holds`` rule).
- Failures recorded before the ``/unblock`` that lifted a stop do not count
  toward the next stop.

Result words and exit codes: ``clear``, ``recorded``, ``dry-run`` (0), ``holds``
(1), ``invalid`` (2), ``unreadable`` (3).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from .gateway import Gateway, GatewayError
from .marker import Unreadable, dispatcher_marker, marker_text, resolve_login
from .write import _UNBLOCK_LINE, EXIT_INVALID, EXIT_NO, EXIT_OK, EXIT_UNREADABLE, Result, Settings, _author

BLOCK = "failure-stop"
THRESHOLD_VARIABLE = "FAILED_SESSIONS_STOP_THRESHOLD"
WINDOW_HOURS = 24
FAILED_ACTION = "session-failed"
STOP_ACTION = "failure-stop"
STEPS = ("implement", "review", "refine")

_THRESHOLD = re.compile(r"[0-9]+")
_MARKER = re.compile(r"<!-- hpw:dispatcher action=(session-failed|failure-stop)((?: [A-Za-z0-9_.-]+=[^\s]+)*) -->")
_RUN_URL = re.compile(r"https?://[^\s]+")

_EXIT = {"clear": EXIT_OK, "recorded": EXIT_OK, "dry-run": EXIT_OK, "holds": EXIT_NO,
         "invalid": EXIT_INVALID, "unreadable": EXIT_UNREADABLE}  # fmt: skip


@dataclass
class StopResult(Result):
    block: str = BLOCK
    fields: dict[str, Any] = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        return _EXIT.get(self.result, EXIT_NO)


class InvalidThreshold(ValueError):
    pass


def parse_threshold(env: Mapping[str, str] | None = None) -> int:
    """A whole number of one or more in decimal digits; no default (like the WIP and usage limits)."""

    env = os.environ if env is None else env
    raw = env.get(THRESHOLD_VARIABLE)
    if raw is None or raw == "":
        raise InvalidThreshold(f"{THRESHOLD_VARIABLE} is not set")
    if not _THRESHOLD.fullmatch(raw) or int(raw) < 1:
        raise InvalidThreshold(f"{THRESHOLD_VARIABLE}={raw!r} is not a whole number of one or more")
    return int(raw)


def _time(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fields_of(text: str) -> dict[str, str]:
    return dict(part.split("=", 1) for part in text.split())


def _first_unblock(comments: list[dict[str, Any]], po_login: str, since: datetime) -> datetime | None:
    """The time of the first standalone PO ``/unblock`` line after ``since``, or ``None``."""

    times = []
    for c in comments:
        at = _time(c.get("created_at") or "")
        if (_author(c) == po_login and at is not None and at > since
                and any(_UNBLOCK_LINE.match(line) for line in str(c.get("body") or "").split("\n"))):  # fmt: skip
            times.append(at)
    return min(times) if times else None


@dataclass
class _State:
    threshold: int
    failures: list[dict[str, Any]]
    holding: list[dict[str, Any]]
    reset: datetime | None
    titles: dict[int, str]


def _read(gateway: Gateway, threshold: int, settings: Settings, env: Mapping[str, str] | None,
          moment: datetime) -> _State:
    """Every marker of the dispatcher's own account on the open issues; raises ``Unreadable``.

    Issues closed inside the window add restart evidence only: the PO's ``/unblock`` after a
    ``failure-stop`` marker still moves the cutoff of the count after the stop issue is
    closed. They never hold a stop and their failures do not count."""

    login = resolve_login(gateway, "dispatcher", env)
    try:
        issues = gateway.list_open_issues()
    except GatewayError as exc:
        raise Unreadable(str(exc)) from None
    failures: list[dict[str, Any]] = []
    holding: list[dict[str, Any]] = []
    resets: list[datetime] = []
    titles: dict[int, str] = {}
    for issue in issues:
        number = issue.get("number")
        if not isinstance(number, int):
            continue
        titles[number] = str(issue.get("title") or "")
        if issue.get("comments") == 0:
            continue  # the list says the issue has no comment: nothing to read
        try:
            comments = gateway.list_comments(number)
        except GatewayError as exc:
            raise Unreadable(str(exc)) from None
        for c in comments:
            if _author(c) != login:
                continue  # a marker counts only from the dispatcher's own account
            at = _time(c.get("created_at") or "")
            if at is None:
                continue
            for action, rest in _MARKER.findall(str(c.get("body") or "")):
                values = _fields_of(rest)
                if action == FAILED_ACTION:
                    failures.append({"issue": number, "step": values.get("step", ""),
                                     "run_url": values.get("run_url", ""), "at": at})  # fmt: skip
                    continue
                lifted = _first_unblock(comments, settings.po_login, at)
                if lifted is None:
                    holding.append({"issue": number, "at": at, "url": c.get("html_url") or c.get("url")})
                else:
                    resets.append(lifted)
    try:
        recently_closed = gateway.list_issues_closed_since(_iso(moment - timedelta(hours=WINDOW_HOURS)))
    except GatewayError as exc:
        raise Unreadable(str(exc)) from None
    for issue in recently_closed:
        number = issue.get("number")
        if not isinstance(number, int) or number in titles or issue.get("comments") == 0:
            continue
        try:
            comments = gateway.list_comments(number)
        except GatewayError as exc:
            raise Unreadable(str(exc)) from None
        for c in comments:
            at = _time(c.get("created_at") or "")
            if _author(c) != login or at is None:
                continue
            if any(action == STOP_ACTION for action, _rest in _MARKER.findall(str(c.get("body") or ""))):
                lifted = _first_unblock(comments, settings.po_login, at)
                if lifted is not None:
                    resets.append(lifted)
    return _State(threshold, failures, holding, max(resets) if resets else None, titles)


def _counted(state: _State, now: datetime) -> list[dict[str, Any]]:
    start = now - timedelta(hours=WINDOW_HOURS)
    rows = [f for f in state.failures if f["at"] > start and (state.reset is None or f["at"] > state.reset)]
    return sorted(rows, key=lambda f: f["at"])


def _sessions(rows: list[dict[str, Any]], titles: Mapping[int, str]) -> list[dict[str, Any]]:
    return [{"issue": r["issue"], "title": titles.get(r["issue"], ""), "step": r["step"], "run_url": r["run_url"],
             "at": _iso(r["at"])} for r in rows]  # fmt: skip


def _base(threshold: int | None, rows: list[dict[str, Any]], titles: Mapping[int, str]) -> dict[str, Any]:
    return {"threshold": threshold, "count": len(rows), "window_hours": WINDOW_HOURS, "sessions": _sessions(rows, titles)}


NOW_VARIABLE = "FAILURE_STOP_NOW"


def _now(now: datetime | None, env: Mapping[str, str] | None = None) -> datetime:
    """The clock of the window. ``FAILURE_STOP_NOW`` (an ISO time) fixes it for tests that
    run the invoke script against a fake GitHub with a fixed clock, like ``USAGE_LIMIT_TODAY``."""

    if now is None:
        fixed = _time((os.environ if env is None else env).get(NOW_VARIABLE, ""))
        now = fixed or datetime.now(timezone.utc)
    return now.astimezone(timezone.utc)


def check(
    gateway: Gateway, *, dry_run: bool = False, settings: Settings | None = None,
    env: Mapping[str, str] | None = None, now: datetime | None = None,
) -> StopResult:
    """``holds`` while a stop is recorded and not lifted; ``clear`` otherwise."""

    try:
        threshold = parse_threshold(env)
    except InvalidThreshold as exc:
        return StopResult("check", "invalid", error=str(exc), fields=_base(None, [], {}))
    if dry_run:
        return StopResult("check", "dry-run", fields=_base(threshold, [], {}))
    settings = settings or Settings.from_env(env)
    moment = _now(now, env)
    try:
        state = _read(gateway, threshold, settings, env, moment)
    except Unreadable as exc:
        return StopResult("check", "unreadable", error=f"read failure markers: {exc}", fields=_base(threshold, [], {}))
    if state.holding:
        stop = max(state.holding, key=lambda s: s["at"])
        rows = [f for f in state.failures
                if stop["at"] - timedelta(hours=WINDOW_HOURS) < f["at"] <= stop["at"]
                and (state.reset is None or f["at"] > state.reset)]  # fmt: skip
        rows.sort(key=lambda f: f["at"])
        return StopResult("check", "holds", fields={**_base(threshold, rows, state.titles), "stop_issue": stop["issue"],
                                                    "stop_url": stop["url"], "stop_at": _iso(stop["at"])})  # fmt: skip
    return StopResult("check", "clear", fields=_base(threshold, _counted(state, moment), state.titles))


class InvalidRecord(ValueError):
    pass


def validate_record(issue: int, step: str, run_url: str) -> None:
    if issue < 1:
        raise InvalidRecord("--issue must be a positive integer")
    if step not in STEPS:
        raise InvalidRecord(f"--step must be one of {', '.join(STEPS)}")
    if not _RUN_URL.fullmatch(run_url or "") or "-->" in run_url:
        raise InvalidRecord("--run-url must be one http(s) URL without whitespace")


def failed_marker(issue: int, step: str, run_url: str) -> str:
    return dispatcher_marker(marker_text(FAILED_ACTION, [("issue", str(issue)), ("step", step), ("run_url", run_url)]))


def stop_marker(issue: int, threshold: int) -> str:
    return dispatcher_marker(marker_text(STOP_ACTION, [("issue", str(issue)), ("threshold", str(threshold)),
                                                       ("window_hours", str(WINDOW_HOURS))]))  # fmt: skip


def record(
    gateway: Gateway, issue: int, step: str, run_url: str, *, dry_run: bool = False,
    settings: Settings | None = None, env: Mapping[str, str] | None = None, now: datetime | None = None,
) -> StopResult:
    """The marker text for the failure comment, and whether this failure reaches the threshold.

    The command writes nothing: the caller adds ``marker`` to the failure blocker
    comment it posts. ``reached`` is true when the count, this failure included,
    is at or above the threshold and no stop holds yet, so a failure recorded
    while the comments could not be read is caught by the next one.
    """

    validate_record(issue, step, run_url)
    moment = _now(now, env)
    failed = failed_marker(issue, step, run_url)
    try:
        threshold = parse_threshold(env)
    except InvalidThreshold as exc:
        return StopResult("record", "invalid", error=str(exc),
                          fields={**_base(None, [], {}), "marker": failed, "reached": False})  # fmt: skip
    if dry_run:
        return StopResult("record", "dry-run", fields={**_base(threshold, [], {}), "marker": "", "reached": False})
    settings = settings or Settings.from_env(env)
    try:
        state = _read(gateway, threshold, settings, env, moment)
    except Unreadable as exc:
        return StopResult("record", "unreadable", error=f"read failure markers: {exc}",
                          fields={**_base(threshold, [], {}), "marker": failed, "reached": False})  # fmt: skip
    rows = _counted(state, moment) + [{"issue": issue, "step": step, "run_url": run_url, "at": moment}]
    reached = not state.holding and len(rows) >= threshold
    marker = f"{failed}\n{stop_marker(issue, threshold)}" if reached else failed
    return StopResult("record", "recorded", fields={**_base(threshold, rows, state.titles), "marker": marker,
                                                    "reached": reached, "holds": bool(state.holding)})  # fmt: skip
