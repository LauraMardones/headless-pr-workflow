"""Board writer: ``hpw dispatcher status sync`` (issue #476).

Every path runs against a fake gateway that holds the board and the issue bodies and applies the writes it is
asked for: no network, no real ``gh``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from headless_pr_workflow.dispatcher import board_sync, cli
from headless_pr_workflow.dispatcher.board_sync import PROGRESS_END, PROGRESS_START, sync_board
from headless_pr_workflow.dispatcher.gateway import BoardCard, BoardItem, BoardSnapshot, GatewayError, GhGateway
from headless_pr_workflow.dispatcher.status import STATUSES
from headless_pr_workflow.dispatcher.write import Settings

from tests.test_dispatcher_facts_block import BLOCKER, PO, spec_line, story_body, summary
from tests.test_dispatcher_status import StatusGateway

SETTINGS = Settings(retry_delay=0.0, sleep=lambda _s: None)


class SyncGateway(StatusGateway):
    """A board, issue bodies and the writes; the writes are applied, so a second run sees them."""

    def __init__(self) -> None:
        super().__init__()
        self.options = {name: f"opt-{i}" for i, name in enumerate(STATUSES)}
        self.board: dict[int, dict] = {}  # number -> {"status", "state", "type"}
        self.other_cards: list[BoardCard] = [BoardCard("item-pr", None, None, (), "In review")]
        self.writes: list[tuple[int, str]] = []
        self.body_writes: list[tuple[int, str]] = []
        self.wrong_read_back = False

    def card(self, number, status, *, state="open", kind="story"):
        self.board[number] = {"status": status, "state": state, "type": kind}

    def list_board_items(self):
        self.calls.append("list_board_items")
        if "board" in self.fail:
            raise GatewayError("read board items", "HTTP 500")
        cards = [BoardCard(f"item-{n}", n, c["state"], (f"type:{c['type']}",) if c["type"] else (), c["status"])
                 for n, c in sorted(self.board.items())]  # fmt: skip
        return BoardSnapshot(options=dict(self.options), cards=cards + self.other_cards)

    def board_item(self, issue):
        self.calls.append(f"board_item {issue}")
        if issue not in self.board:
            return BoardItem("P", "F", dict(self.options))
        return BoardItem("P", "F", dict(self.options), f"item-{issue}", self.board[issue]["status"])

    def set_board_status(self, item, option_id):
        number = int(item.item_id.split("-")[1])
        name = next(n for n, o in self.options.items() if o == option_id)
        self.writes.append((number, name))
        if f"write {number}" in self.fail:
            raise GatewayError("write status", "HTTP 500")
        if not self.wrong_read_back:
            self.board[number]["status"] = name

    def issue_body(self, number):
        self.calls.append(f"issue_body {number}")
        if f"body {number}" in self.fail:
            raise GatewayError(f"read body of #{number}", "HTTP 500")
        return next(i for i in self.issues if i["number"] == number)["body"]

    def edit_issue_body(self, number, body):
        self.body_writes.append((number, body))
        if f"edit {number}" in self.fail:
            raise GatewayError(f"write body of #{number}", "HTTP 500")
        if not self.wrong_read_back:
            next(i for i in self.issues if i["number"] == number)["body"] = body

    def add_closed(self, number, labels, body="", title=None):
        super().add_closed(number, labels, body)
        self.closed[-1]["title"] = title or f"T{number}"


@pytest.fixture(autouse=True)
def _po(monkeypatch):
    monkeypatch.setenv("DECISION_BLOCKER_PO_LOGIN", PO)
    monkeypatch.delenv("GH_TOKEN", raising=False)


@pytest.fixture
def gw():
    g = SyncGateway()
    g.add_issue(10, ["type:epic"], spec_line() + "\n\nEpic text.\n")
    g.comment(10, summary())
    g.add_issue(20, ["type:feature"], story_body(feature=20))
    g.comment(20, summary())
    g.add_issue(30, ["type:story"], story_body())
    g.comment(30, summary())
    g.add_closed(31, ["type:story"], story_body())
    g.card(10, "In progress", kind="epic")
    g.card(20, "In progress", kind="feature")
    g.card(30, "Ready to implement")
    g.card(31, "Done", state="closed")
    return g


def sync(gw, **kw):
    kw.setdefault("settings", SETTINGS)
    return sync_board(gw, "o/r", **kw)


def settled(gw):
    """Run until the epic body has its section, so a test of the board starts from matching bodies."""

    assert sync(gw).result == "synced"
    gw.writes.clear()
    gw.body_writes.clear()


def fields(result):
    return result.fields


def by_issue(result):
    return {c["issue"]: c for c in result.fields["corrections"]}


# ── board corrections (AC 2, 3) ──────────────────────────────────────────────


def test_matching_board_writes_no_status(gw):
    result = sync(gw)
    assert result.result == "synced" and result.exit_code == 0 and fields(result)["corrections"] == []
    assert gw.writes == []


def test_a_card_moved_by_hand_is_written_back(gw, capsys):
    gw.card(30, "Done")  # moved by hand
    result = sync(gw)
    correction = by_issue(result)[30]
    assert gw.writes == [(30, "Ready to implement")] and gw.board[30]["status"] == "Ready to implement"
    assert (correction["before"], correction["after"], correction["result"], correction["type"]) == (
        "Done", "Ready to implement", "written", "story")  # fmt: skip
    assert correction["fact"] and "refined" in correction["fact"] and correction["error"] is None
    assert "[SYNC] #30 Done -> Ready to implement: " + correction["fact"] in capsys.readouterr().err


def test_a_card_with_no_status_shows_none_in_the_log(gw, capsys):
    gw.card(30, None)
    sync(gw)
    assert "[SYNC] #30 (none) -> Ready to implement" in capsys.readouterr().err
    assert by_issue(sync(gw)) == {}


def test_a_closed_issue_on_another_status_is_set_to_done(gw):
    gw.card(31, "In review", state="closed")
    result = sync(gw)
    assert gw.writes == [(31, "Done")] and by_issue(result)[31]["fact"] == "the issue is closed"


def test_unknown_status_is_left_alone_and_listed_with_its_reason(gw):
    gw.fail.add("comments 30")
    result = sync(gw)
    assert result.result == "synced" and gw.writes == []
    assert fields(result)["skipped"] == [{"issue": 30, "reason": "unknown: unreadable: read comments on #30"}]


def test_an_open_issue_missing_from_the_board_is_listed_and_not_written(gw):
    del gw.board[30]
    result = sync(gw)
    assert fields(result)["skipped"] == [{"issue": 30, "reason": "not_on_board"}]
    assert result.result == "synced" and gw.writes == [] and not any(c.startswith("board_item") for c in gw.calls)


def test_items_that_are_not_typed_issues_are_not_touched_or_listed(gw):
    gw.card(77, "Whatever", kind=None)  # an issue without a type: label
    result = sync(gw)
    assert gw.writes == [] and all(s["issue"] != 77 for s in fields(result)["skipped"])
    assert fields(result)["corrections"] == []  # the pull-request card (In review) is not compared


def test_epic_option_limits_the_run_to_that_epic(gw):
    gw.add_issue(11, ["type:epic"], spec_line("specs/11-x/spec.md"))
    gw.card(11, "Done", kind="epic")
    gw.card(30, "Done")
    gw.card(10, "Done", kind="epic")
    result = sync(gw, epic=10)
    assert {c["issue"] for c in fields(result)["corrections"]} == {10, 30}
    assert [p["epic"] for p in fields(result)["progress"]] == [10]


def test_missing_status_option_fails_the_item_with_the_reason(gw):
    del gw.options["Ready to implement"]
    gw.card(30, "Done")
    result = sync(gw, attempts=2)
    correction = by_issue(result)[30]
    assert correction["result"] == "failed" and "is not a Status option" in correction["error"]
    assert result.result == "failed" and result.exit_code == 1 and gw.writes == []


def test_missing_status_option_in_a_dry_run_is_flagged(gw):
    del gw.options["Ready to implement"]
    gw.card(30, "Done")
    correction = by_issue(sync(gw, dry_run=True))[30]
    assert correction["result"] == "dry-run" and correction["missing_option"] is True


def test_one_failed_write_does_not_stop_the_others(gw):
    gw.card(30, "Done")
    gw.card(20, "Done", kind="feature")
    gw.fail.add("write 20")
    result = sync(gw, attempts=2)
    done = by_issue(result)
    assert done[20]["result"] == "failed" and done[30]["result"] == "written"
    assert result.result == "failed" and result.exit_code == 1 and result.error.startswith("write status")
    assert gw.board[30]["status"] == "Ready to implement"


def test_a_write_that_reads_back_wrong_is_failed(gw):
    gw.card(30, "Done")
    gw.wrong_read_back = True
    result = sync(gw, attempts=1)
    assert by_issue(result)[30]["result"] == "failed" and "read back" in result.error


# ── dry run (AC 4) ───────────────────────────────────────────────────────────


def test_dry_run_lists_and_writes_nothing_then_a_run_settles_it(gw):
    gw.card(30, "Done")
    before = [i["body"] for i in gw.issues]
    dry = sync(gw, dry_run=True)
    assert dry.result == "dry-run" and dry.exit_code == 0 and fields(dry)["dry_run"] is True
    assert by_issue(dry)[30]["result"] == "dry-run" and [p["result"] for p in fields(dry)["progress"]] == ["dry-run"]
    assert gw.writes == [] and gw.body_writes == [] and [i["body"] for i in gw.issues] == before
    assert sync(gw).result == "synced"
    again = sync(gw, dry_run=True)
    assert fields(again)["corrections"] == [] and [p["result"] for p in fields(again)["progress"]] == ["already"]


def test_two_runs_write_the_same_things(gw):
    gw.card(30, "Done")
    sync(gw)
    first_writes, first_bodies = list(gw.writes), list(gw.body_writes)
    sync(gw)
    assert gw.writes == first_writes and gw.body_writes == first_bodies


# ── unreadable (AC 5) ────────────────────────────────────────────────────────


@pytest.mark.parametrize("fail", ["board", "closed", "issues", "prs", "specs"])
def test_a_failed_read_writes_nothing_and_is_unreadable(gw, fail):
    gw.card(30, "Done")
    gw.fail.add(fail)
    result = sync(gw)
    assert result.result == "unreadable" and result.exit_code == 3 and result.error
    assert gw.writes == [] and gw.body_writes == []


def test_an_unreadable_epic_body_writes_nothing_for_that_epic(gw):
    gw.fail.add("body 10")
    result = sync(gw)
    assert [p["result"] for p in fields(result)["progress"]] == ["failed"] and result.result == "failed"
    assert gw.body_writes == []


# ── the Epic progress section (AC 6) ─────────────────────────────────────────

SECTION = (f"{PROGRESS_START}\n## Progress\n\n- #20 T20: In progress, 1 of 2 implementation issues closed\n\n"
           f"Features: 0 of 1 closed; implementation issues: 1 of 2 closed\n{PROGRESS_END}")


def epic_body(gw):
    return next(i for i in gw.issues if i["number"] == 10)["body"]


def test_the_section_is_appended_once_and_the_rest_is_unchanged(gw):
    original = epic_body(gw)
    result = sync(gw)
    assert [(p["epic"], p["result"]) for p in fields(result)["progress"]] == [(10, "written")]
    assert epic_body(gw) == original + "\n" + SECTION + "\n" and epic_body(gw).startswith(original)
    sync(gw)
    assert epic_body(gw).count(PROGRESS_START) == 1 and len(gw.body_writes) == 1


def test_an_unchanged_section_sends_no_write(gw):
    gw.issues[0]["body"] = "Before.\n\n" + SECTION + "\n\nAfter.\n"
    result = sync(gw)
    assert [p["result"] for p in fields(result)["progress"]] == ["already"] and gw.body_writes == []


def test_a_changed_section_is_replaced_and_the_text_around_it_is_byte_identical(gw):
    old = SECTION.replace("1 of 2 implementation", "0 of 2 implementation")
    gw.issues[0]["body"] = "Before \r\n  text\t\n\n" + old + "\n\nAfter\r\n\n"
    sync(gw)
    assert epic_body(gw) == "Before \r\n  text\t\n\n" + SECTION + "\n\nAfter\r\n\n"


def test_a_closed_feature_shows_done_and_a_new_closed_story_changes_the_section(gw):
    gw.add_closed(21, ["type:feature"], story_body(feature=21), title="Closed feature")
    gw.add_closed(32, ["type:bug"], story_body(feature=21))
    sync(gw)
    body = epic_body(gw)
    assert "- #21 Closed feature: Done, 1 of 1 implementation issues closed" in body
    assert "Features: 1 of 2 closed; implementation issues: 2 of 3 closed" in body


def test_the_section_has_no_date_or_run_id(gw):
    sync(gw)
    assert not re.search(r"\d{4}-\d{2}-\d{2}|\d{2}:\d{2}", epic_body(gw).split(PROGRESS_START)[1])


@pytest.mark.parametrize("body", [
    f"{PROGRESS_START}\nx\n{PROGRESS_END}\n{PROGRESS_START}\ny\n{PROGRESS_END}",
    f"{PROGRESS_END}\nx\n{PROGRESS_START}",
    f"text\n{PROGRESS_START}\nunfinished",
    f"text\n{PROGRESS_END}",
    f"{PROGRESS_START}{PROGRESS_START}{PROGRESS_END}",
])
def test_malformed_markers_write_nothing_and_fail_the_epic(gw, body):
    gw.issues[0]["body"] = body
    result = sync(gw)
    progress = fields(result)["progress"][0]
    assert progress["result"] == "failed" and "markers" in progress["error"]
    assert result.result == "failed" and result.exit_code == 1 and gw.body_writes == [] and epic_body(gw) == body


def test_malformed_markers_do_not_stop_the_board_writes(gw):
    gw.issues[0]["body"] += PROGRESS_END
    gw.card(30, "Done")
    sync(gw)
    assert gw.writes == [(30, "Ready to implement")]


def test_a_body_that_changes_after_the_list_read_is_not_overwritten(gw):
    original = gw.issue_body

    def human_edit(number):
        body = original(number)
        if number == 10 and not gw.body_writes:
            next(i for i in gw.issues if i["number"] == 10)["body"] += "A human edit.\n"
            body = original(number)
        return body

    gw.issue_body = human_edit
    sync(gw)
    assert epic_body(gw).startswith(spec_line()) and "A human edit.\n" in epic_body(gw)
    assert epic_body(gw).endswith(SECTION + "\n")


def test_a_body_write_that_reads_back_different_is_failed(gw):
    gw.wrong_read_back = True
    result = sync(gw)
    assert fields(result)["progress"][0]["result"] == "failed" and "read back" in result.error


def test_a_failed_body_write_is_failed(gw):
    gw.fail.add("edit 10")
    result = sync(gw)
    assert result.result == "failed" and result.error.startswith("write body of #10")


def test_a_closed_epic_is_not_written(gw):
    gw.closed.append({"number": 12, "title": "Old", "body": "old", "labels": [{"name": "type:epic"}]})
    result = sync(gw)
    assert [p["epic"] for p in fields(result)["progress"]] == [10] and all(n != 12 for n, _ in gw.body_writes)


# ── only the two permitted writes (AC 8) ─────────────────────────────────────


def test_the_module_calls_no_other_write():
    source = (Path(board_sync.__file__)).read_text(encoding="utf-8")
    for forbidden in ("set-project-status", "set_board_status", "post_comment", "add_label", "remove_label",
                      "create_label", "subprocess", "graphql", "STATUS_MUTATION"):  # fmt: skip
        assert forbidden not in source
    assert set(re.findall(r"gateway\.(\w+)\(", source)) <= {
        "list_open_issues", "list_closed_issues", "list_board_items", "issue_body", "edit_issue_body"}


def test_a_run_sends_only_status_and_body_writes(gw):
    gw.card(30, "Done")

    for name in ("post_comment", "add_label", "remove_label", "create_label"):
        setattr(gw, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError("forbidden write")))
    assert sync(gw).result == "synced"


# ── the command (AC 7) ───────────────────────────────────────────────────────


def run(capsys, gateway, *argv):
    rc = cli.main(["status", "sync", "--repo", "o/r", *argv], gateway_factory=lambda repo: gateway, settings=SETTINGS)
    out, err = capsys.readouterr()
    lines = out.splitlines()
    assert len(lines) == 1, out
    return rc, json.loads(lines[0]), err


def test_command_prints_one_json_line_with_the_contract_fields(gw, capsys):
    gw.card(30, "Done")
    rc, out, err = run(capsys, gw)
    assert rc == 0 and out["ok"] is True and (out["block"], out["operation"], out["result"]) == ("status", "sync", "synced")
    assert set(out) == {"ok", "block", "operation", "result", "corrections", "skipped", "progress", "dry_run", "error"}
    assert out["dry_run"] is False and out["error"] is None
    assert out["corrections"][0]["issue"] == 30 and "[SYNC] progress #10: written" in err


def test_command_dry_run_exits_0(gw, capsys):
    gw.card(30, "Done")
    rc, out, _ = run(capsys, gw, "--dry-run")
    assert rc == 0 and out["result"] == "dry-run" and out["dry_run"] is True and gw.writes == []


def test_command_failed_exits_1(gw, capsys):
    gw.card(30, "Done")
    gw.fail.add("write 30")
    rc, out, _ = run(capsys, gw, "--attempts", "1")
    assert rc == 1 and out["result"] == "failed" and out["ok"] is False and out["error"]


def test_command_unreadable_exits_3(gw, capsys):
    gw.fail.add("board")
    rc, out, _ = run(capsys, gw)
    assert rc == 3 and out["result"] == "unreadable" and out["corrections"] == []


@pytest.mark.parametrize("argv", [["--epic", "0"], ["--epic", "x"], ["--attempts", "0"], ["--bogus"], []])
def test_command_invalid_arguments_exit_2(gw, capsys, argv):
    rc = cli.main(["status", "sync", *([] if not argv else ["--repo", "o/r", *argv])], gateway_factory=lambda r: gw)
    out = capsys.readouterr().out
    assert rc == 2 and json.loads(out)["result"] == "invalid"


def test_the_token_is_masked_in_the_output(gw, capsys, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "ghp_secretTOKENvalue123")
    gw.card(30, "Done")
    gw.fail.add("write 30")

    def leak(item, option):
        raise GatewayError("write status", "denied for ghp_secretTOKENvalue123")

    gw.set_board_status = leak
    rc, out, err = run(capsys, gw, "--attempts", "1")
    assert "ghp_secretTOKENvalue123" not in json.dumps(out) + err


def test_exit_code_of_the_new_result_word():
    from headless_pr_workflow.dispatcher.write import Result

    assert Result("sync", "synced").exit_code == 0


# ── the real gateway reads and writes (AC 2, 6) ──────────────────────────────


class Recorded(GhGateway):
    def __init__(self, payloads):
        super().__init__("o/r")
        self.payloads, self.sent = list(payloads), []

    def _json(self, step, arguments):
        self.sent.append(arguments)
        return self.payloads.pop(0)

    def _run(self, step, arguments, *, stdin=None):
        self.sent.append((arguments, stdin))
        return ""


def board_page(nodes, *, next_cursor=None):
    return {"data": {"repository": {"projectsV2": {"nodes": [{
        "id": "P", "fields": {"nodes": [{"id": "F", "name": "Status", "options": [{"id": "o1", "name": "Done"}]}]},
        "items": {"pageInfo": {"hasNextPage": next_cursor is not None, "endCursor": next_cursor}, "nodes": nodes}}]}}}}


def issue_node(item_id, number, state, labels, status, repo="o/r"):
    return {"id": item_id, "content": {"__typename": "Issue", "number": number, "state": state,
                                       "repository": {"nameWithOwner": repo},
                                       "labels": {"nodes": [{"name": n} for n in labels]}},
            "fieldValueByName": {"name": status} if status else None}  # fmt: skip


def test_the_gateway_pages_through_the_board_items():
    gateway = Recorded([
        board_page([issue_node("i1", 1, "OPEN", ["type:story"], "Done")], next_cursor="C1"),
        board_page([issue_node("i2", 2, "CLOSED", [], None), {"id": "i3", "content": {"__typename": "PullRequest"}},
                    {"id": "i4", "content": None}]),
    ])  # fmt: skip
    snapshot = gateway.list_board_items()
    assert snapshot.options == {"Done": "o1"}
    assert snapshot.cards == [BoardCard("i1", 1, "open", ("type:story",), "Done"), BoardCard("i2", 2, "closed", (), None),
                              BoardCard("i3", None, None, (), None), BoardCard("i4", None, None, (), None)]  # fmt: skip
    assert not any(a.startswith("cursor=") for a in gateway.sent[0]) and "cursor=C1" in gateway.sent[1]


def test_the_gateway_ignores_cards_of_another_repository():
    gateway = Recorded([board_page([issue_node("i1", 30, "OPEN", [], "Done"),
                                    issue_node("i2", 30, "CLOSED", [], "In review", repo="other/repo")])])
    cards = gateway.list_board_items().cards
    assert [(c.item_id, c.number) for c in cards] == [("i1", 30), ("i2", None)]


@pytest.mark.parametrize("payload, reason", [
    ({"errors": [{"message": "no scope"}]}, "no scope"),
    ({"data": {"repository": {"projectsV2": {"nodes": []}}}}, "no Projects v2 board"),
    (board_page([], next_cursor=""), "without a cursor"),
])
def test_the_gateway_fails_closed_on_a_bad_board(payload, reason):
    with pytest.raises(GatewayError, match=reason):
        Recorded([payload]).list_board_items()


def test_the_gateway_edits_a_body_through_stdin():
    gateway = Recorded([])
    gateway.edit_issue_body(10, "line\n\"quoted\"")
    arguments, stdin = gateway.sent[0]
    assert arguments[-4:] == ["-X", "PATCH", "--input", "-"] and arguments[1].endswith("/issues/10")
    assert json.loads(stdin) == {"body": "line\n\"quoted\""}


def test_the_gateway_reads_a_body():
    assert Recorded([{"body": None}]).issue_body(10) == ""
    assert Recorded([{"body": "x"}]).issue_body(10) == "x"
