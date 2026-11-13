"""Local Codex worker: ``hpw dispatcher codex-worker`` (issue #528, ADR-012 → Codex Plus amendment, ADR-014).

A scheduled task in the signed-in Codex app on the PO's computer runs
``scripts/codex-worker.sh claim``. The worker reads the ``executor:codex`` queue
from the board, claims one item with a marker comment (Block 2, written through
Block 1) and prints the invocation the task's session follows (#523). The
session records its pushed branch and head with ``progress`` and ends its
attempt with ``finish``. GitHub holds every fact; the worker keeps no local
state, so any later run on any computer reads the same answer.

Markers (own account only, ``<!-- hpw:dispatcher action=<action> ... -->``):

- ``codex-claim issue= attempt= owner= run= command= pr= branch= head=``: the claim; the claim with
  the lowest comment id of an attempt owns it.
- ``codex-progress issue= attempt= run= pr= branch= head=``: what the owning session pushed.
- ``codex-end issue= attempt= run= result=done|wait reason= head= until=``: the attempt is over.
- ``codex-wait issue= attempt= reason=``: the item waits before that attempt could be claimed.

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

from ..review_policy import CODEX_EXECUTOR_LABEL
from .gateway import Gateway, GatewayError
from .marker import Unreadable, dispatcher_marker, marker_text, resolve_login, write_marker
from .write import EXIT_NO, EXIT_OK, EXIT_UNREADABLE, Result, Settings, _author, log

BLOCK = "codex-worker"

# The #523 invocation boundary: the board status of a queued item names the HPW command its session runs.
COMMAND_BY_STATUS = {"In implementation": "implement", "Ready for implementation": "implement"}
STATUS_ORDER = ("In implementation", "Ready for implementation")
COMMANDS = ("refine", "implement")
COMMAND_FILES = {"refine": ".claude/commands/refine.md", "implement": ".claude/commands/implement.md"}
TYPE_LABELS = ("type:story", "type:task", "type:bug")
API_KEY_NAMES = ("OPENAI_API_KEY", "CODEX_API_KEY")

DEFAULT_LEASE_MINUTES = 240
DEFAULT_RETRY_MINUTES = 60
ELIGIBILITY_TIMEOUT = 60

_EXIT_BY_RESULT = {
    "claimed": EXIT_OK, "continued": EXIT_OK, "written": EXIT_OK, "already": EXIT_OK, "read": EXIT_OK,
    "dry-run": EXIT_OK, "unreadable": EXIT_UNREADABLE,
}  # fmt: skip
_MARK = re.compile(r"<!-- hpw:dispatcher action=codex-(claim|progress|end|wait) ([^<>]*?) -->")
_WORD = re.compile(r"^[A-Za-z0-9_.-]+$")
_PRIO = re.compile(r"^prio:P(\d+)$")
_CLOSES = r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#{n}\b"

Eligibility = Callable[[str, int, str], "tuple[bool, str]"]


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

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        env = os.environ if env is None else env
        values = {}
        for name, default in (("CODEX_WORKER_LEASE_MINUTES", DEFAULT_LEASE_MINUTES),
                              ("CODEX_WORKER_RETRY_MINUTES", DEFAULT_RETRY_MINUTES)):  # fmt: skip
            raw = (env.get(name) or "").strip()
            if raw and not re.fullmatch(r"[1-9][0-9]*", raw):
                raise InvalidConfig(f"{name} must be a whole number of minutes, 1 or more (value: {raw!r})")
            values[name] = int(raw) if raw else default
        return cls(lease=timedelta(minutes=values["CODEX_WORKER_LEASE_MINUTES"]),
                   retry=timedelta(minutes=values["CODEX_WORKER_RETRY_MINUTES"]))  # fmt: skip


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
    end: Mark | None = None

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
        if mark.action not in ("progress", "end"):
            continue
        attempt = winners.get(int(mark.fields["attempt"]))
        if attempt is None or mark.fields["run"] != attempt.run:
            # A run that does not own the attempt recorded work or an end: two writers, so fail closed.
            return IssueState(issue, "conflict", reason="foreign-run", marks=marks)
        if mark.action == "progress":
            attempt.progress.append(mark)
        elif attempt.end is None:
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
        else:
            until = _time(current.end.get("until") or "")
            ended = _time(current.end.created_at)
            state.not_before = until or ((ended + config.retry) if ended else None)
            if state.not_before is None:
                state.state, state.reason = "held", "unreadable-time"
        return state
    times = [_time(m.created_at) for m in (current.claim, *current.progress)]
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


def read_queue(gateway: Gateway) -> list[dict[str, Any]]:
    """The eligible ``executor:codex`` items, in claim order; raises ``GatewayError``."""

    queue = []
    for card in gateway.list_board_items().cards:
        executors = [label for label in card.labels if label.startswith("executor:")]
        if card.number is None or card.state != "open" or executors != [CODEX_EXECUTOR_LABEL]:
            continue
        if card.status not in COMMAND_BY_STATUS or not any(label in TYPE_LABELS for label in card.labels):
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
    if old and recorded.get("branch") == branch and old != head:
        try:
            status = gateway.compare_commits(old, head)
        except GatewayError as exc:
            if exc.not_found:
                raise Waiting("head-diverged") from None
            raise
        if status != "ahead":
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
            reason = str((json.loads((proc.stdout or "").strip().splitlines()[0]) or {}).get("reason") or "")
        except (ValueError, IndexError, AttributeError):
            reason = ""
        return False, reason if _WORD.match(reason) else "ineligible"

    return gate


# ── operations ────────────────────────────────────────────────────────────────


def _api_key_names(env: Mapping[str, str]) -> list[str]:
    return [name for name in API_KEY_NAMES if (env.get(name) or "").strip()]


def invocation(repo: str, issue: int, attempt: int, owner: str, run: str, command: str, resume: Resume) -> dict[str, Any]:
    """The #523 contract: what the session runs, and the two calls that record its progress and its end."""

    base = f"scripts/codex-worker.sh {{op}} --repo {repo} --issue {issue} --attempt {attempt} --run {run}"
    return {
        "repo": repo, "issue": issue, "attempt": attempt, "owner": owner, "run": run, "command": command,
        "command_file": COMMAND_FILES[command], "branch_prefix": branch_prefix(issue), "resume": resume.to_dict(),
        "progress": base.format(op="progress") + " --branch <branch> --head <sha> [--pr <n>]",
        "finish": base.format(op="finish") + " --result done --head <sha> | --result wait --reason <word> [--until <iso>]",
    }  # fmt: skip


def _wait(gateway: Gateway, issue: int, attempt: int, reason: str, settings: Settings, env: Mapping[str, str],
          dry_run: bool, *, claimed: bool = False) -> str:  # fmt: skip
    """Post the item's waiting evidence once per attempt and reason; returns Block 1's result word."""

    where = f"to start its claimed attempt {attempt}" if claimed else f"before attempt {attempt}"
    text = (f"Codex worker: #{issue} waits {where} ({reason}). A later scheduled run retries it; "
            "the item is not moved to Claude.")  # fmt: skip
    outcome = write_marker(gateway, issue, "codex-wait", [("issue", str(issue)), ("attempt", str(attempt)),
                                                          ("reason", reason)],
                           body=text, once=True, dry_run=dry_run, settings=settings, env=env)  # fmt: skip
    if outcome.result not in ("written", "already", "dry-run"):
        log(f"[CODEX WORKER] #{issue}: waiting evidence ({reason}) not written: {outcome.error}")
    return outcome.result


def _end_fields(issue: int, attempt: int, run: str, result: str, reason: str, head: str | None,
                until: str | None) -> list[tuple[str, str]]:  # fmt: skip
    fields = [("issue", str(issue)), ("attempt", str(attempt)), ("run", run), ("result", result), ("reason", reason),
              ("head", head or "-")]  # fmt: skip
    if until:
        fields.append(("until", until))
    return fields


def claim(
    gateway: Gateway, repo: str, *, owner: str, run: str | None = None, eligibility: Eligibility,
    now: Callable[[], datetime] | None = None, config: Config | None = None, settings: Settings | None = None,
    env: Mapping[str, str] | None = None, dry_run: bool = False,
) -> WorkerResult:
    """Claim at most one queued item and return its invocation (exit 0), or start nothing."""

    env = os.environ if env is None else env
    config = config or Config()
    settings = settings or Settings.from_env(env)
    clock = now or (lambda: datetime.now(timezone.utc))
    run = run or uuid.uuid4().hex[:12]
    base: dict[str, Any] = {"owner": owner, "run": run}

    keys = _api_key_names(env)
    if keys:
        # A metered key could bill the session (ADR-012); the subscription path never needs one.
        return WorkerResult("claim", "refused", error=f"check environment: {', '.join(keys)} is set; unset it",
                            fields={**base, "reason": "api-key-set"})  # fmt: skip
    try:
        login = resolve_login(gateway, "dispatcher", env)
        queue = read_queue(gateway)
        states = {item["issue"]: derive_state(item["issue"], _own_marks(gateway, item["issue"], login),
                                              now=clock(), config=config) for item in queue}  # fmt: skip
    except (GatewayError, Unreadable) as exc:
        return WorkerResult("claim", "unreadable", error=str(exc), fields=base)

    # A replayed run continues the attempt it already claimed, and claims nothing new.
    for item in queue:
        st = states[item["issue"]]
        mine = [m for m in st.marks if m.action == "claim" and m.fields.get("run") == run]
        if not mine:
            continue
        if st.state == "conflict":
            return WorkerResult("claim", "lost", fields={**base, "issue": item["issue"], "reason": st.reason})
        number = int(mine[0].fields["attempt"])
        fields = {**base, "issue": item["issue"], "attempt": number}
        current = st.attempt
        if current is not None and current.number == number and current.run == run:
            if current.end is not None:
                return WorkerResult("claim", "ended", fields={**fields, "reason": current.end.get("reason")})
            # A replay starts the session too, so it re-reads the remote resume facts and asks the hook again,
            # as a new claim does; a refusal keeps the claim.
            try:
                resume = resolve_resume(gateway, item["issue"], st.recorded)
                eligible, reason = True, ""
            except Waiting as exc:
                eligible, reason = False, str(exc)
            except GatewayError as exc:
                return WorkerResult("claim", "unreadable", error=str(exc), fields=fields)
            if eligible:
                eligible, reason = eligibility(repo, item["issue"], current.command)
            if not eligible:
                skipped = [{"issue": item["issue"], "reason": reason,
                            "evidence": _wait(gateway, item["issue"], number, reason, settings, env, dry_run,
                                              claimed=True)}]  # fmt: skip
                return WorkerResult("claim", "waiting", fields={**fields, "skipped": skipped})
            return WorkerResult("claim", "continued", fields={**fields, "invocation": invocation(
                repo, item["issue"], number, owner, run, current.command, resume)})  # fmt: skip
        return WorkerResult("claim", "lost", fields={**fields, "reason": "another-run-owns-the-attempt"})

    skipped: list[dict[str, Any]] = []
    for item in queue:
        issue, st = item["issue"], states[item["issue"]]
        if st.state in ("held", "conflict"):
            entry = {"issue": issue, "reason": st.reason or st.state}
            if st.state == "conflict":
                entry["evidence"] = _wait(gateway, issue, st.next_attempt, "conflicting-claim", settings, env, dry_run)
            skipped.append(entry)
            continue
        if st.state == "expired":
            assert st.attempt is not None
            ended = write_marker(
                gateway, issue, "codex-end",
                _end_fields(issue, st.attempt.number, st.attempt.run, "wait", "interrupted", None, None),
                body=(f"Codex worker: attempt {st.attempt.number} on #{issue} (run `{st.attempt.run}`) recorded no "
                      f"progress for {int(config.lease.total_seconds() // 60)} minutes; it is treated as interrupted."),
                once=True, dry_run=dry_run, settings=settings, env=env,
            )  # fmt: skip
            if ended.result not in ("written", "already", "dry-run"):
                return WorkerResult("claim", "failed", error=f"record interruption: {ended.error}",
                                    fields={**base, "issue": issue, "skipped": skipped})  # fmt: skip
        elif st.not_before is not None and clock() < st.not_before:
            skipped.append({"issue": issue, "reason": "retry-later", "not_before": _iso(st.not_before)})
            continue
        try:
            resume = resolve_resume(gateway, issue, st.recorded)
        except Waiting as exc:
            skipped.append({"issue": issue, "reason": str(exc),
                            "evidence": _wait(gateway, issue, st.next_attempt, str(exc), settings, env, dry_run)})
            continue
        except GatewayError as exc:
            return WorkerResult("claim", "unreadable", error=str(exc), fields={**base, "skipped": skipped})
        if st.done_head and resume.head == st.done_head:
            skipped.append({"issue": issue, "reason": "handed-off-on-head"})
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
        call = invocation(repo, issue, number, owner, run, item["command"], resume)
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

    return WorkerResult("claim", "waiting" if skipped else "idle", fields={**base, "skipped": skipped})


def _owned(gateway: Gateway, issue: int, attempt: int, run: str, env: Mapping[str, str], now: datetime,
           config: Config) -> tuple[IssueState, bool]:  # fmt: skip
    login = resolve_login(gateway, "dispatcher", env)
    st = derive_state(issue, _own_marks(gateway, issue, login), now=now, config=config)
    current = st.attempt
    owns = st.state != "conflict" and current is not None and current.number == attempt and current.run == run
    return st, owns


def record_progress(
    gateway: Gateway, repo: str, issue: int, attempt: int, run: str, *, branch: str, head: str, pr: int | None = None,
    now: Callable[[], datetime] | None = None, config: Config | None = None, settings: Settings | None = None,
    env: Mapping[str, str] | None = None, dry_run: bool = False,
) -> WorkerResult:
    """Record the owning session's pushed branch and head; a replay of the same record writes nothing."""

    env = os.environ if env is None else env
    base: dict[str, Any] = {"issue": issue, "attempt": attempt, "run": run}
    try:
        st, owns = _owned(gateway, issue, attempt, run, env, (now or (lambda: datetime.now(timezone.utc)))(),
                          config or Config())  # fmt: skip
    except (GatewayError, Unreadable) as exc:
        return WorkerResult("progress", "unreadable", error=str(exc), fields=base)
    if not owns or (st.attempt and st.attempt.end is not None):
        return WorkerResult("progress", "not-owner", error="check owner: this run does not hold an open claim on the attempt",
                            fields=base)  # fmt: skip
    fields = [("issue", str(issue)), ("attempt", str(attempt)), ("run", run), ("pr", str(pr) if pr else "-"),
              ("branch", branch), ("head", head)]  # fmt: skip
    outcome = write_marker(gateway, issue, "codex-progress", fields,
                           body=f"Codex worker: attempt {attempt} on #{issue} pushed `{branch}` at `{head}`.",
                           once=True, dry_run=dry_run, settings=settings, env=env)  # fmt: skip
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
    try:
        st, owns = _owned(gateway, issue, attempt, run, env, (now or (lambda: datetime.now(timezone.utc)))(),
                          config or Config())  # fmt: skip
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
