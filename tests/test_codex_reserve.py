"""Issue #542: the Codex worker's eligibility command (reserve and Claude review capacity).

Fakes stand in for the Codex allowance reading, the Claude allowance probe and the PR facts; no model session
runs. Variables and reasons named here are the ones docs/DISPATCHER-CONFIG.md → "Eligibility command" lists.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from headless_pr_workflow.dispatcher import codex_reserve as cr
from headless_pr_workflow.dispatcher import codex_worker
from headless_pr_workflow.dispatcher.codex_reserve import Allowance, Config, Window, decide, evaluate
from headless_pr_workflow.dispatcher.gateway import GatewayError

from tests.script_tools import find_bash, host_env, requires
from tests.test_codex_worker import BRANCH, CONFIG, H1, REPO, T0, WorkerGitHub, claims, do_claim, settings  # noqa: F401

REPO_ROOT = Path(__file__).resolve().parents[1]
RESET = "2026-11-13T15:00:00Z"
OK = Allowance(Window(50, RESET), Window(60, "2026-11-20T00:00:00Z"))


def codex(five: float = 50, weekly: float = 60) -> Allowance:
    return Allowance(Window(five, RESET), Window(weekly, "2026-11-20T00:00:00Z"))


class ReviewGitHub(WorkerGitHub):
    def __init__(self) -> None:
        super().__init__()
        self.reviews: dict[int, list[dict]] = {}
        self.fail_reads = False

    def list_open_prs(self) -> list[dict]:
        if self.fail_reads:
            raise GatewayError("read open pull requests", "HTTP 502")
        return super().list_open_prs()

    def pr_reviews(self, number: int) -> list[dict]:
        self.calls.append(f"reviews {number}")
        return self.reviews.get(number, [])

    def codex_pr(self, number: int, head: str = H1, *, draft: bool = False, branch: str | None = None) -> None:
        self.add_pr(number, branch or f"codex/issue-{number}-x", head)
        self.prs[number]["draft"] = draft

    def review(self, number: int, head: str, login: str = "claude-reviewer", state: str = "APPROVED") -> None:
        self.reviews.setdefault(number, []).append({"commit_id": head, "state": state, "user": {"login": login}})


@pytest.fixture
def gh() -> ReviewGitHub:
    return ReviewGitHub()


class Probes:
    """The Codex reading and the Claude allowance probe, recorded."""

    def __init__(self, codex_reading=None, claude=None) -> None:
        self.codex_reading = codex_reading or codex()
        self.claude = claude  # None starts; a string refuses; an Unreadable instance raises
        self.calls: list[str] = []

    def codex_probe(self, env):
        self.calls.append("codex")
        if isinstance(self.codex_reading, cr.Unreadable):
            raise self.codex_reading
        return self.codex_reading

    def claude_probe(self, env):
        self.calls.append("claude")
        if isinstance(self.claude, cr.Unreadable):
            raise self.claude
        return self.claude


def run(gateway, probes, env=None, issue=5):
    return evaluate(env or {}, gateway, issue, codex_probe=probes.codex_probe, claude_probe=probes.claude_probe)


# ── AC 1: the Codex reserve, equality starts ──────────────────────────────────


def test_both_windows_above_the_reserve_start():
    assert decide(Config(), codex(), new_work=False).eligible


def test_equality_with_the_reserve_starts():
    assert decide(Config(), codex(10, 20), new_work=False).eligible
    assert decide(Config(codex_five_hour=0, codex_weekly=0), codex(0, 0), new_work=False).eligible


@pytest.mark.parametrize("five, weekly, window", [(9.9, 60, "five-hour"), (50, 19, "weekly"), (9, 19, "five-hour")])
def test_a_window_below_its_reserve_refuses_and_names_it(five, weekly, window):
    verdict = decide(Config(), codex(five, weekly), new_work=False)
    assert (verdict.eligible, verdict.reason) == (False, "codex-reserve")
    assert f"{window} window" in verdict.detail and "reserve" in verdict.detail and "resets" in verdict.detail
    assert RESET in verdict.detail if window == "five-hour" else "2026-11-20T00:00:00Z" in verdict.detail


def test_the_reserve_is_configurable_without_a_code_change(gh):
    probes = Probes(codex(30, 60))
    assert run(gh, probes).eligible
    verdict = run(gh, probes, {"CODEX_RESERVE_FIVE_HOUR_PERCENT": "31"})
    assert verdict.reason == "codex-reserve"
    assert run(gh, probes, {"CODEX_RESERVE_FIVE_HOUR_PERCENT": "30", "CODEX_RESERVE_WEEKLY_PERCENT": "60"}).eligible


# ── AC 2: reserve values ──────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["CODEX_RESERVE_FIVE_HOUR_PERCENT", "CODEX_RESERVE_WEEKLY_PERCENT"])
@pytest.mark.parametrize("value", ["abc", "-1", "101", "10%", "7.5", "1e1", "²"])
def test_an_invalid_reserve_is_a_refusal_and_no_probe_runs(gh, name, value):
    probes = Probes()
    verdict = run(gh, probes, {name: value})
    assert (verdict.eligible, verdict.reason) == (False, "reserve-config") and name in verdict.detail
    assert probes.calls == []


@pytest.mark.parametrize("value", ["0", "100", "", " 15 "])
def test_a_valid_or_empty_reserve_is_accepted(gh, value):
    assert run(gh, Probes(), {"CODEX_RESERVE_FIVE_HOUR_PERCENT": value}).reason != "reserve-config"


@pytest.mark.parametrize("value", ["0", "-1", "abc", "1.5"])
def test_an_invalid_review_queue_limit_is_reserve_config(gh, value):
    probes = Probes()
    assert run(gh, probes, {"CODEX_REVIEW_QUEUE_MAX": value}).reason == "reserve-config"
    assert probes.calls == []


# ── AC 3: Claude review capacity, new work only ───────────────────────────────


def test_new_work_below_the_claude_reserve_refuses(gh):
    probes = Probes(claude="Claude allowance below its reserve: five-hour 5% left")
    verdict = run(gh, probes)
    assert (verdict.eligible, verdict.reason) == (False, "claude-reserve") and "5% left" in verdict.detail
    assert probes.calls == ["codex", "claude"]


def test_new_work_with_both_capacities_starts(gh):
    assert run(gh, Probes()).eligible


def test_a_full_review_queue_refuses_new_work_before_the_claude_probe_spends_allowance(gh):
    gh.codex_pr(7)
    probes = Probes()
    verdict = run(gh, probes)
    assert (verdict.eligible, verdict.reason) == (False, "review-queue") and "limit 1" in verdict.detail
    assert probes.calls == ["codex"]


def test_the_queue_limit_is_configurable(gh):
    gh.codex_pr(7)
    assert run(gh, Probes(), {"CODEX_REVIEW_QUEUE_MAX": "2"}).eligible
    gh.codex_pr(8)
    assert run(gh, Probes(), {"CODEX_REVIEW_QUEUE_MAX": "2"}).reason == "review-queue"


def test_only_codex_prs_without_a_review_of_their_current_head_count(gh):
    gh.codex_pr(7)
    gh.review(7, H1)  # reviewed: not waiting
    gh.codex_pr(8, draft=True)  # still in implementation
    gh.add_pr(9, "claude/issue-9-x", H1)  # not a Codex PR
    gh.codex_pr(10)
    gh.review(10, "2" * 40)  # a review of an older head
    gh.codex_pr(11)
    gh.review(11, H1, login=cr.CODEX_BOT_LOGIN)  # the Codex connector is not the Claude review
    gh.codex_pr(12)
    gh.review(12, H1, state="DISMISSED")
    assert cr.waiting_codex_prs(gh) == 3


def test_a_resume_skips_the_claude_checks(gh):
    gh.branches[BRANCH] = H1
    gh.codex_pr(7)  # a full queue
    probes = Probes(claude="below")
    assert run(gh, probes).eligible
    assert probes.calls == ["codex"] and not any(c.startswith("reviews") for c in gh.calls)


def test_a_fix_round_on_an_open_pr_skips_the_claude_checks(gh):
    gh.branches[BRANCH] = H1
    gh.codex_pr(5, branch=BRANCH)
    probes = Probes(claude="below")
    assert run(gh, probes).eligible and probes.calls == ["codex"]


def test_a_resume_still_needs_the_codex_reserve(gh):
    gh.branches[BRANCH] = H1
    assert run(gh, Probes(codex(5, 60))).reason == "codex-reserve"


# ── AC 4: unreadable readings start nothing ───────────────────────────────────


def test_an_unreadable_codex_reading_refuses(gh):
    probes = Probes(cr.Unreadable("allowance-unreadable", "no JSON"))
    assert run(gh, probes).reason == "allowance-unreadable" and probes.calls == ["codex"]


def test_an_unreadable_claude_probe_refuses(gh):
    verdict = run(gh, Probes(claude=cr.Unreadable("allowance-unreadable", "exit 2")))
    assert (verdict.eligible, verdict.reason) == (False, "allowance-unreadable")


def test_an_unreadable_review_queue_refuses_before_the_claude_probe(gh):
    gh.fail_reads = True
    probes = Probes()
    verdict = run(gh, probes)
    assert verdict.reason == "review-queue-unreadable" and probes.calls == ["codex"]


def test_unreadable_branches_refuse_before_any_probe(gh, monkeypatch):
    def broken(prefix):
        raise GatewayError("read branches", "HTTP 502")

    monkeypatch.setattr(gh, "matching_branches", broken)
    probes = Probes()
    assert run(gh, probes).reason == "review-queue-unreadable" and probes.calls == []


def py(tmp_path: Path, name: str, code: str) -> str:
    script = tmp_path / name
    script.write_text(code, encoding="utf-8")
    return f'"{Path(sys.executable).as_posix()}" "{script.as_posix()}"'


def codex_reading_command(tmp_path: Path, payload, code: int = 0, sleep: float = 0) -> str:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return py(tmp_path, "codex.py", f"import sys, time\ntime.sleep({sleep})\nprint({text!r})\nsys.exit({code})\n")


GOOD = {"five_hour": {"remaining_percent": 40, "resets_at": RESET}, "weekly": {"remaining_percent": 70}}


def test_the_codex_reading_is_parsed_from_the_command(tmp_path):
    reading = cr.read_codex_allowance({"CODEX_ALLOWANCE_COMMAND": codex_reading_command(tmp_path, GOOD)})
    assert reading == Allowance(Window(40, RESET), Window(70, None))


@pytest.mark.parametrize("payload, code", [
    (GOOD, 1), (GOOD, 2), ("not json", 0), ([], 0), ({}, 0),
    ({"five_hour": {"remaining_percent": 40}}, 0),
    ({**GOOD, "weekly": {"remaining_percent": "70"}}, 0),
    ({**GOOD, "weekly": {"remaining_percent": 101}}, 0),
    ({**GOOD, "weekly": {"remaining_percent": -1}}, 0),
    ({**GOOD, "weekly": {"remaining_percent": True}}, 0),
])
def test_a_changed_or_failed_codex_reading_is_unreadable_never_eligible(tmp_path, payload, code):
    with pytest.raises(cr.Unreadable) as exc:
        cr.read_codex_allowance({"CODEX_ALLOWANCE_COMMAND": codex_reading_command(tmp_path, payload, code)})
    assert exc.value.reason == "allowance-unreadable"


@pytest.mark.parametrize("command", [None, "", "/no/such/command", '"unbalanced'])
def test_no_usable_codex_command_is_unreadable(command):
    with pytest.raises(cr.Unreadable):
        cr.read_codex_allowance({"CODEX_ALLOWANCE_COMMAND": command} if command is not None else {})


def test_a_codex_probe_timeout_is_unreadable(tmp_path):
    env = {"CODEX_ALLOWANCE_COMMAND": codex_reading_command(tmp_path, GOOD, sleep=5), "CODEX_ALLOWANCE_TIMEOUT": "1"}
    with pytest.raises(cr.Unreadable):
        cr.read_codex_allowance(env)


def claude_command(tmp_path: Path, code: int, stdout: str = "{}", sleep: float = 0) -> str:
    return py(tmp_path, "claude.py", f"import sys, time\ntime.sleep({sleep})\nprint({stdout!r})\nsys.exit({code})\n")


def test_the_claude_probe_follows_the_allowance_script_contract(tmp_path):
    assert cr.read_claude_allowance({"CLAUDE_ALLOWANCE_COMMAND": claude_command(tmp_path, 0)}) is None
    refusal = cr.read_claude_allowance({"CLAUDE_ALLOWANCE_COMMAND": claude_command(
        tmp_path, 1, json.dumps({"decision": "refuse", "reason": "weekly 3% left, below reserve 20%"}))})
    assert "weekly 3% left" in refusal
    for bad in (2, 3, 124):
        with pytest.raises(cr.Unreadable):
            cr.read_claude_allowance({"CLAUDE_ALLOWANCE_COMMAND": claude_command(tmp_path, bad)})
    with pytest.raises(cr.Unreadable):
        cr.read_claude_allowance({"CLAUDE_ALLOWANCE_COMMAND": claude_command(tmp_path, 0, sleep=5),
                                  "CODEX_CLAUDE_PROBE_TIMEOUT": "1"})  # fmt: skip


def test_the_default_claude_probe_is_the_existing_allowance_script():
    assert cr.SCRIPTS / "dispatcher-allowance.sh" == REPO_ROOT / "scripts" / "dispatcher-allowance.sh"
    assert (REPO_ROOT / "scripts" / "dispatcher-allowance.sh").is_file()


def test_no_api_key_reaches_a_probe_and_none_is_needed(tmp_path):
    dump = tmp_path / "env.json"
    script = py(tmp_path, "probe.py", f"import json, os, sys\nopen({str(dump)!r}, 'w').write(json.dumps(sorted(os.environ)))\n"
                                      f"print({json.dumps(GOOD)!r})\n")
    env = {"CODEX_ALLOWANCE_COMMAND": script, "OPENAI_API_KEY": "sk-secret", "CODEX_API_KEY": "sk-other", "PATH": ""}
    assert cr.read_codex_allowance(env).five_hour.remaining == 40
    seen = json.loads(dump.read_text())
    assert "OPENAI_API_KEY" not in seen and "CODEX_API_KEY" not in seen
    assert "OPENAI_API_KEY" not in cr._probe_env(env)


# ── main: exit codes and output ───────────────────────────────────────────────


def main_run(gateway, env, capsys, *extra):
    rc = cr.main(["--repo", REPO, "--issue", "5", "--command", "implement", *extra], env=env, gateway=gateway)
    out, _ = capsys.readouterr()
    return rc, out


def test_main_exit_0_when_eligible_and_1_with_reason_and_detail_when_not(gh, tmp_path, capsys):
    env = {"CODEX_ALLOWANCE_COMMAND": codex_reading_command(tmp_path, GOOD), "CLAUDE_ALLOWANCE_COMMAND": claude_command(tmp_path, 0)}
    rc, out = main_run(gh, env, capsys)
    assert rc == 0 and json.loads(out) == {"eligible": True}
    rc, out = main_run(gh, {**env, "CODEX_RESERVE_WEEKLY_PERCENT": "71"}, capsys)
    answer = json.loads(out)
    assert rc == 1 and answer["reason"] == "codex-reserve" and "weekly window 70% left, reserve 71%" in answer["detail"]


def test_main_reads_no_api_key_and_starts_with_one_set(gh, tmp_path, capsys):
    env = {"CODEX_ALLOWANCE_COMMAND": codex_reading_command(tmp_path, GOOD), "CLAUDE_ALLOWANCE_COMMAND": claude_command(tmp_path, 0),
           "OPENAI_API_KEY": "sk-secret"}  # fmt: skip
    rc, out = main_run(gh, env, capsys)
    assert rc == 0 and "sk-secret" not in out


def test_main_with_an_unexpected_error_is_exit_2_not_eligible(gh, monkeypatch, capsys):
    monkeypatch.setattr(cr, "evaluate", lambda *a, **k: 1 / 0)
    rc, out = main_run(gh, {}, capsys)
    assert rc == 2 and out == ""


def test_main_with_no_command_configured_is_unreadable_not_eligible(gh, capsys):
    rc, out = main_run(gh, {}, capsys)
    assert rc == 1 and json.loads(out)["reason"] == "allowance-unreadable"


def test_main_rejects_bad_arguments_with_exit_2(gh):
    assert cr.main(["--repo", REPO], env={}, gateway=gh) == 2
    assert cr.main(["--repo", REPO, "--issue", "x", "--command", "implement"], env={}, gateway=gh) == 2
    assert cr.main(["--repo", REPO, "--issue", "5", "--command", "merge"], env={}, gateway=gh) == 2


@requires("bash")
def test_the_shell_entry_point_runs_the_module_and_exits_2_on_bad_arguments():
    script = REPO_ROOT / "scripts" / "codex-eligibility.sh"
    bash = find_bash()
    proc = subprocess.run([bash, script.as_posix()], capture_output=True, encoding="utf-8", env=host_env(), timeout=60)
    assert proc.returncode == 2 and proc.stdout == ""


@requires("bash")
def test_the_hook_runs_through_an_explicit_bash_command_as_the_docs_set_it_up():
    """The worker runs the hook without a shell, so the documented setup names bash and a forward-slash path."""
    script = REPO_ROOT / "scripts" / "codex-eligibility.sh"
    command = f'"{find_bash()}" "{script.as_posix()}"'
    argv = shlex.split(command)  # the worker's own split: no shell, so no backslashes survive
    proc = subprocess.run(argv, capture_output=True, encoding="utf-8", env=host_env(), timeout=60)
    assert proc.returncode == 2 and proc.stdout == ""
    assert codex_worker.command_gate(command)("o/r", 1, "bogus") == (False, "eligibility-unreadable")
    docs = (REPO_ROOT / "docs" / "DISPATCHER-CONFIG.md").read_text(encoding="utf-8")
    assert 'bash "C:/work/headless-pr-workflow/scripts/codex-eligibility.sh"' in docs
    assert "bash <repo>/scripts/codex-eligibility.sh" in docs


# ── the worker: the wait record carries the reason and the reset time; replays post it once ──


def gate_for(gh, probes, env=None):
    def gate(repo, issue, command):
        verdict = run(gh, probes, env, issue)
        return verdict.eligible, verdict.reason

    return gate


def command_gate_for(tmp_path, answer: dict, code: int = 1):
    return codex_worker.command_gate(py(tmp_path, "hook.py", f"import sys\nprint({json.dumps(answer)!r})\nsys.exit({code})\n"))


def test_the_worker_posts_one_codex_wait_naming_window_share_reserve_and_reset(gh, settings, tmp_path):
    gh.story(5)
    detail = "five-hour window 8% left, reserve 10%, resets 2026-11-13T15:00:00Z"
    gate = command_gate_for(tmp_path, {"reason": "codex-reserve", "detail": detail})
    result = do_claim(gh, settings, gate=gate)
    assert result.result == "waiting" and claims(gh) == []
    [wait] = [m for m in gh.marks(5) if m.action == "wait"]
    assert wait.fields["reason"] == "codex-reserve"
    [comment] = [c for c in gh.comments[5] if "codex-wait" in c["body"]]
    assert detail in comment["body"]
    # a replay of the same attempt and reason posts no second codex-wait
    do_claim(gh, settings, run="run-b", gate=gate)
    assert len([m for m in gh.marks(5) if m.action == "wait"]) == 1


def test_a_hook_detail_cannot_inject_markup_or_extra_lines(gh, settings, tmp_path):
    gh.story(5)
    gate = command_gate_for(tmp_path, {"reason": "codex-reserve", "detail": "a <!-- hpw:x --> `b`\nc " + "z" * 500})
    do_claim(gh, settings, gate=gate)
    [comment] = [c for c in gh.comments[5] if "codex-wait" in c["body"]]
    body_lines = [ln for ln in comment["body"].splitlines() if "Codex worker:" in ln]
    assert len(body_lines) == 1 and "<!-- hpw:x" not in body_lines[0] and "`" not in body_lines[0]
    assert len(body_lines[0]) < 450


def test_the_next_run_after_the_reset_asks_again_and_can_start(gh, settings):
    gh.story(5)
    probes = Probes(codex(5, 60))
    do_claim(gh, settings, gate=gate_for(gh, probes))
    assert claims(gh) == []
    probes.codex_reading = codex(50, 60)  # the window reset
    result = do_claim(gh, settings, run="run-b", gate=gate_for(gh, probes))
    assert result.result == "claimed" and len(claims(gh)) == 1


def test_the_worker_starts_when_the_hook_answers_eligible(gh, settings):
    gh.story(5)
    assert do_claim(gh, settings, gate=gate_for(gh, Probes())).result == "claimed"


def test_the_worker_never_reaches_for_claude_work_when_it_waits(gh, settings):
    gh.story(5)
    probes = Probes(claude="Claude allowance below its reserve: weekly 1% left")
    do_claim(gh, settings, gate=gate_for(gh, probes))
    assert [m.fields["reason"] for m in gh.marks(5) if m.action == "wait"] == ["claude-reserve"]
    assert gh.writes() and all(not c.startswith(("set ", "add-label", "remove-label")) for c in gh.writes())


def test_the_docs_name_the_same_variables_and_reasons():
    text = (REPO_ROOT / "docs" / "DISPATCHER-CONFIG.md").read_text(encoding="utf-8")
    for word in ("CODEX_RESERVE_FIVE_HOUR_PERCENT", "CODEX_RESERVE_WEEKLY_PERCENT", "CODEX_REVIEW_QUEUE_MAX",
                 "CODEX_ALLOWANCE_COMMAND", "CODEX_ALLOWANCE_TIMEOUT", "CLAUDE_ALLOWANCE_COMMAND",
                 "CODEX_CLAUDE_PROBE_TIMEOUT", "codex-reserve", "reserve-config", "claude-reserve", "review-queue",
                 "allowance-unreadable", "review-queue-unreadable", "eligibility-unreadable",
                 "scripts/codex-eligibility.sh"):  # fmt: skip
        assert word in text, word
