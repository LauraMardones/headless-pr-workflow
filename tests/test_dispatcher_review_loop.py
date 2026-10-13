"""Dispatcher hand-off to the Codex review loop and its two resume entries (issue #293).

Drives the real ``scripts/dispatcher-invoke.sh`` with the fakes in
``tests/dispatcher_fakes.py``. Each test asserts which sessions started, which
board writes the dispatcher made and which comments it posted.
"""

from __future__ import annotations

import json
import os
import re
import subprocess

import pytest

from headless_pr_workflow import codex_review_bookkeeping as bookkeeping
from tests.dispatcher_fakes import (
    BASH, BOARD_OPTIONS, BOT, HAVE_TOOLS, INVOKE, PO, REPO_ROOT, Dispatcher, bookkeeping_marker,
)

needs_tools = pytest.mark.skipif(not HAVE_TOOLS, reason="requires bash, jq and timeout")

STORY = 500
PR = 900
HEAD = "a" * 40
NEW_HEAD = "b" * 40
CLAUDE_TIERS = ("claude-code-haiku", "claude-code-sonnet", "claude-code-opus")


@pytest.fixture
def d(tmp_path):
    return Dispatcher(tmp_path)


def table(name: str) -> dict[str, str]:
    """Rows of a ``declare -A`` table in dispatcher-invoke.sh."""
    text = INVOKE.read_text(encoding="utf-8")
    block = re.search(rf"^declare -A {name}=\(\n(.*?)^\)$", text, re.MULTILINE | re.DOTALL)
    assert block, f"{name} not found in {INVOKE}"
    return dict(re.findall(r'^\s*\["([^"]+)"\]="([^"]*)"$', block.group(1), re.MULTILINE))


def new_head_on_implement(d: Dispatcher, status: str | None = "In review") -> None:
    """The /implement session pushes a commit and, like implement.md, sets the status."""
    pulls = json.dumps([{"number": PR, "body": f"Closes #{STORY}", "state": "open", "draft": False,
                         "merged_at": None, "updated_at": "2026-10-13T09:00:00Z",
                         "head": {"ref": "branch", "sha": NEW_HEAD}}])
    script = f"cat > \"$MOCK_DIR/pulls.json\" <<'EOF'\n{pulls}\nEOF\n"
    if status:
        script += (f"""jq '. + {{"ITEM_{STORY}": "{status}"}}' "$MOCK_DIR/state.json" > "$MOCK_DIR/s.tmp" """
                   """&& mv "$MOCK_DIR/s.tmp" "$MOCK_DIR/state.json"\n""")
    d.on_session("implement", script)


def new_head_on_implement_once(d: Dispatcher) -> None:
    """Only the first /implement session (the fix round) pushes a commit and sets "In review"."""
    d.on_session("implement", f"""
if [[ ! -f "$MOCK_DIR/fixed" ]]; then
    touch "$MOCK_DIR/fixed"
    jq 'map(if .number == {PR} then .head.sha = "{NEW_HEAD}" else . end)' "$MOCK_DIR/pulls.json" > "$MOCK_DIR/p.tmp" && mv "$MOCK_DIR/p.tmp" "$MOCK_DIR/pulls.json"
    jq '. + {{"ITEM_{STORY}": "In review"}}' "$MOCK_DIR/state.json" > "$MOCK_DIR/s.tmp" && mv "$MOCK_DIR/s.tmp" "$MOCK_DIR/state.json"
fi
""")


# ── Hand-off ────────────────────────────────────────────────────────────────


@needs_tools
@pytest.mark.parametrize("tier", CLAUDE_TIERS)
def test_ac1_claude_story_is_handed_to_the_review_loop(d, tier):
    d.add_story(STORY, "Ready for implementation", executor=tier)
    d.add_pr(PR, STORY)

    proc = d.run_issue(STORY)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {STORY}"]
    assert d.board_writes == [f"{STORY}=In implementation", f"{STORY}=In review"]
    assert d.status(STORY) == "In review"
    assert d.posted(STORY) == [] and d.posted(PR) == []
    assert f"[HANDOFF] #{STORY}: handed to the Codex review loop (Codex Cloud) on PR #{PR};" in proc.stdout
    assert "(review)" not in proc.stdout  # no review budget increment
    assert proc.stdout.count("[BUDGET] Increment:") == 1


@needs_tools
@pytest.mark.parametrize("with_pr", [True, False])
def test_ac1_dry_run_names_the_hand_off(d, with_pr):
    d.add_story(STORY, "Ready for implementation")
    if with_pr:
        d.add_pr(PR, STORY)

    proc = d.run_issue(STORY, "--dry-run")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    on = f"PR #{PR}" if with_pr else "the PR /implement opens"
    assert f"[DRY RUN] Would hand #{STORY} to the Codex review loop (Codex Cloud) on {on}: no /review session" in proc.stdout
    assert "/review" not in proc.stdout.replace("no /review session", "")
    assert d.sessions == [] and d.board_writes == [] and d.posted(STORY) == []


@needs_tools
@pytest.mark.parametrize("status_after_implement", ["In review", "In merge"])
def test_ac2_no_write_when_the_story_already_moved_on(d, status_after_implement):
    d.add_story(STORY, "Ready for implementation")
    d.add_pr(PR, STORY)
    d.session_sets_status("implement", STORY, status_after_implement)

    proc = d.run_issue(STORY)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.board_writes == [f"{STORY}=In implementation"]  # E1's own write only
    assert d.status(STORY) == status_after_implement
    assert d.sessions == [f"implement {STORY}"]


@needs_tools
def test_ac2_no_write_while_a_fix_round_is_still_due(d):
    """Findings are recorded for the head /implement left behind: "In review" is not written."""
    d.add_story(STORY, "Ready for implementation")
    d.add_pr(PR, STORY)
    d.add_findings(PR, HEAD)

    proc = d.run_issue(STORY)

    assert proc.returncode == 1
    assert d.board_writes == [f"{STORY}=In implementation"]
    assert d.status(STORY) == "In implementation"
    assert len(d.posted(STORY)) == 1 and f"head={HEAD}" in d.posted(STORY)[0]


@needs_tools
@pytest.mark.parametrize("pr_before", [True, False])
def test_ac2_findings_recorded_during_the_session_are_not_a_tried_fix_round(d, pr_before):
    """Codex answers, and the bookkeeping job acts, before the /implement session returns."""
    d.add_story(STORY, "Ready for implementation")
    if pr_before:
        d.add_pr(PR, STORY)  # a story that already had a PR, at the old head
    findings = json.dumps({"user": {"login": BOT}, "created_at": "2026-10-13T10:59:00Z",
                           "body": bookkeeping_marker("move-in-implementation", NEW_HEAD)})
    pulls = json.dumps([{"number": PR, "body": f"Closes #{STORY}", "state": "open", "draft": False,
                         "merged_at": None, "updated_at": "2026-09-27T00:00:00Z",
                         "head": {"ref": "branch", "sha": NEW_HEAD}}])
    (d.mock / "pulls_after.json").write_text(pulls, encoding="utf-8", newline="\n")
    (d.mock / "comments_after.json").write_text(f"[{findings}]", encoding="utf-8", newline="\n")
    # First session: a new head, and findings recorded for it. Second session: the fix.
    d.on_session("implement", f"""
if [[ ! -f "$MOCK_DIR/implemented" ]]; then
    touch "$MOCK_DIR/implemented"
    cp "$MOCK_DIR/pulls_after.json" "$MOCK_DIR/pulls.json"
    cp "$MOCK_DIR/comments_after.json" "$MOCK_DIR/comments_{PR}.json"
else
    jq 'map(.head.sha = "{"c" * 40}")' "$MOCK_DIR/pulls.json" > "$MOCK_DIR/p.tmp" && mv "$MOCK_DIR/p.tmp" "$MOCK_DIR/pulls.json"
fi
""")

    proc = d.run_issue(STORY)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {STORY}"]
    assert d.board_writes == [f"{STORY}=In implementation"]
    assert d.status(STORY) == "In implementation"
    assert d.posted(STORY) == []
    assert "the next run starts the fix round" in proc.stdout

    # The next run starts the first fix round; it is not skipped as already tried.
    again = d.resume()
    assert again.returncode == 0, again.stdout + again.stderr
    assert f"[RESUME] #{STORY}: fix round 1 of 3, /implement on PR #{PR}" in again.stdout
    assert d.sessions == [f"implement {STORY}"] * 2
    assert d.board_writes == [f"{STORY}=In implementation", f"{STORY}=In review"]
    assert d.posted(STORY) == []


@needs_tools
@pytest.mark.parametrize("head_changes", [False, True])
def test_ac2_failed_in_review_write_is_retried_on_a_later_run(d, head_changes):
    """The bookkeeping job acts only on "In review": a failed write must not end the hand-off for good."""
    d.add_story(STORY, "Ready for implementation")
    d.add_pr(PR, STORY)
    d.fail_status_write("In review")

    first = d.run_issue(STORY)

    assert first.returncode == 1
    assert "[HANDOFF]" not in first.stdout
    assert d.sessions == [f"implement {STORY}"]
    assert d.status(STORY) == "In implementation"
    comments = d.posted(STORY)
    assert len(comments) == 1 and f"action=hand-off-pending pr={PR} head={HEAD}" in comments[0]

    # Still failing: no session, no second comment.
    second = d.resume()
    assert second.returncode == 0, second.stdout + second.stderr
    assert d.sessions == [f"implement {STORY}"] and len(d.posted(STORY)) == 1
    assert d.status(STORY) == "In implementation"

    # The write works again, and a commit landed meanwhile: a later run still completes the hand-off.
    d.fail_status_write("In review", False)
    if head_changes:
        d.add_pr(PR, STORY, head=NEW_HEAD)
    third = d.resume()
    assert third.returncode == 0, third.stdout + third.stderr
    assert f"[RESUME] #{STORY}: completing the hand-off on PR #{PR}" in third.stdout
    assert d.status(STORY) == "In review"
    assert d.board_writes == [f"{STORY}=In implementation", f"{STORY}=In review"]
    assert d.sessions == [f"implement {STORY}"]
    assert len(d.posted(STORY)) == 2 and f"action=hand-off-done pr={PR}" in d.posted(STORY)[1]

    # Completed once: a story that comes back to "In implementation" is not moved again.
    d.set_status(STORY, "In implementation")
    fourth = d.resume()
    assert fourth.returncode == 0, fourth.stdout + fourth.stderr
    assert d.status(STORY) == "In implementation"
    assert d.board_writes == [f"{STORY}=In implementation", f"{STORY}=In review"]
    assert len(d.posted(STORY)) == 2


@needs_tools
@pytest.mark.parametrize("failure", ["transient", "persistent"])
def test_failed_first_status_write_does_not_start_the_story_twice(d, failure):
    """The dispatcher's "In implementation" write fails and the session sets no status."""
    d.add_story(STORY, "Ready for implementation")
    d.add_pr(PR, STORY)
    d.fail_status_write("In implementation")

    first = d.run_issue(STORY)

    assert first.returncode == 1
    assert "[HANDOFF]" not in first.stdout
    assert d.sessions == [f"implement {STORY}"]  # not selected again as the next ready story
    assert d.status(STORY) == "Ready for implementation"
    comments = d.posted(STORY)
    assert len(comments) == 1 and f"action=hand-off-pending pr={PR} head={HEAD}" in comments[0]

    if failure == "persistent":
        d.fail_status_write("In review")
        second = d.run_issue(STORY)
        assert second.returncode == 0, second.stdout + second.stderr
        assert d.sessions == [f"implement {STORY}"] and len(d.posted(STORY)) == 1
        assert d.status(STORY) == "Ready for implementation"
        d.fail_status_write("In review", False)

    # The board works again. The poll still reports the story as ready: no second session.
    d.fail_status_write("In implementation", False)
    later = d.run_issue(STORY)
    assert later.returncode == 0, later.stdout + later.stderr
    assert f"[RESUME] #{STORY}: completing the hand-off on PR #{PR}" in later.stdout
    assert d.sessions == [f"implement {STORY}"]
    assert d.status(STORY) == "In review"
    assert d.board_writes == [f"{STORY}=In review"]
    assert len(d.posted(STORY)) == 2 and f"action=hand-off-done pr={PR}" in d.posted(STORY)[1]


@needs_tools
def test_pending_hand_off_that_still_fails_does_not_block_other_ready_stories(d):
    """The pending story is kept out of the next-story search; another ready story still runs."""
    d.add_story(700, "Ready for implementation")
    d.add_pr(970, 700)
    d.add_story(STORY, "Ready for implementation")
    d.add_pr(PR, STORY)
    d.add_comment(STORY, f"<!-- hpw:dispatcher action=hand-off-pending pr={PR} head={HEAD} -->")
    d.fail_status_write("In review")
    d.session_sets_status("implement", 700, "In review")  # 700's own session sets its status

    proc = d.run_issue(700)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == ["implement 700"]
    assert d.status(STORY) == "Ready for implementation"


@needs_tools
def test_story_in_implementation_without_a_pending_hand_off_is_left_alone(d):
    """A story someone is working on, with a ready PR, is not moved to "In review"."""
    d.add_story(STORY, "In implementation")
    d.add_pr(PR, STORY)
    d.add_comment(STORY, f"<!-- hpw:dispatcher action=hand-off-pending pr=90 head={HEAD} -->")  # another PR
    d.add_comment(STORY, f"<!-- hpw:dispatcher action=hand-off-pending pr={PR} head={HEAD} -->",
                  login="someone-else")  # forged

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.board_writes == [] and d.sessions == [] and d.posted(STORY) == []


@needs_tools
def test_forged_dispatcher_markers_are_ignored(d):
    """Markers count only on comments by the dispatcher's own account."""
    d.add_story(STORY, "In implementation")
    d.add_pr(PR, STORY)
    d.add_findings(PR, HEAD)
    d.add_comment(STORY, f"<!-- hpw:dispatcher action=fix-round-attempt pr={PR} head={HEAD} -->", login="someone-else")
    d.add_comment(STORY, f"<!-- hpw:dispatcher action=fix-round-max pr={PR} -->", login="someone-else")
    d.add_story(600, "In merge")
    d.add_pr(960, 600)
    d.add_comment(600, f"<!-- hpw:dispatcher action=merge-attempt pr=960 head={HEAD} -->", login="someone-else")
    new_head_on_implement_once(d)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == ["merge 960", "cleanup 600", f"implement {STORY}"]
    assert "[RESUME SKIP]" not in proc.stdout


@needs_tools
def test_dispatcher_markers_by_its_own_account_are_honoured(d):
    """The same markers as above, written by the dispatcher: both stories are skipped."""
    d.add_story(STORY, "In implementation")
    d.add_pr(PR, STORY)
    d.add_findings(PR, HEAD)
    d.add_comment(STORY, f"<!-- hpw:dispatcher action=fix-round-attempt pr={PR} head={HEAD} -->")
    d.add_story(600, "In merge")
    d.add_pr(960, 600)
    d.add_comment(600, f"<!-- hpw:dispatcher action=merge-attempt pr=960 head={HEAD} -->")

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [] and proc.stdout.count("[RESUME SKIP]") == 2


@needs_tools
@pytest.mark.parametrize("draft", [None, True])
def test_ac3_no_open_pr_or_draft_pr_blocks_after_implement(d, draft):
    d.add_story(STORY, "Ready for implementation")
    if draft:
        d.add_pr(PR, STORY, draft=True)

    proc = d.run_issue(STORY)

    assert proc.returncode != 0
    assert d.sessions == [f"implement {STORY}"]
    assert d.status(STORY) == "In implementation"
    assert d.board_writes == [f"{STORY}=In implementation"]
    blockers = d.posted(STORY)
    assert len(blockers) == 1
    assert "## Blocked Declaration" in blockers[0] and "/implement completes successfully" in blockers[0]
    assert d.slack_calls == []


@needs_tools
def test_ac4_codex_story_keeps_the_paired_review_session(d):
    d.add_story(STORY, "Ready for implementation", executor="codex")
    d.add_pr(PR, STORY)

    proc = d.run_issue(STORY)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {STORY}", f"review {PR}", f"merge {PR}", f"cleanup {STORY}"]
    assert d.session_providers == ["implement openai", "review anthropic", "merge openai", "cleanup openai"]
    assert d.board_writes == [f"{STORY}=In implementation", f"{STORY}=In review",
                              f"{STORY}=In merge", f"{STORY}=Done"]
    assert "Review executor: Claude Opus" in proc.stdout
    assert f"[BUDGET] Increment: codex +75000 after #{STORY}\n" in proc.stdout
    assert f"[BUDGET] Increment: opus +75000 after #{STORY} (review)" in proc.stdout


def test_ac5_review_path_tables():
    paired = table("REVIEW_EXECUTOR_ROUTING")
    loop = table("CODEX_REVIEW_LOOP_ROUTING")
    assert paired == {"codex": "claude-code-opus"}
    assert set(loop) == set(CLAUDE_TIERS)
    for tier in CLAUDE_TIERS:
        assert tier not in paired, f"{tier} is paired with a dispatcher /review session"
    assert set(paired) | set(loop) == set(table("EXECUTOR_ROUTING"))
    assert not set(paired) & set(loop)


def test_ac5_executor_names_appear_only_in_tables():
    """OSS invariant: no branch on an executor name outside the ``declare -A`` tables."""
    text = INVOKE.read_text(encoding="utf-8")
    code = re.sub(r"^declare -A \w+=\(\n.*?^\)$", "", text, flags=re.MULTILINE | re.DOTALL)
    code = "\n".join(line for line in code.splitlines() if not line.lstrip().startswith("#"))
    for name in table("EXECUTOR_ROUTING"):
        assert f'"{name}"' not in code and f"'{name}'" not in code and f"== {name}" not in code, name


@needs_tools
def test_ac5_executor_with_no_review_path_gets_the_blocker(d, tmp_path, monkeypatch):
    """A copy of the script with the loop row for one tier removed: that tier fails closed."""
    scripts = tmp_path / "repo" / "scripts"
    scripts.mkdir(parents=True)
    text = INVOKE.read_text(encoding="utf-8")
    row = '    ["claude-code-haiku"]="Codex Cloud"\n'
    assert row in text
    (scripts / "dispatcher-invoke.sh").write_text(text.replace(row, ""), encoding="utf-8", newline="\n")
    commands = tmp_path / "repo" / ".claude" / "commands"
    commands.mkdir(parents=True)
    for name in ("implement", "review", "merge", "cleanup"):
        (commands / f"{name}.md").write_text(
            (REPO_ROOT / ".claude" / "commands" / f"{name}.md").read_text(encoding="utf-8"),
            encoding="utf-8", newline="\n")
    d.add_story(STORY, "Ready for implementation", executor="claude-code-haiku")
    d.add_pr(PR, STORY)

    monkeypatch.setattr("tests.dispatcher_fakes.INVOKE", scripts / "dispatcher-invoke.sh")

    proc = d.run_issue(STORY)

    assert proc.returncode == 1
    assert d.sessions == [f"implement {STORY}"]
    assert "No review path for executor 'executor:claude-code-haiku'" in proc.stderr
    assert len(d.posted(STORY)) == 1 and "/review completes successfully" in d.posted(STORY)[0]


# ── Resume at "In merge" ────────────────────────────────────────────────────


@needs_tools
@pytest.mark.parametrize("executor", ["claude-code-sonnet", "codex"])
def test_ac6_in_merge_story_with_open_pr_is_merged_and_cleaned_up(d, executor):
    d.add_story(STORY, "In merge", executor=executor)
    d.add_pr(PR, STORY)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"merge {PR}", f"cleanup {STORY}"]
    assert d.board_writes == [f"{STORY}=Done"]
    assert d.posted(STORY) == []
    assert f"[RESUME] #{STORY}: /merge PR #{PR}" in proc.stdout


@needs_tools
def test_ac7_in_merge_story_with_merged_pr_is_finished_without_merge(d):
    d.add_story(STORY, "In merge")
    d.add_pr(PR, STORY, state="closed", merged=True)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"cleanup {STORY}"]
    assert d.board_writes == [f"{STORY}=Done"]
    assert d.posted(STORY) == []


@needs_tools
@pytest.mark.parametrize("pr", ["none", "closed-unmerged", "draft"])
def test_ac8_in_merge_story_without_a_pr_gets_one_external_blocker(d, pr):
    d.add_story(STORY, "In merge")
    if pr == "closed-unmerged":
        d.add_pr(PR, STORY, state="closed")
    elif pr == "draft":
        d.add_pr(PR, STORY, draft=True)

    first = d.resume()
    second = d.resume()

    assert first.returncode == 0 and second.returncode == 0, first.stderr + second.stderr
    assert d.sessions == [] and d.board_writes == []
    blockers = d.posted(STORY)
    assert len(blockers) == 1
    assert "## Blocked Declaration\nType: external\n" in blockers[0]
    assert f"[RESUME SKIP] #{STORY}:" in second.stdout


@needs_tools
def test_ac9_one_merge_attempt_per_head(d):
    d.add_story(STORY, "In merge")
    d.add_pr(PR, STORY)
    d.fail("merge")

    first = d.resume()
    assert first.returncode == 1
    assert d.sessions == [f"merge {PR}"]
    blockers = d.posted(STORY)
    assert len(blockers) == 1 and "/merge completes successfully" in blockers[0]
    assert f"pr={PR} head={HEAD}" in blockers[0]
    assert d.status(STORY) == "In merge"

    # Same head: no session, no comment.
    second = d.resume()
    assert second.returncode == 0, second.stdout + second.stderr
    assert d.sessions == [f"merge {PR}"]
    assert len(d.posted(STORY)) == 1
    assert f"[RESUME SKIP] #{STORY}: /merge was already tried on PR #{PR}" in second.stdout

    # A comment that only mentions unblocking, or an /unblock line by someone else, changes nothing.
    d.add_comment(STORY, "please /unblock this", login=PO)
    d.add_comment(STORY, "/unblock", login="someone-else")
    assert d.resume().returncode == 0
    assert d.sessions == [f"merge {PR}"]

    # A standalone /unblock line from the PO after the blocker: one more try.
    d.add_comment(STORY, "Fixed the thread.\n/unblock", login=PO)
    third = d.resume()
    assert third.returncode == 1
    assert d.sessions == [f"merge {PR}"] * 2
    assert len(d.posted(STORY)) == 2

    # The new blocker is newer than the /unblock: skipped again.
    assert d.resume().returncode == 0
    assert d.sessions == [f"merge {PR}"] * 2

    # A new head: tried again, and this time it merges.
    d.add_pr(PR, STORY, head=NEW_HEAD)
    d.fail("merge", False)
    fourth = d.resume()
    assert fourth.returncode == 0, fourth.stdout + fourth.stderr
    assert d.sessions == [f"merge {PR}"] * 3 + [f"cleanup {STORY}"]
    assert d.status(STORY) == "Done"
    assert len(d.posted(STORY)) == 2


# ── Fix round ───────────────────────────────────────────────────────────────


@needs_tools
def test_ac10_fix_round_runs_implement_without_wip_or_overlap_checks(d):
    files = "## Files affected\n\n- `scripts/a.sh`\n"
    d.add_story(STORY, "In implementation", body=files)
    d.add_story(501, "In implementation", executor="codex", body=files)  # WIP limit reached, same file
    d.add_pr(PR, STORY)
    d.add_findings(PR, HEAD)
    new_head_on_implement(d, status=None)  # the session pushes but skips the status write

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {STORY}"]
    assert f"[RESUME] #{STORY}: fix round 1 of 3, /implement on PR #{PR}" in proc.stdout
    assert d.board_writes == [f"{STORY}=In review"]  # AC2: still "In implementation", no round due
    assert d.posted(STORY) == [] and d.posted(501) == []
    assert "WIP limit" not in proc.stdout and "File overlap" not in proc.stdout

    # The new head has no marker: nothing more to resume.
    again = d.resume()
    assert again.returncode == 0 and d.sessions == [f"implement {STORY}"]


@needs_tools
def test_ac10_fix_round_leaves_in_review_written_by_the_session(d):
    d.add_story(STORY, "In implementation")
    d.add_pr(PR, STORY)
    d.add_findings(PR, HEAD)
    new_head_on_implement(d, status="In review")

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {STORY}"]
    assert d.board_writes == []
    assert d.status(STORY) == "In review"


@needs_tools
@pytest.mark.parametrize("case", ["no-marker", "older-head", "other-account", "no-open-pr",
                                  "executor-not-on-loop", "two-executor-labels", "other-action"])
def test_ac11_no_fix_round(d, case):
    executor = "codex" if case == "executor-not-on-loop" else "claude-code-sonnet"
    labels = ["type:story", "executor:claude-code-sonnet", "executor:claude-code-opus"] \
        if case == "two-executor-labels" else None
    d.add_story(STORY, "In implementation", executor=executor, labels=labels)
    if case != "no-open-pr":
        d.add_pr(PR, STORY)
    if case == "older-head":
        d.add_findings(PR, "c" * 40)
    elif case == "other-account":
        d.add_findings(PR, HEAD, login="someone-else")
    elif case == "other-action":
        d.add_comment(PR, bookkeeping.action_marker(bookkeeping.ACTION_MOVE_IN_MERGE, HEAD))
    elif case != "no-marker":
        d.add_findings(PR, HEAD)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [] and d.board_writes == []
    assert d.posted(STORY) == [] and d.posted(PR) == []


@needs_tools
def test_ac12_fix_round_maximum(d):
    d.add_story(STORY, "In implementation")
    d.add_pr(PR, STORY)
    for head in ("1" * 40, "2" * 40, HEAD):
        d.add_findings(PR, head)
    d.add_findings(PR, HEAD)  # a second marker for the same head counts once

    # Three heads, maximum two: no session, one resource blocker, posted once.
    first = d.resume(env={"CODEX_FIX_ROUND_MAX": "2"})
    second = d.resume(env={"CODEX_FIX_ROUND_MAX": "2"})
    assert first.returncode == 0 and second.returncode == 0, first.stderr + second.stderr
    assert d.sessions == [] and d.board_writes == []
    blockers = d.posted(STORY)
    assert len(blockers) == 1 and "## Blocked Declaration\nType: resource\n" in blockers[0]
    assert f"[RESUME SKIP] #{STORY}: fix round 3 on PR #{PR} is above the maximum of 2" in second.stdout

    # The default maximum is three: the third round runs.
    new_head_on_implement(d)
    third = d.resume(env={"CODEX_FIX_ROUND_MAX": ""})
    assert third.returncode == 0, third.stdout + third.stderr
    assert f"[RESUME] #{STORY}: fix round 3 of 3, /implement on PR #{PR}" in third.stdout
    assert d.sessions == [f"implement {STORY}"]


@needs_tools
@pytest.mark.parametrize("value", ["0", "-1", "abc", "2.5", "3x"])
def test_ac12_invalid_maximum_stops_the_run(d, value):
    d.add_story(STORY, "In implementation")
    d.add_pr(PR, STORY)
    d.add_findings(PR, HEAD)

    proc = d.resume(env={"CODEX_FIX_ROUND_MAX": value})

    assert proc.returncode != 0
    assert "CODEX_FIX_ROUND_MAX must be a whole number of one or more" in proc.stderr and value in proc.stderr
    assert d.sessions == [] and d.posted(STORY) == [] and d.board_writes == []


@needs_tools
@pytest.mark.parametrize("seq,returncode,line", [("1", 0, "[RESERVE SKIP]"), ("2", 1, "[ALLOWANCE ERROR]")])
def test_fix_round_keeps_its_place_when_the_allowance_check_stops_it(d, seq, returncode, line):
    """A fix round is unfinished work: it is not put back to "Ready for implementation"."""
    d.add_story(700, "Ready for implementation")
    d.add_pr(970, 700)
    d.add_story(STORY, "In implementation")
    d.add_pr(PR, STORY)
    d.add_findings(PR, HEAD)

    first = d.run_issue(700, seq=seq)
    second = d.run_issue(700, seq=seq)

    for proc in (first, second):
        assert proc.returncode == returncode, proc.stdout + proc.stderr
        assert f"{line} #{STORY}:" in proc.stdout + proc.stderr
    assert d.sessions == [] and d.board_writes == []
    assert d.status(STORY) == "In implementation" and d.status(700) == "Ready for implementation"
    assert d.posted(STORY) == [] and d.posted(700) == [] and d.slack_calls == []

    # With allowance back, the fix round runs before the ready story.
    new_head_on_implement_once(d)
    third = d.run_issue(700)
    assert third.returncode == 0, third.stdout + third.stderr
    assert d.sessions == [f"implement {STORY}", "implement 700"]
    assert d.board_writes == ["700=In implementation", "700=In review"]


@needs_tools
@pytest.mark.parametrize("status_after_session", ["In review", "In merge", None])
def test_fix_round_that_pushes_nothing_is_recorded_whatever_the_status(d, status_after_session):
    """The session moved the story on without a commit; the bookkeeping job would move it back."""
    d.add_story(STORY, "In implementation")
    d.add_pr(PR, STORY)
    d.add_findings(PR, HEAD)
    if status_after_session:
        d.session_sets_status("implement", STORY, status_after_session)

    first = d.resume()

    assert first.returncode == 1
    assert d.sessions == [f"implement {STORY}"]
    assert d.board_writes == []
    blockers = d.posted(STORY)
    assert len(blockers) == 1 and "/implement completes successfully" in blockers[0]
    assert f"action=fix-round-attempt pr={PR} head={HEAD}" in blockers[0]

    # The bookkeeping job sees the findings for the same head and moves the story back.
    d.set_status(STORY, "In implementation")
    second = d.resume()
    assert second.returncode == 0, second.stdout + second.stderr
    assert d.sessions == [f"implement {STORY}"]  # the same round does not start again
    assert len(d.posted(STORY)) == 1
    assert f"[RESUME SKIP] #{STORY}: the fix round was already tried on PR #{PR}" in second.stdout


@needs_tools
def test_fix_round_that_fails_is_not_repeated_on_the_same_head(d):
    d.add_story(STORY, "In implementation")
    d.add_pr(PR, STORY)
    d.add_findings(PR, HEAD)
    d.fail("implement")

    first = d.resume()
    second = d.resume()

    assert first.returncode == 1 and second.returncode == 0
    assert d.sessions == [f"implement {STORY}"]
    assert len(d.posted(STORY)) == 1
    assert f"[RESUME SKIP] #{STORY}: the fix round was already tried on PR #{PR}" in second.stdout


@needs_tools
@pytest.mark.parametrize("held", [True, False])
def test_story_held_by_the_fix_round_maximum_is_not_rolled_back_as_stale(d, held):
    """Stale recovery would restart /implement as a ready story and pass the maximum."""
    d.add_story(700, "Ready for implementation")
    d.add_pr(970, 700)
    d.add_story(STORY, "In implementation")
    d.add_pr(PR, STORY)
    if held:
        for head in ("1" * 40, HEAD):
            d.add_findings(PR, head)

    proc = d.run_issue(700, env={"CODEX_FIX_ROUND_MAX": "1"})

    assert proc.returncode == 0, proc.stdout + proc.stderr
    comments = d.posted(STORY)
    if held:
        assert d.sessions == ["implement 700"]
        assert d.status(STORY) == "In implementation"
        assert len(comments) == 1 and "Type: resource" in comments[0]
        assert f"#{STORY} is held by a fix-round blocker" in proc.stdout
    else:
        # Without the blocker the story is rolled back and picked up as a ready story.
        assert len(comments) == 1 and "## Recovery Comment" in comments[0]
        assert d.sessions == ["implement 700", f"implement {STORY}"]


def test_ac10_implement_command_skips_the_wip_preflight_for_a_story_already_in_implementation():
    """The fix-round session runs implement.md: the prompt itself must not stop on WIP or file overlap."""
    text = (REPO_ROOT / ".claude" / "commands" / "implement.md").read_text(encoding="utf-8")
    step = re.search(r"^7\. Run the WIP pre-flight check.*?(?=^8\. )", text, re.MULTILINE | re.DOTALL)
    assert step
    bullets = re.findall(r"^   - (.+)$", step.group(0), re.MULTILINE)
    skip = bullets[0]  # stated before the stop conditions
    assert skip.startswith('skip this check when the story is already "In implementation" and has an open PR linked to it')
    assert "steps 10 and 17" in skip
    assert any(b.startswith("stop if 2 or more stories") for b in bullets[1:])
    assert any(b.startswith("stop if any active story declares overlapping files") for b in bullets[1:])
    # The canonical rule in docs/PROJECT-STATUS.md covers only the pull from "Ready for implementation".
    policy = (REPO_ROOT / "docs" / "PROJECT-STATUS.md").read_text(encoding="utf-8")
    assert "Before pulling a story from **Ready for implementation** into **In implementation**" in policy


# ── Order and entry ─────────────────────────────────────────────────────────


@needs_tools
def test_ac13_in_merge_then_fix_round_then_ready_story(d):
    d.add_story(700, "Ready for implementation")
    d.add_pr(970, 700)
    d.add_story(STORY, "In implementation")
    d.add_pr(PR, STORY)
    d.add_findings(PR, HEAD)
    d.add_story(600, "In merge")
    d.add_pr(960, 600)
    new_head_on_implement_once(d)

    proc = d.run_issue(700)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == ["merge 960", "cleanup 600", f"implement {STORY}", "implement 700"]
    assert d.board_writes == ["600=Done", "700=In implementation", "700=In review"]


@needs_tools
def test_feature_closure_is_notified_when_its_last_two_stories_finish_in_one_resume_pass(d):
    feature = "Feature group: #50 Feature: something"
    d.add_story(600, "In merge", body=feature)
    d.add_pr(960, 600)
    d.add_story(601, "In merge", body=feature)
    d.add_pr(961, 601)
    d.add_story(602, "Done", body=feature)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == ["merge 960", "cleanup 600", "merge 961", "cleanup 601"]
    assert proc.stdout.count("Feature #50: all 3 stories Done.") == 1
    assert len(d.slack_calls) == 1


@needs_tools
def test_feature_closure_is_not_notified_while_a_story_is_still_open(d):
    feature = "Feature group: #50 Feature: something"
    d.add_story(600, "In merge", body=feature)
    d.add_pr(960, 600)
    d.add_story(601, "In review", body=feature)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == ["merge 960", "cleanup 600"]
    assert "all 2 stories Done" not in proc.stdout and d.slack_calls == []


def poll_json(tmp_path, items: list[tuple[int, str]]) -> dict:
    """Run the real dispatcher-poll.sh against a fake ``curl`` that serves one board page."""
    nodes = [{"content": {"number": n, "title": f"Story {n}"},
              "fieldValues": {"nodes": [{"name": status, "field": {"name": "Status"}}]}} for n, status in items]
    board = {"data": {"repository": {"projectsV2": {"nodes": [{
        "number": 3, "title": "board",
        "items": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}]}}}}
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "board.json").write_text(json.dumps(board), encoding="utf-8")
    curl = bin_dir / "curl"
    curl.write_text('#!/usr/bin/env bash\ncat "$BOARD_FILE"\n', encoding="utf-8", newline="\n")
    curl.chmod(0o755)
    env = {"PATH": f"{bin_dir.as_posix()}:/usr/bin:/bin", "GH_TOKEN": "fake", "HOME": tmp_path.as_posix(),
           "BOARD_FILE": (tmp_path / "board.json").as_posix(),
           "DISPATCHER_STATE_DIR": (tmp_path / "state").as_posix()}
    if os.name == "nt":
        env["PATH"] = os.pathsep.join([str(bin_dir), os.environ["PATH"]])
    proc = subprocess.run(
        [BASH, (REPO_ROOT / "scripts" / "dispatcher-poll.sh").as_posix(), "--repo", "o/r", "--dry-run"],
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@needs_tools
def test_ac14_poll_lists_in_merge_and_in_implementation(tmp_path):
    out = poll_json(tmp_path, [(1, "Ready for implementation"), (2, "Ready for refinement"),
                               (3, "In merge"), (4, "In implementation"), (5, "In review"), (6, "Done")])
    assert out == {
        "ready_for_implementation": [{"number": 1, "title": "Story 1"}],
        "ready_for_refinement": [{"number": 2, "title": "Story 2"}],
        "in_merge": [{"number": 3, "title": "Story 3"}],
        "in_implementation": [{"number": 4, "title": "Story 4"}],
    }


@needs_tools
def test_ac14_poll_lists_are_empty_arrays_when_nothing_matches(tmp_path):
    out = poll_json(tmp_path, [(5, "In review")])
    assert out["in_merge"] == [] and out["in_implementation"] == []
    assert out["ready_for_implementation"] == [] and out["ready_for_refinement"] == []


@needs_tools
@pytest.mark.parametrize("missing", ["In review", "In merge"])
@pytest.mark.parametrize("mode", ["issue", "resume", "dry-run"])
def test_ac15_missing_board_option_stops_the_run_before_any_session(tmp_path, missing, mode):
    options = {name: value for name, value in BOARD_OPTIONS.items() if name != missing}
    d = Dispatcher(tmp_path, options=options)
    d.add_story(STORY, "Ready for implementation")
    d.add_pr(PR, STORY)

    proc = {"issue": lambda: d.run_issue(STORY), "resume": d.resume,
            "dry-run": lambda: d.run_issue(STORY, "--dry-run")}[mode]()

    assert proc.returncode != 0
    assert f'the project board has no "{missing}" Status option' in proc.stderr
    assert d.sessions == [] and d.board_writes == [] and d.posted(STORY) == []
    assert d.allowance_calls == 0


@needs_tools
def test_ac15_allowance_refusal_at_a_resume_entry_comments_once(d):
    d.add_story(STORY, "In merge")
    d.add_pr(PR, STORY)

    first = d.resume(seq="1")
    second = d.resume(seq="1")

    assert first.returncode == 0 and second.returncode == 0, first.stderr + second.stderr
    assert d.sessions == [] and d.status(STORY) == "In merge"
    comments = d.posted(STORY)
    assert len(comments) == 1 and "/merge was not started" in comments[0]
    assert "already the newest comment" in second.stdout

    # Once another comment is newer, a further refusal is reported again.
    d.add_comment(STORY, "noted", login=PO)
    assert d.resume(seq="1").returncode == 0
    assert len(d.posted(STORY)) == 2

    # With allowance back, the story is merged.
    assert d.resume().returncode == 0
    assert d.sessions == [f"merge {PR}", f"cleanup {STORY}"]


# ── Contracts with the bookkeeping job and the workflow ─────────────────────


def test_fix_round_marker_matches_the_bookkeeping_job():
    text = INVOKE.read_text(encoding="utf-8")
    declared = re.search(r'^BOOKKEEPING_FIX_MARKER="(.*)"$', text, re.MULTILINE)
    assert declared
    marker = bookkeeping.action_marker(bookkeeping.ACTION_MOVE_IN_IMPLEMENTATION, HEAD)
    assert marker.startswith(declared.group(1) + HEAD + " ")
    with_source = bookkeeping.action_marker(bookkeeping.ACTION_MOVE_IN_IMPLEMENTATION, HEAD, source="x")
    assert with_source.startswith(declared.group(1) + HEAD + " ")


def test_invoke_script_never_writes_the_review_trigger_or_the_legacy_status():
    from headless_pr_workflow.review_policy import CODEX_REVIEW_TRIGGER_PHRASE

    text = INVOKE.read_text(encoding="utf-8")
    assert CODEX_REVIEW_TRIGGER_PHRASE not in text
    assert "Ready to merge" not in text


WORKFLOW = REPO_ROOT / ".github" / "workflows" / "dispatcher.yml"
WORK_CONDITION = ("steps.guard.outputs.enabled == 'true' && "
                  "(steps.poll.outputs.issue_number != '' || steps.poll.outputs.resumable == 'true')")


def workflow_step(name: str) -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    matches = list(re.finditer(r"^      - name: (.+)$", text, re.MULTILINE))
    for i, match in enumerate(matches):
        if match.group(1).strip() == name:
            return text[match.start(): matches[i + 1].start() if i + 1 < len(matches) else len(text)]
    raise AssertionError(f"step {name!r} not found in {WORKFLOW}")


@pytest.mark.parametrize("name", ["Restore budget state cache", "Install Claude Code",
                                  "Run dispatcher invoke", "Save budget state cache"])
def test_ac14_workflow_steps_run_on_resumable_work(name):
    condition = re.search(r"^        if: (.+)$", workflow_step(name), re.MULTILINE)
    assert condition and condition.group(1).strip() == WORK_CONDITION


def test_ac14_workflow_poll_step_reports_resumable_work():
    body = workflow_step("Run dispatcher poll")
    assert ".in_merge" in body and ".in_implementation" in body
    assert 'echo "resumable=$RESUMABLE" >> "$GITHUB_OUTPUT"' in body
    assert 'echo "issue_number=$NEXT_ISSUE" >> "$GITHUB_OUTPUT"' in body


@needs_tools
@pytest.mark.parametrize("poll,expected", [
    ({"ready_for_implementation": [], "in_merge": [{"number": 3}], "in_implementation": []}, "true"),
    ({"ready_for_implementation": [], "in_merge": [], "in_implementation": [{"number": 4}]}, "true"),
    ({"ready_for_implementation": [{"number": 1}], "in_merge": [], "in_implementation": []}, "false"),
    ({"ready_for_implementation": [], "ready_for_refinement": []}, "false"),
])
def test_ac14_workflow_resumable_expression(poll, expected):
    """The jq expression in the poll step, run as written."""
    expression = re.search(r"RESUMABLE=\$\(echo \"\$POLL_JSON\" \| jq -r '(.+)'\)", workflow_step("Run dispatcher poll"))
    assert expression
    proc = subprocess.run(["jq", "-r", expression.group(1)], input=json.dumps(poll),
                          capture_output=True, text=True)
    assert proc.stdout.strip() == expected, proc.stderr


def test_ac14_workflow_invoke_step_resumes_without_a_ready_story():
    body = workflow_step("Run dispatcher invoke")
    env_block, run_block = body.split("        run:", 1)
    assert "CODEX_FIX_ROUND_MAX: ${{ vars.CODEX_FIX_ROUND_MAX }}" in env_block
    assert "NEXT_ISSUE: ${{ steps.poll.outputs.issue_number }}" in env_block
    assert re.search(r'if \[ -n "\$NEXT_ISSUE" \]; then\n\s+bash scripts/dispatcher-invoke\.sh .*--issue "\$NEXT_ISSUE"\n'
                     r'\s+else\n\s+bash scripts/dispatcher-invoke\.sh .*--resume\n', run_block), run_block


# ── Docs (AC16) ─────────────────────────────────────────────────────────────


def section(path: str, heading: str) -> str:
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    level = heading.split(" ", 1)[0]
    match = re.search(rf"^{re.escape(heading)}$\n(.*?)(?=^#{{1,{len(level)}}} |\Z)", text, re.MULTILINE | re.DOTALL)
    assert match, f"{heading!r} not found in {path}"
    return match.group(1)


def test_ac16_adapters_describes_the_review_loop():
    pairing = section("docs/ADAPTERS.md", "### Cross-Provider Review Pairing")
    for needle in ("Codex review loop", "ADR-006", "`In merge`", "fix round", "CODEX_FIX_ROUND_MAX",
                   "one `/merge` attempt per PR head"):
        assert needle in pairing, needle
    for tier in CLAUDE_TIERS:
        row = re.search(rf"^\| `executor:{tier}` \| (.+) \|$", pairing, re.MULTILINE)
        assert row and "Codex Cloud" in row.group(1) and "no dispatcher session" in row.group(1)
    assert "Ready to merge" not in (REPO_ROOT / "docs" / "ADAPTERS.md").read_text(encoding="utf-8")


def test_ac16_dispatcher_config_describes_hand_off_and_resume():
    text = (REPO_ROOT / "docs" / "DISPATCHER-CONFIG.md").read_text(encoding="utf-8")
    assert "Ready to merge" not in text
    flow = section("docs/DISPATCHER-CONFIG.md", "## Codex review loop hand-off and resume")
    for needle in ("### Hand-off after `/implement`", "### Resume at `In merge`", "### Fix round",
                   "### Order", "`CODEX_FIX_ROUND_MAX`", "One merge attempt per head",
                   "`--resume`", "[RESUME SKIP]", "`in_merge`", "`in_implementation`"):
        assert needle in flow, needle
    review_table = section("docs/DISPATCHER-CONFIG.md", "### Review path (issues #263 and #293)")
    for tier in CLAUDE_TIERS:
        assert re.search(rf"^\| `{tier}` \| Codex review loop", review_table, re.MULTILINE), tier
    assert re.search(r"^\| `codex` \| `/review` session under `claude-code-opus`", review_table, re.MULTILINE)
