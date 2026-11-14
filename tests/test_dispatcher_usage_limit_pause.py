"""A session stopped by a provider usage limit pauses and starts again (issue #316, ADR-012).

Drives the real ``scripts/dispatcher-invoke.sh`` with the fakes in
``tests/dispatcher_fakes.py``; the fake ``claude`` ends with a rate-limited
stream. The helper itself is exercised through ``scripts/lib/claude-session.sh``.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from tests.dispatcher_fakes import BASH, HAVE_TOOLS, REPO_ROOT, Dispatcher, destructive_calls

pytestmark = pytest.mark.skipif(not HAVE_TOOLS, reason="requires bash, jq and timeout")

STORY = 500
PR = 900
HEAD = "a" * 40
RESET = 1760000000  # 2025-10-09T08:53:20Z → "2025-10-09T08:53Z"
RESET_TEXT = "2025-10-09T08:53Z"
INIT = {"type": "system", "subtype": "init", "apiKeySource": "none", "model": "fake"}
PAUSE = "## Paused at a provider usage limit"
TOKEN = "fake-subscription-token"


def result(subtype="error_during_execution", is_error=True, **extra):
    return {"type": "result", "subtype": subtype, "is_error": is_error, "num_turns": 2,
            "duration_ms": 1000, "result": "limit reached", **extra}


def rejected(resets=RESET, **info):
    return {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": resets, **info}}


LIMITED = (INIT, rejected(), result(error="rate_limit"))
OK = (INIT, {"type": "result", "subtype": "success", "is_error": False, "num_turns": 1,
             "duration_ms": 10, "result": "done"})


@pytest.fixture
def d(tmp_path):
    dispatcher = Dispatcher(tmp_path)
    dispatcher.add_story(STORY, "Ready to implement")
    dispatcher.add_pr(PR, STORY)
    dispatcher.claude_stream(*LIMITED, exit_code=1)
    return dispatcher


def limit_stop(events_file, tmp_path):
    proc = subprocess.run(
        [BASH, "-c", f'source "{(REPO_ROOT / "scripts/lib/claude-session.sh").as_posix()}"; '
                     f'claude_limit_stop "{events_file.as_posix()}"'],
        capture_output=True, text=True)
    return proc.returncode, proc.stdout.strip()


# ── Detection (AC1–AC4) ─────────────────────────────────────────────────────


@pytest.mark.parametrize("events,reset", [
    ([result(error="rate_limit")], ""),
    ([result(subtype="rate_limit")], ""),
    ([result(error_type="rate_limit_error")], ""),
    ([result(error_category="rate_limit")], ""),
    ([result(api_error_status=429)], ""),
    ([result("success", True, error="rate_limit")], ""),
    ([rejected()], str(RESET)),  # no result line at all
    ([rejected(), result("error_during_execution", True)], str(RESET)),
    ([{"type": "rate_limit_event", "rate_limit_info": {"status": "allowed_warning", "unifiedWindows": {
        "five_hour": {"utilization": 1, "resetsAt": 100}, "seven_day": {"utilization": 0.2, "resetsAt": 900}}}},
      result(error="rate_limit")], "100"),
    ([rejected(resets=None), result(error="rate_limit")], ""),
])
def test_limit_stop_is_detected_with_its_reset_time(tmp_path, events, reset):
    path = tmp_path / "events.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in events))

    assert limit_stop(path, tmp_path) == (0, reset)


@pytest.mark.parametrize("events", [
    [result("error_during_execution", True)],
    [result("success", False, error="rate_limit")],  # a success is never a stop
    [rejected(), {"type": "result", "subtype": "success", "is_error": False}],
    [result(api_error_status=401)],
    [],
])
def test_other_outcomes_are_not_limit_stops(tmp_path, events):
    path = tmp_path / "events.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in events))

    assert limit_stop(path, tmp_path)[0] == 1


def test_session_returns_a_distinct_code_and_the_reset_time(tmp_path):
    (tmp_path / "bin").mkdir()
    claude = tmp_path / "bin" / "claude"
    claude.write_text("#!/usr/bin/env bash\ncat > /dev/null\n" + "".join(
        f"echo '{json.dumps(e)}'\n" for e in LIMITED) + "exit 1\n")
    claude.chmod(0o755)
    script = f"""
set -u
source "{(REPO_ROOT / 'scripts/lib/claude-session.sh').as_posix()}"
eval "$(sed -n '/^CLAUDE_LIMIT_STOP_RC=/,/^_claude_code_session() {{/p' "{(REPO_ROOT / 'scripts/dispatcher-invoke.sh').as_posix()}" | sed '$d')"
eval "$(sed -n '/^_claude_code_session() {{/,/^}}$/p' "{(REPO_ROOT / 'scripts/dispatcher-invoke.sh').as_posix()}")"
CLAUDE_BIN="{claude.as_posix()}"; REPO_ROOT="{tmp_path.as_posix()}"
DISPATCH_JOB_START_TS=$(date +%s); AGENT_MAX_WALLCLOCK_SECONDS=100; AGENT_MAX_TURNS=2
rc=0; run_claude_code_session sonnet tok "prompt" implement 5 || rc=$?
echo "rc=$rc reset=$CLAUDE_LIMIT_RESET_TS"
"""
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True)
    assert f"rc=75 reset={RESET}" in proc.stdout, proc.stdout + proc.stderr


# ── New /implement (AC5, AC6, AC11–AC15) ────────────────────────────────────


def test_new_implement_writes_no_status_and_leaves_a_pause_record(d):
    proc = d.run_issue(STORY)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {STORY}"]
    assert d.board_writes == []
    comments = d.posted(STORY)
    assert len(comments) == 1 and comments[0].startswith(PAUSE)
    assert "Blocked Declaration" not in comments[0]
    body = comments[0]
    assert f"`/implement {STORY}` for #{STORY}" in body
    assert "Branch: branch" in body
    assert f"PR: #{PR}, head `{HEAD}`" in body
    assert f"Resets: {RESET_TEXT}" in body
    assert "No PO action is needed." in body
    assert d.slack_calls == [] and d.queued_events == []
    log = proc.stdout + proc.stderr
    assert f"[SESSION RATE LIMIT] /implement #{STORY} stopped at a provider usage limit; resets {RESET_TEXT}" in log
    assert "#316" not in log and TOKEN not in log + body
    assert "[HANDOFF]" not in proc.stdout
    assert f"Next: /implement starts again for #{STORY} on a later poll" in body
    assert "Ready to implement" not in body and "Ready for implementation" not in body


def test_reset_time_unknown_when_the_stream_has_none(d):
    d.claude_stream(INIT, result(error="rate_limit"), exit_code=1)

    proc = d.run_issue(STORY)

    assert proc.returncode == 0
    assert "Resets: unknown — the dispatcher checks the allowance on every poll" in d.posted(STORY)[0]


def test_no_pr_yet_is_named(tmp_path):
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "Ready to implement")
    d.claude_stream(*LIMITED, exit_code=1)

    d.run_issue(STORY)

    assert "PR: no PR yet" in d.posted(STORY)[0]


def test_story_starts_again_with_the_normal_prompt_after_the_stop(d):
    d.run_issue(STORY)
    d.claude_stream(*OK)

    proc = d.run_issue(STORY)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {STORY}"] * 2
    first, second = d.claude_calls
    assert first["stdin"] == second["stdin"]
    assert first["stdin"].startswith(f"Implement issue #{STORY}")


def test_second_stop_at_the_same_step_and_head_posts_no_new_record(d):
    d.run_issue(STORY)
    d.run_issue(STORY)

    assert len(d.posted(STORY)) == 1
    assert d.sessions == [f"implement {STORY}"] * 2


def test_a_new_head_gets_a_new_record(d):
    d.run_issue(STORY)
    d.add_pr(PR, STORY, head="c" * 40)
    d.run_issue(STORY)

    assert len(d.posted(STORY)) == 2


def test_a_real_failure_keeps_the_blocker(d):
    d.claude_stream(INIT, result(), exit_code=1)

    proc = d.run_issue(STORY)

    assert proc.returncode != 0
    assert "Blocked Declaration" in d.posted(STORY)[0]


# ── The pause keeps the story branch (issue #439, F308.7 part 1) ────────────
# Evidence for F308.7 part 1: test_pause_makes_no_destructive_call_on_any_path,
# test_restart_after_the_stop_makes_no_destructive_call,
# test_pause_record_counts_the_unpushed_commits_exactly,
# test_guard_fails_on_each_destructive_call, test_pause_runs_reach_the_git_fake.


def pr_identity(dispatcher):
    return [(p["number"], p["head"]["ref"], p["head"]["sha"], p["state"]) for p in dispatcher.pull_requests]


def pause_run(tmp_path, path):
    d = Dispatcher(tmp_path)
    d.fake_git(unpushed=2)
    if path == "new":
        d.add_story(STORY, "Ready to implement")
        d.add_pr(PR, STORY)
        d.claude_stream(*LIMITED, exit_code=1)
        return d, lambda: d.run_issue(STORY)
    if path == "fix-round":
        d.add_story(STORY, "In implementation")
        d.add_pr(PR, STORY)
        d.add_findings(PR, HEAD)
    else:
        d.add_story(STORY, "In review", executor="codex")
        d.add_pr(PR, STORY)
    d.claude_stream(*LIMITED, exit_code=1)
    return d, d.resume


@pytest.mark.parametrize("path", ["new", "fix-round", "review"])
def test_pause_makes_no_destructive_call_on_any_path(tmp_path, path):
    d, run = pause_run(tmp_path, path)
    before = pr_identity(d)

    proc = run()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.posted(STORY)[0].startswith(PAUSE)
    assert d.git_calls, "the fake git was not reached"
    assert d.destructive_calls == []
    assert pr_identity(d) == before


def test_restart_after_the_stop_makes_no_destructive_call(tmp_path):
    d, run = pause_run(tmp_path, "new")
    before = pr_identity(d)
    run()
    assert d.git_calls, "the fake git was not reached"
    d.claude_stream(*OK)

    proc = d.run_issue(STORY)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {STORY}"] * 2
    first, second = d.claude_calls
    assert first["stdin"] == second["stdin"]
    assert d.destructive_calls == []
    assert pr_identity(d) == before


@pytest.mark.parametrize("unpushed,dirty,expected", [
    (2, False, "- Not pushed when the session stopped: 2 commit(s), no uncommitted changes"),
    (0, True, "- Not pushed when the session stopped: 0 commit(s), uncommitted changes"),
])
def test_pause_record_counts_the_unpushed_commits_exactly(tmp_path, unpushed, dirty, expected):
    d = Dispatcher(tmp_path)
    d.fake_git(unpushed=unpushed, dirty=dirty)
    d.add_story(STORY, "Ready to implement")
    d.add_pr(PR, STORY)
    d.claude_stream(*LIMITED, exit_code=1)

    d.run_issue(STORY)

    assert expected + "\n" in d.posted(STORY)[0]


def test_pause_runs_reach_the_git_fake(tmp_path):
    d, run = pause_run(tmp_path, "new")

    run()

    assert any(" rev-list --count " in call for call in d.git_calls)
    assert any(call.endswith(" status --porcelain") for call in d.git_calls)


@pytest.mark.parametrize("git_log,gh_log", [
    (["git push --force"], []),
    (["git -C /r push --force origin b"], []),
    (["git push origin --delete branch"], []),
    (["git branch -D branch"], []),
    (["git reset --hard"], []),
    (["git checkout -B branch"], []),
    ([], ["gh api repos/o/r/git/refs/heads/branch -X DELETE"]),
    ([], ["gh api repos/o/r/pulls/900 -X PATCH -f state=closed"]),
    ([], ["gh api repos/o/r/pulls/900 -X PATCH -f head=other"]),
])
def test_guard_fails_on_each_destructive_call(git_log, gh_log):
    assert destructive_calls(git_log, gh_log) != []


def test_guard_passes_on_the_read_calls_and_other_gh_writes():
    git_log = ["git -C /r rev-parse --abbrev-ref HEAD", "git -C /r rev-list --count @{upstream}..HEAD",
               "git -C /r status --porcelain"]
    gh_log = ["gh api repos/o/r/issues/500/comments -f body=x", "gh api repos/o/r/pulls/900 -X PATCH -f title=t"]
    assert destructive_calls(git_log, gh_log) == []


# ── Restart bound (AC15) ────────────────────────────────────────────────────


def test_restart_counts_and_the_usage_limit_stops_a_loop(d):
    env = {"USAGE_LIMIT_SESSIONS_PER_DAY": "1"}
    first = d.run_issue(STORY, env=env)
    second = d.run_issue(STORY, env=env)

    assert first.returncode == 0 and second.returncode == 0
    assert d.sessions == [f"implement {STORY}"]  # the restart was refused
    assert d.usage_count == 1
    assert "[USAGE LIMIT SKIP]" in second.stdout + second.stderr


def test_restart_goes_through_the_allowance_check(d):
    d.run_issue(STORY)
    second = d.run_issue(STORY, seq="1")

    assert second.returncode == 0
    assert d.sessions == [f"implement {STORY}"]
    assert d.allowance_calls == 1  # counted per run: the refused restart asked


# ── Fix round (AC7–AC9) ─────────────────────────────────────────────────────


def test_fix_round_stays_in_implementation_and_is_tried_again(tmp_path):
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "In implementation")
    d.add_pr(PR, STORY)
    d.add_findings(PR, HEAD)
    d.claude_stream(*LIMITED, exit_code=1)

    first = d.resume()

    assert first.returncode == 0, first.stdout + first.stderr
    assert d.sessions == [f"implement {STORY}"]
    assert d.board_writes == [] and d.status(STORY) == "In implementation"
    comments = d.posted(STORY)
    assert len(comments) == 1 and comments[0].startswith(PAUSE)
    assert "Blocked Declaration" not in comments[0] and "fix-round-attempt" not in comments[0]
    assert "[HANDOFF]" not in first.stdout

    second = d.resume()

    assert "already tried" not in second.stdout
    assert d.sessions == [f"implement {STORY}"] * 2
    assert len(d.posted(STORY)) == 1  # same step and head: not posted again


NEW_HEAD = "b" * 40


def push_new_head_once(d, status=None):
    """The first /implement session (the fix round) pushes a commit, so the PR head changes.

    With ``status`` it also writes that board status, as step 16 does before the stop.
    """
    write_status = ""
    if status:
        write_status = ("jq '. + {\"ITEM_%d\": \"%s\"}' \"$MOCK_DIR/state.json\" > \"$MOCK_DIR/s.tmp\""
                        " && mv \"$MOCK_DIR/s.tmp\" \"$MOCK_DIR/state.json\"" % (STORY, status))
    d.on_session("implement", f"""
if [[ ! -f "$MOCK_DIR/pushed" ]]; then
    touch "$MOCK_DIR/pushed"
    {write_status}
    jq 'map(if .number == {PR} then .head.sha = "{NEW_HEAD}" else . end)' "$MOCK_DIR/pulls.json" > "$MOCK_DIR/p.tmp" && mv "$MOCK_DIR/p.tmp" "$MOCK_DIR/pulls.json"
fi
""")


def pushed_fix_round(tmp_path, status=None):
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "In implementation")
    d.add_pr(PR, STORY)
    d.add_findings(PR, HEAD)
    d.claude_stream(*LIMITED, exit_code=1)
    push_new_head_once(d, status)
    return d


def test_fix_round_that_pushed_a_head_is_handed_off_at_the_pause(tmp_path):
    d = pushed_fix_round(tmp_path)

    first = d.resume()

    assert first.returncode == 0, first.stdout + first.stderr
    assert d.sessions == [f"implement {STORY}"]
    owner = f"<!-- hpw:dispatcher action=merge-owner pr={PR} head={NEW_HEAD} -->"
    assert [b for b in d.posted(PR) if owner in b]
    assert d.board_writes == []
    assert f"[HANDOFF] #{STORY}" in first.stdout
    pauses = [c for c in d.posted(STORY) if c.startswith(PAUSE)]
    assert len(pauses) == 1 and "handed off to the Codex review loop" in pauses[0]
    assert "Blocked Declaration" not in "".join(d.posted(STORY))

    second = d.resume()

    assert second.returncode == 0, second.stdout + second.stderr
    assert d.sessions == [f"implement {STORY}"]
    assert "merge-owner-missing" not in "".join(d.posted(STORY) + d.posted(PR))
    assert "no merge-owner evidence" not in second.stdout


def test_fix_round_that_moved_to_in_review_before_the_stop_is_handed_off_and_named_truthfully(tmp_path):
    d = pushed_fix_round(tmp_path, "In review")

    first = d.resume()

    assert first.returncode == 0, first.stdout + first.stderr
    assert d.sessions == [f"implement {STORY}"]
    owner = f"<!-- hpw:dispatcher action=merge-owner pr={PR} head={NEW_HEAD} -->"
    assert len([b for b in d.posted(PR) if owner in b]) == 1
    assert d.board_writes == []
    assert not [b for b in d.posted(PR) if "hpw:codex-review" in b.lower() and "@codex" in b]
    pauses = [c for c in d.posted(STORY) if c.startswith(PAUSE)]
    assert len(pauses) == 1
    assert 'the story is "In review"' in pauses[0] and "handed off to the Codex review loop" in pauses[0]
    assert "In implementation" not in pauses[0]

    second = d.resume()

    assert second.returncode == 0, second.stdout + second.stderr
    assert d.sessions == [f"implement {STORY}"]
    assert "merge-owner-missing" not in "".join(d.posted(STORY) + d.posted(PR))
    assert "Blocked Declaration" not in "".join(d.posted(STORY))
    assert len([c for c in d.posted(STORY) if c.startswith(PAUSE)]) == 1


def test_fix_round_in_review_pause_exits_1_before_the_hand_off_when_the_owner_cannot_be_recorded(tmp_path):
    d = pushed_fix_round(tmp_path, "In review")
    d.fail_comment_writes(PR, 20)

    proc = d.resume()

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "[HANDOFF]" not in proc.stdout


def test_fix_round_that_pushed_to_a_draft_pr_posts_no_blocker_and_no_hand_off(tmp_path):
    d = pushed_fix_round(tmp_path)
    d.add_pr(PR, STORY, draft=True)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not [b for b in d.posted(PR) if "action=merge-owner" in b]
    assert "[HANDOFF]" not in proc.stdout and d.status(STORY) == "In implementation"
    posted = d.posted(STORY)
    assert len(posted) == 1 and posted[0].startswith(PAUSE) and "still a draft" in posted[0]
    assert "Blocked Declaration" not in "".join(posted)


def test_fix_round_without_a_push_posts_no_owner_and_no_hand_off(tmp_path):
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "In implementation")
    d.add_pr(PR, STORY)
    d.add_findings(PR, HEAD)
    d.claude_stream(*LIMITED, exit_code=1)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not [b for b in d.posted(PR) if "action=merge-owner" in b]
    assert "[HANDOFF]" not in proc.stdout and d.status(STORY) == "In implementation"
    pauses = d.posted(STORY)
    assert len(pauses) == 1 and "is tried again on a later poll" in pauses[0]


def test_fix_round_pause_exits_1_before_the_hand_off_when_the_owner_cannot_be_recorded(tmp_path):
    d = pushed_fix_round(tmp_path)
    d.fail_comment_writes(PR, 20)

    proc = d.resume()

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "[HANDOFF]" not in proc.stdout and d.status(STORY) == "In implementation"


def test_fix_round_waiting_at_the_allowance_is_not_rolled_back_as_stale(tmp_path):
    d = Dispatcher(tmp_path)
    d.add_story(700, "Ready to implement")
    d.add_pr(970, 700)
    d.add_story(STORY, "In implementation")
    d.add_pr(PR, STORY)  # updated_at is long past STALE_THRESHOLD
    d.add_findings(PR, HEAD)
    d.claude_stream(*LIMITED, exit_code=1)
    d.resume()

    proc = d.run_issue(700, seq="1", env={"STALE_THRESHOLD": "60"})

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.status(STORY) == "In implementation"
    assert all("Recovery Comment" not in c for c in d.posted(STORY))


# ── /review (AC10) ──────────────────────────────────────────────────────────


def test_review_stays_in_review_and_is_tried_again_on_the_same_head(tmp_path):
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "In review", executor="codex")
    d.add_pr(PR, STORY)
    d.claude_stream(*LIMITED, exit_code=1)

    first = d.resume()

    assert first.returncode == 0, first.stdout + first.stderr
    assert d.sessions == [f"review {PR}"]
    assert d.board_writes == []
    comments = d.posted(STORY)
    assert len(comments) == 1 and comments[0].startswith(PAUSE)
    assert f"`/review {PR}` for #{STORY}" in comments[0]
    assert "review-attempt" not in comments[0] and "Blocked Declaration" not in comments[0]

    second = d.resume()

    assert "already tried" not in second.stdout
    assert d.sessions == [f"review {PR}"] * 2
    assert len(d.posted(STORY)) == 1


# ── Texts and docs (AC16, AC17, AC20) ───────────────────────────────────────


def test_allowance_and_usage_limit_comments_say_the_dispatcher_tries_again():
    text = (REPO_ROOT / "scripts" / "dispatcher-invoke.sh").read_text(encoding="utf-8")
    assert "Nothing restarts it automatically" not in text and "by hand after the reset" not in text
    assert text.count("tries /") >= 2 and "again on a later poll" in text


def test_implement_command_continues_on_a_remote_branch_without_a_pr():
    text = (REPO_ROOT / ".claude" / "commands" / "implement.md").read_text(encoding="utf-8")
    step9 = text[text.index("9. If no PR exists yet:"):text.index("10. If a PR already exists:")]
    assert "already exists on the remote without a PR" in step9 and "continue on that branch" in step9


def test_docs_describe_the_pause():
    text = (REPO_ROOT / "docs" / "DISPATCHER-CONFIG.md").read_text(encoding="utf-8")
    assert "## Pause at a provider usage limit" in text
    assert "resume is #316" not in text and "run it by hand after the reset" not in text
