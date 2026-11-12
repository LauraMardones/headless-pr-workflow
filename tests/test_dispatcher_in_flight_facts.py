"""The resume pass finds work in flight from PR facts, not from board statuses (issue #475).

Drives the real ``scripts/dispatcher-invoke.sh --resume`` with the fakes in
``tests/dispatcher_fakes.py``. Each test sets the board card to a status that does
not match the step, so only the PR facts can select the story. The rule itself is
tested in ``tests/test_dispatcher_in_flight.py``.
"""

from __future__ import annotations

import re
import subprocess

import pytest

from tests.dispatcher_fakes import BASH, REPO_ROOT, Dispatcher, needs_tools
from tests.script_tools import windows_env

pytestmark = needs_tools

STORY = 500
OTHER = 501
PR = 900
OTHER_PR = 901
HEAD = "a" * 40
INVOKE = REPO_ROOT / "scripts" / "dispatcher-invoke.sh"
POLL = REPO_ROOT / "scripts" / "dispatcher-poll.sh"
IN_FLIGHT_LIB = REPO_ROOT / "scripts" / "lib" / "in-flight.sh"
BOARD_STATUSES = ("In merge", "In review", "In implementation", "Ready for implementation")


@pytest.fixture
def d(tmp_path):
    return Dispatcher(tmp_path)


def output(proc) -> str:
    return proc.stdout + proc.stderr


def owner(d: Dispatcher, pr: int = PR, head: str = HEAD) -> None:
    d.add_comment(pr, f"<!-- hpw:dispatcher action=merge-owner pr={pr} head={head} -->")


def pending_hand_off(d: Dispatcher) -> None:
    d.add_comment(STORY, f"## Recovery Comment\n\n<!-- hpw:dispatcher action=hand-off-pending pr={PR} head={HEAD} -->")


# ── AC 1: no board status chooses work in flight ─────────────────────────────


def _function(text: str, name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{\n(.*?)^\}}\n", text, re.MULTILINE | re.DOTALL)
    assert match, f"{name} not found"
    return match.group(1)


def _code(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


RESUME_FUNCTIONS = ("run_resume_pass", "resume_row_story", "resume_in_merge_story", "resume_fix_round",
                    "resume_pending_hand_off", "resume_review_story", "inflight_prepare", "ask_scheduler")
STATUS_NAMES = ("In merge", "In implementation", "In review", "Ready for implementation")


def test_ac1_no_script_selects_in_flight_work_by_a_board_status():
    invoke = INVOKE.read_text(encoding="utf-8")
    for path in (INVOKE, POLL, IN_FLIGHT_LIB):
        code = _code(path.read_text(encoding="utf-8"))
        assert "board_items_in_status" not in code and "status_items" not in code, path
        if path != INVOKE:
            assert not re.search(r'\(\.name\? // ""\) == "(In |Ready)', code), path
    for name in RESUME_FUNCTIONS:
        body = _code(_function(invoke, name))
        assert "board_status_of" not in body, name
        assert not re.search(r'\(\.name\? // ""\) == "(In |Ready)', body), name
    # resume_review_story decides the merge from the gate facts, not from the status it reads back.
    review = _code(_function(invoke, "resume_review_story"))
    assert "inflight_gates_pass" in review and '"In merge" ]]' not in review
    poll = _code(POLL.read_text(encoding="utf-8"))
    for status in STATUS_NAMES:
        assert f'"{status}"' not in poll, status
    assert not any(f'"{status}"' in _code(IN_FLIGHT_LIB.read_text(encoding="utf-8")) for status in STATUS_NAMES)


# ── AC 2: the merge step ─────────────────────────────────────────────────────


@pytest.mark.parametrize("status", ["In review", "In implementation", "Ready for implementation"])
def test_ac2_gates_passing_on_the_head_give_the_merge_step_whatever_the_card(d, status):
    d.add_story(STORY, status, executor="codex")
    d.add_pr(PR, STORY, draft=False, owned=True)
    d.set_gates(PR, "pass")

    proc = d.resume()

    assert proc.returncode == 0, output(proc)
    assert f"[RESUME] #{STORY}: /merge PR #{PR}" in proc.stdout
    assert d.phases == [f"merge {PR}", f"cleanup {STORY}"] and d.status(STORY) == "Done"


def test_ac2_passing_gates_merge_even_with_a_pending_hand_off(d):
    d.add_story(STORY, "In review", executor="claude-code-sonnet")
    d.add_pr(PR, STORY, draft=False, owned=True)
    d.set_gates(PR, "pass")
    pending_hand_off(d)

    proc = d.resume()

    assert proc.returncode == 0, output(proc)
    assert f"[RESUME] #{STORY}: /merge PR #{PR}" in proc.stdout
    assert d.phases == [f"merge {PR}", f"cleanup {STORY}"]


@pytest.mark.parametrize("gate", ["fail", "unreadable"])
def test_ac2_a_failing_or_unreadable_gate_gives_no_merge_even_in_merge(d, gate):
    d.add_story(STORY, "In merge", executor="claude-code-sonnet")
    d.add_pr(PR, STORY)  # owned: the merge-owner marker is on the head
    d.set_gates(PR, gate)

    proc = d.resume()

    assert proc.returncode == 0, output(proc)
    assert d.phases == [] and d.sessions == [] and f"/merge PR #{PR}" not in proc.stdout


@pytest.mark.parametrize("line", [
    "policy=pass(present_non_required), checks=fail, approval=separate-session, threads=none, mergeable=yes, head=aaaaaaa",
    "policy=pass(present_non_required), checks=pending, approval=separate-session, threads=none, mergeable=yes, head=aaaaaaa",
    "policy=pass(present_non_required), checks=pass, approval=none, threads=none, mergeable=yes, head=aaaaaaa",
    "policy=pass(present_non_required), checks=pass, approval=separate-session, threads=unresolved(1), mergeable=yes, head=aaaaaaa",
    "policy=fail(missing), checks=pass, approval=separate-session, threads=none, mergeable=yes, head=aaaaaaa",
    "policy=pass(present_non_required), checks=pass, approval=separate-session, threads=none, mergeable=yes, head=bbbbbbb",
])
def test_ac2_each_failing_gate_keeps_the_pr_out_of_the_merge_step(tmp_path, line):
    assert not _gates_pass(tmp_path, f"Merge gate: {line}", 1)


@pytest.mark.parametrize("mergeable", ["yes", "no", "unknown"])
def test_ac2_mergeability_is_left_to_the_merge_step(tmp_path, mergeable):
    line = ("Merge gate: policy=pass(present_non_required), checks=absent-ok, approval=codex-review, "
            f"threads=none, mergeable={mergeable}, head=aaaaaaa")
    assert _gates_pass(tmp_path, line, 0 if mergeable == "yes" else 1)


def test_ac2_a_gate_script_that_cannot_run_counts_as_not_passing(tmp_path):
    assert not _gates_pass(tmp_path, "merge-gate-summary: could not read the pull request", 2)


def _gates_pass(tmp_path, line: str, rc: int) -> bool:
    script = tmp_path / "gate.sh"
    script.write_text(f"#!/usr/bin/env bash\necho '{line}'\nexit {rc}\n", encoding="utf-8", newline="\n")
    script.chmod(0o755)
    driver = (f'source "{IN_FLIGHT_LIB.as_posix()}"; REPO=o/r MERGE_GATE_SCRIPT="{script.as_posix()}" '
              f'inflight_gates_pass 900 {HEAD}')
    return subprocess.run([BASH, "-c", driver], env=windows_env(), capture_output=True, timeout=60).returncode == 0


def test_ac2_unknown_mergeability_still_waits(d):
    d.add_story(STORY, "In review", executor="codex")
    d.add_pr(PR, STORY, owned=True, mergeable=None, mergeable_state="unknown")
    d.set_gates(PR, "pass")

    proc = d.resume()

    assert proc.returncode == 0, output(proc)
    assert (f"[RESUME SKIP] #{STORY}: GitHub has not computed mergeability for PR #{PR} yet; "
            "trying again on the next run") in proc.stdout
    assert d.phases == [] and d.sessions == []


def test_ac2_a_conflict_still_gets_the_conflict_blocker(d):
    d.add_story(STORY, "In implementation", executor="codex")
    d.add_pr(PR, STORY, owned=True, mergeable=False, mergeable_state="dirty")
    d.set_gates(PR, "conflicting")

    proc = d.resume()

    assert proc.returncode == 0, output(proc)
    assert d.phases == [] and d.sessions == []
    blockers = [b for b in d.posted(STORY) if "## Blocked Declaration" in b]
    assert len(blockers) == 1 and "conflict" in blockers[0].lower()


# ── AC 3: finish a merged PR ─────────────────────────────────────────────────


@pytest.mark.parametrize("status", ["In review", "In implementation", "Ready for implementation"])
def test_ac3_a_merged_linked_pr_is_finished_whatever_the_card(d, status):
    d.add_story(STORY, status)
    d.add_pr(PR, STORY, state="closed", merged=True)

    proc = d.resume()

    assert proc.returncode == 0, output(proc)
    assert d.phases == [f"cleanup {STORY}"] and d.status(STORY) == "Done" and d.sessions == []


# ── AC 4: the review step ────────────────────────────────────────────────────


@pytest.mark.parametrize("status", ["In implementation", "In merge", "Ready for implementation"])
def test_ac4_review_once_per_head_whatever_the_card(d, status):
    d.add_story(STORY, status, executor="codex")
    d.add_pr(PR, STORY, draft=False)
    d.set_gates(PR, "fail")

    first = d.resume()
    second = d.resume()

    assert first.returncode == 0 and second.returncode == 0, output(first) + output(second)
    assert d.sessions == [f"review {PR}"]
    assert f"[RESUME SKIP] #{STORY}: /review was already tried on PR #{PR} head {HEAD[:7]}" in output(second)
    assert d.phases == []


def test_ac4_after_review_the_merge_runs_only_when_the_gates_pass(d):
    d.add_story(STORY, "In implementation", executor="codex")
    d.add_pr(PR, STORY, owned=True)
    d.set_gates(PR, "fail")
    d.on_session("review", f'echo pass > "$MOCK_DIR/gate_{PR}"\n')  # the formal review approved the head

    proc = d.resume()

    assert proc.returncode == 0, output(proc)
    assert d.sessions == [f"review {PR}"]
    assert d.phases == [f"merge {PR}", f"cleanup {STORY}"]


def test_ac4_review_leaving_the_gates_failing_runs_no_merge_even_when_the_card_says_in_merge(d):
    d.add_story(STORY, "In implementation", executor="codex")
    d.add_pr(PR, STORY, owned=True)
    d.set_gates(PR, "fail")
    d.session_sets_status("review", STORY, "In merge")

    proc = d.resume()

    assert proc.returncode == 0, output(proc)
    assert d.phases == []
    assert f"[RESUME] #{STORY}: /review left PR #{PR} outside the merge step; no merge" in proc.stdout


def test_ac4_review_loop_story_gets_the_missing_owner_blocker_whatever_the_card(d):
    d.add_story(STORY, "Ready for implementation", executor="claude-code-sonnet")
    d.add_pr(PR, STORY, draft=False, owned=False)

    proc = d.resume()

    assert proc.returncode == 0, output(proc)
    assert d.sessions == []
    blockers = d.posted(STORY)
    assert len(blockers) == 1 and "Type: resource" in blockers[0] and f"PR #{PR}" in blockers[0]


def test_ac4_review_loop_story_gets_the_conflict_round_whatever_the_card(d):
    d.add_story(STORY, "In merge", executor="claude-code-sonnet")
    d.add_pr(PR, STORY, mergeable=False, mergeable_state="dirty")
    d.set_gates(PR, "fail")

    proc = d.resume()

    assert f"[RESUME] #{STORY}: PR #{PR} conflicts with main; conflict round, /implement on PR #{PR}" in proc.stdout
    assert d.sessions == [f"implement {STORY}"]


# ── AC 5: fix round and pending hand-off ─────────────────────────────────────


@pytest.mark.parametrize("status", ["In review", "In merge", "Ready for implementation"])
def test_ac5_findings_on_the_head_give_the_fix_round_whatever_the_card(d, status):
    d.add_story(STORY, status)
    d.add_pr(PR, STORY, draft=False)
    owner(d)
    d.add_findings(PR, HEAD)
    d.set_gates(PR, "fail")

    proc = d.resume()

    assert f"[RESUME] #{STORY}: fix round 1 of 3, /implement on PR #{PR}" in proc.stdout, output(proc)
    assert d.sessions == [f"implement {STORY}"]


@pytest.mark.parametrize("status", ["In merge", "In implementation", "Ready for implementation"])
def test_ac5_a_pending_hand_off_is_completed_whatever_the_card(d, status):
    d.add_story(STORY, status)
    d.add_pr(PR, STORY, draft=False)
    owner(d)
    pending_hand_off(d)
    d.set_gates(PR, "fail")

    proc = d.resume()

    assert proc.returncode == 0, output(proc)
    assert f"[RESUME] #{STORY}: completing the hand-off on PR #{PR}" in proc.stdout
    assert d.status(STORY) == "In review" and d.sessions == []
    assert any(f"action=hand-off-done pr={PR}" in b for b in d.posted(STORY))


# ── AC 6: moving a card starts nothing ───────────────────────────────────────


@pytest.mark.parametrize("status", BOARD_STATUSES)
@pytest.mark.parametrize("executor", ["codex", "claude-code-sonnet"])
def test_ac6_a_moved_card_without_a_pr_or_marker_starts_nothing(d, status, executor):
    d.add_story(STORY, status, executor=executor)

    proc = d.resume()

    assert proc.returncode == 0, output(proc)
    assert d.sessions == [] and d.phases == [] and d.board_writes == []
    assert d.posted(STORY) == [] and d.queued_events == []
    assert f"#{STORY}" not in "\n".join(line for line in proc.stdout.splitlines() if "[RESUME" in line)


# ── AC 10 and AC 11: the stale spec record holds every in-flight session ─────


STALE = {"ok": True, "block": "facts", "operation": "read", "result": "read", "error": None,
         "epics": [{"number": 306, "spec": {"state": "merged", "commit": "c" * 40}}],
         "issues": [{"number": STORY, "type": "story", "epic": 306, "stale": True,
                     "record": {"commit": "d" * 40, "state": "stale"}, "reasons": []},
                    {"number": OTHER, "type": "story", "epic": 306, "stale": False,
                     "record": {"commit": "c" * 40, "state": "current"}, "reasons": []}]}  # fmt: skip
STALE_LINE = (f"[RESUME SKIP] #{STORY}: stale spec record (ddddddd is older than the Epic's ccccccc); "
              "no fix round, conflict round, /review or /merge until it is re-refined")


def fix_round_due(d: Dispatcher, story: int = STORY, pr: int = PR) -> None:
    d.add_story(story, "In implementation")
    d.add_pr(pr, story, draft=False)
    owner(d, pr)
    d.add_findings(pr, HEAD)
    d.set_gates(pr, "fail")


def conflict_round_due(d: Dispatcher, story: int = STORY, pr: int = PR) -> None:
    d.add_story(story, "In review")
    d.add_pr(pr, story, mergeable=False, mergeable_state="dirty")
    d.set_gates(pr, "fail")


def review_due(d: Dispatcher, story: int = STORY, pr: int = PR) -> None:
    d.add_story(story, "In review", executor="codex")
    d.add_pr(pr, story)
    d.set_gates(pr, "fail")


def merge_due(d: Dispatcher, story: int = STORY, pr: int = PR) -> None:
    d.add_story(story, "In merge", executor="codex")
    d.add_pr(pr, story)
    d.set_gates(pr, "pass")


SESSION_KINDS = {"fix round": fix_round_due, "conflict round": conflict_round_due, "review": review_due,
                 "merge": merge_due}


@pytest.mark.parametrize("kind", list(SESSION_KINDS))
def test_ac10_a_stale_story_gets_no_in_flight_session(d, kind):
    SESSION_KINDS[kind](d)
    d.set_facts(STALE)

    proc = d.resume()

    assert proc.returncode == 0, output(proc)
    assert d.sessions == [] and d.phases == []
    assert STALE_LINE in proc.stdout


@pytest.mark.parametrize("kind", list(SESSION_KINDS))
def test_ac10_a_current_story_still_gets_its_session(d, kind):
    SESSION_KINDS[kind](d, story=OTHER, pr=OTHER_PR)
    d.set_facts(STALE)

    proc = d.resume()

    assert d.sessions or d.phases, output(proc)
    assert STALE_LINE not in proc.stdout


def test_ac10_finishing_a_merged_pr_is_not_held_back(d):
    d.add_story(STORY, "In merge")
    d.add_pr(PR, STORY, state="closed", merged=True)
    d.set_facts(STALE)

    proc = d.resume()

    assert proc.returncode == 0, output(proc)
    assert d.phases == [f"cleanup {STORY}"] and d.status(STORY) == "Done"
    assert STALE_LINE not in proc.stdout


@pytest.mark.parametrize("kind", list(SESSION_KINDS))
def test_ac11_unreadable_facts_start_no_in_flight_session(d, kind):
    SESSION_KINDS[kind](d)
    d.set_facts({"ok": False, "block": "facts", "operation": "read", "result": "unreadable",
                 "error": "open issues: HTTP 502"}, rc=3)

    proc = d.resume()

    assert proc.returncode == 0, output(proc)
    assert d.sessions == [] and d.phases == []
    assert ("[RESUME SKIP] facts unreadable: open issues: HTTP 502; no in-flight session in this run"
            in output(proc))


def test_ac11_one_story_with_unreadable_facts_is_skipped_alone(d):
    review_due(d)
    review_due(d, story=OTHER, pr=OTHER_PR)
    facts = {**STALE, "issues": [{**STALE["issues"][0], "stale": False, "reasons": ["unreadable: body"]},
                                 STALE["issues"][1]]}
    d.set_facts(facts)

    proc = d.resume()

    assert proc.returncode == 0, output(proc)
    assert f"[RESUME SKIP] #{STORY}: facts unreadable (body); trying again on the next run" in output(proc)
    assert d.sessions == [f"review {OTHER_PR}"]


def test_ac11_a_story_outside_any_epic_is_never_stale(d):
    review_due(d)
    d.set_facts({**STALE, "issues": []})  # no facts entry: no parent Epic or no merged spec (#283)

    proc = d.resume()

    assert proc.returncode == 0, output(proc)
    assert d.sessions == [f"review {PR}"]


def test_the_facts_are_read_once_per_run(d):
    review_due(d)
    review_due(d, story=OTHER, pr=OTHER_PR)
    d.set_facts(STALE)

    d.resume()

    assert (d.mock / "facts.log").read_text(encoding="utf-8").splitlines() == ["facts"]
