"""Parity baseline for the in-flight PR-fact rules (issue #571, Epic #519, Feature #525).

Each rule of this group in the ``docs/DISPATCHER-DECISIONS.md`` inventory has more than one
implementation. For every case below, one set of GitHub facts (open and closed PRs, the story,
its comments, the merge-gate line) goes through each copy of the rule, as its entry point calls
it, and the decision of each copy is recorded in ``BASELINE``. Shell copies run for real in bash
(``scripts/lib/*.sh`` sourced, ``scripts/dispatcher-invoke.sh`` functions extracted) against a
fake ``gh`` that, like the GitHub REST API, returns only the first 100 items of a list read
without ``--paginate``. Python copies get a fake gateway that raises ``GatewayError`` for a read
that fails.

``DIVERGENCES`` lists every case where the copies decide differently, with the resolution class
of the #525 divergence decision (``contract``, ``read defect`` or ``PO decision``). A case whose
copies agree must not be in it, and a divergent case must be. Nothing here changes a decision:
the tests only record today's behaviour, so #556 and #557 can show that consolidation keeps it.

Decisions are normalised per rule, as named in each rule's ``*_decisions`` function.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from headless_pr_workflow.dispatcher import gateway as gateway_mod
from headless_pr_workflow.dispatcher.codex_reserve import waiting_codex_prs
from headless_pr_workflow.dispatcher.codex_worker import _CLOSES, work_in_flight
from headless_pr_workflow.dispatcher.facts import _Facts
from headless_pr_workflow.dispatcher.gateway import GatewayError, GhGateway
from headless_pr_workflow.dispatcher.session_blocker import open_session_blocker, read_session_blocker
from headless_pr_workflow.dispatcher.status import _linked, derive_status, other_blocker_open
from headless_pr_workflow.dispatcher.write import DEFAULT_PO_LOGIN, _po_unblocked_after
from tests.dispatcher_fakes import BASH, REPO_ROOT, needs_tools
from tests.script_tools import windows_env

pytestmark = needs_tools

REPO = "owner/repo"
STORY = 500
PR = 900
HEAD = "a" * 40
BOT, BK, PO, MEMBER = "hpw-bot", "hpw-bookkeeping", "po-login", "member-login"
T0, T1, T_EARLY = "2026-11-10T10:00:00Z", "2026-11-10T11:00:00Z", "2026-11-10T09:00:00Z"
INVOKE_TEXT = (REPO_ROOT / "scripts" / "dispatcher-invoke.sh").read_text(encoding="utf-8")
POLL_TEXT = (REPO_ROOT / "scripts" / "dispatcher-poll.sh").read_text(encoding="utf-8")
LIB = REPO_ROOT / "scripts" / "lib"
RESOLUTION_CLASSES = ("contract", "read defect", "PO decision")
MERGE_GATE = gateway_mod._merge_gate_module()  # the real scripts/merge-gate-summary, loaded before any stub


# ── Facts ────────────────────────────────────────────────────────────────────


def pr(number: int = PR, body: str = f"Closes #{STORY}", *, draft: bool = False, ref: str = "claude/issue-500-x",
       mergeable: bool | None = True, merged_at: str | None = None) -> dict[str, Any]:  # fmt: skip
    return {"number": number, "state": "open" if merged_at is None else "closed", "body": body, "draft": draft,
            "head": {"sha": HEAD, "ref": ref}, "mergeable": mergeable,
            "mergeable_state": "dirty" if mergeable is False else "clean", "merged_at": merged_at}  # fmt: skip


def fillers(count: int = 100, start: int = 2000) -> list[dict[str, Any]]:
    """PRs linked to another story, listed before the story's own PR (newest first)."""

    return [pr(start + i, "Closes #1") for i in range(count)]


def comment(author: str, body: str, at: str = T0, association: str = "NONE", user_type: str = "User") -> dict[str, Any]:
    return {"user": {"login": author, "type": user_type}, "author_association": association, "body": body,
            "created_at": at, "html_url": f"https://github.com/{REPO}/issues/{STORY}#c-{at}"}  # fmt: skip


def declaration(kind: str, author: str = PO, at: str = T0, declared_by: str = "PO", **extra: Any) -> dict[str, Any]:
    body = f"## Blocked Declaration\n\nType: {kind}\nDeclared by: {declared_by}\nReason: waiting\n"
    return comment(author, body, at, **extra)


def gate_line(mergeable: str = "yes", threads: str = "none", head: str = HEAD[:7]) -> str:
    return (f"Merge gate: policy=pass(required), checks=pass, approval=formal, threads={threads}, "
            f"mergeable={mergeable}, head={head}")  # fmt: skip


def facts(*, open_prs: list | None = None, closed: list | None = None, comments: list | None = None,
          gates: dict[int, str] | None = None, fail: tuple[str, ...] = ()) -> dict[str, Any]:  # fmt: skip
    """``fail`` names the reads that fail: ``open`` (open PRs), ``closed`` (closed PRs), ``comments``, ``gate``."""

    return {"open": open_prs or [], "closed": closed or [], "comments": comments or [], "gates": gates or {},
            "fail": set(fail)}  # fmt: skip


class FakeGateway:
    """The reads of ``Gateway`` the Python copies use; a failing read raises ``GatewayError``."""

    def __init__(self, f: dict[str, Any]) -> None:
        self.f = f

    def list_open_prs(self) -> list[dict[str, Any]]:
        if "open" in self.f["fail"]:
            raise GatewayError("read open pull requests", "HTTP 502")
        return list(self.f["open"])

    def list_comments(self, number: int) -> list[dict[str, Any]]:
        if "comments" in self.f["fail"]:
            raise GatewayError(f"read comments of #{number}", "HTTP 502")
        return list(self.f["comments"]) if number == STORY else []

    def pr_reviews(self, number: int) -> list[dict[str, Any]]:
        return []


# ── Running the shell copies ─────────────────────────────────────────────────

FAKE_GH = r'''
gh() {
    local path="" a
    for a in "$@"; do [[ "$a" == repos/* ]] && path="$a"; done
    case "$path" in
        *"pulls?state=open"*) [[ -f "$FACTS/fail-open" ]] && return 1; jq -c '.[0:100]' "$FACTS/open.json" ;;
        *"pulls?state=closed"*) [[ -f "$FACTS/fail-closed" ]] && return 1; jq -c '.[0:100]' "$FACTS/closed.json" ;;
        */comments*) [[ -f "$FACTS/fail-comments" ]] && return 1; cat "$FACTS/comments.json" ;;
        *) echo "fake gh: unsupported $path" >&2; return 1 ;;
    esac
}
inflight_get_all() {
    case "$1" in
        *"pulls?state=open"*) [[ -f "$FACTS/fail-open" ]] && return 1; cat "$FACTS/open.json" ;;
        *"issues?state=open"*) cat "$FACTS/issues.json" ;;
        *"issues/'"$STORY"'/comments"*) [[ -f "$FACTS/fail-comments" ]] && return 1; cat "$FACTS/comments.json" ;;
        */comments*) echo '[]' ;;
    esac
}
inflight_get_page() { [[ -f "$FACTS/fail-closed" ]] && return 1; jq -c '.[0:100]' "$FACTS/closed.json"; }
'''.replace("'\"$STORY\"'", str(STORY))

FAKE_GATE = """#!/usr/bin/env bash
[[ -f "$FACTS/fail-gate" ]] && { echo "merge-gate-summary: unreadable" >&2; exit 2; }
[[ -f "$FACTS/gate-$2.txt" ]] || exit 2
cat "$FACTS/gate-$2.txt"
"""


def _function(name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", INVOKE_TEXT, re.MULTILINE | re.DOTALL)
    assert match, f"{name} not found in scripts/dispatcher-invoke.sh"
    return match.group(0)


def _literal(text: str, name: str) -> str:
    match = re.search(rf'^{name}="([^"]*)"$', text, re.MULTILINE)
    assert match, name
    return match.group(1)


def bash(tmp: Path, f: dict[str, Any], body: str, *, stdin: str | None = None) -> tuple[int, str]:
    """Run ``body`` in bash after writing the facts to files under ``tmp``; ``(exit code, stdout)``."""

    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "open.json").write_text(json.dumps(f["open"]), encoding="utf-8")
    (tmp / "closed.json").write_text(json.dumps(f["closed"]), encoding="utf-8")
    (tmp / "comments.json").write_text(json.dumps(f["comments"]), encoding="utf-8")
    (tmp / "issues.json").write_text(json.dumps([{
        "number": STORY, "title": "Story", "comments": len(f["comments"]),
        "labels": [{"name": "type:story"}, {"name": "executor:claude-code-opus"}]}]), encoding="utf-8")  # fmt: skip
    for name in ("open", "closed", "comments", "gate"):
        marker = tmp / f"fail-{name}"
        marker.unlink(missing_ok=True)
        if name in f["fail"]:
            marker.write_text("", encoding="utf-8")
    for number, line in f["gates"].items():
        (tmp / f"gate-{number}.txt").write_text(line + "\n", encoding="utf-8")
    gate = tmp / "gate.sh"
    gate.write_text(FAKE_GATE, encoding="utf-8", newline="\n")
    gate.chmod(0o755)
    hpw = tmp / "hpw"
    hpw.write_text("#!/usr/bin/env bash\n"
                   f'PYTHONPATH="{(REPO_ROOT / "src").as_posix()}" exec "{Path(sys.executable).as_posix()}" '
                   "-c 'import sys; from headless_pr_workflow.cli import main; sys.exit(main())' \"$@\"\n",
                   encoding="utf-8", newline="\n")  # fmt: skip
    hpw.chmod(0o755)
    script = "\n".join([
        "set -uo pipefail", f'export FACTS="{tmp.as_posix()}"', f'REPO="{REPO}"', f'HPW_BIN="{hpw.as_posix()}"',
        f'MERGE_GATE_SCRIPT="{gate.as_posix()}"', f'INFLIGHT_BOT="{BOT}"', f'INFLIGHT_BK="{BK}"',
        f'INFLIGHT_PO="{PO}"', "declare -A CODEX_REVIEW_LOOP_ROUTING=()", FAKE_GH,
        f'source "{(LIB / "in-flight.sh").as_posix()}"', f'source "{(LIB / "conflict-round.sh").as_posix()}"',
        body])  # fmt: skip
    proc = subprocess.run([BASH, "-c", script], input=stdin, capture_output=True, text=True,
                          env=windows_env(), timeout=120, check=False)  # fmt: skip
    return proc.returncode, proc.stdout.strip()


def numbers(prs: list[dict[str, Any]]) -> str:
    return ",".join(f"#{p['number']}" for p in prs) or "none"


def inflight(tmp: Path, f: dict[str, Any]) -> dict[str, Any] | None:
    """``inflight_compute`` (poll and resume) with ``plan_in_flight`` behind it; ``None`` when it returns 1."""

    rc, out = bash(tmp, f, 'inflight_compute && printf "%s" "$INFLIGHT_JSON"')
    return json.loads(out) if rc == 0 else None


def story_category(plan: dict[str, Any] | None) -> str:
    if plan is None:
        return "unreadable"
    if STORY in plan["in_implementation"]:
        return "implementing"
    return "in flight" if STORY in plan["in_flight"] else "none"


def gate_result(line: str | None) -> Any:
    """``GateResult`` of scripts/merge-gate-summary, built from its own gate line."""

    if line is None:
        return None
    values = dict(part.split("=", 1) for part in line.removeprefix("Merge gate: ").split(", "))
    return MERGE_GATE.GateResult(**values)


def gateway_gates(f: dict[str, Any], number: int, monkeypatch: pytest.MonkeyPatch) -> bool:
    """``GhGateway.merge_gates_pass`` with the evaluation stubbed; a raise counts as not passing, as in status.py."""

    def evaluate(n: int, repo: str) -> Any:
        if "gate" in f["fail"] or number not in f["gates"]:
            raise ValueError("merge-gate-summary: unreadable")
        return gate_result(f["gates"][number])

    with monkeypatch.context() as m:
        m.setattr(gateway_mod, "_merge_gate_module", lambda: SimpleNamespace(evaluate=evaluate))
        try:
            return GhGateway(REPO).merge_gates_pass(number, HEAD)
        except GatewayError:
            return False


def status_decision(f: dict[str, Any], monkeypatch: pytest.MonkeyPatch, po: str = PO) -> str:
    """``derive_status`` with the facts ``read_status`` collects for one refined story in an Epic."""

    gateway = FakeGateway(f)
    try:
        prs = gateway.list_open_prs()
        comments = gateway.list_comments(STORY)
    except GatewayError:
        return "unreadable"
    helper = _Facts(gateway, {"DECISION_BLOCKER_PO_LOGIN": po})
    linked = [{"number": p["number"], "draft": bool(p["draft"]),
               "gates_pass": False if p["draft"] else gateway_gates(f, p["number"], monkeypatch)}
              for p in prs if _linked(str(p.get("body") or ""), STORY)]  # fmt: skip
    blocked = helper.decision_blocker(comments)["state"] == "open" or other_blocker_open(helper, comments)
    status, _ = derive_status({"type": "story", "state": "open", "unreadable": None, "blocked": blocked,
                               "refined": True, "ready": True, "parent_open_epic": True, "spec_merged": True,
                               "spec_pr": False, "prs": linked})  # fmt: skip
    return status


# ── Rule: linked_pr_lookup ───────────────────────────────────────────────────


def linked_pr_lookup_decisions(f: dict[str, Any], tmp: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """The linked PR(s) each copy finds for the story: ``#n[,#m]``, ``none`` or ``unreadable``."""

    def shell(name: str, body: str) -> str:
        rc, out = bash(tmp / name, f, body)
        return "unreadable" if rc else (",".join(f"#{n}" for n in out.split()) or "none")

    def python(match: Callable[[str], bool]) -> str:
        try:
            prs = FakeGateway(f).list_open_prs()
        except GatewayError:
            return "unreadable"
        return numbers([p for p in prs if match(str(p.get("body") or ""))])

    closes = re.compile(_CLOSES.format(n=STORY), re.IGNORECASE)
    return {
        "in-flight.sh:INFLIGHT_JQ_LINKED": shell("jq", f'prs=$(inflight_get_all "repos/$REPO/pulls?state=open") || exit 1\n'
                                                       f'jq -r --argjson n {STORY} "$INFLIGHT_JQ_LINKED"\'linked(.; $n) | .number // empty\' <<< "$prs"'),
        "dispatcher-invoke.sh:find_linked_pr_json": shell("json", _function("find_linked_pr_json")
                                                          + f"find_linked_pr_json {STORY} | jq -r '.number'"),
        "dispatcher-invoke.sh:find_linked_pr": shell("plain", _function("find_linked_pr") + f"find_linked_pr {STORY}"),
        "status.py:_linked": python(lambda body: _linked(body, STORY)),
        "codex_worker.py:_CLOSES": python(lambda body: bool(closes.search(body))),
    }  # fmt: skip


# ── Rule: merged_pr_finish ───────────────────────────────────────────────────


def merged_pr_finish_decisions(f: dict[str, Any], tmp: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Whether the story goes to the finish step: ``finish #n``, ``no finish`` or ``unreadable``."""

    plan = inflight(tmp / "inflight", f)
    finish = [e for e in (plan or {}).get("finish", []) if e["number"] == STORY]
    rc, out = bash(tmp / "invoke", f, _function("find_linked_pr_json") + _function("find_merged_linked_pr")
                   + f'open=$(find_linked_pr_json {STORY}) || exit 3\n[[ -z "$open" ]] || exit 0\n'
                   + f"find_merged_linked_pr {STORY}")  # fmt: skip
    invoke = "unreadable" if rc == 3 else (f"finish #{out}" if out else "no finish")
    merged = [p for p in f["closed"][:100] if p["merged_at"] and _linked(p["body"], STORY)]
    return {
        "in_flight.py:plan_in_flight + in-flight.sh:inflight_compute": (
            "unreadable" if plan is None else f"finish #{merged[0]['number']}" if finish else "no finish"),
        "dispatcher-invoke.sh:find_merged_linked_pr": invoke,
    }


# ── Rule: merge_gates_pass ───────────────────────────────────────────────────


def merge_gates_pass_decisions(f: dict[str, Any], tmp: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Whether the merge gates pass on the PR's current head: ``pass`` or ``fail``."""

    line = None if "gate" in f["fail"] else f["gates"].get(PR)
    result = gate_result(line)
    owner = result is not None and result.passed and HEAD.startswith(result.head.lower())
    rc, _ = bash(tmp, f, f"inflight_gates_pass {PR} {HEAD}")
    word = {True: "pass", False: "fail"}
    return {
        "merge-gate-summary:GateResult": word[bool(owner)],
        "gateway.py:merge_gates_pass": word[gateway_gates(f, PR, monkeypatch)],
        "in-flight.sh:inflight_gates_pass": word[rc == 0],
    }


# ── Rule: in_flight_step ─────────────────────────────────────────────────────


def in_flight_step_decisions(f: dict[str, Any], tmp: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """The story's scheduler list: ``implementing``, ``in flight``, ``none`` or ``unreadable``.

    ``derive_status``: In implementation → implementing; In review, Mergeable or Blocked → in flight.
    """

    try:
        flight = work_in_flight(FakeGateway(f), {"issues": [{"number": STORY, "type": "story"}]},  # type: ignore[arg-type]
                                {"DECISION_BLOCKER_PO_LOGIN": PO})  # fmt: skip
        codex = "in flight" if STORY in flight else "none"
    except GatewayError:
        codex = "unreadable"
    status = status_decision(f, monkeypatch)
    status = {"In implementation": "implementing", "In review": "in flight", "Mergeable": "in flight",
              "Blocked": "in flight", "unreadable": "unreadable"}.get(status, "none")  # fmt: skip
    return {
        "in_flight.py:plan_in_flight + in-flight.sh:inflight_compute": story_category(inflight(tmp, f)),
        "codex_worker.py:work_in_flight": codex,
        "status.py:derive_status": status,
    }


# ── Rule: draft_ready_signal ─────────────────────────────────────────────────


def draft_ready_signal_decisions(f: dict[str, Any], tmp: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """What the PR's draft state signals: ``draft`` (implementing) or ``ready`` (review, merge or queue).

    ``conflict_round_rule``: ``not-open`` → draft, any other word → ready. ``waiting_codex_prs``: the PR is
    counted → ready, not counted → draft.
    """

    plan = story_category(inflight(tmp / "inflight", f))
    status = status_decision(f, monkeypatch)
    _, word = bash(tmp / "conflict", f, f'conflict_round_rule "{BOT}" "{BK}" "{PO}"',
                   stdin=json.dumps(f["open"][-1]) + "\n[]\n[]\n")  # fmt: skip
    queue = waiting_codex_prs(FakeGateway(f))  # type: ignore[arg-type]
    return {
        "in_flight.py:plan_in_flight": {"implementing": "draft", "in flight": "ready"}.get(plan, plan),
        "status.py:derive_status": "draft" if status == "In implementation" else "ready",
        "conflict-round.sh:not-open": "draft" if word == "not-open" else "ready",
        "codex_reserve.py:waiting_codex_prs": "ready" if queue else "draft",
    }


# ── Rule: open_blocker ───────────────────────────────────────────────────────


def open_blocker_decisions(f: dict[str, Any], tmp: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Whether the story has an open Blocked Declaration: ``open``, ``not open`` or ``unreadable``.

    ``other_blocker_open`` runs as the in-flight plan and the board status call it
    (both ``po_unblock=True``). ``conflict-round.sh:open_blocker`` runs inside ``conflict_round_rule`` for
    a conflicting PR with no attempt marker: ``blocked`` → open, ``owed`` → not open. A copy whose caller
    reads the comments itself records what that caller does when the read fails.
    """

    word = {True: "open", False: "not open"}
    env = {"DECISION_BLOCKER_PO_LOGIN": PO}
    session = read_session_blocker(FakeGateway(f), STORY, env=env)  # type: ignore[arg-type]
    rc, out = bash(tmp / "decision", f, _function("latest_decision_blocker_json")
                   + f'DECISION_BLOCKER_PO_LOGIN="{PO}"\nlatest_decision_blocker_json {STORY}')  # fmt: skip
    decision = "unreadable" if rc else word[bool(out) and json.loads(out)["unblocked"] is False]
    if "comments" in f["fail"]:
        # inflight_compute logs a warning and checks no blocker; the board status shows unknown;
        # the conflict round and the facts read their comments in their own callers.
        plan = story_category(inflight(tmp / "inflight", f))
        return {"status.py:other_blocker_open (in-flight plan)": "unreadable" if plan == "unreadable" else word[plan == "in flight"],
                "status.py:other_blocker_open (board status)": status_decision(f, monkeypatch),
                "session_blocker.py:open_session_blocker": session.result,
                "dispatcher-invoke.sh:latest_decision_blocker_json": decision}  # fmt: skip
    comments = f["comments"]
    trust = _Facts(FakeGateway(f), env)  # type: ignore[arg-type]
    conflicting = json.dumps(pr(mergeable=False))
    _, rule = bash(tmp / "conflict", f, f'conflict_round_rule "{BOT}" "{BK}" "{PO}"',
                   stdin=f"{conflicting}\n{json.dumps(comments)}\n[]\n")  # fmt: skip
    return {
        "status.py:other_blocker_open (in-flight plan)": word[other_blocker_open(trust, comments, po_unblock=True)],
        "status.py:other_blocker_open (board status)": word[other_blocker_open(trust, comments, po_unblock=True)],
        "conflict-round.sh:open_blocker": {"blocked": "open", "owed": "not open"}.get(rule, rule),
        "session_blocker.py:open_session_blocker": word[open_session_blocker(trust, comments) is not None],
        "facts.py:dependency_blocker": word[trust.dependency_blocker(comments, {})["state"] == "open"],
        "dispatcher-invoke.sh:latest_decision_blocker_json": decision,
    }


# ── Rule: po_unblock ─────────────────────────────────────────────────────────


def po_unblock_decisions(f: dict[str, Any], tmp: Path, monkeypatch: pytest.MonkeyPatch, *,
                         default_logins: bool = False) -> dict[str, str]:  # fmt: skip
    """Whether a PO ``/unblock`` after the declaration at ``T0`` lifts it: ``lifted`` or ``not lifted``.

    ``conflict-round.sh:unblocked_after`` runs inside ``conflict_round_rule``: a conflicting PR with an attempt
    marker at ``T0`` is ``tried`` while the marker holds and ``owed`` once it is lifted. ``facts.py:_UNBLOCK_LINE``
    runs inside ``_Facts.decision_blocker``. With ``default_logins`` each copy uses its entry point's default PO
    login instead of an explicit one: the poll's ``PO_LOGIN``, the invoke script's ``DECISION_BLOCKER_PO_LOGIN``,
    ``write.py``'s ``DEFAULT_PO_LOGIN``, and an empty environment for ``_Facts`` (the Codex worker's).
    """

    word = {True: "lifted", False: "not lifted"}
    poll_po = _literal(POLL_TEXT, "PO_LOGIN") if default_logins else PO
    invoke_po = _literal(INVOKE_TEXT, "DECISION_BLOCKER_PO_LOGIN") if default_logins else PO
    write_po = DEFAULT_PO_LOGIN if default_logins else PO
    env = {} if default_logins else {"DECISION_BLOCKER_PO_LOGIN": PO}
    comments = f["comments"]
    _, rule = bash(tmp / "conflict", f, f'conflict_round_rule "{BOT}" "{BK}" "{poll_po}"',
                   stdin=f"{json.dumps(pr(mergeable=False))}\n{json.dumps(comments)}\n[]\n")  # fmt: skip
    rc, out = bash(tmp / "decision", f, _function("latest_decision_blocker_json")
                   + f'DECISION_BLOCKER_PO_LOGIN="{invoke_po}"\nlatest_decision_blocker_json {STORY}')  # fmt: skip
    return {
        "write.py:_po_unblocked_after": word[_po_unblocked_after(comments, write_po, T0)],
        "conflict-round.sh:unblocked_after": {"owed": "lifted", "tried": "not lifted"}.get(rule, rule),
        "facts.py:_UNBLOCK_LINE": word[_Facts(FakeGateway(f), env).decision_blocker(comments)["state"] == "unblocked"],  # type: ignore[arg-type]
        "dispatcher-invoke.sh:latest_decision_blocker_json": "unreadable" if rc else word[json.loads(out)["unblocked"]],
    }


def unblock_facts(body: str, author: str = PO, at: str = T1, association: str = "OWNER") -> dict[str, Any]:
    """A PO decision declaration and the dispatcher's conflict-round attempt marker at ``T0``, then ``body``."""

    return facts(comments=[
        declaration("decision", author=PO if author != "LauraMardones" else author, association="OWNER"),
        comment(BOT, f"<!-- hpw:dispatcher action=conflict-round-attempt pr={PR} head={HEAD} -->", user_type="Bot"),
        comment(author, body, at, association=association)])  # fmt: skip


# ── Cases and the recorded baseline ──────────────────────────────────────────

LINKED = "in-flight.sh:INFLIGHT_JQ_LINKED", "dispatcher-invoke.sh:find_linked_pr_json", "dispatcher-invoke.sh:find_linked_pr", "status.py:_linked", "codex_worker.py:_CLOSES"  # fmt: skip


def same(copies: tuple[str, ...], decision: str) -> dict[str, str]:
    return dict.fromkeys(copies, decision)


ON_101 = fillers() + [pr()]
CASES: dict[str, dict[str, tuple[dict[str, Any], dict[str, str]]]] = {
    "linked_pr_lookup": {
        "closes": (facts(open_prs=[pr()]), same(LINKED, "#900")),
        "fixes-lower-case": (facts(open_prs=[pr(body="fixes #500")]), same(LINKED, "#900")),
        "other-number": (facts(open_prs=[pr(body="Closes #5000")]), same(LINKED, "none")),
        "closed-past-tense": (facts(open_prs=[pr(body="Closed #500")]),
                              {**same(LINKED, "none"), "codex_worker.py:_CLOSES": "#900"}),
        "two-linked-prs": (facts(open_prs=[pr(), pr(901)]),
                           {**same(LINKED[:3], "#900"), **same(LINKED[3:], "#900,#901")}),
        "more-than-100-open-prs": (facts(open_prs=ON_101), {**same(LINKED, "#900"), **same(LINKED[1:3], "none")}),
        "open-prs-unreadable": (facts(fail=("open",)), same(LINKED, "unreadable")),
    },
    "merged_pr_finish": {
        "merged": (facts(closed=[pr(800, merged_at=T0)]), {"in_flight.py:plan_in_flight + in-flight.sh:inflight_compute": "finish #800",
                                                           "dispatcher-invoke.sh:find_merged_linked_pr": "finish #800"}),
        "closed-unmerged": (facts(closed=[pr(800) | {"state": "closed"}]), {"in_flight.py:plan_in_flight + in-flight.sh:inflight_compute": "no finish",
                                                                          "dispatcher-invoke.sh:find_merged_linked_pr": "no finish"}),
        "open-and-merged": (facts(open_prs=[pr()], closed=[pr(800, merged_at=T0)]),
                            {"in_flight.py:plan_in_flight + in-flight.sh:inflight_compute": "no finish",
                             "dispatcher-invoke.sh:find_merged_linked_pr": "no finish"}),
        "more-than-100-open-prs": (facts(open_prs=ON_101, closed=[pr(800, merged_at=T0)]),
                                   {"in_flight.py:plan_in_flight + in-flight.sh:inflight_compute": "no finish",
                                    "dispatcher-invoke.sh:find_merged_linked_pr": "finish #800"}),
        "merged-beyond-100-closed": (facts(closed=fillers() + [pr(800, merged_at=T0)]),
                                     {"in_flight.py:plan_in_flight + in-flight.sh:inflight_compute": "no finish",
                                      "dispatcher-invoke.sh:find_merged_linked_pr": "no finish"}),
        "closed-prs-unreadable": (facts(closed=[pr(800, merged_at=T0)], fail=("closed",)),
                                  {"in_flight.py:plan_in_flight + in-flight.sh:inflight_compute": "no finish",
                                   "dispatcher-invoke.sh:find_merged_linked_pr": "no finish"}),
        "open-prs-unreadable": (facts(closed=[pr(800, merged_at=T0)], fail=("open",)),
                                {"in_flight.py:plan_in_flight + in-flight.sh:inflight_compute": "unreadable",
                                 "dispatcher-invoke.sh:find_merged_linked_pr": "unreadable"}),
    },
    "merge_gates_pass": {
        "all-pass": (facts(gates={PR: gate_line()}), {"merge-gate-summary:GateResult": "pass", "gateway.py:merge_gates_pass": "pass",
                                                     "in-flight.sh:inflight_gates_pass": "pass"}),
        "not-mergeable": (facts(gates={PR: gate_line("no")}), {"merge-gate-summary:GateResult": "fail", "gateway.py:merge_gates_pass": "fail",
                                                              "in-flight.sh:inflight_gates_pass": "pass"}),
        "mergeability-unknown": (facts(gates={PR: gate_line("unknown")}), {"merge-gate-summary:GateResult": "fail",
                                                                          "gateway.py:merge_gates_pass": "fail",
                                                                          "in-flight.sh:inflight_gates_pass": "pass"}),
        "unresolved-thread": (facts(gates={PR: gate_line(threads="1")}), {"merge-gate-summary:GateResult": "fail",
                                                                         "gateway.py:merge_gates_pass": "fail",
                                                                         "in-flight.sh:inflight_gates_pass": "fail"}),
        "other-head": (facts(gates={PR: gate_line(head="bbbbbbb")}), {"merge-gate-summary:GateResult": "fail",
                                                                     "gateway.py:merge_gates_pass": "fail",
                                                                     "in-flight.sh:inflight_gates_pass": "fail"}),
        "gates-unreadable": (facts(gates={PR: gate_line()}, fail=("gate",)), {"merge-gate-summary:GateResult": "fail",
                                                                             "gateway.py:merge_gates_pass": "fail",
                                                                             "in-flight.sh:inflight_gates_pass": "fail"}),
    },
}

STEP = "in_flight.py:plan_in_flight + in-flight.sh:inflight_compute", "codex_worker.py:work_in_flight", "status.py:derive_status"  # fmt: skip
DEPENDENCY = [declaration("dependency", association="OWNER")]
CASES["in_flight_step"] = {
    "draft-pr": (facts(open_prs=[pr(draft=True)]), {**same(STEP, "implementing"), STEP[1]: "in flight"}),
    "ready-gates-fail": (facts(open_prs=[pr()], gates={PR: gate_line(threads="1")}), same(STEP, "in flight")),
    "ready-gates-pass": (facts(open_prs=[pr()], gates={PR: gate_line()}), same(STEP, "in flight")),
    "draft-first-then-gated-pr": (facts(open_prs=[pr(draft=True), pr(901)], gates={901: gate_line()}),
                                  {**same(STEP, "in flight"), STEP[0]: "implementing"}),
    "dependency-blocker-no-pr": (facts(comments=DEPENDENCY), same(STEP, "in flight")),
    "no-pr-no-blocker": (facts(), same(STEP, "none")),
    "comments-unreadable": (facts(comments=DEPENDENCY, fail=("comments",)), {**same(STEP, "none"), STEP[2]: "unreadable"}),
    "open-prs-unreadable": (facts(open_prs=[pr()], fail=("open",)), same(STEP, "unreadable")),
}

SIGNAL = "in_flight.py:plan_in_flight", "status.py:derive_status", "conflict-round.sh:not-open", "codex_reserve.py:waiting_codex_prs"  # fmt: skip
CASES["draft_ready_signal"] = {
    "codex-draft": (facts(open_prs=[pr(draft=True, ref="codex/issue-500-x")]), same(SIGNAL, "draft")),
    "codex-ready": (facts(open_prs=[pr(ref="codex/issue-500-x")], gates={PR: gate_line(threads="1")}), same(SIGNAL, "ready")),
    "codex-ready-mergeability-unknown": (facts(open_prs=[pr(ref="codex/issue-500-x", mergeable=None)]), same(SIGNAL, "ready")),
    "ready-pr-on-claude-branch": (facts(open_prs=[pr()]), {**same(SIGNAL, "ready"), SIGNAL[3]: "draft"}),
}

BLOCKER = ("status.py:other_blocker_open (in-flight plan)", "status.py:other_blocker_open (board status)",
           "conflict-round.sh:open_blocker", "session_blocker.py:open_session_blocker", "facts.py:dependency_blocker",
           "dispatcher-invoke.sh:latest_decision_blocker_json")  # fmt: skip
UNBLOCKED_BY_MEMBER = comment(MEMBER, "Unblocked: the dependency is closed.", T1, association="MEMBER")
CASES["open_blocker"] = {
    "po-dependency": (facts(comments=DEPENDENCY), {**same(BLOCKER, "open"), BLOCKER[5]: "not open"}),
    "po-decision": (facts(comments=[declaration("decision", association="OWNER")]),
                    {**same(BLOCKER, "open"), BLOCKER[0]: "not open", BLOCKER[1]: "not open", BLOCKER[4]: "not open"}),
    "dependency-then-po-unblock": (facts(comments=DEPENDENCY + [comment(PO, "/unblock", T1, association="OWNER")]),
                                   same(BLOCKER, "not open")),
    "dependency-then-member-unblocked": (facts(comments=DEPENDENCY + [UNBLOCKED_BY_MEMBER]),
                                         {**same(BLOCKER, "not open"), BLOCKER[2]: "open"}),
    "automation-dependency": (facts(comments=[declaration("dependency", author=BOT, declared_by="dispatcher", user_type="Bot")]),
                              {**same(BLOCKER, "open"), BLOCKER[3]: "not open", BLOCKER[5]: "not open"}),
    "comments-unreadable": (facts(comments=DEPENDENCY, fail=("comments",)),
                            {BLOCKER[0]: "not open", BLOCKER[1]: "unreadable", BLOCKER[3]: "unreadable", BLOCKER[5]: "unreadable"}),
}

UNBLOCK = "write.py:_po_unblocked_after", "conflict-round.sh:unblocked_after", "facts.py:_UNBLOCK_LINE", "dispatcher-invoke.sh:latest_decision_blocker_json"  # fmt: skip
CASES["po_unblock"] = {
    "standalone-line": (unblock_facts("Go ahead.\n/unblock"), same(UNBLOCK, "lifted")),
    "padded-line": (unblock_facts("  /unblock  "), same(UNBLOCK, "lifted")),
    "in-a-sentence": (unblock_facts("Please /unblock this."), same(UNBLOCK, "not lifted")),
    "upper-case": (unblock_facts("/UNBLOCK"), same(UNBLOCK, "not lifted")),
    "by-a-member": (unblock_facts("/unblock", author=MEMBER, association="MEMBER"), same(UNBLOCK, "not lifted")),
    "before-the-declaration": (unblock_facts("/unblock", at=T_EARLY), same(UNBLOCK, "not lifted")),
    "default-po-login": (unblock_facts("/unblock", author="LauraMardones"), {**same(UNBLOCK, "lifted"), UNBLOCK[2]: "not lifted"}),
}

DECIDE: dict[str, Callable[..., dict[str, str]]] = {
    "linked_pr_lookup": linked_pr_lookup_decisions, "merged_pr_finish": merged_pr_finish_decisions,
    "merge_gates_pass": merge_gates_pass_decisions, "in_flight_step": in_flight_step_decisions,
    "draft_ready_signal": draft_ready_signal_decisions, "open_blocker": open_blocker_decisions,
    "po_unblock": po_unblock_decisions,
}  # fmt: skip

# Every case whose copies decide differently, with its resolution class under the #525 divergence decision.
DIVERGENCES: dict[tuple[str, str], tuple[str, str]] = {
    ("linked_pr_lookup", "closed-past-tense"): (
        "PO decision", "Which closing keywords link a PR is not in a documented contract: keep both copies on the "
                       "allowlist until the PO decides between Closes/Fixes/Resolves and GitHub's full keyword list."),
    ("linked_pr_lookup", "two-linked-prs"): (
        "PO decision", "No contract says which of two open PRs for one story counts; the jq copies take the newest, "
                       "the Python copies keep all. Needs a PO decision."),
    ("linked_pr_lookup", "more-than-100-open-prs"): (
        "read defect", "find_linked_pr_json and find_linked_pr read one page: follow the paged read of INFLIGHT_JQ_LINKED."),
    ("merged_pr_finish", "more-than-100-open-prs"): (
        "read defect", "The invoke copy misses the open PR past the first page and finishes a story with an open PR: "
                       "follow inflight_compute's paged read."),
    ("merge_gates_pass", "not-mergeable"): (
        "PO decision", "inflight_gates_pass leaves mergeability to the merge step by design (in-flight.sh), "
                       "GateResult needs mergeable=yes (MERGE-POLICY.md); whether the board shows such a PR In review "
                       "while the resume pass routes it to the merge step needs a PO decision."),
    ("merge_gates_pass", "mergeability-unknown"): ("PO decision", "As not-mergeable, for mergeable=unknown."),
    ("in_flight_step", "draft-pr"): (
        "contract", "PROJECT-STATUS.md counts a draft linked PR as In implementation for the WIP limit: "
                    "work_in_flight follows plan_in_flight and reports a draft as in implementation."),
    ("in_flight_step", "draft-first-then-gated-pr"): (
        "PO decision", "Follows linked_pr_lookup two-linked-prs: which PR decides the step needs a PO decision."),
    ("in_flight_step", "comments-unreadable"): (
        "read defect", "inflight_compute and work_in_flight treat unreadable comments as no blocker and may start a "
                       "blocked story: follow the fail-closed read of derive_status (unreadable)."),
    ("draft_ready_signal", "ready-pr-on-claude-branch"): (
        "contract", "WORKTREE-MODEL.md names the branch by agent, so the codex/ prefix is the documented sign of a "
                    "Codex PR; the reserve counts only Codex PRs and keeps its copy."),
    ("open_blocker", "po-dependency"): (
        "contract", "PROJECT-STATUS.md gives each Blocked Declaration type its own rule; latest_decision_blocker_json "
                    "reads only decision blockers. The consolidated rule takes the type as an input."),
    ("open_blocker", "po-decision"): (
        "contract", "As po-dependency: other_blocker_open and dependency_blocker read no decision blocker by contract."),
    ("open_blocker", "dependency-then-member-unblocked"): (
        "contract", "PROJECT-STATUS.md resolves a non-decision blocker with an Unblocked comment: the conflict-round "
                    "open_blocker follows other_blocker_open."),
    ("open_blocker", "automation-dependency"): (
        "PO decision", "open_session_blocker leaves out automation declarers by design, the others count them; "
                       "no documented contract decides between them. Needs a PO decision."),
    ("open_blocker", "comments-unreadable"): (
        "read defect", "inflight_compute treats unreadable comments as no blocker: follow the fail-closed copies."),
    ("po_unblock", "default-po-login"): (
        "contract", "PROJECT-STATUS.md lifts a blocker with the PO's /unblock line; the Codex worker's _Facts runs "
                    "without DECISION_BLOCKER_PO_LOGIN and counts none: give every entry point the configured PO login."),
}

REQUIRED_KINDS = {
    "unreadable facts": [("linked_pr_lookup", "open-prs-unreadable"), ("merge_gates_pass", "gates-unreadable"),
                         ("in_flight_step", "open-prs-unreadable"), ("merged_pr_finish", "closed-prs-unreadable")],
    "failed marker read": [("in_flight_step", "comments-unreadable"), ("open_blocker", "comments-unreadable")],
    "unknown mergeability": [("merge_gates_pass", "mergeability-unknown"),
                             ("draft_ready_signal", "codex-ready-mergeability-unknown")],
    "more than 100 open PRs": [("linked_pr_lookup", "more-than-100-open-prs"),
                               ("merged_pr_finish", "more-than-100-open-prs")],
}  # fmt: skip

ALL_CASES = [(rule, case) for rule, cases in CASES.items() for case in cases]


@pytest.mark.parametrize(("rule", "case"), ALL_CASES, ids=[f"{r}-{c}" for r, c in ALL_CASES])
def test_ac1_each_copy_decides_as_recorded(rule, case, tmp_path, monkeypatch):
    f, baseline = CASES[rule][case]
    kwargs = {"default_logins": True} if case == "default-po-login" else {}

    decisions = DECIDE[rule](f, tmp_path, monkeypatch, **kwargs)

    assert decisions == baseline


@pytest.mark.parametrize(("rule", "case"), ALL_CASES, ids=[f"{r}-{c}" for r, c in ALL_CASES])
def test_ac2_a_case_is_listed_as_a_divergence_exactly_when_its_copies_differ(rule, case):
    decisions = set(CASES[rule][case][1].values())
    assert (len(decisions) > 1) == ((rule, case) in DIVERGENCES)


def test_ac2_each_divergence_has_a_resolution_class_and_a_known_case():
    for (rule, case), (kind, resolution) in DIVERGENCES.items():
        assert case in CASES[rule], (rule, case)
        assert kind in RESOLUTION_CLASSES and resolution, (rule, case)


def test_ac1_the_cases_cover_read_errors_and_paging():
    for kind, cases in REQUIRED_KINDS.items():
        for rule, case in cases:
            assert case in CASES[rule], (kind, rule, case)


def test_ac1_every_implementation_file_of_the_inventory_row_is_a_recorded_copy():
    doc = (REPO_ROOT / "docs" / "DISPATCHER-DECISIONS.md").read_text(encoding="utf-8")
    for rule, cases in CASES.items():
        row = next(line for line in doc.splitlines() if line.startswith(f"| `{rule}` |"))
        files = {Path(p).name for p in re.findall(r"`((?:scripts|src)/[^`]+)`", row.split(" | ")[2])}
        copies = {m for _, baseline in cases.values() for name in baseline
                  for m in re.findall(r"([\w.-]+):", name)}  # fmt: skip
        # These files only supply the PO login default, which po_unblock reads from the script; the two
        # that the duplicate-rule guard (#570) added to the allowlist are not decided by a parity fixture.
        login_only = {"dispatcher-poll.sh", "dispatcher-notify-retry.sh", "codex_review_bookkeeping.py"}
        assert files - copies <= login_only, (rule, files - copies)


def test_doc_links_each_rule_to_its_fixtures_and_lists_each_divergence():
    doc = (REPO_ROOT / "docs" / "DISPATCHER-DECISIONS.md").read_text(encoding="utf-8")
    section = doc.split("## Parity baseline", 1)[1]
    for rule in CASES:
        assert f"`{rule}`" in section and "tests/test_dispatcher_parity_in_flight.py" in section, rule
    for (rule, case), (kind, _) in DIVERGENCES.items():
        assert re.search(rf"`{rule}`[^\n]*`{case}`[^\n]*{kind}", section), (rule, case)
