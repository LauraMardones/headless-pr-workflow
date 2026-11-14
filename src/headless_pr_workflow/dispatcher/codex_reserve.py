"""The Codex worker's eligibility command (issue #542, ADR-012 → Codex Plus amendment).

``CODEX_WORKER_ELIGIBILITY_COMMAND`` runs ``scripts/codex-eligibility.sh --repo <r> --issue <n> --command <c>``.
Exit ``0`` is eligible; exit ``1`` is not and prints ``{"reason": "<word>", "detail": "<text>"}``; any other
result is unreadable to the worker (``eligibility-unreadable``). Nothing here starts a model session other than
the Claude allowance probe, and nothing reads an OpenAI API key.

Every session start needs the Codex Plus five-hour and weekly windows at or above the PO's reserve. Only new
Codex work (a story with no ``codex/issue-<n>-*`` branch yet) also needs Claude review capacity: the Claude
reserve and fewer open Codex PRs waiting for a Claude review than ``CODEX_REVIEW_QUEUE_MAX``. A resume or a fix
round skips the Claude checks, because it shrinks the review queue.

The decision is the pure ``decide``: readings in, verdict out. ``evaluate`` reads them, in the order of cost:
Codex reading, review queue, Claude probe.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from ..review_policy import CODEX_BOT_LOGIN
from .codex_worker import branch_prefix
from .gateway import Gateway, GatewayError

CODEX_PREFIX = "codex/issue-"
FORMAL_REVIEW_STATES = ("APPROVED", "CHANGES_REQUESTED", "COMMENTED")
API_KEY_NAMES = ("OPENAI_API_KEY", "CODEX_API_KEY")
DEFAULT_CODEX_TIMEOUT = 20
DEFAULT_CLAUDE_TIMEOUT = 25  # below the worker's 60-second hook timeout together with the Codex reading
SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


class InvalidConfig(Exception):
    """A variable has an invalid value: the reason is ``reserve-config``."""


class Unreadable(Exception):
    """A reading could not be taken; ``reason`` is the word the refusal carries."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Window:
    remaining: float
    resets_at: str | None


@dataclass(frozen=True)
class Allowance:
    five_hour: Window
    weekly: Window


@dataclass(frozen=True)
class Config:
    codex_five_hour: int = 10
    codex_weekly: int = 20
    queue_max: int = 1

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Config:
        """Raises ``InvalidConfig`` before any probe runs. The Claude reserve (``RESERVE_*``) is read and checked
        by ``scripts/dispatcher-allowance.sh`` itself, so no second Claude reserve exists."""

        def percent(name: str, default: int) -> int:
            raw = (env.get(name) or "").strip()
            if raw == "":
                return default
            if not raw.isascii() or not raw.isdigit() or int(raw) > 100:
                raise InvalidConfig(f"{name} must be a whole number from 0 to 100, got {raw!r}")
            return int(raw)

        queue_raw = (env.get("CODEX_REVIEW_QUEUE_MAX") or "").strip()
        queue_max = 1
        if queue_raw:
            if not queue_raw.isascii() or not queue_raw.isdigit() or int(queue_raw) < 1:
                raise InvalidConfig(f"CODEX_REVIEW_QUEUE_MAX must be a whole number of 1 or more, got {queue_raw!r}")
            queue_max = int(queue_raw)
        return cls(
            codex_five_hour=percent("CODEX_RESERVE_FIVE_HOUR_PERCENT", 10),
            codex_weekly=percent("CODEX_RESERVE_WEEKLY_PERCENT", 20),
            queue_max=queue_max,
        )


@dataclass(frozen=True)
class Verdict:
    eligible: bool
    reason: str = "eligible"
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"eligible": True} if self.eligible else {"reason": self.reason, "detail": self.detail}


def _share(value: float) -> str:
    return f"{value:g}%"


def below_reserve(allowance: Allowance, five_hour: int, weekly: int) -> str:
    """The windows under their reserve, one clause each; empty when both are at or above it (equality starts)."""

    clauses = []
    for name, window, reserve in (("five-hour", allowance.five_hour, five_hour), ("weekly", allowance.weekly, weekly)):
        if window.remaining < reserve:
            clauses.append(f"{name} window {_share(window.remaining)} left, reserve {reserve}%, "
                           f"resets {window.resets_at or 'unknown'}")  # fmt: skip
    return "; ".join(clauses)


def decide(config: Config, codex: Allowance, *, new_work: bool, queue: int | None = None,
           claude_below: str | None = None) -> Verdict:  # fmt: skip
    """Readings in, verdict out. ``queue`` is the number of Codex PRs waiting for a Claude review and
    ``claude_below`` the Claude allowance probe's refusal text (``None``: it started). Both are only read for new work."""

    below = below_reserve(codex, config.codex_five_hour, config.codex_weekly)
    if below:
        return Verdict(False, "codex-reserve", below)
    if not new_work:
        return Verdict(True)
    if queue is None:
        return Verdict(False, "review-queue-unreadable", "the Codex PRs waiting for a Claude review were not read")
    if queue >= config.queue_max:
        return Verdict(False, "review-queue", f"{queue} Codex PR(s) wait for a Claude review, limit {config.queue_max}")
    if claude_below is not None:
        return Verdict(False, "claude-reserve", claude_below)
    return Verdict(True)


# ── readings ──────────────────────────────────────────────────────────────────


def _probe_env(env: Mapping[str, str]) -> dict[str, str]:
    """The probes run without any OpenAI API key: a metered key must never be reachable from them."""

    return {k: v for k, v in env.items() if k not in API_KEY_NAMES}


def _timeout(env: Mapping[str, str], name: str, default: int) -> int:
    raw = (env.get(name) or "").strip()
    return int(raw) if raw.isascii() and raw.isdigit() and int(raw) > 0 else default


def _window(value: Any, name: str) -> Window:
    pct = value.get("remaining_percent") if isinstance(value, dict) else None
    if isinstance(pct, bool) or not isinstance(pct, (int, float)) or not 0 <= pct <= 100:
        raise Unreadable("allowance-unreadable", f"the Codex {name} window has no remaining_percent from 0 to 100")
    at = value.get("resets_at")
    return Window(float(pct), at if isinstance(at, str) and at.strip() else None)


def read_codex_allowance(env: Mapping[str, str]) -> Allowance:
    """Run ``CODEX_ALLOWANCE_COMMAND``, which prints ``{"five_hour": {"remaining_percent": n, "resets_at": iso},
    "weekly": {...}}`` on the PO's computer. The reading comes from the signed-in Codex app or CLI, never from a
    metered API call. Anything else (no command, exit other than 0, a timeout, a changed shape) is unreadable."""

    command = (env.get("CODEX_ALLOWANCE_COMMAND") or "").strip()
    if not command:
        raise Unreadable("allowance-unreadable", "CODEX_ALLOWANCE_COMMAND is not set")
    try:
        proc = subprocess.run(shlex.split(command), capture_output=True, encoding="utf-8", errors="replace",
                              timeout=_timeout(env, "CODEX_ALLOWANCE_TIMEOUT", DEFAULT_CODEX_TIMEOUT), check=False,
                              env=_probe_env(env), stdin=subprocess.DEVNULL)  # fmt: skip
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise Unreadable("allowance-unreadable", "the Codex allowance command did not run or timed out") from None
    if proc.returncode != 0:
        raise Unreadable("allowance-unreadable", f"the Codex allowance command exited {proc.returncode}")
    try:
        data = json.loads(proc.stdout or "")
    except ValueError:
        raise Unreadable("allowance-unreadable", "the Codex allowance command printed no JSON") from None
    if not isinstance(data, dict):
        raise Unreadable("allowance-unreadable", "the Codex allowance reading has an unexpected shape")
    return Allowance(_window(data.get("five_hour"), "five-hour"), _window(data.get("weekly"), "weekly"))


def read_claude_allowance(env: Mapping[str, str]) -> str | None:
    """The ``scripts/dispatcher-allowance.sh`` contract: exit 0 start, 1 refuse, anything else unreadable.
    Returns ``None`` for a start and the refusal text for a refusal."""

    command = (env.get("CLAUDE_ALLOWANCE_COMMAND") or "").strip()
    probe_env = _probe_env(env)
    probe_env.setdefault("ALLOWANCE_PROBE_TIMEOUT", str(DEFAULT_CLAUDE_TIMEOUT))
    try:
        argv = shlex.split(command) if command else ["bash", str(SCRIPTS / "dispatcher-allowance.sh")]
        proc = subprocess.run(argv, capture_output=True, encoding="utf-8", errors="replace", env=probe_env,
                              timeout=_timeout(env, "CODEX_CLAUDE_PROBE_TIMEOUT", DEFAULT_CLAUDE_TIMEOUT + 10),
                              check=False, stdin=subprocess.DEVNULL)  # fmt: skip
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise Unreadable("allowance-unreadable", "the Claude allowance probe did not run or timed out") from None
    if proc.returncode == 0:
        return None
    if proc.returncode != 1:
        raise Unreadable("allowance-unreadable", f"the Claude allowance probe exited {proc.returncode}")
    try:
        reason = str(json.loads((proc.stdout or "").strip().splitlines()[0]).get("reason") or "")
    except (ValueError, IndexError, AttributeError):
        reason = ""
    return f"Claude allowance below its reserve: {reason or 'refused'}"


def is_new_work(gateway: Gateway, issue: int) -> bool:
    """New Codex work has no ``codex/issue-<n>-*`` branch yet; a branch means a resume or a fix round."""

    try:
        return not gateway.matching_branches(branch_prefix(issue))
    except GatewayError as exc:
        raise Unreadable("review-queue-unreadable", f"the story's branches were not read: {exc}") from None


def waiting_codex_prs(gateway: Gateway) -> int:
    """Open, ready-for-review Codex PRs (branch ``codex/issue-<n>-*``) with no submitted review of their current
    head from anyone but the Codex connector. Counted from PR facts."""

    try:
        waiting = 0
        for pr in gateway.list_open_prs():
            head = pr.get("head") or {}
            if pr.get("draft") or not str(head.get("ref") or "").startswith(CODEX_PREFIX):
                continue
            sha = str(head.get("sha") or "")
            reviewed = any(
                str(review.get("commit_id") or "") == sha and review.get("state") in FORMAL_REVIEW_STATES
                and (review.get("user") or {}).get("login") != CODEX_BOT_LOGIN
                for review in gateway.pr_reviews(int(pr["number"]))
            )  # fmt: skip
            waiting += 0 if reviewed else 1
        return waiting
    except (GatewayError, KeyError, TypeError, ValueError) as exc:
        raise Unreadable("review-queue-unreadable", f"the review queue was not read: {exc}") from None


def evaluate(env: Mapping[str, str], gateway: Gateway, issue: int, *,
             codex_probe: Callable[[Mapping[str, str]], Allowance] = read_codex_allowance,
             claude_probe: Callable[[Mapping[str, str]], str | None] = read_claude_allowance) -> Verdict:  # fmt: skip
    """The whole answer; never starts a model session besides the Claude allowance probe."""

    try:
        # Facts first: a configuration error or an unreadable story stops before any probe spends allowance.
        new_work = is_new_work(gateway, issue)
        try:
            config = Config.from_env(env)
        except InvalidConfig as exc:
            return Verdict(False, "reserve-config", str(exc))
        codex = codex_probe(env)
        verdict = decide(config, codex, new_work=False)
        if not verdict.eligible or not new_work:
            return verdict
        queue = waiting_codex_prs(gateway)
        if queue >= config.queue_max:
            return decide(config, codex, new_work=True, queue=queue)
        return decide(config, codex, new_work=True, queue=queue, claude_below=claude_probe(env))
    except Unreadable as exc:
        return Verdict(False, exc.reason, exc.detail)


def main(argv: list[str], env: Mapping[str, str] | None = None, gateway: Gateway | None = None) -> int:
    parser = argparse.ArgumentParser(prog="codex-eligibility", add_help=False)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--command", required=True, choices=("refine", "implement"))
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2
    env = os.environ if env is None else env
    try:
        if gateway is None:
            from .gateway import GhGateway

            gateway = GhGateway(args.repo)
        verdict = evaluate(env, gateway, args.issue)
    except Exception as exc:  # noqa: BLE001 - anything unexpected is unreadable, never eligible
        print(f"[CODEX ELIGIBILITY] unreadable: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(json.dumps(verdict.to_dict(), separators=(",", ":")))
    print(f"[CODEX ELIGIBILITY] #{args.issue} {'eligible' if verdict.eligible else verdict.reason}", file=sys.stderr)
    return 0 if verdict.eligible else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
