"""scripts/dispatcher-merge.sh: /merge without a model session (issue #264).

Each test maps to a step of .claude/commands/merge.md; see
docs/DISPATCHER-MERGE-CLEANUP-MAP.md.
"""

from __future__ import annotations

import json

import pytest

from tests.phase_script_fakes import HAVE_TOOLS, PhaseHarness

pytestmark = pytest.mark.skipif(not HAVE_TOOLS, reason="requires bash, jq, git and python3")

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
    h.set("hpw_merge-owner.json", json.dumps({"blocking_reasons": ["Expected merge owner evidence is missing"]}))
    proc = run(h)
    assert proc.returncode == 1 and "ownership gate not passed" in proc.stderr
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
    proc = run(h)
    assert proc.returncode == 1 and "ownership gate not passed" in proc.stderr
    assert_no_mutation(h)


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
    assert len([line for line in h.log("gh.log") if "-X POST" in line]) == 3


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
