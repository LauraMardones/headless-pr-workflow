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
