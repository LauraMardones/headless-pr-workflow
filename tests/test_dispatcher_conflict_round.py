"""The dispatcher merges main into its own conflicting PR: the conflict round.

Issue #409 adds the round. Issue #449 replaces its resume logic with one rule:
a stopped round is never resumed, and every run decides from GitHub facts only
(``scripts/lib/conflict-round.sh``, used by the poll and the resume pass).

Drives the real ``scripts/dispatcher-invoke.sh`` and ``scripts/dispatcher-poll.sh``
with the fakes in ``tests/dispatcher_fakes.py`` (no network). A PR's
mergeability is the fake PR's ``mergeable`` / ``mergeable_state``.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from tests.dispatcher_fakes import BASH, BOT, PO, REPO_ROOT, Dispatcher, needs_tools
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
PAUSED = "## Paused at a provider usage limit"
INIT = {"type": "system", "subtype": "init", "apiKeySource": "none", "model": "fake"}
LIMITED = (INIT, {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": 1760000000}},
           {"type": "result", "subtype": "error_during_execution", "is_error": True, "num_turns": 2,
            "duration_ms": 1000, "result": "limit reached", "error": "rate_limit"})
ROUND_LOG = f"[RESUME] #{STORY}: PR #{PR} conflicts with main; conflict round, /implement on PR #{PR}"
OWNERLESS_LOG = f"[RESUME SKIP] #{STORY}: PR #{PR} head {NEW_HEAD[:7]} has no merge-owner evidence; blocker posted"
REVIEW_LOOP_STATUSES = ["In implementation", "In review", "In merge"]


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


def append_to_session(d: Dispatcher, line: str) -> None:
    with (d.mock / "on_implement.sh").open("a", encoding="utf-8", newline="\n") as script:
        script.write(line + "\n")


def session_merges_main_and_hands_off(d: Dispatcher, story: int = STORY) -> None:
    """The session pushes the merge with main and moves the story to "In review" (step 16)."""
    session_merges_main(d)
    append_to_session(d, f"jq '. + {{\"ITEM_{story}\": \"In review\"}}' \"$MOCK_DIR/state.json\" > \"$MOCK_DIR/s.tmp\""
                         f" && mv \"$MOCK_DIR/s.tmp\" \"$MOCK_DIR/state.json\"")


def blockers(d: Dispatcher, story: int = STORY) -> list[str]:
    return [body for body in d.posted(story) if body.startswith(BLOCKED)]


def pause_records(d: Dispatcher, story: int = STORY) -> list[str]:
    return [body for body in d.posted(story) if body.startswith(PAUSED)]


def po_events(d: Dispatcher) -> list[dict]:
    return [e for e in d.queued_events if e["event"] == "po_blocker"]


def attempt_marker(head: str = HEAD) -> str:
    return f"<!-- hpw:dispatcher action=conflict-round-attempt pr={PR} head={head} -->"


def owner_marker(head: str) -> str:
    return f"<!-- hpw:dispatcher action=merge-owner pr={PR} head={head} -->"


def ownerless_marker(head: str) -> str:
    return f"<!-- hpw:dispatcher action=merge-owner-missing pr={PR} head={head} -->"


def fix_round_attempt_marker(head: str = HEAD) -> str:
    return f"<!-- hpw:dispatcher action=fix-round-attempt pr={PR} head={head} -->"


def decision_blocker(by: str = PO) -> tuple[str, str]:
    return ("## Blocked Declaration\nType: decision\nBlocks: #500\nQuestion: waive the findings?\n"
            "Unblocked when: the PO decides", by)


def stop_the_session_at_the_usage_limit(d: Dispatcher) -> None:
    d.claude_stream(*LIMITED, exit_code=1)


def let_sessions_finish(d: Dispatcher) -> None:
    (d.mock / "claude_stream.jsonl").unlink(missing_ok=True)


def assert_one_ownerless_blocker(d: Dispatcher, head: str = NEW_HEAD) -> None:
    [blocker] = blockers(d)
    assert "Type: resource" in blocker
    assert f"the merge owner is missing for head {head[:7]} on PR #{PR}" in blocker
    assert "a session stopped after its push, before the hand-off finished" in blocker
    assert "docs/TAKEOVER-RULES.md → Merge Takeover" in blocker
    assert "pushes a new head and posts /unblock" in blocker
    assert ownerless_marker(head) in blocker
    assert len(po_events(d)) == 1


# ── #409 AC 1, AC 3: conflict in "In merge" ─────────────────────────────────


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
    assert d.phases == [] and d.sessions == [f"implement {STORY}"] and blockers(d) == []

    # The bookkeeping job moves it to "In merge" after a clean review; the
    # next resume pass merges the new head with the script-only /merge.
    d.set_status(STORY, "In merge")
    proc = d.resume()
    assert proc.returncode == 0, proc.stderr
    assert d.phases == [f"merge {PR}", f"cleanup {STORY}"]
    assert d.sessions == [f"implement {STORY}"]


# ── #409 AC 2: conflict in "In review" ──────────────────────────────────────


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
    d.add_comment(PR, owner_marker(HEAD))  # the dispatcher's own /implement pushed this head

    proc = d.resume()

    assert proc.returncode == 0, proc.stderr
    assert d.sessions == [] and d.board_writes == [] and d.posted(STORY) == []


def test_ac2_in_review_with_an_unreadable_pr_starts_nothing(d):
    conflicting_story(d, "In review")
    d.fail_pr_read()

    proc = d.resume()

    assert proc.returncode == 0, proc.stderr
    assert d.sessions == [] and d.board_writes == [] and d.posted(STORY) == []


# ── #449 AC 1: no record of an open round ───────────────────────────────────


def test_449_ac1_no_round_writes_a_start_record_or_a_pause_marker(d):
    conflicting_story(d, "In review")
    stop_the_session_at_the_usage_limit(d)
    d.resume()
    let_sessions_finish(d)
    session_merges_main(d)
    d.resume()

    written = d.posted(STORY) + d.posted(PR)
    assert not any("conflict-round-start" in body or "conflict-round-done" in body for body in written)
    # The pause record is the standard one, labelled for the PO, with no marker.
    [pause] = pause_records(d)
    assert f"- Step: `/implement {STORY}` for #{STORY} (conflict round)" in pause
    assert "<!--" not in pause
    assert "The next run decides from the PR as it is then" in pause
    # No code reads such a record.
    script = (REPO_ROOT / "scripts" / "dispatcher-invoke.sh").read_text(encoding="utf-8")
    assert "conflict-round-start" not in script and "conflict-round-done" not in script


def test_449_ac1_the_pause_record_decides_nothing(d):
    """A pause record left on a story whose PR is clean and owned starts nothing."""
    d.add_story(STORY, "In implementation", executor="claude-code-opus")
    d.add_pr(PR, STORY, head=NEW_HEAD)
    d.add_comment(PR, owner_marker(NEW_HEAD))
    d.add_comment(STORY, f"{PAUSED}\n\n- Step: `/implement {STORY}` for #{STORY} (conflict round)\n")

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [] and blockers(d) == [] and d.queued_events == []


# ── #449 AC 2: one rule finds an owed round (resume pass) ───────────────────


CONDITIONS = {
    # condition: (setup, round owed)
    "owed": (lambda d: None, True),
    "not conflicting": (lambda d: (d.add_pr(PR, STORY, head=HEAD), d.add_comment(PR, owner_marker(HEAD))), False),
    "fix round due": (lambda d: d.add_findings(PR, HEAD), False),
    "open blocker": (lambda d: d.add_comment(STORY, *decision_blocker()), False),
    "attempt marker on the head": (lambda d: d.add_comment(STORY, attempt_marker()), False),
    "blocker lifted by /unblock": (lambda d: (d.add_comment(STORY, *decision_blocker()),
                                              d.add_comment(STORY, "/unblock", login=PO)), True),
    "dispatcher blocker of an older head": (
        lambda d: d.add_comment(STORY, f"{BLOCKED}\nType: external\n\n{attempt_marker(OTHER_HEAD)}"), True),
    "completed fix round on the head": (
        lambda d: (d.add_findings(PR, HEAD), d.add_comment(STORY, fix_round_attempt_marker())), True),
}


@pytest.mark.parametrize("status", REVIEW_LOOP_STATUSES)
@pytest.mark.parametrize("condition", list(CONDITIONS))
def test_449_ac2_resume_pass_applies_the_owed_round_rule(d, status, condition):
    conflicting_story(d, status)
    setup, owed = CONDITIONS[condition]
    setup(d)

    proc = d.resume("--dry-run")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (ROUND_LOG in proc.stdout) is owed, proc.stdout
    if condition != "not conflicting":
        assert f"merge {PR}" not in d.phases  # a conflicting PR is never merged


def test_449_ac2_a_story_in_implementation_with_a_conflicting_pr_gets_a_fresh_round(d):
    conflicting_story(d, "In implementation")
    session_merges_main(d)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert ROUND_LOG in proc.stdout and d.sessions == [f"implement {STORY}"]
    assert d.board_writes == [f"{STORY}=In review"]  # no pre-start write: it is already there
    assert owner_marker(NEW_HEAD) in d.posted(PR) and blockers(d) == []


def test_findings_recorded_after_the_pause_start_the_fix_round(d):
    conflicting_story(d, "In review")
    stop_the_session_at_the_usage_limit(d)
    d.resume()
    let_sessions_finish(d)

    d.add_findings(PR, HEAD)  # the bookkeeping job acted on the head after the pause
    proc = d.resume("--dry-run")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"[RESUME] #{STORY}: fix round 1 of " in proc.stdout
    assert ROUND_LOG not in proc.stdout


# ── #409 AC 2, #449 AC 2: the poll lists conflict_due ───────────────────────


def poll(tmp_path, items: list[tuple], pulls: list[dict], details: dict[int, dict],
         comments: dict[int, list[dict]] | None = None) -> tuple[dict, str]:
    """Run the real dispatcher-poll.sh against a fake ``curl``.

    ``details`` maps a PR number to its single-PR read; a PR missing from it
    cannot be read (curl fails, as on an HTTP error). ``comments`` maps an
    issue or PR number to its comments; a ``details/unreadable_<n>`` file
    makes the comment read of <n> fail.
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
    detail_dir.mkdir(exist_ok=True)
    (tmp_path / "board.json").write_text(json.dumps(board), encoding="utf-8")
    (tmp_path / "pulls.json").write_text(json.dumps(pulls), encoding="utf-8")
    for number, detail in details.items():
        (detail_dir / f"pr_{number}.json").write_text(json.dumps({"number": number, **detail}), encoding="utf-8")
    for number, items_ in (comments or {}).items():
        (detail_dir / f"comments_{number}.json").write_text(json.dumps(items_), encoding="utf-8")
    curl = bin_dir / "curl"
    curl.write_text(f"""#!/usr/bin/env bash
url="${{@: -1}}"
echo "$url" >> "$CURL_LOG"
case "$url" in
  */user) echo '{{"login": "{BOT}"}}' ;;
  */issues/*/comments*)
    n="${{url%/comments*}}"; n="${{n##*/}}"
    [[ "$url" == *"page=1" ]] || {{ echo '[]'; exit 0; }}
    [[ -f "$DETAIL_DIR/unreadable_$n" ]] && exit 22
    f="$DETAIL_DIR/comments_$n.json"; [[ -f "$f" ]] && cat "$f" || echo '[]' ;;
  */pulls/[0-9]*) f="$DETAIL_DIR/pr_${{url##*/}}.json"; [[ -f "$f" ]] || exit 22; cat "$f" ;;
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
        env=env, capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout), proc.stderr


def open_pr(number: int, issue: int, draft: bool = False) -> dict:
    return {"number": number, "draft": draft, "body": f"Closes #{issue}"}


def comment(body: str, login: str = BOT, at: str = "2026-10-13T10:00:00Z", **extra) -> dict:
    return {"user": {"login": login}, "body": body, "created_at": at, **extra}


def findings(head: str, at: str = "2026-10-13T09:00:00Z") -> dict:
    from tests.dispatcher_fakes import bookkeeping_marker
    return comment("## Session Summary\nCommand: review\n\n" + bookkeeping_marker("move-in-implementation", head),
                   at=at)


DIRTY = {"mergeable": False, "mergeable_state": "dirty", "state": "open", "draft": False}


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
        details={11: {"mergeable": False, "mergeable_state": "dirty", "head": {"sha": HEAD}},
                 12: {"mergeable": None, "mergeable_state": "dirty", "head": {"sha": HEAD}},
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


POLL_CONDITIONS = {
    # condition: (story comments, PR comments, listed)
    "owed": ([], [], True),
    "fix round due": ([], [findings(HEAD)], False),
    "open blocker": ([comment(decision_blocker()[0], login=PO)], [], False),
    "attempt marker on the head": ([comment(f"{BLOCKED}\n\n" + attempt_marker().replace(f"pr={PR}", "pr=11"))],
                                   [], False),
    "blocker lifted by /unblock": ([comment(decision_blocker()[0], login=PO),
                                    comment("/unblock", login=PO, at="2026-10-13T11:00:00Z")], [], True),
    "completed fix round on the head": (
        [comment(fix_round_attempt_marker().replace(f"pr={PR}", "pr=11"))], [findings(HEAD)], True),
    "marker from another account": ([comment(attempt_marker().replace(f"pr={PR}", "pr=11"), login="someone")],
                                    [], True),
}


@pytest.mark.parametrize("condition", list(POLL_CONDITIONS))
def test_449_ac2_poll_applies_the_owed_round_rule(tmp_path, condition):
    story_comments, pr_comments, listed = POLL_CONDITIONS[condition]
    out, stderr = poll(tmp_path, [(1, "In review", "claude-code-opus")], pulls=[open_pr(11, 1)],
                       details={11: {**DIRTY, "head": {"sha": HEAD}}},
                       comments={1: story_comments, 11: pr_comments})
    assert out["conflict_due"] == ([{"number": 1, "title": "Story 1"}] if listed else []), stderr


def test_449_ac2_poll_lists_nothing_when_the_comments_cannot_be_read(tmp_path):
    (tmp_path / "details").mkdir()
    (tmp_path / "details" / "unreadable_1").write_text("")
    out, stderr = poll(tmp_path, [(1, "In review", "claude-code-opus")], pulls=[open_pr(11, 1)],
                       details={11: {**DIRTY, "head": {"sha": HEAD}}})
    assert out["conflict_due"] == []
    assert "[POLL] Warning: could not read the comments of #1 or PR #11; conflict_due skips it this cycle" in stderr


# ── #409 AC 4: unknown mergeability waits one run ───────────────────────────


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


# ── #409 AC 5: merges before conflict rounds ────────────────────────────────


def test_ac5_every_merge_of_the_run_comes_before_any_conflict_round(d):
    conflicting_story(d, "In merge", story=501, pr=901)   # first on the board
    d.add_story(502, "In merge", executor="claude-code-opus")
    d.add_pr(902, 502, head=OTHER_HEAD)                  # clean
    session_merges_main(d, pr=901)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.phases[0] == "merge 902" and "merge 901" not in d.phases
    assert d.sequence.index("merge 902") < d.sequence.index("session implement 501")


# ── #409 AC 6, #449 AC 3: success is judged from the PR ─────────────────────


def assert_one_conflict_blocker(d: Dispatcher, head: str = HEAD) -> None:
    [blocker] = blockers(d)
    assert f"merge conflict with main on PR #{PR}" in blocker
    assert "This blocker is the PO's only alert for this conflict." in blocker
    assert ("Unblocked when: the PR branch is merged with main and the conflict resolved, "
            "or the PO posts /unblock") in blocker
    assert attempt_marker(head) in blocker
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


def test_449_ac3_a_clean_pr_on_the_same_head_gets_the_owner_and_the_hand_off(d):
    """Main moved so the conflict is gone; the session pushed nothing. The PR decides."""
    conflicting_story(d, "In merge")
    d.on_session("implement", f"""
jq 'map(if .number == {PR} then .mergeable = true | .mergeable_state = "clean" else . end)' \\
    "$MOCK_DIR/pulls.json" > "$MOCK_DIR/p.tmp" && mv "$MOCK_DIR/p.tmp" "$MOCK_DIR/pulls.json"
""")

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert owner_marker(HEAD) in d.posted(PR)
    assert d.status(STORY) == "In review" and blockers(d) == [] and d.queued_events == []


def test_449_ac3_a_new_head_that_still_conflicts_gets_one_blocker(d):
    conflicting_story(d, "In review")
    d.on_session("implement", f"""
jq 'map(if .number == {PR} then .head.sha = "{NEW_HEAD}" else . end)' \\
    "$MOCK_DIR/pulls.json" > "$MOCK_DIR/p.tmp" && mv "$MOCK_DIR/p.tmp" "$MOCK_DIR/pulls.json"
""")

    proc = d.resume()

    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert_one_conflict_blocker(d, head=NEW_HEAD)
    assert owner_marker(NEW_HEAD) not in d.posted(PR)

    proc = d.resume()  # the attempt marker names the head the PR has now
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {STORY}"] and len(blockers(d)) == 1 and len(po_events(d)) == 1


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


# ── #409 AC 7: a refused round changes nothing ──────────────────────────────


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
    assert d.posted(STORY) == [] and d.queued_events == []
    output = proc.stdout + proc.stderr
    assert f'stays in "{status}"' in output and "the conflict round is tried again" in output

    proc = d.resume()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {STORY}"]
    assert d.status(STORY) == "In review"


def test_ac7_failed_status_write_starts_no_session_and_writes_nothing(d):
    conflicting_story(d, "In merge")
    d.fail_status_write("In implementation")

    proc = d.resume()

    assert proc.returncode == 0, proc.stderr
    assert d.sessions == [] and d.status(STORY) == "In merge"
    assert d.posted(STORY) == [] and d.queued_events == []
    assert "the conflict round on PR #900 is tried again on the next run" in proc.stdout


# ── #449 AC 4: a head left without an owner gets one blocker ────────────────


@pytest.mark.parametrize("status", ["In implementation", "In review"])
def test_449_ac4_a_clean_head_without_merge_owner_evidence_gets_one_blocker(d, status):
    d.add_story(STORY, status, executor="claude-code-opus")
    d.add_pr(PR, STORY, head=NEW_HEAD)
    d.add_comment(PR, owner_marker(HEAD))  # evidence for an older head does not count

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [] and d.board_writes == [] and d.status(STORY) == status
    assert OWNERLESS_LOG in proc.stdout
    assert_one_ownerless_blocker(d)

    proc = d.resume()  # a later run on the same head posts nothing more
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [] and len(blockers(d)) == 1 and len(po_events(d)) == 1
    assert "has no merge-owner evidence; the blocker is already posted" in proc.stdout

    d.add_comment(STORY, "/unblock", login=PO)  # an /unblock on the same head changes nothing
    proc = d.resume()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [] and len(blockers(d)) == 1


@pytest.mark.parametrize("evidence", [
    owner_marker(NEW_HEAD),
    f"Dispatcher: pending.\n\n<!-- hpw:dispatcher action=evidence-pending item=merge-owner head={NEW_HEAD} payload=eA== -->",
], ids=["marker", "evidence-pending copy"])
def test_449_ac4_merge_owner_evidence_posts_nothing(d, evidence):
    d.add_story(STORY, "In review", executor="claude-code-opus")
    d.add_pr(PR, STORY, head=NEW_HEAD)
    d.add_comment(PR, evidence)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.posted(STORY) == [] and d.queued_events == []


def test_449_ac4_evidence_from_another_account_does_not_count(d):
    d.add_story(STORY, "In review", executor="claude-code-opus")
    d.add_pr(PR, STORY, head=NEW_HEAD)
    d.add_comment(PR, owner_marker(NEW_HEAD), login="someone-else")

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert_one_ownerless_blocker(d)


def test_449_ac4_a_pending_hand_off_posts_no_blocker(d):
    d.add_story(STORY, "In implementation", executor="claude-code-opus")
    d.add_pr(PR, STORY, head=NEW_HEAD)
    d.add_comment(STORY, f"<!-- hpw:dispatcher action=hand-off-pending pr={PR} head={NEW_HEAD} -->")

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"[RESUME] #{STORY}: completing the hand-off on PR #{PR}" in proc.stdout
    assert blockers(d) == [] and d.queued_events == [] and d.status(STORY) == "In review"


@pytest.mark.parametrize("status", ["In implementation", "In review"])
def test_449_ac4_a_due_fix_round_posts_no_blocker(d, status):
    d.add_story(STORY, status, executor="claude-code-opus")
    d.add_pr(PR, STORY, head=NEW_HEAD)
    d.add_findings(PR, NEW_HEAD)

    proc = d.resume("--dry-run")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "merge-owner-missing" not in proc.stdout and d.posted(STORY) == []


@pytest.mark.parametrize("pr_fields", [{"draft": True}, UNKNOWN], ids=["draft", "unknown mergeability"])
def test_449_ac4_a_draft_or_undecided_pr_posts_no_blocker(d, pr_fields):
    d.add_story(STORY, "In implementation", executor="claude-code-opus")
    d.add_pr(PR, STORY, head=NEW_HEAD, **pr_fields)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.posted(STORY) == [] and d.queued_events == [] and d.sessions == []


def test_449_ac4_the_blocker_holds_the_story_against_stale_recovery(d):
    """Stale recovery would restart /implement as a ready story past the blocker."""
    d.add_story(700, "Ready for implementation")
    d.add_pr(970, 700)
    d.add_story(STORY, "In implementation", executor="claude-code-opus")
    d.add_pr(PR, STORY, head=NEW_HEAD)

    proc = d.run_issue(700)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == ["implement 700"] and d.status(STORY) == "In implementation"
    assert f"#{STORY} is held by a conflict-round or merge-owner blocker" in proc.stdout
    assert_one_ownerless_blocker(d)


# ── #449 AC 5: no stop point leaves a story silent ──────────────────────────


@pytest.mark.parametrize("from_status", ["In merge", "In review"])
def test_449_ac5_pause_before_the_push_gets_a_fresh_round(d, from_status):
    conflicting_story(d, from_status)
    stop_the_session_at_the_usage_limit(d)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.status(STORY) == "In implementation"
    assert d.board_writes == [f"{STORY}=In implementation"]  # the pause writes no status
    assert len(pause_records(d)) == 1 and blockers(d) == [] and d.queued_events == []

    let_sessions_finish(d)
    session_merges_main(d)
    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert ROUND_LOG in proc.stdout
    assert d.sessions == [f"implement {STORY}", f"implement {STORY}"]
    assert owner_marker(NEW_HEAD) in d.posted(PR)
    assert d.status(STORY) == "In review" and blockers(d) == []


@pytest.mark.parametrize("session, left_in", [
    (session_merges_main, "In implementation"),
    (session_merges_main_and_hands_off, "In review"),
], ids=["after the push", "after In review"])
def test_449_ac5_pause_after_the_push_gets_one_blocker(d, session, left_in):
    conflicting_story(d, "In merge")
    session(d)
    stop_the_session_at_the_usage_limit(d)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.status(STORY) == left_in and len(pause_records(d)) == 1 and blockers(d) == []

    let_sessions_finish(d)
    d.on_session("implement", "")
    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {STORY}"]  # no session for the stopped round
    assert OWNERLESS_LOG in proc.stdout
    assert_one_ownerless_blocker(d)


@pytest.mark.parametrize("status, pr_fields, outcome", [
    ("In implementation", {"head": HEAD, **CONFLICTING}, "round"),
    ("In implementation", {"head": NEW_HEAD}, "blocker"),
    ("In review", {"head": NEW_HEAD}, "blocker"),
], ids=["before the push", "after the push", "after In review"])
def test_449_ac5_cancelled_run_gets_a_round_or_one_blocker(d, status, pr_fields, outcome):
    """A cancelled run posts no pause record: the state left on GitHub decides."""
    d.add_story(STORY, status, executor="claude-code-opus")
    d.add_pr(PR, STORY, **pr_fields)
    d.add_comment(PR, owner_marker(HEAD))  # the head before the round was the dispatcher's own
    session_merges_main(d)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    if outcome == "round":
        assert ROUND_LOG in proc.stdout and d.sessions == [f"implement {STORY}"]
        assert owner_marker(NEW_HEAD) in d.posted(PR) and blockers(d) == []
    else:
        assert d.sessions == [] and OWNERLESS_LOG in proc.stdout
        assert_one_ownerless_blocker(d)


def test_449_ac5_failed_write_of_the_pause_record_gets_a_fresh_round(d):
    conflicting_story(d, "In review")
    d.on_session("implement", f'echo 1 > "$MOCK_DIR/fail_posts_{STORY}"')
    stop_the_session_at_the_usage_limit(d)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.status(STORY) == "In implementation" and d.posted(STORY) == []
    assert "could not post the pause record" in proc.stderr

    let_sessions_finish(d)
    session_merges_main(d)
    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert ROUND_LOG in proc.stdout
    assert d.sessions == [f"implement {STORY}", f"implement {STORY}"]
    assert owner_marker(NEW_HEAD) in d.posted(PR) and blockers(d) == [] and d.status(STORY) == "In review"


@pytest.mark.parametrize("status", ["In implementation", "In review"])
def test_449_ac5_round_on_a_head_with_a_completed_fix_round_is_not_hidden(d, status):
    conflicting_story(d, status)
    d.add_findings(PR, HEAD)
    d.add_comment(STORY, fix_round_attempt_marker())  # the post-waiver round that pushed nothing
    session_merges_main(d)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert ROUND_LOG in proc.stdout
    assert d.sessions == [f"implement {STORY}"]
    assert owner_marker(NEW_HEAD) in d.posted(PR)
    assert blockers(d) == [] and d.queued_events == [] and d.status(STORY) == "In review"


# ── #409 AC 8: stories without a conflict round ─────────────────────────────


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


# ── #409 AC 9, #449 AC 6: no gate is weakened ───────────────────────────────


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
