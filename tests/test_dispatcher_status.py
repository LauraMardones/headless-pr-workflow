"""Status rule: ``hpw dispatcher status read`` (issue #258).

``derive_status`` and ``count_progress`` are tested over hand-built facts: no gateway at all. The reader and the
command are tested through a fake gateway (tests/test_dispatcher_facts_block.py) that fails on any write.
"""

from __future__ import annotations

import inspect
import json

import pytest

from headless_pr_workflow.dispatcher import cli, status
from headless_pr_workflow.dispatcher.gateway import GatewayError, GhGateway
from headless_pr_workflow.dispatcher.status import STATUSES, count_progress, derive_status, read_status

from tests.test_dispatcher_facts_block import BLOCKER, PO, RECORD_OK, SPEC_PATH, FakeGateway, spec_line, story_body, summary

DRAFT = {"number": 50, "draft": True, "gates_pass": False}
REVIEW = {"number": 51, "draft": False, "gates_pass": False}
GREEN = {"number": 52, "draft": False, "gates_pass": True}


def facts(**over):
    base = {"type": "story", "state": "open", "unreadable": None, "blocked": False, "refined": True, "ready": True,
            "parent_open_epic": True, "spec_merged": True, "spec_pr": False, "prs": []}  # fmt: skip
    base.update(over)
    return base


def of(**over):
    return derive_status(facts(**over))[0]


# ── the pure rule ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("over, expected", [
    ({"state": "closed"}, "Done"),
    ({"blocked": True}, "Blocked"),
    ({"type": "epic", "spec_pr": True, "refined": False}, "In specification"),
    ({"refined": False, "spec_merged": False, "spec_pr": True}, "In specification"),
    ({"refined": False, "spec_merged": False}, "No spec"),
    ({"refined": False, "parent_open_epic": False}, "No spec"),
    ({"type": "epic", "refined": False, "spec_merged": False}, "No spec"),
    ({"refined": False}, "Not refined"),
    ({"type": "feature", "refined": False}, "Not refined"),
    ({"prs": [GREEN, REVIEW, DRAFT]}, "Mergeable"),
    ({"prs": [REVIEW, DRAFT]}, "In review"),
    ({"prs": [DRAFT]}, "In implementation"),
    ({}, "Ready to implement"),
    ({"ready": False}, "Refined"),
    ({"type": "epic", "ready": False}, "In progress"),
    ({"type": "feature"}, "In progress"),
    ({"type": None}, "unknown"),
    ({"unreadable": "unreadable: comments"}, "unknown"),
])
def test_every_status_and_type(over, expected):
    assert of(**over) == expected


def test_every_status_name_is_reachable():
    reached = {"unknown"}
    for over in [{"state": "closed"}, {"blocked": True}, {"type": "epic", "spec_pr": True},
                 {"refined": False, "spec_merged": False}, {"refined": False, "refinement_failed": True},
                 {"refined": False}, {"prs": [GREEN]}, {"prs": [REVIEW]},
                 {"prs": [DRAFT]}, {}, {"ready": False}, {"type": "epic"}, {"type": None}]:  # fmt: skip
        reached.add(of(**over))
    assert reached == set(STATUSES) | {"unknown"}


@pytest.mark.parametrize("kind", ["epic", "feature"])
@pytest.mark.parametrize("prs", [[], [DRAFT], [REVIEW], [GREEN]])
def test_epics_and_features_never_get_story_statuses(kind, prs):
    story_only = {"Refined", "Ready to implement", "In implementation", "In review", "Mergeable"}
    for refined in (True, False):
        for merged in (True, False):
            for spec_pr in (True, False):
                got = of(type=kind, refined=refined, spec_merged=merged, spec_pr=spec_pr, prs=prs, ready=True)
                assert got not in story_only
    assert of(type=kind, prs=prs) == "In progress"


@pytest.mark.parametrize("kind", ["story", "task", "bug"])
def test_in_progress_never_applies_to_implementation_issues(kind):
    for over in [{}, {"ready": False}, {"prs": [DRAFT]}, {"refined": False}]:
        assert of(type=kind, **over) != "In progress"


# precedence: for each pair, the facts of both statuses hold and the earlier one wins
@pytest.mark.parametrize("over, winner", [
    ({"state": "closed", "blocked": True, "unreadable": "x", "prs": [GREEN]}, "Done"),
    ({"blocked": True, "spec_pr": True, "type": "epic", "refined": False}, "Blocked"),
    ({"type": "epic", "spec_pr": True, "spec_merged": False, "refined": False}, "In specification"),
    ({"refined": False, "spec_merged": False, "spec_pr": True, "parent_open_epic": True}, "In specification"),
    ({"refined": False, "spec_merged": False}, "No spec"),
    ({"refined": False, "prs": [GREEN]}, "Not refined"),
    ({"prs": [GREEN, REVIEW, DRAFT], "ready": True}, "Mergeable"),
    ({"prs": [REVIEW, DRAFT], "ready": True}, "In review"),
    ({"prs": [DRAFT], "ready": True}, "In implementation"),
])
def test_precedence(over, winner):
    assert of(**over) == winner


@pytest.mark.parametrize("kind", ["epic", "feature", "story", "task", "bug"])
def test_refinement_failed_for_every_type_and_beats_not_refined(kind):
    assert of(type=kind, refined=False, refinement_failed=True) == "Refinement failed"
    assert of(type=kind, refined=False, refinement_failed=False) == "Not refined"


@pytest.mark.parametrize("over, winner", [
    ({"state": "closed"}, "Done"),
    ({"blocked": True}, "Blocked"),
    ({"spec_pr": True, "refined": False, "spec_merged": False}, "In specification"),
    ({"refined": False, "spec_merged": False}, "No spec"),
    ({"refined": False, "parent_open_epic": False}, "No spec"),
])
def test_refinement_failed_yields_to_the_earlier_statuses(over, winner):
    assert of(refinement_failed=True, **{"refined": False, **over}) == winner


def test_a_refinement_failed_fact_names_why():
    status_, fact = derive_status(facts(refined=False, refinement_failed=True))
    assert status_ == "Refinement failed" and "refine-check" in fact and "\n" not in fact


def test_no_spec_beats_not_refined_when_both_hold():
    assert of(refined=False, spec_merged=False) == "No spec"
    assert of(refined=False, spec_merged=True) == "Not refined"


def test_refined_story_without_merged_spec_keeps_implementation_statuses():
    assert of(spec_merged=False, prs=[DRAFT]) == "In implementation"
    assert of(spec_merged=False) == "Ready to implement"


def test_gap_rules():
    # 1. an unrefined issue whose Epic has an open spec PR and no merged spec
    assert of(refined=False, spec_merged=False, spec_pr=True) == "In specification"
    assert of(type="feature", refined=False, spec_merged=False, spec_pr=True) == "In specification"
    # 2. no open parent Epic: No spec, unless an open linked PR gives the PR statuses
    assert of(parent_open_epic=False, refined=False) == "No spec"
    assert of(parent_open_epic=False, refined=False, prs=[DRAFT]) == "In implementation"
    assert of(parent_open_epic=False, refined=False, prs=[GREEN]) == "Mergeable"
    assert of(type="feature", parent_open_epic=False, refined=False, prs=[GREEN]) == "No spec"
    # 3. no known type: no derived status
    assert derive_status(facts(type=None))[0] == "unknown"


def test_unreadable_facts_give_unknown_with_reason_and_closed_still_done():
    assert derive_status(facts(unreadable="read comments on #1")) == ("unknown", "read comments on #1")
    assert derive_status(facts(unreadable="x", state="closed"))[0] == "Done"
    assert derive_status(facts(type="epic", state="closed", unreadable="x"))[0] == "Done"


def test_closed_with_unknown_type_has_no_status():
    assert derive_status(facts(type=None, state="closed"))[0] == "unknown"


def test_same_facts_same_result_and_a_fact_names_why():
    one = facts(prs=[REVIEW])
    assert derive_status(one) == derive_status(dict(one))
    assert "PR #51" in derive_status(one)[1]
    for over in [{}, {"blocked": True}, {"state": "closed"}, {"refined": False}]:
        status_, fact = derive_status(facts(**over))
        assert fact and "\n" not in fact


# ── progress counts ──────────────────────────────────────────────────────────


def row(number, kind, state="open", epic=None, feature=None):
    return {"number": number, "type": kind, "state": state, "epic": epic, "feature": feature}


def test_progress_counts_per_feature_and_epic():
    items = [
        row(10, "epic"), row(20, "feature", epic=10), row(21, "feature", "closed", epic=10),
        row(30, "story", epic=10, feature=20), row(31, "task", "closed", epic=10, feature=20),
        row(32, "bug", "closed", epic=10, feature=20), row(33, "story", "closed", epic=10, feature=21),
        row(34, "story", epic=10),
        row(11, "epic"), row(40, "story", epic=11),
    ]
    counts = count_progress(items)
    assert counts["features"] == {20: {"closed": 2, "total": 3}, 21: {"closed": 1, "total": 1}}
    assert counts["epics"][10] == {"features_closed": 1, "features_total": 2, "issues_closed": 3, "issues_total": 5}
    assert counts["epics"][11] == {"features_closed": 0, "features_total": 0, "issues_closed": 0, "issues_total": 1}
    assert items[3]["state"] == "open"  # inputs are not changed


def test_a_closed_issue_counts_as_closed_whatever_its_reason():
    items = [row(10, "epic"), row(30, "story", "closed", epic=10)]  # reason is not part of the input at all
    assert count_progress(items)["epics"][10]["issues_closed"] == 1


# ── the reader ───────────────────────────────────────────────────────────────


class StatusGateway(FakeGateway):
    def __init__(self) -> None:
        super().__init__()
        self.prs: list[dict] = []
        self.files: dict[int, list[str]] = {}
        self.closed: list[dict] = []
        self.gates: dict[int, bool] = {}

    def add_pr(self, number, body, draft=False, head=None, files=()):
        self.prs.append({"number": number, "body": body, "draft": draft, "head": {"sha": head or f"{number:040x}"}})
        self.files[number] = list(files)

    def add_closed(self, number, labels, body=""):
        self.closed.append({"number": number, "body": body, "labels": [{"name": n} for n in labels]})

    def list_open_prs(self):
        self.calls.append("list_open_prs")
        if "prs" in self.fail:
            raise GatewayError("read open pull requests", "HTTP 500")
        return self.prs

    def pr_files(self, number):
        self.calls.append(f"pr_files {number}")
        if f"files {number}" in self.fail:
            raise GatewayError(f"read files of PR #{number}", "HTTP 500")
        return self.files[number]

    def list_closed_issues(self):
        self.calls.append("list_closed_issues")
        if "closed" in self.fail:
            raise GatewayError("read closed issues", "HTTP 500")
        return self.closed

    def merge_gates_pass(self, number, head):
        self.calls.append(f"gates {number}")
        if f"gates {number}" in self.fail:
            raise GatewayError(f"evaluate merge gates of PR #{number}", "HTTP 500")
        return self.gates.get(number, False)


@pytest.fixture(autouse=True)
def _po(monkeypatch):
    monkeypatch.setenv("DECISION_BLOCKER_PO_LOGIN", PO)
    monkeypatch.delenv("GH_TOKEN", raising=False)


@pytest.fixture
def gw():
    g = StatusGateway()
    g.add_issue(10, ["type:epic"], spec_line())
    g.comment(10, summary())
    g.add_issue(20, ["type:feature"], story_body(feature=20))
    g.comment(20, summary())
    return g


def by_number(result):
    return {i["number"]: i for i in result.fields["items"]}


def test_reader_derives_statuses_from_facts(gw):
    gw.add_issue(30, ["type:story"], story_body())
    gw.comment(30, summary())
    gw.add_issue(31, ["type:story"], story_body(hard="- #99"))
    gw.comment(31, summary())
    gw.states[99] = "open"
    gw.add_issue(32, ["type:story"], story_body())
    gw.comment(32, summary())
    gw.add_issue(33, ["type:bug"], story_body())
    gw.comment(33, summary())
    gw.add_issue(34, ["type:story"], story_body())  # never refined
    gw.add_issue(35, ["type:story"], story_body())
    gw.comment(35, summary())
    gw.comment(35, BLOCKER)
    gw.add_pr(60, "Closes #32", draft=True)
    gw.add_pr(61, "This fixes #33", draft=False)
    gw.add_pr(62, "Resolves #35")
    gw.gates[62] = True
    items = by_number(read_status(gw, "o/r"))
    assert {n: i["status"] for n, i in items.items()} == {
        10: "In progress", 20: "In progress", 30: "Ready to implement", 31: "Refined", 32: "In implementation",
        33: "In review", 34: "Not refined", 35: "Blocked"}  # fmt: skip
    assert all(i["fact"] for i in items.values())


def test_reader_green_gates_give_mergeable_and_gate_is_asked_for_non_draft_prs_only(gw):
    gw.add_issue(30, ["type:story"], story_body())
    gw.comment(30, summary())
    gw.add_pr(60, "Closes #30")
    gw.add_pr(61, "Closes #30", draft=True)
    gw.gates[60] = True
    assert by_number(read_status(gw, "o/r"))[30]["status"] == "Mergeable"
    assert "gates 60" in gw.calls and "gates 61" not in gw.calls


def test_linked_pr_rule_matches_find_linked_pr(gw):
    gw.add_issue(30, ["type:story"], story_body())
    gw.comment(30, summary())
    gw.add_issue(300, ["type:story"], story_body())
    gw.comment(300, summary())
    gw.add_pr(60, "closes #300", draft=True)  # #30 is a prefix of #300: no match for #30
    gw.add_pr(61, "Related to #30")
    items = by_number(read_status(gw, "o/r"))
    assert items[30]["status"] == "Ready to implement"
    assert items[300]["status"] == "In implementation"


def test_unreadable_gate_counts_as_not_passing(gw):
    gw.add_issue(30, ["type:story"], story_body())
    gw.comment(30, summary())
    gw.add_pr(60, "Closes #30")
    gw.fail.add("gates 60")
    assert by_number(read_status(gw, "o/r"))[30]["status"] == "In review"


def test_other_blocker_types_block_until_a_trusted_unblocked_line(gw):
    gw.add_issue(30, ["type:story"], story_body())
    gw.comment(30, summary())
    gw.comment(30, "## Blocked Declaration\nType: dependency\nBlocked by: #99", login="bot", kind="Bot")
    assert by_number(read_status(gw, "o/r"))[30]["status"] == "Blocked"
    gw.comment(30, "Unblocked: #99 is merged", login="stranger", assoc="NONE")
    assert by_number(read_status(gw, "o/r"))[30]["status"] == "Blocked"
    gw.comment(30, "I think this is unblocked now", login=PO)
    assert by_number(read_status(gw, "o/r"))[30]["status"] == "Blocked"
    gw.comment(30, "Unblocked: #99 is merged", login=PO)
    assert by_number(read_status(gw, "o/r"))[30]["status"] == "Ready to implement"


def test_a_later_declarations_unblocked_when_field_does_not_resolve_an_earlier_blocker(gw):
    gw.add_issue(30, ["type:story"], story_body())
    gw.comment(30, summary())
    gw.comment(30, "## Blocked Declaration\nType: external\nBlocked by: a vendor", login=PO)
    gw.comment(30, "## Blocked Declaration\nType: decision\nUnblocked when: the PO answers\n", login=PO)
    gw.comment(30, "Unblocked when: later", login=PO)
    assert by_number(read_status(gw, "o/r"))[30]["status"] == "Blocked"
    gw.comment(30, "/unblock", login=PO)
    assert by_number(read_status(gw, "o/r"))[30]["status"] == "Blocked"  # the external blocker is still open
    gw.comment(30, "Unblocked: the vendor answered", login=PO)
    gw.comment(30, summary())  # a refinement after the /unblock, as the facts require
    assert by_number(read_status(gw, "o/r"))[30]["status"] == "Ready to implement"


def test_unreadable_epic_spec_record_gives_unknown_not_not_refined(gw):
    gw.fail.add(RECORD_OK[:7])
    item = by_number(read_status(gw, "o/r"))[10]
    assert item["status"] == "unknown" and "unreadable" in item["fact"]


@pytest.mark.parametrize("kind", ["external", "conflict", "resource"])
def test_every_other_blocker_type_blocks(gw, kind):
    gw.add_issue(30, ["type:story"], story_body())
    gw.comment(30, summary())
    gw.comment(30, f"## Blocked Declaration\nType: {kind}", login=PO)
    assert by_number(read_status(gw, "o/r"))[30]["status"] == "Blocked"


def test_untrusted_blocker_does_not_block(gw):
    gw.add_issue(30, ["type:story"], story_body())
    gw.comment(30, summary())
    gw.comment(30, "## Blocked Declaration\nType: external", login="stranger")
    assert by_number(read_status(gw, "o/r"))[30]["status"] == "Ready to implement"


def test_epic_with_open_spec_pr_is_in_specification_and_its_unrefined_issues_too():
    g = StatusGateway()
    g.dirs = []
    g.add_issue(10, ["type:epic"], "")
    g.add_issue(20, ["type:feature"], story_body(spec=spec_line()))
    g.add_issue(30, ["type:story"], story_body())
    g.add_pr(70, "spec", files=["specs/10-x/spec.md", "README.md"])
    items = by_number(read_status(g, "o/r"))
    assert [items[n]["status"] for n in (10, 20, 30)] == ["In specification"] * 3
    g.prs.clear()
    items = by_number(read_status(g, "o/r"))
    assert [items[n]["status"] for n in (10, 20, 30)] == ["No spec"] * 3


def test_spec_pr_for_another_epic_does_not_count(gw):
    gw.add_pr(70, "spec", files=["specs/100-x/spec.md", "specs/10-x/notes.md"])
    assert by_number(read_status(gw, "o/r"))[10]["status"] == "In progress"


def test_issue_without_open_parent_epic(gw):
    gw.add_issue(40, ["type:story"], "no parent here")
    gw.add_issue(41, ["type:story"], "no parent here")
    gw.add_pr(60, "Closes #41", draft=True)
    items = by_number(read_status(gw, "o/r"))
    assert items[40]["status"] == "No spec"
    assert items[41]["status"] == "In implementation"


def test_issue_without_known_type_is_not_listed(gw):
    gw.add_issue(50, ["bug"], "x")
    assert 50 not in by_number(read_status(gw, "o/r"))


def test_unreadable_item_facts_give_unknown(gw):
    gw.add_issue(30, ["type:story"], story_body())
    gw.fail.add("comments 30")
    item = by_number(read_status(gw, "o/r"))[30]
    assert item["status"] == "unknown" and "comments on #30" in item["fact"]


def test_unreadable_spec_prs_give_unknown_for_items_under_the_epic(gw):
    gw.add_issue(30, ["type:story"], story_body())
    gw.comment(30, summary())
    gw.add_pr(70, "x", files=[])
    gw.fail.add("files 70")
    result = read_status(gw, "o/r")
    assert result.result == "read"
    assert {i["status"] for i in result.fields["items"]} == {"unknown"}


@pytest.mark.parametrize("fail", ["issues", "prs"])
def test_unreadable_lists_give_exit_3(gw, fail):
    gw.fail.add(fail)
    result = read_status(gw, "o/r")
    assert result.result == "unreadable" and result.exit_code == 3


def test_unreadable_facts_give_exit_3(gw):
    gw.fail.add("specs")
    assert read_status(gw, "o/r").exit_code == 3


def test_progress_is_read_and_unreadable_closed_issues_give_null_progress(gw):
    gw.add_issue(30, ["type:story"], story_body())
    gw.add_closed(31, ["type:task"], story_body())
    gw.add_closed(32, ["type:story"], story_body(feature=21))
    gw.add_closed(21, ["type:feature"], "Parent epic: #10 Epic: x")
    gw.add_closed(99, ["type:story"], "Parent epic: #11 Epic: y")
    progress = read_status(gw, "o/r").fields["progress"]
    assert progress["features"]["20"] == {"closed": 1, "total": 2}
    assert progress["epics"]["10"] == {"features_closed": 1, "features_total": 2, "issues_closed": 2, "issues_total": 3}
    gw.fail.add("closed")
    result = read_status(gw, "o/r")
    assert result.result == "read" and result.fields["progress"] is None


def test_epic_filter_limits_items_and_progress(gw):
    gw.add_issue(11, ["type:epic"], spec_line())
    gw.add_issue(41, ["type:story"], story_body(parent=11))
    result = read_status(gw, "o/r", epic=10)
    assert set(by_number(result)) == {10, 20}
    assert set(result.fields["progress"]["epics"]) == {"10"}


def test_reader_writes_nothing(gw):
    gw.add_issue(30, ["type:story"], story_body())
    gw.add_pr(60, "Closes #30")
    read_status(gw, "o/r")
    assert gw.calls and not [c for c in gw.calls if c.startswith("WRITE")]
    for name in ("post_comment", "set_board_status", "add_label", "remove_label", "create_label"):
        with pytest.raises(AssertionError):
            getattr(gw, name)()
    source = inspect.getsource(status)
    assert not any(word in source for word in ("post_comment", "set_board_status", "add_label", "remove_label"))


def test_gateway_protocol_has_no_write_among_the_status_reads():
    assert hasattr(GhGateway, "list_open_prs") and hasattr(GhGateway, "merge_gates_pass")


# ── the command ──────────────────────────────────────────────────────────────


def run(capsys, gw, *args):
    code = cli.main(["status", "read", *args], gateway_factory=lambda repo: gw)
    out, err = capsys.readouterr()
    return code, json.loads(out), err


def test_command_prints_one_json_line_and_one_log_line_per_item(capsys, gw):
    gw.add_issue(30, ["type:story"], story_body())
    gw.comment(30, summary())
    code, payload, err = run(capsys, gw, "--repo", "o/r")
    assert code == 0
    assert payload["block"] == "status" and payload["operation"] == "read" and payload["result"] == "read"
    assert [(i["number"], i["type"], i["status"]) for i in payload["items"]] == [
        (10, "epic", "In progress"), (20, "feature", "In progress"), (30, "story", "Ready to implement")]
    assert set(payload["items"][0]) == {"number", "type", "status", "fact"}
    assert set(payload["progress"]) == {"epics", "features"}
    assert [line for line in err.splitlines() if line.startswith("[STATUS] #30 story Ready to implement:")]
    assert len([line for line in err.splitlines() if line.startswith("[STATUS] #")]) == 3


def test_command_exit_3_when_unreadable(capsys, gw):
    gw.fail.add("prs")
    code, payload, err = run(capsys, gw, "--repo", "o/r")
    assert code == 3 and payload["result"] == "unreadable" and payload["ok"] is False


def test_command_invalid_arguments(capsys, gw):
    code = cli.main(["status", "read"], gateway_factory=lambda repo: gw)
    assert code == 2
    assert json.loads(capsys.readouterr().out)["block"] == "status"
    assert cli.main(["status"], gateway_factory=lambda repo: gw) == 2
    capsys.readouterr()
    assert cli.main(["status", "read", "--repo", "bad"], gateway_factory=lambda repo: gw) == 2


# ── the real gateway's merge gate read ───────────────────────────────────────


class _Gate:
    def __init__(self, passed, head):
        self.passed, self.head = passed, head


def test_gh_gateway_merge_gates_use_the_script_evaluation(monkeypatch):
    from headless_pr_workflow.dispatcher import gateway as gateway_module

    class Module:
        calls = []

        @staticmethod
        def evaluate(pr, repo):
            Module.calls.append((pr, repo))
            if pr == 3:
                raise ValueError("GitHub did not return a valid current PR head SHA")
            return _Gate(pr == 1, "abcdef1")

    monkeypatch.setattr(gateway_module, "_merge_gate_module", lambda: Module)
    gw = GhGateway("o/r")
    assert gw.merge_gates_pass(1, "abcdef1" + "0" * 33) is True
    assert gw.merge_gates_pass(1, "1234567" + "0" * 33) is False  # the gates were read for another head
    assert gw.merge_gates_pass(2, "abcdef1" + "0" * 33) is False
    with pytest.raises(GatewayError):
        gw.merge_gates_pass(3, "abcdef1")
    assert Module.calls[0] == (1, "o/r")


def test_the_merge_gate_script_loads_as_a_module():
    from headless_pr_workflow.dispatcher.gateway import _merge_gate_module

    assert callable(_merge_gate_module().evaluate)


# ── Refinement failed: parity with the scheduler's gate (issue #476) ──────────


def _gate_and_status(gw, number):
    from headless_pr_workflow.dispatcher.facts import read_facts
    from headless_pr_workflow.dispatcher.scheduler import _refine_gate

    out = read_facts(gw, "")
    epic = out.fields["epics"][0]
    entry = next(e for e in out.fields["issues"] if e["number"] == number)
    return _refine_gate(entry, epic["spec"]), by_number(read_status(gw, "o/r"))[number]["status"]


@pytest.mark.parametrize("comments, failed", [
    ([summary("fail: spec_record")], True),
    ([summary("unreadable")], True),
    ([summary()], False),
    ([summary("fail: x"), summary()], False),
    ([summary("fail: x"), "/unblock"], True),  # an /unblock answers a blocker; there is none
    ([summary("fail: x"), BLOCKER, "/unblock"], False),
    ([summary(), BLOCKER, "/unblock", summary("fail: x")], True),
    ([], False),
])
@pytest.mark.parametrize("kind", ["story", "feature"])
def test_refinement_failed_holds_exactly_when_the_scheduler_gate_says_last_refinement_failed(gw, comments, failed, kind):
    gw.add_issue(30, [f"type:{kind}"], story_body())
    for text in comments:
        gw.comment(30, text)
    gate, got = _gate_and_status(gw, 30)
    assert (gate == "last-refinement-failed") is failed
    assert (got == "Refinement failed") is failed


def test_a_summary_older_than_the_spec_commit_is_not_a_refinement_failure(gw):
    gw.add_issue(30, ["type:story"], story_body())
    gw.comment(30, summary("fail: x"))
    gw.commits[SPEC_PATH] = {"sha": "a" * 40, "date": "2026-12-01T00:00:00Z"}
    gate, got = _gate_and_status(gw, 30)
    assert gate != "last-refinement-failed" and got == "Not refined"


def test_a_failed_epic_refinement_gives_the_epic_refinement_failed(gw):
    gw.comment(10, summary("fail: x"))
    assert by_number(read_status(gw, "o/r"))[10]["status"] == "Refinement failed"


def test_a_blocker_beats_a_failed_refinement(gw):
    gw.add_issue(30, ["type:story"], story_body())
    gw.comment(30, summary("fail: x"))
    gw.comment(30, BLOCKER)
    assert by_number(read_status(gw, "o/r"))[30]["status"] == "Blocked"
