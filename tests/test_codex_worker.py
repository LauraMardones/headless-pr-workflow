"""Local Codex worker: ``hpw dispatcher codex-worker`` and ``scripts/codex-worker.sh`` (issue #528).

Every path runs against an in-memory GitHub (the Block 1 fake plus the board, branches and pull
requests) and a fake eligibility hook: no network, no real ``gh``, no Codex app.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from headless_pr_workflow.dispatcher import cli, codex_worker
from headless_pr_workflow.dispatcher.codex_worker import Config, claim, finish, parse_marks, record_progress, status
from headless_pr_workflow.dispatcher.gateway import BoardCard, BoardSnapshot, GatewayError
from headless_pr_workflow.dispatcher.write import Settings

from tests.script_tools import find_bash, host_env, requires
from tests.test_dispatcher_write_block import BOT, PO, FakeGateway

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO = "o/r"
T0 = datetime(2026, 11, 13, 10, 0, tzinfo=timezone.utc)
H1 = "1" * 40
H2 = "2" * 40
H3 = "3" * 40
BRANCH = "codex/issue-5-local-worker"
CONFIG = Config(lease=timedelta(minutes=240), retry=timedelta(minutes=60))


class WorkerGitHub(FakeGateway):
    """The Block 1 fake plus what the worker reads. ``at`` is the time a new comment gets."""

    def __init__(self) -> None:
        super().__init__()
        self.at = T0
        self.cards: list[BoardCard] = []
        self.branches: dict[str, str] = {}
        self.prs: dict[int, dict] = {}
        self.ancestors: set[tuple[str, str]] = set()
        self.board_failures = 0
        self.on_post = None  # called after a comment is stored, e.g. to let a second run interleave
        self.before_post = None  # called before a comment is stored

    def add(self, number: int, body: str, login: str = BOT) -> None:
        self.clock += 1
        self.comments.setdefault(number, []).append({
            "id": 1000 + self.clock, "user": {"login": login}, "body": body,
            "created_at": self.at.strftime("%Y-%m-%dT%H:%M:%SZ"), "html_url": f"https://x/{number}#c{self.clock}",
        })

    def post_comment(self, number: int, body: str) -> dict:
        hook, self.before_post = self.before_post, None
        if hook:
            hook()
        try:
            return super().post_comment(number, body)
        finally:
            hook, self.on_post = self.on_post, None
            if hook:
                hook()

    def story(self, number: int, status: str = "Ready for implementation", *labels: str, state: str = "open") -> None:
        self.cards.append(BoardCard(item_id=f"I{number}", number=number, state=state, status=status,
                                    labels=labels or ("type:story", "executor:codex")))

    def list_board_items(self) -> BoardSnapshot:
        self.calls.append("board-items")
        if self.board_failures:
            self.board_failures -= 1
            raise GatewayError("read board items", "HTTP 502")
        return BoardSnapshot(options=dict(self.options), cards=list(self.cards))

    def matching_branches(self, prefix: str) -> dict[str, str]:
        self.calls.append(f"branches {prefix}")
        return {name: sha for name, sha in self.branches.items() if name.startswith(prefix)}

    def list_open_prs(self) -> list[dict]:
        self.calls.append("open-prs")
        return [pr for pr in self.prs.values() if pr["state"] == "open"]

    def pull_request(self, number: int) -> dict:
        self.calls.append(f"pr {number}")
        if number not in self.prs:
            raise GatewayError(f"read PR #{number}", "HTTP 404: Not Found", not_found=True)
        return self.prs[number]

    def add_pr(self, number: int, branch: str, head: str, body: str = "", state: str = "open") -> None:
        self.prs[number] = {"number": number, "state": state, "body": body, "head": {"ref": branch, "sha": head}}

    def compare_commits(self, base: str, head: str) -> str:
        self.calls.append(f"compare {base[:7]} {head[:7]}")
        return "ahead" if (base, head) in self.ancestors else "diverged"

    def writes(self) -> list[str]:
        return [c for c in self.calls if c.startswith(("post ", "set ", "add-label", "remove-label", "create-label"))]

    def marks(self, number: int) -> list:
        return parse_marks([c for c in self.comments.get(number, []) if c["user"]["login"] == BOT])


@pytest.fixture
def gh() -> WorkerGitHub:
    return WorkerGitHub()


@pytest.fixture
def settings() -> Settings:
    return Settings(retry_delay=0, evidence_retry_max=3, po_login=PO, sleep=lambda _s: None)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("CODEX_BOOKKEEPING_LOGIN", "DISPATCHER_LOGIN", "OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_WORKER_ID",
                 "CODEX_WORKER_RUN_ID", "CODEX_WORKER_ELIGIBILITY_COMMAND", "CODEX_WORKER_LEASE_MINUTES",
                 "CODEX_WORKER_RETRY_MINUTES"):
        monkeypatch.delenv(name, raising=False)


class Gate:
    """The #524 eligibility hook, recorded."""

    def __init__(self, answer: tuple[bool, str] = (True, "eligible")) -> None:
        self.answer = answer
        self.calls: list[tuple[str, int, str]] = []

    def __call__(self, repo: str, issue: int, command: str) -> tuple[bool, str]:
        self.calls.append((repo, issue, command))
        return self.answer


def do_claim(gh, settings, *, run="run-a", owner="po-laptop", gate=None, at=None, env=None):
    return claim(gh, REPO, owner=owner, run=run, eligibility=gate or Gate(), now=lambda: at or gh.at,
                 config=CONFIG, settings=settings, env=env or {})


def claims(gh, number=5):
    return [m for m in gh.marks(number) if m.action == "claim"]


# ── AC 1: one scheduled run selects one eligible item and starts one session ──


def test_a_run_claims_the_first_eligible_codex_item_and_prints_its_invocation(gh, settings):
    gh.story(3, "Ready for implementation", "type:story", "executor:claude-code-opus")
    gh.story(4, "In review")
    gh.story(6, "Ready for implementation", "type:epic", "executor:codex")
    gh.story(7, "Ready for implementation", "type:story", "executor:codex", "executor:claude-code-opus")
    gh.story(8, "Ready for implementation", state="closed")
    gh.story(9, "Ready for implementation", "type:story", "executor:codex", "prio:P2")
    gh.story(5, "Ready for implementation", "type:story", "executor:codex", "prio:P1")
    gate = Gate()
    result = do_claim(gh, settings, gate=gate)
    assert result.result == "claimed" and result.exit_code == 0
    call = result.fields["invocation"]
    assert call["issue"] == 5 and call["attempt"] == 1 and call["command"] == "implement" and call["run"] == "run-a"
    assert call["command_file"] == ".claude/commands/implement.md" and call["branch_prefix"] == "codex/issue-5-"
    assert call["resume"] is None and "progress" in call["progress"] and "finish" in call["finish"]
    assert gate.calls == [(REPO, 5, "implement")]
    assert gh.writes() == ["post 5"]
    [mark] = claims(gh)
    assert mark.fields == {"issue": "5", "attempt": "1", "owner": "po-laptop", "run": "run-a", "command": "implement",
                           "pr": "-", "branch": "-", "head": "-"}


def test_work_in_implementation_is_resumed_before_new_work(gh, settings):
    gh.story(2, "Ready for implementation", "type:story", "executor:codex", "prio:P0")
    gh.story(5, "In implementation")
    assert do_claim(gh, settings).fields["issue"] == 5


def test_nothing_queued_is_idle_and_writes_nothing(gh, settings):
    gh.story(4, "In review")
    result = do_claim(gh, settings)
    assert result.result == "idle" and result.exit_code == 1 and gh.writes() == []


def test_the_worker_never_writes_a_board_status_or_a_label(gh, settings):
    gh.story(5)
    do_claim(gh, settings)
    do_claim(gh, settings, run="run-b")
    assert all(c.startswith("post ") for c in gh.writes())


# ── AC 2: concurrent runs and replays give one owner ──────────────────────────


def test_two_concurrent_runs_on_one_item_give_one_owner(gh, settings):
    gh.story(5)
    seen = {}
    # Both runs read the item as free; run B posts and reads back before run A's claim is stored.
    gh.before_post = lambda: seen.setdefault("b", do_claim(gh, settings, run="run-b"))
    a = do_claim(gh, settings, run="run-a")
    b = seen["b"]
    assert (b.result, a.result) == ("claimed", "lost") and a.exit_code == 1 and "invocation" not in a.fields
    assert [m.fields["run"] for m in claims(gh)] == ["run-b", "run-a"]
    # A replay of either run gives the same answer.
    assert do_claim(gh, settings, run="run-a").result == "lost"
    assert do_claim(gh, settings, run="run-b").result == "continued"
    assert len(claims(gh)) == 2


def other_claim(run: str = "run-z", attempt: int = 1) -> str:
    return codex_worker.dispatcher_marker(
        f"action=codex-claim issue=5 attempt={attempt} owner=other-pc run={run} command=implement pr=- branch=- head=-")


def test_a_claim_that_lands_after_another_runs_claim_loses(gh, settings):
    gh.story(5)
    # The other computer's claim reaches GitHub after this run read the issue but before its own claim.
    gh.before_post = lambda: gh.add(5, other_claim())
    result = do_claim(gh, settings)
    assert result.result == "lost" and result.exit_code == 1 and "invocation" not in result.fields
    assert [m.fields["run"] for m in claims(gh)] == ["run-z", "run-a"]


def test_an_item_another_run_holds_is_skipped_without_a_write(gh, settings):
    gh.story(5)
    gh.add(5, other_claim())
    result = do_claim(gh, settings)
    assert result.result == "waiting" and result.fields["skipped"] == [{"issue": 5, "reason": "claimed"}]
    assert gh.writes() == []


def test_a_replay_of_the_same_run_continues_and_posts_nothing(gh, settings):
    gh.story(5)
    first = do_claim(gh, settings)
    posts = len(gh.posts())
    again = do_claim(gh, settings)
    assert again.result == "continued" and again.exit_code == 0 and len(gh.posts()) == posts
    assert again.fields["invocation"]["attempt"] == first.fields["invocation"]["attempt"] == 1


def test_a_new_run_of_the_task_while_the_claim_is_held_starts_nothing(gh, settings):
    gh.story(5)
    do_claim(gh, settings)
    result = do_claim(gh, settings, run="run-b", at=T0 + timedelta(minutes=30))
    assert result.result == "waiting" and result.exit_code == 1 and len(claims(gh)) == 1


def test_a_replay_of_a_run_that_lost_its_attempt_reports_lost(gh, settings):
    gh.story(5)
    gh.add(5, other_claim("run-z"))
    gh.add(5, other_claim("run-b"))
    result = do_claim(gh, settings, run="run-b")
    assert result.result == "lost" and result.exit_code == 1 and len(gh.posts()) == 0


@pytest.mark.parametrize("marker", [
    "action=codex-claim issue=5 attempt=one owner=x run=r command=implement",
    "action=codex-claim issue=5 attempt=1 owner=x run=r command=deploy",
    "action=codex-claim issue=6 attempt=1 owner=x run=r command=implement",
    "action=codex-claim issue=5 attempt=1 owner=x run=r command=implement stray",
])
def test_a_malformed_claim_fails_closed_with_waiting_evidence(gh, settings, marker):
    gh.story(5)
    gh.add(5, codex_worker.dispatcher_marker(marker))
    result = do_claim(gh, settings)
    assert result.result == "waiting" and result.fields["skipped"][0]["reason"] == "malformed-marker"
    assert [m.fields["reason"] for m in gh.marks(5) if m.action == "wait"] == ["conflicting-claim"]
    assert not [m for m in claims(gh) if m.fields.get("run") == "run-a"]


def test_a_replay_on_a_conflicting_item_reports_lost_and_starts_nothing(gh, settings):
    gh.story(5)
    gh.add(5, codex_worker.dispatcher_marker(
        "action=codex-claim issue=5 attempt=one owner=x run=run-a command=implement pr=- branch=- head=-"))
    result = do_claim(gh, settings)
    assert result.result == "lost" and result.fields["reason"] == "malformed-marker" and gh.writes() == []


def test_progress_from_a_run_that_lost_the_attempt_is_a_conflict(gh, settings):
    gh.story(5)
    gh.add(5, codex_worker.dispatcher_marker(
        "action=codex-claim issue=5 attempt=1 owner=x run=r1 command=implement pr=- branch=- head=-"))
    gh.add(5, codex_worker.dispatcher_marker(
        f"action=codex-progress issue=5 attempt=1 run=r2 pr=- branch={BRANCH} head={H1}"))
    result = do_claim(gh, settings, at=T0 + timedelta(days=1))
    assert result.result == "waiting" and result.fields["skipped"][0]["reason"] == "foreign-run"
    assert len(claims(gh)) == 1


def test_a_marker_from_another_account_is_ignored(gh, settings):
    gh.story(5)
    gh.add(5, codex_worker.dispatcher_marker(
        "action=codex-claim issue=5 attempt=1 owner=x run=r1 command=implement pr=- branch=- head=-"), login="intruder")
    result = do_claim(gh, settings)
    assert result.result == "claimed" and result.fields["attempt"] == 1


# ── AC 3: a lost response after a successful write ────────────────────────────


def test_a_lost_claim_response_is_reconciled_in_the_same_call(gh, settings):
    gh.story(5)
    gh.post_modes = ["lost"]
    result = do_claim(gh, settings)
    assert result.result == "claimed" and len(claims(gh)) == 1 and len(gh.posts()) == 1


def test_a_retry_after_a_lost_claim_response_continues_the_attempt(gh, settings):
    gh.story(5)
    gh.post_modes = ["lost"]

    def github_goes_away():
        gh.list_failures = 50

    gh.on_post = github_goes_away
    first = do_claim(gh, settings)
    assert first.result in ("failed", "unreadable") and first.exit_code != 0  # no session starts
    gh.list_failures = 0
    retry = do_claim(gh, settings)
    assert retry.result == "continued" and retry.fields["attempt"] == 1
    assert len(claims(gh)) == 1 and len(gh.posts()) == 1


def test_a_retried_progress_write_is_not_recorded_twice(gh, settings):
    gh.story(5)
    do_claim(gh, settings)
    gh.post_modes = ["lost"]
    one = record_progress(gh, REPO, 5, 1, "run-a", branch=BRANCH, head=H1, settings=settings, env={}, config=CONFIG,
                          now=lambda: gh.at)
    two = record_progress(gh, REPO, 5, 1, "run-a", branch=BRANCH, head=H1, settings=settings, env={}, config=CONFIG,
                          now=lambda: gh.at)
    assert one.result == "written" and two.result == "already" and two.exit_code == 0
    assert len([m for m in gh.marks(5) if m.action == "progress"]) == 1


def test_a_retried_finish_is_not_recorded_twice(gh, settings):
    gh.story(5)
    do_claim(gh, settings)
    kwargs = dict(result="done", reason="handoff", head=H1, settings=settings, env={}, config=CONFIG, now=lambda: gh.at)
    assert finish(gh, REPO, 5, 1, "run-a", **kwargs).result == "written"
    assert finish(gh, REPO, 5, 1, "run-a", **kwargs).result == "already"
    assert len([m for m in gh.marks(5) if m.action == "end"]) == 1


def test_progress_and_finish_from_a_run_that_does_not_own_the_attempt_write_nothing(gh, settings):
    gh.story(5)
    do_claim(gh, settings)
    posts = len(gh.posts())
    other = record_progress(gh, REPO, 5, 1, "run-x", branch=BRANCH, head=H1, settings=settings, env={}, config=CONFIG)
    late = finish(gh, REPO, 5, 2, "run-a", result="done", reason="handoff", head=H1, settings=settings, env={},
                  config=CONFIG)
    assert other.result == late.result == "not-owner" and other.exit_code == 1 and len(gh.posts()) == posts


# ── AC 4: an interrupted session resumes its pushed work ──────────────────────


def interrupted(gh: WorkerGitHub, *, progress: bool = True) -> None:
    gh.story(5, "In implementation")
    gh.add(5, codex_worker.dispatcher_marker(
        "action=codex-claim issue=5 attempt=1 owner=po-laptop run=r1 command=implement pr=- branch=- head=-"))
    if progress:
        gh.add(5, codex_worker.dispatcher_marker(
            f"action=codex-progress issue=5 attempt=1 run=r1 pr=12 branch={BRANCH} head={H1}"))
    gh.branches[BRANCH] = H2  # a commit pushed after the last progress record
    gh.ancestors.add((H1, H2))
    gh.add_pr(12, BRANCH, H2, body="Closes #5")


def test_an_interrupted_session_is_resumed_from_the_current_remote_head(gh, settings):
    interrupted(gh)
    later = T0 + timedelta(hours=5)
    result = do_claim(gh, settings, run="r2", at=later)
    assert result.result == "claimed" and result.fields["attempt"] == 2
    assert result.fields["invocation"]["resume"] == {"pr": 12, "branch": BRANCH, "head": H2}
    [end] = [m for m in gh.marks(5) if m.action == "end"]
    assert end.fields["attempt"] == "1" and end.fields["run"] == "r1" and end.fields["reason"] == "interrupted"
    [_, second] = claims(gh)
    assert second.fields["branch"] == BRANCH and second.fields["head"] == H2 and second.fields["pr"] == "12"
    assert gh.branches == {BRANCH: H2} and list(gh.prs) == [12]  # no new branch and no new PR


def test_a_lost_progress_write_still_finds_the_pushed_branch(gh, settings):
    interrupted(gh, progress=False)
    result = do_claim(gh, settings, run="r2", at=T0 + timedelta(hours=5))
    assert result.fields["invocation"]["resume"] == {"pr": 12, "branch": BRANCH, "head": H2}


def test_the_lease_keeps_a_running_session_from_being_resumed_twice(gh, settings):
    interrupted(gh)
    result = do_claim(gh, settings, run="r2", at=T0 + timedelta(hours=3))
    assert result.result == "waiting" and len(claims(gh)) == 1 and gh.writes() == []


def test_the_interrupted_session_cannot_record_progress_after_the_resume(gh, settings):
    interrupted(gh)
    do_claim(gh, settings, run="r2", at=T0 + timedelta(hours=5))
    late = record_progress(gh, REPO, 5, 1, "r1", branch=BRANCH, head=H3, settings=settings, env={}, config=CONFIG)
    assert late.result == "not-owner"


@pytest.mark.parametrize("change, reason", [
    (lambda gh: gh.ancestors.clear(), "head-diverged"),
    (lambda gh: gh.branches.clear(), "branch-missing"),
    (lambda gh: gh.prs[12].update(state="closed"), "pr-closed"),
    (lambda gh: gh.prs[12]["head"].update(ref="codex/issue-5-other"), "pr-branch-mismatch"),
    (lambda gh: gh.add_pr(40, "claude/issue-5-x", H3, body="Closes #5"), "foreign-pr"),
])
def test_resume_facts_that_do_not_match_github_start_nothing(gh, settings, change, reason):
    interrupted(gh)
    change(gh)
    result = do_claim(gh, settings, run="r2", at=T0 + timedelta(hours=5))
    assert result.result == "waiting" and result.fields["skipped"][0]["reason"] == reason
    assert len(claims(gh)) == 1
    assert [m.fields["reason"] for m in gh.marks(5) if m.action == "wait"] == [reason]


def test_two_pushed_branches_without_a_record_are_ambiguous(gh, settings):
    gh.story(5, "In implementation")
    gh.branches = {BRANCH: H1, "codex/issue-5-second-try": H2}
    result = do_claim(gh, settings)
    assert result.fields["skipped"][0]["reason"] == "ambiguous-branch" and claims(gh) == []


def test_a_handed_off_head_is_not_started_again(gh, settings):
    interrupted(gh)
    gh.comments[5] = []
    gh.add(5, codex_worker.dispatcher_marker(
        f"action=codex-claim issue=5 attempt=1 owner=po-laptop run=r1 command=implement pr=12 branch={BRANCH} head={H2}"))
    gh.add(5, codex_worker.dispatcher_marker(
        f"action=codex-end issue=5 attempt=1 run=r1 result=done reason=handoff head={H2}"))
    result = do_claim(gh, settings, run="r2")
    assert result.fields["skipped"] == [{"issue": 5, "reason": "handed-off-on-head"}] and len(claims(gh)) == 1


# ── AC 5: unavailable, usage limit and unreadable all wait ────────────────────


def test_a_usage_limit_waits_and_a_later_run_resumes_the_work(gh, settings):
    gh.story(5)
    do_claim(gh, settings, run="r1")
    gh.branches[BRANCH] = H1
    record_progress(gh, REPO, 5, 1, "r1", branch=BRANCH, head=H1, settings=settings, env={}, config=CONFIG,
                    now=lambda: gh.at)
    stopped = finish(gh, REPO, 5, 1, "r1", result="wait", reason="usage-limit", until="2026-11-13T15:00:00Z",
                     settings=settings, env={}, config=CONFIG, now=lambda: gh.at)
    assert stopped.result == "written"
    early = do_claim(gh, settings, run="r2", at=T0 + timedelta(hours=2))
    assert early.result == "waiting" and early.fields["skipped"][0]["reason"] == "retry-later"
    assert early.fields["skipped"][0]["not_before"] == "2026-11-13T15:00:00Z" and len(claims(gh)) == 1
    later = do_claim(gh, settings, run="r3", at=T0 + timedelta(hours=6))
    assert later.result == "claimed" and later.fields["attempt"] == 2
    assert later.fields["invocation"]["resume"] == {"pr": None, "branch": BRANCH, "head": H1}


def test_a_wait_without_until_retries_after_the_retry_interval(gh, settings):
    gh.story(5)
    do_claim(gh, settings, run="r1")
    finish(gh, REPO, 5, 1, "r1", result="wait", reason="unavailable", settings=settings, env={}, config=CONFIG)
    assert do_claim(gh, settings, run="r2", at=T0 + timedelta(minutes=59)).result == "waiting"
    assert do_claim(gh, settings, run="r3", at=T0 + timedelta(minutes=61)).result == "claimed"


@pytest.mark.parametrize("answer, reason", [
    ((False, "reserve"), "reserve"),
    ((False, "eligibility-unconfigured"), "eligibility-unconfigured"),
])
def test_an_ineligible_run_waits_with_evidence_once_and_starts_nothing(gh, settings, answer, reason):
    gh.story(5)
    gh.story(6)
    gate = Gate(answer)
    first = do_claim(gh, settings, gate=gate)
    second = do_claim(gh, settings, gate=gate, run="run-b")
    assert first.result == second.result == "waiting" and first.exit_code == 1
    assert gate.calls == [(REPO, 5, "implement"), (REPO, 5, "implement")]  # the reserve is shared: #6 is not tried
    assert claims(gh) == [] and [m.fields["reason"] for m in gh.marks(5) if m.action == "wait"] == [reason]


@pytest.mark.parametrize("fail", ["board", "comments", "login"])
def test_unreadable_github_starts_nothing_and_writes_nothing(gh, settings, fail, monkeypatch):
    gh.story(5)
    if fail == "board":
        gh.board_failures = 1
    elif fail == "comments":
        gh.list_failures = 1
    else:
        monkeypatch.setattr(gh, "viewer_login", lambda: (_ for _ in ()).throw(GatewayError("resolve dispatcher login", "HTTP 401")))
    result = do_claim(gh, settings)
    assert result.result == "unreadable" and result.exit_code == 3 and gh.writes() == []


@pytest.mark.parametrize("name", ["OPENAI_API_KEY", "CODEX_API_KEY"])
def test_an_api_key_in_the_environment_refuses_before_any_github_call(gh, settings, name):
    gh.story(5)
    result = do_claim(gh, settings, env={name: "sk-test"})
    assert result.result == "refused" and result.fields["reason"] == "api-key-set" and gh.calls == []
    assert "sk-test" not in json.dumps(result.to_dict())


def test_a_session_that_never_came_back_is_recorded_as_interrupted_by_the_next_run(gh, settings):
    # The computer or the app was off: no run wrote anything until the lease ran out.
    gh.story(5)
    do_claim(gh, settings, run="r1")
    later = do_claim(gh, settings, run="r2", gate=Gate((False, "reserve")), at=T0 + timedelta(hours=5))
    assert later.result == "waiting"
    reasons = [(m.action, m.fields.get("reason")) for m in gh.marks(5) if m.action in ("end", "wait")]
    assert reasons == [("end", "interrupted"), ("wait", "reserve")]


def test_status_reads_the_queue_and_each_claim_from_github(gh, settings):
    interrupted(gh)
    gh.story(6)
    result = status(gh, REPO, now=lambda: T0 + timedelta(hours=1), config=CONFIG, env={})
    assert result.result == "read" and gh.writes() == []
    by_issue = {item["issue"]: item for item in result.fields["items"]}
    assert by_issue[5]["state"] == "held" and by_issue[5]["run"] == "r1"
    assert by_issue[5]["recorded"] == {"pr": "12", "branch": BRANCH, "head": H1}
    assert by_issue[6]["state"] == "free" and by_issue[6]["attempt"] is None


# ── AC 6: the eligibility hook and the CLI contract ───────────────────────────


def gate_command(tmp_path: Path, code: int, stdout: str = "", sleep: float = 0) -> str:
    script = tmp_path / "gate.py"
    script.write_text(f"import sys, time\ntime.sleep({sleep})\nprint({stdout!r})\nsys.exit({code})\n", encoding="utf-8")
    return f'"{Path(sys.executable).as_posix()}" "{script.as_posix()}"'


@pytest.mark.parametrize("code, stdout, expected", [
    (0, "", (True, "eligible")),
    (1, '{"reason": "weekly-reserve"}', (False, "weekly-reserve")),
    (1, "no", (False, "ineligible")),
    (1, '{"reason": "has space"}', (False, "ineligible")),
    (2, "", (False, "eligibility-unreadable")),
])
def test_the_eligibility_hook_fails_closed(tmp_path, code, stdout, expected):
    assert codex_worker.command_gate(gate_command(tmp_path, code, stdout))(REPO, 5, "implement") == expected


@pytest.mark.parametrize("command", [None, "", "   ", "/no/such/gate-command", '"unbalanced'])
def test_a_missing_or_broken_hook_command_is_not_eligible(command):
    eligible, reason = codex_worker.command_gate(command)(REPO, 5, "implement")
    assert eligible is False and reason in ("eligibility-unconfigured", "eligibility-unreadable")


def test_the_hook_receives_the_item(tmp_path):
    script = tmp_path / "gate.py"
    out = tmp_path / "argv.json"
    script.write_text(f"import json, sys\nopen({str(out)!r}, 'w').write(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
    codex_worker.command_gate(f'"{Path(sys.executable).as_posix()}" "{script.as_posix()}"')(REPO, 5, "implement")
    assert json.loads(out.read_text()) == ["--repo", REPO, "--issue", "5", "--command", "implement"]


def test_a_hook_that_does_not_answer_in_time_is_unreadable(tmp_path):
    command = gate_command(tmp_path, 0, sleep=5)
    assert codex_worker.command_gate(command, timeout=0.5)(REPO, 5, "implement") == (False, "eligibility-unreadable")


def run_cli(gh, settings, capsys, *argv):
    rc = cli.main(["codex-worker", *argv], gateway_factory=lambda repo: gh, settings=settings)
    out, _ = capsys.readouterr()
    lines = out.splitlines()
    assert len(lines) == 1, out
    return rc, json.loads(lines[0])


def test_the_cli_claims_with_the_configured_hook_and_prints_one_json_line(gh, settings, capsys, monkeypatch, tmp_path):
    gh.story(5)
    monkeypatch.setenv("CODEX_WORKER_ID", "po-laptop")
    monkeypatch.setenv("CODEX_WORKER_ELIGIBILITY_COMMAND", gate_command(tmp_path, 0))
    rc, out = run_cli(gh, settings, capsys, "claim", "--repo", REPO, "--run", "r1")
    assert rc == 0 and out["ok"] is True and out["block"] == "codex-worker" and out["result"] == "claimed"
    assert out["invocation"]["issue"] == 5 and out["owner"] == "po-laptop"
    rc, out = run_cli(gh, settings, capsys, "progress", "--repo", REPO, "--issue", "5", "--attempt", "1", "--run", "r1",
                      "--branch", BRANCH, "--head", H1, "--pr", "12")
    assert rc == 0 and out["result"] == "written"
    rc, out = run_cli(gh, settings, capsys, "finish", "--repo", REPO, "--issue", "5", "--attempt", "1", "--run", "r1",
                      "--result", "wait", "--reason", "usage-limit", "--until", "2026-11-13T18:00:00+01:00")
    assert rc == 0 and out["result"] == "written"
    [end] = [m for m in gh.marks(5) if m.action == "end"]
    assert end.fields["until"] == "2026-11-13T17:00:00Z"
    rc, out = run_cli(gh, settings, capsys, "status", "--repo", REPO)
    assert rc == 0 and out["items"][0]["ended"] == "wait"


def test_the_cli_without_a_hook_waits(gh, settings, capsys):
    gh.story(5)
    rc, out = run_cli(gh, settings, capsys, "claim", "--repo", REPO, "--owner", "pc")
    assert rc == 1 and out["result"] == "waiting" and out["skipped"][0]["reason"] == "eligibility-unconfigured"


@pytest.mark.parametrize("argv", [
    ["claim", "--repo", REPO],  # no owner
    ["claim", "--repo", REPO, "--owner", "has space"],
    ["claim", "--repo", "bad", "--owner", "pc"],
    ["progress", "--repo", REPO, "--issue", "5", "--attempt", "1", "--run", "r", "--branch", "claude/issue-5-x", "--head", H1],
    ["progress", "--repo", REPO, "--issue", "5", "--attempt", "1", "--run", "r", "--branch", "codex/issue-5-", "--head", H1],
    ["progress", "--repo", REPO, "--issue", "5", "--attempt", "1", "--run", "r", "--branch", BRANCH, "--head", "main"],
    ["finish", "--repo", REPO, "--issue", "5", "--attempt", "1", "--run", "r", "--result", "done"],
    ["finish", "--repo", REPO, "--issue", "5", "--attempt", "1", "--run", "r", "--result", "wait"],
    ["finish", "--repo", REPO, "--issue", "5", "--attempt", "1", "--run", "r", "--result", "wait", "--reason", "a b"],
    ["finish", "--repo", REPO, "--issue", "5", "--attempt", "1", "--run", "r", "--result", "wait", "--reason", "x",
     "--until", "tomorrow"],
    ["finish", "--repo", REPO, "--issue", "5", "--attempt", "0", "--run", "r", "--result", "done", "--head", H1],
])
def test_invalid_arguments_exit_2_without_github_calls(gh, settings, capsys, argv):
    rc, out = run_cli(gh, settings, capsys, *argv)
    assert rc == 2 and out["result"] == "invalid" and out["block"] == "codex-worker" and gh.calls == []


def test_an_invalid_lease_setting_exits_2(gh, settings, capsys, monkeypatch):
    monkeypatch.setenv("CODEX_WORKER_LEASE_MINUTES", "0")
    rc, out = run_cli(gh, settings, capsys, "status", "--repo", REPO)
    assert rc == 2 and "CODEX_WORKER_LEASE_MINUTES" in out["error"]


@requires("bash")
def test_the_scheduled_task_script_passes_through_to_hpw(tmp_path):
    fake = tmp_path / "hpw"
    fake.write_text('#!/usr/bin/env bash\necho "{\\"args\\": \\"$*\\"}"\nexit 3\n', encoding="utf-8", newline="\n")
    fake.chmod(0o755)
    env = {**host_env(), "HPW_BIN": str(fake)}
    script = str(REPO_ROOT / "scripts" / "codex-worker.sh")
    done = subprocess.run([find_bash(), script, "claim", "--repo", REPO], capture_output=True, text=True, env=env,
                          timeout=60)
    assert done.returncode == 3 and json.loads(done.stdout) == {"args": f"dispatcher codex-worker claim --repo {REPO}"}
    bad = subprocess.run([find_bash(), script, "merge", "--repo", REPO], capture_output=True, text=True, env=env,
                         timeout=60)
    assert bad.returncode == 2 and bad.stdout == ""


# ── AC 7: the documentation names the same protocol ───────────────────────────


def test_the_dispatcher_config_documents_the_worker_protocol():
    doc = (REPO_ROOT / "docs" / "DISPATCHER-CONFIG.md").read_text(encoding="utf-8")
    section = doc[doc.index("## Local Codex worker"):]
    section = section[:section.index("\n## ", 3)]
    for word in ("codex-claim", "codex-progress", "codex-end", "codex-wait", "CODEX_WORKER_ID", "CODEX_WORKER_RUN_ID",
                 "CODEX_WORKER_ELIGIBILITY_COMMAND", "CODEX_WORKER_LEASE_MINUTES", "CODEX_WORKER_RETRY_MINUTES",
                 "scripts/codex-worker.sh claim", "interrupted", "usage-limit", "eligibility-unconfigured",
                 "lowest comment id", "OPENAI_API_KEY", *codex_worker.COMMAND_BY_STATUS):
        assert word in section, word
    for reason in ("head-diverged", "branch-missing", "pr-closed", "pr-missing", "pr-branch-mismatch", "foreign-pr",
                   "ambiguous-branch", "ambiguous-pr", "conflicting-claim", "retry-later", "handed-off-on-head"):
        assert f"`{reason}`" in section, reason
    assert f"{codex_worker.DEFAULT_LEASE_MINUTES}" in section and f"{codex_worker.DEFAULT_RETRY_MINUTES}" in section
