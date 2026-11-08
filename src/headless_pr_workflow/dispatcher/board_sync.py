"""Board writer: ``hpw dispatcher status sync`` (issue #476, ADR-010, ADR-014).

Writes the status that the rule of ``status.py`` derives to every board item whose Status differs, and keeps
the progress section of each open Epic issue current. It is the only code that writes board status. Board
writes go through ``write_board_status`` (write, read back, retry); the only other write is the Epic body edit,
read back. No label, comment or other issue is written.

Result words and exit codes: ``synced`` (0: every difference written and verified, or none), ``dry-run`` (0),
``failed`` (1: at least one board or Epic-body write failed), ``unreadable`` (3: a read failed, nothing written).
"""

from __future__ import annotations

from typing import Any, Mapping

from .facts import KNOWN_TYPES, STORY_TYPES, _FEATURE_GROUP, _PARENT_EPIC
from .gateway import Gateway, GatewayError, mask_tokens
from .status import _kind, derive_status, read_status
from .write import DEFAULT_ATTEMPTS, Result, Settings, log, write_board_status

BLOCK = "status"
OPERATION = "sync"

PROGRESS_START = "<!-- hpw:progress:start -->"
PROGRESS_END = "<!-- hpw:progress:end -->"


class MarkerError(Exception):
    pass


def progress_section(features: list[Mapping[str, Any]], epic_counts: Mapping[str, int]) -> str:
    """The text between (and with) the markers. No date or run id: unchanged facts give identical text."""

    lines = [PROGRESS_START, "## Progress", ""]
    for feature in sorted(features, key=lambda f: f["number"]):
        counts = feature["counts"]
        lines.append(f"- #{feature['number']} {feature['title']}: {feature['status']}, "
                     f"{counts['closed']} of {counts['total']} implementation issues closed")  # fmt: skip
    if features:
        lines.append("")
    lines.append(f"Features: {epic_counts['features_closed']} of {epic_counts['features_total']} closed; "
                 f"implementation issues: {epic_counts['issues_closed']} of {epic_counts['issues_total']} closed")  # fmt: skip
    lines.append(PROGRESS_END)
    return "\n".join(lines)


def with_section(body: str, section: str) -> str:
    """``body`` with ``section`` between its markers, or appended once when there are none. Raises ``MarkerError``."""

    starts, ends = body.count(PROGRESS_START), body.count(PROGRESS_END)
    if starts == 0 and ends == 0:
        if not body:
            return section + "\n"
        return body + ("" if body.endswith("\n") else "\n") + "\n" + section + "\n"
    first, last = body.find(PROGRESS_START), body.find(PROGRESS_END)
    if starts != 1 or ends != 1 or first > last:
        raise MarkerError(f"the progress markers are duplicated, out of order or only one of the pair "
                          f"({starts} start, {ends} end)")  # fmt: skip
    return body[:first] + section + body[last + len(PROGRESS_END):]


def _rows(gateway: Gateway) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    """Every typed issue, open and closed, as ``{"number", "title", "type", "state", "epic", "feature", "body"}``."""

    rows: list[dict[str, Any]] = []
    for state, issues in (("open", gateway.list_open_issues()), ("closed", gateway.list_closed_issues())):
        for issue in issues:
            kind = _kind(issue)
            if kind is None or not isinstance(issue.get("number"), int):
                continue
            body = str(issue.get("body") or "")
            parent, group = _PARENT_EPIC.search(body), _FEATURE_GROUP.search(body)
            number = int(issue["number"])
            rows.append({"number": number, "title": str(issue.get("title") or ""), "type": kind, "state": state,
                         "epic": number if kind == "epic" else int(parent.group(1)) if parent else None,
                         "feature": int(group.group(1)) if group and kind in STORY_TYPES else None,
                         "body": body})  # fmt: skip
    return rows, {r["number"]: r for r in rows}


def sync_board(gateway: Gateway, repo: str, *, epic: int | None = None, attempts: int = DEFAULT_ATTEMPTS,
               dry_run: bool = False, settings: Settings | None = None) -> Result:  # fmt: skip
    settings = settings or Settings.from_env()
    try:
        return _sync(gateway, epic, attempts, dry_run, settings)
    except GatewayError as exc:
        log(f"[SYNC] unreadable: {exc}")
        return _result("unreadable", [], [], [], dry_run, str(exc))


def _result(result: str, corrections: list, skipped: list, progress: list, dry_run: bool, error: str | None) -> Result:
    return Result(OPERATION, result, error=error, block=BLOCK, fields={
        "corrections": corrections, "skipped": skipped, "progress": progress, "dry_run": dry_run})  # fmt: skip


def _sync(gateway: Gateway, only_epic: int | None, attempts: int, dry_run: bool, settings: Settings) -> Result:
    read = read_status(gateway, "", epic=only_epic)
    if read.result != "read":
        raise GatewayError("read statuses", read.error or "the statuses could not be read")
    progress = read.fields["progress"]
    if progress is None:
        raise GatewayError("read progress", "the closed issues could not be read")
    open_items = {int(i["number"]): i for i in read.fields["items"]}
    snapshot = gateway.list_board_items()
    rows, by_number = _rows(gateway)
    if only_epic is not None:
        rows = [r for r in rows if r["epic"] == only_epic]
    scope = {r["number"] for r in rows} | set(open_items)

    corrections: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    on_board: set[int] = set()
    for card in snapshot.cards:
        if card.number is None:
            continue  # a draft or a pull request: not touched
        on_board.add(card.number)
        kinds = [label.split(":", 1)[1] for label in card.labels if label.startswith("type:")]
        kind = next((k for k in kinds if k in KNOWN_TYPES), None)
        if kind is None or card.number not in scope:
            continue
        if card.state == "closed":
            status, fact = derive_status({"type": kind, "state": "closed"})
        elif card.number in open_items:
            status, fact = open_items[card.number]["status"], open_items[card.number]["fact"]
        else:
            status, fact = "unknown", "the issue was not read"
        if status == "unknown":
            skipped.append({"issue": card.number, "reason": f"unknown: {fact}"})
        elif status != card.status:
            correction = {"issue": card.number, "type": kind, "before": card.status, "after": status, "fact": fact}
            if dry_run:
                correction.update(result="dry-run", missing_option=status not in snapshot.options, error=None)
            else:
                correction.update(_write(gateway, card.number, status, attempts, settings))
            corrections.append(correction)
            log(f"[SYNC] #{card.number} {card.status or '(none)'} -> {status}: {fact}")
    for number in sorted(open_items):
        if number not in on_board and open_items[number]["status"] != "unknown":
            skipped.append({"issue": number, "reason": "not_on_board"})
    skipped.sort(key=lambda s: s["issue"])
    for item in skipped:
        log(f"[SYNC] skipped #{item['issue']}: {item['reason']}")

    epics = [n for n in sorted(open_items) if open_items[n]["type"] == "epic"]
    sections = [_epic_progress(gateway, n, open_items, rows, by_number, progress, dry_run) for n in epics]
    for item in sections:
        log(f"[SYNC] progress #{item['epic']}: {item['result']}")

    failure = next((c["error"] for c in corrections if c["result"] == "failed"), None)
    failure = failure or (next((p["error"] for p in sections if p["result"] == "failed"), None) if not dry_run else None)
    result = "dry-run" if dry_run else "failed" if failure else "synced"
    return _result(result, corrections, skipped, sections, dry_run, failure)


def _write(gateway: Gateway, number: int, status: str, attempts: int, settings: Settings) -> dict[str, Any]:
    written = write_board_status(gateway, number, status, attempts=attempts, settings=settings)
    if written.result in ("written", "already"):
        return {"result": "written", "error": None}
    return {"result": "failed", "error": mask_tokens(written.error or "write status: failed")}


def _epic_progress(gateway: Gateway, number: int, open_items: Mapping[int, Mapping[str, Any]],
                   rows: list[dict[str, Any]], by_number: Mapping[int, Mapping[str, Any]],
                   progress: Mapping[str, Any], dry_run: bool) -> dict[str, Any]:  # fmt: skip
    base = {"epic": number, "error": None}
    epic_counts = progress["epics"].get(str(number))
    if epic_counts is None:
        return {**base, "result": "failed", "error": f"progress of #{number}: no counts"}
    features = []
    for row in rows:
        if row["type"] == "feature" and row["epic"] == number:
            if row["state"] == "closed":
                status = derive_status({"type": "feature", "state": "closed"})[0]
            else:
                status = (open_items.get(row["number"]) or {}).get("status", "unknown")
            features.append({"number": row["number"], "title": row["title"], "status": status,
                             "counts": progress["features"].get(str(row["number"])) or {"closed": 0, "total": 0}})  # fmt: skip
    section = progress_section(features, epic_counts)
    try:
        wanted = with_section(by_number[number]["body"], section)
    except MarkerError as exc:
        return {**base, "result": "failed", "error": f"epic #{number}: {exc}"}
    if wanted == by_number[number]["body"]:
        return {**base, "result": "already"}
    if dry_run:
        return {**base, "result": "dry-run"}
    try:
        fresh = gateway.issue_body(number)  # the body as it is now, not as listed earlier
        wanted = with_section(fresh, section)
        if wanted == fresh:
            return {**base, "result": "already"}
        gateway.edit_issue_body(number, wanted)
        seen = gateway.issue_body(number)
    except MarkerError as exc:
        return {**base, "result": "failed", "error": f"epic #{number}: {exc}"}
    except GatewayError as exc:
        return {**base, "result": "failed", "error": mask_tokens(str(exc))}
    if seen != wanted:
        return {**base, "result": "failed",
                "error": f"read back: wrote the progress section of #{number} but the body differs"}  # fmt: skip
    return {**base, "result": "written"}
