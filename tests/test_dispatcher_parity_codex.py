"""Parity baseline for the Codex and usage rules (issue #583, Epic #519, Feature #525).

Each rule of this group in the ``docs/DISPATCHER-DECISIONS.md`` inventory is recorded here: for every
case, one set of facts goes through each copy of the rule, as its entry point calls it, and the decision
of each copy is recorded in ``CASES``. Where an inventory row names one owner file but its **Suspected:**
note compares it with another rule's copy, that copy runs on the same facts too, so the suspicion is
proved (the case is in ``DIVERGENCES``) or disproved (the copies agree).

- ``codex_eligibility_reserve``: ``is_new_work`` (through ``evaluate``, the codex-worker entry point) against
  the in-flight rule (``plan_in_flight`` behind ``inflight_compute``), which counts a story as new work when no
  open PR links it; and ``below_reserve`` called directly against ``evaluate``. Facts: the story's
  ``codex/issue-<n>-*`` branches and the open PRs. The rule reads no comment marker and no mergeability.
- ``codex_resume_branch``: the foreign-PR check of ``resolve_resume`` (``_CLOSES``) against the PR that
  ``INFLIGHT_JQ_LINKED`` links, with the same head-ref filter applied to it. Facts: the branches and the open PRs.
  The worker's own marks are read by ``claim`` and passed in, so no marker read is the rule's; no mergeability.
- ``claude_allowance_usage_gate``: the real ``scripts/dispatcher-invoke.sh`` through three entry points (invoke
  ``/implement``, the resume pass's ``/review`` and its fix round), with the fakes of ``tests/dispatcher_fakes.py``.
  Facts: ``USAGE_LIMIT_SESSIONS_PER_DAY``, the allowance script's answer and the session's stream-json. The rule
  reads no PR list, no comment marker and no mergeability (``pause_at_usage_limit`` reads the linked PR for the
  text of its comment only).
- ``evidence_retry_max``: ``write.py``'s ``retry_pending`` (``Settings.from_env``) against the shell's
  ``evidence_record_failure`` (``EVIDENCE_RETRY_MAX``). Facts: the value of ``EVIDENCE_RETRY_MAX`` and the failed-run
  markers on the story, whose read can fail. No PR list and no mergeability.

Shell copies run for real in bash; Python copies get a fake gateway whose failing reads raise ``GatewayError``.
The harness follows ``tests/test_dispatcher_parity_in_flight.py`` (#571), whose fake ``gh`` serves the in-flight copy.
``DIVERGENCES`` lists every case where the copies decide differently, with the #525 resolution class. Nothing
here changes a decision.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

from headless_pr_workflow.dispatcher.codex_reserve import Allowance, Config, InvalidConfig, Window, below_reserve, evaluate
from headless_pr_workflow.dispatcher.codex_worker import Waiting, resolve_resume
from headless_pr_workflow.dispatcher.gateway import GatewayError
from headless_pr_workflow.dispatcher.write import Settings, evidence_failed_marker, evidence_pending_comment, retry_pending
from tests.dispatcher_fakes import BASH, REPO_ROOT, Dispatcher, needs_tools
from tests.script_tools import windows_env
from tests.test_dispatcher_parity_in_flight import (
    RESOLUTION_CLASSES, bash as inflight_bash, fillers, inflight, pr, story_category,
)  # fmt: skip
from tests.test_dispatcher_parity_in_flight import facts as inflight_facts

pytestmark = [
    needs_tools,
    pytest.mark.skipif(sys.platform == "win32",
                       reason="the real hpw runs gh through Python subprocess, which cannot start the bash gh fake"),
]  # fmt: skip

STORY = 500
PR = 900
HEAD = "a" * 40
BOT = "hpw-bot"
CODEX_REF = f"codex/issue-{STORY}-x"
CLAUDE_REF = f"claude/issue-{STORY}-x"
INVOKE_TEXT = (REPO_ROOT / "scripts" / "dispatcher-invoke.sh").read_text(encoding="utf-8")


# ── Facts ────────────────────────────────────────────────────────────────────


def facts(*, branches: tuple[str, ...] = (), open_prs: list | None = None, fail: tuple[str, ...] = ()) -> dict[str, Any]:
    """The #571 in-flight facts plus the story's branches; ``fail`` adds ``branches`` to the reads that can fail."""

    return {**inflight_facts(open_prs=open_prs, fail=fail), "branches": {b: HEAD for b in branches}}


class FakeGateway:
    """The reads ``evaluate`` and ``resolve_resume`` use; a failing read raises ``GatewayError``."""

    def __init__(self, f: dict[str, Any]) -> None:
        self.f = f

    def matching_branches(self, prefix: str) -> dict[str, str]:
        if "branches" in self.f["fail"]:
            raise GatewayError(f"read branches {prefix}*", "HTTP 502")
        return {b: s for b, s in self.f["branches"].items() if b.startswith(prefix)}

    def list_open_prs(self) -> list[dict[str, Any]]:  # GhGateway reads every page (--paginate)
        if "open" in self.f["fail"]:
            raise GatewayError("read open pull requests", "HTTP 502")
        return list(self.f["open"])

    def pr_reviews(self, number: int) -> list[dict[str, Any]]:
        return []


# ── Rule: codex_eligibility_reserve ──────────────────────────────────────────

AT_RESERVE = Allowance(Window(10, None), Window(20, None))  # equal to the default Codex reserve: starts
CLAUDE_REFUSES = "Claude allowance below its reserve: weekly"


def new_work_word(verdict: Any) -> str:
    """``evaluate`` with the Codex windows at the reserve and the Claude probe refusing: the Claude checks
    (queue, reserve) run only for new work."""

    if verdict.reason in ("claude-reserve", "review-queue"):
        return "new work"
    return "not new work" if verdict.eligible else {"review-queue-unreadable": "unreadable"}.get(verdict.reason, verdict.reason)


def codex_eligibility_reserve_decisions(f: dict[str, Any], tmp: Path) -> dict[str, str]:
    """New-work cases: ``new work``, ``not new work`` or ``unreadable``. Reserve cases (``allowance`` in the facts):
    ``start``, ``refuse`` or ``reserve-config``."""

    if "allowance" in f:
        five, week = f["allowance"]
        reading = Allowance(Window(five, None), Window(week, None))
        try:
            config = Config.from_env(f["env"])
            direct = "refuse" if below_reserve(reading, config.codex_five_hour, config.codex_weekly) else "start"
        except InvalidConfig:
            direct = "reserve-config"
        verdict = evaluate(f["env"], FakeGateway(f), STORY, codex_probe=lambda env: reading,
                           claude_probe=lambda env: CLAUDE_REFUSES)  # fmt: skip
        via = "start" if verdict.eligible else {"codex-reserve": "refuse"}.get(verdict.reason, verdict.reason)
        return {"codex_reserve.py:below_reserve (decide)": direct, "codex_reserve.py:below_reserve (evaluate)": via}
    verdict = evaluate({}, FakeGateway(f), STORY, codex_probe=lambda env: AT_RESERVE, claude_probe=lambda env: CLAUDE_REFUSES)
    category = story_category(inflight(tmp, f))
    return {
        "codex_reserve.py:is_new_work (evaluate)": new_work_word(verdict),
        "in_flight.py:plan_in_flight + in-flight.sh:inflight_compute": {"none": "new work", "unreadable": "unreadable"}.get(
            category, "not new work"),
    }  # fmt: skip


# ── Rule: codex_resume_branch ────────────────────────────────────────────────


def codex_resume_branch_decisions(f: dict[str, Any], tmp: Path) -> dict[str, str]:
    """Whether an open PR on another branch closes the story and stops a Codex resume: ``foreign-pr``,
    ``no foreign PR`` or ``unreadable``. The in-flight copy applies ``resolve_resume``'s head-ref filter to the PR
    that ``INFLIGHT_JQ_LINKED`` links, so only the link differs."""

    try:
        resolve_resume(FakeGateway(f), STORY, None)
        python = "no foreign PR"
    except Waiting as exc:
        python = str(exc) if str(exc) == "foreign-pr" else f"waiting {exc} (not checked)"
    except GatewayError:
        python = "unreadable"
    rc, out = inflight_bash(tmp, f, f'prs=$(inflight_get_all "repos/$REPO/pulls?state=open") || exit 1\n'
                                    f'jq -r --argjson n {STORY} "$INFLIGHT_JQ_LINKED"\'linked(.; $n) | .head.ref // empty\' <<< "$prs"')  # fmt: skip
    shell = "unreadable" if rc else ("foreign-pr" if out and not out.startswith(f"codex/issue-{STORY}-") else "no foreign PR")
    return {"codex_worker.py:resolve_resume (_CLOSES)": python, "in-flight.sh:INFLIGHT_JQ_LINKED": shell}


# ── Rule: claude_allowance_usage_gate ────────────────────────────────────────

INIT = {"type": "system", "subtype": "init", "apiKeySource": "none", "model": "fake"}
REJECTED = {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": 1760000000}}
LIMIT_RESULT = {"type": "result", "subtype": "error_during_execution", "is_error": True, "num_turns": 2,
                "duration_ms": 1000, "result": "limit reached", "error": "rate_limit"}  # fmt: skip
SUCCESS = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 1, "duration_ms": 10, "result": "done"}


def invoke_entry(d: Dispatcher) -> tuple[Callable[..., Any], str]:
    d.add_story(STORY, "Ready to implement")
    d.add_pr(PR, STORY)
    return lambda **kw: d.run_issue(STORY, **kw), f"implement {STORY}"


def review_entry(d: Dispatcher) -> tuple[Callable[..., Any], str]:
    d.add_story(STORY, "In review", executor="codex")
    d.add_pr(PR, STORY)
    d.session_sets_status("review", STORY, "Mergeable")
    return lambda **kw: d.resume(**kw), f"review {PR}"


def fix_round_entry(d: Dispatcher) -> tuple[Callable[..., Any], str]:
    """``fix_round_due`` of tests/test_dispatcher_usage_limit.py: the resume pass runs a fix round on #500 first."""

    d.add_story(700, "Ready to implement")
    d.add_pr(970, 700)
    d.add_story(STORY, "In implementation")
    d.add_pr(PR, STORY)
    d.add_findings(PR, HEAD)
    return lambda **kw: d.run_issue(700, **kw), f"implement {STORY}"


ENTRIES = {
    "dispatcher-invoke.sh:check_usage_limit + check_subscription_allowance (invoke /implement)": invoke_entry,
    "dispatcher-invoke.sh:check_usage_limit + check_subscription_allowance (resume /review)": review_entry,
    "dispatcher-invoke.sh:check_usage_limit + check_subscription_allowance (resume fix round)": fix_round_entry,
}
PAUSE_COPY = "dispatcher-invoke.sh:pause_at_usage_limit + claude-session.sh:claude_limit_stop"


def claude_allowance_usage_gate_decisions(f: dict[str, Any], tmp: Path) -> dict[str, str]:
    """The first session of each entry point: ``start``, ``paused`` (stopped at a provider limit, run ends with exit 0)
    or ``not started, exit <rc>``. Stream cases record each entry point under the pause copy's name."""

    decisions: dict[str, str] = {}
    for name, entry in ENTRIES.items():
        (tmp / str(len(decisions))).mkdir()
        d = Dispatcher(tmp / str(len(decisions)))
        d.fake_git()
        run, first = entry(d)
        if f.get("stream"):
            d.claude_stream(*f["stream"], exit_code=f.get("exit", 0))
        proc = run(seq=f.get("seq", "0"), env={"USAGE_LIMIT_SESSIONS_PER_DAY": f.get("limit", "1000")})
        started = d.sessions[:1] == [first]
        paused = "paused at a provider usage limit" in proc.stdout
        word = ("paused" if paused and proc.returncode == 0 else "start") if started else f"not started, exit {proc.returncode}"
        key = f"{PAUSE_COPY} ({name.rsplit('(', 1)[1]}" if f.get("stream") else name
        decisions[key] = word
    return decisions


# ── Rule: evidence_retry_max ─────────────────────────────────────────────────

ITEM, REF = "merge-owner", f"pr{PR}"


class EvidenceGateway:
    """A pending merge-owner evidence on the PR, ``prior`` failed-run markers on the story; every write fails."""

    def __init__(self, prior: int, unreadable: bool) -> None:
        self.prior, self.unreadable = prior, unreadable

    def viewer_login(self) -> str:
        return BOT

    def list_comments(self, number: int) -> list[dict[str, Any]]:
        def c(body: str, i: int) -> dict[str, Any]:
            return {"user": {"login": BOT}, "body": body, "created_at": f"2026-11-10T10:00:{i:02d}Z", "id": i}

        if number == PR:
            return [c(evidence_pending_comment(ITEM, HEAD, "## Merge owner\n"), 0)]
        if self.unreadable:
            raise GatewayError(f"read comments of #{number}", "HTTP 502")
        return [c(f"failed\n\n{evidence_failed_marker(ITEM, REF)}", i + 1) for i in range(self.prior)]

    def post_comment(self, number: int, body: str) -> dict[str, Any]:
        raise GatewayError(f"write comment on #{number}", "HTTP 500")


def python_run(value: str | None, prior: int, unreadable: bool) -> str:
    env = {"COMMENT_RETRY_DELAY": "0", **({} if value is None else {"EVIDENCE_RETRY_MAX": value})}
    settings = Settings.from_env(env)
    settings = Settings(retry_delay=0, evidence_retry_max=settings.evidence_retry_max, sleep=lambda s: None)
    result = retry_pending(EvidenceGateway(prior, unreadable), PR, ITEM, REF, STORY, settings=settings)
    return {"gave-up": "blocker", "missing": "retry"}.get(result.result, result.result)


def _function(name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", INVOKE_TEXT, re.MULTILINE | re.DOTALL)
    assert match, f"{name} not found in scripts/dispatcher-invoke.sh"
    return match.group(0)


def _default_line() -> str:
    match = re.search(r'^EVIDENCE_RETRY_MAX="\$\{EVIDENCE_RETRY_MAX:-3\}"$', INVOKE_TEXT, re.MULTILINE)
    assert match, "the EVIDENCE_RETRY_MAX default was not found in scripts/dispatcher-invoke.sh"
    return match.group(0)


READ_OWN = 'jq -c --argjson k "$prior" ".[:\\$k]" <<< "$OWN"'


def shell_runs(tmp: Path, value: str | None, unreadable: bool) -> list[str]:
    """``evidence_record_failure`` for 0..3 prior failed runs, each in a subshell of the script's options."""

    own = "[" + ",".join(json.dumps({"body": evidence_failed_marker(ITEM, REF), "created_at": f"2026-11-10T10:00:{i:02d}Z"})
                         for i in range(8)) + "]"  # fmt: skip
    script = "\n".join([
        "set -uo pipefail", *([] if value is None else [f"export EVIDENCE_RETRY_MAX='{value}'"]), _default_line(),
        _function("dispatcher_marker"), _function("evidence_failed_marker"), _function("evidence_gave_up_marker"),
        _function("evidence_failure_count"), _function("evidence_record_failure"),
        "post_comment() { :; }", "marker_holds() { return 1; }", 'post_blocker_once() { echo blocker; }',
        f"OWN='{own}'",
        "for prior in 0 1 2 3; do",
        f"  bot_comments_json() {{ {'return 1' if unreadable else READ_OWN}; }}",
        f'  out=$( (evidence_record_failure {STORY} Story {ITEM} {REF} what; echo "rc=$?") 2>/dev/null)',
        '  case "$out" in *blocker*) echo blocker ;; *rc=1*) echo retry ;; *rc=2*) echo "stop, no blocker" ;;'
        '       *) echo abort ;; esac',
        "done"])  # fmt: skip
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True, env=windows_env(), timeout=60, check=False)
    return proc.stdout.split("\n")[:4] if proc.stdout else ["abort"] * 4


def evidence_retry_max_decisions(f: dict[str, Any], tmp: Path) -> dict[str, str]:
    """What runs 1 to 4 of a failing evidence write do: ``retry``, ``blocker``, ``stop, no blocker`` (retrying stops
    and no blocker is posted) or ``abort`` (the script exits)."""

    value, unreadable = f["value"], f.get("unreadable", False)
    return {
        "write.py:DEFAULT_EVIDENCE_RETRY_MAX (retry_pending)": ",".join(python_run(value, k, unreadable) for k in range(4)),
        "dispatcher-invoke.sh:EVIDENCE_RETRY_MAX (evidence_record_failure)": ",".join(shell_runs(tmp, value, unreadable)),
    }


DECIDE: dict[str, Callable[[dict[str, Any], Path], dict[str, str]]] = {
    "codex_eligibility_reserve": codex_eligibility_reserve_decisions,
    "codex_resume_branch": codex_resume_branch_decisions,
    "claude_allowance_usage_gate": claude_allowance_usage_gate_decisions,
    "evidence_retry_max": evidence_retry_max_decisions,
}


# ── Cases and the recorded baseline ──────────────────────────────────────────


def same(copies: tuple[str, ...] | list[str], decision: str) -> dict[str, str]:
    return dict.fromkeys(copies, decision)


NEW = ("codex_reserve.py:is_new_work (evaluate)", "in_flight.py:plan_in_flight + in-flight.sh:inflight_compute")
RESERVE = ("codex_reserve.py:below_reserve (decide)", "codex_reserve.py:below_reserve (evaluate)")
FOREIGN = ("codex_worker.py:resolve_resume (_CLOSES)", "in-flight.sh:INFLIGHT_JQ_LINKED")
GATE = tuple(ENTRIES)
PAUSE = tuple(f"{PAUSE_COPY} ({name.rsplit('(', 1)[1]}" for name in ENTRIES)
RETRY = ("write.py:DEFAULT_EVIDENCE_RETRY_MAX (retry_pending)", "dispatcher-invoke.sh:EVIDENCE_RETRY_MAX (evidence_record_failure)")


def reserve(five: float, week: float, **env: str) -> dict[str, Any]:
    return {**facts(branches=(CODEX_REF,)), "allowance": (five, week), "env": env}


def retry(value: str | None, **kw: Any) -> dict[str, Any]:
    return {"value": value, **kw}


CODEX_PR = pr(901, ref=CODEX_REF)
CLAUDE_PR = pr(ref=CLAUDE_REF)
CASES: dict[str, dict[str, tuple[dict[str, Any], dict[str, str]]]] = {
    "codex_eligibility_reserve": {
        "no-branch-no-pr": (facts(), same(NEW, "new work")),
        "branch-and-pr": (facts(branches=(CODEX_REF,), open_prs=[CODEX_PR]), same(NEW, "not new work")),
        "branch-without-pr": (facts(branches=(CODEX_REF,)), {**same(NEW, "new work"), NEW[0]: "not new work"}),
        "claude-pr-no-branch": (facts(open_prs=[CLAUDE_PR]), {**same(NEW, "not new work"), NEW[0]: "new work"}),
        "more-than-100-open-prs": (facts(branches=(CODEX_REF,), open_prs=fillers() + [CODEX_PR]), same(NEW, "not new work")),
        "branches-unreadable": (facts(fail=("branches",)), {**same(NEW, "new work"), NEW[0]: "unreadable"}),
        "open-prs-unreadable": (facts(fail=("open",)), same(NEW, "unreadable")),
        "codex-at-reserve": (reserve(10, 20), same(RESERVE, "start")),
        "codex-weekly-below-reserve": (reserve(10, 19.5), same(RESERVE, "refuse")),
        "codex-reserve-invalid": (reserve(50, 50, CODEX_RESERVE_WEEKLY_PERCENT="101"), same(RESERVE, "reserve-config")),
    },
    "codex_resume_branch": {
        "no-pr": (facts(branches=(CODEX_REF,)), same(FOREIGN, "no foreign PR")),
        "own-codex-pr": (facts(branches=(CODEX_REF,), open_prs=[CODEX_PR]), same(FOREIGN, "no foreign PR")),
        "claude-pr-closes": (facts(branches=(CODEX_REF,), open_prs=[CLAUDE_PR]), same(FOREIGN, "foreign-pr")),
        "claude-pr-fixes-lower-case": (facts(open_prs=[pr(body=f"fixes #{STORY}", ref=CLAUDE_REF)]), same(FOREIGN, "foreign-pr")),
        "claude-pr-closed-past-tense": (facts(branches=(CODEX_REF,), open_prs=[pr(body=f"Closed #{STORY}", ref=CLAUDE_REF)]),
                                        {**same(FOREIGN, "no foreign PR"), FOREIGN[0]: "foreign-pr"}),
        "claude-pr-other-number": (facts(open_prs=[pr(body="Closes #5000", ref=CLAUDE_REF)]), same(FOREIGN, "no foreign PR")),
        "own-pr-listed-before-foreign-pr": (facts(branches=(CODEX_REF,), open_prs=[CODEX_PR, CLAUDE_PR]),
                                            {**same(FOREIGN, "no foreign PR"), FOREIGN[0]: "foreign-pr"}),
        "more-than-100-open-prs": (facts(branches=(CODEX_REF,), open_prs=fillers() + [CLAUDE_PR]), same(FOREIGN, "foreign-pr")),
        "open-prs-unreadable": (facts(branches=(CODEX_REF,), fail=("open",)), same(FOREIGN, "unreadable")),
    },
    "claude_allowance_usage_gate": {
        "start": ({}, same(GATE, "start")),
        "usage-limit-reached": ({"limit": "0"}, same(GATE, "not started, exit 0")),
        "usage-limit-invalid": ({"limit": "many"}, same(GATE, "not started, exit 1")),
        "allowance-refused": ({"seq": "1"}, same(GATE, "not started, exit 0")),
        "allowance-unreadable": ({"seq": "2"}, same(GATE, "not started, exit 1")),
        "limit-stop-in-result": ({"stream": (INIT, REJECTED, LIMIT_RESULT), "exit": 1}, same(PAUSE, "paused")),
        "limit-stop-event-only": ({"stream": (INIT, REJECTED), "exit": 1}, same(PAUSE, "paused")),
        "rejected-event-then-success": ({"stream": (INIT, REJECTED, SUCCESS)}, same(PAUSE, "start")),
    },
    "evidence_retry_max": {
        "default": (retry(None), same(RETRY, "retry,retry,blocker,blocker")),
        "empty": (retry(""), same(RETRY, "retry,retry,blocker,blocker")),
        "one": (retry("1"), same(RETRY, "blocker,blocker,blocker,blocker")),
        "five": (retry("5"), same(RETRY, "retry,retry,retry,retry")),
        "zero": (retry("0"), same(RETRY, "blocker,blocker,blocker,blocker")),
        "negative": (retry("-1"), same(RETRY, "blocker,blocker,blocker,blocker")),
        "non-numeric": (retry("abc"), {RETRY[0]: "retry,retry,blocker,blocker", RETRY[1]: "abort,abort,abort,abort"}),
        "decimal": (retry("2.5"), {RETRY[0]: "retry,retry,blocker,blocker",
                                   RETRY[1]: "stop, no blocker,stop, no blocker,stop, no blocker,stop, no blocker"}),
        "leading-zero-eight": (retry("08"), {RETRY[0]: "retry,retry,retry,retry",
                                             RETRY[1]: "stop, no blocker,stop, no blocker,stop, no blocker,stop, no blocker"}),
        "story-comments-unreadable": (retry("2", unreadable=True), same(RETRY, "retry,retry,retry,retry")),
    },
}  # fmt: skip

# Every case whose copies decide differently, with its resolution class under the #525 divergence decision.
DIVERGENCES: dict[tuple[str, str], tuple[str, str]] = {
    ("codex_eligibility_reserve", "branch-without-pr"): (
        "contract", "DISPATCHER-CONFIG.md (Codex worker reserve) defines new Codex work as a story with no "
                    "codex/issue-<n>-* branch, Work in flight from PR facts defines the scheduler's in-flight story by "
                    "an open linked PR: two documented questions. The consolidated rule keeps both and names them apart."),
    ("codex_eligibility_reserve", "claude-pr-no-branch"): (
        "contract", "As branch-without-pr: is_new_work counts the Claude reserve for new Codex work by branch; the "
                    "foreign PR then stops the worker in resolve_resume (foreign-pr)."),
    ("codex_eligibility_reserve", "branches-unreadable"): (
        "contract", "ADR-012: when the review capacity cannot be confirmed the worker waits; is_new_work fails closed "
                    "on its branch read. The in-flight rule does not read branches, so it has nothing to fail on."),
    ("codex_resume_branch", "claude-pr-closed-past-tense"): (
        "PO decision", "As linked_pr_lookup closed-past-tense (#571): no contract names the closing keywords; "
                       "_CLOSES also accepts Closed/Fixed/Resolved, INFLIGHT_JQ_LINKED only Closes/Fixes/Resolves."),
    ("codex_resume_branch", "own-pr-listed-before-foreign-pr"): (
        "contract", "DISPATCHER-CONFIG.md (Codex worker, Recovery): no open PR from another branch may close the "
                    "issue, so every linked PR counts; INFLIGHT_JQ_LINKED keeps only the first, follow resolve_resume."),
    ("evidence_retry_max", "non-numeric"): (
        "PO decision", "EVIDENCE_RETRY_MAX is not in the DISPATCHER-CONFIG.md variable table: write.py falls back to 3, "
                       "the shell aborts the run (set -u on an unbound name). Other limits reject an invalid value and "
                       "start nothing; the PO decides whether this one does too."),
    ("evidence_retry_max", "decimal"): (
        "PO decision", "As non-numeric: write.py falls back to 3, the shell's [[ -ge ]] fails on 2.5, so retrying stops "
                       "on the first failed run without a blocker."),
    ("evidence_retry_max", "leading-zero-eight"): (
        "PO decision", "As non-numeric: write.py reads 08 as eight, bash as an invalid octal number (retrying stops "
                       "without a blocker). DISPATCHER-CONFIG.md reads 08 as eight for USAGE_LIMIT_SESSIONS_PER_DAY."),
}

REQUIRED_KINDS = {
    "unreadable facts": [("codex_eligibility_reserve", "branches-unreadable"),
                         ("codex_eligibility_reserve", "open-prs-unreadable"),
                         ("codex_resume_branch", "open-prs-unreadable"),
                         ("claude_allowance_usage_gate", "usage-limit-invalid"),
                         ("claude_allowance_usage_gate", "allowance-unreadable")],
    "failed marker read": [("evidence_retry_max", "story-comments-unreadable")],
    "more than 100 open PRs": [("codex_eligibility_reserve", "more-than-100-open-prs"),
                               ("codex_resume_branch", "more-than-100-open-prs")],
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


def test_ac1_the_cases_cover_read_errors_and_paging():
    for kind, cases in REQUIRED_KINDS.items():
        for rule, case in cases:
            assert case in CASES[rule], (kind, rule, case)


def test_ac1_each_rule_of_the_group_is_recorded_or_deferred():
    group = {"codex_eligibility_reserve", "codex_resume_branch", "claude_allowance_usage_gate", "evidence_retry_max"}
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
    assert "tests/test_dispatcher_parity_codex.py" in section
    for rule in [*CASES, *DEFERRED]:
        assert f"`{rule}`" in section, rule
    for (rule, case), (kind, _) in DIVERGENCES.items():
        assert re.search(rf"`{rule}`[^\n]*`{case}`[^\n]*{kind}", section), (rule, case)
