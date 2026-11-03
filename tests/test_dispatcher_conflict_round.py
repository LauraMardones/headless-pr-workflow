"""The dispatcher merges main into its own conflicting PR: the conflict round (issue #409).

Drives the real ``scripts/dispatcher-invoke.sh`` and ``scripts/dispatcher-poll.sh``
with the fakes in ``tests/dispatcher_fakes.py`` (no network). A PR's
mergeability is the fake PR's ``mergeable`` / ``mergeable_state``.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from tests.dispatcher_fakes import BASH, PO, REPO_ROOT, Dispatcher, needs_tools
from tests.phase_script_fakes import PhaseHarness
from tests.script_tools import script_path, windows_env

pytestmark = needs_tools

STORY = 500
PR = 900
HEAD = "a" * 40
NEW_HEAD = "b" * 40
OTHER_HEAD = "c" * 40
CONFLICTING = {"mergeable": False, "mergeable_state": "dirty"}
UNKNOWN = {"mergeable": None, "mergeable_state": "unknown"}
BLOCKED = "## Blocked Declaration"
INIT = {"type": "system", "subtype": "init", "apiKeySource": "none", "model": "fake"}
LIMITED = (INIT, {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": 1760000000}},
           {"type": "result", "subtype": "error_during_execution", "is_error": True, "num_turns": 2,
            "duration_ms": 1000, "result": "limit reached", "error": "rate_limit"})
ROUND_LOG = f"[RESUME] #{STORY}: PR #{PR} conflicts with main; conflict round, /implement on PR #{PR}"


@pytest.fixture
def d(tmp_path):
    return Dispatcher(tmp_path)


def conflicting_story(d: Dispatcher, status: str, story: int = STORY, pr: int = PR,
                      executor: str = "claude-code-opus", **pr_fields) -> None:
    d.add_story(story, status, executor=executor)
    d.add_pr(pr, story, head=HEAD, **{**CONFLICTING, **pr_fields})


def session_merges_main(d: Dispatcher, pr: int = PR) -> None:
    """The /implement session merges main, resolves the conflict and pushes a new head."""
    d.on_session("implement", f"""
jq 'map(if .number == {pr} then .head.sha = "{NEW_HEAD}" | .mergeable = true | .mergeable_state = "clean" else . end)' \\
    "$MOCK_DIR/pulls.json" > "$MOCK_DIR/p.tmp" && mv "$MOCK_DIR/p.tmp" "$MOCK_DIR/pulls.json"
""")


def blockers(d: Dispatcher, story: int = STORY) -> list[str]:
    return [body for body in d.posted(story) if body.startswith(BLOCKED)]


def po_events(d: Dispatcher) -> list[dict]:
    return [e for e in d.queued_events if e["event"] == "po_blocker"]


def attempt_marker(head: str = HEAD) -> str:
    return f"<!-- hpw:dispatcher action=conflict-round-attempt pr={PR} head={head} -->"


def owner_marker(head: str) -> str:
    return f"<!-- hpw:dispatcher action=merge-owner pr={PR} head={head} -->"


# ── AC 1, AC 3: conflict in "In merge" ──────────────────────────────────────


def test_ac1_ac3_in_merge_conflict_starts_a_conflict_round_and_hands_the_new_head_off(d):
    conflicting_story(d, "In merge")
    # The story already holds its place: a full WIP limit does not stop the round.
    for other in (601, 602):
        d.add_story(other, "In implementation")
    session_merges_main(d)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert ROUND_LOG in proc.stdout
    assert d.phases == []  # no /merge for the conflicting head
    assert d.sessions == [f"implement {STORY}"]
    assert d.board_writes == [f"{STORY}=In implementation", f"{STORY}=In review"]
    assert blockers(d) == [] and d.queued_events == [] and d.slack_calls == []
    assert not any("action=merge-attempt" in body for body in d.posted(STORY))
    # AC 3: the new head carries the merge-owner marker; the old head does not
    assert owner_marker(NEW_HEAD) in d.posted(PR) and owner_marker(HEAD) not in d.posted(PR)
    assert f"[HANDOFF] #{STORY}: handed to the Codex review loop" in proc.stdout


def test_ac3_the_new_head_reaches_merge_only_through_the_bookkeeping_job(d):
    conflicting_story(d, "In merge")
    session_merges_main(d)
    d.resume()
    assert d.status(STORY) == "In review"

    # Nothing merges while the story waits for the Codex review in "In review".
    d.on_session("implement", "")
    proc = d.resume()
    assert proc.returncode == 0, proc.stderr
    assert d.phases == [] and d.sessions == [f"implement {STORY}"]

    # The bookkeeping job moves it to "In merge" after a clean review; the
    # next resume pass merges the new head with the script-only /merge.
    d.set_status(STORY, "In merge")
    proc = d.resume()
    assert proc.returncode == 0, proc.stderr
    assert d.phases == [f"merge {PR}", f"cleanup {STORY}"]
    assert d.sessions == [f"implement {STORY}"]


# ── AC 2: conflict in "In review" ───────────────────────────────────────────


def test_ac2_in_review_conflict_starts_a_conflict_round(d):
    conflicting_story(d, "In review")
    session_merges_main(d)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert ROUND_LOG in proc.stdout
    assert d.sessions == [f"implement {STORY}"]
    assert d.board_writes == [f"{STORY}=In implementation", f"{STORY}=In review"]
    assert owner_marker(NEW_HEAD) in d.posted(PR)
    assert blockers(d) == [] and d.queued_events == []


@pytest.mark.parametrize("pr_fields", [
    {"mergeable": True, "mergeable_state": "clean"},
    UNKNOWN,
    {"draft": True},
], ids=["clean", "unknown", "draft"])
def test_ac2_in_review_without_a_conflict_starts_nothing(d, pr_fields):
    d.add_story(STORY, "In review", executor="claude-code-opus")
    d.add_pr(PR, STORY, **({**CONFLICTING, **pr_fields} if "draft" in pr_fields else pr_fields))

    proc = d.resume()

    assert proc.returncode == 0, proc.stderr
    assert d.sessions == [] and d.board_writes == [] and d.posted(STORY) == []


def test_ac2_in_review_with_an_unreadable_pr_starts_nothing(d):
    conflicting_story(d, "In review")
    d.fail_pr_read()

    proc = d.resume()

    assert proc.returncode == 0, proc.stderr
    assert d.sessions == [] and d.board_writes == [] and d.posted(STORY) == []


# ── AC 2: the poll lists conflict_due ───────────────────────────────────────


def poll(tmp_path, items: list[tuple], pulls: list[dict], details: dict[int, dict]) -> tuple[dict, str]:
    """Run the real dispatcher-poll.sh against a fake ``curl``.

    ``details`` maps a PR number to its single-PR read; a PR missing from it
    cannot be read (curl fails, as on an HTTP error).
    """
    nodes = [{"content": {"number": n, "title": f"Story {n}",
                          "labels": {"nodes": [{"name": f"executor:{executor}"}]}},
              "fieldValues": {"nodes": [{"name": status, "field": {"name": "Status"}}]}}
             for n, status, executor in items]
    board = {"data": {"repository": {"projectsV2": {"nodes": [{
        "number": 3, "title": "board",
        "items": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}}]}}}}
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    detail_dir = tmp_path / "details"
    detail_dir.mkdir()
    (tmp_path / "board.json").write_text(json.dumps(board), encoding="utf-8")
    (tmp_path / "pulls.json").write_text(json.dumps(pulls), encoding="utf-8")
    for number, detail in details.items():
        (detail_dir / f"pr_{number}.json").write_text(json.dumps(detail), encoding="utf-8")
    curl = bin_dir / "curl"
    curl.write_text("""#!/usr/bin/env bash
url="${@: -1}"
echo "$url" >> "$CURL_LOG"
case "$url" in
  */pulls/[0-9]*) f="$DETAIL_DIR/pr_${url##*/}.json"; [[ -f "$f" ]] || exit 22; cat "$f" ;;
  */pulls*) cat "$PULLS_FILE" ;;
  *) cat "$BOARD_FILE" ;;
esac
""", encoding="utf-8", newline="\n")
    curl.chmod(0o755)
    env = {"PATH": script_path(bin_dir.as_posix()), "GH_TOKEN": "fake", "HOME": tmp_path.as_posix(),
           "BOARD_FILE": (tmp_path / "board.json").as_posix(),
           "PULLS_FILE": (tmp_path / "pulls.json").as_posix(),
           "DETAIL_DIR": detail_dir.as_posix(), "CURL_LOG": (tmp_path / "curl.log").as_posix(),
           "DISPATCHER_STATE_DIR": (tmp_path / "state").as_posix(),
           "WIP_LIMIT_IMPLEMENTATION": "2", "READY_BUFFER_MIN": "3", "READY_BUFFER_MAX": "5", **windows_env()}
    proc = subprocess.run(
        [BASH, (REPO_ROOT / "scripts" / "dispatcher-poll.sh").as_posix(), "--repo", "o/r", "--dry-run"],
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout), proc.stderr


def open_pr(number: int, issue: int, draft: bool = False) -> dict:
    return {"number": number, "draft": draft, "body": f"Closes #{issue}"}


def test_ac2_poll_lists_only_review_loop_stories_in_review_with_a_conflicting_pr(tmp_path):
    out, stderr = poll(
        tmp_path,
        [(1, "In review", "claude-code-opus"),     # conflicting (mergeable false)
         (2, "In review", "claude-code-sonnet"),   # conflicting (dirty)
         (3, "In review", "claude-code-haiku"),    # clean
         (4, "In review", "claude-code-opus"),     # unknown mergeability
         (5, "In review", "claude-code-opus"),     # PR cannot be read
         (6, "In review", "claude-code-opus"),     # draft PR
         (7, "In review", "codex"),                # no review-loop row
         (8, "In merge", "claude-code-opus")],     # not "In review"
        pulls=[open_pr(11, 1), open_pr(12, 2), open_pr(13, 3), open_pr(14, 4), open_pr(15, 5),
               open_pr(16, 6, draft=True), open_pr(17, 7), open_pr(18, 8)],
        details={11: {"mergeable": False, "mergeable_state": "dirty"},
                 12: {"mergeable": None, "mergeable_state": "dirty"},
                 13: {"mergeable": True, "mergeable_state": "clean"},
                 14: {"mergeable": None, "mergeable_state": "unknown"},
                 16: {"mergeable": False, "mergeable_state": "dirty"},
                 17: {"mergeable": False, "mergeable_state": "dirty"},
                 18: {"mergeable": False, "mergeable_state": "dirty"}},
    )
    assert out["conflict_due"] == [{"number": 1, "title": "Story 1"}, {"number": 2, "title": "Story 2"}]
    assert "[POLL] Warning: could not read PR #15 for #5; conflict_due skips it this cycle" in stderr
    assert out["review_due"] == [{"number": 7, "title": "Story 7"}]  # unchanged for the codex story


def test_ac2_poll_conflict_due_is_empty_when_nothing_conflicts(tmp_path):
    out, _ = poll(tmp_path, [(1, "In review", "claude-code-opus")], pulls=[open_pr(11, 1)],
                  details={11: {"mergeable": True, "mergeable_state": "clean"}})
    assert out["conflict_due"] == []


# ── AC 4: unknown mergeability waits one run ────────────────────────────────


def test_ac4_unknown_mergeability_in_merge_waits_for_the_next_run(d):
    d.add_story(STORY, "In merge", executor="claude-code-opus")
    d.add_pr(PR, STORY, **UNKNOWN)

    proc = d.resume()

    assert proc.returncode == 0, proc.stderr
    assert (f"[RESUME SKIP] #{STORY}: GitHub has not computed mergeability for PR #{PR} yet; "
            "trying again on the next run") in proc.stdout
    assert d.phases == [] and d.sessions == [] and d.board_writes == []
    assert d.posted(STORY) == [] and d.posted(PR) == [] and d.queued_events == []

    # Once GitHub has computed it, a clean PR goes straight to /merge.
    d.add_pr(PR, STORY)
    proc = d.resume()
    assert proc.returncode == 0, proc.stderr
    assert d.phases == [f"merge {PR}", f"cleanup {STORY}"] and d.sessions == []


# ── AC 5: merges before conflict rounds ─────────────────────────────────────


def test_ac5_every_merge_of_the_run_comes_before_any_conflict_round(d):
    conflicting_story(d, "In merge", story=501, pr=901)   # first on the board
    d.add_story(502, "In merge", executor="claude-code-opus")
    d.add_pr(902, 502, head=OTHER_HEAD)                  # clean
    session_merges_main(d, pr=901)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.phases[0] == "merge 902" and "merge 901" not in d.phases
    assert d.sequence.index("merge 902") < d.sequence.index("session implement 501")


# ── AC 6: a conflict the round cannot resolve ───────────────────────────────


def assert_one_conflict_blocker(d: Dispatcher) -> None:
    [blocker] = blockers(d)
    assert f"merge conflict with main on PR #{PR}" in blocker
    assert "This blocker is the PO's only alert for this conflict." in blocker
    assert ("Unblocked when: the PR branch is merged with main and the conflict resolved, "
            "or the PO posts /unblock") in blocker
    assert attempt_marker() in blocker
    assert len(po_events(d)) == 1


@pytest.mark.parametrize("outcome", ["session fails", "no new head"])
def test_ac6_round_that_cannot_resolve_gives_one_blocker_and_no_second_round(d, outcome):
    conflicting_story(d, "In merge")
    if outcome == "session fails":
        d.fail("implement")

    proc = d.resume()

    assert proc.returncode == 1
    assert d.sessions == [f"implement {STORY}"] and d.phases == []
    assert_one_conflict_blocker(d)
    assert owner_marker(HEAD) not in d.posted(PR)

    # A later run on the same head starts no second round and posts nothing.
    d.fail("implement", False)
    proc = d.resume()
    assert proc.returncode == 0, proc.stderr
    assert (f"[RESUME SKIP] #{STORY}: the conflict round was already tried on PR #{PR} at head {HEAD[:7]}; "
            "waiting for a new head or a PO /unblock line") in proc.stdout
    assert d.sessions == [f"implement {STORY}"]
    assert_one_conflict_blocker(d)


def test_ac6_po_unblock_allows_one_new_attempt(d):
    conflicting_story(d, "In merge")
    d.fail("implement")
    d.resume()
    d.fail("implement", False)
    d.add_comment(STORY, "/unblock", login=PO)
    session_merges_main(d)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {STORY}", f"implement {STORY}"]
    assert owner_marker(NEW_HEAD) in d.posted(PR)
    assert d.status(STORY) == "In review"


def test_ac6_a_new_head_allows_one_new_attempt(d):
    conflicting_story(d, "In merge")
    d.fail("implement")
    d.resume()
    d.fail("implement", False)
    d.add_pr(PR, STORY, head=OTHER_HEAD, **CONFLICTING)  # someone pushed; the PR still conflicts
    session_merges_main(d)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {STORY}", f"implement {STORY}"]
    assert len(blockers(d)) == 1


def test_ac6_a_forged_attempt_marker_does_not_stop_the_round(d):
    conflicting_story(d, "In merge")
    d.add_comment(STORY, attempt_marker(), login="someone-else")
    session_merges_main(d)

    proc = d.resume()

    assert proc.returncode == 0, proc.stderr
    assert d.sessions == [f"implement {STORY}"]


# ── AC 7: a refused or paused round is not lost ─────────────────────────────


@pytest.mark.parametrize("status", ["In merge", "In review"])
@pytest.mark.parametrize("refusal", ["allowance", "usage limit"])
def test_ac7_refused_round_changes_nothing_and_runs_on_a_later_run(d, status, refusal):
    conflicting_story(d, status)
    session_merges_main(d)

    if refusal == "allowance":
        proc = d.resume(seq="1")
    else:
        proc = d.resume(env={"USAGE_LIMIT_SESSIONS_PER_DAY": "0"})

    assert proc.returncode == 0, proc.stderr
    assert d.sessions == [] and d.board_writes == [] and d.status(STORY) == status
    assert blockers(d) == [] and d.queued_events == []
    assert not any("conflict-round-attempt" in body for body in d.posted(STORY))
    output = proc.stdout + proc.stderr
    assert f'stays in "{status}"' in output and "the conflict round is tried again" in output

    proc = d.resume()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {STORY}"]
    assert d.status(STORY) == "In review"


def test_ac7_round_paused_at_a_usage_limit_starts_again_on_a_later_run(d):
    conflicting_story(d, "In merge")
    d.claude_stream(*LIMITED, exit_code=1)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {STORY}"]
    assert d.status(STORY) == "In implementation"
    [pause] = [body for body in d.posted(STORY) if body.startswith("## Paused at a provider usage limit")]
    assert "the conflict round stays in \"In implementation\"" in pause
    assert f"<!-- hpw:dispatcher action=conflict-round-paused pr={PR} head={HEAD} start={HEAD} -->" in pause
    assert blockers(d) == [] and d.queued_events == []
    assert not any("conflict-round-attempt" in body for body in d.posted(STORY))

    (d.mock / "claude_stream.jsonl").unlink()
    session_merges_main(d)
    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert ROUND_LOG in proc.stdout
    assert d.sessions == [f"implement {STORY}", f"implement {STORY}"]
    assert d.board_writes == [f"{STORY}=In implementation", f"{STORY}=In review"]
    assert owner_marker(NEW_HEAD) in d.posted(PR)


def test_ac7_round_that_pushed_before_the_pause_finishes_without_another_commit(d):
    """The pause record keeps the start head, so the resumed round is judged against it."""
    conflicting_story(d, "In merge")
    session_merges_main(d)  # the merge with main is pushed before the limit stops the session
    d.claude_stream(*LIMITED, exit_code=1)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    [pause] = [body for body in d.posted(STORY) if body.startswith("## Paused at a provider usage limit")]
    assert f"<!-- hpw:dispatcher action=conflict-round-paused pr={PR} head={NEW_HEAD} start={HEAD} -->" in pause

    # The resumed session only finishes CI and readiness: it pushes nothing.
    (d.mock / "claude_stream.jsonl").unlink()
    d.on_session("implement", "")
    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {STORY}", f"implement {STORY}"]
    assert blockers(d) == [] and d.queued_events == []
    assert not any("conflict-round-attempt" in body for body in d.posted(STORY))
    assert owner_marker(NEW_HEAD) in d.posted(PR)
    assert d.board_writes == [f"{STORY}=In implementation", f"{STORY}=In review"]

    # Paused again on the new head: the start head is still the original one.
    d.set_status(STORY, "In implementation")
    d.claude_stream(*LIMITED, exit_code=1)
    d.resume()
    pauses = [body for body in d.posted(STORY) if body.startswith("## Paused at a provider usage limit")]
    assert f"action=conflict-round-paused pr={PR} head={NEW_HEAD} start={HEAD} -->" in pauses[-1]


def test_ac7_failed_status_write_starts_no_session_and_writes_nothing(d):
    conflicting_story(d, "In merge")
    d.fail_status_write("In implementation")

    proc = d.resume()

    assert proc.returncode == 0, proc.stderr
    assert d.sessions == [] and d.status(STORY) == "In merge"
    assert d.posted(STORY) == [] and d.queued_events == []
    assert "the conflict round on PR #900 is tried again on the next run" in proc.stdout


def test_story_in_implementation_without_a_round_marker_gets_no_round(d):
    """Only a round the dispatcher started is resumed from "In implementation"."""
    conflicting_story(d, "In implementation")

    proc = d.resume()

    assert proc.returncode == 0, proc.stderr
    assert d.sessions == [] and d.posted(STORY) == []


# ── AC 8: stories without a conflict round ──────────────────────────────────


@pytest.mark.parametrize("labels", [["type:story", "executor:codex"],
                                    ["type:story", "executor:claude-code-opus", "executor:codex"]],
                         ids=["codex", "two executor labels"])
def test_ac8_story_without_a_review_loop_row_gets_one_clear_blocker(d, labels):
    d.add_story(STORY, "In merge", labels=labels)
    d.add_pr(PR, STORY, head=HEAD, **CONFLICTING)

    proc = d.resume()

    assert proc.returncode == 0, proc.stderr
    assert d.phases == [] and d.sessions == [] and d.board_writes == []
    [blocker] = blockers(d)
    assert f"merge conflict with main on PR #{PR}" in blocker
    assert "/merge failed" not in blocker  # not the generic merge-gate refusal
    assert f"<!-- hpw:dispatcher action=merge-conflict pr={PR} head={HEAD} -->" in blocker
    assert len(po_events(d)) == 1

    proc = d.resume()  # once per head
    assert proc.returncode == 0, proc.stderr
    assert len(blockers(d)) == 1 and len(po_events(d)) == 1 and d.phases == []


# ── AC 9: no gate is weakened ───────────────────────────────────────────────


def test_ac9_conflict_round_does_not_count_toward_the_fix_round_maximum(d):
    conflicting_story(d, "In merge")
    session_merges_main(d)
    d.resume()

    # Codex then finds a Blocker on the conflict-round head.
    d.set_status(STORY, "In implementation")
    d.add_findings(PR, NEW_HEAD)
    proc = d.resume("--dry-run")

    assert proc.returncode == 0, proc.stderr
    assert f"[RESUME] #{STORY}: fix round 1 of " in proc.stdout


def test_ac9_merge_gate_refuses_a_conflict_round_head_without_a_review(tmp_path):
    """scripts/dispatcher-merge.sh, unchanged: the owner marker alone never merges."""
    h = PhaseHarness(tmp_path)
    h.fixture(f"repos/o/r/pulls/{PR}/files", [{"filename": "scripts/dispatcher-invoke.sh"}])
    h.fixture(f"repos/o/r/pulls/{PR}", {"state": "open", "draft": False, "merged_at": None,
                                         "head": {"sha": NEW_HEAD, "ref": "claude/issue-500-x"}})
    h.fixture("user", {"login": "bot"})
    h.fixture(f"repos/o/r/issues/{PR}/comments", [{"user": {"login": "bot"}, "body": owner_marker(NEW_HEAD)}])
    h.set("gate_line", f"Merge gate: policy=pass, checks=pass, approval=missing, threads=none, "
                       f"mergeable=yes, head={NEW_HEAD[:7]}\n")
    h.set("gate_rc", "1")
    h.git("init", "-q", "-b", "main")
    h.git("commit", "-q", "--allow-empty", "-m", "init")

    proc = h.run("dispatcher-merge.sh", "--pr", str(PR), "--repo", "o/r")

    assert proc.returncode == 1
    assert "merge gate exit 1" in proc.stderr
    assert not [line for line in h.log("hpw.log") if line.startswith("hpw merge-pr")]
    assert h.posts == []
