"""/merge and /cleanup are script-only phases of the dispatcher (issue #264).

Drives the real ``scripts/dispatcher-invoke.sh`` with the fakes in
``tests/dispatcher_fakes.py``. The fake ``claude``, the fake ``curl`` (the
OpenAI API) and the stub allowance script record every call; the merge and
cleanup scripts are stubs that log to ``phases.log``. The scripts' own
behavior is tested in test_dispatcher_merge_script.py and
test_dispatcher_cleanup_script.py.
"""

from __future__ import annotations

import re

import pytest

from tests.dispatcher_fakes import HAVE_TOOLS, REPO_ROOT, Dispatcher

pytestmark = pytest.mark.skipif(not HAVE_TOOLS, reason="requires bash, jq and timeout")

STORY = 500
PR = 900
INVOKE = (REPO_ROOT / "scripts" / "dispatcher-invoke.sh").read_text(encoding="utf-8")


def assert_zero_model_footprint(d, proc):
    assert d.claude_calls == []
    assert not [line for line in d._log("curl.log") if "openai" in line]
    assert d.allowance_calls == 0
    assert list((d.mock / "usage").iterdir()) == []  # no session counted (#320)
    assert d.sessions == []
    assert "Subscription allowance" not in "".join(d.posted(STORY))


@pytest.mark.parametrize("executor", ["claude-code-sonnet", "codex"])
def test_resume_at_in_merge_starts_no_session(tmp_path, executor):
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "In merge", executor=executor)
    d.add_pr(PR, STORY)
    proc = d.resume(seq="1")  # a refusing allowance must not matter
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.phases == [f"merge {PR}", f"cleanup {STORY}"]
    assert_zero_model_footprint(d, proc)


def test_resume_with_a_merged_pr_runs_only_cleanup(tmp_path):
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "In merge")
    d.add_pr(PR, STORY, state="closed", merged=True)
    proc = d.resume(seq="1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.phases == [f"cleanup {STORY}"]
    assert_zero_model_footprint(d, proc)


def test_paired_codex_run_has_two_sessions_then_two_scripts(tmp_path):
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "Ready for implementation", executor="codex")
    d.add_pr(PR, STORY)
    proc = d.run_issue(STORY, seq="0,0,1,1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {STORY}", f"review {PR}"]
    assert d.phases == [f"merge {PR}", f"cleanup {STORY}"]
    assert d.allowance_calls == 2
    assert d.board_writes[-2:] == [f"{STORY}=In merge", f"{STORY}=Done"]
    assert d.usage_count == 2  # /implement and /review only; the scripts count nothing


def test_implement_session_is_followed_by_the_merge_owner_marker_on_the_pr(tmp_path):
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "Ready for implementation", executor="codex")
    d.add_pr(PR, STORY)
    d.on_session("implement", """jq 'map(.head.sha = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")' "$MOCK_DIR/pulls.json" > "$MOCK_DIR/p.tmp" && mv "$MOCK_DIR/p.tmp" "$MOCK_DIR/pulls.json"
""")
    proc = d.run_issue(STORY, seq="0,0,1,1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    owner = [b for b in d.posted(PR) if "action=merge-owner" in b]
    assert len(owner) == 1 and re.fullmatch(rf"<!-- hpw:dispatcher action=merge-owner pr={PR} head=\S+ -->", owner[0])


def test_no_owner_marker_when_implement_pushed_no_new_head(tmp_path):
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "Ready for implementation", executor="codex")
    d.add_pr(PR, STORY)  # the PR already has its head; the fake session pushes nothing
    d.run_issue(STORY, seq="0,0,1,1")
    assert [b for b in d.posted(PR) if "action=merge-owner" in b] == []


def test_merge_script_failure_posts_the_marker_blocker_and_keeps_in_merge(tmp_path):
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "In merge")
    d.add_pr(PR, STORY)
    d.fail("merge")
    proc = d.resume()
    assert proc.returncode == 1
    blockers = d.posted(STORY)
    assert len(blockers) == 1 and f"action=merge-attempt pr={PR} head=" in blockers[0]
    assert d.status(STORY) == "In merge"
    assert d.phases == [f"merge {PR}"]  # cleanup does not run
    assert d.resume().returncode == 0 and d.phases == [f"merge {PR}"]  # one attempt per head


def test_cleanup_script_failure_is_non_fatal_and_the_story_stays_done(tmp_path):
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "In merge")
    d.add_story(501, "In merge")
    d.add_pr(PR, STORY)
    d.add_pr(PR + 1, 501)
    for story in (STORY, 501):  # the closing summary is on the story: only other steps failed
        d.add_comment(story, "## Session Summary\nCommand: cleanup\nIssue/PR: #1 / #2")
    d.fail("cleanup")
    proc = d.resume()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"/cleanup failed for #{STORY}; story is Done" in proc.stderr
    assert d.status(STORY) == "Done" and d.status(501) == "Done"
    assert d.phases == [f"merge {PR}", f"cleanup {STORY}", f"merge {PR + 1}", f"cleanup 501"]  # the loop continued
    assert d.posted(STORY) == []


def test_348_cleanup_that_left_no_evidence_on_the_story_goes_back_to_in_merge(tmp_path):
    """Neither the closing summary nor its pending copy is on GitHub: nothing else would revisit the story."""
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "In merge")
    d.add_pr(PR, STORY)
    d.fail("cleanup")
    proc = d.resume()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.status(STORY) == "In merge"
    assert d.phases == [f"merge {PR}", f"cleanup {STORY}"]
    d.fail("cleanup", False)  # the next run reruns cleanup
    assert d.resume().returncode == 0
    assert d.status(STORY) == "Done" and d.phases[-1] == f"cleanup {STORY}"


def test_348_cleanup_without_evidence_gives_up_after_three_runs_and_stays_done(tmp_path):
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "In merge")
    d.add_pr(PR, STORY)
    d.fail("cleanup")
    for _ in range(3):
        assert d.resume().returncode == 0
    assert d.status(STORY) == "Done"
    assert len([b for b in d.posted(STORY) if "## Blocked Declaration" in b]) == 1
    assert len(d.slack_calls) == 1


def test_348_merged_pr_without_any_confirmation_does_not_reach_cleanup(tmp_path):
    """A merge script that failed before writing the confirmation or a pending copy must not be skipped."""
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "In merge")
    d.add_pr(PR, STORY)
    d.fail("merge")
    d.on_session("merge", """jq 'map(.state = "closed" | .merged_at = "2026-10-13T09:30:00Z")' "$MOCK_DIR/pulls.json" > "$MOCK_DIR/p.tmp" && mv "$MOCK_DIR/p.tmp" "$MOCK_DIR/pulls.json"\n""")
    d.resume()
    d.fail("merge", False)
    for _ in range(3):  # later runs find the merged PR with no confirmation
        assert d.resume().returncode == 0
    assert d.status(STORY) != "Done" and not any(p.startswith("cleanup") for p in d.phases)
    assert len([b for b in d.posted(STORY) if "## Blocked Declaration" in b]) >= 1


def test_348_missing_confirmation_records_no_failure_after_the_give_up_blocker(tmp_path):
    """After the give-up blocker, later runs wait for /unblock instead of starting a new count every poll."""
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "In merge")
    d.add_pr(PR, STORY)
    d.fail("merge")
    d.on_session("merge", """jq 'map(.state = "closed" | .merged_at = "2026-10-13T09:30:00Z")' "$MOCK_DIR/pulls.json" > "$MOCK_DIR/p.tmp" && mv "$MOCK_DIR/p.tmp" "$MOCK_DIR/pulls.json"\n""")
    d.resume()
    d.fail("merge", False)
    for _ in range(3):
        assert d.resume().returncode == 0
    gave_up = "action=evidence-gave-up item=merge-confirmation-missing"
    failed = "action=evidence-retry-failed item=merge-confirmation-missing"
    assert len([b for b in d.posted(STORY) if gave_up in b]) == 1
    posted, slack = len(d.posted(STORY)), len(d.slack_calls)

    for _ in range(2):
        proc = d.resume()
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "waiting for a PO /unblock line" in proc.stdout

    assert len(d.posted(STORY)) == posted and len(d.slack_calls) == slack
    assert len([b for b in d.posted(STORY) if failed in b]) == 3
    assert d.status(STORY) == "In merge" and not any(p.startswith("cleanup") for p in d.phases)


def test_dry_run_runs_no_script(tmp_path):
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "In merge")
    d.add_pr(PR, STORY)
    proc = d.resume("--dry-run")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.phases == [] and "no model session" in proc.stdout


# ── Invocation-path isolation ────────────────────────────────────────────────


def test_only_implement_and_review_go_through_invoke_executor_command():
    calls = re.findall(r'invoke_executor_command "(\w+)"', INVOKE)
    assert sorted(set(calls)) == ["implement", "review"]


def test_merge_and_cleanup_path_does_not_use_sessions_allowance_or_budget():
    body = re.search(r"run_phase_script\(\) \{.*?\n\}\n", INVOKE, re.S).group(0)
    assert "ALLOWANCE_SCRIPT" not in body and "BUDGET" not in body.upper().replace("BUDGET_", "")
    assert "check_subscription_allowance" not in body and "run_claude_code_session" not in body
    for name in ("run_merge_or_block", "finish_story"):
        func = re.search(rf"{name}\(\) \{{.*?\n\}}\n", INVOKE, re.S).group(0)
        assert "invoke_executor_command" not in func and "EXECUTOR_BUDGET_TYPE" not in func
        assert "run_phase_script" in func


def test_dispatcher_does_not_read_the_manual_command_files_for_merge_or_cleanup():
    for script in ("dispatcher-merge.sh", "dispatcher-cleanup.sh"):
        text = (REPO_ROOT / "scripts" / script).read_text(encoding="utf-8")
        assert ".claude/commands" not in re.sub(r"^#.*$", "", text, flags=re.M)
    for command in ("merge", "cleanup"):
        text = (REPO_ROOT / ".claude" / "commands" / f"{command}.md").read_text(encoding="utf-8")
        assert "dispatcher-merge" not in text and "dispatcher-cleanup" not in text


def test_allowance_code_has_no_merge_or_cleanup_wording():
    func = re.search(r"allowance_comment\(\) \{.*?\n\}\n", INVOKE, re.S).group(0)
    assert '"merge"' not in func and "/merge" not in func and "/cleanup" not in func
    assert "/review, /merge or /cleanup" not in INVOKE
