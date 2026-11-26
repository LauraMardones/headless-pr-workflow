"""The one merge-gate rule: ``hpw dispatcher merge-gates decide|read`` (issue #629).

``decide_merge_gates`` is tested over hand-built gate lines, and against the copies it replaces in #650:
``GateResult`` of scripts/merge-gate-summary, ``GhGateway.merge_gates_pass`` and ``inflight_gates_pass`` of
scripts/lib/in-flight.sh. Divergence (3) of #556 → Decisions (2026-11-16) is named in the tests that cover it:
``merge_step`` follows the in-flight routing (mergeability left to the merge step), ``board_mergeable`` follows
``GateResult`` (Mergeable only with ``mergeable=yes``).
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from headless_pr_workflow.dispatcher import gateway as gateway_module
from headless_pr_workflow.dispatcher.cli import main as dispatcher_main
from headless_pr_workflow.dispatcher.gateway import (GatewayError, GhGateway, decide_merge_gates, parse_gate_line,
                                                     read_merge_gates)
from tests.dispatcher_fakes import HAVE_TOOLS
from tests.test_dispatcher_in_flight_facts import _gates_pass

HEAD = "a" * 40
PASS = {"policy": "pass(present_non_required)", "checks": "absent-ok", "approval": "codex-review", "threads": "none",
        "mergeable": "yes", "head": "aaaaaaa"}  # fmt: skip
MERGEABILITY = ("yes", "fail(conflicting)", "warn(unknown)", "fail(wrong-base)", "fail(draft)", "fail(not-open)",
                "warn(expected-base-unavailable)")  # fmt: skip
FAILING = {
    "checks-fail": {"checks": "fail"},
    "checks-pending": {"checks": "pending"},
    "checks-unavailable": {"checks": "warn(unavailable)"},
    "no-approval": {"approval": "none"},
    "approval-unknown": {"approval": "warn(unknown)"},
    "unresolved-thread": {"threads": "fail(1)"},
    "threads-unavailable": {"threads": "warn(unavailable)"},
    "policy-fail": {"policy": "fail(missing-base-policy)"},
    "policy-warn": {"policy": "warn(unknown-source)"},
    "other-head": {"head": "bbbbbbb"},
    "short-gate-head": {"head": "aaaaaa"},
    "empty-gate-head": {"head": ""},
}


def line(**over: str) -> str:
    v = {**PASS, **over}
    return "Merge gate: " + ", ".join(f"{k}={v[k]}" for k in PASS)


def gate(**over: str) -> dict[str, str]:
    parsed = parse_gate_line(line(**over))
    assert parsed is not None
    return parsed


MERGE_GATE = gateway_module._merge_gate_module()


def gate_result(**over: str) -> Any:
    return MERGE_GATE.GateResult(**{**PASS, **over})


def owner_passes(result: Any, head: str = HEAD) -> bool:
    """``GateResult`` on the PR's current head, as ``GhGateway.merge_gates_pass`` reads it."""

    return bool(result.passed) and head.lower().startswith(result.head.lower())


def gateway_passes(result: Any, monkeypatch: pytest.MonkeyPatch, head: str = HEAD) -> bool:
    with monkeypatch.context() as m:
        m.setattr(gateway_module, "_merge_gate_module", lambda: SimpleNamespace(evaluate=lambda n, repo: result))
        return GhGateway("o/r").merge_gates_pass(900, head)


# ── the rule ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("checks", ["pass", "absent-ok"])
@pytest.mark.parametrize("approval", ["formal", "codex-review", "separate-session"])
def test_629_every_passing_gate_passes_and_shows_mergeable(checks, approval):
    decision = decide_merge_gates(gate(checks=checks, approval=approval), HEAD)
    assert decision == {"outcome": "pass", "mergeable": "yes", "gate_head": "aaaaaaa", "merge_step": True,
                        "board_mergeable": True}  # fmt: skip


@pytest.mark.parametrize("case", sorted(FAILING))
def test_629_each_failing_gate_fails_whatever_the_mergeability(case):
    for mergeable in MERGEABILITY:
        decision = decide_merge_gates(gate(**{**FAILING[case], "mergeable": mergeable}), HEAD)
        assert decision["outcome"] == "fail" and not decision["merge_step"] and not decision["board_mergeable"]
        assert decision["mergeable"] == mergeable


@pytest.mark.parametrize("mergeable", MERGEABILITY)
def test_629_divergence_3_gates_pass_apart_from_mergeability_plus_mergeability(mergeable):
    """#556 → Decisions (3), cases ``not-mergeable`` and ``mergeability-unknown``: routing keeps the PR in the
    merge step for the conflict rule; the board shows Mergeable only with ``mergeable=yes``."""

    decision = decide_merge_gates(gate(mergeable=mergeable), HEAD)
    assert decision["outcome"] == "pass" and decision["merge_step"] is True
    assert decision["mergeable"] == mergeable
    assert decision["board_mergeable"] is (mergeable == "yes")


def test_629_the_gate_head_is_a_prefix_of_the_current_head_in_any_case():
    assert decide_merge_gates(gate(head="AAAAAAA"), HEAD)["outcome"] == "pass"
    assert decide_merge_gates(gate(), HEAD.upper())["outcome"] == "pass"
    assert decide_merge_gates(gate(), "aaaaaaa")["outcome"] == "pass"
    assert decide_merge_gates(gate(), "aaaaab" + "a" * 34)["outcome"] == "fail"


@pytest.mark.parametrize("head", ["", "  "])
def test_629_no_current_head_is_no_pass(head):
    assert decide_merge_gates(gate(), head) == {"outcome": "fail", "mergeable": "yes", "gate_head": "aaaaaaa",
                                                "merge_step": False, "board_mergeable": False}  # fmt: skip


def test_629_unreadable_gates_fail_closed():
    assert decide_merge_gates(None, HEAD) == {"outcome": "unreadable", "mergeable": None, "gate_head": None,
                                              "merge_step": False, "board_mergeable": False}  # fmt: skip


@pytest.mark.parametrize("mergeable", ["warn(unknown)", "warn(expected-base-unavailable)"])
def test_629_unreadable_mergeability_fails_closed_for_the_board(mergeable):
    decision = decide_merge_gates(gate(mergeable=mergeable), HEAD)
    assert decision["board_mergeable"] is False and decision["merge_step"] is True  # the merge step checks again


def test_629_the_rule_takes_a_gate_result_as_well_as_a_parsed_line():
    for over in [{}, {"mergeable": "fail(conflicting)"}, *FAILING.values()]:
        assert decide_merge_gates(gate_result(**over), HEAD) == decide_merge_gates(gate(**over), HEAD)


def test_629_parse_takes_the_first_gate_line_of_the_script_output():
    text = f"note\n{line(mergeable='fail(conflicting)')}\r\n{line()}\n"
    assert parse_gate_line(text) == {**PASS, "mergeable": "fail(conflicting)"}
    assert parse_gate_line("merge-gate-summary: could not read the pull request") is None
    assert parse_gate_line("") is None
    assert parse_gate_line(line().replace("threads=none, ", "")) is None
    assert parse_gate_line(f"Merge gate: broken\n{line()}\n") is None  # only the first gate line counts


@pytest.mark.skipif(not HAVE_TOOLS, reason="needs bash and jq")
def test_629_a_malformed_first_gate_line_is_unreadable_and_no_copy_passes(tmp_path):
    output = f"Merge gate: broken\n{line()}"
    assert decide_merge_gates(parse_gate_line(output), HEAD)["outcome"] == "unreadable"
    assert not _gates_pass(tmp_path, output, 1)


# ── the same decisions as the copies, apart from divergence (3) ─────────────────


@pytest.mark.parametrize("over", [{}, *FAILING.values()], ids=["all-pass", *sorted(FAILING)])
@pytest.mark.parametrize("mergeable", MERGEABILITY)
def test_629_board_mergeable_is_the_owner_and_gateway_decision(over, mergeable, monkeypatch):
    result = gate_result(**{**over, "mergeable": mergeable})
    decision = decide_merge_gates(result, HEAD)
    assert decision["board_mergeable"] is owner_passes(result) is gateway_passes(result, monkeypatch)


def test_629_board_mergeable_matches_the_gateway_for_another_current_head(monkeypatch):
    result = gate_result()
    other = "1234567" + "0" * 33
    assert decide_merge_gates(result, other)["board_mergeable"] is gateway_passes(result, monkeypatch, other) is False


@pytest.mark.skipif(not HAVE_TOOLS, reason="needs bash and jq")
@pytest.mark.parametrize("over", [{}, *FAILING.values()], ids=["all-pass", *sorted(FAILING)])
@pytest.mark.parametrize("mergeable", ["yes", "fail(conflicting)", "warn(unknown)"])
def test_629_merge_step_is_the_in_flight_decision(over, mergeable, tmp_path):
    text = line(**{**over, "mergeable": mergeable})
    decision = decide_merge_gates(parse_gate_line(text), HEAD)
    rc = 0 if decision["board_mergeable"] else 1  # the script's exit code for its own line
    assert decision["merge_step"] is _gates_pass(tmp_path, text, rc)


@pytest.mark.skipif(not HAVE_TOOLS, reason="needs bash and jq")
def test_629_a_script_with_no_gate_line_is_unreadable_and_no_copy_passes(tmp_path):
    output = "merge-gate-summary: could not read the pull request"
    assert decide_merge_gates(parse_gate_line(output), HEAD)["outcome"] == "unreadable"
    assert not _gates_pass(tmp_path, output, 2)


# ── reading the gates now ───────────────────────────────────────────────────────


def test_629_read_evaluates_the_pr_through_the_merge_gate_script(monkeypatch):
    calls = []

    def evaluate(pr, repo):
        calls.append((pr, repo))
        return gate_result(mergeable="fail(conflicting)")

    monkeypatch.setattr(gateway_module, "_merge_gate_module", lambda: SimpleNamespace(evaluate=evaluate))
    decision, error = read_merge_gates("o/r", 900, HEAD)
    assert calls == [(900, "o/r")] and error is None
    assert decision["outcome"] == "pass" and decision["mergeable"] == "fail(conflicting)"


@pytest.mark.parametrize("exc", [ValueError("GitHub did not return a valid current PR head SHA"),
                                 GatewayError("evaluate merge gates", "scripts/merge-gate-summary not found"),
                                 RuntimeError("")])  # fmt: skip
def test_629_a_failed_read_is_unreadable_with_the_reason(exc, monkeypatch):
    def evaluate(pr, repo):
        raise exc

    monkeypatch.setattr(gateway_module, "_merge_gate_module", lambda: SimpleNamespace(evaluate=evaluate))
    decision, error = read_merge_gates("o/r", 900, HEAD)
    assert decision == decide_merge_gates(None, HEAD)
    assert error is not None and error.startswith("evaluate merge gates of PR #900: ")


def test_629_a_failed_read_masks_the_token(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_secretsecretsecret")

    def evaluate(pr, repo):
        raise ValueError("HTTP 401 for ghp_secretsecretsecret")

    monkeypatch.setattr(gateway_module, "_merge_gate_module", lambda: SimpleNamespace(evaluate=evaluate))
    _, error = read_merge_gates("o/r", 900, HEAD)
    assert error is not None and "ghp_secretsecretsecret" not in error


# ── the command ─────────────────────────────────────────────────────────────────


def run(argv: list[str], capsys) -> tuple[int, dict[str, Any]]:
    code = dispatcher_main(argv)
    out = capsys.readouterr().out
    assert out.count("\n") == 1  # one JSON object on one line
    return code, json.loads(out)


def test_629_decide_reads_the_script_output_from_a_file(tmp_path, capsys):
    path = tmp_path / "gate.txt"
    path.write_text(line(mergeable="fail(conflicting)") + "\n", encoding="utf-8")
    code, out = run(["merge-gates", "decide", "--head", HEAD, "--input", str(path)], capsys)
    assert code == 0
    assert out == {"ok": True, "block": "merge-gates", "operation": "decide", "result": "read", "outcome": "pass",
                   "mergeable": "fail(conflicting)", "gate_head": "aaaaaaa", "merge_step": True,
                   "board_mergeable": False, "error": None}  # fmt: skip


def test_629_decide_reads_stdin(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", SimpleNamespace(buffer=SimpleNamespace(read=lambda: line(threads="fail(2)").encode())))
    code, out = run(["merge-gates", "decide", "--head", HEAD], capsys)
    assert code == 0 and out["outcome"] == "fail" and out["merge_step"] is False


@pytest.mark.parametrize("content", [None, "merge-gate-summary: could not read the pull request\n", b"\xff\xfe"])
def test_629_decide_fails_closed_on_an_unreadable_input(content, tmp_path, capsys):
    path = tmp_path / "gate.txt"
    if isinstance(content, bytes):
        path.write_bytes(content)
    elif content is not None:
        path.write_text(content, encoding="utf-8")
    code, out = run(["merge-gates", "decide", "--head", HEAD, "--input", str(path)], capsys)
    assert code == 3 and out["ok"] is False and out["result"] == "unreadable" and out["outcome"] == "unreadable"
    assert out["merge_step"] is False and out["board_mergeable"] is False and out["error"].startswith("read input: ")


def test_629_read_runs_the_rule_over_the_pr(capsys, monkeypatch):
    monkeypatch.setattr(gateway_module, "_merge_gate_module",
                        lambda: SimpleNamespace(evaluate=lambda pr, repo: gate_result()))
    code, out = run(["merge-gates", "read", "--repo", "o/r", "--pr", "900", "--head", HEAD], capsys)
    assert code == 0 and out["operation"] == "read" and out["board_mergeable"] is True and out["error"] is None


def test_629_read_fails_closed_when_the_gates_are_unreadable(capsys, monkeypatch):
    def evaluate(pr, repo):
        raise ValueError("HTTP 502")

    monkeypatch.setattr(gateway_module, "_merge_gate_module", lambda: SimpleNamespace(evaluate=evaluate))
    code, out = run(["merge-gates", "read", "--repo", "o/r", "--pr", "900", "--head", HEAD], capsys)
    assert code == 3 and out["result"] == "unreadable" and out["merge_step"] is False
    assert out["error"] == "evaluate merge gates of PR #900: HTTP 502"


@pytest.mark.parametrize("argv", [
    ["merge-gates", "decide", "--head", "not-a-sha"],
    ["merge-gates", "decide"],
    ["merge-gates", "read", "--repo", "o/r", "--pr", "900", "--head", ""],
    ["merge-gates", "read", "--repo", "bad", "--pr", "900", "--head", HEAD],
    ["merge-gates", "read", "--repo", "o/r", "--pr", "0", "--head", HEAD],
    ["merge-gates", "read", "--repo", "o/r", "--head", HEAD],
    ["merge-gates"],
])
def test_629_invalid_arguments_exit_2(argv, capsys):
    code, out = run(argv, capsys)
    assert code == 2 and out["result"] == "invalid" and out["block"] == "merge-gates"


def test_629_the_command_never_writes(capsys, monkeypatch):
    def no_gateway(repo):
        raise AssertionError("merge-gates needs no gateway")

    monkeypatch.setattr(gateway_module, "_merge_gate_module",
                        lambda: SimpleNamespace(evaluate=lambda pr, repo: gate_result()))
    code = dispatcher_main(["merge-gates", "read", "--repo", "o/r", "--pr", "900", "--head", HEAD, "--dry-run"],
                           gateway_factory=no_gateway)  # fmt: skip
    capsys.readouterr()
    assert code == 0
