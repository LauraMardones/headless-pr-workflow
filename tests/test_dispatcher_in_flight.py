"""The in-flight rule: ``hpw dispatcher in-flight plan`` (issue #475).

``plan_in_flight`` decides each story's resume step and the two scheduler lists from the
facts ``scripts/lib/in-flight.sh`` read. The rule is pure, so these tests give it rows
directly; the scripts that read the facts are covered by the dispatcher tests.
"""

from __future__ import annotations

import json
import re

import pytest

from headless_pr_workflow.dispatcher import cli
from headless_pr_workflow.dispatcher.in_flight import (
    hand_off_done_marker,
    hand_off_pending_prefix,
    plan_in_flight,
)
from tests.dispatcher_fakes import REPO_ROOT, bookkeeping_marker

BOT, BK, PO = "hpw-bot", "hpw-bk", "LauraMardones"
HEAD = "a" * 40


def comment(body: str, login: str = BOT, at: str = "2026-10-13T10:00:00Z", **extra) -> dict:
    return {"user": {"login": login}, "body": body, "created_at": at, **extra}


def row(number: int, *, pr: int | None = None, draft: bool = False, head: str = HEAD, review_loop: bool = True,
        merged: bool = False, gates: bool = False, story: list | None = None, pr_comments: list | None = None) -> dict:
    return {"number": number, "title": f"Story {number}", "executors": ["claude-code-sonnet"],
            "review_loop": review_loop,
            "pr": None if pr is None else {"number": pr, "draft": draft, "head": head},
            "merged": merged, "gates_pass": gates,
            "story_comments": [] if story is None else story,
            "pr_comments": [] if pr_comments is None else pr_comments}  # fmt: skip


def plan(*rows: dict, dispatcher: str = BOT, bookkeeping: str = BK) -> dict:
    return plan_in_flight(list(rows), dispatcher=dispatcher, bookkeeping=bookkeeping, po=PO)


def steps(result: dict) -> dict[str, list[int]]:
    return {name: [e["number"] for e in result[name]] for name in ("merge", "finish", "fix_round", "hand_off", "review")}


FINDINGS = comment(bookkeeping_marker("move-in-implementation", HEAD), login=BK)
PENDING = comment(f"{hand_off_pending_prefix(90)}head={HEAD} -->")


def test_each_step_comes_from_its_fact():
    result = plan(row(1, pr=91, gates=True), row(2, merged=True), row(3, pr=93, pr_comments=[FINDINGS]),
                  row(4, pr=90, story=[PENDING]), row(5, pr=95), row(6, pr=96, draft=True), row(7))
    assert steps(result) == {"merge": [1], "finish": [2], "fix_round": [3], "hand_off": [4], "review": [5]}
    # A draft PR or a fix round due counts for the WIP limit; every other open linked PR is in flight (AC 8).
    assert result["in_implementation"] == [3, 6]
    assert result["in_flight"] == [1, 4, 5]


@pytest.mark.parametrize("story,pr_comments", [
    ([PENDING], []),
    ([], [FINDINGS]),
    ([PENDING], [FINDINGS]),
])
def test_passing_merge_gates_take_precedence_over_pending_markers(story, pr_comments):
    result = plan(row(4, pr=90, gates=True, story=story, pr_comments=pr_comments))
    assert steps(result) == {"merge": [4], "finish": [], "fix_round": [], "hand_off": [], "review": []}
    assert result["in_implementation"] == [] and result["in_flight"] == [4]


def test_entries_carry_the_pr_and_head():
    result = plan(row(5, pr=95))
    assert result["review"] == [{"number": 5, "title": "Story 5", "executors": ["claude-code-sonnet"], "pr": 95,
                                 "head": HEAD}]  # fmt: skip


@pytest.mark.parametrize("case,listed", [
    ("findings on the head", True),
    ("findings on an older head", False),
    ("findings by another account", False),
    ("not a review-loop story", False),
    ("comments not read", False),
    ("no dispatcher account", False),
])
def test_fix_round_needs_the_findings_marker_on_the_head(case, listed):
    kwargs: dict = {"pr_comments": [FINDINGS]}
    if case == "findings on an older head":
        kwargs["pr_comments"] = [comment(bookkeeping_marker("move-in-implementation", "c" * 40), login=BK)]
    elif case == "findings by another account":
        kwargs["pr_comments"] = [comment(FINDINGS["body"], login="someone")]
    elif case == "not a review-loop story":
        kwargs["review_loop"] = False
    r = row(3, pr=93, **kwargs)
    if case == "comments not read":
        r["pr_comments"] = None
    result = plan(r, dispatcher="" if case == "no dispatcher account" else BOT)
    assert steps(result)["fix_round"] == ([3] if listed else [])
    # Either way the story is in flight: a ready PR is never new work.
    assert 3 in result["in_implementation"] + result["in_flight"]


def test_fix_round_on_a_draft_counts_once_for_the_wip_limit():
    result = plan(row(3, pr=93, draft=True, pr_comments=[FINDINGS]))
    assert steps(result)["fix_round"] == [3] and result["in_implementation"] == [3] and result["in_flight"] == []


@pytest.mark.parametrize("case,listed", [
    ("pending", True),
    ("done after it", False),
    ("PO unblock after it", False),
    ("other account", False),
    ("draft PR", False),
    ("other PR", False),
])
def test_hand_off_needs_a_pending_marker_without_done(case, listed):
    story = [PENDING]
    if case == "done after it":
        story.append(comment(hand_off_done_marker(90), at="2026-10-13T11:00:00Z"))
    elif case == "PO unblock after it":
        story.append(comment("/unblock", login=PO, at="2026-10-13T11:00:00Z"))
    elif case == "other account":
        story = [comment(PENDING["body"], login="someone")]
    elif case == "other PR":
        story = [comment(f"{hand_off_pending_prefix(80)}head={HEAD} -->")]
    result = plan(row(4, pr=90, draft=case == "draft PR", story=story))
    assert steps(result)["hand_off"] == ([4] if listed else [])


@pytest.mark.parametrize("kind,listed", [("dependency", True), ("external", True), ("conflict", True),
                                         ("resource", True), ("decision", False)])
def test_a_story_without_a_pr_is_in_flight_while_a_non_decision_blocker_is_open(kind, listed):
    blocker = comment(f"## Blocked Declaration\nType: {kind}\nDeclared by: dispatcher", author_association="OWNER")
    result = plan(row(7, story=[blocker]))
    assert result["in_flight"] == ([7] if listed else []) and result["in_implementation"] == []
    assert all(v == [] for v in steps(result).values())


def test_a_later_po_unblock_lifts_the_blocker():
    blocker = comment("## Blocked Declaration\nType: external\nDeclared by: dispatcher", author_association="OWNER")
    lifted = comment("Unblocked: the dependency is closed", login=PO, at="2026-10-13T11:00:00Z")
    assert plan(row(7, story=[blocker, lifted]))["in_flight"] == []


def test_a_story_with_nothing_in_flight_is_in_no_list():
    """#475 AC 6: no linked PR and no dispatcher marker: no step and no scheduler list."""
    result = plan(row(8), row(9, story=[comment("a note")]))
    assert all(v == [] for v in steps(result).values())
    assert result["in_implementation"] == [] and result["in_flight"] == []


def test_rows_are_ordered_by_number():
    result = plan(row(5, pr=95), row(2, pr=92))
    assert steps(result)["review"] == [2, 5] and result["in_flight"] == [2, 5]


def test_command_reads_the_input_file(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("DECISION_BLOCKER_PO_LOGIN", PO)
    (tmp_path / "rows.json").write_text(json.dumps([row(1, pr=91, gates=True)]), encoding="utf-8")
    rc = cli.main(["in-flight", "plan", "--repo", "o/r", "--input", str(tmp_path / "rows.json"),
                   "--dispatcher", BOT, "--bookkeeping", BK])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True and out["block"] == "in-flight" and out["result"] == "read"
    assert [e["number"] for e in out["merge"]] == [1] and out["in_flight"] == [1]


@pytest.mark.parametrize("content", ["not json", '{"rows": []}', "[1, 2]"])
def test_command_fails_closed_on_unreadable_input(tmp_path, capsys, content):
    (tmp_path / "rows.json").write_text(content, encoding="utf-8")
    rc = cli.main(["in-flight", "plan", "--repo", "o/r", "--input", str(tmp_path / "rows.json")])
    out = json.loads(capsys.readouterr().out)
    assert rc == 3 and out["result"] == "unreadable" and out["error"]


def test_command_without_an_input_file_fails_closed(tmp_path, capsys):
    rc = cli.main(["in-flight", "plan", "--repo", "o/r", "--input", str(tmp_path / "missing.json")])
    assert rc == 3 and json.loads(capsys.readouterr().out)["result"] == "unreadable"


def test_command_needs_the_repo_and_input(capsys):
    assert cli.main(["in-flight", "plan", "--repo", "o/r"]) == 2
    assert json.loads(capsys.readouterr().out)["result"] == "invalid"


# ── AC 12: the docs ──────────────────────────────────────────────────────────

DOCS = REPO_ROOT / "docs"


def _section(path, heading: str) -> str:
    text = path.read_text(encoding="utf-8")
    level = heading.split(" ", 1)[0]
    match = re.search(rf"^{re.escape(heading)}$\n(.*?)(?=^#{{1,{len(level)}}} |\Z)", text, re.MULTILINE | re.DOTALL)
    assert match, heading
    return match.group(1)


def test_ac12_dispatcher_config_names_the_fact_behind_each_step():
    flow = _section(DOCS / "DISPATCHER-CONFIG.md", "## Codex review loop hand-off and resume")
    facts = _section(DOCS / "DISPATCHER-CONFIG.md", "### Work in flight from PR facts")
    for needle in ("ignoring the mergeability gate", "`policy`, `checks`, `approval`, `threads` and `head`",
                   "a merged linked PR", "the bookkeeping job's findings marker", "`hand-off-pending` marker",
                   "with no `hand-off-done` marker", "not in the merge step", "`hpw dispatcher in-flight plan`",
                   "Moving a card starts nothing"):
        assert needle in facts, needle
    # The stale rule and its log lines.
    for needle in ("[RESUME SKIP] #<n>: stale spec record (<record commit> is older than the Epic's <current commit>); "
                   "no fix round, conflict round, /review or /merge until it is re-refined",
                   "[RESUME SKIP] facts unreadable: <error>; no in-flight session in this run",
                   "[RESUME SKIP] #<n>: facts unreadable (<reason>); trying again on the next run",
                   "is not held back"):
        assert needle in facts, needle
    # The two scheduler lists.
    for needle in ("`--in-implementation`: the stories with an open linked PR that is a draft, or whose current head has "
                   "a fix round due", "`--in-flight`: every other open Story, Task or Bug with an open linked PR",
                   "does not count them for the WIP limit"):
        assert needle in facts, needle
    # No board status starts a step any more.
    for stale in ("### Resume at `In merge`", "the story's board status is `In review`",
                  "the story's board status is `In implementation`", "Neither (including a PR that is still a draft)",
                  "the board status, the linked PR and hidden markers", "/review left the story in"):
        assert stale not in flow, stale
    scheduler = _section(DOCS / "DISPATCHER-CONFIG.md", "## Scheduler: new work from GitHub facts")
    assert "--in-flight <n>[,<n>...]" in scheduler and "PO decision on PR #485" not in scheduler


def test_ac12_scheduler_command_page_documents_in_flight():
    text = (DOCS / "commands" / "dispatcher-scheduler.md").read_text(encoding="utf-8")
    assert "[--in-flight <n>[,<n>...]]" in text
    flag = next(line for line in text.splitlines() if line.startswith("- `--in-flight`"))
    for needle in ("never an implementation candidate", "refined only when stale", "**not** counted for the WIP limit",
                   "A number in both lists counts as `--in-implementation`"):
        assert needle in flag, needle


def test_markers_match_the_invoke_script():
    """The rule reads the markers the invoke script writes (hand_off_pending_marker, hand_off_done_marker)."""
    from tests.dispatcher_fakes import INVOKE

    text = INVOKE.read_text(encoding="utf-8")
    assert 'dispatcher_marker "action=hand-off-pending pr=$1 head=$2"' in text
    assert 'dispatcher_marker "action=hand-off-done pr=$1"' in text
    assert hand_off_pending_prefix(90) == "<!-- hpw:dispatcher action=hand-off-pending pr=90 "
    assert hand_off_done_marker(90) == "<!-- hpw:dispatcher action=hand-off-done pr=90 -->"
    assert 'BOOKKEEPING_FIX_MARKER="<!-- hpw:codex-review-bookkeeping action=move-in-implementation head="' in text
