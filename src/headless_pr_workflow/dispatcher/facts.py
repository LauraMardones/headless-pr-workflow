"""Facts reader: ``hpw dispatcher facts read`` (issue #283, ADR-010, ADR-014).

Reads, from GitHub alone, each open Epic's merged spec and each open Epic,
Feature and story's spec record, refinement, decision blocker, hard
dependencies and declared files (issue #473), and derives *refined*, *ready*
and *stale* from them. It writes
nothing: the gateway it is given is only asked to read.

Result words and exit codes: ``read`` (0), ``unreadable`` (3: the open-issue
list, the ``specs/`` folder or a spec file's history could not be read). A
failure on one issue is reported on that issue and is not an exit code.
"""

from __future__ import annotations

import os
import re
from typing import Any, Mapping

from .gateway import Gateway, GatewayError
from .write import Result, log

BLOCK = "facts"
OPERATION = "read"

STORY_TYPES = ("story", "task", "bug")
KNOWN_TYPES = ("epic", "feature", *STORY_TYPES)

# Own copies of the rules of scripts/refine-check (SPEC_LINE, parse_sections, hard_dependencies);
# tests/test_dispatcher_facts_block.py checks that they agree.
SPEC_LINE = re.compile(r"^[ \t]*Spec:\s*\[`([^`]+)`\]\((\S+)\)\s+at\s+`([0-9a-fA-F]{7,40})`(?:\s.*)?$", re.M)
_PARENT_EPIC = re.compile(r"^[ \t]*Parent epic:\s*#(\d+)\b", re.I | re.M)
_FEATURE_GROUP = re.compile(r"^[ \t]*Feature group:\s*#(\d+)\b", re.I | re.M)
_CRITERION_PREFIX = re.compile(r"^[ \t]*Criterion prefix:\s*(F\d+)\b", re.I | re.M)
_REFINE_COMMAND = re.compile(r"^[ \t]*Command:\s*refine(?:-[A-Za-z]+)?[ \t]*$", re.M)
# The refine-check item may follow other checks on the line, e.g. "spec-check — pass; refine-check — pass" (#492).
_CHECK_RESULT = re.compile(
    r"^[ \t]*Checks run:[^\n]*?(?<![\w-])refine-check\s*[—–-]+\s*(pass|fail|unreadable)\b", re.I | re.M
)
_UNBLOCK_LINE = re.compile(r"^\s*/unblock\s*$")


def parse_sections(body: str) -> dict[str, str]:
    """Map each ``## `` heading to its text; headings inside code fences are ignored."""

    sections: dict[str, list[str]] = {}
    current: str | None = None
    fenced = False
    for line in body.replace("\r\n", "\n").split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        match = None if fenced else re.match(r"^##\s+(.+?)\s*$", line)
        if match:
            current = match.group(1)
            sections.setdefault(current, [])
        elif current is not None:
            sections[current].append(line)
    return {name: "\n".join(lines).strip() for name, lines in sections.items()}


def files_affected(body: str) -> list[str] | None:
    """The paths under ``## Files affected``; ``None`` when the section is missing.

    Own copy of ``extract_files`` in scripts/dispatcher-invoke.sh: the section runs from the first line that starts
    with ``## Files affected`` to the next line that starts with ``##``; each ``- `` item loses its backticks and a
    ``(note)``. tests/test_dispatcher_facts_block.py checks that they agree.
    """

    lines = body.replace("\r\n", "\n").split("\n")
    start = next((i for i, line in enumerate(lines) if line.startswith("## Files affected")), None)
    if start is None:
        return None
    paths: list[str] = []
    for line in lines[start + 1:]:
        if line.startswith("##"):
            break
        item = re.match(r"^\s*-\s\s*(.*)$", line)
        if not item:
            continue
        path = re.sub(r"\s*\(.*\)", "", item.group(1).replace("`", ""), count=1).strip()
        if path:
            paths.append(path)
    return paths


def hard_dependencies(sections: dict[str, str], own: int) -> list[int] | None:
    """The issues under ``Hard depends on:``; ``None`` when the section or the line is missing."""

    text = sections.get("Dependencies")
    if text is None:
        return None
    collected: list[str] = []
    active = False
    found = False
    for line in text.split("\n"):
        bare = line.strip().strip("*_ ").lower()
        if bare.startswith("hard depends on"):
            active = True
            found = True
            continue
        if bare.startswith(("soft depends on", "revalidate after")):
            active = False
        if active:
            collected.append(line)
    if not found:
        return None
    numbers = [int(n) for n in re.findall(r"#(\d+)", "\n".join(collected))]
    return sorted({n for n in numbers if n != own})


class _Unreadable(Exception):
    """One issue could not be read; ``str(exc)`` is the step."""


class _Facts:
    def __init__(self, gateway: Gateway, env: Mapping[str, str]) -> None:
        self.gw = gateway
        self.po_login = (env.get("DECISION_BLOCKER_PO_LOGIN") or "").strip()
        self._specs: dict[int, dict[str, Any]] = {}
        self._spec_dirs: list[str] | None = None
        self._compare: dict[tuple[str, str], str | GatewayError] = {}
        self._dependency: dict[int, str] = {}

    # ── Epic spec ────────────────────────────────────────────────────────────

    def spec_dirs(self) -> list[str]:
        if self._spec_dirs is None:
            try:
                entries = self.gw.list_dir("specs")
            except GatewayError as exc:
                if not exc.not_found:
                    raise
                entries = []
            self._spec_dirs = [str(e.get("name")) for e in entries if e.get("type") == "dir" and e.get("name")]
        return self._spec_dirs

    def spec(self, epic: int) -> dict[str, Any]:
        """The Epic's spec on ``main``. Raises ``GatewayError`` when the folder or a history cannot be read."""

        if epic in self._specs:
            return self._specs[epic]
        expected = f"specs/{epic}-*/spec.md"
        found = []
        for name in self.spec_dirs():
            if not name.startswith(f"{epic}-"):
                continue
            path = f"specs/{name}/spec.md"
            commit = self.gw.newest_commit(path)
            if commit is not None:
                found.append((path, commit))
        if not found:
            spec = {"state": "missing", "path": None, "commit": None, "committed_at": None,
                    "reason": f"no {expected} on main"}  # fmt: skip
        elif len(found) > 1:
            spec = {"state": "ambiguous", "path": None, "commit": None, "committed_at": None,
                    "reason": f"more than one {expected} on main: {', '.join(p for p, _ in found)}"}  # fmt: skip
        else:
            path, commit = found[0]
            spec = {"state": "merged", "path": path, "commit": commit["sha"], "committed_at": commit["date"],
                    "reason": None}  # fmt: skip
        self._specs[epic] = spec
        return spec

    # ── spec record ──────────────────────────────────────────────────────────

    def compare(self, base: str, head: str) -> str:
        key = (base, head)
        if key not in self._compare:
            try:
                self._compare[key] = self.gw.compare_commits(base, head)
            except GatewayError as exc:
                self._compare[key] = exc
        outcome = self._compare[key]
        if isinstance(outcome, GatewayError):
            if outcome.not_found:
                return "not-found"
            raise _Unreadable(outcome.step)
        return outcome

    def record(self, body: str, epic: int, spec: dict[str, Any]) -> dict[str, Any]:
        match = SPEC_LINE.search(body)
        if not match:
            has_line = re.search(r"^[ \t]*Spec:", body, re.M)
            return {"state": "invalid" if has_line else "missing", "path": None, "commit": None}
        path, _, commit = match.groups()
        out = {"path": path, "commit": commit}
        if spec["state"] == "merged":
            if path != spec["path"]:
                return {"state": "invalid", **out}
            status = self.compare(spec["commit"], commit)
            state = {"identical": "current", "ahead": "current", "behind": "stale", "diverged": "stale"}.get(status, "invalid")
            return {"state": state, **out}
        if not re.fullmatch(rf"specs/{epic}-[^/]+/spec\.md", path):
            return {"state": "invalid", **out}
        return {"state": "unchecked", **out}  # no merged spec to compare with

    # ── comments ─────────────────────────────────────────────────────────────

    def trusted(self, comment: dict[str, Any]) -> bool:
        user = comment.get("user") or {}
        login = str(user.get("login") or "")
        return (
            bool(self.po_login) and login == self.po_login
            or str(comment.get("author_association") or "") in ("OWNER", "MEMBER", "COLLABORATOR")
            or str(user.get("type") or "") == "Bot"
        )

    def refinement(self, comments: list[dict[str, Any]]) -> dict[str, Any]:
        latest = None
        for comment in comments:
            body = str(comment.get("body") or "")
            if self.trusted(comment) and "## Session Summary" in body and _REFINE_COMMAND.search(body):
                if latest is None or str(comment.get("created_at") or "") >= str(latest.get("created_at") or ""):
                    latest = comment
        if latest is None:
            return {"created_at": None, "refine_check": "none", "url": None}
        result = _CHECK_RESULT.search(str(latest.get("body") or ""))
        return {"created_at": latest.get("created_at"), "refine_check": result.group(1).lower() if result else "unreadable",
                "url": latest.get("html_url")}  # fmt: skip

    def decision_blocker(self, comments: list[dict[str, Any]]) -> dict[str, Any]:
        blocker = None
        for comment in comments:
            body = str(comment.get("body") or "")
            if self.trusted(comment) and "## Blocked Declaration" in body and "Type: decision" in body:
                if blocker is None or str(comment.get("created_at") or "") >= str(blocker.get("created_at") or ""):
                    blocker = comment
        if blocker is None:
            return {"state": "none", "created_at": None, "unblocked_at": None, "url": None}
        since = str(blocker.get("created_at") or "")
        times = [
            str(c.get("created_at") or "") for c in comments
            if self.po_login and (c.get("user") or {}).get("login") == self.po_login
            and str(c.get("created_at") or "") > since
            and any(_UNBLOCK_LINE.match(line) for line in str(c.get("body") or "").split("\n"))
        ]  # fmt: skip
        unblocked_at = max(times) if times else None
        return {"state": "unblocked" if unblocked_at else "open", "created_at": since or None,
                "unblocked_at": unblocked_at, "url": blocker.get("html_url")}  # fmt: skip

    # ── dependencies ─────────────────────────────────────────────────────────

    def dependencies(self, body: str, own: int) -> dict[str, Any]:
        numbers = hard_dependencies(parse_sections(body), own)
        if numbers is None:
            return {"state": "unreadable", "items": []}
        items = []
        for number in numbers:
            if number not in self._dependency:
                try:
                    self._dependency[number] = self.gw.issue_state(number)
                except GatewayError:
                    self._dependency[number] = "unresolvable"
            items.append({"number": number, "state": self._dependency[number]})
        return {"state": "read", "items": items}


def _label(labels: list[str], prefix: str) -> str | None:
    return next((label for label in labels if label.startswith(prefix)), None)


def _labels(issue: dict[str, Any]) -> list[str]:
    return [str(label.get("name")) for label in issue.get("labels") or [] if isinstance(label, dict) and label.get("name")]


def _match(pattern: re.Pattern[str], body: str) -> str | None:
    found = pattern.search(body)
    return found.group(1) if found else None


def _evaluate(facts: _Facts, issue: dict[str, Any], kind: str, epics: dict[int, dict[str, Any]],
              parent: int | None) -> dict[str, Any]:  # fmt: skip
    number = int(issue["number"])
    entry: dict[str, Any] = {
        "number": number, "title": issue.get("title"), "type": kind, "prio": _label(_labels(issue), "prio:"),
        "epic": parent, "feature": None, "criterion_prefix": None, "record": None, "refinement": None,
        "decision_blocker": None, "dependencies": None, "files_affected": None, "refined": False, "ready": False,
        "stale": False, "reasons": [],
    }  # fmt: skip
    if "body" not in issue:
        entry["reasons"] = ["unreadable: body"]
        return entry
    if parent is None:
        entry["reasons"] = ["no-parent"]
        return entry
    if parent not in epics:
        entry["reasons"] = ["parent-not-open-epic"]
        return entry
    body = str(issue.get("body") or "").replace("\r\n", "\n")
    group = _match(_FEATURE_GROUP, body)
    entry["feature"] = int(group) if group and kind in STORY_TYPES else None
    if kind == "feature":
        entry["criterion_prefix"] = (_match(_CRITERION_PREFIX, body) or "").upper() or None
    entry["files_affected"] = files_affected(body)
    spec = epics[parent]["spec"]
    try:
        entry["record"] = facts.record(body, parent, spec)
        comments = facts.gw.list_comments(number)
    except _Unreadable as exc:
        entry["reasons"] = [f"unreadable: {exc}"]
        return entry
    except GatewayError as exc:
        entry["reasons"] = [f"unreadable: {exc.step}"]
        return entry
    entry["refinement"] = facts.refinement(comments)
    entry["decision_blocker"] = facts.decision_blocker(comments)
    reasons: list[str] = []
    merged = spec["state"] == "merged"
    record = entry["record"]
    refined = True
    if merged and record["state"] != "current":
        refined = False
        reasons.append(f"record-{record['state']}")
    if entry["refinement"]["refine_check"] != "pass":
        refined = False
        reasons.append("no-passing-summary")
    blocker = entry["decision_blocker"]
    if blocker["state"] == "open":
        refined = False
        reasons.append("open-decision-blocker")
    elif blocker["state"] == "unblocked" and str(entry["refinement"]["created_at"] or "") <= blocker["unblocked_at"]:
        refined = False
        reasons.append("summary-before-unblock")
    entry["refined"] = refined
    entry["stale"] = merged and record["state"] in ("stale", "invalid")
    if kind in STORY_TYPES:
        entry["dependencies"] = facts.dependencies(body, number)
        deps = entry["dependencies"]
        if deps["state"] == "unreadable":
            reasons.append("dependencies-unreadable")
        for item in deps["items"]:
            if item["state"] == "open":
                reasons.append(f"blocked-by #{item['number']}")
            elif item["state"] != "closed":
                reasons.append(f"unresolvable #{item['number']}")
        entry["ready"] = refined and deps["state"] == "read" and all(i["state"] == "closed" for i in deps["items"])
    entry["reasons"] = reasons
    return entry


def read_facts(gateway: Gateway, repo: str, *, epic: int | None = None, env: Mapping[str, str] | None = None) -> Result:
    """Read the facts of every open Epic, Feature and story (or of one Epic and the issues under it)."""

    facts = _Facts(gateway, os.environ if env is None else env)
    try:
        return _read(facts, epic)
    except GatewayError as exc:
        log(f"[FACTS] unreadable: {exc}")
        return Result(OPERATION, "unreadable", error=str(exc), fields={"epics": [], "issues": []}, block=BLOCK)


def _read(facts: _Facts, only_epic: int | None) -> Result:
    open_issues = [i for i in facts.gw.list_open_issues() if isinstance(i.get("number"), int)]
    typed: list[tuple[dict[str, Any], str]] = []
    for issue in open_issues:
        kinds = [label.split(":", 1)[1] for label in _labels(issue) if label.startswith("type:")]
        kind = next((k for k in kinds if k in KNOWN_TYPES), None)
        if kind:
            typed.append((issue, kind))

    epics: dict[int, dict[str, Any]] = {}
    for issue, kind in typed:
        if kind != "epic" or (only_epic is not None and issue["number"] != only_epic):
            continue
        number = int(issue["number"])
        spec = facts.spec(number)
        epics[number] = {"number": number, "title": issue.get("title"), "prio": _label(_labels(issue), "prio:"),
                         "spec": spec, "record": None, "stale": False}  # fmt: skip
        body = str(issue.get("body") or "").replace("\r\n", "\n")
        try:
            record = facts.record(body, number, spec)
        except _Unreadable as exc:
            record = {"state": "unreadable", "path": None, "commit": None, "reason": f"unreadable: {exc}"}
        epics[number]["record"] = record
        epics[number]["stale"] = spec["state"] == "merged" and record["state"] in ("stale", "invalid")
        try:
            comments = facts.gw.list_comments(number)
        except GatewayError as exc:
            log(f"[FACTS] epic #{number} unreadable: {exc.step}")
            epics[number].update(refinement=None, decision_blocker=None)
        else:
            epics[number].update(refinement=facts.refinement(comments), decision_blocker=facts.decision_blocker(comments))

    issues: list[dict[str, Any]] = []
    for issue, kind in typed:
        if kind == "epic":
            continue
        body = str(issue.get("body") or "")
        parent = _match(_PARENT_EPIC, body)
        parent_number = int(parent) if parent else None
        if only_epic is not None and parent_number != only_epic:
            continue
        issues.append(_evaluate(facts, issue, kind, epics, parent_number))

    for number, entry in epics.items():
        if entry["spec"]["state"] != "merged":
            log(f"[FACTS] epic #{number} spec={entry['spec']['state']} expected specs/{number}-*/spec.md: {entry['spec']['reason']}")
    for entry in issues:
        flag = lambda value: "yes" if value else "no"  # noqa: E731
        log(f"[FACTS] #{entry['number']} {entry['type']} refined={flag(entry['refined'])} ready={flag(entry['ready'])} "
            f"stale={flag(entry['stale'])} {', '.join(entry['reasons'])}".rstrip())  # fmt: skip
    return Result(OPERATION, "read", fields={"epics": list(epics.values()), "issues": issues}, block=BLOCK)
