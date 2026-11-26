"""Block 2, markers under the dispatcher's own account: ``hpw dispatcher marker`` (issue #433, ADR-014).

Every path runs against the Python fake gateway of the Block 1 tests: no network, no real ``gh``.
"""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest

from headless_pr_workflow.dispatcher import cli
from headless_pr_workflow.dispatcher.gateway import GatewayError
from headless_pr_workflow.dispatcher.marker import decide_holds, decide_merge_owner, dispatcher_marker, marker_text
from headless_pr_workflow.dispatcher.write import Settings, _gave_up, evidence_pending_comment

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


# ── Issue #628: one marker-holds rule ─────────────────────────────────────────

T_EARLY, T0, T1 = "2026-11-10T09:00:00Z", "2026-11-10T10:00:00Z", "2026-11-10T11:00:00Z"
CANCEL = dispatcher_marker("action=hand-off-done pr=5")


def c(login, body, at):
    return {"user": {"login": login}, "body": body, "created_at": at, "html_url": f"https://x/5#{at}"}


def test_628_the_newest_marker_by_created_at_holds_not_the_last_listed():
    comments = [c(BOT, OWNER, T1), c(PO, "/unblock", T0), c(BOT, OWNER, T_EARLY)]
    word, comment = decide_holds(comments, BOT, OWNER, po_login=PO)
    assert word == "holds" and comment["created_at"] == T1


def test_628_a_po_unblock_after_the_newest_marker_lifts_it_whatever_the_list_order():
    comments = [c(PO, "/unblock", T1), c(BOT, OWNER, T0), c(BOT, OWNER, T_EARLY)]
    assert decide_holds(comments, BOT, OWNER, po_login=PO)[0] == "unblocked"


def test_628_a_cancel_marker_counts_only_after_the_newest_marker():
    assert decide_holds([c(BOT, OWNER, T1), c(BOT, CANCEL, T0), c(BOT, OWNER, T_EARLY)], BOT, OWNER, po_login=PO,
                        cancel=CANCEL)[0] == "holds"  # fmt: skip
    word, comment = decide_holds([c(BOT, CANCEL, T1), c(BOT, OWNER, T0)], BOT, OWNER, po_login=PO, cancel=CANCEL)
    assert word == "cancelled" and comment["created_at"] == T1


def test_628_markers_of_another_account_and_a_missing_marker_are_absent():
    assert decide_holds([c(OTHER, OWNER, T0)], BOT, OWNER, po_login=PO) == ("absent", None)
    assert decide_holds([], BOT, OWNER, po_login=PO) == ("absent", None)


def test_628_on_equal_created_at_the_last_listed_marker_decides():
    first, last = c(BOT, OWNER, T0), {**c(BOT, OWNER, T0), "html_url": "https://x/5#last"}
    assert decide_holds([first, last], BOT, OWNER, po_login=PO)[1]["html_url"] == "https://x/5#last"


def test_628_holds_reads_github_and_follows_created_at(gw, settings, capsys):
    gw.comments[5] = [c(BOT, OWNER, T1), c(PO, "/unblock", T0), c(BOT, OWNER, T_EARLY)]
    rc, out, _ = holds(gw, settings, capsys)
    assert rc == 0 and out["result"] == "holds" and out["created_at"] == T1


def test_628_the_evidence_give_up_marker_uses_the_same_rule():
    gave = dispatcher_marker("action=evidence-gave-up item=merge-owner ref=pr5")
    assert _gave_up([c(BOT, gave, T1), c(PO, "/unblock", T0), c(BOT, gave, T_EARLY)], BOT, gave, PO) is True
    assert _gave_up([c(BOT, gave, T0), c(PO, "/unblock", T1)], BOT, gave, PO) is False


def decide(capsys, monkeypatch, stdin, *extra):
    monkeypatch.setattr("sys.stdin", SimpleNamespace(buffer=io.BytesIO(stdin.encode())))
    rc = cli.main(["marker", "decide", "--login", BOT, "--po-login", PO, *extra],
                  gateway_factory=lambda repo: pytest.fail("decide reads no GitHub"))  # fmt: skip
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 1, out
    return rc, json.loads(out[0])


@pytest.mark.parametrize("comments,word,rc", [
    ([c(BOT, OWNER, T1), c(PO, "/unblock", T0), c(BOT, OWNER, T_EARLY)], "holds", 0),
    ([c(BOT, OWNER, T0), c(PO, "/unblock", T1)], "unblocked", 1),
    ([c(OTHER, OWNER, T0)], "absent", 1),
])
def test_628_decide_gives_the_rule_s_word_and_exit_code(capsys, monkeypatch, comments, word, rc):
    code, out = decide(capsys, monkeypatch, json.dumps(comments), "--marker", OWNER)
    assert code == rc and out["result"] == word and out["block"] == "marker" and out["operation"] == "decide"


@pytest.mark.parametrize("stdin", ["not json", '{"a": 1}', '[1, 2]'])
def test_628_decide_with_unreadable_comments_is_unreadable_exit_3(capsys, monkeypatch, stdin):
    code, out = decide(capsys, monkeypatch, stdin, "--marker", OWNER)
    assert code == 3 and out["result"] == "unreadable" and out["ok"] is False


def test_628_decide_with_an_empty_marker_or_login_reads_nothing(capsys, monkeypatch):
    code, out = decide(capsys, monkeypatch, "[]", "--marker", " ")
    assert code == 2 and out["result"] == "invalid"
    monkeypatch.setattr("sys.stdin", SimpleNamespace(buffer=io.BytesIO(b"[]")))
    assert cli.main(["marker", "decide", "--login", " ", "--marker", OWNER]) == 3


# ── Issue #647: the one merge-owner-evidence rule ─────────────────────────────

PR = 5
HEAD = "a" * 40
OTHER_HEAD = "b" * 40
OWNS = dispatcher_marker(f"action=merge-owner pr={PR} head={HEAD}")
OWNS_PENDING = evidence_pending_comment("merge-owner", HEAD, OWNS)


def owner(comments, head=HEAD, login=BOT):
    word, evidence, comment = decide_merge_owner(comments, login, PR, head)
    return word, evidence, (comment or {}).get("created_at")


@pytest.mark.parametrize("comments,expected", [
    # every case of the parity baseline (tests/test_dispatcher_parity_review_loop.py, merge_owner_evidence)
    ([c(BOT, OWNS, T0)], ("holds", "marker", T0)),                          # marker-on-head
    ([], ("absent", None, None)),                                          # no-marker
    ([c(OTHER, OWNS, T0)], ("absent", None, None)),                        # marker-by-another-account
    ([c(BOT, OWNS_PENDING, T0)], ("holds", "evidence-pending", T0)),       # evidence-pending-copy: decision 5
    ([c(BOT, OWNS, T0), c(PO, "/unblock", T1)], ("holds", "marker", T0)),  # marker-then-po-unblock: decision 6
    # cases the copies decide besides the baseline
    ([c(OTHER, OWNS_PENDING, T0)], ("absent", None, None)),                # a pending copy of another account
    ([c(BOT, OWNS_PENDING, T1), c(BOT, OWNS, T0)], ("holds", "marker", T0)),  # the marker wins over its pending copy
    ([c(BOT, OWNS, T_EARLY), c(BOT, OWNS, T1), c(BOT, OWNS, T0)], ("holds", "marker", T1)),  # newest decides
    ([c(BOT, OWNS.upper(), T0)], ("holds", "marker", T0)),                 # case ignored, as conflict_round_rule
    ([c(BOT, "merged it, " + OWNS + " thanks", T0)], ("holds", "marker", T0)),  # a substring of the body
    ([c(BOT, {"no": "string"}, T0), {"body": None}], ("absent", None, None)),  # odd comments match nothing
])
def test_647_the_rule_decides_every_case_of_the_copies(comments, expected):
    assert owner(comments) == expected


@pytest.mark.parametrize("body", [
    dispatcher_marker(f"action=merge-owner pr={PR} head={OTHER_HEAD}"),      # another head
    dispatcher_marker(f"action=merge-owner pr=55 head={HEAD}"),             # another PR
    dispatcher_marker(f"action=merge-owner pr={PR} head={HEAD}0"),          # a longer head
    evidence_pending_comment("merge-owner", OTHER_HEAD, OWNS),              # pending copy of another head
    evidence_pending_comment("merge-confirmation", HEAD, OWNS),             # pending copy of another item
    dispatcher_marker(f"action=merge-owner-missing pr={PR} head={HEAD}"),   # the ownerless blocker's marker
    OWNS.replace("<!-- ", ""),                                             # not the hidden marker
])
def test_647_only_the_marker_for_exactly_this_pr_and_head_is_evidence(body):
    assert owner([c(BOT, body, T0)]) == ("absent", None, None)


def test_647_the_head_is_matched_whatever_its_case_and_surrounding_space():
    assert owner([c(BOT, OWNS, T0)], head=f"  {HEAD.upper()} ") == ("holds", "marker", T0)


class PrGateway(FakeGateway):
    """The Block 1 fake with the pulls API read: ``head`` is the PR head, ``fail_pr`` fails the read."""

    def __init__(self) -> None:
        super().__init__()
        self.head: str | None = HEAD
        self.fail_pr = False

    def pull_request(self, number: int) -> dict:
        self.calls.append(f"pr {number}")
        if self.fail_pr:
            raise GatewayError(f"read PR #{number}", "HTTP 502")
        return {"number": number, "head": {"sha": self.head}}


@pytest.fixture
def pgw() -> PrGateway:
    return PrGateway()


def merge_owner(gw, settings, capsys, *extra):
    return run(gw, settings, capsys, "marker", "merge-owner", "--repo", "o/r", "--pr", str(PR), *extra)


def test_647_merge_owner_reads_github_and_prints_the_contract_fields(pgw, settings, capsys):
    pgw.add(PR, OWNS)
    pgw.add(PR, "/unblock", login=PO)
    rc, out, _ = merge_owner(pgw, settings, capsys, "--head", HEAD)
    assert rc == 0 and out["ok"] is True
    assert (out["block"], out["operation"], out["result"], out["head"], out["evidence"]) == (
        "marker", "merge-owner", "holds", HEAD, "marker")
    assert out["url"].startswith(f"https://x/{PR}#") and out["created_at"] and out["error"] is None
    assert "pr 5" not in pgw.calls  # with --head the PR is not read


def test_647_merge_owner_without_head_reads_the_pr_head(pgw, settings, capsys):
    pgw.add(PR, OWNS_PENDING)
    rc, out, _ = merge_owner(pgw, settings, capsys)
    assert (rc, out["result"], out["evidence"], out["head"]) == (0, "holds", "evidence-pending", HEAD)
    pgw.head = OTHER_HEAD
    rc, out, _ = merge_owner(pgw, settings, capsys)
    assert (rc, out["result"], out["evidence"], out["head"]) == (1, "absent", None, OTHER_HEAD)


def test_647_a_forged_or_missing_marker_is_absent_exit_1(pgw, settings, capsys):
    rc, out, _ = merge_owner(pgw, settings, capsys, "--head", HEAD)
    assert (rc, out["result"], out["ok"]) == (1, "absent", False)
    pgw.add(PR, OWNS, login=OTHER)
    rc, out, _ = merge_owner(pgw, settings, capsys, "--head", HEAD)
    assert (rc, out["result"]) == (1, "absent")


def test_647_a_pr_that_cannot_be_read_is_unreadable_exit_3(pgw, settings, capsys):
    pgw.add(PR, OWNS)
    pgw.fail_pr = True
    rc, out, _ = merge_owner(pgw, settings, capsys)
    assert (rc, out["result"], out["ok"], out["evidence"]) == (3, "unreadable", False, None)
    assert "HTTP 502" in out["error"]
    pgw.fail_pr, pgw.head = False, None  # a PR read without a head SHA
    rc, out, _ = merge_owner(pgw, settings, capsys)
    assert (rc, out["result"]) == (3, "unreadable") and "head SHA" in out["error"]


def test_647_a_login_that_cannot_be_resolved_is_unreadable_exit_3(pgw, settings, capsys, monkeypatch):
    pgw.add(PR, OWNS)
    monkeypatch.setattr(pgw, "viewer_login", lambda: (_ for _ in ()).throw(GatewayError("resolve login", "HTTP 401")))
    rc, out, _ = merge_owner(pgw, settings, capsys, "--head", HEAD)
    assert (rc, out["result"]) == (3, "unreadable")
    monkeypatch.setattr(pgw, "viewer_login", lambda: " ")
    rc, out, _ = merge_owner(pgw, settings, capsys, "--head", HEAD)
    assert (rc, out["result"]) == (3, "unreadable")


def test_647_comments_that_cannot_be_read_are_unreadable_exit_3(pgw, settings, capsys):
    pgw.add(PR, OWNS)
    pgw.list_failures = 1
    rc, out, _ = merge_owner(pgw, settings, capsys, "--head", HEAD)
    assert (rc, out["result"], out["evidence"], out["url"]) == (3, "unreadable", None, None)


def test_647_the_bookkeeping_login_supplies_no_merge_owner_evidence(pgw, settings, capsys, monkeypatch):
    monkeypatch.setenv("CODEX_BOOKKEEPING_LOGIN", BK)
    pgw.add(PR, OWNS, login=BK)
    rc, out, _ = merge_owner(pgw, settings, capsys, "--head", HEAD)
    assert (rc, out["result"]) == (1, "absent")


def owner_decide(capsys, monkeypatch, stdin, *argv):
    monkeypatch.setattr("sys.stdin", SimpleNamespace(buffer=io.BytesIO(stdin.encode())))
    rc = cli.main(["marker", "merge-owner-decide", *argv],
                  gateway_factory=lambda repo: pytest.fail("merge-owner-decide reads no GitHub"))  # fmt: skip
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 1, out
    return rc, json.loads(out[0])


@pytest.mark.parametrize("comments,word,evidence,rc", [
    ([c(BOT, OWNS, T0), c(PO, "/unblock", T1)], "holds", "marker", 0),
    ([c(BOT, OWNS_PENDING, T0)], "holds", "evidence-pending", 0),
    ([c(OTHER, OWNS, T0)], "absent", None, 1),
])
def test_647_merge_owner_decide_gives_the_rule_s_word_and_exit_code(capsys, monkeypatch, comments, word, evidence, rc):
    code, out = owner_decide(capsys, monkeypatch, json.dumps(comments),
                             "--login", BOT, "--pr", str(PR), "--head", HEAD)  # fmt: skip
    assert (code, out["result"], out["evidence"], out["head"]) == (rc, word, evidence, HEAD)
    assert out["block"] == "marker" and out["operation"] == "merge-owner-decide"


@pytest.mark.parametrize("stdin", ["not json", '{"a": 1}', "[1, 2]", "\xff"])
def test_647_merge_owner_decide_with_unreadable_comments_is_unreadable_exit_3(capsys, monkeypatch, stdin):
    code, out = owner_decide(capsys, monkeypatch, stdin, "--login", BOT, "--pr", str(PR), "--head", HEAD)
    assert (code, out["result"], out["ok"], out["evidence"]) == (3, "unreadable", False, None)


def test_647_merge_owner_decide_with_an_empty_login_is_unreadable_exit_3(capsys, monkeypatch):
    code, out = owner_decide(capsys, monkeypatch, json.dumps([c(" ", OWNS, T0)]),
                             "--login", " ", "--pr", str(PR), "--head", HEAD)  # fmt: skip
    assert (code, out["result"]) == (3, "unreadable")


@pytest.mark.parametrize("argv", [
    ["merge-owner-decide", "--login", BOT, "--pr", str(PR), "--head", "not-a-sha"],
    ["merge-owner-decide", "--login", BOT, "--pr", str(PR)],
    ["merge-owner-decide", "--login", BOT, "--pr", "0", "--head", HEAD],
    ["merge-owner", "--repo", "o/r", "--pr", str(PR), "--head", "xyz"],
    ["merge-owner", "--repo", "bad", "--pr", str(PR)],
    ["merge-owner", "--repo", "o/r"],
])
def test_647_invalid_arguments_exit_2_and_read_nothing(capsys, argv):
    rc = cli.main(["marker", *argv], gateway_factory=lambda repo: pytest.fail("invalid arguments read no GitHub"))
    out = json.loads(capsys.readouterr().out.splitlines()[0])
    assert rc == 2 and out["result"] == "invalid"
