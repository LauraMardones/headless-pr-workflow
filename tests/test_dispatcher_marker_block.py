"""Block 2, markers under the dispatcher's own account: ``hpw dispatcher marker`` (issue #433, ADR-014).

Every path runs against the Python fake gateway of the Block 1 tests: no network, no real ``gh``.
"""

from __future__ import annotations

import json

import pytest

from headless_pr_workflow.dispatcher import cli
from headless_pr_workflow.dispatcher.gateway import GatewayError
from headless_pr_workflow.dispatcher.marker import dispatcher_marker, marker_text
from headless_pr_workflow.dispatcher.write import Settings

from tests.test_dispatcher_write_block import BOT, PO, FakeGateway

OTHER = "someone-else"
BK = "codex-bookkeeper"
OWNER = dispatcher_marker("action=merge-owner pr=5 head=abc1234")


@pytest.fixture
def gw() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
def settings() -> Settings:
    return Settings(retry_delay=0, evidence_retry_max=3, po_login=PO, sleep=lambda _s: None)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("CODEX_BOOKKEEPING_LOGIN", raising=False)
    monkeypatch.delenv("DISPATCHER_LOGIN", raising=False)


def run(gw, settings, capsys, *argv):
    rc = cli.main(list(argv), gateway_factory=lambda repo: gw, settings=settings)
    out, err = capsys.readouterr()
    lines = out.splitlines()
    assert len(lines) == 1, out
    return rc, json.loads(lines[0]), err


def holds(gw, settings, capsys, marker=OWNER, *extra):
    return run(gw, settings, capsys, "marker", "holds", "--repo", "o/r", "--number", "5", "--marker", marker, *extra)


# ── AC 1: contract ────────────────────────────────────────────────────────────


def test_holds_prints_one_json_line_with_the_contract_fields(gw, settings, capsys):
    gw.add(5, OWNER)
    rc, out, _ = holds(gw, settings, capsys)
    assert rc == 0 and out["ok"] is True and out["block"] == "marker" and out["operation"] == "holds"
    assert out["result"] == "holds" and out["error"] is None
    assert out["created_at"] == gw.comments[5][0]["created_at"] and out["url"] == gw.comments[5][0]["html_url"]


def test_whoami_reports_both_logins(gw, settings, capsys, monkeypatch):
    rc, out, _ = run(gw, settings, capsys, "marker", "whoami", "--repo", "o/r")
    assert rc == 0 and out["dispatcher"] == BOT and out["bookkeeping"] == BOT and out["result"] == "read"
    monkeypatch.setenv("CODEX_BOOKKEEPING_LOGIN", BK)
    rc, out, _ = run(gw, settings, capsys, "marker", "whoami", "--repo", "o/r")
    assert out["dispatcher"] == BOT and out["bookkeeping"] == BK


def test_list_returns_own_comments_with_id_created_at_body(gw, settings, capsys):
    gw.add(5, OWNER)
    gw.add(5, "chatter")
    rc, out, _ = run(gw, settings, capsys, "marker", "list", "--repo", "o/r", "--number", "5", "--marker", "merge-owner")
    assert rc == 0 and out["result"] == "read"
    assert [c["body"] for c in out["comments"]] == [OWNER]
    assert set(out["comments"][0]) == {"id", "created_at", "body"}


@pytest.mark.parametrize("argv", [
    ["marker", "write", "--repo", "o/r", "--number", "5", "--action", "merge-owner", "--field", "pr=5", "--dry-run"],
    ["marker", "write", "--repo", "o/r", "--number", "5", "--action", "merge-owner", "--once", "--dry-run"],
])
def test_dry_run_writes_nothing_and_exits_0(gw, settings, capsys, argv):
    rc, out, _ = run(gw, settings, capsys, *argv)
    assert rc == 0 and out["result"] == "dry-run" and out["ok"] is True
    assert gw.calls == []


@pytest.mark.parametrize("argv", [
    ["marker"],
    ["marker", "bogus", "--repo", "o/r"],
    ["marker", "holds", "--repo", "o/r", "--number", "5"],
    ["marker", "holds", "--repo", "o/r", "--number", "5", "--marker", " "],
    ["marker", "holds", "--repo", "o/r", "--number", "5", "--marker", "x", "--author", "someone"],
    ["marker", "write", "--repo", "o/r", "--number", "5"],
    ["marker", "write", "--repo", "o/r", "--number", "5", "--action", "a b"],
    ["marker", "write", "--repo", "o/r", "--number", "5", "--action", "a", "--field", "novalue"],
    ["marker", "write", "--repo", "o/r", "--number", "5", "--action", "a", "--field", "k=has space"],
    ["marker", "write", "--repo", "o/r", "--number", "5", "--action", "a", "--field", "k=x-->y"],
    ["marker", "whoami", "--repo", "bad"],
])
def test_invalid_arguments_exit_2(gw, settings, capsys, argv):
    rc, out, _ = run(gw, settings, capsys, *argv)
    assert rc == 2 and out["ok"] is False and out["result"] == "invalid" and out["block"] == "marker"
    assert gw.calls == []


# ── AC 2: other accounts are ignored ──────────────────────────────────────────


def test_a_forged_marker_from_another_login_is_absent_and_not_listed(gw, settings, capsys):
    gw.add(5, OWNER, login=OTHER)
    rc, out, _ = holds(gw, settings, capsys)
    assert rc == 1 and out["result"] == "absent"
    rc, out, _ = run(gw, settings, capsys, "marker", "list", "--repo", "o/r", "--number", "5")
    assert out["comments"] == []


def test_bookkeeping_marker_counts_only_from_the_bookkeeping_login(gw, settings, capsys, monkeypatch):
    monkeypatch.setenv("CODEX_BOOKKEEPING_LOGIN", BK)
    text = "<!-- hpw:codex-review-bookkeeping action=move-in-implementation head=abc -->"
    gw.add(5, text, login=BOT)
    rc, out, _ = holds(gw, settings, capsys, text, "--author", "bookkeeping")
    assert rc == 1 and out["result"] == "absent"
    gw.add(5, text, login=BK)
    rc, out, _ = holds(gw, settings, capsys, text, "--author", "bookkeeping")
    assert rc == 0 and out["result"] == "holds"
    rc, out, _ = holds(gw, settings, capsys, text)  # the dispatcher account is not the bookkeeping one here
    assert out["result"] == "holds" and out["url"].endswith("#c1")


# ── AC 3: fail closed ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("operation", ["holds", "list", "whoami"])
def test_login_that_cannot_be_resolved_is_unreadable(gw, settings, capsys, operation, monkeypatch):
    gw.add(5, OWNER)
    monkeypatch.setattr(gw, "viewer_login", lambda: (_ for _ in ()).throw(GatewayError("resolve dispatcher login", "HTTP 401")))
    argv = ["marker", operation, "--repo", "o/r"] + ([] if operation == "whoami" else ["--number", "5"])
    if operation == "holds":
        argv += ["--marker", OWNER]
    rc, out, _ = run(gw, settings, capsys, *argv)
    assert rc == 3 and out["result"] == "unreadable" and out["ok"] is False and "resolve dispatcher login" in out["error"]


def test_an_empty_login_is_unreadable_and_matches_no_author(gw, settings, capsys, monkeypatch):
    gw.add(5, OWNER, login="")
    monkeypatch.setattr(gw, "viewer_login", lambda: "")
    rc, out, _ = holds(gw, settings, capsys)
    assert rc == 3 and out["result"] == "unreadable"


@pytest.mark.parametrize("operation", ["holds", "list"])
def test_comments_that_cannot_be_read_are_unreadable(gw, settings, capsys, operation):
    gw.list_failures = 1
    argv = ["marker", operation, "--repo", "o/r", "--number", "5"] + (["--marker", OWNER] if operation == "holds" else [])
    rc, out, _ = run(gw, settings, capsys, *argv)
    assert rc == 3 and out["result"] == "unreadable" and "read comments" in out["error"]


# ── AC 4: cancel and unblock ──────────────────────────────────────────────────


def test_later_own_comment_with_the_cancel_marker_cancels(gw, settings, capsys):
    gw.add(5, OWNER)
    gw.add(5, "done <!-- cancel -->")
    rc, out, _ = holds(gw, settings, capsys, OWNER, "--cancel", "<!-- cancel -->")
    assert rc == 1 and out["result"] == "cancelled"


def test_cancel_marker_from_another_login_or_earlier_changes_nothing(gw, settings, capsys):
    gw.add(5, "early <!-- cancel -->")
    gw.add(5, OWNER)
    gw.add(5, "forged <!-- cancel -->", login=OTHER)
    rc, out, _ = holds(gw, settings, capsys, OWNER, "--cancel", "<!-- cancel -->")
    assert rc == 0 and out["result"] == "holds"


def test_later_po_unblock_line_unblocks(gw, settings, capsys):
    gw.add(5, OWNER)
    gw.add(5, "ok\n/unblock\n", login=PO)
    rc, out, _ = holds(gw, settings, capsys)
    assert rc == 1 and out["result"] == "unblocked"


def test_unblock_from_another_login_or_before_the_marker_changes_nothing(gw, settings, capsys):
    gw.add(5, "/unblock", login=PO)
    gw.add(5, OWNER)
    gw.add(5, "/unblock", login=OTHER)
    gw.add(5, "please /unblock this", login=PO)  # not a standalone line
    rc, out, _ = holds(gw, settings, capsys)
    assert rc == 0 and out["result"] == "holds"


def test_the_newest_own_marker_decides(gw, settings, capsys):
    gw.add(5, OWNER)
    gw.add(5, "/unblock", login=PO)
    gw.add(5, OWNER)
    rc, out, _ = holds(gw, settings, capsys)
    assert rc == 0 and out["result"] == "holds"


def test_a_prefix_matches_as_a_substring(gw, settings, capsys):
    gw.add(5, OWNER)
    rc, out, _ = holds(gw, settings, capsys, "action=merge-owner pr=5 ")
    assert rc == 0


# ── AC 5: write ───────────────────────────────────────────────────────────────


def test_write_posts_the_marker_through_block_1_with_read_back(gw, settings, capsys):
    rc, out, _ = run(gw, settings, capsys, "marker", "write", "--repo", "o/r", "--number", "5",
                     "--action", "merge-owner", "--field", "pr=5", "--field", "head=abc1234")  # fmt: skip
    assert rc == 0 and out["result"] == "written" and out["block"] == "marker" and out["operation"] == "write"
    assert out["attempts"] == 1
    assert gw.comments[5][0]["body"] == OWNER
    assert gw.calls.count("list 5") >= 2  # the read-back


def test_write_with_a_body_file_puts_the_marker_after_the_text(gw, settings, capsys, tmp_path):
    path = tmp_path / "b.md"
    path.write_text("## Blocked Declaration\nType: external\n", encoding="utf-8")
    run(gw, settings, capsys, "marker", "write", "--repo", "o/r", "--number", "5", "--action", "x",
        "--body-file", str(path))  # fmt: skip
    assert gw.comments[5][0]["body"] == "## Blocked Declaration\nType: external\n\n<!-- hpw:dispatcher action=x -->"


def test_write_once_posts_nothing_when_the_marker_holds(gw, settings, capsys):
    gw.add(5, OWNER)
    rc, out, _ = run(gw, settings, capsys, "marker", "write", "--repo", "o/r", "--number", "5",
                     "--action", "merge-owner", "--field", "pr=5", "--field", "head=abc1234", "--once")  # fmt: skip
    assert rc == 0 and out["result"] == "already" and gw.posts() == []


def test_write_once_ignores_a_forged_marker_and_posts(gw, settings, capsys):
    gw.add(5, OWNER, login=OTHER)
    rc, out, _ = run(gw, settings, capsys, "marker", "write", "--repo", "o/r", "--number", "5",
                     "--action", "merge-owner", "--field", "pr=5", "--field", "head=abc1234", "--once")  # fmt: skip
    assert rc == 0 and out["result"] == "written" and len(gw.posts()) == 1


def test_write_once_posts_again_after_the_marker_was_unblocked(gw, settings, capsys):
    gw.add(5, OWNER)
    gw.add(5, "/unblock", login=PO)
    rc, out, _ = run(gw, settings, capsys, "marker", "write", "--repo", "o/r", "--number", "5",
                     "--action", "merge-owner", "--field", "pr=5", "--field", "head=abc1234", "--once")  # fmt: skip
    assert out["result"] == "written"


def test_write_once_with_unreadable_state_posts_nothing(gw, settings, capsys):
    gw.list_failures = 1
    rc, out, _ = run(gw, settings, capsys, "marker", "write", "--repo", "o/r", "--number", "5",
                     "--action", "x", "--once")  # fmt: skip
    assert rc == 1 and out["result"] == "failed" and gw.posts() == []


def test_write_that_is_not_confirmed_exits_1(gw, settings, capsys):
    gw.post_modes = ["missing"] * 3
    rc, out, _ = run(gw, settings, capsys, "marker", "write", "--repo", "o/r", "--number", "5", "--action", "x")
    assert rc == 1 and out["result"] == "failed" and out["ok"] is False and "read back" in out["error"]


@pytest.mark.parametrize("action,fields,old", [
    ("merge-owner", [("pr", "5"), ("head", "abc")], "action=merge-owner pr=5 head=abc"),
    ("merge-attempt", [("pr", "5"), ("head", "abc")], "action=merge-attempt pr=5 head=abc"),
    ("fix-round-attempt", [("pr", "5"), ("head", "abc")], "action=fix-round-attempt pr=5 head=abc"),
    ("conflict-round-attempt", [("pr", "5"), ("head", "abc")], "action=conflict-round-attempt pr=5 head=abc"),
    ("merge-owner-missing", [("pr", "5"), ("head", "abc")], "action=merge-owner-missing pr=5 head=abc"),
    ("evidence-retry-failed", [("item", "merge-owner"), ("ref", "pr5")], "action=evidence-retry-failed item=merge-owner ref=pr5"),
    ("evidence-gave-up", [("item", "merge-owner"), ("ref", "pr5")], "action=evidence-gave-up item=merge-owner ref=pr5"),
    ("in-merge-without-pr", [], "action=in-merge-without-pr"),
])
def test_marker_text_equals_the_text_the_bash_builders_print(action, fields, old):
    assert marker_text(action, fields) == old
    assert dispatcher_marker(old) == f"<!-- hpw:dispatcher {old} -->"
