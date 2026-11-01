"""scripts/dispatcher-merge.sh: /merge without a model session (issue #264).

Each test maps to a step of .claude/commands/merge.md; see
docs/DISPATCHER-MERGE-CLEANUP-MAP.md.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
from pathlib import Path

import pytest

from tests.phase_script_fakes import REPO_ROOT, PhaseHarness, needs_tools

pytestmark = needs_tools

PR = 77
REPO = "o/r"
HEAD = "a" * 40
GATE = "Merge gate: policy=pass(present_non_required), checks=absent-ok, approval=formal, threads=none, mergeable=yes, head=aaaaaaa"
BRANCH = "claude/issue-1-x"


@pytest.fixture
def h(tmp_path):
    harness = PhaseHarness(tmp_path)
    harness.fixture(f"repos/{REPO}/pulls/{PR}/files", [{"filename": "src/a.py"}, {"filename": "docs/x.md"}])
    harness.fixture(f"repos/{REPO}/pulls/{PR}", pr_json())
    harness.fixture("user", {"login": "bot"})
    harness.fixture(f"repos/{REPO}/issues/{PR}/comments", [owner_marker(HEAD)])
    harness.set("gate_line", GATE + "\n")
    harness.set("hpw_merge-pr.json", json.dumps({"merged": True, "head_sha": HEAD, "blocking_reasons": []}))
    harness.git("init", "-q", "-b", "main")
    harness.git("commit", "-q", "--allow-empty", "-m", "init")
    return harness


def owner_marker(head, login="bot", pr=PR):
    return {"user": {"login": login}, "body": f"<!-- hpw:dispatcher action=merge-owner pr={pr} head={head} -->"}


def owner_calls(h):
    return [line for line in h.log("hpw.log") if line.startswith("hpw merge-owner")]


def pr_json(**overrides):
    value = {"state": "open", "draft": False, "merged_at": None, "head": {"sha": HEAD, "ref": BRANCH}}
    value.update(overrides)
    return value


def run(h, *extra):
    return h.run("dispatcher-merge.sh", "--pr", str(PR), "--repo", REPO, *extra)


def merge_calls(h):
    return [line for line in h.log("hpw.log") if line.startswith("hpw merge-pr")]


def assert_no_mutation(h):
    assert h.posts == [] and merge_calls(h) == []


@pytest.mark.parametrize("path", ["specs/160-dispatcher/spec.md", "docs/decisions/ADR-013-x.md"])
def test_decision_document_pr_is_refused(h, path):
    h.fixture(f"repos/{REPO}/pulls/{PR}/files", [{"filename": "src/a.py"}, {"filename": path}])
    proc = run(h)
    assert proc.returncode == 1
    assert "ADR-011" in proc.stderr and "PO merges" in proc.stderr
    assert_no_mutation(h) and h.log("gate.log") == []


def test_adr_lookalike_paths_are_not_decision_documents(h):
    h.fixture(f"repos/{REPO}/pulls/{PR}/files", [{"filename": "docs/decisions/README.md"}, {"filename": "specs/notes.md"}])
    assert run(h).returncode == 0


def test_graphql_preflight_failure_makes_no_mutation(h):
    h.set("graphql_fail", "")
    proc = run(h)
    assert proc.returncode == 1
    assert "GraphQL-capable" in proc.stderr
    assert_no_mutation(h) and h.log("gate.log") == []
    assert not any("pulls" in line for line in h.log("gh.log"))  # failed before any other call


def test_gate_exit_0_merges_and_comments(h):
    proc = run(h)
    assert proc.returncode == 0, proc.stderr
    assert merge_calls(h) == [f"hpw merge-pr {PR} --repo {REPO} --method squash --execute --expect-head {HEAD} --json"]
    assert h.posts == [f"POST repos/{REPO}/issues/{PR}/comments"]
    body = h.body(f"repos/{REPO}/issues/{PR}/comments")
    assert body.startswith(GATE + "\n\n## Session Summary\n")  # exact line, outside the generated block
    assert "Command: merge" in body and HEAD in body
    assert "Next: cleanup" in body


def test_owner_evidence_for_the_current_head_is_passed_to_merge_owner(h):
    assert run(h).returncode == 0
    assert owner_calls(h) == [
        f"hpw merge-owner {PR} --repo {REPO} --session-id dispatcher --expected-owner dispatcher "
        f"--expected-owner-sha {HEAD} --json"
    ]


def test_missing_owner_evidence_for_the_head_blocks_before_the_merge(h):
    h.fixture(f"repos/{REPO}/issues/{PR}/comments", [owner_marker("b" * 40)])  # an older head
    h.set("hpw_merge-owner.rc", "1")
    h.set("hpw_merge-owner.json", json.dumps({"ownership_status": "unknown_owner",
                                              "blocking_reasons": ["Expected merge owner evidence is missing"]}))
    proc = run(h)
    assert proc.returncode == 1 and "ownership gate not passed" in proc.stderr
    assert "status: unknown_owner" in proc.stderr and "Expected merge owner evidence is missing" in proc.stderr
    assert "no dispatcher owner marker for head aaaaaaa" in proc.stderr
    assert "--expected-owner" not in owner_calls(h)[0]
    assert_no_mutation(h) and h.log("gate.log") == []


@pytest.mark.parametrize("comment", [
    # a manual /implement handoff names the head but is not the dispatcher's marker
    {"user": {"login": "bot"}, "body": "## Session Summary\nCommand: implement\nIssue/PR: #1 / #" + str(PR) + "\nHead: " + HEAD + "\nNext: review\n"},
    # the marker text posted by anyone but the dispatcher's own account
    owner_marker(HEAD, login="someone-else"),
    # a marker for another PR
    owner_marker(HEAD, pr=PR + 1),
])
def test_only_the_dispatcher_owner_marker_for_this_head_is_owner_evidence(h, comment):
    h.fixture(f"repos/{REPO}/issues/{PR}/comments", [comment])
    run(h)
    assert owner_calls(h) and "--expected-owner" not in owner_calls(h)[0]


def test_ownership_gate_failure_blocks_even_with_evidence(h):
    h.set("hpw_merge-owner.rc", "1")
    h.set("hpw_merge-owner.json", json.dumps({"ownership_status": "takeover_required",
                                              "blocking_reasons": ["Current session is not the expected owner"]}))
    proc = run(h)
    assert proc.returncode == 1 and "ownership gate not passed" in proc.stderr
    assert "status: takeover_required" in proc.stderr and "Current session is not the expected owner" in proc.stderr
    assert "owner marker" not in proc.stderr  # the marker for this head was found
    assert_no_mutation(h)


def gh_error_json(error="gh-command-failed", stderr="HTTP 403: Resource not accessible by personal access token"):
    """The shape of hpw's _print_gh_error output (src/headless_pr_workflow/cli.py)."""
    return json.dumps({"ok": False, "error": error, "command": ["gh", "pr", "view", str(PR)],
                       "returncode": 1, "stderr": stderr}, indent=2)


def assert_not_an_ownership_message(stderr):
    assert "owner marker" not in stderr and "takeover required" not in stderr
    assert "ownership gate not passed" not in stderr


def test_merge_owner_gh_error_names_the_gh_error_not_ownership(h):
    h.set("hpw_merge-owner.rc", "1")
    h.set("hpw_merge-owner.json", gh_error_json())
    proc = run(h)
    assert proc.returncode == 1
    assert "hpw merge-owner could not evaluate ownership" in proc.stderr
    assert "gh-command-failed" in proc.stderr and "gh exit 1" in proc.stderr
    assert "HTTP 403: Resource not accessible by personal access token" in proc.stderr
    assert_not_an_ownership_message(proc.stderr)
    assert_no_mutation(h) and h.log("gate.log") == []


@pytest.mark.parametrize("rc,stdout", [
    ("1", ""),
    ("1", "Traceback (most recent call last):\n  File \"cli.py\", line 1\n"),
    ("127", ""),  # hpw could not start
])
def test_merge_owner_without_usable_output_quotes_hpw_stderr(h, rc, stdout):
    h.set("hpw_merge-owner.rc", rc)
    h.set("hpw_merge-owner.json", stdout)
    h.set("hpw_merge-owner.stderr", "ModuleNotFoundError: No module named 'headless_pr_workflow'\n")
    proc = run(h)
    assert proc.returncode == 1
    assert f"hpw merge-owner failed (exit {rc}) without a usable result" in proc.stderr
    assert "ModuleNotFoundError: No module named 'headless_pr_workflow'" in proc.stderr
    assert_not_an_ownership_message(proc.stderr)
    assert_no_mutation(h) and h.log("gate.log") == []


def test_merge_owner_failure_with_only_stdout_quotes_stdout(h):
    h.set("hpw_merge-owner.rc", "1")
    h.set("hpw_merge-owner.json", "Traceback (most recent call last):\nKeyError: 'headRefOid'\n")
    proc = run(h)
    assert proc.returncode == 1 and "without a usable result" in proc.stderr
    assert "KeyError: 'headRefOid'" in proc.stderr


def test_merge_owner_json_without_reasons_or_error_is_not_usable(h):
    h.set("hpw_merge-owner.rc", "1")
    h.set("hpw_merge-owner.json", json.dumps({"ownership_status": "unknown_owner", "blocking_reasons": []}))
    proc = run(h)
    assert proc.returncode == 1 and "hpw merge-owner failed (exit 1) without a usable result" in proc.stderr
    assert_not_an_ownership_message(proc.stderr)


def long_text(lines=500):
    return "".join(f"line {i:04d} " + "x" * 90 + "\n" for i in range(lines))


def test_quoted_hpw_stderr_is_bounded(h):
    h.set("hpw_merge-owner.rc", "1")
    h.set("hpw_merge-owner.stderr", long_text())
    proc = run(h)
    assert proc.returncode == 1 and "line 0499" in proc.stderr  # the end is kept
    assert "line 0000" not in proc.stderr and len(proc.stderr) < 2500


def test_quoted_gh_stderr_is_bounded(h):
    h.set("hpw_merge-owner.rc", "1")
    h.set("hpw_merge-owner.json", gh_error_json(stderr=long_text()))
    proc = run(h)
    assert proc.returncode == 1 and "line 0499" in proc.stderr
    assert "line 0000" not in proc.stderr and len(proc.stderr) < 2500


# The real hpw starts ``gh`` through Python's subprocess; on Windows that finds only ``gh.exe``, never the
# bash fake, so the run would reach the host's real gh (issue #384).
@pytest.mark.skipif(os.name == "nt", reason="on Windows the real hpw cannot run the bash gh fake (needs gh.exe)")
def test_real_hpw_merge_owner_reports_the_failing_gh_call(h):
    """AC6 (#374): the real scripts/hpw, not the HPW_BIN stub, against a failing gh."""
    shim = h.bin / "python"  # scripts/hpw runs `python -m headless_pr_workflow.cli`
    shim.write_text(f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" "$@"\n', encoding="utf-8", newline="\n")
    shim.chmod(0o755)
    h.set("gh_fail_stderr", "HTTP 403: Resource not accessible by personal access token (repository.pullRequest)\n")
    proc = h.run("dispatcher-merge.sh", "--pr", str(PR), "--repo", REPO,
                 env_overrides={"HPW_BIN": (REPO_ROOT / "scripts" / "hpw").as_posix()})
    assert proc.returncode == 1
    assert "hpw merge-owner could not evaluate ownership" in proc.stderr
    assert "HTTP 403: Resource not accessible by personal access token (repository.pullRequest)" in proc.stderr
    assert_not_an_ownership_message(proc.stderr)
    assert any(line.startswith(f"gh pr view {PR}") for line in h.log("gh.log"))  # the real hpw called gh
    assert h.posts == [] and h.log("gate.log") == []


def test_unreadable_comments_block(h):
    (h.mock / f"repos_{REPO.replace('/', '_')}_issues_{PR}_comments.json").unlink()
    proc = run(h)
    assert proc.returncode == 1 and "ownership cannot be verified" in proc.stderr
    assert_no_mutation(h)


def test_confirmation_comment_failure_fails_the_phase_with_the_text_logged(h):
    h.set("post_fail", "")
    proc = run(h)
    assert proc.returncode == 1
    assert "could not be posted after 3 attempts" in proc.stderr and GATE in proc.stderr
    assert len([line for line in h.log("gh.log") if "-X POST" in line]) == 6  # 3 confirmation + 3 pending attempts


@pytest.mark.parametrize("rc", [1, 2])
def test_gate_failure_blocks_without_merge(h, rc):
    h.set("gate_rc", str(rc))
    proc = run(h)
    assert proc.returncode == 1
    assert f"merge gate exit {rc}" in proc.stderr
    assert GATE in proc.stdout  # the evidence line is still shown
    assert_no_mutation(h)


def test_head_changing_after_the_gate_blocks(h):
    h.set("gate_line", GATE.replace("head=aaaaaaa", "head=bbbbbbb") + "\n")
    proc = run(h)
    assert proc.returncode == 1 and "not the refreshed head" in proc.stderr
    assert_no_mutation(h)


def test_draft_pr_is_not_merged(h):
    h.fixture(f"repos/{REPO}/pulls/{PR}", pr_json(draft=True))
    assert run(h).returncode == 1
    assert_no_mutation(h) and h.log("gate.log") == []


def test_closed_unmerged_pr_is_not_merged(h):
    h.fixture(f"repos/{REPO}/pulls/{PR}", pr_json(state="closed"))
    proc = run(h)
    assert proc.returncode == 1 and "not open" in proc.stderr
    assert_no_mutation(h)


def test_already_merged_pr_is_a_success_without_a_second_merge(h):
    h.fixture(f"repos/{REPO}/pulls/{PR}", pr_json(state="closed", merged_at="2026-10-14T00:00:00Z"))
    assert run(h).returncode == 0
    assert_no_mutation(h)


def test_merge_command_failure_exits_1(h):
    h.set("hpw_merge-pr.rc", "1")
    h.set("hpw_merge-pr.json", json.dumps({"merged": False, "blocking_reasons": ["GitHub merge command failed."]}))
    proc = run(h)
    assert proc.returncode == 1 and "GitHub merge command failed." in proc.stderr
    assert h.posts == []  # no confirmation without a confirmed merge


def test_merged_state_must_be_confirmed(h):
    h.set("hpw_merge-pr.json", json.dumps({"merged": False, "blocking_reasons": ["GitHub does not report PR as merged"]}))
    assert run(h).returncode == 1
    assert h.posts == []


def test_merge_pr_gh_error_names_the_gh_error(h):
    h.set("hpw_merge-pr.rc", "1")
    h.set("hpw_merge-pr.json", gh_error_json(stderr="GraphQL: Pull request is not mergeable"))
    proc = run(h)
    assert proc.returncode == 1 and "hpw merge-pr did not confirm a merge (exit 1)" in proc.stderr
    assert "gh-command-failed" in proc.stderr and "GraphQL: Pull request is not mergeable" in proc.stderr
    assert h.posts == []


def test_merge_pr_without_usable_output_quotes_hpw_stderr(h):
    h.set("hpw_merge-pr.rc", "1")
    h.set("hpw_merge-pr.json", "")
    h.set("hpw_merge-pr.stderr", "Traceback (most recent call last):\nRuntimeError: boom\n")
    proc = run(h)
    assert proc.returncode == 1 and "hpw merge-pr failed (exit 1) without a usable result" in proc.stderr
    assert "RuntimeError: boom" in proc.stderr
    assert h.posts == []


def test_merged_local_branch_is_deleted(h):
    h.git("branch", BRANCH)
    proc = run(h)
    assert proc.returncode == 0
    assert f"Deleted local branch {BRANCH}" in proc.stdout
    assert BRANCH not in h.git("branch", "--list")


def test_unmerged_local_branch_is_kept_not_forced(h):
    h.git("checkout", "-q", "-b", BRANCH)
    h.git("commit", "-q", "--allow-empty", "-m", "unique work")
    h.git("checkout", "-q", "main")
    proc = run(h)
    assert proc.returncode == 0 and "Kept local branch" in proc.stdout and "cleanup session" in proc.stdout
    assert BRANCH in h.git("branch", "--list")


def test_checked_out_branch_is_kept(h):
    h.git("checkout", "-q", "-b", BRANCH)
    assert "is checked out" in run(h).stdout
    assert BRANCH in h.git("branch", "--list")


def test_branch_used_by_other_worktree_is_kept(h, tmp_path):
    h.git("branch", BRANCH)
    h.git("worktree", "add", "-q", (tmp_path / "wt").as_posix(), BRANCH)
    assert "another worktree uses it" in run(h).stdout
    assert BRANCH in h.git("branch", "--list")


def test_dirty_checkout_keeps_the_local_branch(h):
    h.git("branch", BRANCH)
    (h.cwd / "scratch.txt").write_text("unrelated work")
    assert "not clean" in run(h).stdout
    assert BRANCH in h.git("branch", "--list")


@pytest.mark.parametrize("args", [["--pr", "x", "--repo", REPO], ["--pr", "1", "--repo", "bad"], ["--pr", "1", "--repo", REPO, "--method", "ff"]])
def test_invalid_invocation_exits_2(h, args):
    assert h.run("dispatcher-merge.sh", *args).returncode == 2


def pending_payload(body):
    match = re.search(r"action=evidence-pending item=merge-confirmation head=(\S+) payload=([A-Za-z0-9+/=]*) -->", body)
    assert match, body
    return match.group(1), base64.b64decode(match.group(2)).decode("utf-8")


def test_failed_confirmation_leaves_the_exact_text_as_a_pending_comment(h):
    h.set("post_fail_times", "3")  # the three confirmation attempts fail, the pending comment is written
    proc = run(h)
    assert proc.returncode == 1 and "A later dispatcher run posts it" in proc.stderr
    assert h.posts == [f"POST repos/{REPO}/issues/{PR}/comments"]
    head, payload = pending_payload(h.body(f"repos/{REPO}/issues/{PR}/comments"))
    assert head == HEAD
    assert payload.startswith(GATE + "\n\n## Session Summary\n") and "Command: merge" in payload
    assert "Merge gate:" not in h.body(f"repos/{REPO}/issues/{PR}/comments").split("<!--")[0]  # never mistaken for the evidence


def test_confirmation_that_posts_first_time_leaves_no_pending_comment(h):
    assert run(h).returncode == 0
    assert "evidence-pending" not in h.body(f"repos/{REPO}/issues/{PR}/comments")
