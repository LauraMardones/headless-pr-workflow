"""Parity baseline for the review-loop, fix-round and marker rules (issue #582, Epic #519, Feature #525).

Each rule of this group in the ``docs/DISPATCHER-DECISIONS.md`` inventory has more than one
implementation. For every case below, one set of GitHub facts (the story's comments, the PR, its
conversation comments, its ``pytest`` check runs, the executor labels) goes through each copy of
the rule, as its entry point calls it, and the decision of each copy is recorded in ``CASES``.

Shell copies run for real in bash (``scripts/lib/*.sh`` sourced, ``scripts/dispatcher-invoke.sh``
functions extracted), and the marker shims run the real ``hpw dispatcher marker`` block, against a
fake ``gh`` executable on ``PATH`` that serves the facts and fails a read the case names. Python
copies get a fake gateway whose failing reads raise ``GatewayError``. The harness follows the
conventions of ``tests/test_dispatcher_parity_in_flight.py`` (#571).

``DIVERGENCES`` lists every case where the copies decide differently, with the resolution class of
the #525 divergence decision (``contract``, ``read defect`` or ``PO decision``). A case whose copies
agree must not be in it, and a divergent case must be. Nothing here changes a decision.

No rule of this group reads the open-PR list: each takes the linked PR that ``linked_pr_lookup``
found, whose paging (more than 100 open PRs) the #571 baseline records. Here the PR's own reads
fail instead: its detail, its comments, its check runs.
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

from headless_pr_workflow.dispatcher.gateway import GatewayError
from headless_pr_workflow.dispatcher.in_flight import plan_in_flight
from headless_pr_workflow.dispatcher.marker import marker_holds
from headless_pr_workflow.dispatcher.session_blocker import read_session_blocker
from headless_pr_workflow.review_policy import CODEX_REVIEW_TRIGGER_PHRASE, is_codex_review_request
from tests.dispatcher_fakes import BASH, REPO_ROOT, needs_tools
from tests.script_tools import windows_env
from tests.test_dispatcher_parity_in_flight import MERGE_GATE, RESOLUTION_CLASSES

pytestmark = [
    needs_tools,
    pytest.mark.skipif(sys.platform == "win32",
                       reason="the real hpw runs gh through Python subprocess, which cannot start the bash gh fake"),
]  # fmt: skip

REPO = "owner/repo"
STORY = 500
PR = 900
HEAD = "a" * 40
OLD = "b" * 40
BOT, BK, PO, OTHER, MEMBER = "hpw-bot", "hpw-bookkeeping", "po-login", "someone-else", "member-login"
T_FIND, T_EARLY, T0, T1, T2 = (f"2026-11-10T{h}:00:00Z" for h in ("08", "09", "10", "11", "12"))
INVOKE_TEXT = (REPO_ROOT / "scripts" / "dispatcher-invoke.sh").read_text(encoding="utf-8")
POLL_TEXT = (REPO_ROOT / "scripts" / "dispatcher-poll.sh").read_text(encoding="utf-8")
LIB = REPO_ROOT / "scripts" / "lib"
ENV = {"DISPATCHER_LOGIN": BOT, "CODEX_BOOKKEEPING_LOGIN": BK, "DECISION_BLOCKER_PO_LOGIN": PO}


# ── Facts ────────────────────────────────────────────────────────────────────


def marker(text: str) -> str:
    return f"<!-- hpw:dispatcher {text} -->"


def findings(head: str = HEAD) -> str:
    return f"<!-- hpw:codex-review-bookkeeping action=move-in-implementation head={head} -->"


ATTEMPT = marker(f"action=fix-round-attempt pr={PR} head={HEAD}")
PENDING = marker(f"action=hand-off-pending pr={PR} head={HEAD}")
PENDING_OLD = marker(f"action=hand-off-pending pr={PR} head={OLD}")
DONE = marker(f"action=hand-off-done pr={PR}")
OWNER = marker(f"action=merge-owner pr={PR} head={HEAD}")
OWNER_PENDING = marker(f"action=evidence-pending item=merge-owner head={HEAD} ref={PR}")
MERGE_ATTEMPT = marker(f"action=merge-attempt pr={PR} head={HEAD}")
CONFLICT_ATTEMPT = marker(f"action=conflict-round-attempt pr={PR} head={HEAD}")


def comment(author: str, body: str, at: str = T0, association: str = "NONE", user_type: str = "User") -> dict[str, Any]:
    return {"user": {"login": author, "type": user_type}, "author_association": association, "body": body,
            "created_at": at, "html_url": f"https://github.com/{REPO}/issues/{STORY}#c-{at}"}  # fmt: skip


def unblock(at: str) -> dict[str, Any]:
    return comment(PO, "/unblock", at, association="OWNER")


def run(conclusion: str | None, at: str = T0, name: str = "pytest") -> dict[str, Any]:
    return {"name": name, "status": "completed" if conclusion else "in_progress", "conclusion": conclusion,
            "started_at": at}  # fmt: skip


def facts(*, story: list | None = None, prc: list | None = None, mergeable: bool | None = True,
          checks: list | None = None, labels: tuple[str, ...] = ("claude-code-opus",), gates_pass: bool = False,
          fail: tuple[str, ...] = (), draft: bool = False, head_before: str = "") -> dict[str, Any]:  # fmt: skip
    """``fail`` names the reads that fail: ``story`` and ``prc`` (comments), ``pr`` (detail), ``checks``, ``list``
    (the open-PR list the linked PR comes from; the story's PR is in it unless ``linked`` is false, see ``no_pr``).
    ``head_before`` is the head the /implement session started on (the hand-off's first argument)."""

    state = {True: "clean", False: "dirty", None: "unknown"}[mergeable]
    return {"story": story or [], "prc": prc or [], "checks": checks or [], "labels": labels, "gates_pass": gates_pass,
            "head_before": head_before,
            "pr": {"number": PR, "state": "open", "draft": draft, "body": f"Closes #{STORY}", "head": {"sha": HEAD}, "mergeable": mergeable,
                   "mergeable_state": state}, "fail": set(fail)}  # fmt: skip


class FakeGateway:
    """The reads ``marker_holds`` uses; a failing read raises ``GatewayError``."""

    def __init__(self, f: dict[str, Any]) -> None:
        self.f = f

    def viewer_login(self) -> str:
        return BOT

    def list_comments(self, number: int) -> list[dict[str, Any]]:
        key = "story" if number == STORY else "prc"
        if key in self.f["fail"]:
            raise GatewayError(f"read comments of #{number}", "HTTP 502")
        return list(self.f[key])


# ── Running the shell copies ─────────────────────────────────────────────────

FAKE_GH = f"""#!/usr/bin/env bash
path=""
for a in "$@"; do [[ "$a" == repos/* || "$a" == user ]] && path="$a"; done
case "$path" in
    user) echo '{{"login": "{BOT}"}}' ;;
    */issues/{STORY}/comments*) [[ -f "$FACTS/fail-story" ]] && exit 1; cat "$FACTS/story.json" ;;
    */issues/{PR}/comments*) [[ -f "$FACTS/fail-prc" ]] && exit 1; cat "$FACTS/prc.json" ;;
    */pulls\\?state=open*) [[ -f "$FACTS/fail-list" ]] && exit 1; cat "$FACTS/list.json" ;;
    */pulls/{PR}) [[ -f "$FACTS/fail-pr" ]] && exit 1; cat "$FACTS/pr.json" ;;
    */check-runs*) [[ -f "$FACTS/fail-checks" ]] && exit 1; cat "$FACTS/checks.json" ;;
    *) echo "fake gh: unsupported $*" >&2; exit 1 ;;
esac
"""

INVOKE_FUNCTIONS = (
    "dispatcher_marker", "marker_block", "resolve_dispatcher_login", "marker_holds", "bot_comments_json",
    "marker_list_json", "bookkeeping_fix_marker_init", "bookkeeping_login", "head_in_list", "fix_round_heads",
    "fix_round_due", "fix_round_attempt_marker", "fix_round_attempt_superseded", "hand_off_pending_marker",
    "hand_off_pending_prefix", "hand_off_done_marker", "complete_pending_hand_off", "merge_owner_marker",
    "head_has_merge_owner_marker", "merge_attempt_marker", "pytest_green_on_head", "conflict_round_state",
    "is_review_loop_story", "codex_review_trigger_phrase", "review_request_after", "hand_off_to_codex_review_loop",
    "find_linked_pr_json", "pr_conflicts_with_main", "no_push_round_is_expected", "latest_decision_blocker_json",
)  # fmt: skip


def _function(name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", INVOKE_TEXT, re.MULTILINE | re.DOTALL)
    assert match, f"{name} not found in scripts/dispatcher-invoke.sh"
    return match.group(0)


def _poll_mergeability_filter() -> str:
    """The poll's filter that skips a PR whose mergeability GitHub has not computed."""

    match = re.search(r"jq -e '(\.mergeable != null[^']*)'", POLL_TEXT)
    assert match, "the poll's mergeability filter was not found in scripts/dispatcher-poll.sh"
    return match.group(1)


def bash(tmp: Path, f: dict[str, Any], body: str) -> tuple[int, str]:
    """Run ``body`` in bash after writing the facts to files under ``tmp``; ``(exit code, stdout)``."""

    tmp.mkdir(parents=True, exist_ok=True)
    for name in ("story", "prc", "pr"):
        (tmp / f"{name}.json").write_text(json.dumps(f[name]), encoding="utf-8")
    (tmp / "checks.json").write_text(json.dumps({"total_count": len(f["checks"]), "check_runs": f["checks"]}),
                                     encoding="utf-8")  # fmt: skip
    (tmp / "list.json").write_text(json.dumps([] if f.get("no_pr") else [f["pr"]]), encoding="utf-8")
    for name in ("story", "prc", "pr", "checks", "list"):
        if name in f["fail"]:
            (tmp / f"fail-{name}").write_text("", encoding="utf-8")
    bin_dir = tmp / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8", newline="\n")
    gh.chmod(0o755)
    hpw = tmp / "hpw"
    hpw.write_text("#!/usr/bin/env bash\n"
                   f'PYTHONPATH="{(REPO_ROOT / "src").as_posix()}" exec "{Path(sys.executable).as_posix()}" '
                   "-c 'import sys; from headless_pr_workflow.cli import main; sys.exit(main())' \"$@\"\n",
                   encoding="utf-8", newline="\n")  # fmt: skip
    hpw.chmod(0o755)
    labels = " ".join(f'"{label}"' for label in f["labels"])
    script = "\n".join([
        "set -uo pipefail", f'export FACTS="{tmp.as_posix()}"', f'export PATH="{bin_dir.as_posix()}:$PATH"',
        f'REPO="{REPO}"', f'HPW_BIN="{hpw.as_posix()}"', f'DISPATCHER_LOGIN="{BOT}"',
        f'CODEX_BOOKKEEPING_LOGIN="{BK}"', 'LOG_PREFIX=""',
        f'export DECISION_BLOCKER_PO_LOGIN="{PO}"',  # set for every dispatcher job by .github/workflows/dispatcher.yml
        f'REVIEW_POLICY_FILE="{(REPO_ROOT / "src" / "headless_pr_workflow" / "review_policy.py").as_posix()}"',
        "DRY_RUN=false", f'ISSUE_NUMBER={STORY}', 'TARGET_TITLE="Story"', 'DECLARED_BY=dispatcher',
        'LAST_ACTION=""', 'DISPATCH_HANDLED=false', "declare -A CODEX_REVIEW_LOOP_ROUTING=([claude-code-opus]=codex)",
        "RESUME_SESSIONS=0", 'NON_EXECUTABLE_ISSUES=""', 'post_comment() { echo "posted"; }',
        f"LABELS=({labels})", 'STORY_EXECUTOR_LABEL_COUNT=${#LABELS[@]}', 'EXECUTOR_LABEL="${LABELS[0]:-}"',
        f'source "{(LIB / "review-routing.sh").as_posix()}"', f'source "{(LIB / "conflict-round.sh").as_posix()}"',
        *(_function(name) for name in INVOKE_FUNCTIONS), "bookkeeping_fix_marker_init", body])  # fmt: skip
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True, env=windows_env(), timeout=120,
                          check=False)  # fmt: skip
    return proc.returncode, proc.stdout.strip()


def plan(f: dict[str, Any]) -> dict[str, Any]:
    """``plan_in_flight`` (poll and resume) for the story's row, with the comments it could read."""

    row = {"number": STORY, "title": "Story", "executors": list(f["labels"]), "review_loop": True,
           "pr": {"number": PR, "draft": False, "head": HEAD}, "merged": False, "gates_pass": f["gates_pass"],
           "story_comments": None if "story" in f["fail"] else f["story"],
           "pr_comments": None if "prc" in f["fail"] else f["prc"]}  # fmt: skip
    return plan_in_flight([row], dispatcher=BOT, bookkeeping=BK, po=PO)


def step(f: dict[str, Any]) -> str:
    result = plan(f)
    return next((name for name in ("merge", "fix_round", "hand_off", "review") if result[name]), "none")


def resume_rule(tmp: Path, f: dict[str, Any]) -> str:
    """``conflict_round_state`` as ``check_review_loop_pr`` (resume) calls it: the rule's word, or ``unreadable``."""

    rc, out = bash(tmp, f, f"conflict_round_state {STORY} {PR}")
    return out.split()[0] if rc == 0 and out else "unreadable"


def poll_rule(tmp: Path, f: dict[str, Any]) -> str:
    """The poll's review-loop branch: a review-step row, a mergeability filter, then ``conflict_round_rule``."""

    if step(f) != "review":
        return "not checked"
    if "pr" in f["fail"] or "story" in f["fail"] or "prc" in f["fail"]:
        return "unreadable"
    rc, _ = bash(tmp / "filter", f, f"jq -e '{_poll_mergeability_filter()}' \"$FACTS/pr.json\" >/dev/null")
    if rc:
        return "skipped (mergeability unknown)"
    rc, out = bash(tmp / "rule", f, 'cat "$FACTS/pr.json" "$FACTS/story.json" "$FACTS/prc.json" '
                                    f'| conflict_round_rule "{BOT}" "{BK}" "{PO}"')  # fmt: skip
    return out if rc == 0 and out else "unreadable"


def holds(f: dict[str, Any], number: int, text: str, cancel: str = "") -> str:
    return marker_holds(FakeGateway(f), number, text, cancel=cancel, env=ENV).result


# ── Rule decisions ───────────────────────────────────────────────────────────


def fix_round_due_decisions(f: dict[str, Any], tmp: Path) -> dict[str, str]:
    """Whether a fix round is due on the head: ``due``, ``not due`` or ``unreadable``."""

    rc, _ = bash(tmp / "invoke", f, f"fix_round_due {PR} {HEAD}")
    word = resume_rule(tmp / "rule", f)
    return {
        "in_flight.py:findings_on_head (plan_in_flight)": "due" if step(f) == "fix_round" else "not due",
        "dispatcher-invoke.sh:fix_round_due": "due" if rc == 0 else "not due",
        "conflict-round.sh:fix_due": "unreadable" if word == "unreadable" else ("due" if word == "fix-round-due" else "not due"),
    }  # fmt: skip


def fix_round_attempt_once_per_head_decisions(f: dict[str, Any], tmp: Path) -> dict[str, str]:
    """With findings on the head: ``round`` (no attempt holds) or ``no round`` (tried, or the facts unreadable)."""

    rc, _ = bash(tmp / "invoke", f, f'marker_holds {STORY} "$(fix_round_attempt_marker {PR} {HEAD})" 0 '
                                    f"&& ! fix_round_attempt_superseded {STORY} {PR} {HEAD}")  # fmt: skip
    return {
        "dispatcher-invoke.sh:fix_round_attempt_marker + fix_round_attempt_superseded": "no round" if rc == 0 else "round",
        "conflict-round.sh:conflict_round_rule": "round" if resume_rule(tmp / "rule", f) == "fix-round-due" else "no round",
    }  # fmt: skip


def fix_round_max_decisions(f: dict[str, Any], tmp: Path) -> dict[str, str]:
    """``fix round`` or ``blocked`` (above ``CODEX_FIX_ROUND_MAX``, the invoke default) for a head with findings."""

    maximum = int(re.search(r'^CODEX_FIX_ROUND_MAX="\$\{CODEX_FIX_ROUND_MAX:-(\d+)\}"$', INVOKE_TEXT, re.MULTILINE)[1])
    rc, out = bash(tmp, f, f'heads=$(fix_round_heads {PR}); head_in_list {HEAD} "$heads" || exit 1\n'
                           "wc -l <<< \"$heads\" | tr -d '[:space:]'")  # fmt: skip
    invoke = "none" if rc else ("blocked" if int(out) > maximum else "fix round")
    return {
        "dispatcher-invoke.sh:CODEX_FIX_ROUND_MAX (resume_fix_round)": invoke,
        "in_flight.py:plan_in_flight": "fix round" if step(f) == "fix_round" else "none",
    }


def no_push_round_expected_decisions(f: dict[str, Any], tmp: Path) -> dict[str, str]:
    """Whether the PR's one comment after ``T_EARLY`` counts as a fresh review request: ``request`` or ``none``."""

    rc, _ = bash(tmp, f, f"review_request_after {PR} {T_EARLY}")
    if "prc" in f["fail"]:
        python = "none"  # the caller reads no comment, so finds no request
    else:
        events = [SimpleNamespace(kind="comment", author_type=c["user"]["type"], body=c["body"]) for c in f["prc"]
                  if c["created_at"] > T_EARLY]  # fmt: skip
        python = "request" if any(is_codex_review_request(e) for e in events) else "none"
    return {"review_policy.py:is_codex_review_request": python,
            "dispatcher-invoke.sh:review_request_after": "request" if rc == 0 else "none"}  # fmt: skip


def hand_off_pending_decisions(f: dict[str, Any], tmp: Path) -> dict[str, str]:
    """Whether a pending hand-off on the ready PR is completed: ``pending`` or ``none``."""

    _, out = bash(tmp / "prefix", f, f"complete_pending_hand_off {STORY} item {PR} false")
    rc, _ = bash(tmp / "head", f, f'marker_holds {STORY} "$(hand_off_pending_marker {PR} {HEAD})" 1 '
                                  f'"$(hand_off_done_marker {PR})"')  # fmt: skip
    return {
        "in_flight.py:hand_off_pending (plan_in_flight)": "pending" if step(f) == "hand_off" else "none",
        "dispatcher-invoke.sh:complete_pending_hand_off": "pending" if "posted" in out else "none",
        "dispatcher-invoke.sh:hand_off_pending_marker (resume_fix_round)": "pending" if rc == 0 else "none",
    }  # fmt: skip


def merge_owner_evidence_decisions(f: dict[str, Any], tmp: Path) -> dict[str, str]:
    """Whether the clean head has merge-owner evidence: ``owned``, ``not owned`` or ``unreadable``."""

    rc_merge, _ = bash(tmp / "merge", f, f'"$HPW_BIN" dispatcher marker holds --repo "$REPO" --number {PR} '
                                         f'--marker "action=merge-owner pr={PR} head={HEAD} -->"')  # fmt: skip
    rc_head, _ = bash(tmp / "head", f, f"head_has_merge_owner_marker {PR} {HEAD}")

    def word(w: str) -> str:
        return {"clean": "owned", "ownerless": "not owned", "ownerless-reported": "not owned"}.get(w, w)

    return {
        "dispatcher-merge.sh:holds_rc (marker.py:marker_holds)": {0: "owned", 1: "not owned"}.get(rc_merge, "unreadable"),
        "conflict-round.sh:owned (resume)": word(resume_rule(tmp / "rule", f)),
        "dispatcher-invoke.sh:head_has_merge_owner_marker": "owned" if rc_head == 0 else "not owned",
        "dispatcher-poll.sh:OWNER_MISSING_DUE": word(poll_rule(tmp / "poll", f)),
    }  # fmt: skip


def attempt_once_per_head_decisions(f: dict[str, Any], tmp: Path) -> dict[str, str]:
    """Whether a /merge session may start on the head: ``attempt`` or ``no attempt`` (held, or unreadable)."""

    rc, _ = bash(tmp, f, f'marker_holds {STORY} "$(merge_attempt_marker {PR} {HEAD})" 0')
    owner = holds(f, STORY, MERGE_ATTEMPT)
    return {"marker.py:marker_holds": "attempt" if owner in ("absent", "unblocked", "cancelled") else "no attempt",
            "dispatcher-invoke.sh:merge_attempt_marker": "no attempt" if rc == 0 else "attempt"}  # fmt: skip


def conflict_round_owed_decisions(f: dict[str, Any], tmp: Path) -> dict[str, str]:
    """What each entry point does about a conflicting PR: ``owed``, ``nothing``, ``unreadable`` or ``not checked``."""

    def word(w: str) -> str:
        return w if w in ("owed", "unreadable", "not checked") else "nothing"

    return {"conflict-round.sh:conflict_round_rule (dispatcher-invoke.sh:check_review_loop_pr)": word(resume_rule(tmp / "rule", f)),
            "dispatcher-poll.sh:CONFLICT_DUE": word(poll_rule(tmp / "poll", f))}  # fmt: skip


def pytest_check_green_decisions(f: dict[str, Any], tmp: Path) -> dict[str, str]:
    """Whether the head's checks let the PR go on: ``green`` or ``not green``."""

    rc, _ = bash(tmp, f, f"pytest_green_on_head {HEAD}")
    if "checks" in f["fail"]:
        gate = "unreadable"  # merge-gate-summary's own error path: checks=fail
    else:
        bucket = {"success": "success", "failure": "failure", None: "pending"}
        latest = {r["name"]: r for r in sorted(f["checks"], key=lambda r: r["started_at"])}  # one entry per check name
        context = SimpleNamespace(status_checks=[SimpleNamespace(name=r["name"], bucket=bucket[r["conclusion"]])
                                                 for r in latest.values()])  # fmt: skip
        required = SimpleNamespace(names=("pytest",), status="available")
        gate = MERGE_GATE._checks_value(context, required, MERGE_GATE.REQUIRED_POLICY_VALUE)
    return {"merge-gate-summary:_checks_value": "green" if gate == "pass" else "not green",
            "dispatcher-invoke.sh:pytest_green_on_head": "green" if rc == 0 else "not green"}  # fmt: skip


def review_path_routing_decisions(f: dict[str, Any], tmp: Path) -> dict[str, str]:
    """The review path for the story's executor labels: ``session``, ``loop`` or ``none``."""

    _, poll = bash(tmp / "poll", f, 'if review_by_dispatcher_session "${LABELS[@]}"; then echo session\n'
                                    'elif [[ ${#LABELS[@]} -eq 1 && -n "${CODEX_REVIEW_LOOP_ROUTING[${LABELS[0]}]:-}" ]]; '
                                    "then echo loop; else echo none; fi")  # fmt: skip
    _, invoke = bash(tmp / "invoke", f, "if is_review_loop_story; then echo loop\n"
                                        'elif review_by_dispatcher_session "${LABELS[@]}"; then echo session; '
                                        "else echo none; fi")  # fmt: skip
    return {"review-routing.sh:review_by_dispatcher_session + CODEX_REVIEW_LOOP_ROUTING (poll)": poll,
            "dispatcher-invoke.sh:is_review_loop_story": invoke}


def post_implement_outcome_decisions(f: dict[str, Any], tmp: Path) -> dict[str, str]:
    """What /implement's end does: ``declaration kept`` (the story's own open Blocked Declaration stays the record),
    ``blocker`` (the dispatcher declares one), ``conflict blocker``, ``waits for PO`` or ``hand-off``.

    ``open_session_blocker`` decides only the no-PR branch; the facts of a story with a linked PR reach the hand-off
    alone, so only the no-PR cases record both copies (the overlap the story names under Risks)."""

    body = ('post_mid_cycle_blocker() { if [[ "${5:-}" == *"conflicts with main"* ]]; then echo "BLOCKER conflict"; '
            'else echo "BLOCKER generic"; fi; }\n'
            f'hand_off_to_codex_review_loop "{f["head_before"]}" "" 2>&1')
    rc, out = bash(tmp, f, "( " + body + " )")
    shell = ("declaration kept" if "open Blocked Declaration" in out else "conflict blocker" if "BLOCKER conflict" in out
             else "blocker" if "BLOCKER" in out else "waits for PO" if "waiting for the PO" in out
             else "hand-off" if "handed to the Codex review loop" in out else f"none ({rc})")
    decisions = {"dispatcher-invoke.sh:hand_off_to_codex_review_loop": shell}
    if f.get("no_pr") and "list" not in f["fail"]:
        result = read_session_blocker(FakeGateway(f), STORY, env=ENV)
        decisions["session_blocker.py:open_session_blocker"] = (
            "declaration kept" if result.fields.get("outcome") == "open_blocker" else "blocker")  # unreadable: the caller keeps its own
    return decisions


DECIDE: dict[str, Callable[[dict[str, Any], Path], dict[str, str]]] = {
    "fix_round_due": fix_round_due_decisions,
    "fix_round_attempt_once_per_head": fix_round_attempt_once_per_head_decisions,
    "fix_round_max": fix_round_max_decisions,
    "no_push_round_expected": no_push_round_expected_decisions,
    "hand_off_pending": hand_off_pending_decisions,
    "merge_owner_evidence": merge_owner_evidence_decisions,
    "attempt_once_per_head": attempt_once_per_head_decisions,
    "conflict_round_owed": conflict_round_owed_decisions,
    "pytest_check_green": pytest_check_green_decisions,
    "review_path_routing": review_path_routing_decisions,
    "post_implement_outcome": post_implement_outcome_decisions,
}


# ── Cases and the recorded baseline ──────────────────────────────────────────


def same(copies: tuple[str, ...] | list[str], decision: str) -> dict[str, str]:
    return dict.fromkeys(copies, decision)


FOUND = [comment(BK, findings(), T_FIND)]
DUE = ("in_flight.py:findings_on_head (plan_in_flight)", "dispatcher-invoke.sh:fix_round_due", "conflict-round.sh:fix_due")
TRIED = ("dispatcher-invoke.sh:fix_round_attempt_marker + fix_round_attempt_superseded", "conflict-round.sh:conflict_round_rule")
MAX = ("dispatcher-invoke.sh:CODEX_FIX_ROUND_MAX (resume_fix_round)", "in_flight.py:plan_in_flight")
REQUEST = ("review_policy.py:is_codex_review_request", "dispatcher-invoke.sh:review_request_after")
HAND_OFF = ("in_flight.py:hand_off_pending (plan_in_flight)", "dispatcher-invoke.sh:complete_pending_hand_off",
            "dispatcher-invoke.sh:hand_off_pending_marker (resume_fix_round)")  # fmt: skip
OWNERSHIP = ("dispatcher-merge.sh:holds_rc (marker.py:marker_holds)", "conflict-round.sh:owned (resume)",
             "dispatcher-invoke.sh:head_has_merge_owner_marker", "dispatcher-poll.sh:OWNER_MISSING_DUE")  # fmt: skip
ATTEMPT_COPIES = ("marker.py:marker_holds", "dispatcher-invoke.sh:merge_attempt_marker")
CONFLICT = ("conflict-round.sh:conflict_round_rule (dispatcher-invoke.sh:check_review_loop_pr)", "dispatcher-poll.sh:CONFLICT_DUE")
GREEN = ("merge-gate-summary:_checks_value", "dispatcher-invoke.sh:pytest_green_on_head")
ROUTING = ("review-routing.sh:review_by_dispatcher_session + CODEX_REVIEW_LOOP_ROUTING (poll)", "dispatcher-invoke.sh:is_review_loop_story")
PHRASE = CODEX_REVIEW_TRIGGER_PHRASE
OUTCOME = ("session_blocker.py:open_session_blocker", "dispatcher-invoke.sh:hand_off_to_codex_review_loop")
SHELL_ONLY = ("dispatcher-invoke.sh:hand_off_to_codex_review_loop",)


def declaration(by: str, kind: str = "dependency", at: str = T0, author: str = PO, association: str = "OWNER") -> dict[str, Any]:
    body = f"## Blocked Declaration\nType: {kind}\nDeclared by: {by}\nBlocks: #{STORY}\n"
    return comment(author, body, at, association=association)


def no_pr(**kw: Any) -> dict[str, Any]:
    return {**facts(**kw), "no_pr": True}


def heads(count: int) -> list[dict[str, Any]]:
    """Findings markers on ``count`` heads, the current head last."""

    return [comment(BK, findings(f"{i:x}" * 40), T_FIND) for i in range(1, count)] + FOUND


CASES: dict[str, dict[str, tuple[dict[str, Any], dict[str, str]]]] = {
    "fix_round_due": {
        "findings-on-head": (facts(prc=FOUND), same(DUE, "due")),
        "findings-on-older-head": (facts(prc=[comment(BK, findings(OLD), T_FIND)]), same(DUE, "not due")),
        "findings-by-another-account": (facts(prc=[comment(OTHER, findings(), T_FIND)]), same(DUE, "not due")),
        "findings-then-attempt": (facts(prc=FOUND, story=[comment(BOT, ATTEMPT, T0)]),
                                  {**same(DUE, "due"), "conflict-round.sh:fix_due": "not due"}),
        "pr-comments-unreadable": (facts(prc=FOUND, fail=("prc",)),
                                   {**same(DUE, "not due"), "conflict-round.sh:fix_due": "unreadable"}),
    },
    "fix_round_attempt_once_per_head": {
        "no-attempt": (facts(prc=FOUND), same(TRIED, "round")),
        "attempt": (facts(prc=FOUND, story=[comment(BOT, ATTEMPT, T0)]), same(TRIED, "no round")),
        "attempt-then-po-unblock": (facts(prc=FOUND, story=[comment(BOT, ATTEMPT, T0), unblock(T1)]), same(TRIED, "round")),
        "attempt-then-newer-findings": (facts(prc=[comment(BK, findings(), T1)], story=[comment(BOT, ATTEMPT, T0)]),
                                        same(TRIED, "round")),
        "attempt-by-another-account": (facts(prc=FOUND, story=[comment(OTHER, ATTEMPT, T0)]), same(TRIED, "round")),
        "newest-attempt-listed-first": (
            facts(prc=FOUND, story=[comment(BOT, ATTEMPT, T1), unblock(T0), comment(BOT, ATTEMPT, T_EARLY)]),
            {TRIED[0]: "round", TRIED[1]: "no round"}),
        "story-comments-unreadable": (facts(prc=FOUND, story=[comment(BOT, ATTEMPT, T0)], fail=("story",)),
                                      same(TRIED, "no round")),
    },
    "fix_round_max": {
        "third-round": (facts(prc=heads(3)), same(MAX, "fix round")),
        "fourth-round": (facts(prc=heads(4)), {MAX[0]: "blocked", MAX[1]: "fix round"}),
        "pr-comments-unreadable": (facts(prc=heads(4), fail=("prc",)), same(MAX, "none")),
    },
    "no_push_round_expected": {
        "phrase": (facts(prc=[comment(MEMBER, PHRASE, T0)]), same(REQUEST, "request")),
        "phrase-upper-case-after-blank-line": (facts(prc=[comment(MEMBER, f"\n  {PHRASE.upper()}  \nthanks", T0)]),
                                               same(REQUEST, "request")),
        "phrase-with-crlf": (facts(prc=[comment(MEMBER, f"{PHRASE}\r\nthanks", T0)]), same(REQUEST, "request")),
        "phrase-on-second-line": (facts(prc=[comment(MEMBER, f"Please\n{PHRASE}", T0)]), same(REQUEST, "none")),
        "phrase-with-more-text": (facts(prc=[comment(MEMBER, f"{PHRASE} again", T0)]), same(REQUEST, "none")),
        "phrase-by-a-bot": (facts(prc=[comment("ci[bot]", PHRASE, T0, user_type="Bot")]), same(REQUEST, "none")),
        "pr-comments-unreadable": (facts(prc=[comment(MEMBER, PHRASE, T0)], fail=("prc",)), same(REQUEST, "none")),
    },
    "hand_off_pending": {
        "pending": (facts(story=[comment(BOT, PENDING, T0)]), same(HAND_OFF, "pending")),
        "pending-then-done": (facts(story=[comment(BOT, PENDING, T0), comment(BOT, DONE, T1)]), same(HAND_OFF, "none")),
        "pending-then-po-unblock": (facts(story=[comment(BOT, PENDING, T0), unblock(T1)]), same(HAND_OFF, "none")),
        "pending-by-another-account": (facts(story=[comment(OTHER, PENDING, T0)]), same(HAND_OFF, "none")),
        "pending-for-older-head": (facts(story=[comment(BOT, PENDING_OLD, T0)]),
                                   {**same(HAND_OFF, "pending"), HAND_OFF[2]: "none"}),
        "newest-pending-listed-first": (
            facts(story=[comment(BOT, PENDING, T1), comment(BOT, DONE, T0), comment(BOT, PENDING, T_EARLY)]),
            {HAND_OFF[0]: "pending", HAND_OFF[1]: "none", HAND_OFF[2]: "none"}),
        "story-comments-unreadable": (facts(story=[comment(BOT, PENDING, T0)], fail=("story",)), same(HAND_OFF, "none")),
    },
    "merge_owner_evidence": {
        "marker-on-head": (facts(prc=[comment(BOT, OWNER, T0)]), same(OWNERSHIP, "owned")),
        "no-marker": (facts(), same(OWNERSHIP, "not owned")),
        "marker-by-another-account": (facts(prc=[comment(OTHER, OWNER, T0)]), same(OWNERSHIP, "not owned")),
        "evidence-pending-copy": (facts(prc=[comment(BOT, OWNER_PENDING, T0)]),
                                  {**same(OWNERSHIP, "not owned"), OWNERSHIP[1]: "owned", OWNERSHIP[3]: "owned"}),
        "marker-then-po-unblock": (facts(prc=[comment(BOT, OWNER, T0), unblock(T1)]),
                                   {**same(OWNERSHIP, "owned"), OWNERSHIP[0]: "not owned"}),
        "pr-comments-unreadable": (facts(prc=[comment(BOT, OWNER, T0)], fail=("prc",)),
                                   {**same(OWNERSHIP, "unreadable"), OWNERSHIP[2]: "owned"}),
        "mergeability-unknown": (facts(prc=[comment(BOT, OWNER, T0)], mergeable=None),
                                 {**same(OWNERSHIP, "owned"), OWNERSHIP[1]: "unknown",
                                  OWNERSHIP[3]: "skipped (mergeability unknown)"}),
    },
    "attempt_once_per_head": {
        "no-attempt": (facts(), same(ATTEMPT_COPIES, "attempt")),
        "attempt-on-head": (facts(story=[comment(BOT, MERGE_ATTEMPT, T0)]), same(ATTEMPT_COPIES, "no attempt")),
        "attempt-then-po-unblock": (facts(story=[comment(BOT, MERGE_ATTEMPT, T0), unblock(T1)]),
                                    same(ATTEMPT_COPIES, "attempt")),
        "attempt-by-another-account": (facts(story=[comment(OTHER, MERGE_ATTEMPT, T0)]), same(ATTEMPT_COPIES, "attempt")),
        "story-comments-unreadable": (facts(story=[comment(BOT, MERGE_ATTEMPT, T0)], fail=("story",)),
                                      same(ATTEMPT_COPIES, "no attempt")),
    },
    "conflict_round_owed": {
        "conflicting": (facts(mergeable=False), same(CONFLICT, "owed")),
        "conflicting-tried": (facts(mergeable=False, story=[comment(BOT, CONFLICT_ATTEMPT, T0)]), same(CONFLICT, "nothing")),
        "conflicting-blocked": (facts(mergeable=False, story=[comment(PO, "## Blocked Declaration\nType: dependency\n", T0)]),
                                same(CONFLICT, "nothing")),
        "mergeability-unknown": (facts(mergeable=None), same(CONFLICT, "nothing")),
        "conflicting-in-merge-step": (facts(mergeable=False, gates_pass=True), {CONFLICT[0]: "owed", CONFLICT[1]: "not checked"}),
        "conflicting-with-findings": (facts(mergeable=False, prc=FOUND), {CONFLICT[0]: "nothing", CONFLICT[1]: "not checked"}),
        "pr-unreadable": (facts(mergeable=False, fail=("pr",)), same(CONFLICT, "unreadable")),
        "story-comments-unreadable": (facts(mergeable=False, fail=("story",)), same(CONFLICT, "unreadable")),
    },
    "pytest_check_green": {
        "success": (facts(checks=[run("success")]), same(GREEN, "green")),
        "failure": (facts(checks=[run("failure")]), same(GREEN, "not green")),
        "pending": (facts(checks=[run(None)]), same(GREEN, "not green")),
        "absent": (facts(), same(GREEN, "not green")),
        "rerun-green-after-failure": (facts(checks=[run("failure", T0), run("success", T1)]), same(GREEN, "green")),
        "other-check-failing": (facts(checks=[run("success"), run("failure", name="lint")]),
                                {GREEN[0]: "not green", GREEN[1]: "green"}),
        "check-runs-unreadable": (facts(checks=[run("success")], fail=("checks",)), same(GREEN, "not green")),
    },
    "review_path_routing": {
        "claude-opus": (facts(labels=("claude-code-opus",)), same(ROUTING, "loop")),
        "claude-haiku": (facts(labels=("claude-code-haiku",)), same(ROUTING, "loop")),
        "codex": (facts(labels=("codex",)), same(ROUTING, "session")),
        "two-executors": (facts(labels=("codex", "claude-code-opus")), same(ROUTING, "none")),
        "unknown-executor": (facts(labels=("someone",)), same(ROUTING, "none")),
    },
    "post_implement_outcome": {
        "no-pr-no-blocker": (no_pr(), same(OUTCOME, "blocker")),
        "no-pr-session-declaration": (no_pr(story=[declaration("claude-code-session")]), same(OUTCOME, "declaration kept")),
        "no-pr-person-declaration": (no_pr(story=[declaration("someone", "decision", author=MEMBER, association="MEMBER")]),
                                     same(OUTCOME, "declaration kept")),
        "no-pr-session-declaration-then-po-unblock": (no_pr(story=[declaration("claude-code-session"), unblock(T1)]),
                                                      same(OUTCOME, "blocker")),
        "no-pr-dispatcher-declaration": (no_pr(story=[declaration("dispatcher", "external")]), same(OUTCOME, "blocker")),
        "no-pr-story-comments-unreadable": (no_pr(story=[declaration("claude-code-session")], fail=("story",)),
                                            same(OUTCOME, "blocker")),
        "pr-list-unreadable": (no_pr(story=[declaration("claude-code-session")], fail=("list",)),
                                same(SHELL_ONLY, "declaration kept")),
        "draft-pr": (facts(draft=True), same(SHELL_ONLY, "blocker")),
        "draft-pr-conflicting": (facts(draft=True, mergeable=False), same(SHELL_ONLY, "conflict blocker")),
        "draft-pr-mergeability-unknown": (facts(draft=True, mergeable=None), same(SHELL_ONLY, "blocker")),
        "draft-pr-unreadable": (facts(draft=True, fail=("pr",)), same(SHELL_ONLY, "blocker")),
        "ready-pr": (facts(), same(SHELL_ONLY, "hand-off")),
        "ready-pr-with-session-declaration": (facts(story=[declaration("claude-code-session")]), same(SHELL_ONLY, "hand-off")),
        "round-pushed-nothing": (facts(prc=FOUND, head_before=HEAD), same(SHELL_ONLY, "blocker")),
        "round-pushed-a-commit": (facts(prc=FOUND, head_before=OLD), same(SHELL_ONLY, "hand-off")),
        "round-pushed-nothing-waiting-for-po": (
            facts(prc=FOUND, head_before=HEAD, story=[declaration("claude-code-session", "decision")]),
            same(SHELL_ONLY, "waits for PO")),
        "round-pushed-nothing-pr-comments-unreadable": (facts(prc=FOUND, head_before=HEAD, fail=("prc",)),
                                                        same(SHELL_ONLY, "hand-off")),
    },
}  # fmt: skip

# Every case whose copies decide differently, with its resolution class under the #525 divergence decision.
DIVERGENCES: dict[tuple[str, str], tuple[str, str]] = {
    ("fix_round_due", "findings-then-attempt"): (
        "PO decision", "plan_in_flight keeps a tried round in fix_round (In implementation, counted for the WIP limit, "
                       "the poll resumable); the conflict rule treats it as done. Whether a tried round holds a WIP "
                       "place is not in a documented contract."),
    ("fix_round_due", "pr-comments-unreadable"): (
        "read defect", "plan_in_flight and fix_round_due read unreadable PR comments as no findings and route the "
                       "story to review; follow conflict_round_state and skip the story this run."),
    ("fix_round_attempt_once_per_head", "newest-attempt-listed-first"): (
        "contract", "marker_holds (#262/#293) holds the newest own marker; marker.py takes the last in list order, "
                    "the jq the newest created_at: follow created_at."),
    ("fix_round_max", "fourth-round"): (
        "contract", "CODEX_FIX_ROUND_MAX stops the rounds with a resource blocker (dispatcher-invoke.sh header); "
                    "plan_in_flight still lists the story in fix_round: apply the maximum in the plan."),
    ("hand_off_pending", "pending-for-older-head"): (
        "contract", "dispatcher-invoke.sh matches a pending hand-off by PR, not by head (issue #478); resume_fix_round "
                    "matches the full marker with the head: match by PR."),
    ("hand_off_pending", "newest-pending-listed-first"): (
        "contract", "As fix_round_attempt_once_per_head newest-attempt-listed-first: hand_off_pending takes the newest "
                    "created_at, marker.py the last in list order; follow created_at."),
    ("merge_owner_evidence", "evidence-pending-copy"): (
        "PO decision", "The conflict rule and the poll accept the evidence-pending copy (#348) as merge-owner evidence, "
                       "the merge script and head_has_merge_owner_marker only the marker; no contract says which."),
    ("merge_owner_evidence", "marker-then-po-unblock"): (
        "PO decision", "dispatcher-merge.sh uses marker holds, so a later PO /unblock voids the ownership evidence; "
                       "the other copies ignore /unblock. Needs a PO decision."),
    ("merge_owner_evidence", "pr-comments-unreadable"): (
        "read defect", "head_has_merge_owner_marker counts a failed read as owned; fail closed as the others do."),
    ("merge_owner_evidence", "mergeability-unknown"): (
        "contract", "The conflict rule decides nothing while mergeability is unknown (issue #449), and the poll skips "
                    "the PR; ownership itself is evidence on the head: the consolidated rule reads it apart from "
                    "mergeability."),
    ("conflict_round_owed", "conflicting-in-merge-step"): (
        "contract", "conflict-round.sh exists so that the poll and the resume pass cannot disagree (issue #449); the "
                    "poll applies the rule only to review-step rows: apply it to merge-step and fix-round rows too."),
    ("conflict_round_owed", "conflicting-with-findings"): (
        "contract", "As conflicting-in-merge-step, for a fix-round row; the rule gives fix-round-due for both."),
    ("pytest_check_green", "other-check-failing"): (
        "contract", "The hand-off needs only the pytest check (implement.md step 16), the merge gate every required "
                    "check (MERGE-POLICY.md): the consolidated rule takes the check names as input."),
}

REQUIRED_KINDS = {
    "unreadable facts": [("conflict_round_owed", "pr-unreadable"), ("pytest_check_green", "check-runs-unreadable"),
                         ("fix_round_due", "pr-comments-unreadable"), ("fix_round_max", "pr-comments-unreadable"),
                         ("post_implement_outcome", "draft-pr-unreadable"), ("post_implement_outcome", "pr-list-unreadable")],
    "failed marker read": [("fix_round_attempt_once_per_head", "story-comments-unreadable"),
                           ("hand_off_pending", "story-comments-unreadable"),
                           ("attempt_once_per_head", "story-comments-unreadable"),
                           ("merge_owner_evidence", "pr-comments-unreadable"),
                           ("no_push_round_expected", "pr-comments-unreadable"),
                           ("post_implement_outcome", "no-pr-story-comments-unreadable")],
    "unknown mergeability": [("conflict_round_owed", "mergeability-unknown"),
                             ("merge_owner_evidence", "mergeability-unknown"),
                             ("post_implement_outcome", "draft-pr-mergeability-unknown")],
}  # fmt: skip

# Inventory rules of this group not in CASES yet, with the follow-up that records them.
DEFERRED: set[str] = set()

ALL_CASES = [(rule, case) for rule, cases in CASES.items() for case in cases]


@pytest.mark.parametrize(("rule", "case"), ALL_CASES, ids=[f"{r}-{c}" for r, c in ALL_CASES])
def test_ac1_each_copy_decides_as_recorded(rule, case, tmp_path):
    f, baseline = CASES[rule][case]

    decisions = DECIDE[rule](f, tmp_path)

    assert decisions == baseline


@pytest.mark.parametrize(("rule", "case"), ALL_CASES, ids=[f"{r}-{c}" for r, c in ALL_CASES])
def test_ac2_a_case_is_listed_as_a_divergence_exactly_when_its_copies_differ(rule, case):
    decisions = set(CASES[rule][case][1].values())
    assert (len(decisions) > 1) == ((rule, case) in DIVERGENCES)


def test_ac2_each_divergence_has_a_resolution_class_and_a_known_case():
    for (rule, case), (kind, resolution) in DIVERGENCES.items():
        assert case in CASES[rule], (rule, case)
        assert kind in RESOLUTION_CLASSES and resolution, (rule, case)


def test_ac1_the_cases_cover_read_errors_and_unknown_mergeability():
    for kind, cases in REQUIRED_KINDS.items():
        for rule, case in cases:
            assert case in CASES[rule], (kind, rule, case)


def test_ac1_each_rule_of_the_group_is_recorded_or_deferred():
    group = {"merge_owner_evidence", "hand_off_pending", "fix_round_due", "fix_round_attempt_once_per_head",
             "fix_round_max", "attempt_once_per_head", "conflict_round_owed", "pytest_check_green",
             "no_push_round_expected", "post_implement_outcome", "review_path_routing"}  # fmt: skip
    assert set(CASES) | DEFERRED == group and not set(CASES) & DEFERRED


def test_ac1_every_implementation_file_of_the_inventory_row_is_a_recorded_copy():
    doc = (REPO_ROOT / "docs" / "DISPATCHER-DECISIONS.md").read_text(encoding="utf-8")
    for rule, cases in CASES.items():
        row = next(line for line in doc.splitlines() if line.startswith(f"| `{rule}` |"))
        files = {Path(p).name for p in re.findall(r"`((?:scripts|src)/[^`]+)`", row.split(" | ")[2])}
        copies = {m for _, baseline in cases.values() for name in baseline
                  for m in re.findall(r"([\w.-]+):", name)}  # fmt: skip
        assert files <= copies, (rule, files - copies)


def test_doc_links_each_rule_to_its_fixtures_and_lists_each_divergence():
    doc = (REPO_ROOT / "docs" / "DISPATCHER-DECISIONS.md").read_text(encoding="utf-8")
    section = doc.split("## Parity baseline", 1)[1]
    assert "tests/test_dispatcher_parity_review_loop.py" in section
    for rule in [*CASES, *DEFERRED]:
        assert f"`{rule}`" in section, rule
    for (rule, case), (kind, _) in DIVERGENCES.items():
        assert re.search(rf"`{rule}`[^\n]*`{case}`[^\n]*{kind}", section), (rule, case)
