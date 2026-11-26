"""A session that ends without a PR because of an open blocker (issue #533).

Unit tests for the rule in ``dispatcher/session_blocker.py`` and the real
``scripts/dispatcher-invoke.sh`` driven with the fakes in ``tests/dispatcher_fakes.py``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from headless_pr_workflow.dispatcher.cli import main as dispatcher_main
from headless_pr_workflow.dispatcher.gateway import GatewayError
from headless_pr_workflow.dispatcher.session_blocker import open_session_blocker, read_session_blocker
from tests.dispatcher_fakes import HAVE_TOOLS, Dispatcher

needs_tools = pytest.mark.skipif(not HAVE_TOOLS, reason="requires bash, jq and timeout")

STORY = 500
PO = "LauraMardones"


def blocked(kind: str = "dependency", by: str = "Claude Code (implementer, Sonnet)", at: str = "2026-11-09T05:33:22Z",
            login: str = "member", association: str = "MEMBER", url: str = "https://x/1") -> dict[str, Any]:
    return {"user": {"login": login}, "author_association": association, "created_at": at, "html_url": url,
            "body": f"## Blocked Declaration\n\nType: {kind}\nDeclared by: {by}\nUnblocked when: a thing\n"}


def reply(body: str, at: str, login: str = "member", association: str = "MEMBER") -> dict[str, Any]:
    return {"user": {"login": login}, "author_association": association, "created_at": at, "body": body}


class Comments:
    def __init__(self, comments: list[dict[str, Any]] | Exception) -> None:
        self.comments = comments

    def list_comments(self, number: int) -> list[dict[str, Any]]:
        if isinstance(self.comments, Exception):
            raise self.comments
        return self.comments


def read(comments: list[dict[str, Any]] | Exception) -> dict[str, Any]:
    return read_session_blocker(Comments(comments), STORY, env={"DECISION_BLOCKER_PO_LOGIN": PO}).to_dict()  # type: ignore[arg-type]


@pytest.mark.parametrize("kind", ["dependency", "decision", "external", "conflict", "resource"])
def test_ac5_an_open_session_blocker_is_reported_with_type_declarer_and_url(kind):
    out = read([blocked(kind)])

    assert out["result"] == "read" and out["ok"] is True and out["outcome"] == "open_blocker"
    assert (out["type"], out["declared_by"], out["url"], out["created_at"]) == (
        kind, "Claude Code (implementer, Sonnet)", "https://x/1", "2026-11-09T05:33:22Z")


@pytest.mark.parametrize("by", ["dispatcher", "codex-review-bookkeeping (GitHub Actions)"])
def test_ac4_automation_declarations_are_not_session_blockers(by):
    assert read([blocked("external", by)])["outcome"] == "none"


def test_ac4_a_person_whose_name_starts_like_automation_still_counts():
    assert read([blocked("dependency", "dispatcher-fan")])["outcome"] == "open_blocker"


def test_ac4_a_po_unblock_line_after_the_blocker_lifts_it():
    comments = [blocked(), reply("/unblock", "2026-11-09T06:00:00Z", login=PO, association="OWNER")]

    assert read(comments)["outcome"] == "none"


def test_ac4_an_unblock_line_before_the_blocker_does_not_lift_it():
    comments = [reply("/unblock", "2026-11-09T05:00:00Z", login=PO, association="OWNER"), blocked()]

    assert read(comments)["outcome"] == "open_blocker"


def test_ac4_an_unblock_line_from_someone_else_does_not_lift_it():
    assert read([blocked(), reply("/unblock", "2026-11-09T06:00:00Z", login="other")])["outcome"] == "open_blocker"


def test_ac4_a_trusted_unblocked_comment_after_the_blocker_lifts_it():
    comments = [blocked(), reply("Unblocked: the dependency closed.", "2026-11-09T06:00:00Z")]

    assert read(comments)["outcome"] == "none"


def test_ac4_an_untrusted_unblocked_comment_does_not_lift_it():
    comments = [blocked(), reply("Unblocked: done", "2026-11-09T06:00:00Z", login="stranger", association="NONE")]

    assert read(comments)["outcome"] == "open_blocker"


def test_ac4_a_blocker_from_an_untrusted_account_is_ignored():
    assert read([blocked(login="stranger", association="NONE")])["outcome"] == "none"


def test_ac3_an_older_open_blocker_survives_a_later_session_summary():
    comments = [blocked(at="2026-11-13T05:00:00Z"), reply("## Session Summary\nCommand: implement\n", "2026-11-13T06:00:00Z")]

    assert read(comments)["outcome"] == "open_blocker"


def test_the_dispatchers_later_blocker_does_not_hide_an_open_session_blocker():
    comments = [blocked(at="2026-11-09T05:33:22Z"), blocked("external", "dispatcher", at="2026-11-09T05:33:28Z")]

    assert read(comments)["created_at"] == "2026-11-09T05:33:22Z"


def test_the_newest_open_declaration_is_reported():
    comments = [blocked("dependency", at="2026-11-09T05:00:00Z"), blocked("decision", "Codex", at="2026-11-09T07:00:00Z")]

    out = read(comments)
    assert (out["type"], out["declared_by"]) == ("decision", "Codex")


def test_ac5_a_read_failure_is_unreadable_and_names_no_blocker():
    out = read(GatewayError("list comments", "HTTP 502"))

    assert out["result"] == "unreadable" and out["ok"] is False and out["outcome"] == "none"


def test_a_comment_without_a_declared_by_line_is_not_a_session_blocker():
    comment = blocked()
    comment["body"] = "## Blocked Declaration\n\nType: dependency\n"

    assert open_session_blocker(_facts(), [comment]) is None


def _facts():
    from headless_pr_workflow.dispatcher.facts import _Facts

    return _Facts(Comments([]), {"DECISION_BLOCKER_PO_LOGIN": PO})  # type: ignore[arg-type]


def test_the_cli_prints_one_json_line_and_exits_zero(capsys):
    code = dispatcher_main(["session-blocker", "read", "--repo", "o/r", "--issue", str(STORY)],
                           gateway_factory=lambda repo: Comments([blocked()]))  # type: ignore[arg-type,return-value]

    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["block"] == "session-blocker" and out["outcome"] == "open_blocker"


# ── the dispatcher run (AC 1, 2, 3, 4, 6) ────────────────────────────────────

def po_events(d: Dispatcher) -> list[dict]:
    return [e for e in d.queued_events if e["event"] == "po_blocker"]


def session_blocks(d: Dispatcher, kind: str, by: str = "Claude Code (implementer, Sonnet)") -> None:
    body = f"## Blocked Declaration\\n\\nType: {kind}\\nDeclared by: {by}\\nUnblocked when: a thing\\n"
    d.on_session("implement", f"""f="$MOCK_DIR/comments_{STORY}.json"; [[ -f "$f" ]] || echo '[]' > "$f"
jq '. + [{{"user":{{"login":"hpw-bot"}},"author_association":"COLLABORATOR","body":"{body}","created_at":"2026-10-13T11:00:00Z"}}]' "$f" > "$f.tmp" && mv "$f.tmp" "$f"
""")


@needs_tools
@pytest.mark.parametrize("kind", ["dependency", "decision"])
def test_ac1_ac2_ac6_a_session_blocker_without_a_pr_ends_quietly(tmp_path, kind):
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "Ready to implement")
    session_blocks(d, kind)

    proc = d.run_issue(STORY)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {STORY}"]
    assert d.posted(STORY) == []
    assert f"[HANDOFF] #{STORY}: /implement ended without a PR; open Blocked Declaration (Type: {kind}) from " \
           "Claude Code (implementer, Sonnet): " in proc.stdout
    assert "no dispatcher blocker" in proc.stdout and "No linked open PR" not in proc.stderr + proc.stdout
    assert po_events(d) == []
    expected = ["decision_blocker"] if kind == "decision" else []
    assert [e["event"] for e in d.queued_events] == expected


@needs_tools
def test_ac3_an_open_blocker_from_before_the_session_is_enough(tmp_path):
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "Ready to implement")
    d.add_comment(STORY, "## Blocked Declaration\n\nType: dependency\nDeclared by: Claude Code (implementer, Sonnet)\n"
                         "Unblocked when: a thing\n", at="2026-11-13T05:00:00Z")
    d.add_comment(STORY, "## Session Summary\nCommand: implement\n", at="2026-11-13T06:00:00Z")

    proc = d.run_issue(STORY)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.posted(STORY) == [] and po_events(d) == []
    assert "no dispatcher blocker" in proc.stdout


@needs_tools
@pytest.mark.parametrize("setup", ["none", "dispatcher", "bookkeeping", "lifted", "untrusted"])
def test_ac4_without_an_open_session_blocker_the_dispatcher_blocks_as_before(tmp_path, setup):
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "Ready to implement")
    declaration = "## Blocked Declaration\n\nType: dependency\nDeclared by: {}\nUnblocked when: a thing\n"
    if setup == "dispatcher":
        d.add_comment(STORY, declaration.format("dispatcher"))
    if setup == "bookkeeping":
        d.add_comment(STORY, declaration.format("codex-review-bookkeeping (GitHub Actions)"))
    if setup == "lifted":
        d.add_comment(STORY, declaration.format("Claude Code"))
        d.add_comment(STORY, "/unblock", login=PO)
    if setup == "untrusted":
        d.add_comment(STORY, declaration.format("Claude Code"), login="stranger")
        comments = d._read(f"comments_{STORY}.json")
        comments[-1]["author_association"] = "NONE"
        d._write(f"comments_{STORY}.json", comments)

    proc = d.run_issue(STORY)

    assert proc.returncode != 0
    assert "Error: No linked open PR found" in proc.stderr + proc.stdout
    blockers = [b for b in d.posted(STORY) if "/implement completes successfully" in b]
    assert len(blockers) == 1 and len(po_events(d)) == 1


# ── the one open-blocker rule (issue #646) ───────────────────────────────────
# ``decide_open_blocker`` decides every case the copies listed under ``open_blocker`` in
# ``rules.py`` decide (the parity cases of ``tests/test_dispatcher_parity_in_flight.py``), with
# the divergences resolved: the declaration types are a caller input (po-dependency, po-decision),
# the PO's /unblock and a member's Unblocked comment lift a non-decision declaration
# (dependency-then-po-unblock, dependency-then-member-unblocked), an automation declaration blocks
# unless the caller leaves its declarer out (automation-dependency, #556 → Decisions, case (7)),
# and an unreadable read fails closed (comments-unreadable).

from headless_pr_workflow.dispatcher.facts import _Facts  # noqa: E402
from headless_pr_workflow.dispatcher.session_blocker import (  # noqa: E402
    AUTOMATION_DECLARERS,
    decide_open_blocker,
    read_open_blocker,
)
from headless_pr_workflow.dispatcher.status import other_blocker_open  # noqa: E402

BOT = "hpw-bot"
HEAD = "abc1234"
FOUR = ("dependency", "external", "conflict", "resource")
T0, T1, T2 = "2026-11-09T05:00:00Z", "2026-11-09T06:00:00Z", "2026-11-09T07:00:00Z"


def po(body: str, at: str = T1) -> dict[str, Any]:
    return reply(body, at, login=PO, association="OWNER")


def automation(kind: str = "dependency", at: str = T0, marker: str = "") -> dict[str, Any]:
    comment = blocked(kind, "dispatcher", at, login=BOT, association="NONE")
    comment["user"]["type"] = "Bot"
    comment["body"] += marker
    return comment


def rule(comments: list[dict[str, Any]], **inputs: Any) -> dict[str, Any] | None:
    return decide_open_blocker(_facts(), comments, **inputs)


@pytest.mark.parametrize("kind", ["decision", "dependency", "external", "conflict", "resource"])
def test_646_each_declaration_type_is_open_with_the_default_types(kind):
    found = rule([blocked(kind, at=T0)])

    assert found == {"type": kind, "declared_by": "Claude Code (implementer, Sonnet)", "url": "https://x/1",
                     "created_at": T0}


def test_646_po_dependency_the_types_are_a_caller_input():
    # Divergence po-dependency: latest_decision_blocker_json reads only decision declarations.
    comments = [blocked("dependency", login=PO, association="OWNER")]

    assert rule(comments) is not None and rule(comments, types=FOUR) is not None
    assert rule(comments, types=("decision",)) is None


def test_646_po_decision_the_four_other_types_leave_a_decision_out():
    # Divergence po-decision: other_blocker_open and dependency_blocker read no decision declaration.
    comments = [blocked("decision", login=PO, association="OWNER")]

    assert rule(comments) is not None and rule(comments, types=("decision",)) is not None
    assert rule(comments, types=FOUR) is None and rule(comments, types=("dependency",)) is None


@pytest.mark.parametrize("kind", ["decision", "dependency", "external", "conflict", "resource"])
def test_646_dependency_then_po_unblock_a_later_po_unblock_line_lifts_every_type(kind):
    assert rule([blocked(kind, at=T0), po("Go ahead.\n  /unblock  ")]) is None


@pytest.mark.parametrize("body,at,login", [
    ("Please /unblock this.", T1, PO),   # the word in prose
    ("/UNBLOCK", T1, PO),                # not the exact line
    ("/unblock", T1, "member"),          # not the PO
    ("/unblock", "2026-11-09T04:00:00Z", PO),  # before the declaration
])  # fmt: skip
def test_646_an_unblock_that_is_not_the_pos_later_line_does_not_lift(body, at, login):
    assert rule([blocked(at=T0), reply(body, at, login=login, association="OWNER")]) is not None


@pytest.mark.parametrize("kind", ["dependency", "external", "conflict", "resource"])
def test_646_dependency_then_member_unblocked_lifts_a_non_decision_declaration(kind):
    assert rule([blocked(kind, at=T0), reply("Unblocked: the dependency is closed.", T1)]) is None


def test_646_an_unblocked_comment_does_not_lift_a_decision_declaration():
    # PROJECT-STATUS.md → Resolution 1: only the PO's /unblock line resolves a decision blocker.
    # open_session_blocker lifts it today; the one rule follows the contract (#649 switches the caller).
    assert rule([blocked("decision", at=T0), reply("Unblocked: done.", T1)]) is not None


@pytest.mark.parametrize("body,login,association", [
    ("Unblocked: done.", "stranger", "NONE"),             # untrusted
    ("## Blocked Declaration\n\nUnblocked when: x", "member", "MEMBER"),  # a declaration's own field
])  # fmt: skip
def test_646_an_untrusted_or_declaration_unblocked_line_does_not_lift(body, login, association):
    assert rule([blocked(at=T0), reply(body, T1, login=login, association=association)]) is not None


def test_646_automation_dependency_blocks_unless_the_caller_leaves_its_declarer_out():
    # Divergence automation-dependency, resolved by the PO on 2026-11-16 (#556 → Decisions, case (7)).
    comments = [automation()]

    assert rule(comments) is not None and rule(comments)["declared_by"] == "dispatcher"
    assert rule(comments, exclude_declarers=AUTOMATION_DECLARERS) is None
    assert rule([blocked(by="codex-review-bookkeeping (GitHub Actions)")], exclude_declarers=AUTOMATION_DECLARERS) is None
    assert rule([blocked(by="dispatcher-fan")], exclude_declarers=AUTOMATION_DECLARERS) is not None


def test_646_an_untrusted_declaration_does_not_count():
    assert rule([blocked(login="stranger", association="NONE")]) is None


def test_646_the_newest_open_declaration_is_reported_and_a_lifted_one_is_skipped():
    comments = [blocked("decision", at=T0, url="https://x/0"), blocked("dependency", at=T1, url="https://x/1"),
                reply("Unblocked: the dependency is closed.", T2)]

    assert rule(comments)["url"] == "https://x/0"  # the decision declaration stays open
    assert rule(comments + [blocked("external", at="2026-11-09T08:00:00Z", url="https://x/2")])["url"] == "https://x/2"


def test_646_the_conflict_round_head_filter_drops_a_bot_declaration_for_another_head():
    stale = automation(marker="\n<!-- hpw:dispatcher action=conflict-round pr=900 head=def5678 -->")
    current = automation(marker=f"\n<!-- hpw:dispatcher action=conflict-round pr=900 head={HEAD.upper()} -->")

    assert rule([stale], bot=BOT, head=HEAD) is None
    assert rule([current], bot=BOT, head=HEAD) is not None
    assert rule([automation()], bot=BOT, head=HEAD) is not None  # no marker: not stale
    assert rule([stale]) is not None  # no head given: no filter


def test_646_the_bot_login_is_trusted_as_in_the_conflict_round():
    comment = blocked(login=BOT, association="NONE")

    assert rule([comment]) is None and rule([comment], bot=BOT, head=HEAD) is not None


def test_646_a_passing_refine_summary_after_a_dependency_declaration_lifts_it():
    # facts.dependency_blocker (#529): a later passing refine Session Summary lifts a dependency declaration only.
    assert rule([blocked("dependency", at=T0)], refine_pass_at=T1) is None
    assert rule([blocked("dependency", at=T1)], refine_pass_at=T0) is not None
    assert rule([blocked("external", at=T0)], refine_pass_at=T1) is not None


def test_646_a_declaration_without_a_type_line_counts_only_for_any_type():
    comment = blocked()
    comment["body"] = "## Blocked Declaration\n\nUnblocked when: a thing\n"

    assert rule([comment]) is None
    assert rule([comment], types=None) == {"type": None, "declared_by": None, "url": "https://x/1",
                                          "created_at": "2026-11-09T05:33:22Z"}


def test_646_the_heading_must_start_a_line():
    comment = blocked()
    comment["body"] = "Quoted: ## Blocked Declaration\nType: dependency\nDeclared by: member\n"

    assert rule([comment]) is None


CROSS = {
    "po-dependency": [blocked("dependency", login=PO, association="OWNER")],
    "po-decision": [blocked("decision", login=PO, association="OWNER")],
    "dependency-then-po-unblock": [blocked(at=T0), po("/unblock")],
    "dependency-then-member-unblocked": [blocked(at=T0), reply("Unblocked: the dependency is closed.", T1)],
    "automation-dependency": [automation()],
    "external-then-dependency-unblocked": [blocked("external", at=T0), blocked("dependency", at=T1),
                                           reply("Unblocked: done.", T2)],
    "unblock-before": [po("/unblock", T0), blocked(at=T1)],
    "untrusted": [blocked(login="stranger", association="NONE")],
    "none": [reply("A comment.", T0)],
}


@pytest.mark.parametrize("case", sorted(CROSS))
def test_646_the_rule_decides_as_each_python_copy_with_that_copys_inputs(case):
    comments, facts = CROSS[case], _facts()

    assert (rule(comments, types=FOUR, po_unblock=False) is not None) is other_blocker_open(facts, comments)
    assert (rule(comments, types=FOUR) is not None) is other_blocker_open(facts, comments, po_unblock=True)
    assert (rule(comments, types=("dependency",)) is not None) is (facts.dependency_blocker(comments, {})["state"] == "open")
    assert rule(comments, exclude_declarers=AUTOMATION_DECLARERS) == open_session_blocker(facts, comments)


# ── the CLI: session-blocker decide | open (ADR-014 call contract) ───────────


def run(argv: list[str], capsys, gateway: Any = None) -> tuple[int, dict[str, Any]]:
    code = dispatcher_main(argv, gateway_factory=lambda repo: gateway)  # type: ignore[arg-type,return-value]
    return code, json.loads(capsys.readouterr().out)


def test_646_decide_reports_the_open_declaration(tmp_path, capsys):
    path = tmp_path / "comments.json"
    path.write_text(json.dumps([automation()]))

    code, out = run(["session-blocker", "decide", "--input", str(path), "--po-login", PO], capsys)
    assert code == 0 and out["ok"] is True and out["block"] == "session-blocker" and out["operation"] == "decide"
    assert out["result"] == "read" and out["outcome"] == "open" and out["declared_by"] == "dispatcher"

    code, out = run(["session-blocker", "decide", "--input", str(path), "--exclude-declarer", "dispatcher"], capsys)
    assert code == 0 and out["outcome"] == "none"


def test_646_decide_passes_each_caller_input(tmp_path, capsys):
    path = tmp_path / "comments.json"
    path.write_text(json.dumps([blocked("decision", at=T0), po("/unblock", T1)]))
    base = ["session-blocker", "decide", "--input", str(path)]

    assert run(base + ["--po-login", PO], capsys)[1]["outcome"] == "none"
    assert run(base + ["--po-login", "someone-else"], capsys)[1]["outcome"] == "open"
    assert run(base + ["--po-login", "someone-else", "--type", "dependency"], capsys)[1]["outcome"] == "none"


@pytest.mark.parametrize("content", ["not json", '{"a": 1}', "[1, 2]"])
def test_646_decide_fails_closed_on_unreadable_input(tmp_path, capsys, content):
    path = tmp_path / "comments.json"
    path.write_text(content)

    code, out = run(["session-blocker", "decide", "--input", str(path)], capsys)
    assert code == 3 and out["ok"] is False and out["result"] == "unreadable" and out["outcome"] == "unreadable"


def test_646_decide_fails_closed_on_a_missing_input_file(tmp_path, capsys):
    code, out = run(["session-blocker", "decide", "--input", str(tmp_path / "absent.json")], capsys)
    assert code == 3 and out["outcome"] == "unreadable"


def test_646_open_reads_the_story_and_fails_closed_when_the_comments_are_unreadable(capsys):
    argv = ["session-blocker", "open", "--repo", "o/r", "--issue", str(STORY), "--po-login", PO]

    code, out = run(argv, capsys, Comments([blocked()]))
    assert code == 0 and out["operation"] == "open" and out["outcome"] == "open" and out["type"] == "dependency"

    code, out = run(argv, capsys, Comments(GatewayError("list comments", "HTTP 502")))
    assert code == 3 and out["result"] == "unreadable" and out["outcome"] == "unreadable"


def test_646_read_open_blocker_uses_the_po_login_of_its_environment():
    comments = Comments([blocked(at=T0), po("/unblock")])

    assert read_open_blocker(comments, STORY, env={"DECISION_BLOCKER_PO_LOGIN": PO}).fields["outcome"] == "none"  # type: ignore[arg-type]
    assert read_open_blocker(comments, STORY, env={}).fields["outcome"] == "open"  # type: ignore[arg-type]


@pytest.mark.parametrize("extra", [["--type", "blocked"], ["--bot", BOT], ["--head", HEAD], ["--any-type", "--type", "decision"]])
def test_646_invalid_inputs_exit_two(tmp_path, capsys, extra):
    code, out = run(["session-blocker", "decide", "--input", str(tmp_path / "x.json")] + extra, capsys)
    assert code == 2 and out["result"] == "invalid"


def test_646_open_needs_a_repository(capsys):
    code, out = run(["session-blocker", "open", "--issue", str(STORY)], capsys)
    assert code == 2 and out["result"] == "invalid"
