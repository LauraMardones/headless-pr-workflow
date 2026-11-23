"""The failure stop block, `hpw dispatcher failure-stop` (issue #503)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from headless_pr_workflow.dispatcher import failure_stop
from headless_pr_workflow.dispatcher.cli import main as dispatcher_main
from headless_pr_workflow.dispatcher.gateway import GatewayError
from headless_pr_workflow.dispatcher.write import Settings

BOT = "dispatcher-bot"
PO = "LauraMardones"
NOW = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)
RUN = "https://github.com/o/r/actions/runs/{}"


def at(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeGateway:
    def __init__(self) -> None:
        self.issues: dict[int, dict[str, Any]] = {}
        self.comments: dict[int, list[dict[str, Any]]] = {}
        self.login: str | Exception = BOT
        self.fail_comments: set[int] = set()
        self.fail_issues = False
        self.reads: list[int] = []

    def viewer_login(self) -> str:
        if isinstance(self.login, Exception):
            raise self.login
        return self.login

    def list_open_issues(self) -> list[dict[str, Any]]:
        if self.fail_issues:
            raise GatewayError("read open issues", "HTTP 502")
        return [{**issue, "comments": len(self.comments.get(n, []))} for n, issue in self.issues.items()]

    def list_comments(self, number: int) -> list[dict[str, Any]]:
        self.reads.append(number)
        if number in self.fail_comments:
            raise GatewayError(f"read comments on #{number}", "HTTP 502")
        return list(self.comments.get(number, []))

    def post_comment(self, number: int, body: str) -> dict[str, Any]:  # pragma: no cover - never called
        raise AssertionError("the failure-stop block writes nothing")

    def comment(self, number: int, body: str, hours_ago: float, login: str = BOT) -> None:
        self.issues.setdefault(number, {"number": number, "title": f"Story {number}"})
        self.comments.setdefault(number, []).append(
            {"user": {"login": login}, "created_at": at(hours_ago), "body": body,
             "html_url": f"https://github.com/o/r/issues/{number}#c{len(self.comments.get(number, []))}"})

    def failure(self, number: int, hours_ago: float, step: str = "implement", login: str = BOT, stop: bool = False,
                threshold: int = 3) -> None:
        body = "## Blocked Declaration\nType: external\n\n" + failure_stop.failed_marker(
            number, step, RUN.format(int(hours_ago * 100)))
        if stop:
            body += "\n" + failure_stop.stop_marker(number, threshold)
        self.comment(number, body, hours_ago, login)

    def unblock(self, number: int, hours_ago: float, body: str = "/unblock", login: str = PO) -> None:
        self.comment(number, body, hours_ago, login)


def env(threshold: str | None = "3") -> dict[str, str]:
    values = {"DECISION_BLOCKER_PO_LOGIN": PO}
    if threshold is not None:
        values["FAILED_SESSIONS_STOP_THRESHOLD"] = threshold
    return values


def settings() -> Settings:
    return Settings(po_login=PO, sleep=lambda _s: None)


def check(gw: FakeGateway, threshold: str | None = "3") -> dict[str, Any]:
    return failure_stop.check(gw, settings=settings(), env=env(threshold), now=NOW).to_dict()  # type: ignore[arg-type]


def record(gw: FakeGateway, issue: int = 7, step: str = "implement", threshold: str | None = "3") -> dict[str, Any]:
    result = failure_stop.record(gw, issue, step, RUN.format(999), settings=settings(), env=env(threshold),  # type: ignore[arg-type]
                                 now=NOW)
    return {**result.to_dict(), "exit": result.exit_code}


# ── AC 1: the record ─────────────────────────────────────────────────────────


def test_ac1_a_failure_below_the_threshold_gets_only_the_session_failed_marker():
    gw = FakeGateway()
    gw.failure(5, 2)

    out = record(gw, 7, "review")

    assert out["result"] == "recorded" and out["exit"] == 0 and out["reached"] is False
    assert out["marker"] == ("<!-- hpw:dispatcher action=session-failed issue=7 step=review "
                             "run_url=https://github.com/o/r/actions/runs/999 -->")
    assert out["count"] == 2 and out["window_hours"] == 24
    assert [(s["issue"], s["step"]) for s in out["sessions"]] == [(5, "implement"), (7, "review")]


@pytest.mark.parametrize("body", [
    "## Paused at a provider usage limit\n\nNo PO action is needed.",  # a provider usage-limit stop
    "## Usage limit\n\n/review was not started for #5",                # a refused start
    "Dispatcher: /review completed on PR #9 at head abc1234\n\n<!-- hpw:dispatcher action=review-attempt pr=9 -->",
    "## Blocked Declaration\nType: decision\nDeclared by: Claude Code (implementer)",  # an F1.1 session blocker
])
def test_ac1_comments_without_the_session_failed_marker_count_nothing(body):
    gw = FakeGateway()
    gw.comment(5, body, 1)

    assert check(gw)["count"] == 0


# ── AC 2: the stop and the window ────────────────────────────────────────────


def test_ac2_the_nth_failure_reaches_the_threshold_and_carries_the_stop_marker():
    gw = FakeGateway()
    gw.failure(5, 20)
    gw.failure(6, 10)

    out = record(gw, 7)

    assert out["reached"] is True and out["count"] == 3
    assert out["marker"].splitlines()[1] == ("<!-- hpw:dispatcher action=failure-stop issue=7 threshold=3 "
                                             "window_hours=24 -->")
    assert [s["issue"] for s in out["sessions"]] == [5, 6, 7]
    assert out["sessions"][0]["title"] == "Story 5" and out["sessions"][0]["run_url"] == RUN.format(2000)


def test_ac2_window_boundary_23h59_counts_24h01_does_not():
    inside = FakeGateway()
    inside.failure(5, 23 + 59 / 60)
    inside.failure(6, 1)
    assert record(inside, 7)["reached"] is True

    outside = FakeGateway()
    outside.failure(5, 24 + 1 / 60)
    outside.failure(6, 1)
    out = record(outside, 7)
    assert out["reached"] is False and out["count"] == 2


def test_ac2_a_holding_stop_records_no_second_stop():
    gw = FakeGateway()
    gw.failure(5, 3)
    gw.failure(6, 2)
    gw.failure(7, 1, stop=True)

    out = record(gw, 8)

    assert out["reached"] is False and out["holds"] is True and "failure-stop" not in out["marker"]


def test_ac2_a_count_above_the_threshold_without_a_stop_still_reaches_it():
    # A failure recorded while the comments could not be read carries no stop marker.
    gw = FakeGateway()
    for number in (4, 5, 6):
        gw.failure(number, 5 - number)

    assert record(gw, 7)["reached"] is True


# ── AC 3, AC 4: the hold, read from comments only ────────────────────────────


def test_ac3_ac4_a_stop_marker_holds_from_the_comments_alone():
    gw = FakeGateway()
    gw.failure(5, 3)
    gw.failure(6, 2)
    gw.failure(7, 1, stop=True)

    out = check(gw)
    result = failure_stop.check(gw, settings=settings(), env=env(), now=NOW)  # type: ignore[arg-type]

    assert out["result"] == "holds" and result.exit_code == 1 and out["ok"] is False
    assert out["stop_issue"] == 7 and out["stop_url"].startswith("https://github.com/o/r/issues/7")
    assert out["count"] == 3 and [s["issue"] for s in out["sessions"]] == [5, 6, 7]


def test_ac3_a_stop_older_than_the_window_still_holds():
    gw = FakeGateway()
    gw.failure(7, 72, stop=True)

    assert check(gw)["result"] == "holds"


def test_a_clear_check_reports_the_count_in_the_window():
    gw = FakeGateway()
    gw.failure(5, 1)

    out = check(gw)

    assert out["result"] == "clear" and out["ok"] is True and out["count"] == 1 and out["threshold"] == 3


def test_an_issue_with_no_comment_is_not_read():
    gw = FakeGateway()
    gw.issues[9] = {"number": 9, "title": "quiet"}
    gw.failure(5, 1)

    check(gw)

    assert gw.reads == [5]


# ── AC 5: the PO's /unblock restarts and resets ──────────────────────────────


def test_ac5_a_po_unblock_lifts_the_stop_and_earlier_failures_no_longer_count():
    gw = FakeGateway()
    gw.failure(5, 4)
    gw.failure(6, 3)
    gw.failure(7, 2, stop=True)
    gw.unblock(7, 1)

    assert check(gw)["result"] == "clear"
    assert check(gw)["count"] == 0
    assert record(gw, 8)["reached"] is False


def test_ac5_failures_after_the_unblock_count_toward_the_next_stop():
    gw = FakeGateway()
    gw.failure(7, 5, stop=True)
    gw.unblock(7, 4)
    gw.failure(5, 3)
    gw.failure(6, 2)

    out = record(gw, 8)

    assert out["reached"] is True and [s["issue"] for s in out["sessions"]] == [5, 6, 8]


@pytest.mark.parametrize("login,body", [
    ("someone-else", "/unblock"),         # not the PO
    (PO, "please /unblock"),               # not a standalone line
    (PO, "/unblock now"),
])
def test_ac5_an_unblock_by_another_author_or_not_standalone_does_not_lift(login, body):
    gw = FakeGateway()
    gw.failure(7, 2, stop=True)
    gw.unblock(7, 1, body=body, login=login)

    assert check(gw)["result"] == "holds"


def test_ac5_an_unblock_on_another_issue_does_not_lift():
    gw = FakeGateway()
    gw.failure(7, 2, stop=True)
    gw.failure(5, 3)
    gw.unblock(5, 1)

    assert check(gw)["result"] == "holds"


def test_ac5_an_unblock_before_the_stop_does_not_lift():
    gw = FakeGateway()
    gw.unblock(7, 3)
    gw.failure(7, 2, stop=True)

    assert check(gw)["result"] == "holds"


def test_a_standalone_unblock_line_inside_a_longer_po_reply_lifts():
    gw = FakeGateway()
    gw.failure(7, 2, stop=True)
    gw.unblock(7, 1, body="Token rotated.\n/unblock")

    assert check(gw)["result"] == "clear"


def test_a_closed_stop_issue_drops_out_of_the_read():
    gw = FakeGateway()
    gw.failure(7, 2, stop=True)
    del gw.issues[7]

    assert check(gw)["result"] == "clear"


# ── AC 6: the threshold setting ──────────────────────────────────────────────


@pytest.mark.parametrize("value", [None, "", "abc", "0", "-1", "2.5", "3 ", " 3", "+3", "3\n"])
def test_ac6_unset_empty_or_invalid_threshold_is_invalid_exit_2(value):
    gw = FakeGateway()
    result = failure_stop.check(gw, settings=settings(), env=env(value), now=NOW)  # type: ignore[arg-type]

    assert result.result == "invalid" and result.exit_code == 2
    assert "FAILED_SESSIONS_STOP_THRESHOLD" in (result.error or "")
    assert gw.reads == []


def test_ac6_invalid_threshold_on_record_still_returns_the_session_failed_marker():
    out = record(FakeGateway(), threshold="abc")

    assert out["result"] == "invalid" and out["exit"] == 2 and "action=session-failed" in out["marker"]


def test_ac6_a_changed_threshold_takes_effect_on_the_next_read():
    gw = FakeGateway()
    gw.failure(5, 2)
    gw.failure(6, 1)

    assert record(gw, 7, threshold="4")["reached"] is False
    assert record(gw, 7, threshold="3")["reached"] is True
    assert record(gw, 7, threshold="1")["reached"] is True


# ── AC 7: own account only, fail closed ──────────────────────────────────────


def test_ac7_markers_from_another_author_neither_count_nor_stop():
    gw = FakeGateway()
    gw.failure(5, 3, login="intruder")
    gw.failure(6, 2, login="intruder")
    gw.failure(7, 1, login="intruder", stop=True)

    out = check(gw)

    assert out["result"] == "clear" and out["count"] == 0
    assert record(gw, 8)["reached"] is False


@pytest.mark.parametrize("break_it", ["login", "empty-login", "issues", "comments"])
def test_ac7_an_unreadable_list_or_login_fails_closed(break_it):
    gw = FakeGateway()
    gw.failure(5, 1)
    if break_it == "login":
        gw.login = GatewayError("resolve login", "HTTP 401")
    elif break_it == "empty-login":
        gw.login = ""
    elif break_it == "issues":
        gw.fail_issues = True
    else:
        gw.fail_comments.add(5)

    result = failure_stop.check(gw, settings=settings(), env=env(), now=NOW)  # type: ignore[arg-type]

    assert result.result == "unreadable" and result.exit_code == 3 and result.error
    out = record(gw)
    assert out["result"] == "unreadable" and out["exit"] == 3 and "action=session-failed" in out["marker"]


# ── CLI ──────────────────────────────────────────────────────────────────────


def run_cli(gw: FakeGateway, capsys, monkeypatch, *argv: str, threshold: str = "3") -> tuple[int, dict[str, Any]]:
    monkeypatch.setenv("FAILED_SESSIONS_STOP_THRESHOLD", threshold)
    monkeypatch.setenv("DECISION_BLOCKER_PO_LOGIN", PO)
    monkeypatch.setenv("FAILURE_STOP_NOW", NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))  # the CLI reads the real clock otherwise
    rc = dispatcher_main(["failure-stop", *argv, "--repo", "o/r"], gateway_factory=lambda _repo: gw,  # type: ignore[arg-type, return-value]
                         settings=settings())
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    return rc, json.loads(lines[0])


def test_cli_check_prints_one_json_line_with_the_contract_fields(capsys, monkeypatch):
    gw = FakeGateway()
    gw.failure(5, 1)

    rc, out = run_cli(gw, capsys, monkeypatch, "check", "--json")

    assert rc == 0 and out["block"] == "failure-stop" and out["result"] == "clear"
    assert {"result", "threshold", "count", "window_hours", "sessions"} <= out.keys()
    assert set(out["sessions"][0]) >= {"issue", "step", "run_url", "at"}


def test_cli_check_holds_exits_1(capsys, monkeypatch):
    gw = FakeGateway()
    gw.failure(7, 1, stop=True)

    assert run_cli(gw, capsys, monkeypatch, "check")[0] == 1


def test_cli_record_prints_the_marker(capsys, monkeypatch):
    rc, out = run_cli(FakeGateway(), capsys, monkeypatch, "record", "--issue", "7", "--step", "refine",
                      "--run-url", RUN.format(1), threshold="1")

    assert rc == 0 and out["reached"] is True and "action=failure-stop issue=7" in out["marker"]


@pytest.mark.parametrize("argv", [
    ["record", "--issue", "7", "--step", "merge", "--run-url", RUN.format(1)],
    ["record", "--issue", "7", "--step", "cleanup", "--run-url", RUN.format(1)],
    ["record", "--issue", "7", "--step", "implement", "--run-url", "not a url"],
    ["record", "--issue", "0", "--step", "implement", "--run-url", RUN.format(1)],
    ["record", "--step", "implement", "--run-url", RUN.format(1)],
])
def test_cli_invalid_record_arguments_exit_2(capsys, monkeypatch, argv):
    rc, out = run_cli(FakeGateway(), capsys, monkeypatch, *argv)

    assert rc == 2 and out["result"] == "invalid"


@pytest.mark.parametrize("operation", [["check"], ["record", "--issue", "7", "--step", "implement",
                                                   "--run-url", RUN.format(1)]])
def test_cli_dry_run_reads_and_records_nothing(capsys, monkeypatch, operation):
    gw = FakeGateway()
    gw.failure(7, 1, stop=True)

    rc, out = run_cli(gw, capsys, monkeypatch, *operation, "--dry-run")

    assert rc == 0 and out["result"] == "dry-run" and gw.reads == []
    assert out.get("marker", "") == ""
