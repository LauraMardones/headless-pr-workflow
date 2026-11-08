"""Status rule: ``hpw dispatcher status read`` (issue #258, ADR-010, ADR-014).

Derives exactly one board status for every open issue with a known ``type:``
label, and counts each Feature's and Epic's closed and remaining implementation
issues, from GitHub facts alone. ``derive_status`` and ``count_progress`` are
pure: they get no gateway, use no model and write nothing. ``read_status``
collects the facts with read calls only: the facts of ``read_facts`` (#283) plus
linked PRs and their merge gates, spec PRs, other Blocked Declarations and
closed issues.

Result words and exit codes: ``read`` (0), ``unreadable`` (3: the open-issue
list, the open-PR list or the facts could not be read). A fact that cannot be
read for one item gives that item the status ``unknown`` with the reason; the
rule never guesses one.
"""

from __future__ import annotations

import os
import re
from typing import Any, Mapping

from .facts import KNOWN_TYPES, STORY_TYPES, _FEATURE_GROUP, _PARENT_EPIC, _Facts, _labels, read_facts
from .gateway import Gateway, GatewayError
from .scheduler import _refine_gate
from .write import Result, log

BLOCK = "status"
OPERATION = "read"

STATUSES = ("No spec", "In specification", "Refinement failed", "Not refined", "Blocked", "Refined", "Ready to implement",
            "In implementation", "In review", "Mergeable", "In progress", "Done")  # fmt: skip
UNKNOWN = "unknown"

_BLOCKED_TYPES = ("dependency", "external", "conflict", "resource")
_TYPE_LINE = re.compile(r"^[ \t]*Type:\s*([A-Za-z]+)\b", re.M)
_UNBLOCKED_LINE = re.compile(r"^\s*Unblocked\b(?!\s+when\b)", re.M | re.I)


def _decided(status: str, fact: str) -> tuple[str, str]:
    return status, fact


def derive_status(facts: Mapping[str, Any]) -> tuple[str, str]:
    """``(status, fact)`` for one item. Pure: the same facts give the same result.

    Keys of ``facts``: ``type`` (``epic``, ``feature``, ``story``, ``task``, ``bug`` or ``None``), ``state``
    (``open`` or ``closed``), ``unreadable`` (a reason, or ``None``), ``blocked``, ``refined``, ``ready``,
    ``parent_open_epic`` (the item has an open parent Epic; always true for an Epic), ``spec_merged`` and
    ``spec_pr`` (for the item's Epic: spec merged on main; an open PR adds or changes the spec), ``refinement_failed``
    (the scheduler's ``last-refinement-failed`` fact, #476), and ``prs`` (open linked PRs, each
    ``{"number", "draft", "gates_pass"}``).
    """

    kind = facts.get("type")
    if kind not in KNOWN_TYPES:
        return UNKNOWN, "no known type: label"
    if facts.get("state") == "closed":
        return _decided("Done", "the issue is closed")
    if facts.get("unreadable"):
        return UNKNOWN, str(facts["unreadable"])
    if facts.get("blocked"):
        return _decided("Blocked", "an open Blocked Declaration without resolution")

    implementation = kind in STORY_TYPES
    prs = list(facts.get("prs") or []) if implementation else []
    refined = bool(facts.get("refined"))
    spec_merged = bool(facts.get("spec_merged"))
    parented = bool(facts.get("parent_open_epic"))
    # An issue outside any open Epic has no spec to wait for; an open PR still shows its own status (gap 2).
    own_pr = not parented and bool(prs)

    if facts.get("spec_pr") and (kind == "epic" or (not refined and not spec_merged)):
        return _decided("In specification", "an open PR adds or changes the Epic's spec")
    if not refined and not own_pr:
        if not parented:
            return _decided("No spec", "the issue has no open parent Epic and no open linked PR")
        if not spec_merged:
            return _decided("No spec", "the Epic has no merged spec and no open spec PR, and the issue is not refined")
    if facts.get("refinement_failed"):
        return _decided("Refinement failed", "the latest refine Session Summary after the current spec commit "
                                             "reports refine-check fail or unreadable, with no /unblock after it")
    if not refined and not own_pr:
        return _decided("Not refined", "the Epic's spec is merged and the issue has no refinement for the current spec")

    if not implementation:
        return _decided("In progress", "refined and not closed")
    non_draft = [pr for pr in prs if not pr.get("draft")]
    gated = [pr for pr in non_draft if pr.get("gates_pass")]
    if gated:
        return _decided("Mergeable", f"the merge gates pass on the current head of PR #{gated[0]['number']}")
    if non_draft:
        return _decided("In review", f"PR #{non_draft[0]['number']} is not draft and its merge gates do not all pass")
    if prs:
        return _decided("In implementation", f"PR #{prs[0]['number']} is open in draft")
    if facts.get("ready"):
        return _decided("Ready to implement", "refined, every hard dependency closed, no open linked PR")
    return _decided("Refined", "refined and a hard dependency is open, unresolved or unreadable")


def count_progress(items: list[Mapping[str, Any]]) -> dict[str, dict[int, dict[str, int]]]:
    """Closed and total implementation issues per Feature, and Features and implementation issues per Epic.

    ``items``: ``{"number", "type", "state", "epic", "feature"}`` for every issue, open and closed. A closed
    issue counts as closed whatever its close reason. Pure.
    """

    features: dict[int, dict[str, int]] = {}
    epics: dict[int, dict[str, int]] = {}
    for item in items:
        if item.get("type") == "feature":
            features.setdefault(int(item["number"]), {"closed": 0, "total": 0})
        elif item.get("type") == "epic":
            epics.setdefault(int(item["number"]), {"features_closed": 0, "features_total": 0, "issues_closed": 0,
                                                   "issues_total": 0})  # fmt: skip
    for item in items:
        closed = item.get("state") == "closed"
        kind = item.get("type")
        epic = item.get("epic")
        if kind in STORY_TYPES:
            feature = item.get("feature")
            if feature in features:
                features[feature]["total"] += 1
                features[feature]["closed"] += closed
            if epic in epics:
                epics[epic]["issues_total"] += 1
                epics[epic]["issues_closed"] += closed
        elif kind == "feature" and epic in epics:
            epics[epic]["features_total"] += 1
            epics[epic]["features_closed"] += closed
    return {"epics": epics, "features": features}


def _kind(issue: Mapping[str, Any]) -> str | None:
    kinds = [label.split(":", 1)[1] for label in _labels(dict(issue)) if label.startswith("type:")]
    return next((k for k in kinds if k in KNOWN_TYPES), None)


def _linked(body: str, number: int) -> bool:
    return re.search(rf"(closes|fixes|resolves)\s+#{number}\b", body.lower()) is not None


def _resolves(body: str) -> bool:
    """An ``Unblocked`` line in a comment that is not itself a declaration (its ``Unblocked when:`` field is no resolution)."""

    return "## Blocked Declaration" not in body and _UNBLOCKED_LINE.search(body) is not None


def other_blocker_open(facts: _Facts, comments: list[dict[str, Any]]) -> bool:
    """An open Blocked Declaration of type dependency, external, conflict or resource (see the command page)."""

    declared = ""
    for comment in comments:
        body = str(comment.get("body") or "")
        match = _TYPE_LINE.search(body)
        if (facts.trusted(comment) and "## Blocked Declaration" in body and match
                and match.group(1).lower() in _BLOCKED_TYPES):
            declared = max(declared, str(comment.get("created_at") or ""))
    if not declared:
        return False
    return not any(
        facts.trusted(c) and str(c.get("created_at") or "") > declared and _resolves(str(c.get("body") or ""))
        for c in comments
    )


def _epic_refined(entry: Mapping[str, Any]) -> bool:
    refinement, blocker, record = entry.get("refinement"), entry.get("decision_blocker"), entry.get("record") or {}
    if refinement is None or blocker is None:
        return False
    if entry["spec"]["state"] == "merged" and record.get("state") != "current":
        return False
    if refinement["refine_check"] != "pass" or blocker["state"] == "open":
        return False
    return not (blocker["state"] == "unblocked" and str(refinement["created_at"] or "") <= blocker["unblocked_at"])


def _refinement_failed(entry: Mapping[str, Any], spec: Mapping[str, Any]) -> bool:
    """The scheduler's ``last-refinement-failed`` gate, with the Epic's spec merged (the rule is the scheduler's)."""

    return spec.get("state") == "merged" and _refine_gate(dict(entry), dict(spec)) == "last-refinement-failed"


def read_status(gateway: Gateway, repo: str, *, epic: int | None = None, env: Mapping[str, str] | None = None) -> Result:
    """Derive the status of every open typed issue (or of one Epic and the issues under it) and the progress counts."""

    env = os.environ if env is None else env
    try:
        return _read(gateway, epic, env)
    except GatewayError as exc:
        log(f"[STATUS] unreadable: {exc}")
        return Result(OPERATION, "unreadable", error=str(exc), fields={"items": [], "progress": None}, block=BLOCK)


def _read(gateway: Gateway, only_epic: int | None, env: Mapping[str, str]) -> Result:
    facts_result = read_facts(gateway, "", epic=only_epic, env=env)
    if facts_result.result != "read":
        raise GatewayError("read facts", facts_result.error or "the facts could not be read")
    epics = {int(e["number"]): e for e in facts_result.fields["epics"]}
    entries = facts_result.fields["issues"]
    open_issues = {int(i["number"]): i for i in gateway.list_open_issues() if isinstance(i.get("number"), int)}
    prs = [p for p in gateway.list_open_prs() if isinstance(p.get("number"), int)]
    helper = _Facts(gateway, env)

    spec_pr: dict[int, bool | None] = {}
    if epics:
        touched: list[list[str]] | None = []
        for pr in prs:
            try:
                touched.append(gateway.pr_files(int(pr["number"])))
            except GatewayError:
                touched = None
                break
        for number in epics:
            pattern = re.compile(rf"^specs/{number}-[^/]+/spec\.md$")
            spec_pr[number] = None if touched is None else any(pattern.match(f) for files in touched for f in files)

    gates: dict[tuple[int, str], bool] = {}

    def gates_pass(pr: Mapping[str, Any]) -> bool:
        head = str((pr.get("head") or {}).get("sha") or "")
        key = (int(pr["number"]), head)
        if key not in gates:
            try:
                gates[key] = gateway.merge_gates_pass(key[0], head)
            except GatewayError as exc:
                log(f"[STATUS] merge gates of PR #{key[0]} unreadable, counted as not passing: {exc.step}")
                gates[key] = False
        return gates[key]

    def linked(number: int) -> list[dict[str, Any]]:
        out = []
        for pr in prs:
            if _linked(str(pr.get("body") or ""), number):
                draft = bool(pr.get("draft"))
                out.append({"number": int(pr["number"]), "draft": draft, "gates_pass": False if draft else gates_pass(pr)})
        return out

    def blocked_and_reason(number: int, decision: Mapping[str, Any] | None, *, read_own: bool) -> tuple[bool, str | None]:
        """Whether the item has an open blocker, or why that cannot be told. ``read_own``: the facts skipped its comments."""

        if decision is None and not read_own:
            return False, "decision blocker unreadable"
        try:
            comments = gateway.list_comments(number)
        except GatewayError as exc:
            return False, exc.step
        decision = helper.decision_blocker(comments) if decision is None else decision
        return decision["state"] == "open" or other_blocker_open(helper, comments), None

    items: list[dict[str, Any]] = []
    for number, entry in epics.items():
        kind = "epic"
        blocked, reason = blocked_and_reason(number, entry.get("decision_blocker"), read_own=False)
        record = entry.get("record") or {}
        unreadable = reason or (str(record.get("reason") or "unreadable: spec record") if record.get("state") == "unreadable" else None)
        unreadable = unreadable or (f"unreadable: spec PRs of #{number}" if spec_pr[number] is None else None)
        items.append(_item(number, kind, {
            "type": kind, "state": "open", "unreadable": unreadable, "blocked": blocked,
            "refined": _epic_refined(entry), "parent_open_epic": True, "spec_merged": entry["spec"]["state"] == "merged",
            "spec_pr": bool(spec_pr[number]), "prs": [],
            "refinement_failed": _refinement_failed({**entry, "type": "epic"}, entry["spec"])}))  # fmt: skip
    for entry in entries:
        number, kind = int(entry["number"]), entry["type"]
        parent = entry.get("epic")
        in_epic = parent in epics
        unreadable = None
        for reason in entry["reasons"]:
            if reason.startswith("unreadable"):
                unreadable = reason
        blocked = False
        if unreadable is None:
            blocked, unreadable = blocked_and_reason(number, entry.get("decision_blocker"), read_own=True)
        if unreadable is None and in_epic and spec_pr[parent] is None:
            unreadable = f"unreadable: spec PRs of #{parent}"
        items.append(_item(number, kind, {
            "type": kind, "state": "open", "unreadable": unreadable, "blocked": blocked,
            "refined": bool(entry["refined"]), "ready": bool(entry["ready"]), "parent_open_epic": in_epic,
            "spec_merged": in_epic and epics[parent]["spec"]["state"] == "merged",
            "spec_pr": bool(in_epic and spec_pr[parent]), "prs": linked(number) if unreadable is None else [],
            "refinement_failed": in_epic and _refinement_failed(entry, epics[parent]["spec"])}))  # fmt: skip

    for item in sorted(items, key=lambda i: i["number"]):
        log(f"[STATUS] #{item['number']} {item['type']} {item['status']}: {item['fact']}")
    items.sort(key=lambda i: i["number"])

    progress: Any
    try:
        progress = _progress(gateway, open_issues, epics, only_epic)
    except GatewayError as exc:
        log(f"[STATUS] progress unreadable: {exc}")
        progress = None
    return Result(OPERATION, "read", fields={"items": items, "progress": progress}, block=BLOCK)


def _item(number: int, kind: str, facts: dict[str, Any]) -> dict[str, Any]:
    status, fact = derive_status(facts)
    return {"number": number, "type": kind, "status": status, "fact": fact}


def _progress(gateway: Gateway, open_issues: dict[int, dict[str, Any]], epics: dict[int, dict[str, Any]],
              only_epic: int | None) -> dict[str, Any]:  # fmt: skip
    rows = []
    for state, issues in (("open", open_issues.values()), ("closed", gateway.list_closed_issues())):
        for issue in issues:
            kind = _kind(issue)
            if kind is None or not isinstance(issue.get("number"), int):
                continue
            body = str(issue.get("body") or "")
            parent = _PARENT_EPIC.search(body)
            group = _FEATURE_GROUP.search(body)
            number = int(issue["number"])
            epic = number if kind == "epic" else int(parent.group(1)) if parent else None
            rows.append({"number": number, "type": kind, "state": state, "epic": epic,
                         "feature": int(group.group(1)) if group and kind in STORY_TYPES else None})  # fmt: skip
    if only_epic is not None:
        rows = [r for r in rows if r["epic"] == only_epic]
    counts = count_progress(rows)
    wanted = set(epics) | {r["number"] for r in rows if r["type"] == "epic" and r["state"] == "open"}
    return {"epics": {str(n): c for n, c in sorted(counts["epics"].items()) if n in wanted},
            "features": {str(n): c for n, c in sorted(counts["features"].items())}}  # fmt: skip
