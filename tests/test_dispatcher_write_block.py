"""Block 1, the verified GitHub write: ``hpw dispatcher write`` (issue #432, ADR-014).

Every path runs against a Python fake gateway: no network and no real ``gh``.
A few tests at the end run the real ``GhGateway`` against a Python stand-in for
``gh``, so the command lines and the parsing are covered too.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.script_tools import BASH

from headless_pr_workflow.dispatcher import cli
from headless_pr_workflow.dispatcher.gateway import BoardItem, GatewayError, GhGateway, parse_json_stream
from headless_pr_workflow.dispatcher.write import (
    Settings,
    evidence_failed_marker,
    evidence_gave_up_marker,
    evidence_pending_comment,
    retry_pending,
    write_board_status,
    write_comment,
    write_label,
)

BOT = "hpw-bot"
PO = "LauraMardones"
TOKEN = "ghp_secretTOKENvalue123"
OPTIONS = {"Ready for implementation": "OPT_READY", "In implementation": "OPT_IMPL", "In review": "OPT_REVIEW",
           "In merge": "OPT_MERGE", "Done": "OPT_DONE"}


class FakeGateway:
    """In-memory GitHub. ``post_modes`` decides each comment write in turn:

    ``ok`` stores it; ``fail`` raises and stores nothing; ``lost`` stores it and
    raises (the response was lost); ``missing`` answers success and stores
    nothing; ``other-body`` / ``other-author`` store a different comment.
    Bodies in ``fail_bodies`` always fail.
    """

    def __init__(self) -> None:
        self.comments: dict[int, list[dict]] = {}
        self.post_modes: list[str] = []
        self.fail_bodies: set[str] = set()
        self.list_failures = 0
        self.statuses: dict[int, str | None] = {}
        self.status_modes: list[str] = []
        self.options = dict(OPTIONS)
        self.labels: dict[int, set[str]] = {}
        self.repo_labels: set[str] = set()
        self.label_modes: list[str] = []
        self.calls: list[str] = []
        self.clock = 0
        self.error_text = "HTTP 500"

    # helpers
    def add(self, number: int, body: str, login: str = BOT) -> None:
        self.clock += 1
        self.comments.setdefault(number, []).append({
            "id": 1000 + self.clock, "user": {"login": login}, "body": body,
            "created_at": f"2026-10-13T10:00:{self.clock:02d}Z", "html_url": f"https://x/{number}#c{self.clock}",
        })

    def posts(self) -> list[str]:
        return [c for c in self.calls if c.startswith("post ")]

    # Gateway
    def viewer_login(self) -> str:
        self.calls.append("login")
        return BOT

    def list_comments(self, number: int) -> list[dict]:
        self.calls.append(f"list {number}")
        if self.list_failures:
            self.list_failures -= 1
            raise GatewayError(f"read comments on #{number}", self.error_text)
        return [dict(c) for c in self.comments.get(number, [])]

    def post_comment(self, number: int, body: str) -> dict:
        self.calls.append(f"post {number}")
        mode = self.post_modes.pop(0) if self.post_modes else "ok"
        if body in self.fail_bodies or mode == "fail":
            raise GatewayError(f"write comment on #{number}", self.error_text)
        if mode == "missing":
            return {}
        if mode == "other-body":
            self.add(number, body + " (edited)")
            return {}
        if mode == "other-author":
            self.add(number, body, login="someone-else")
            return {}
        self.add(number, body)
        if mode == "lost":
            raise GatewayError(f"write comment on #{number}", "connection reset")
        return dict(self.comments[number][-1])

    def board_item(self, issue: int) -> BoardItem:
        self.calls.append(f"board {issue}")
        if issue not in self.statuses:
            return BoardItem(project_id="P", field_id="F", options=self.options)
        return BoardItem(project_id="P", field_id="F", options=self.options, item_id=f"ITEM_{issue}",
                         status=self.statuses[issue])

    def set_board_status(self, item: BoardItem, option_id: str) -> None:
        self.calls.append(f"set {item.item_id} {option_id}")
        mode = self.status_modes.pop(0) if self.status_modes else "ok"
        if mode == "fail":
            raise GatewayError("write status", self.error_text)
        if mode == "noop":
            return
        name = next(n for n, o in self.options.items() if o == option_id)
        self.statuses[int(item.item_id.split("_")[1])] = name

    def issue_labels(self, number: int) -> list[str]:
        self.calls.append(f"labels {number}")
        return sorted(self.labels.get(number, set()))

    def add_label(self, number: int, name: str) -> None:
        self.calls.append(f"add-label {number} {name}")
        mode = self.label_modes.pop(0) if self.label_modes else "ok"
        if mode == "fail":
            raise GatewayError(f"add label to #{number}", self.error_text)
        if name not in self.repo_labels:
            raise GatewayError(f"add label to #{number}", "HTTP 404: Not Found", not_found=True)
        if mode != "noop":
            self.labels.setdefault(number, set()).add(name)

    def remove_label(self, number: int, name: str) -> None:
        self.calls.append(f"remove-label {number} {name}")
        mode = self.label_modes.pop(0) if self.label_modes else "ok"
        if mode == "fail":
            raise GatewayError(f"remove label from #{number}", self.error_text)
        if mode != "noop":
            self.labels.setdefault(number, set()).discard(name)

    def create_label(self, name: str, color: str, description: str) -> None:
        self.calls.append(f"create-label {name}")
        self.repo_labels.add(name)


@pytest.fixture
def gw() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
def settings() -> Settings:
    return Settings(retry_delay=0, evidence_retry_max=3, po_login=PO, sleep=lambda _s: None)


def run_cli(gateway: FakeGateway, settings: Settings, capsys, *argv: str) -> tuple[int, dict, str]:
    rc = cli.main(list(argv), gateway_factory=lambda repo: gateway, settings=settings)
    out, err = capsys.readouterr()
    lines = out.splitlines()
    assert len(lines) == 1, out  # exactly one JSON object on one line
    return rc, json.loads(lines[0]), err


def body_file(tmp_path: Path, text: str) -> str:
    path = tmp_path / "body.txt"
    path.write_bytes(text.encode("utf-8"))
    return str(path)


# ── AC 1: the contract ────────────────────────────────────────────────────────


def test_comment_written_prints_one_json_line_with_the_contract_fields(gw, settings, capsys, tmp_path):
    rc, out, _ = run_cli(gw, settings, capsys, "write", "comment", "--repo", "o/r", "--number", "7",
                         "--body-file", body_file(tmp_path, "hello"))
    assert rc == 0
    assert out["ok"] is True and out["block"] == "write" and out["operation"] == "comment"
    assert out["result"] == "written" and out["error"] is None and out["attempts"] == 1
    assert out["created_at"] == gw.comments[7][0]["created_at"] and out["url"] == gw.comments[7][0]["html_url"]
    assert len(gw.posts()) == 1


def test_board_status_written_reports_before_and_after(gw, settings, capsys):
    gw.statuses[5] = "Ready for implementation"
    rc, out, _ = run_cli(gw, settings, capsys, "write", "board-status", "--repo", "o/r", "--issue", "5",
                         "--status", "In implementation")
    assert rc == 0 and out["result"] == "written"
    assert out["before"] == "Ready for implementation" and out["after"] == "In implementation"
    assert gw.statuses[5] == "In implementation"


@pytest.mark.parametrize("argv", [
    ["write", "comment", "--repo", "o/r", "--number", "7", "--body-file", "BODY", "--dry-run"],
    ["write", "board-status", "--repo", "o/r", "--issue", "7", "--status", "Done", "--dry-run"],
    ["write", "label", "--repo", "o/r", "--number", "7", "--add", "x", "--dry-run"],
    ["write", "label", "--repo", "o/r", "--number", "7", "--remove", "x", "--dry-run"],
])
def test_dry_run_writes_nothing_and_exits_0(gw, settings, capsys, tmp_path, argv):
    argv = [body_file(tmp_path, "x") if a == "BODY" else a for a in argv]
    rc, out, _ = run_cli(gw, settings, capsys, *argv)
    assert rc == 0 and out["result"] == "dry-run" and out["ok"] is True
    assert gw.calls == []  # not even a read


@pytest.mark.parametrize("argv", [
    [],
    ["write"],
    ["write", "comment", "--repo", "o/r", "--number", "7"],
    ["write", "comment", "--repo", "bad", "--number", "7", "--body-file", "x"],
    ["write", "comment", "--repo", "o/r", "--number", "0", "--body-file", "x"],
    ["write", "comment", "--repo", "o/r", "--number", "7", "--body-file", "x", "--evidence-item", "merge-confirmation"],
    ["write", "comment", "--repo", "o/r", "--number", "7", "--body-file", "x", "--attempts", "0"],
    ["write", "board-status", "--repo", "o/r", "--issue", "7", "--status", " "],
    ["write", "label", "--repo", "o/r", "--number", "7"],
    ["write", "label", "--repo", "o/r", "--number", "7", "--add", "x", "--remove", "x"],
    ["write", "retry-pending", "--repo", "o/r", "--target", "7", "--item", "a b", "--ref", "r", "--story", "1"],
    ["write", "unknown", "--repo", "o/r"],
    ["marker", "holds", "--repo", "o/r"],
])
def test_invalid_arguments_exit_2_with_one_json_line(gw, settings, capsys, argv):
    rc, out, _ = run_cli(gw, settings, capsys, *argv)
    assert rc == 2 and out["ok"] is False and out["result"] == "invalid" and out["error"].startswith("arguments:")
    assert gw.calls == []


def test_empty_body_file_is_invalid(gw, settings, capsys, tmp_path):
    rc, out, _ = run_cli(gw, settings, capsys, "write", "comment", "--repo", "o/r", "--number", "7",
                         "--body-file", body_file(tmp_path, "  \n"))
    assert rc == 2 and out["result"] == "invalid"


# ── AC 2: comment ─────────────────────────────────────────────────────────────


def test_comment_write_fails_on_every_attempt(gw, settings):
    gw.post_modes = ["fail"] * 5
    result = write_comment(gw, 7, "hello", attempts=3, settings=settings)
    assert result.exit_code == 1 and result.result == "failed" and result.fields["attempts"] == 3
    assert result.error.startswith("write comment on #7")
    assert len(gw.posts()) == 3 and 7 not in gw.comments


@pytest.mark.parametrize("mode", ["missing", "other-body", "other-author"])
def test_comment_read_back_mismatch_is_retried_then_fails(gw, settings, mode):
    gw.post_modes = [mode] * 3
    result = write_comment(gw, 7, "hello", settings=settings)
    assert result.exit_code == 1 and result.result == "failed"
    assert result.error.startswith("read back: no comment of hpw-bot with the sent text on #7")
    assert len(gw.posts()) == 3


def test_comment_read_back_mismatch_then_match_is_written(gw, settings):
    gw.post_modes = ["missing", "ok"]
    result = write_comment(gw, 7, "hello", settings=settings)
    assert result.result == "written" and result.fields["attempts"] == 2


def test_lost_response_is_found_and_not_posted_twice(gw, settings):
    gw.post_modes = ["lost"]
    result = write_comment(gw, 7, "hello", settings=settings)
    assert result.exit_code == 0 and result.result == "written"
    assert len(gw.posts()) == 1 and [c["body"] for c in gw.comments[7]] == ["hello"]


def test_identical_older_comment_does_not_count_as_this_write(gw, settings):
    gw.add(7, "hello")  # posted by an earlier call
    gw.post_modes = ["missing", "missing", "missing"]
    result = write_comment(gw, 7, "hello", settings=settings)
    assert result.result == "failed" and len(gw.posts()) == 3


def test_unreadable_comments_before_the_first_post_are_retried(gw, settings):
    gw.list_failures = 1
    result = write_comment(gw, 7, "hello", settings=settings)
    assert result.result == "written" and len(gw.posts()) == 1


def test_evidence_comment_left_as_pending_when_retries_run_out(gw, settings):
    text = "Merge gate: pass head=abc\n\n## Session Summary\nCommand: merge"
    gw.fail_bodies.add(text)
    result = write_comment(gw, 7, text, evidence_item="merge-confirmation", evidence_head="a" * 40, settings=settings)
    assert result.exit_code == 1 and result.result == "pending"
    assert result.error.startswith("write comment on #7")
    [pending] = gw.comments[7]
    match = re.search(r"action=evidence-pending item=merge-confirmation head=(\S+) payload=([A-Za-z0-9+/=]*) -->", pending["body"])
    assert match and match.group(1) == "a" * 40
    assert base64.b64decode(match.group(2)).decode("utf-8") == text
    assert text not in pending["body"]  # the exact text is never visible in the pending copy


def test_evidence_pending_comment_matches_the_bash_library_format():
    body = evidence_pending_comment("cleanup-summary", "-", "## Session Summary\nCommand: cleanup — done")
    assert body.startswith("Dispatcher: a required GitHub comment (cleanup-summary) could not be written.")
    assert body.endswith(" -->") and "\n\n<!-- hpw:dispatcher action=evidence-pending item=cleanup-summary head=- payload=" in body
    payload = body.rsplit("payload=", 1)[1].removesuffix(" -->")
    assert "\n" not in payload and base64.b64decode(payload).decode("utf-8").endswith("— done")


@pytest.mark.skipif(BASH is None, reason="bash not available")
def test_evidence_pending_comment_equals_the_bash_library_output():
    """``$(evidence_pending_comment ...)`` as the bash callers posted it, byte for byte."""
    text = 'Merge gate: pass head=abc\n\n## Session Summary\nBlockers: "none" — ok'
    lib = (Path(__file__).resolve().parent.parent / "scripts" / "lib" / "evidence-pending.sh").as_posix()
    proc = subprocess.run(
        [BASH, "-c", f'source "{lib}"; printf "%s" "$(evidence_pending_comment "$1" "$2" "$(cat)")"', "_",
         "merge-confirmation", "a" * 40],
        input=text.encode("utf-8"), capture_output=True, check=True,
    )
    assert proc.stdout.decode("utf-8") == evidence_pending_comment("merge-confirmation", "a" * 40, text)


def test_evidence_pending_write_failing_too_is_failed_naming_the_step(gw, settings):
    gw.post_modes = ["fail"] * 6
    result = write_comment(gw, 7, "text", evidence_item="cleanup-summary", evidence_head="-", settings=settings)
    assert result.exit_code == 1 and result.result == "failed"
    assert result.error.startswith("write evidence-pending:")
    assert len(gw.posts()) == 6  # 3 for the text, 3 for its pending copy


def test_evidence_comment_already_on_github_is_already(gw, settings):
    gw.add(7, "Merge gate: pass")
    result = write_comment(gw, 7, "Merge gate: pass", evidence_item="merge-confirmation", evidence_head="-",
                           settings=settings)
    assert result.exit_code == 0 and result.result == "already" and gw.posts() == []


# ── AC 2: board status ────────────────────────────────────────────────────────


def test_board_status_already_set_makes_no_write(gw, settings):
    gw.statuses[5] = "In review"
    result = write_board_status(gw, 5, "In review", settings=settings)
    assert result.exit_code == 0 and result.result == "already" and result.fields["attempts"] == 0
    assert not any(c.startswith("set ") for c in gw.calls)


def test_board_status_write_fails_on_every_attempt(gw, settings):
    gw.statuses[5] = "In merge"
    gw.status_modes = ["fail"] * 3
    result = write_board_status(gw, 5, "Done", attempts=3, settings=settings)
    assert result.exit_code == 1 and result.result == "failed" and result.fields["attempts"] == 3
    assert result.fields["before"] == "In merge" and result.error.startswith("write status")
    assert gw.statuses[5] == "In merge"


def test_board_status_read_back_other_status_is_retried_then_fails(gw, settings):
    gw.statuses[5] = "In merge"
    gw.status_modes = ["noop"] * 3
    result = write_board_status(gw, 5, "Done", settings=settings)
    assert result.result == "failed" and "but the board shows 'In merge'" in result.error
    assert len([c for c in gw.calls if c.startswith("set ")]) == 3


def test_board_status_read_back_then_match_is_written(gw, settings):
    gw.statuses[5] = "In merge"
    gw.status_modes = ["noop", "ok"]
    result = write_board_status(gw, 5, "Done", settings=settings)
    assert result.result == "written" and result.fields["attempts"] == 2 and gw.statuses[5] == "Done"


def test_board_status_unknown_option_fails_without_retry(gw, settings):
    gw.statuses[5] = "In merge"
    result = write_board_status(gw, 5, "Shipped", settings=settings)
    assert result.result == "failed" and result.error.startswith("resolve status: 'Shipped' is not a Status option")
    assert gw.calls == ["board 5"]


def test_board_status_issue_not_on_board_fails(gw, settings):
    result = write_board_status(gw, 5, "Done", settings=settings)
    assert result.result == "failed" and result.error == "find item: issue #5 is not on the project board"


# ── AC 2: label ───────────────────────────────────────────────────────────────


def test_label_add_written_and_already(gw, settings):
    gw.repo_labels.add("evidence:pending")
    first = write_label(gw, 9, "evidence:pending", add=True, settings=settings)
    assert first.result == "written" and gw.labels[9] == {"evidence:pending"}
    gw.calls.clear()
    again = write_label(gw, 9, "evidence:pending", add=True, settings=settings)
    assert again.exit_code == 0 and again.result == "already" and gw.calls == ["labels 9"]


def test_label_add_creates_a_missing_repository_label(gw, settings):
    result = write_label(gw, 9, "evidence:pending", add=True, settings=settings)
    assert result.result == "written" and "create-label evidence:pending" in gw.calls


def test_label_remove_written_and_already(gw, settings):
    gw.labels[9] = {"evidence:pending"}
    assert write_label(gw, 9, "evidence:pending", add=False, settings=settings).result == "written"
    assert gw.labels[9] == set()
    assert write_label(gw, 9, "evidence:pending", add=False, settings=settings).result == "already"


@pytest.mark.parametrize("add", [True, False])
def test_label_write_fails_on_every_attempt(gw, settings, add):
    gw.repo_labels.add("x")
    if not add:
        gw.labels[9] = {"x"}
    gw.label_modes = ["fail"] * 3
    result = write_label(gw, 9, "x", add=add, settings=settings)
    assert result.exit_code == 1 and result.result == "failed" and result.fields["attempts"] == 3


@pytest.mark.parametrize("add", [True, False])
def test_label_read_back_mismatch_is_retried_then_fails(gw, settings, add):
    gw.repo_labels.add("x")
    if not add:
        gw.labels[9] = {"x"}
    gw.label_modes = ["noop"] * 3
    result = write_label(gw, 9, "x", add=add, settings=settings)
    assert result.result == "failed" and result.error.startswith("read back: #9")
    assert ("does not carry" if add else "still carries") in result.error


# ── AC 2: retry-pending ───────────────────────────────────────────────────────

STORY, PR, ITEM = 50, 90, "merge-confirmation"
TEXT = "Merge gate: pass head=abc\n\n## Session Summary\nCommand: merge"


def leave_pending(gw, head="-", text=TEXT, target=PR, login=BOT):
    gw.add(target, evidence_pending_comment(ITEM, head, text), login=login)


def test_retry_pending_none(gw, settings):
    result = retry_pending(gw, PR, ITEM, "pr90", STORY, settings=settings)
    assert result.exit_code == 0 and result.result == "none"


def test_retry_pending_of_another_account_is_none(gw, settings):
    leave_pending(gw, login="someone-else")
    result = retry_pending(gw, PR, ITEM, "pr90", STORY, settings=settings)
    assert result.result == "none" and gw.posts() == []


def test_retry_pending_posts_the_exact_text_once(gw, settings):
    leave_pending(gw)
    result = retry_pending(gw, PR, ITEM, "pr90", STORY, settings=settings)
    assert result.exit_code == 0 and result.result == "posted" and result.fields["reason"] == "now"
    assert gw.comments[PR][-1]["body"] == TEXT
    again = retry_pending(gw, PR, ITEM, "pr90", STORY, settings=settings)
    assert again.result == "posted" and again.fields["reason"] == "earlier" and len(gw.posts()) == 1


def test_retry_pending_for_another_head_is_stale(gw, settings):
    leave_pending(gw, head="b" * 40)
    result = retry_pending(gw, PR, "merge-confirmation", "pr90", STORY, expected_head="a" * 40, settings=settings)
    assert result.exit_code == 1 and result.result == "stale" and gw.posts() == []


def test_retry_pending_write_failure_records_a_failed_run_and_is_missing(gw, settings):
    leave_pending(gw)
    gw.fail_bodies.add(TEXT)
    result = retry_pending(gw, PR, ITEM, "pr90", STORY, settings=settings)
    assert result.exit_code == 1 and result.result == "missing"
    assert result.fields["run"] == 1 and result.fields["failure_recorded"] is True
    assert result.fields["blocker_due"] is False and result.fields["payload"] == TEXT
    [marker] = gw.comments[STORY]
    assert marker["body"].startswith(f"Dispatcher: the {ITEM} evidence on #{PR} could not be written to GitHub (failed run 1 of 3).")
    assert evidence_failed_marker(ITEM, "pr90") in marker["body"]


def test_retry_pending_gives_up_on_the_run_that_reaches_the_limit(gw, settings):
    leave_pending(gw)
    gw.add(STORY, "x " + evidence_failed_marker(ITEM, "pr90"))
    gw.add(STORY, "x " + evidence_failed_marker(ITEM, "pr90"))
    gw.fail_bodies.add(TEXT)
    result = retry_pending(gw, PR, ITEM, "pr90", STORY, settings=settings)
    assert result.exit_code == 4 and result.result == "gave-up"
    assert result.fields["run"] == 3 and result.fields["blocker_due"] is True


def test_retry_pending_stops_while_the_give_up_marker_holds(gw, settings):
    leave_pending(gw)
    gw.add(STORY, "## Blocked Declaration\n" + evidence_gave_up_marker(ITEM, "pr90"))
    result = retry_pending(gw, PR, ITEM, "pr90", STORY, settings=settings)
    assert result.exit_code == 4 and result.result == "gave-up" and result.fields["reason"] == "stopped"
    assert gw.posts() == []


def test_retry_pending_resumes_after_the_po_unblock(gw, settings):
    leave_pending(gw)
    gw.add(STORY, "## Blocked Declaration\n" + evidence_gave_up_marker(ITEM, "pr90"))
    gw.add(STORY, "Posted by hand.\n/unblock\n", login=PO)
    result = retry_pending(gw, PR, ITEM, "pr90", STORY, settings=settings)
    assert result.result == "posted"


def test_retry_pending_counts_failures_since_the_last_give_up_only(gw, settings):
    leave_pending(gw)
    for _ in range(2):
        gw.add(STORY, evidence_failed_marker(ITEM, "pr90"))
    gw.add(STORY, evidence_gave_up_marker(ITEM, "pr90"))
    gw.add(STORY, "/unblock", login=PO)
    gw.fail_bodies.add(TEXT)
    result = retry_pending(gw, PR, ITEM, "pr90", STORY, settings=settings)
    assert result.result == "missing" and result.fields["run"] == 1


def test_retry_pending_unreadable_comments_is_missing(gw, settings):
    gw.list_failures = 1
    result = retry_pending(gw, PR, ITEM, "pr90", STORY, settings=settings)
    assert result.exit_code == 1 and result.result == "missing" and result.fields["reason"] == "unreadable"


@pytest.mark.parametrize("body,reason", [
    ("<!-- hpw:dispatcher action=evidence-pending item=merge-confirmation broken -->", "malformed"),
    ("<!-- hpw:dispatcher action=evidence-pending item=merge-confirmation head=- payload=!!!! -->", "undecodable"),
    ("<!-- hpw:dispatcher action=evidence-pending item=merge-confirmation head=- payload= -->", "undecodable"),
])
def test_retry_pending_malformed_payload_is_ignored(gw, settings, body, reason):
    gw.add(PR, body)
    result = retry_pending(gw, PR, ITEM, "pr90", STORY, settings=settings)
    assert result.exit_code == 0 and result.result == "none" and result.fields["reason"] == reason


def test_retry_pending_dry_run_posts_nothing(gw, settings):
    leave_pending(gw)
    result = retry_pending(gw, PR, ITEM, "pr90", STORY, dry_run=True, settings=settings)
    assert result.exit_code == 0 and result.result == "dry-run" and result.fields["payload"] == TEXT
    assert gw.posts() == []


def test_retry_pending_exit_codes_through_the_cli(gw, settings, capsys):
    leave_pending(gw)
    gw.add(STORY, evidence_gave_up_marker(ITEM, "pr90"))
    rc, out, _ = run_cli(gw, settings, capsys, "write", "retry-pending", "--repo", "o/r", "--target", str(PR),
                         "--item", ITEM, "--ref", "pr90", "--story", str(STORY))
    assert rc == 4 and out["result"] == "gave-up" and out["ok"] is False


# ── the token is never printed ────────────────────────────────────────────────


def test_no_output_line_contains_the_token(gw, settings, capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    gw.error_text = f"HTTP 401: Bad credentials (token {TOKEN})"
    gw.post_modes = ["fail"] * 6
    gw.statuses[5] = "In merge"
    gw.status_modes = ["fail"] * 3
    outputs = []
    for argv in (
        ["write", "comment", "--repo", "o/r", "--number", "7", "--body-file", body_file(tmp_path, "x"),
         "--evidence-item", "cleanup-summary", "--evidence-head", "-"],
        ["write", "board-status", "--repo", "o/r", "--issue", "5", "--status", "Done"],
    ):
        cli.main(argv, gateway_factory=lambda repo: gw, settings=settings)
        out, err = capsys.readouterr()
        outputs.append(out + err)
    text = "\n".join(outputs)
    assert TOKEN not in text and "***" in text


# ── the real gateway against a stand-in gh ────────────────────────────────────

FAKE_GH = r'''
import json, os, sys
state_path = os.environ["FAKE_GH_STATE"]
state = json.load(open(state_path))
args = sys.argv[1:]
state["calls"].append(args)
def save():
    json.dump(state, open(state_path, "w"))
if args[:2] == ["api", "user"]:
    print(json.dumps({"login": "hpw-bot"}))
elif args[:2] == ["api", "--paginate"]:
    comments = state["comments"]
    print(json.dumps(comments[:1]) + json.dumps(comments[1:]))  # two pages, as gh --paginate prints them
elif "--input" in args:
    body = json.load(sys.stdin)["body"]
    comment = {"user": {"login": "hpw-bot"}, "body": body, "created_at": "2026-10-13T10:00:0%dZ" % len(state["comments"]),
               "html_url": "https://x/c"}
    state["comments"].append(comment)
    print(json.dumps(comment))
elif args[:2] == ["api", "graphql"]:
    query = next(a for a in args if a.startswith("query="))
    if "mutation" in query:
        option = next(a for a in args if a.startswith("optionId=")).split("=", 1)[1]
        state["status"] = {"OPT_DONE": "Done"}[option]
        print("{}")
    else:
        print(json.dumps({"data": {"repository": {
            "projectsV2": {"nodes": [{"id": "P", "fields": {"nodes": [{}, {"id": "F", "name": "Status",
                "options": [{"id": "OPT_MERGE", "name": "In merge"}, {"id": "OPT_DONE", "name": "Done"}]}]}}]},
            "issue": {"projectItems": {"nodes": [{"id": "ITEM", "project": {"id": "P"},
                "fieldValueByName": {"name": state["status"]}}]}}}}}))
else:
    print("gh: Not Found (HTTP 404)", file=sys.stderr)
    sys.exit(1)
save()
'''


@pytest.fixture
def real_gateway(tmp_path, monkeypatch):
    script = tmp_path / "gh.py"
    script.write_text(FAKE_GH, encoding="utf-8")
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"calls": [], "comments": [{"user": {"login": "x"}, "body": "old"}],
                                 "status": "In merge"}), encoding="utf-8")
    monkeypatch.setenv("FAKE_GH_STATE", str(state))
    monkeypatch.delenv("DISPATCHER_LOGIN", raising=False)
    gateway = GhGateway("o/r", gh_command=(sys.executable, str(script)))
    return gateway, lambda: json.loads(state.read_text(encoding="utf-8"))


def test_real_gateway_posts_a_body_with_quotes_and_newlines_on_stdin(real_gateway, settings):
    gateway, state = real_gateway
    text = 'Blockers: "merged" is not a field\nC:\\path\\ — done'
    result = write_comment(gateway, 7, text, settings=settings)
    assert result.result == "written", result.error
    assert state()["comments"][-1]["body"] == text
    post = next(c for c in state()["calls"] if "--input" in c)
    assert post == ["api", "repos/o/r/issues/7/comments", "-X", "POST", "--input", "-"]  # never the body in argv


def test_real_gateway_board_write_reads_back(real_gateway, settings):
    gateway, state = real_gateway
    result = write_board_status(gateway, 5, "Done", settings=settings)
    assert result.result == "written" and result.fields["before"] == "In merge" and state()["status"] == "Done"


def test_real_gateway_failure_names_the_step(real_gateway):
    gateway, _ = real_gateway
    with pytest.raises(GatewayError) as error:
        gateway.issue_labels(9)
    assert error.value.step == "read labels of #9" and error.value.not_found


def test_paginated_output_is_one_list():
    assert parse_json_stream('[1, 2]\n[3]\n') == [[1, 2], [3]]
    assert parse_json_stream("") == []
