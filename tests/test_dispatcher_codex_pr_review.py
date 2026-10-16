"""executor:codex is skipped at /implement; a Codex-implemented PR is reviewed on the subscription (issue #315).

Drives the real ``scripts/dispatcher-invoke.sh`` with the fakes in
``tests/dispatcher_fakes.py``. Poll and workflow cases are in
``tests/test_dispatcher_review_loop.py``; the static F308.2 check is in
``tests/test_dispatcher_metered_path_removed.py``.
"""

from __future__ import annotations

import re

import pytest

from tests.dispatcher_fakes import HAVE_TOOLS, REPO_ROOT, Dispatcher

pytestmark = pytest.mark.skipif(not HAVE_TOOLS, reason="requires bash, jq and timeout")

STORY = 500
PR = 900
WARNING = (f"Warning: #{STORY} has executor:codex, which the dispatcher does not start for /implement "
           "(no subscription mechanism, ADR-012); skipping it for this run and continuing selection.")
INVOKE_TEXT = (REPO_ROOT / "scripts" / "dispatcher-invoke.sh").read_text(encoding="utf-8")


@pytest.fixture
def d(tmp_path):
    return Dispatcher(tmp_path)


def codex_in_review(d, story=STORY, pr=PR, draft=False):
    d.add_story(story, "In review", executor="codex")
    d.add_pr(pr, story, draft=draft)


# ── Skip at implementation (AC6–AC9) ────────────────────────────────────────


def test_ac6_ac7_target_codex_story_is_skipped_with_nothing_changed(d):
    d.add_story(STORY, "Ready for implementation", executor="codex")
    proc = d.run_issue(STORY, seq="1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert WARNING in proc.stderr
    assert "Unrecognised executor label" not in proc.stderr + proc.stdout
    assert d.sessions == [] and d.board_writes == [] and d.posted(STORY) == []
    assert d.slack_calls == [] and d.allowance_calls == 0 and d.usage_count == 0
    assert d.status(STORY) == "Ready for implementation"


def test_ac6_ac8_next_ready_story_continues_past_a_codex_story(d):
    d.add_story(STORY, "Ready for implementation", executor="codex")
    d.add_story(501, "Ready for implementation", executor="claude-code-sonnet")
    d.add_pr(PR, 501)
    proc = d.run_issue(STORY)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stderr.count(WARNING) == 1  # not returned again across the recursive exec
    assert d.sessions == ["implement 501"]
    assert d.board_writes[0] == "501=In implementation"
    assert d.posted(STORY) == [] and d.status(STORY) == "Ready for implementation"


def test_ac6_codex_story_picked_as_next_ready_story_is_skipped_too(d):
    d.add_story(STORY, "Ready for implementation", executor="claude-code-sonnet")
    d.add_story(501, "Ready for implementation", executor="codex")
    d.add_pr(PR, STORY)
    proc = d.run_issue(STORY)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {STORY}"]
    assert d.board_writes[0] == f"{STORY}=In implementation"
    assert not any(write.startswith("501=") for write in d.board_writes)
    assert d.status(501) == "Ready for implementation" and d.posted(501) == []


def test_ac9_the_skip_is_a_table_lookup_not_a_branch_on_the_label():
    code = "\n".join(line for line in INVOKE_TEXT.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r'==\s*\*?"?codex"?\*?|=~\s*codex|\[\[\s*"?\$\w+"?\s*==\s*\*"codex"', code)
    assert "== *\"codex\"" not in code and '== "codex"' not in code


# ── Review entry (AC10–AC17) ────────────────────────────────────────────────


def test_ac10_codex_story_in_review_starts_review_on_opus(d):
    codex_in_review(d)
    proc = d.resume()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"[RESUME] #{STORY}: /review on PR #{PR} under claude-code-opus" in proc.stdout
    assert d.sessions == [f"review {PR}"]
    call = d.claude_calls[0]
    assert "opus" in call["argv"] and call["stdin"].startswith(f"Review PR #{PR}")
    output = proc.stdout + proc.stderr
    assert f"[SESSION] /review #{PR} on opus:" in output and "apiKeySource none" in output


@pytest.mark.parametrize("case", ["claude-tier", "no-label", "two-labels", "no-pr", "draft"])
def test_ac11_cases_that_do_nothing(d, case):
    labels = {"no-label": ["type:story"], "two-labels": ["type:story", "executor:codex", "executor:claude-code-opus"]}
    d.add_story(STORY, "In review", executor="claude-code-sonnet" if case == "claude-tier" else "codex",
                labels=labels.get(case))
    if case != "no-pr":
        d.add_pr(PR, STORY, draft=(case == "draft"))
    proc = d.resume()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [] and d.board_writes == [] and d.posted(STORY) == []
    output = proc.stdout + proc.stderr
    skips = [line for line in output.splitlines() if "[RESUME SKIP]" in line]
    assert len(skips) == (0 if case == "claude-tier" else 1), output
    if skips:
        assert skips[0].startswith(f"[RESUME SKIP] #{STORY}: ")


def test_ac12_review_leaving_in_merge_runs_merge_and_cleanup(d):
    codex_in_review(d)
    d.session_sets_status("review", STORY, "In merge")
    proc = d.resume()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.phases == [f"merge {PR}", f"cleanup {STORY}"]
    assert d.status(STORY) == "Done"


def test_ac12_review_leaving_in_implementation_runs_no_merge(d):
    codex_in_review(d)
    d.session_sets_status("review", STORY, "In implementation")
    proc = d.resume()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.phases == []
    assert f'[RESUME] #{STORY}: /review left the story in "In implementation"; no merge' in proc.stdout


def test_ac13_one_review_attempt_per_head(d):
    codex_in_review(d)
    d.fail("review")
    first = d.resume()
    assert first.returncode == 1
    blockers = d.posted(STORY)
    assert len(blockers) == 1 and f"action=review-attempt pr={PR} head={'a' * 40}" in blockers[0]
    d.fail("review", failing=False)
    second = d.resume()
    assert second.returncode == 0, second.stdout + second.stderr
    assert (f"[RESUME SKIP] #{STORY}: /review was already tried on PR #{PR} head {'a' * 7}; "
            "waiting for a new head or /unblock") in second.stdout + second.stderr
    assert d.sessions == [f"review {PR}"]  # only the failed first attempt
    # a new head allows one new attempt
    d.add_pr(PR, STORY, head="b" * 40)
    third = d.resume()
    assert third.returncode == 0, third.stdout + third.stderr
    assert d.sessions == [f"review {PR}", f"review {PR}"]


def test_ac13_completed_review_that_leaves_in_review_is_not_repeated_on_the_same_head(d):
    codex_in_review(d)  # the fake /review session changes no status
    first = d.resume()
    assert first.returncode == 0, first.stdout + first.stderr
    assert f'/review left the story in "In review"; no merge' in first.stdout
    recorded = d.posted(STORY)
    assert len(recorded) == 1 and f"action=review-attempt pr={PR} head={'a' * 40}" in recorded[0]
    second = d.resume()
    assert second.returncode == 0, second.stdout + second.stderr
    assert f"[RESUME SKIP] #{STORY}: /review was already tried on PR #{PR} head {'a' * 7}" in second.stdout + second.stderr
    assert d.sessions == [f"review {PR}"]  # one session for the head
    d.add_pr(PR, STORY, head="b" * 40)  # a new head allows one new attempt
    assert d.resume().returncode == 0
    assert d.sessions == [f"review {PR}", f"review {PR}"]


def test_ac14_usage_limit_stops_the_entry(d):
    codex_in_review(d)
    day = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%d")
    (d.mock / "usage" / "sessions").write_text(f"{day}:3\n")
    proc = d.resume(env={"USAGE_LIMIT_SESSIONS_PER_DAY": "3"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == []
    assert "[USAGE LIMIT SKIP]" in proc.stdout


def test_ac14_reserve_stops_the_entry(d):
    codex_in_review(d)
    proc = d.resume(seq="1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == []
    assert "[RESERVE SKIP]" in proc.stdout


def test_ac17_dry_run_starts_nothing(d):
    codex_in_review(d)
    d.session_sets_status("review", STORY, "In merge")
    proc = d.resume("--dry-run")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"[DRY RUN] Would start Claude Code (opus) on the subscription for /review {PR}" in proc.stdout
    assert d.sessions == [] and d.phases == [] and d.board_writes == [] and d.posted(STORY) == []
    assert d.status(STORY) == "In review"


# ── One provider (AC2) ──────────────────────────────────────────────────────


def test_ac2_executor_without_a_provider_row_starts_nothing(d, tmp_path, monkeypatch):
    """A copy whose review table names ``codex`` as the reviewer: it has no EXECUTOR_PROVIDER row."""
    import shutil

    repo = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "scripts", repo / "scripts")
    shutil.copytree(REPO_ROOT / ".claude" / "commands", repo / ".claude" / "commands")
    lib = repo / "scripts" / "lib" / "review-routing.sh"
    text = lib.read_text(encoding="utf-8")
    assert '["codex"]="claude-code-opus"' in text
    lib.write_text(text.replace('["codex"]="claude-code-opus"', '["codex"]="codex"'), encoding="utf-8", newline="\n")
    monkeypatch.setattr("tests.dispatcher_fakes.INVOKE", repo / "scripts" / "dispatcher-invoke.sh")
    codex_in_review(d)

    proc = d.resume()

    assert proc.returncode != 0
    assert "Error: No provider/model entry for executor 'codex' in EXECUTOR_PROVIDER/EXECUTOR_MODEL." in proc.stderr
    assert d.sessions == [] and d.claude_calls == []
