"""Parity baseline for the new-work admission rules (issue #583, Epic #519, Feature #525).

Each rule of this group in the ``docs/DISPATCHER-DECISIONS.md`` inventory has more than one
implementation. For every case below, one set of GitHub facts (the open issues with their labels,
bodies and native types, their comments, the open PRs, the board cards the Codex worker reads and
the three limit variables) goes through each copy of the rule, as its entry point calls it, and the
decision of each copy is recorded in ``CASES``.

Shell copies run for real in bash: ``scripts/lib/*.sh`` is sourced, and the functions and pre-flight
steps of ``scripts/dispatcher-invoke.sh`` (Step 1 to Step 6) are extracted as text, against a fake
``gh`` that serves the facts from files and fails a read the case names (a list read without
``--paginate`` returns only the first 100 items). ``hpw`` runs for real; it only reads the facts
from a file the Python reader wrote from the same facts. Python copies get a fake gateway (the
facts-reader fake of ``tests/test_dispatcher_facts_block.py``) whose failing reads raise
``GatewayError``; the Codex worker runs its real ``claim`` under ``dry_run``. The harness follows the
conventions of ``tests/test_dispatcher_parity_in_flight.py`` (#571).

``DIVERGENCES`` lists every case where the copies decide differently, with the resolution class of
the #525 divergence decision (``contract``, ``read defect`` or ``PO decision``). A case whose copies
agree must not be in it, and a divergent case must be. Nothing here changes a decision.

Which read errors apply:

- Unreadable facts (the open issues, one issue, a spec record, the open PRs) apply to every rule
  but ``limits_valid``, which reads only three environment variables and no GitHub fact.
- A failed comment (marker) read applies where a rule reads comments: ``wip_limit`` (a fix round
  due is found from the bookkeeping marker on the PR), ``next_new_work_selection`` and
  ``refine_executor_split`` (the refinement summary). ``file_overlap``, ``executable_issue_type``,
  ``executor_label_routing`` and ``stale_spec_gate`` decide from bodies, labels and spec records;
  a failed comment read on the running story is recorded once for ``file_overlap``.
- More than 100 open PRs applies where a rule reads the open-PR list: ``wip_limit`` and
  ``next_new_work_selection`` (through the in-flight lists). Every copy here reads it in full.
- Unknown mergeability applies to no rule of this group: the in-implementation and in-flight
  lists ignore mergeability (``inflight_gates_pass`` leaves it to the merge step), and no other
  rule reads a PR's mergeability.
"""

from __future__ import annotations

import json
import ast
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from headless_pr_workflow.dispatcher import scheduler as scheduler_mod
from headless_pr_workflow.dispatcher.codex_worker import claim, scheduler_refine_due
from headless_pr_workflow.dispatcher.facts import STORY_TYPES, read_facts
from headless_pr_workflow.dispatcher.gateway import BoardCard, BoardSnapshot, GatewayError
from headless_pr_workflow.dispatcher.scheduler import (InvalidLimits, choose_next, codex_refine_due, load_limits,
                                                       next_action)  # fmt: skip
from headless_pr_workflow.dispatcher.status import _kind
from tests.dispatcher_fakes import BASH, REPO_ROOT, needs_tools
from tests.script_tools import windows_env
from tests.test_dispatcher_facts_block import PO, RECORD_OK, FakeGateway, spec_line, summary
from tests.test_dispatcher_parity_in_flight import RESOLUTION_CLASSES
from tests.test_dispatcher_parity_review_loop import findings

pytestmark = [
    needs_tools,
    pytest.mark.skipif(sys.platform == "win32",
                       reason="the extracted pre-flight steps and the hpw wrapper run under bash with a gh function"),
]  # fmt: skip

REPO = "owner/repo"
EPIC, OTHER_EPIC = 10, 11
STORY, RUNNING, NEXT = 500, 501, 502
HEAD = "a" * 40
STALE = "c" * 7  # a spec record whose commit is behind the Epic's spec commit
UNREAD = "d" * 7  # a spec record whose compare cannot be read
BOT, BK = "hpw-bot", "hpw-bookkeeping"
OPUS, CODEX = "executor:claude-code-opus", "executor:codex"
LIMITS = {"WIP_LIMIT_IMPLEMENTATION": "2", "READY_BUFFER_MIN": "2", "READY_BUFFER_MAX": "4"}
INVOKE_TEXT = (REPO_ROOT / "scripts" / "dispatcher-invoke.sh").read_text(encoding="utf-8")
LIB = REPO_ROOT / "scripts" / "lib"


# ── Facts ────────────────────────────────────────────────────────────────────


def comment(author: str, body: str, at: str = "2026-11-02T10:00:00Z") -> dict[str, Any]:
    return {"user": {"login": author, "type": "User"}, "author_association": "OWNER", "body": body,
            "created_at": at, "html_url": f"https://github.com/{REPO}/issues/1#c-{at}"}  # fmt: skip


def body(*, parent: int | None = EPIC, files: tuple[str, ...] | None = ("src/a.py",), record: str = RECORD_OK[:7],
         newline: str = "\n") -> str:  # fmt: skip
    epic = parent or EPIC
    lines = [f"Parent epic: #{parent} Epic: x" if parent else "", f"Feature group: #{epic + 10} Feature: y",
             spec_line(f"specs/{epic}-epic/spec.md", record), "", "## Goal", "", "x", "", "## Dependencies", "",
             "Hard depends on:", "", "- None.", ""]  # fmt: skip
    if files is not None:
        lines += ["## Files affected", "", *(f"- {path}" for path in files), ""]
    return newline.join(lines)


def issue(number: int, labels: tuple[str, ...], text: str, native: str | None = None) -> dict[str, Any]:
    return {"number": number, "title": f"T{number}", "body": text, "labels": [{"name": n} for n in labels],
            "type": {"name": native} if native else None}  # fmt: skip


def story(number: int = STORY, *labels: str, refined: bool = True, native: str | None = None,
          **kw: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:  # fmt: skip
    """An open story (or any issue) under Epic 10 with its comments: a passing refinement summary when refined."""

    labels = labels or ("type:story", OPUS)
    return issue(number, labels, body(**kw), native), [comment(PO, summary())] if refined else []


def pr(number: int, closes: int, *, draft: bool = True) -> dict[str, Any]:
    return {"number": number, "state": "open", "body": f"Closes #{closes}", "draft": draft,
            "head": {"sha": HEAD, "ref": f"claude/issue-{closes}-x"}, "mergeable": True}  # fmt: skip


def fillers(count: int = 100) -> list[dict[str, Any]]:
    """Open PRs for another issue, listed before the PR the case is about (newest first)."""

    return [pr(2000 + i, 1, draft=False) for i in range(count)]


def world(*stories: tuple[dict[str, Any], list[dict[str, Any]]], prs: list | tuple = (), board: dict | None = None,
          fail: tuple[str, ...] = (), limits: dict | None = None, prc: dict | None = None) -> dict[str, Any]:  # fmt: skip
    """Epic 10 (and 11) with a merged spec and a current record, Feature 20 (and 21) refined, and the stories."""

    issues, comments = [], {}
    for epic, prio in ((EPIC, "prio:P1"), (OTHER_EPIC, "prio:P0")):
        issues.append(issue(epic, ("type:epic", prio), spec_line(f"specs/{epic}-epic/spec.md")))
        issues.append(issue(epic + 10, ("type:feature",), body(parent=epic, files=None)))
        comments[epic + 10] = [comment(PO, summary())]
    for entry, story_comments in stories:
        issues.append(entry)
        comments[entry["number"]] = story_comments
    comments.update(prc or {})
    return {"issues": issues, "comments": comments, "prs": list(prs), "board": board or {}, "fail": set(fail),
            "limits": {**LIMITS, **(limits or {})}}  # fmt: skip


class World(FakeGateway):
    """The facts-reader fake, plus the reads of the in-flight lists and the Codex worker."""

    def __init__(self, f: dict[str, Any]) -> None:
        super().__init__()
        self.f = f
        self.issues = f["issues"]
        self.comments = f["comments"]
        self.fail = set(f["fail"])
        self.dirs = ["10-epic", "11-epic"]
        self.commits = {f"specs/{e}-epic/spec.md": {"sha": "a" * 40, "date": "2026-11-01T00:00:00Z"} for e in (10, 11)}
        self.compares = {RECORD_OK[:7]: "identical", STALE: "behind", UNREAD: "identical"}

    def list_open_prs(self) -> list[dict[str, Any]]:
        if "open" in self.fail:
            raise GatewayError("read open pull requests", "HTTP 502")
        return list(self.f["prs"])

    def list_board_items(self) -> BoardSnapshot:
        labels = {i["number"]: tuple(n["name"] for n in i["labels"]) for i in self.issues}
        return BoardSnapshot(cards=[BoardCard(f"item-{n}", n, "open", labels[n], status)
                                    for n, status in self.f["board"].items()])  # fmt: skip

    def viewer_login(self) -> str:
        return BOT

    def matching_branches(self, prefix: str) -> dict[str, str]:
        return {}


def facts_of(f: dict[str, Any]) -> dict[str, Any]:
    """``hpw dispatcher facts read`` on the facts: the scheduler's and the resume pass's input."""

    return read_facts(World(f), REPO, env={"DECISION_BLOCKER_PO_LOGIN": PO}).to_dict()


# ── Running the shell copies ─────────────────────────────────────────────────

FAKE_GH = r"""
_fail() { [[ -f "$FACTS/fail-$1" ]]; }
gh() {
    local path="" a paged=false n
    for a in "$@"; do [[ "$a" == repos/* ]] && path="$a"; [[ "$a" == --paginate ]] && paged=true; done
    case "$path" in
        *"/pulls?state=open"*) _fail open && return 1
            if $paged; then cat "$FACTS/open.json"; else jq -c '.[0:100]' "$FACTS/open.json"; fi ;;
        *"/pulls?state=closed"*) echo '[]' ;;
        *"/issues?state=open"*) _fail issues && return 1; cat "$FACTS/issues.json" ;;
        */comments*) n="${path#*/issues/}"; n="${n%%/*}"; _fail "comments-$n" && return 1
            if [[ -f "$FACTS/comments-$n.json" ]]; then cat "$FACTS/comments-$n.json"; else echo '[]'; fi ;;
        */issues/*) n="${path##*/}"; { _fail issues || _fail "issue-$n"; } && return 1
            jq -ce --argjson n "$n" '.[] | select(.number == $n)' "$FACTS/issues.json" ;;
        *) echo "fake gh: unsupported $*" >&2; return 1 ;;
    esac
}
inflight_get_all() { gh api --paginate "$1"; }
inflight_get_page() { gh api "$1"; }
inflight_prepare() { INFLIGHT_BOT="$BOT_LOGIN" INFLIGHT_BK="$BK_LOGIN" INFLIGHT_PO="$DECISION_BLOCKER_PO_LOGIN"; }
post_conflict_blocker() { echo "BLOCKER: $3"; exit 0; }
skip_story_and_continue() { echo "SKIP: $2"; exit 0; }
run_refine() { echo "REFINE $REFINE_ISSUE"; exit 0; }
"""

# The real hpw, except that `facts read` prints the facts the Python reader wrote from the same facts, `scheduler next`
# reads them from that file, and `in-flight plan` keeps a copy of its input rows (what inflight_compute read).
FAKE_HPW = """#!/usr/bin/env bash
case "$1 $2" in
    "dispatcher facts") cat "$FACTS/facts.json"; jq -e '.result == "read"' "$FACTS/facts.json" >/dev/null || exit 3; exit 0 ;;
    "dispatcher scheduler") set -- "$@" --facts-file "$FACTS/facts.json" ;;
    "dispatcher in-flight") prev=""; for a in "$@"; do [[ "$prev" == --input ]] && cp "$a" "$FACTS/plan-input.json"; prev="$a"; done ;;
esac
PYTHONPATH="{src}" exec "{python}" -c 'import sys; from headless_pr_workflow.cli import main; sys.exit(main())' "$@"
"""

FAKE_GATE = "#!/usr/bin/env bash\nexit 2\n"  # no merge gate passes: a non-draft linked PR is in the review step


def _function(name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", INVOKE_TEXT, re.MULTILINE | re.DOTALL)
    assert match, f"{name} not found in scripts/dispatcher-invoke.sh"
    return match.group(0)


def _declare(name: str) -> str:
    match = re.search(rf"^declare -A {name}=\(\n.*?^\)\n", INVOKE_TEXT, re.MULTILINE | re.DOTALL)
    assert match, f"the {name} table was not found in scripts/dispatcher-invoke.sh"
    return match.group(0)


def _between(start: str, end: str) -> str:
    """The literal top-level step of scripts/dispatcher-invoke.sh from ``start`` up to ``end``."""

    i = INVOKE_TEXT.find(start)
    j = INVOKE_TEXT.find(end, i + 1)
    assert i >= 0 and j > i, f"{start!r} .. {end!r} not found in scripts/dispatcher-invoke.sh"
    return INVOKE_TEXT[i:j]


STEP1 = _between("# ─── Step 1: Fetch target story", "# ─── Step 1b")
STEP1B = _between("# ─── Step 1b", "# ─── Step 2")
STEP2 = _between("# ─── Step 2: Executor label routing", "# ─── Step 5: WIP limit check")
STEP5 = _between("# ─── Step 5: WIP limit check", "# ─── Step 6: File overlap check")
STEP6 = _between("# ─── Step 6: File overlap check", "# ─── All checks passed")
HAND_OFF_CHECK = 'if [[ -n "${CODEX_REVIEW_LOOP_ROUTING[$EXECUTOR_LABEL]:-}" ]]; then'  # the review path after /implement
assert HAND_OFF_CHECK in INVOKE_TEXT, "the review-path check after /implement was not found"
INVOKE_PIECES = "\n".join([
    _declare("EXECUTOR_ROUTING"), _declare("EXECUTOR_PROVIDER"), _declare("EXECUTOR_MODEL"),
    *(_function(name) for name in ("admission_issue_type", "admission_executor", "ask_scheduler", "next_new_work",
                                   "load_story_context", "is_review_loop_story", "read_resume_facts",
                                   "resume_session_allowed")),
])  # fmt: skip


def bash(tmp: Path, f: dict[str, Any], body_text: str) -> tuple[int, str, str]:
    """Run ``body_text`` in bash after writing the facts to files under ``tmp``; ``(exit code, stdout, stderr)``."""

    tmp.mkdir(parents=True, exist_ok=True)
    counts = {n: len(c) for n, c in f["comments"].items()}
    issues = [{**i, "comments": counts.get(i["number"], 0)} for i in f["issues"]]
    (tmp / "issues.json").write_text(json.dumps(issues), encoding="utf-8")
    (tmp / "open.json").write_text(json.dumps(f["prs"]), encoding="utf-8")
    (tmp / "facts.json").write_text(json.dumps(facts_of(f)), encoding="utf-8")
    for number, items in f["comments"].items():
        (tmp / f"comments-{number}.json").write_text(json.dumps(items), encoding="utf-8")
    for key in f["fail"]:
        (tmp / f"fail-{key.replace(' ', '-')}").write_text("", encoding="utf-8")
    hpw, gate = tmp / "hpw", tmp / "gate.sh"
    hpw.write_text(FAKE_HPW.replace("{src}", (REPO_ROOT / "src").as_posix()).replace("{python}", Path(sys.executable).as_posix()),
                   encoding="utf-8", newline="\n")  # fmt: skip
    gate.write_text(FAKE_GATE, encoding="utf-8", newline="\n")
    for script in (hpw, gate):
        script.chmod(0o755)
    script = "\n".join([
        "set -uo pipefail", f'export FACTS="{tmp.as_posix()}"', f'REPO="{REPO}"', f'HPW_BIN="{hpw.as_posix()}"',
        f'MERGE_GATE_SCRIPT="{gate.as_posix()}"', f'BOT_LOGIN="{BOT}"', f'BK_LOGIN="{BK}"', f"ISSUE_NUMBER={STORY}",
        "DRY_RUN=false", 'LOG_PREFIX=""', 'LAST_ACTION=""', "DISPATCH_HANDLED=false", 'NON_EXECUTABLE_ISSUES=""',
        'DISPATCH_RUN_STARTED_ISSUES=""', 'HAND_OFF_RESUMED_ISSUES=""', 'RESUME_FACTS_STATE=""',
        'RESUME_FACTS_JSON=""', 'RESUME_FACTS_ERROR=""', "STORY_EXECUTOR_LABEL_COUNT=0",
        *(f'source "{(LIB / name).as_posix()}"' for name in ("review-routing.sh", "in-flight.sh", "scheduler.sh",
                                                             "limits.sh")),
        INVOKE_PIECES, FAKE_GH, 'read_limits || exit 9; WIP_LIMIT="$WIP_LIMIT_IMPLEMENTATION"', body_text])  # fmt: skip
    env = {**windows_env(), **f["limits"], "PATH": os.environ.get("PATH", ""), "DECISION_BLOCKER_PO_LOGIN": PO}
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True, env=env, timeout=120, check=False)
    return proc.returncode, proc.stdout.strip(), proc.stderr


def preflight(tmp: Path, f: dict[str, Any]) -> str:
    """Steps 1 to 6 of ``dispatcher-invoke.sh`` for the target story, as a scheduled or ``--issue`` run reaches them."""

    rc, out, err = bash(tmp, f, "\n".join([STEP1, STEP1B, STEP2, STEP5, STEP6, "echo START"]))
    last = out.splitlines()[-1] if out else ""
    if last.startswith("BLOCKER: Fewer than"):
        return "wip-limit"
    if last.startswith("BLOCKER: The overlapping files"):
        return "overlap " + re.search(r": (\S+) \(owned by", last).group(1)
    if last.startswith("SKIP: "):
        return "skipped"
    if last == "START":
        return "start"
    return "unreadable" if re.search(r"[Cc]ould not (fetch|read)", err) else f"stopped (exit {rc})"


def scheduler_poll(tmp: Path, f: dict[str, Any]) -> tuple[str, str]:
    """The poll's scheduler call (in-flight lists, ``scheduler_next``, ``hpw dispatcher scheduler next``)."""

    _, out, err = bash(tmp, f, "inflight_prepare; inflight_compute || { echo unreadable; exit 0; }\n"
                                "inflight_scheduler_lists\n"
                                'scheduler_next "$IN_IMPLEMENTATION_LIST" "$IN_FLIGHT_LIST"\n'
                                'echo "$SCHEDULER_RESULT $SCHEDULER_ISSUE"')  # fmt: skip
    words = out.split()
    if not words or words[0] in ("unreadable", "idle"):
        return ("unreadable" if not words else {"unreadable": "unreadable", "idle": "none"}[words[0]]), err
    return f"{words[0]} #{words[1]}", err


def scheduler_invoke(tmp: Path, f: dict[str, Any]) -> str:
    """``next_new_work`` (with ``ask_scheduler``) of ``dispatcher-invoke.sh``: what the run starts next."""

    _, out, _ = bash(tmp, f, 'next_new_work\necho "IMPLEMENT $NEXT_ISSUE"')
    last = out.splitlines()[-1] if out else ""
    if last.startswith(("IMPLEMENT ", "REFINE ")):
        kind, number = last.split()
        return f"{kind.lower()} #{number}"
    return "unreadable" if 'result is "unreadable"' in out else "none"


def plan_rows(tmp: Path, f: dict[str, Any]) -> dict[int, dict[str, Any]] | None:
    """The rows ``inflight_compute`` (poll and resume) handed to ``hpw dispatcher in-flight plan``; ``None`` on a failed read."""

    rc, _, _ = bash(tmp, f, "inflight_prepare; inflight_compute")
    if rc:
        return None
    return {row["number"]: row for row in json.loads((tmp / "plan-input.json").read_text(encoding="utf-8"))}


def codex_claim(f: dict[str, Any]) -> str:
    """``scripts/codex-worker.sh claim`` under ``--dry-run``: the item it would claim, with its refine source (#535)."""

    gateway, env = World(f), {**f["limits"], "DECISION_BLOCKER_PO_LOGIN": PO}
    result = claim(gateway, REPO, owner="laptop", eligibility=lambda repo, number, command: (True, "eligible"),
                   env=env, dry_run=True, refine_due=scheduler_refine_due(gateway, REPO, env))  # fmt: skip
    if result.result == "dry-run":
        call = result.fields["invocation"]
        return f"{call['command']} #{call['issue']}"
    return "unreadable" if result.result == "unreadable" else "none"


# ── Rule decisions ───────────────────────────────────────────────────────────


def started(word: str) -> str:
    return "start" if word == f"implement #{STORY}" else "unreadable" if word == "unreadable" else "no start"


def wip_limit_decisions(f: dict[str, Any], tmp: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Whether the ready target story #500 may start: ``start``, ``no start`` or ``unreadable``."""

    seen: dict[str, Any] = {}

    def capture(facts: Any, **kw: Any) -> list[int]:
        seen.update(kw, facts=facts)
        return []

    monkeypatch.setattr(scheduler_mod, "codex_refine_due", capture)
    gateway = World(f)
    try:
        scheduler_refine_due(gateway, REPO, {**f["limits"], "DECISION_BLOCKER_PO_LOGIN": PO})()
    except GatewayError:
        codex = "unreadable"
    else:
        decision = choose_next(seen["facts"], in_implementation=seen["in_implementation"],
                               in_flight=seen["in_flight"], limits=seen["limits"])  # fmt: skip
        codex = started(f"{decision.result} #{(decision.action or {}).get('issue')}")
    word = preflight(tmp / "invoke", f)
    return {
        "scheduler.py:choose_next (dispatcher-poll.sh)": started(scheduler_poll(tmp / "poll", f)[0]),
        "dispatcher-invoke.sh:IN_IMPL_COUNT (Step 5)": {"start": "start", "unreadable": "unreadable"}.get(word, "no start"),
        "codex_worker.py:scheduler_refine_due (its list through choose_next)": codex,
    }


def limits_valid_decisions(f: dict[str, Any], tmp: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """The limits each copy loads from the three variables (a missing name is unset): the values or ``invalid``."""

    env = f["env"]
    try:
        loaded = load_limits(env)
        python = f"{loaded.wip}/{loaded.buffer_min}/{loaded.buffer_max}"
    except InvalidLimits:
        python = "invalid"
    exports = "".join(f"export {name}={json.dumps(value)}\n" for name, value in env.items())
    unset = "unset WIP_LIMIT_IMPLEMENTATION READY_BUFFER_MIN READY_BUFFER_MAX\n"
    rc, out, _ = bash(tmp, world(), unset + exports + 'read_limits && echo "$WIP_LIMIT_IMPLEMENTATION/$READY_BUFFER_MIN/'
                                                      '$READY_BUFFER_MAX" || echo invalid')  # fmt: skip
    return {"scheduler.py:load_limits": python, "limits.sh:load_limits": out if rc == 0 else f"exit {rc}"}


def file_overlap_decisions(f: dict[str, Any], tmp: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Whether the target story #500 overlaps a story in implementation: ``overlap <path>``, ``no overlap``, ``unreadable``."""

    word, err = scheduler_poll(tmp / "poll", f)
    found = re.search(rf"skip #{STORY} file-overlap #\d+ (\S+)", err)
    scheduler = f"overlap {found.group(1)}" if found else "no overlap" if word == f"implement #{STORY}" else word
    invoke = preflight(tmp / "invoke", f)
    return {"facts.py:files_affected + scheduler.py:choose_next": scheduler,
            "dispatcher-invoke.sh:extract_files (Step 6)": "no overlap" if invoke == "start" else invoke}


def next_new_work_selection_decisions(f: dict[str, Any], tmp: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """The next new work: ``implement #<n>``, ``refine #<n>``, ``none`` or ``unreadable``."""

    return {"scheduler.sh:scheduler_next (dispatcher-poll.sh; scheduler.py:choose_next)": scheduler_poll(tmp / "poll", f)[0],
            "dispatcher-invoke.sh:next_new_work + ask_scheduler": scheduler_invoke(tmp / "invoke", f),
            "codex_worker.py:_queue (claim)": codex_claim(f)}  # fmt: skip


def refine_executor_split_decisions(f: dict[str, Any], tmp: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Whether story #500 is left to the Codex worker for refinement: ``codex``, ``not codex`` or ``unreadable``."""

    facts = facts_of(f)
    if facts["result"] != "read":
        return dict.fromkeys(SPLIT, "unreadable") | {"codex_worker.py:_queue (claim)": codex_claim(f)}
    limits = load_limits(f["limits"])
    actions = choose_next(facts, in_implementation=[], limits=limits)
    codex_due = codex_refine_due(facts, in_implementation=[], limits=limits)
    return {
        "scheduler.py:choose_next (codex-assigned)": "codex" if {"issue": STORY, "reason": "codex-assigned"} in actions.skipped else "not codex",
        "scheduler.py:codex_refine_due": "codex" if STORY in codex_due else "not codex",
        "codex_worker.py:_queue (claim)": {f"refine #{STORY}": "codex", "unreadable": "unreadable"}.get(codex_claim(f), "not codex"),
    }  # fmt: skip


def executable_issue_type_decisions(f: dict[str, Any], tmp: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Whether issue #500 is an executable Story, Task or Bug: ``executable``, ``not executable`` or ``unreadable``."""

    def yes(flag: Any) -> str:
        return "executable" if flag else "not executable"

    facts = facts_of(f)
    entry = next((i for i in facts["issues"] if i["number"] == STORY), None)
    _, out, err = bash(tmp / "step", f, "\n".join([STEP1, STEP1B, "echo EXECUTABLE"]))
    _, resume, _ = bash(tmp / "resume", f, f'load_story_context {STORY} item && echo EXECUTABLE || echo "$STORY_SKIP_REASON"')
    rows = plan_rows(tmp / "in-flight", f)
    target = next(i for i in f["issues"] if i["number"] == STORY)
    claimed = codex_claim(f)
    return {
        "facts.py:STORY_TYPES": "unreadable" if facts["result"] != "read" else yes(entry and entry["type"] in STORY_TYPES),
        "dispatcher-invoke.sh:classify_issue_type (Step 1b)":
            "unreadable" if "Could not fetch" in err else yes(out.endswith("EXECUTABLE")),
        "dispatcher-invoke.sh:classify_issue_type (load_story_context)":
            "unreadable" if "could not be read" in resume else yes(not resume.startswith("not an executable")),
        "in-flight.sh:inflight_compute": "unreadable" if rows is None else yes(STORY in rows),
        "status.py:_kind": "unreadable" if "issues" in f["fail"] else yes(_kind(target) in STORY_TYPES),
        "codex_worker.py:TYPE_LABELS (claim)": "unreadable" if claimed == "unreadable" else yes(claimed == f"implement #{STORY}"),
    }  # fmt: skip


def executor_label_routing_decisions(f: dict[str, Any], tmp: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """The path the labels give story #500: ``loop`` (Codex review loop), ``no loop``, ``skipped``, ``stopped``, ``unreadable``."""

    _, out, err = bash(tmp / "step", f, "\n".join([STEP1, STEP1B, STEP2, HAND_OFF_CHECK + " echo LOOP; else echo NOLOOP; fi"]))
    last = out.splitlines()[-1] if out else ""
    step = {"LOOP": "loop", "NOLOOP": "no loop"}.get(last, "skipped" if last.startswith("SKIP: ") else None)
    step = step or ("unreadable" if "Could not fetch" in err else "stopped")
    _, resume, _ = bash(tmp / "resume", f, f"if load_story_context {STORY} item; then is_review_loop_story && echo loop "
                                           '|| echo "no loop"; else echo "$STORY_SKIP_REASON"; fi')  # fmt: skip
    rows = plan_rows(tmp / "in-flight", f)
    return {
        "dispatcher-invoke.sh:EXECUTOR_ROUTING + ROUTING_ENTRY + skip_story_and_continue (Step 2, hand-off)": step,
        "dispatcher-invoke.sh:load_story_context + is_review_loop_story (resume)":
            resume if resume in ("loop", "no loop") else "unreadable" if "could not be read" in resume else "skipped",
        "in-flight.sh:inflight_compute (review_loop)":
            "unreadable" if rows is None else "loop" if rows[STORY]["review_loop"] else "no loop",
    }  # fmt: skip


def stale_spec_gate_decisions(f: dict[str, Any], tmp: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Whether an implementation-type session may start for story #500: ``allowed``, ``blocked`` or ``unreadable``."""

    result = next_action(None, REPO, in_implementation=[], env=f["limits"], facts=facts_of(f))
    skipped = {s["issue"]: s["reason"] for s in result.fields["skipped"]}
    if result.result == "unreadable" or str(skipped.get(STORY, "")).startswith("unreadable"):
        scheduler = "unreadable"
    else:
        action = result.fields["action"] or {}
        scheduler = "allowed" if (action.get("kind"), action.get("issue")) == ("implement", STORY) else "blocked"
    rc, out, err = bash(tmp, f, f"resume_session_allowed {STORY} && echo allowed")
    resume = "allowed" if rc == 0 else "unreadable" if "facts unreadable" in out + err else "blocked"
    claimed = codex_claim(f)
    return {"scheduler.py:choose_next": scheduler,
            "dispatcher-invoke.sh:resume_session_allowed + read_resume_facts": resume,
            "codex_worker.py:claim (suspected)": "allowed" if claimed == f"implement #{STORY}" else
                                                 "unreadable" if claimed == "unreadable" else "blocked"}  # fmt: skip


DECIDE = {
    "wip_limit": wip_limit_decisions,
    "limits_valid": limits_valid_decisions,
    "file_overlap": file_overlap_decisions,
    "next_new_work_selection": next_new_work_selection_decisions,
    "refine_executor_split": refine_executor_split_decisions,
    "executable_issue_type": executable_issue_type_decisions,
    "executor_label_routing": executor_label_routing_decisions,
    "stale_spec_gate": stale_spec_gate_decisions,
}


# ── Cases and the recorded baseline ──────────────────────────────────────────


def same(copies: tuple[str, ...], decision: str) -> dict[str, str]:
    return dict.fromkeys(copies, decision)


def but(copies: tuple[str, ...], decision: str, **others: str) -> dict[str, str]:
    """``decision`` for every copy, except the copies whose marker (the text after ``:``) is a keyword."""

    out = same(copies, decision)
    for copy in copies:
        marker = re.sub(r"\W", "_", copy.split(":", 1)[1].split(" ")[0])
        if marker in others:
            out[copy] = others[marker]
    assert set(others) <= {re.sub(r"\W", "_", c.split(":", 1)[1].split(" ")[0]) for c in copies}, others
    return out


WIP = ("scheduler.py:choose_next (dispatcher-poll.sh)", "dispatcher-invoke.sh:IN_IMPL_COUNT (Step 5)",
       "codex_worker.py:scheduler_refine_due (its list through choose_next)")  # fmt: skip
LIMIT = ("scheduler.py:load_limits", "limits.sh:load_limits")
OVERLAP = ("facts.py:files_affected + scheduler.py:choose_next", "dispatcher-invoke.sh:extract_files (Step 6)")
SELECT = ("scheduler.sh:scheduler_next (dispatcher-poll.sh; scheduler.py:choose_next)",
          "dispatcher-invoke.sh:next_new_work + ask_scheduler", "codex_worker.py:_queue (claim)")  # fmt: skip
SPLIT = ("scheduler.py:choose_next (codex-assigned)", "scheduler.py:codex_refine_due", "codex_worker.py:_queue (claim)")
TYPES = ("facts.py:STORY_TYPES", "dispatcher-invoke.sh:classify_issue_type (Step 1b)",
         "dispatcher-invoke.sh:classify_issue_type (load_story_context)", "in-flight.sh:inflight_compute",
         "status.py:_kind", "codex_worker.py:TYPE_LABELS (claim)")  # fmt: skip
ROUTE = ("dispatcher-invoke.sh:EXECUTOR_ROUTING + ROUTING_ENTRY + skip_story_and_continue (Step 2, hand-off)",
         "dispatcher-invoke.sh:load_story_context + is_review_loop_story (resume)",
         "in-flight.sh:inflight_compute (review_loop)")  # fmt: skip
STALE_COPIES = ("scheduler.py:choose_next", "dispatcher-invoke.sh:resume_session_allowed + read_resume_facts",
                "codex_worker.py:claim (suspected)")  # fmt: skip

TARGET = story(STORY, "type:story", OPUS, "prio:P1")
RUNNING_STORY = story(RUNNING, "type:story", OPUS, files=("src/other.py",))
FIX_ROUND = {901: [comment(BK, findings())]}  # the bookkeeping job's findings on the PR's current head


def running(wip: str = "1", **kw: Any) -> dict[str, Any]:
    """The target story ready, #501 with a draft linked PR (in implementation from PR facts)."""

    kw.setdefault("board", {RUNNING: "In implementation", STORY: "Ready to implement"})
    return world(TARGET, RUNNING_STORY, prs=kw.pop("prs", [pr(901, RUNNING)]), limits={"WIP_LIMIT_IMPLEMENTATION": wip}, **kw)


def overlap(target_files: tuple[str, ...] | None, running_files: tuple[str, ...] | None, *, parent: int | None = EPIC,
            newline: str = "\n", fail: tuple[str, ...] = ()) -> dict[str, Any]:  # fmt: skip
    return world(story(STORY, "type:story", OPUS, files=target_files, newline=newline),
                 story(RUNNING, "type:story", OPUS, files=running_files, parent=parent),
                 prs=[pr(901, RUNNING)], fail=fail)  # fmt: skip


def limits(**env: str) -> dict[str, Any]:
    return {"env": env}


def codex_story(number: int, *labels: str, status: str = "Ready to implement", **kw: Any) -> tuple[Any, str]:
    return story(number, "type:story", CODEX, *labels, **kw), status


def select(*items: tuple[Any, str], prs: list | tuple = (), fail: tuple[str, ...] = ()) -> dict[str, Any]:
    return world(*(entry for entry, _ in items), prs=prs, fail=fail,
                 board={entry[0]["number"]: status for entry, status in items})  # fmt: skip


def unrefined(*labels: str, fail: tuple[str, ...] = ()) -> dict[str, Any]:
    """Story #500 not refined yet (no summary), under a refined Feature, with the ready buffer open."""

    return world(story(STORY, *labels, refined=False), board={STORY: "Ready to refine"}, fail=fail)


def typed(*labels: str, native: str | None = None, fail: tuple[str, ...] = ()) -> dict[str, Any]:
    """Issue #500 with these type labels (and ``executor:codex``) on the board as Ready to implement."""

    return world(story(STORY, *labels, CODEX, native=native),
                 board={STORY: "Ready to implement"}, fail=fail)  # fmt: skip


def routed(*labels: str, fail: tuple[str, ...] = ()) -> dict[str, Any]:
    return world(story(STORY, "type:story", *labels), prs=[pr(900, STORY, draft=False)], fail=fail)


def staled(record: str, fail: tuple[str, ...] = (), **limits_env: str) -> dict[str, Any]:
    return world(story(STORY, "type:story", CODEX, record=record), board={STORY: "Ready to implement"}, fail=fail,
                 limits=limits_env)  # fmt: skip


CASES: dict[str, dict[str, tuple[dict[str, Any], dict[str, str]]]] = {
    "wip_limit": {
        "below-limit": (running("2"), same(WIP, "start")),
        "at-limit": (running("1"), same(WIP, "no start")),
        "board-lags-pr-facts": (running("1", board={RUNNING: "Ready to implement", STORY: "Ready to implement"}),
                                but(WIP, "no start", scheduler_refine_due="start")),
        "board-only-in-implementation": (running("1", prs=[], board={RUNNING: "In implementation"}),
                                         but(WIP, "start", scheduler_refine_due="no start")),
        "target-in-implementation": (running("1", prs=[pr(900, STORY)], board={STORY: "In implementation"}),
                                     but(WIP, "no start", IN_IMPL_COUNT="start")),
        "fix-round-due": (running("1", prs=[pr(901, RUNNING, draft=False)], prc=FIX_ROUND), same(WIP, "no start")),
        "fix-round-pr-comments-unreadable": (running("1", prs=[pr(901, RUNNING, draft=False)], prc=FIX_ROUND,
                                                     fail=("comments 901",)),
                                             but(WIP, "start", scheduler_refine_due="no start")),
        "open-prs-unreadable": (running("1", fail=("open",)), same(WIP, "unreadable")),
        "running-story-unreadable": (running("2", fail=(f"issue {RUNNING}",)), but(WIP, "start", IN_IMPL_COUNT="unreadable")),
        "more-than-100-open-prs": (running("1", prs=[*fillers(), pr(901, RUNNING)]), same(WIP, "no start")),
    },
    "limits_valid": {
        "valid": (limits(**LIMITS), same(LIMIT, "2/2/4")),
        "leading-zeros": (limits(WIP_LIMIT_IMPLEMENTATION="08", READY_BUFFER_MIN="0", READY_BUFFER_MAX="010"),
                          same(LIMIT, "8/0/10")),
        "empty": (limits(**{**LIMITS, "READY_BUFFER_MIN": ""}), same(LIMIT, "invalid")),
        "unset": (limits(WIP_LIMIT_IMPLEMENTATION="2", READY_BUFFER_MIN="2"), same(LIMIT, "invalid")),
        "not-a-whole-number": (limits(**{**LIMITS, "WIP_LIMIT_IMPLEMENTATION": "2.5"}), same(LIMIT, "invalid")),
        "negative": (limits(**{**LIMITS, "READY_BUFFER_MIN": "-1"}), same(LIMIT, "invalid")),
        "surrounding-space": (limits(**{**LIMITS, "WIP_LIMIT_IMPLEMENTATION": " 2"}), same(LIMIT, "invalid")),
        "wip-zero": (limits(**{**LIMITS, "WIP_LIMIT_IMPLEMENTATION": "0"}), same(LIMIT, "invalid")),
        "min-above-max": (limits(WIP_LIMIT_IMPLEMENTATION="1", READY_BUFFER_MIN="5", READY_BUFFER_MAX="4"),
                          same(LIMIT, "invalid")),
    },
    "file_overlap": {
        "same-file": (overlap(("src/a.py",), ("src/a.py",)), same(OVERLAP, "overlap src/a.py")),
        "other-files": (overlap(("src/a.py",), ("src/b.py",)), same(OVERLAP, "no overlap")),
        "target-section-missing": (overlap(None, ("src/a.py",)), same(OVERLAP, "no overlap")),
        "running-section-missing": (overlap(("src/a.py",), None), same(OVERLAP, "no overlap")),
        "backticks-and-note": (overlap(("`src/a.py` (new file)",), ("src/a.py",)), same(OVERLAP, "overlap src/a.py")),
        "crlf-body": (overlap(("src/a.py",), ("src/a.py",), newline="\r\n"), same(OVERLAP, "overlap src/a.py")),
        "running-story-without-parent-epic": (overlap(("src/a.py",), ("src/a.py",), parent=None),
                                              but(OVERLAP, "overlap src/a.py", files_affected="no overlap")),
        "running-comments-unreadable": (overlap(("src/a.py",), ("src/a.py",), fail=(f"comments {RUNNING}",)),
                                        same(OVERLAP, "overlap src/a.py")),
        "open-issues-unreadable": (overlap(("src/a.py",), ("src/a.py",), fail=("issues",)), same(OVERLAP, "unreadable")),
    },
    "next_new_work_selection": {
        "one-ready-story": (select(codex_story(STORY)), same(SELECT, f"implement #{STORY}")),
        "story-prio": (select(codex_story(STORY, "prio:P2"), codex_story(RUNNING, "prio:P1")),
                       same(SELECT, f"implement #{RUNNING}")),
        "board-in-implementation-first": (
            select(codex_story(STORY, "prio:P2", status="In implementation"), codex_story(RUNNING, "prio:P1")),
            but(SELECT, f"implement #{RUNNING}", _queue=f"implement #{STORY}")),
        "two-prio-labels": (select(codex_story(STORY, "prio:P3", "prio:P0"), codex_story(RUNNING, "prio:P1")),
                            but(SELECT, f"implement #{RUNNING}", _queue=f"implement #{STORY}")),
        "epic-prio-first": (select(codex_story(STORY, "prio:P2", parent=OTHER_EPIC), codex_story(RUNNING, "prio:P1")),
                            but(SELECT, f"implement #{STORY}", _queue=f"implement #{RUNNING}")),
        "story-in-review": (select(codex_story(STORY, status="In review"), prs=[pr(900, STORY, draft=False)]),
                            same(SELECT, "none")),
        "issues-unreadable": (select(codex_story(STORY), fail=("issues",)), same(SELECT, "unreadable")),
        "open-prs-unreadable": (select(codex_story(STORY), fail=("open",)), same(SELECT, "unreadable")),
        "story-comments-unreadable": (select(codex_story(STORY), fail=(f"comments {STORY}",)),
                                      but(SELECT, "none", _queue="unreadable")),
        "more-than-100-open-prs": (select(codex_story(STORY, status="In review"), codex_story(RUNNING),
                                          prs=[*fillers(), pr(900, STORY, draft=False)]),
                                   same(SELECT, f"implement #{RUNNING}")),
    },
    "refine_executor_split": {
        "codex-story": (unrefined("type:story", CODEX), same(SPLIT, "codex")),
        "claude-story": (unrefined("type:story", OPUS), same(SPLIT, "not codex")),
        "codex-label-first-of-two": (unrefined("type:story", CODEX, OPUS), but(SPLIT, "codex", _queue="not codex")),
        "claude-label-first-of-two": (unrefined("type:story", OPUS, CODEX), same(SPLIT, "not codex")),
        "codex-task": (unrefined("type:task", CODEX), same(SPLIT, "codex")),
        "story-comments-unreadable": (unrefined("type:story", CODEX, fail=(f"comments {STORY}",)), same(SPLIT, "not codex")),
        "issues-unreadable": (unrefined("type:story", CODEX, fail=("issues",)), same(SPLIT, "unreadable")),
    },
    "executable_issue_type": {
        "type-story": (typed("type:story"), same(TYPES, "executable")),
        "type-bug": (typed("type:bug"), same(TYPES, "executable")),
        "type-epic": (typed("type:epic"), same(TYPES, "not executable")),
        "capitalised-label": (typed("type:Story"), same(TYPES, "not executable")),
        "story-and-feature-labels": (typed("type:story", "type:feature"),
                                     but(TYPES, "executable", classify_issue_type="not executable")),
        "feature-and-story-labels": (typed("type:feature", "type:story"),
                                     but(TYPES, "not executable", inflight_compute="executable", TYPE_LABELS="executable")),
        "native-type-only": (typed(native="Bug"), but(TYPES, "not executable", classify_issue_type="executable",
                                                      inflight_compute="executable")),
        "other-type-label-and-native-story": (typed("type:spike", native="Story"),
                                              but(TYPES, "not executable", inflight_compute="executable")),
        "issues-unreadable": (typed("type:story", fail=("issues",)), same(TYPES, "unreadable")),
    },
    "executor_label_routing": {
        "claude-opus": (routed(OPUS), same(ROUTE, "loop")),
        "codex": (routed(CODEX), but(ROUTE, "no loop", EXECUTOR_ROUTING="skipped")),
        "claude-then-codex": (routed(OPUS, CODEX), but(ROUTE, "no loop", EXECUTOR_ROUTING="loop")),
        "two-claude-tiers": (routed(OPUS, "executor:claude-code-sonnet"), but(ROUTE, "no loop", EXECUTOR_ROUTING="loop")),
        "codex-then-claude": (routed(CODEX, OPUS), but(ROUTE, "no loop", EXECUTOR_ROUTING="skipped")),
        "no-executor-label": (routed(), but(ROUTE, "no loop", EXECUTOR_ROUTING="stopped", load_story_context="skipped")),
        "issues-unreadable": (routed(OPUS, fail=("issues",)), same(ROUTE, "unreadable")),
    },
    "stale_spec_gate": {
        "current-record": (staled(RECORD_OK[:7]), same(STALE_COPIES, "allowed")),
        "stale-record-buffer-open": (staled(STALE), same(STALE_COPIES, "blocked")),
        "stale-record-buffer-full": (staled(STALE, READY_BUFFER_MIN="0"), but(STALE_COPIES, "blocked", claim="allowed")),
        "record-unreadable": (staled(UNREAD, fail=(UNREAD,)), but(STALE_COPIES, "unreadable", claim="allowed")),
        "specs-unreadable": (staled(RECORD_OK[:7], fail=("specs",)), same(STALE_COPIES, "unreadable")),
    },
}  # fmt: skip

# Every case whose copies decide differently, with its resolution class under the #525 divergence decision.
DIVERGENCES: dict[tuple[str, str], tuple[str, str]] = {
    ("wip_limit", "board-lags-pr-facts"): (
        "contract", "ADR-010 (Decision 2: the board never triggers work) and DISPATCHER-CONFIG.md (--in-implementation "
                    "from PR facts): the Codex worker's refine source counts the stories in implementation from PR "
                    "facts too, not from the board status."),
    ("wip_limit", "board-only-in-implementation"): ("contract", "As board-lags-pr-facts."),
    ("wip_limit", "target-in-implementation"): (
        "contract", "A story with a draft linked PR already holds its WIP place (DISPATCHER-CONFIG.md, fix rounds): "
                    "the count leaves it out, as Step 5 does (#496); that it is not started again is the in-flight "
                    "rule's decision, which the scheduler applies through --in-implementation."),
    ("wip_limit", "fix-round-pr-comments-unreadable"): (
        "read defect", "inflight_compute drops a due fix round when the PR comments cannot be read, so the story "
                       "frees its WIP slot: fail closed and start nothing new while the count is unknown."),
    ("wip_limit", "running-story-unreadable"): (
        "read defect", "Step 5 stops when a running story cannot be read; the scheduler needs no such read. The "
                       "consolidated rule keeps the fail-closed read, since the file overlap needs that body."),
    ("file_overlap", "running-story-without-parent-epic"): (
        "read defect", "The facts give files_affected only for an issue under an open Epic, so the scheduler skips the "
                       "check for a running story without one; read the files of every story in implementation, as "
                       "Step 6 does."),
    ("next_new_work_selection", "board-in-implementation-first"): (
        "contract", "ADR-010 (Decision 2): the next action comes from GitHub facts, never from the board status; the "
                    "Codex queue order by status (DISPATCHER-CONFIG.md, Queue and claim) gives way to it."),
    ("next_new_work_selection", "two-prio-labels"): (
        "PO decision", "No documented contract says which of two prio: labels counts: the facts take the first, the "
                       "Codex queue the lowest."),
    ("next_new_work_selection", "epic-prio-first"): (
        "PO decision", "DISPATCHER-CONFIG.md documents both orders, the scheduler's (Epic prio first) and the Codex "
                       "queue's (story prio, then number); the PO chooses one."),
    ("next_new_work_selection", "story-comments-unreadable"): (
        "read defect", "Both start nothing; the scheduler reports idle where claim reports unreadable. Report the "
                       "failed read, as claim does."),
    ("refine_executor_split", "codex-label-first-of-two"): (
        "contract", "DISPATCHER-CONFIG.md (Queue and claim) needs exactly one executor: label, executor:codex: the "
                    "facts' first-label rule must count the labels too, or nobody refines the story."),
    ("executable_issue_type", "story-and-feature-labels"): (
        "contract", "DISPATCHER-CONFIG.md (Executable Issue Types): more than one type: label is non-executable, as "
                    "classify_issue_type decides."),
    ("executable_issue_type", "feature-and-story-labels"): ("contract", "As story-and-feature-labels."),
    ("executable_issue_type", "native-type-only"): (
        "contract", "DISPATCHER-CONFIG.md (Executable Issue Types): with no type: label the native Story, Task or Bug "
                    "type is executable; the facts, _kind and TYPE_LABELS read the native type too."),
    ("executable_issue_type", "other-type-label-and-native-story"): (
        "contract", "DISPATCHER-CONFIG.md: the native type is only a fallback when there is no type: label; another "
                    "type: label is non-executable, so the in-flight filter follows classify_issue_type."),
    ("executor_label_routing", "codex"): (
        "contract", "ADR-012 and DISPATCHER-CONFIG.md: the dispatcher starts no /implement for executor:codex (no "
                    "EXECUTOR_PROVIDER row), the Codex worker does; resume and in-flight route its PR to review."),
    ("executor_label_routing", "codex-then-claude"): ("contract", "As codex: Step 2 takes the first label."),
    ("executor_label_routing", "claude-then-codex"): (
        "contract", "DISPATCHER-CONFIG.md: a review-loop story has exactly one executor: label (MERGE-POLICY.md too): "
                    "Step 2 must count the labels, or the story gets /implement and a hand-off but never a fix round."),
    ("executor_label_routing", "two-claude-tiers"): ("contract", "As claude-then-codex."),
    ("executor_label_routing", "no-executor-label"): (
        "contract", "DISPATCHER-CONFIG.md (Executable Issue Types): the executor-label check stays fatal for an "
                    "executable story; resume skips it and in-flight only tracks it, which needs no label."),
    ("stale_spec_gate", "stale-record-buffer-full"): (
        "contract", "DISPATCHER-CONFIG.md (Stale spec record, F2.7): no session for a stale story until it is "
                    "re-refined; claim must check the record before /implement, not only through the refine queue."),
    ("stale_spec_gate", "record-unreadable"): (
        "read defect", "claim starts /implement when the story's record cannot be read: fail closed, as the scheduler "
                       "and resume_session_allowed do."),
}

REQUIRED_KINDS = {
    "unreadable facts": [("wip_limit", "open-prs-unreadable"), ("wip_limit", "running-story-unreadable"),
                         ("file_overlap", "open-issues-unreadable"), ("next_new_work_selection", "issues-unreadable"),
                         ("next_new_work_selection", "open-prs-unreadable"), ("refine_executor_split", "issues-unreadable"),
                         ("executable_issue_type", "issues-unreadable"), ("executor_label_routing", "issues-unreadable"),
                         ("stale_spec_gate", "record-unreadable"), ("stale_spec_gate", "specs-unreadable")],
    "failed marker read": [("wip_limit", "fix-round-pr-comments-unreadable"), ("file_overlap", "running-comments-unreadable"),
                           ("next_new_work_selection", "story-comments-unreadable"),
                           ("refine_executor_split", "story-comments-unreadable")],
    "more than 100 open PRs": [("wip_limit", "more-than-100-open-prs"),
                               ("next_new_work_selection", "more-than-100-open-prs")],
    # Unknown mergeability: no rule of this group reads it (module docstring).
}  # fmt: skip

DEFERRED: set[str] = set()

ALL_CASES = [(rule, case) for rule, cases in CASES.items() for case in cases]


@pytest.mark.parametrize(("rule", "case"), ALL_CASES, ids=[f"{r}-{c}" for r, c in ALL_CASES])
def test_ac1_each_copy_decides_as_recorded(rule, case, tmp_path, monkeypatch):
    f, baseline = CASES[rule][case]

    decisions = DECIDE[rule](f, tmp_path, monkeypatch)

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


def test_ac1_each_rule_of_the_group_is_recorded_or_deferred():
    group = {"wip_limit", "limits_valid", "file_overlap", "next_new_work_selection", "refine_executor_split",
             "executable_issue_type", "executor_label_routing", "stale_spec_gate"}  # fmt: skip
    assert set(CASES) | DEFERRED == group and not set(CASES) & DEFERRED


def test_ac1_each_rule_has_an_agreeing_case():
    for rule, cases in CASES.items():
        assert any(len(set(baseline.values())) == 1 for _, baseline in cases.values()), rule


def test_ac1_inventory_implementations_have_parity_or_shared_owner_coverage():
    doc = (REPO_ROOT / "docs" / "DISPATCHER-DECISIONS.md").read_text(encoding="utf-8")
    # #557 consolidated these rules after the baseline was recorded. Their
    # owner has direct parity tests; the baseline still checks caller behavior.
    owner_rules = {"wip_limit": "check_start", "file_overlap": "check_start",
                   "executable_issue_type": "classify_executable_type", "executor_label_routing": "executor_labels"}
    owner_test = REPO_ROOT / "tests/test_dispatcher_admission.py"
    owner_calls = {node.func.id for node in ast.walk(ast.parse(owner_test.read_text(encoding="utf-8")))
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    for rule, cases in CASES.items():
        row = next(line for line in doc.splitlines() if line.startswith(f"| `{rule}` |"))
        files = {Path(p).name for p in re.findall(r"`((?:scripts|src)/[^`]+)`", row.split(" | ")[2])}
        if "admission.py" in files:
            assert "`src/headless_pr_workflow/dispatcher/admission.py` (owner)" in row
            assert "`test_dispatcher_admission.py`" in row
            assert owner_rules[rule] in owner_calls, rule
            files.remove("admission.py")
        copies = {m for _, baseline in cases.values() for name in baseline
                  for m in re.findall(r"([\w.-]+\.(?:py|sh)):", name)}  # fmt: skip
        assert files <= copies, (rule, files - copies)


def test_doc_links_each_rule_to_its_fixtures_and_lists_each_divergence():
    doc = (REPO_ROOT / "docs" / "DISPATCHER-DECISIONS.md").read_text(encoding="utf-8")
    section = doc.split("## Parity baseline", 1)[1]
    assert "tests/test_dispatcher_parity_new_work.py" in section
    for rule in CASES:
        assert f"`{rule}`" in section, rule
    for (rule, case), (kind, _) in DIVERGENCES.items():
        assert re.search(rf"`{rule}`[^\n]*`{case}`[^\n]*{kind}", section), (rule, case)
