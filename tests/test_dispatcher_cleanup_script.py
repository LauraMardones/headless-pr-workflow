"""scripts/dispatcher-cleanup.sh: /cleanup without a model session (issue #264).

The script orchestrates ``hpw post-merge-sync`` and ``hpw branch-cleanup``,
which own the safety classification and are tested in test_post_merge_sync*.py
and test_branch_cleanup*.py. These tests cover what the script itself decides:
merged state first, what is reported, and what counts as a failure. See
docs/DISPATCHER-MERGE-CLEANUP-MAP.md.
"""

from __future__ import annotations

import base64
import json
import re

import pytest

from tests.phase_script_fakes import HAVE_TOOLS, PhaseHarness

pytestmark = pytest.mark.skipif(not HAVE_TOOLS, reason="requires bash, jq, git and python3")

ISSUE = 12
PR = 77
REPO = "o/r"
COMMENTS = f"repos/{REPO}/issues/{ISSUE}/comments"

SYNC_OK = {"ok": True, "classification": "safe_fast_forward", "blocking_reasons": [], "manual_commands": [],
           "execution": {"failed_step": None}}
BRANCHES_OK = {"ok": True, "deleted": ["local:claude/issue-12-x", "remote:claude/issue-12-x"], "kept": [],
               "manual_commands": []}


@pytest.fixture
def h(tmp_path):
    harness = PhaseHarness(tmp_path)
    harness.fixture(f"repos/{REPO}/pulls",
                    [{"number": 70, "body": "Closes #99", "merged_at": "2026-10-13T00:00:00Z"},
                     {"number": PR, "body": f"Closes #{ISSUE}\n", "merged_at": "2026-10-13T00:00:00Z"},
                     {"number": 71, "body": f"Closes #{ISSUE}", "merged_at": None}])
    harness.fixture(f"repos/{REPO}/pulls/{PR}", {"merged_at": "2026-10-13T00:00:00Z"})
    harness.set("hpw_post-merge-sync.json", json.dumps(SYNC_OK))
    harness.set("hpw_branch-cleanup.json", json.dumps(BRANCHES_OK))
    return harness


def run(h, *extra):
    return h.run("dispatcher-cleanup.sh", "--issue", str(ISSUE), "--repo", REPO, *extra)


def hpw_calls(h):
    return h.log("hpw.log")


def test_report_names_what_was_done(h):
    proc = run(h)
    assert proc.returncode == 0, proc.stderr
    assert hpw_calls(h) == [f"hpw post-merge-sync {PR} --repo {REPO} --execute --json",
                            f"hpw branch-cleanup {PR} --repo {REPO} --execute --json"]
    assert "local base branch: safe_fast_forward" in proc.stdout
    assert "deleted: remote:claude/issue-12-x" in proc.stdout
    body = h.body(COMMENTS)
    assert body.startswith("## Session Summary\n") and "Command: cleanup" in body
    assert f"#{ISSUE}" in body and "Next: done" in body and "Blockers: none" in body


def test_explicit_pr_skips_the_lookup(h):
    proc = run(h, "--pr", str(PR))
    assert proc.returncode == 0
    assert not any("pulls?state=closed" in line for line in h.log("gh.log"))


def test_no_merged_pr_touches_nothing(h):
    h.fixture(f"repos/{REPO}/pulls", [{"number": 71, "body": f"Closes #{ISSUE}", "merged_at": None}])
    proc = run(h)
    assert proc.returncode == 1 and "nothing was changed" in proc.stderr
    assert hpw_calls(h) == [] and h.posts == []


def test_unmerged_pr_touches_nothing(h):
    h.fixture(f"repos/{REPO}/pulls/{PR}", {"merged_at": None})
    proc = run(h)
    assert proc.returncode == 1 and "does not report PR" in proc.stderr
    assert hpw_calls(h) == [] and h.posts == []


def test_unrelated_dirty_base_is_left_alone(h):
    h.set("hpw_post-merge-sync.json", json.dumps({
        "ok": False, "classification": "blocked_unrelated_dirty_work", "execution": None,
        "blocking_reasons": ["1 dirty file(s) are not part of the merged PR changes."],
        "manual_commands": ["git stash push -- notes.txt"]}))
    h.set("hpw_post-merge-sync.rc", "1")
    proc = run(h)
    assert proc.returncode == 0  # kept, reported, not a failure
    assert "local base branch kept (blocked_unrelated_dirty_work)" in proc.stdout
    assert "git stash push -- notes.txt" in proc.stdout
    assert hpw_calls(h)[-1].startswith("hpw branch-cleanup")  # the rest still runs


def test_stale_copy_is_restored_then_fast_forwarded(h):
    h.set("hpw_post-merge-sync.json", json.dumps({
        "ok": True, "classification": "verified_stale_pr_copy", "blocking_reasons": [], "manual_commands": [],
        "execution": {"failed_step": None, "steps_executed": ["backup", "restore", "fast-forward", "verify", "cleanup"]}}))
    proc = run(h)
    assert proc.returncode == 0
    assert "local base branch: verified_stale_pr_copy" in proc.stdout


def test_failed_sync_step_is_a_failure_and_is_recorded(h):
    h.set("hpw_post-merge-sync.json", json.dumps({
        "ok": False, "classification": "verified_stale_pr_copy", "blocking_reasons": [], "manual_commands": [],
        "execution": {"failed_step": "fast-forward"}}))
    h.set("hpw_post-merge-sync.rc", "1")
    proc = run(h)
    assert proc.returncode == 1
    assert "post-merge-sync failed at: fast-forward" in proc.stdout
    assert "post-merge-sync failed at: fast-forward" in h.body(COMMENTS)


def test_kept_branch_is_reported_not_failed(h):
    h.set("hpw_branch-cleanup.json", json.dumps({
        "ok": True, "deleted": [], "manual_commands": ["git worktree remove ../wt"],
        "kept": [{"branch": "claude/issue-12-x", "reason": "worktree is dirty"}]}))
    proc = run(h)
    assert proc.returncode == 0
    assert "claude/issue-12-x kept: worktree is dirty" in proc.stdout
    assert "git worktree remove ../wt" in proc.stdout


def test_branch_cleanup_report_with_ok_false_is_a_failure(h):
    h.set("hpw_branch-cleanup.json", json.dumps({
        "ok": False, "deleted": [], "manual_commands": ["git branch -d claude/issue-12-x"],
        "kept": [{"branch": "claude/issue-12-x", "reason": "deletion failed: permission denied"}]}))
    h.set("hpw_branch-cleanup.rc", "1")
    proc = run(h)
    assert proc.returncode == 1
    assert "branch-cleanup reported failure" in proc.stdout
    assert "git branch -d claude/issue-12-x" in proc.stdout  # the safe command is still reported


def test_post_merge_sync_error_report_is_a_failure(h):
    h.set("hpw_post-merge-sync.json", json.dumps({"ok": False, "error": "gh-command-failed", "message": "boom"}))
    h.set("hpw_post-merge-sync.rc", "1")
    proc = run(h)
    assert proc.returncode == 1 and "post-merge-sync produced no report: gh-command-failed" in proc.stdout


def test_helper_without_a_report_is_a_failure(h):
    h.set("hpw_branch-cleanup.json", "not json")
    h.set("hpw_branch-cleanup.rc", "2")
    proc = run(h)
    assert proc.returncode == 1 and "branch-cleanup produced no report" in proc.stdout


@pytest.mark.parametrize("args", [["--issue", "x", "--repo", REPO], ["--issue", "1", "--repo", "bad"], ["--issue", "1", "--repo", REPO, "--pr", "y"]])
def test_invalid_invocation_exits_2(h, args):
    assert h.run("dispatcher-cleanup.sh", *args).returncode == 2


def test_closing_comment_failure_fails_the_phase_with_the_text_logged(h):
    h.set("post_fail", "")
    proc = run(h)
    assert proc.returncode == 1
    assert "could not be posted after 3 attempts" in proc.stderr and "Command: cleanup" in proc.stderr
    assert len([line for line in h.log("gh.log") if "-X POST" in line]) == 6  # 3 closing + 3 pending attempts


def test_failed_closing_comment_leaves_the_exact_text_as_a_pending_comment(h):
    h.set("post_fail_times", "3")  # the three closing attempts fail, the pending comment is written
    proc = run(h)
    assert proc.returncode == 1 and "A later dispatcher run posts it" in proc.stderr
    assert h.posts == [f"POST {COMMENTS}"]
    body = h.body(COMMENTS)
    match = re.search(r"action=evidence-pending item=cleanup-summary head=- payload=([A-Za-z0-9+/=]*) -->", body)
    assert match, body
    payload = base64.b64decode(match.group(1)).decode("utf-8")
    assert payload.startswith("## Session Summary\n") and "Command: cleanup" in payload
    assert not body.startswith("## Session Summary")  # never mistaken for the evidence
