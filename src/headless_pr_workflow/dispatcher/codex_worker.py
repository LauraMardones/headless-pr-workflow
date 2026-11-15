"""Local Codex worker: ``hpw dispatcher codex-worker`` (issue #528, ADR-012 → Codex Plus amendment, ADR-014).

A scheduled task in the signed-in Codex app on the PO's computer runs
``scripts/codex-worker.sh claim``. The worker reads the ``executor:codex`` queue
from the board, claims one item with a marker comment (Block 2, written through
Block 1) and prints the invocation the task's session follows (#523). The
session records its pushed branch and head with ``progress`` and ends its
attempt with ``finish``. GitHub holds every fact; the worker keeps no local
state, so any later run on any computer reads the same answer.

Markers (own account only, ``<!-- hpw:dispatcher action=<action> ... -->``):

- ``codex-claim issue= attempt= owner= run= command= pr= branch= head= [fix=]``: the claim; the claim with
  the lowest comment id of an attempt owns it. ``fix`` names the reviewed head of a fix round (#536).
- ``codex-progress issue= attempt= run= pr= branch= head=``: what the owning session pushed.
- ``codex-renew issue= attempt= run=``: a replay renews the lease before its session starts; it carries no resume facts.
- ``codex-end issue= attempt= run= result=done|wait reason= head= until= by=``: the attempt is over; ``by`` names
  the run that recorded an interruption, which counts only when the lease had run out by GitHub's timestamps.
- ``codex-wait issue= attempt= reason=``: the item waits before that attempt could be claimed.
- ``fix-round-max pr=``: the ``Type: resource`` blocker posted once when a PR is above the fix-round maximum; the
  dispatcher's Codex Cloud review loop posts the same marker.

Fix rounds (#536): an ``implement`` claim whose PR's current head has a blocking review from someone other than
Codex (``CHANGES_REQUESTED``, or a review Session Summary with blockers and ``Next: implementation``) is a fix
round on that PR, branch and head. Rounds are counted per PR, one for each reviewed head with blockers; above
``CODEX_FIX_ROUND_MAX`` (default ``3``) the worker starts nothing and posts the blocker. Codex Cloud findings
markers are never counted here.

Result words and exit codes: ``claimed``, ``continued``, ``written``, ``already``, ``read``,
``dry-run`` (0); ``idle``, ``waiting``, ``lost``, ``ended``, ``refused``, ``not-owner``,
``failed`` (1); ``unreadable`` (3).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from ..review_policy import CODEX_BOT_LOGIN, CODEX_EXECUTOR_LABEL
from .gateway import Gateway, GatewayError
from .marker import Unreadable, dispatcher_marker, marker_text, resolve_login, write_marker
from .write import EXIT_NO, EXIT_OK, EXIT_UNREADABLE, Result, Settings, _author, log

BLOCK = "codex-worker"

# The #523 invocation boundary: the board status of a queued item names the HPW command its session runs.
COMMAND_BY_STATUS = {"In implementation": "implement", "Ready to implement": "implement"}
STATUS_ORDER = ("In implementation", "Ready to implement")
COMMANDS = ("refine", "implement")
COMMAND_FILES = {"refine": ".claude/commands/refine.md", "implement": ".claude/commands/implement.md"}
TYPE_LABELS = ("type:story", "type:task", "type:bug")
API_KEY_NAMES = ("OPENAI_API_KEY", "CODEX_API_KEY")

DEFAULT_LEASE_MINUTES = 240
DEFAULT_RETRY_MINUTES = 60
DEFAULT_FIX_ROUND_MAX = 3  # the default of CODEX_FIX_ROUND_MAX in scripts/dispatcher-invoke.sh
TRUSTED_ASSOCIATIONS = ("OWNER", "MEMBER", "COLLABORATOR")
ELIGIBILITY_TIMEOUT = 60

_EXIT_BY_RESULT = {
    "claimed": EXIT_OK, "continued": EXIT_OK, "written": EXIT_OK, "already": EXIT_OK, "read": EXIT_OK,
    "dry-run": EXIT_OK, "unreadable": EXIT_UNREADABLE,
}  # fmt: skip
_MARK = re.compile(r"<!-- hpw:dispatcher action=codex-(claim|progress|renew|end|wait) ([^<>]*?) -->")
_WORD = re.compile(r"^[A-Za-z0-9_.-]+$")
_PRIO = re.compile(r"^prio:P(\d+)$")
_CLOSES = r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#{n}\b"
_SUMMARY_COMMAND = re.compile(r"^Command:\s*review\s*$", re.MULTILINE)
_SUMMARY_BLOCKERS = re.compile(r"^Blockers:\s*(.*?)\s*$", re.MULTILINE)
_SUMMARY_NEXT = re.compile(r"^Next:\s*implementation\s*$", re.MULTILINE)


class Reason(str):
    """A refusal's reason word, with the hook's optional one-line ``detail`` for the ``codex-wait`` text."""

    detail: str = ""


Eligibility = Callable[[str, int, str], "tuple[bool, str]"]
RefineDue = Callable[[], "list[int]"]  # the executor:codex stories due for refinement (#535); raises GatewayError


@dataclass
class WorkerResult(Result):
    block: str = BLOCK

    @property
    def exit_code(self) -> int:
        return _EXIT_BY_RESULT.get(self.result, EXIT_NO)


class InvalidConfig(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    lease: timedelta = timedelta(minutes=DEFAULT_LEASE_MINUTES)
    retry: timedelta = timedelta(minutes=DEFAULT_RETRY_MINUTES)
    fix_round_max: int = DEFAULT_FIX_ROUND_MAX

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        env = os.environ if env is None else env
        values = {}
        for name, default, unit in (("CODEX_WORKER_LEASE_MINUTES", DEFAULT_LEASE_MINUTES, "a whole number of minutes"),
                                    ("CODEX_WORKER_RETRY_MINUTES", DEFAULT_RETRY_MINUTES, "a whole number of minutes"),
                                    ("CODEX_FIX_ROUND_MAX", DEFAULT_FIX_ROUND_MAX, "a whole number")):  # fmt: skip
            raw = (env.get(name) or "").strip()
            if raw and not re.fullmatch(r"[1-9][0-9]*", raw):
                raise InvalidConfig(f"{name} must be {unit}, 1 or more (value: {raw!r})")
            values[name] = int(raw) if raw else default
        return cls(lease=timedelta(minutes=values["CODEX_WORKER_LEASE_MINUTES"]),
                   retry=timedelta(minutes=values["CODEX_WORKER_RETRY_MINUTES"]),
                   fix_round_max=values["CODEX_FIX_ROUND_MAX"])  # fmt: skip


# ── markers ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Mark:
    action: str
    fields: dict[str, str]
    order: tuple[int, str]
    created_at: str
    body: str

    def get(self, key: str) -> str | None:
        value = self.fields.get(key)
        return None if value in (None, "-") else value


def parse_marks(own: list[dict[str, Any]]) -> list[Mark]:
    """The worker's markers in the own comments ``own``, oldest first; a token that is not ``key=value`` is kept as ``_malformed``."""

    marks = []
    for comment in own:
        body = str(comment.get("body") or "")
        for found in _MARK.finditer(body):
            fields: dict[str, str] = {}
            for token in found.group(2).split():
                key, sep, value = token.partition("=")
                fields[key if sep else "_malformed"] = value if sep else token
            try:
                cid = int(comment.get("id") or 0)
            except (TypeError, ValueError):
                cid = 0
            created = str(comment.get("created_at") or "")
            marks.append(Mark(found.group(1), fields, (cid, created), created, body))
    return sorted(marks, key=lambda m: m.order)


def branch_prefix(issue: int) -> str:
    """The branch name prefix of a Codex story (docs/WORKTREE-MODEL.md → Naming)."""

    return f"codex/issue-{issue}-"


def _time(text: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── state of one issue ────────────────────────────────────────────────────────


@dataclass
class Attempt:
    number: int
    claim: Mark
    progress: list[Mark] = field(default_factory=list)
    renewals: list[Mark] = field(default_factory=list)
    end: Mark | None = None

    def records(self) -> tuple[Mark, ...]:
        """The records that hold the lease: the claim, each progress record and each renewal."""

        return (self.claim, *self.progress, *self.renewals)

    @property
    def run(self) -> str:
        return self.claim.fields["run"]

    @property
    def owner(self) -> str:
        return self.claim.fields["owner"]

    @property
    def command(self) -> str:
        return self.claim.fields["command"]


@dataclass
class IssueState:
    """``state``: ``free`` (a new attempt may be claimed), ``held``, ``expired`` (the lease ran out) or ``conflict``."""

    issue: int
    state: str
    attempt: Attempt | None = None
    reason: str = ""
    recorded: dict[str, str] | None = None
    not_before: datetime | None = None
    done_head: str | None = None
    done_command: str | None = None
    done_fix: str | None = None
    marks: list[Mark] = field(default_factory=list)

    @property
    def next_attempt(self) -> int:
        return (self.attempt.number if self.attempt else 0) + 1

    def to_dict(self) -> dict[str, Any]:
        attempt = self.attempt
        return {"issue": self.issue, "state": self.state, "reason": self.reason or None,
                "attempt": attempt.number if attempt else None, "owner": attempt.owner if attempt else None,
                "run": attempt.run if attempt else None,
                "ended": (attempt.end.fields.get("result") if attempt and attempt.end else None),
                "recorded": self.recorded, "not_before": _iso(self.not_before) if self.not_before else None}  # fmt: skip


def _valid(mark: Mark, issue: int) -> bool:
    if "_malformed" in mark.fields or mark.fields.get("issue") != str(issue):
        return False
    if mark.action == "wait":
        return bool(re.fullmatch(r"[1-9][0-9]*", mark.fields.get("attempt", "")))
    if not re.fullmatch(r"[1-9][0-9]*", mark.fields.get("attempt", "")) or not mark.fields.get("run"):
        return False
    if mark.action == "claim":
        return bool(mark.fields.get("owner")) and mark.fields.get("command") in COMMANDS
    if mark.action == "end":
        return mark.fields.get("result") in ("done", "wait")
    return True


def _end_counts(end: Mark, attempt: Attempt, config: Config) -> bool:
    """Whether ``end`` ends ``attempt``; ``attempt.progress`` holds only the records older than ``end``.

    The owner's own ``finish`` always counts. An interruption (``by=`` the recovering run) counts only when, by
    GitHub's own timestamps, the attempt's newest earlier claim, progress or renewal record is older than the lease: an owner
    record that landed between the recovering run's read and its write renews the lease and voids the interruption.
    """

    if not end.get("by"):
        return True
    ended = _time(end.created_at)
    times = [_time(m.created_at) for m in attempt.records()]
    if ended is None or any(t is None for t in times):
        return False  # a lease that cannot be read is never expired
    return ended - max(t for t in times if t) > config.lease


def derive_state(issue: int, marks: list[Mark], *, now: datetime, config: Config) -> IssueState:
    """The pure claim rule over the own markers of ``issue``."""

    if any(not _valid(m, issue) for m in marks):
        return IssueState(issue, "conflict", reason="malformed-marker", marks=marks)
    winners: dict[int, Attempt] = {}
    for mark in marks:
        if mark.action == "claim":
            number = int(mark.fields["attempt"])
            winners.setdefault(number, Attempt(number, mark))
    for mark in marks:
        if mark.action not in ("progress", "renew", "end"):
            continue
        attempt = winners.get(int(mark.fields["attempt"]))
        if attempt is None or mark.fields["run"] != attempt.run:
            # A run that does not own the attempt recorded work or an end: two writers, so fail closed.
            return IssueState(issue, "conflict", reason="foreign-run", marks=marks)
        if mark.action == "progress":
            attempt.progress.append(mark)
        elif mark.action == "renew":
            attempt.renewals.append(mark)
        elif attempt.end is None and _end_counts(mark, attempt, config):
            attempt.end = mark

    recorded: dict[str, str] | None = None
    for attempt in sorted(winners.values(), key=lambda a: a.number):
        source = attempt.progress[-1] if attempt.progress else attempt.claim
        if source.get("branch") or (recorded is None and source.get("pr")):
            recorded = {k: source.fields.get(k, "-") for k in ("pr", "branch", "head")}
    if not winners:
        return IssueState(issue, "free", recorded=recorded, marks=marks)
    current = winners[max(winners)]
    state = IssueState(issue, "free", attempt=current, recorded=recorded, marks=marks)
    if current.end is not None:
        if current.end.fields["result"] == "done":
            state.done_head = current.end.get("head")
            state.done_command = current.claim.fields.get("command")
            state.done_fix = current.claim.get("fix")
        else:
            until = _time(current.end.get("until") or "")
            ended = _time(current.end.created_at)
            state.not_before = until or ((ended + config.retry) if ended else None)
            if state.not_before is None:
                state.state, state.reason = "held", "unreadable-time"
        return state
    times = [_time(m.created_at) for m in current.records()]
    if any(t is None for t in times):
        state.state, state.reason = "held", "unreadable-time"  # a lease that cannot be read is never expired
    elif now - max(t for t in times if t) > config.lease:
        state.state, state.reason = "expired", "lease-expired"
    else:
        state.state, state.reason = "held", "claimed"
    return state


# ── reads ─────────────────────────────────────────────────────────────────────


def _prio(labels: tuple[str, ...]) -> int:
    ranks = [int(m.group(1)) for m in (_PRIO.match(label) for label in labels) if m]
    return min(ranks) if ranks else 1_000_000


def read_queue(gateway: Gateway, refine_due: RefineDue | None = None) -> list[dict[str, Any]]:
    """The eligible ``executor:codex`` items, in claim order; raises ``GatewayError``."""

    return _queue(gateway.list_board_items().cards, refine_due() if refine_due else None)


def _codex_issues(cards: list[Any]) -> list[int]:
    """Every board issue that carries ``executor:codex``, whatever its status or state: where a run's claims can be."""

    return sorted({card.number for card in cards if card.number is not None and CODEX_EXECUTOR_LABEL in card.labels})


def _queue(cards: list[Any], refine_due: list[int] | None = None) -> list[dict[str, Any]]:
    """Implementation items by board status, and ``refine`` items for the stories ``refine_due`` names (#535)."""

    queue = []
    due = set(refine_due or [])
    for card in cards:
        executors = [label for label in card.labels if label.startswith("executor:")]
        if card.number is None or card.state != "open" or executors != [CODEX_EXECUTOR_LABEL]:
            continue
        if not any(label in TYPE_LABELS for label in card.labels):
            continue
        if card.number in due:  # a due refinement comes before implementation, whatever the board status shows
            queue.append({"issue": card.number, "status": card.status, "command": "refine",
                          "rank": (0.5, _prio(card.labels), card.number)})  # fmt: skip
            continue
        if card.status not in COMMAND_BY_STATUS:
            continue
        queue.append({"issue": card.number, "status": card.status, "command": COMMAND_BY_STATUS[card.status],
                      "rank": (STATUS_ORDER.index(card.status), _prio(card.labels), card.number)})  # fmt: skip
    return sorted(queue, key=lambda item: item["rank"])


def _own_marks(gateway: Gateway, issue: int, login: str) -> list[Mark]:
    return parse_marks([c for c in gateway.list_comments(issue) if _author(c) == login])


class Waiting(Exception):
    """The item cannot be resumed safely; ``str(exc)`` is the reason word."""


@dataclass(frozen=True)
class Resume:
    pr: int | None = None
    branch: str | None = None
    head: str | None = None

    def fields(self) -> list[tuple[str, str]]:
        return [("pr", str(self.pr) if self.pr else "-"), ("branch", self.branch or "-"), ("head", self.head or "-")]

    def to_dict(self) -> dict[str, Any] | None:
        if not (self.pr or self.branch):
            return None
        return {"pr": self.pr, "branch": self.branch, "head": self.head}


def resolve_resume(gateway: Gateway, issue: int, recorded: dict[str, str] | None) -> Resume:
    """The PR, branch and current remote head to continue from; raises ``Waiting`` or ``GatewayError``."""

    prefix = branch_prefix(issue)
    recorded = {k: v for k, v in (recorded or {}).items() if v and v != "-"}
    branches = gateway.matching_branches(prefix)
    branch = recorded.get("branch")
    if branch:
        if branch not in branches:
            raise Waiting("branch-missing")
    elif len(branches) > 1:
        raise Waiting("ambiguous-branch")
    elif branches:
        branch = next(iter(branches))
    open_prs = gateway.list_open_prs()
    closing = re.compile(_CLOSES.format(n=issue), re.IGNORECASE)
    if any(not str((p.get("head") or {}).get("ref") or "").startswith(prefix) and closing.search(str(p.get("body") or ""))
           for p in open_prs):  # fmt: skip
        raise Waiting("foreign-pr")
    if not branch:
        if recorded.get("pr"):
            raise Waiting("branch-missing")
        return Resume()
    head = branches[branch]
    old = recorded.get("head")
    if old and recorded.get("branch") == branch and old.lower() != head.lower():
        try:
            status = gateway.compare_commits(old, head)
        except GatewayError as exc:
            if exc.not_found:
                raise Waiting("head-diverged") from None
            raise
        if status not in ("ahead", "identical"):  # identical: the same commit spelled another way
            raise Waiting("head-diverged")
    pr: int | None = None
    if recorded.get("pr"):
        try:
            data = gateway.pull_request(int(recorded["pr"]))
        except GatewayError as exc:
            if exc.not_found:
                raise Waiting("pr-missing") from None
            raise
        if data.get("state") != "open":
            raise Waiting("pr-closed")
        if (data.get("head") or {}).get("ref") != branch:
            raise Waiting("pr-branch-mismatch")
        pr = int(recorded["pr"])
    else:
        matches = [p for p in open_prs if (p.get("head") or {}).get("ref") == branch]
        if len(matches) > 1:
            raise Waiting("ambiguous-pr")
        if matches:
            pr = int(matches[0]["number"])
    return Resume(pr, branch, head)


# ── fix rounds (#536) ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FixRound:
    round: int
    maximum: int
    pr: int
    reviewed_head: str

    def to_dict(self) -> dict[str, Any]:
        return {"round": self.round, "max": self.maximum, "pr": self.pr, "reviewed_head": self.reviewed_head}


def blocking_review(review: Mapping[str, Any], po_login: str | None) -> bool:
    """A formal review with blockers from a trusted reviewer other than Codex (Codex Cloud findings never count)."""

    user = review.get("user") or {}
    login = str(user.get("login") or "")
    if not login or login == CODEX_BOT_LOGIN:
        return False
    if not ((po_login and login == po_login) or str(review.get("author_association") or "") in TRUSTED_ASSOCIATIONS):
        return False
    if review.get("state") == "CHANGES_REQUESTED":
        return True
    body = str(review.get("body") or "")
    found = _SUMMARY_BLOCKERS.search(body)
    return bool(_SUMMARY_COMMAND.search(body) and _SUMMARY_NEXT.search(body) and found
                and found.group(1).lower() not in ("", "none"))  # fmt: skip


def blocking_heads(gateway: Gateway, pr: int, po_login: str | None) -> list[str]:
    """The reviewed heads of ``pr`` with a blocking review, oldest first, each once; raises ``GatewayError``."""

    heads: list[str] = []
    for review in gateway.pr_reviews(pr):
        head = str(review.get("commit_id") or "").lower()
        if head and head not in heads and blocking_review(review, po_login):
            heads.append(head)
    return heads


def fix_round_for(gateway: Gateway, pr: int | None, head: str | None, config: Config,
                  settings: Settings) -> FixRound | None:  # fmt: skip
    """The fix round on ``head`` of ``pr``, or ``None`` when no blocking review names that head; raises ``GatewayError``."""

    if not (pr and head):
        return None
    heads = blocking_heads(gateway, pr, settings.po_login)
    if head.lower() not in heads:
        return None
    return FixRound(heads.index(head.lower()) + 1, config.fix_round_max, pr, head.lower())


def fix_round_blocker(issue: int, pr: int, head: str, maximum: int) -> str:
    """The ``Type: resource`` Blocked Declaration in the format of ``docs/PROJECT-STATUS.md``."""

    return ("## Blocked Declaration\n"
            "Type: resource\n"
            "Declared by: codex-worker\n"
            f"Blocks: #{issue}\n"
            f"Unblocked when: the findings on PR #{pr} are fixed by hand, or CODEX_FIX_ROUND_MAX is raised and the "
            "PO posts /unblock\n"
            "Owner: PO (@LauraMardones)\n"
            f"State of in-progress work: PR #{pr} has had {maximum} automatic fix rounds and the Claude review still "
            f"has blockers on head {head[:7]}; branch and commits intact; the item is not moved to Claude")  # fmt: skip


# ── refine queue entry (#535) ─────────────────────────────────────────────────


def work_in_flight(gateway: Gateway, facts: Mapping[str, Any], env: Mapping[str, str]) -> list[int]:
    """The open stories, tasks and bugs with work in flight, as ``scripts/lib/in-flight.sh`` finds them.

    A story is in flight when an open PR closes it (draft or not), or, without such a PR, when it has an open
    dependency, external, conflict or resource blocker that no later PO ``/unblock`` lifts. Open PRs that cannot be
    read raise ``GatewayError``; comments that cannot be read add no blocker for that story, as on the Actions path.
    """

    from .facts import STORY_TYPES, _Facts
    from .status import other_blocker_open

    open_prs = gateway.list_open_prs()
    trust = _Facts(gateway, env)
    flight: list[int] = []
    for entry in facts.get("issues") or []:
        number = entry.get("number")
        if not isinstance(number, int) or entry.get("type") not in STORY_TYPES:
            continue
        closing = re.compile(_CLOSES.format(n=number), re.IGNORECASE)
        if any(closing.search(str(p.get("body") or "")) for p in open_prs):
            flight.append(number)
            continue
        try:
            comments = gateway.list_comments(number)
        except GatewayError as exc:
            log(f"[CODEX-WORKER] Warning: could not read the comments of #{number}; its blockers are not checked: {exc}")
            continue
        if other_blocker_open(trust, comments, po_unblock=True):
            flight.append(number)
    return sorted(flight)


def scheduler_refine_due(gateway: Gateway, repo: str, env: Mapping[str, str]) -> RefineDue:
    """The ``refine`` queue source: the scheduler's story refinement rules, for ``executor:codex`` stories only.

    It reads the facts and the scheduler limits like the Actions scheduler. Stories on the board with status
    ``In implementation`` count for the WIP limit; every other story with work in flight (``work_in_flight``) is
    passed as ``in_flight``, as ``scripts/dispatcher-invoke.sh::ask_scheduler`` does, so a stale record of a story in
    review is refined whatever the ready buffer holds. Limits that cannot be used queue no refinement (fail closed);
    the implementation queue is not affected.
    """

    from .facts import read_facts
    from .scheduler import InvalidLimits, codex_refine_due, load_limits

    def due() -> list[int]:
        try:
            limits = load_limits(env)
        except InvalidLimits as exc:
            log(f"[CODEX-WORKER] no refine entries: scheduler limits: {exc}")
            return []
        read = read_facts(gateway, repo, env=env)
        if read.result != "read":
            raise GatewayError("read refinement facts", read.error or "unreadable")
        facts = read.to_dict()
        implementing = [c.number for c in gateway.list_board_items().cards
                        if c.number is not None and c.state == "open" and c.status == "In implementation"]  # fmt: skip
        flight = [n for n in work_in_flight(gateway, facts, env) if n not in implementing]
        return codex_refine_due(facts, in_implementation=implementing, in_flight=flight, limits=limits)

    return due


# ── eligibility hook (#524) ───────────────────────────────────────────────────


def command_gate(command: str | None, *, timeout: float = ELIGIBILITY_TIMEOUT) -> Eligibility:
    """The eligibility hook: ``<command> --repo <r> --issue <n> --command <c>``.

    Exit 0 is eligible; exit 1 is not (an optional JSON ``{"reason": "<word>"}`` on stdout names why);
    any other exit, a timeout or a command that cannot start is ``eligibility-unreadable``.
    No command configured is ``eligibility-unconfigured``. Every answer but exit 0 starts nothing.
    """

    def gate(repo: str, issue: int, cmd: str) -> tuple[bool, str]:
        if not (command or "").strip():
            return False, "eligibility-unconfigured"
        try:
            argv = shlex.split(command or "")
            proc = subprocess.run([*argv, "--repo", repo, "--issue", str(issue), "--command", cmd],
                                  capture_output=True, encoding="utf-8", errors="replace", timeout=timeout,
                                  check=False)  # fmt: skip
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return False, "eligibility-unreadable"
        if proc.returncode == 0:
            return True, "eligible"
        if proc.returncode != 1:
            return False, "eligibility-unreadable"
        try:
            answer = json.loads((proc.stdout or "").strip().splitlines()[0]) or {}
            reason, detail = str(answer.get("reason") or ""), str(answer.get("detail") or "")
        except (ValueError, IndexError, AttributeError):
            reason, detail = "", ""
        refusal = Reason(reason if _WORD.match(reason) else "ineligible")
        # One plain line: the text lands in an issue comment next to the marker.
        refusal.detail = " ".join(re.sub(r"[<>`]", "", detail).split())[:300]
        return False, refusal

    return gate


# ── operations ────────────────────────────────────────────────────────────────


def _api_key_names(env: Mapping[str, str]) -> list[str]:
    return [name for name in API_KEY_NAMES if (env.get(name) or "").strip()]


def invocation(repo: str, issue: int, attempt: int, owner: str, run: str, command: str, resume: Resume,
               fix: FixRound | None = None) -> dict[str, Any]:  # fmt: skip
    """The #523 contract: what the session runs, and the two calls that record its progress and its end.

    ``fix_round`` is set for a fix round (#536): the session follows ``implement.md``'s fix-round rules with the
    review's findings on ``resume``'s PR and branch, and opens no second PR or branch.
    """

    base = f"scripts/codex-worker.sh {{op}} --repo {repo} --issue {issue} --attempt {attempt} --run {run}"
    return {
        "fix_round": fix.to_dict() if fix else None,
        "repo": repo, "issue": issue, "attempt": attempt, "owner": owner, "run": run, "command": command,
        "command_file": COMMAND_FILES[command], "branch_prefix": branch_prefix(issue), "resume": resume.to_dict(),
        "progress": base.format(op="progress") + " --branch <branch> --head <full-sha> [--pr <n>]",
        "finish": base.format(op="finish") + " --result done --head <full-sha> | --result wait --reason <word> [--until <iso>]",
    }  # fmt: skip


def _wait(gateway: Gateway, issue: int, attempt: int, reason: str, settings: Settings, env: Mapping[str, str],
          dry_run: bool, *, claimed: bool = False) -> str:  # fmt: skip
    """Post the item's waiting evidence once per attempt and reason; returns Block 1's result word."""

    where = f"to start its claimed attempt {attempt}" if claimed else f"before attempt {attempt}"
    detail = getattr(reason, "detail", "")
    text = (f"Codex worker: #{issue} waits {where} ({reason}{': ' + detail if detail else ''}). "
            "A later scheduled run retries it; the item is not moved to Claude.")  # fmt: skip
    outcome = write_marker(gateway, issue, "codex-wait", [("issue", str(issue)), ("attempt", str(attempt)),
                                                          ("reason", reason)],
                           body=text, once=True, dry_run=dry_run, settings=settings, env=env)  # fmt: skip
    if outcome.result not in ("written", "already", "dry-run"):
        log(f"[CODEX WORKER] #{issue}: waiting evidence ({reason}) not written: {outcome.error}")
    return outcome.result


def _end_fields(issue: int, attempt: int, run: str, result: str, reason: str, head: str | None,
                until: str | None, by: str | None = None) -> list[tuple[str, str]]:  # fmt: skip
    fields = [("issue", str(issue)), ("attempt", str(attempt)), ("run", run), ("result", result), ("reason", reason),
              ("head", head or "-")]  # fmt: skip
    if until:
        fields.append(("until", until))
    if by:
        fields.append(("by", by))
    return fields


def claim(
    gateway: Gateway, repo: str, *, owner: str, run: str | None = None, eligibility: Eligibility,
    now: Callable[[], datetime] | None = None, config: Config | None = None, settings: Settings | None = None,
    env: Mapping[str, str] | None = None, dry_run: bool = False, refine_due: RefineDue | None = None,
) -> WorkerResult:
    """Claim at most one queued item and return its invocation (exit 0), or start nothing."""

    env = os.environ if env is None else env
    config = config or Config()
    settings = settings or Settings.from_env(env)
    clock = now or (lambda: datetime.now(timezone.utc))
    named = bool(run)  # only a named run can be a replay: a fresh name has no claims anywhere
    run = run or uuid.uuid4().hex[:12]
    base: dict[str, Any] = {"owner": owner, "run": run}

    keys = _api_key_names(env)
    if keys:
        # A metered key could bill the session (ADR-012); the subscription path never needs one.
        return WorkerResult("claim", "refused", error=f"check environment: {', '.join(keys)} is set; unset it",
                            fields={**base, "reason": "api-key-set"})  # fmt: skip
    try:
        login = resolve_login(gateway, "dispatcher", env)
        cards = gateway.list_board_items().cards
        queue = _queue(cards, refine_due() if refine_due else None)
        # A named run also reads every other executor:codex issue: its claimed item may have left the queue (for
        # example to In review, or closed), and a replay must find that claim rather than claim a second item.
        numbers = _codex_issues(cards) if named else [item["issue"] for item in queue]
        numbers = sorted(set(numbers) | {item["issue"] for item in queue})
        states = {n: derive_state(n, _own_marks(gateway, n, login), now=clock(), config=config) for n in numbers}
    except (GatewayError, Unreadable) as exc:
        return WorkerResult("claim", "unreadable", error=str(exc), fields=base)

    # A replayed run continues the attempt it already claimed, and claims nothing new. Its newest claim on any
    # issue names the item (comment ids grow across the repository).
    queued = {item["issue"]: item for item in queue}
    own = [(m.order, n) for n, st in states.items() for m in st.marks
           if m.action == "claim" and m.fields.get("run") == run]  # fmt: skip
    candidates = queue
    replaying = False
    if own:
        issue_no = max(own)[1]
        st = states[issue_no]
        mine = [m for m in st.marks if m.action == "claim" and m.fields.get("run") == run]
        if st.state == "conflict":
            return WorkerResult("claim", "lost", fields={**base, "issue": issue_no, "reason": st.reason})
        number = int(mine[-1].fields["attempt"])
        fields = {**base, "issue": issue_no, "attempt": number}
        current = st.attempt
        item = queued.get(issue_no)
        if current is None or current.number != number or current.run != run:
            return WorkerResult("claim", "lost", fields={**fields, "reason": "another-run-owns-the-attempt"})
        if current.end is not None:
            return WorkerResult("claim", "ended", fields={**fields, "reason": current.end.get("reason")})
        if item is None:
            # The item left the queue while the attempt is open: nothing starts, and no other item is claimed.
            return WorkerResult("claim", "waiting", fields={**fields, "skipped": [{"issue": issue_no,
                                                                                  "reason": "not-queued"}]})  # fmt: skip
        if st.state == "expired":
            # The lease ran out, so a new run may already treat the attempt as interrupted: the replay takes the
            # claim rule's recovery path for this item only and starts only on a newly won attempt.
            candidates, replaying = [item], True
        else:
            # A replay starts the session too, so it re-reads the remote resume facts and asks the hook again, as
            # a new claim does; a refusal keeps the claim.
            try:
                resume = resolve_resume(gateway, issue_no, st.recorded)
                # A replayed fix round keeps the round its claim named, even after its session pushed a new head.
                fix = fix_round_for(gateway, resume.pr, current.claim.get("fix"), config, settings)
                eligible, reason = True, ""
            except Waiting as exc:
                eligible, reason = False, str(exc)
            except GatewayError as exc:
                return WorkerResult("claim", "unreadable", error=str(exc), fields=fields)
            if eligible:
                eligible, reason = eligibility(repo, issue_no, current.command)
            if not eligible:
                skipped = [{"issue": issue_no, "reason": reason,
                            "evidence": _wait(gateway, issue_no, number, reason, settings, env, dry_run,
                                              claimed=True)}]  # fmt: skip
                return WorkerResult("claim", "waiting", fields={**fields, "skipped": skipped})
            # Time passes during the reads and the hook, so the lease read at the start no longer decides. The replay
            # renews the lease on GitHub and starts only when the read-back shows it still owns the open attempt:
            # GitHub's order decides against any interruption, as it does for a claim (no local clock in the answer).
            call = invocation(repo, issue_no, number, owner, run, current.command, resume, fix)
            renewed = write_marker(
                gateway, issue_no, "codex-renew", [("issue", str(issue_no)), ("attempt", str(number)), ("run", run)],
                body=f"Codex worker: run `{run}` resumes its claimed attempt {number} on #{issue_no}.",
                dry_run=dry_run, settings=settings, env=env,
            )  # fmt: skip
            if renewed.result == "dry-run":
                return WorkerResult("claim", "dry-run", fields={**fields, "invocation": call})
            if renewed.result != "written":
                return WorkerResult("claim", "failed", error=f"renew claim: {renewed.error}", fields=fields)
            try:
                after = derive_state(issue_no, _own_marks(gateway, issue_no, login), now=clock(), config=config)
            except GatewayError as exc:
                return WorkerResult("claim", "unreadable", error=f"read back renewal: {exc}", fields=fields)
            held = after.attempt
            if after.state == "conflict" or held is None or held.number != number or held.run != run:
                return WorkerResult("claim", "lost", fields={**fields, "reason": after.reason or
                                                             "another-run-owns-the-attempt"})  # fmt: skip
            if held.end is not None:
                # An interruption or an end landed before the renewal: the attempt is over and nothing starts.
                return WorkerResult("claim", "lost", fields={**fields, "reason": held.end.get("reason") or "ended"})
            return WorkerResult("claim", "continued", fields={**fields, "invocation": call})

    skipped: list[dict[str, Any]] = []
    for item in candidates:
        issue, st = item["issue"], states[item["issue"]]
        entry: dict[str, Any] | None = None
        recovered = False
        # The rules run on what GitHub shows. After this run records an interruption they run once more on the
        # read-back, because any other write may have landed between the first read and the interruption: the owner's
        # progress (the interruption does not count), its own end (its until or its handed-off head applies), or
        # another run's recovery (that run's attempt holds). Only this run's own counted interruption skips the wait.
        for _pass in range(2):
            if st.state in ("held", "conflict"):
                entry = {"issue": issue, "reason": st.reason or st.state}
                if st.state == "conflict":
                    entry["evidence"] = _wait(gateway, issue, st.next_attempt, "conflicting-claim", settings, env,
                                              dry_run)  # fmt: skip
                break
            if st.state == "expired":
                assert st.attempt is not None
                if recovered:
                    entry = {"issue": issue, "reason": "claimed"}
                    break
                recovered = True
                number_k = st.attempt.number
                # Not ``once``: an interruption that an owner record voided must not block a later, valid one.
                ended = write_marker(
                    gateway, issue, "codex-end",
                    _end_fields(issue, number_k, st.attempt.run, "wait", "interrupted", None, None, by=run),
                    body=(f"Codex worker: attempt {number_k} on #{issue} (run `{st.attempt.run}`) recorded no "
                          f"progress for {int(config.lease.total_seconds() // 60)} minutes; run `{run}` treats it as "
                          "interrupted."),
                    dry_run=dry_run, settings=settings, env=env,
                )  # fmt: skip
                if ended.result not in ("written", "dry-run"):
                    return WorkerResult("claim", "failed", error=f"record interruption: {ended.error}",
                                        fields={**base, "issue": issue, "skipped": skipped})  # fmt: skip
                if ended.result == "dry-run":
                    break
                try:
                    after = derive_state(issue, _own_marks(gateway, issue, login), now=clock(), config=config)
                except GatewayError as exc:
                    return WorkerResult("claim", "unreadable", error=f"read back interruption: {exc}",
                                        fields={**base, "issue": issue, "skipped": skipped})  # fmt: skip
                current = after.attempt
                same = after.state != "conflict" and current is not None and current.number == number_k
                if same and current.end is None:
                    entry = {"issue": issue, "reason": "lease-held"}
                    break
                st = after
                if same and current.end.get("by") == run and current.end.get("reason") == "interrupted":
                    break  # this run's own interruption counted: continue at once, with no retry wait
                continue
            if st.not_before is not None and clock() < st.not_before:
                entry = {"issue": issue, "reason": "retry-later", "not_before": _iso(st.not_before)}
            break
        if entry is not None:
            skipped.append(entry)
            continue
        try:
            resume = resolve_resume(gateway, issue, st.recorded)
        except Waiting as exc:
            skipped.append({"issue": issue, "reason": str(exc),
                            "evidence": _wait(gateway, issue, st.next_attempt, str(exc), settings, env, dry_run)})
            continue
        except GatewayError as exc:
            return WorkerResult("claim", "unreadable", error=str(exc), fields={**base, "skipped": skipped})
        # A blocking review on the current head of the PR makes the implementation a fix round on it (#536).
        fix: FixRound | None = None
        if item["command"] == "implement":
            try:
                fix = fix_round_for(gateway, resume.pr, resume.head, config, settings)
            except GatewayError as exc:
                return WorkerResult("claim", "unreadable", error=str(exc), fields={**base, "skipped": skipped})
        # Only an implementation handoff suppresses: a finished refinement neither blocks the first implementation
        # nor a later re-refinement, because its branch head does not move when the spec changes. A blocking review
        # of the handed-off head lifts it once: a fix round that ended on the head it was to fix is not run again.
        handed_off = (st.done_head and st.done_command == "implement" == item["command"] and resume.head
                      and resume.head.lower() == st.done_head.lower())  # fmt: skip
        if handed_off and (fix is None or (st.done_fix or "").lower() == fix.reviewed_head):
            skipped.append({"issue": issue, "reason": "handed-off-on-head" if fix is None else "fix-round-tried-on-head"})
            continue
        if fix is not None and fix.round > fix.maximum:
            # Above the bound nothing starts and the item waits for the PO; it is not moved to Claude.
            assert resume.pr is not None
            posted = write_marker(gateway, issue, "fix-round-max", [("pr", str(resume.pr))],
                                  body=fix_round_blocker(issue, resume.pr, fix.reviewed_head, fix.maximum),
                                  once=True, dry_run=dry_run, settings=settings, env=env)  # fmt: skip
            if posted.result not in ("written", "already", "dry-run"):
                log(f"[CODEX WORKER] #{issue}: fix-round blocker not written: {posted.error}")
            skipped.append({"issue": issue, "reason": "fix-round-max", "round": fix.round, "max": fix.maximum,
                            "evidence": posted.result})  # fmt: skip
            continue

        eligible, reason = eligibility(repo, issue, item["command"])
        if not eligible:
            # The reserve is shared, so no other item may start either.
            skipped.append({"issue": issue, "reason": reason,
                            "evidence": _wait(gateway, issue, st.next_attempt, reason, settings, env, dry_run)})
            return WorkerResult("claim", "waiting", fields={**base, "skipped": skipped})

        number = st.next_attempt
        fields = [("issue", str(issue)), ("attempt", str(number)), ("owner", owner), ("run", run),
                  ("command", item["command"]), *resume.fields()]  # fmt: skip
        text = f"Codex worker `{owner}` claims #{issue} for `/{item['command']}`, attempt {number} (run `{run}`)."
        if resume.to_dict():
            text += f" It resumes branch `{resume.branch}` at `{resume.head}`" + (f" on PR #{resume.pr}." if resume.pr else ".")
        if fix is not None:
            fields.append(("fix", fix.reviewed_head))
            text += f" Fix round {fix.round} of {fix.maximum} for the blocking review of `{fix.reviewed_head[:7]}`."
        call = invocation(repo, issue, number, owner, run, item["command"], resume, fix)
        written = write_marker(gateway, issue, "codex-claim", fields, body=text, dry_run=dry_run, settings=settings,
                               env=env)  # fmt: skip
        if written.result == "dry-run":
            return WorkerResult("claim", "dry-run", fields={**base, "issue": issue, "attempt": number,
                                                            "invocation": call, "skipped": skipped})  # fmt: skip
        if written.result != "written":
            return WorkerResult("claim", "failed", error=f"write claim: {written.error}",
                                fields={**base, "issue": issue, "attempt": number, "skipped": skipped})  # fmt: skip
        # The claim is posted; only the read-back decides who owns the attempt.
        try:
            after = derive_state(issue, _own_marks(gateway, issue, login), now=clock(), config=config)
        except GatewayError as exc:
            return WorkerResult("claim", "unreadable", error=f"read back claim: {exc}",
                                fields={**base, "issue": issue, "attempt": number, "skipped": skipped})  # fmt: skip
        fields_out = {**base, "issue": issue, "attempt": number, "skipped": skipped}
        # The winner decides, not the lease: a local clock ahead of GitHub's must not lose a won claim.
        won = after.attempt is not None and after.attempt.number == number and after.attempt.run == run
        if after.state in ("held", "expired") and won and after.attempt.end is None:
            return WorkerResult("claim", "claimed", fields={**fields_out, "invocation": call})
        return WorkerResult("claim", "lost", fields={**fields_out, "reason": after.reason or "another-run-owns-the-attempt"})

    if replaying and skipped and skipped[-1]["reason"] == "claimed":
        # Another run recovered the replay's expired attempt first and holds the next one.
        return WorkerResult("claim", "lost", fields={**base, "issue": skipped[-1]["issue"],
                                                     "reason": "another-run-owns-the-attempt", "skipped": skipped})
    return WorkerResult("claim", "waiting" if skipped else "idle", fields={**base, "skipped": skipped})


def _owned(gateway: Gateway, issue: int, attempt: int, run: str, env: Mapping[str, str], now: datetime,
           config: Config) -> tuple[IssueState, bool]:  # fmt: skip
    login = resolve_login(gateway, "dispatcher", env)
    st = derive_state(issue, _own_marks(gateway, issue, login), now=now, config=config)
    current = st.attempt
    owns = st.state != "conflict" and current is not None and current.number == attempt and current.run == run
    return st, owns


def _still_owned(gateway: Gateway, op: str, issue: int, attempt: int, run: str, env: Mapping[str, str], now: datetime,
                 config: Config, base: dict[str, Any], *, end_marker: str | None = None) -> WorkerResult | None:  # fmt: skip
    """Re-read after a written record: ``None`` while the run still owns the attempt, else the result to report.

    A run that recovered the attempt between this call's read and its write wins when its interruption landed first
    (the claim rule decides by comment order), so the session must stop rather than trust its own write.
    """

    try:
        st, owns = _owned(gateway, issue, attempt, run, env, now, config)
    except (GatewayError, Unreadable) as exc:
        return WorkerResult(op, "unreadable", error=f"read back: {exc}", fields=base)
    end = st.attempt.end if st.attempt else None
    held = owns and (end is None if end_marker is None else end is not None and end_marker in end.body)
    if held:
        return None
    return WorkerResult(op, "not-owner", error="check owner: the attempt ended or moved to another run before this "
                        "record landed; stop the session", fields=base)  # fmt: skip


def record_progress(
    gateway: Gateway, repo: str, issue: int, attempt: int, run: str, *, branch: str, head: str, pr: int | None = None,
    now: Callable[[], datetime] | None = None, config: Config | None = None, settings: Settings | None = None,
    env: Mapping[str, str] | None = None, dry_run: bool = False, refine_due: RefineDue | None = None,
) -> WorkerResult:
    """Record the owning session's pushed branch and head; a replay of the same record writes nothing."""

    env = os.environ if env is None else env
    clock = now or (lambda: datetime.now(timezone.utc))
    config = config or Config()
    base: dict[str, Any] = {"issue": issue, "attempt": attempt, "run": run}
    try:
        st, owns = _owned(gateway, issue, attempt, run, env, clock(), config)
    except (GatewayError, Unreadable) as exc:
        return WorkerResult("progress", "unreadable", error=str(exc), fields=base)
    if not owns or (st.attempt and st.attempt.end is not None):
        return WorkerResult("progress", "not-owner", error="check owner: this run does not hold an open claim on the attempt",
                            fields=base)  # fmt: skip
    claimed = st.attempt.claim if st.attempt else None
    if claimed is not None and claimed.get("fix") and (branch != claimed.get("branch") or str(pr or "") != claimed.get("pr")):
        # A fix round continues the claimed PR and branch; a second PR or branch is never recorded (#536).
        return WorkerResult("progress", "refused", error=f"check fix round: record branch {claimed.get('branch')} and "
                            f"PR #{claimed.get('pr')}, the ones the fix round claimed", fields={**base, "reason": "fix-round-target"})  # fmt: skip
    fields = [("issue", str(issue)), ("attempt", str(attempt)), ("run", run), ("pr", str(pr) if pr else "-"),
              ("branch", branch), ("head", head)]  # fmt: skip
    outcome = write_marker(gateway, issue, "codex-progress", fields,
                           body=f"Codex worker: attempt {attempt} on #{issue} pushed `{branch}` at `{head}`.",
                           once=True, dry_run=dry_run, settings=settings, env=env)  # fmt: skip
    if outcome.result in ("written", "already"):
        lost = _still_owned(gateway, "progress", issue, attempt, run, env, clock(), config, base)
        if lost:
            return lost
    return WorkerResult("progress", outcome.result, error=outcome.error, fields={**base, **outcome.fields})


def finish(
    gateway: Gateway, repo: str, issue: int, attempt: int, run: str, *, result: str, reason: str,
    head: str | None = None, until: str | None = None, now: Callable[[], datetime] | None = None,
    config: Config | None = None, settings: Settings | None = None, env: Mapping[str, str] | None = None,
    dry_run: bool = False,
) -> WorkerResult:
    """End the attempt: ``done`` after the hand-off, ``wait`` (for example ``usage-limit``) to retry later."""

    env = os.environ if env is None else env
    base: dict[str, Any] = {"issue": issue, "attempt": attempt, "run": run, "end": result}
    fields = _end_fields(issue, attempt, run, result, reason, head, until)
    marker = dispatcher_marker(marker_text("codex-end", fields))
    clock = now or (lambda: datetime.now(timezone.utc))
    config = config or Config()
    try:
        st, owns = _owned(gateway, issue, attempt, run, env, clock(), config)
    except (GatewayError, Unreadable) as exc:
        return WorkerResult("finish", "unreadable", error=str(exc), fields=base)
    if owns and st.attempt and st.attempt.end is not None:
        if marker in st.attempt.end.body:
            return WorkerResult("finish", "already", fields=base)
        owns = False
    if not owns:
        return WorkerResult("finish", "not-owner", error="check owner: this run does not hold an open claim on the attempt",
                            fields=base)  # fmt: skip
    text = (f"Codex worker: attempt {attempt} on #{issue} handed off at `{head}`." if result == "done" else
            f"Codex worker: attempt {attempt} on #{issue} stopped ({reason}); a later scheduled run resumes it. "
            "The item is not moved to Claude.")  # fmt: skip
    outcome = write_marker(gateway, issue, "codex-end", fields, body=text, once=True, dry_run=dry_run,
                           settings=settings, env=env)  # fmt: skip
    if outcome.result in ("written", "already"):
        # Only the end that counts hands the attempt off; one that landed after an interruption does not.
        lost = _still_owned(gateway, "finish", issue, attempt, run, env, clock(), config, base, end_marker=marker)
        if lost:
            return lost
    return WorkerResult("finish", outcome.result, error=outcome.error, fields={**base, **outcome.fields})


def status(
    gateway: Gateway, repo: str, *, now: Callable[[], datetime] | None = None, config: Config | None = None,
    env: Mapping[str, str] | None = None,
) -> WorkerResult:
    """Read-only: the queue and each item's claim state, from GitHub alone."""

    env = os.environ if env is None else env
    clock = now or (lambda: datetime.now(timezone.utc))
    try:
        login = resolve_login(gateway, "dispatcher", env)
        items = [{**{k: v for k, v in item.items() if k != "rank"},
                  **derive_state(item["issue"], _own_marks(gateway, item["issue"], login), now=clock(),
                                 config=config or Config()).to_dict()}
                 for item in read_queue(gateway)]  # fmt: skip
    except (GatewayError, Unreadable) as exc:
        return WorkerResult("status", "unreadable", error=str(exc), fields={"items": []})
    return WorkerResult("status", "read", fields={"items": items})
