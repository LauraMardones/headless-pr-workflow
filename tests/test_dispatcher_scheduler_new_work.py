"""The dispatcher starts new work from the scheduler, not from the board (issue #474, ADR-010).

Drives the real ``scripts/dispatcher-invoke.sh`` in its scheduled form (``--repo`` only) with
the fakes in ``tests/dispatcher_fakes.py``. The fake ``hpw`` answers ``hpw dispatcher
scheduler next`` with the decisions a test sets (``schedule``), or runs the real rule of #473
on fixed facts (``schedule_from_facts``). The scripts and the workflow are also read as text.
"""

from __future__ import annotations

import re

import pytest

from tests.dispatcher_fakes import REPO_ROOT, Dispatcher, needs_tools

pytestmark = needs_tools

INVOKE = REPO_ROOT / "scripts" / "dispatcher-invoke.sh"
POLL = REPO_ROOT / "scripts" / "dispatcher-poll.sh"
STEP = REPO_ROOT / "scripts" / "dispatcher-workflow-step.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "dispatcher.yml"
STORY = 700
OTHER = 701
PR = 970
NEW_WORK_SESSIONS = ("implement", "review", "merge", "cleanup")


@pytest.fixture
def d(tmp_path):
    return Dispatcher(tmp_path)


def scheduled(d: Dispatcher, *args: str, **kwargs):
    """The run the workflow's invoke step starts: ``--repo`` only (scripts/dispatcher-workflow-step.sh)."""
    return d.run(*args, **kwargs)


def code_lines(path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")]


# ── AC1: the scheduler call and no "Ready" board read ─────────────────────────


def test_ac1_a_scheduled_run_asks_the_scheduler_with_the_work_in_flight(d):
    d.add_story(10, "In implementation")
    d.add_story(11, "In review")
    d.add_story(STORY, "Ready for implementation")
    d.schedule(("idle",))

    proc = scheduled(d)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.scheduler_calls == ["dispatcher scheduler next --repo o/r --in-implementation 10,11"]
    assert d.sessions == []


def test_ac1_dry_run_passes_dry_run_to_the_scheduler(d):
    proc = scheduled(d, "--dry-run")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.scheduler_calls == ["dispatcher scheduler next --repo o/r --in-implementation  --dry-run"]


@pytest.mark.parametrize("path", [INVOKE, POLL], ids=["invoke", "poll"])
def test_ac1_no_script_selects_board_items_by_a_ready_status(path):
    code = "\n".join(code_lines(path))
    # The jq filter that picks board items by their Status value.
    assert not re.search(r'\(\.name\? // ""\) == "Ready', code)
    assert "find_next_ready_story" not in code
    # Board item lists by status are read only for work in flight. The one
    # "Ready for implementation" read left is the resume pass's pending hand-off:
    # work already on a PR, which #475 moves to PR facts.
    reads = re.findall(r'(?:board_items_in_status|status_items) "([^"]+)"', code)
    allowed = {"In merge", "In implementation", "In review"} | ({"Ready for implementation"} if path == INVOKE else set())
    assert set(reads) <= allowed, reads
    if path == INVOKE:
        ready_reads = [line for line in code_lines(path) if 'board_items_in_status "Ready for implementation"' in line]
        assert ready_reads == ['    items=$(board_items_in_status "Ready for implementation")']
        resume_pass = code.split("run_resume_pass() {", 1)[1].split("\n}\n", 1)[0]
        assert 'board_items_in_status "Ready for implementation"' in resume_pass
        assert "resume_pending_hand_off" in resume_pass
    else:
        assert "Ready for refinement" not in code and "Ready for implementation" not in code


# ── AC2: refine ───────────────────────────────────────────────────────────────


def test_ac2_a_refine_decision_starts_one_refinement_session_and_ends_the_run(d):
    d.add_story(STORY, "In refinement")
    d.add_story(OTHER, "Ready for implementation")
    d.schedule(("refine", STORY), ("implement", OTHER))

    proc = scheduled(d)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"refine {STORY}"]
    assert not any(s.split()[0] in NEW_WORK_SESSIONS for s in d.sessions) and d.phases == []
    assert len(d.scheduler_calls) == 1  # no chained decision after it
    assert f"[REFINE] #{STORY}: /refine finished" in proc.stdout
    assert d.board_writes == []  # the /refine command writes the board, not the dispatcher


def test_ac2_a_refine_decision_runs_after_the_work_in_flight(d):
    d.add_story(20, "In merge")
    d.add_pr(920, 20)
    d.add_story(STORY, "In refinement")
    d.schedule(("refine", STORY))

    proc = scheduled(d)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sequence.index("merge 920") < d.sequence.index("scheduler") < d.sequence.index(f"session refine {STORY}")
    assert d.sessions == [f"refine {STORY}"]


# ── AC3: implement ────────────────────────────────────────────────────────────


def test_ac3_an_implement_decision_starts_implement_with_the_board_write(d):
    d.add_story(STORY, "Ready for implementation")
    d.add_pr(PR, STORY)
    d.schedule(("implement", STORY))

    proc = scheduled(d)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {STORY}"]
    assert d.board_writes[0] == f"{STORY}=In implementation"
    assert f"[HANDOFF] #{STORY}: handed to the Codex review loop" in proc.stdout


# ── AC4: moving a card starts nothing ────────────────────────────────────────


def test_ac4_ready_cards_the_scheduler_does_not_choose_get_no_session(d):
    d.add_story(STORY, "Ready for implementation")
    d.add_story(OTHER, "Ready for refinement")
    d.schedule(("idle",))

    proc = scheduled(d)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [] and d.board_writes == []


@pytest.mark.parametrize("status", ["Backlog", "In refinement", "Ready for refinement"])
def test_ac4_the_issue_the_scheduler_chooses_gets_its_session_whatever_its_status(d, status):
    d.add_story(OTHER, "Ready for implementation")  # a ready card that is not chosen
    d.add_story(STORY, status)
    d.add_pr(PR, STORY)
    d.schedule(("implement", STORY))

    proc = scheduled(d)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {STORY}"]
    assert f"{OTHER}=In implementation" not in d.board_writes


# ── AC5: idle and unreadable ──────────────────────────────────────────────────


def test_ac5_idle_starts_no_new_work_and_is_in_the_log(d):
    d.add_story(STORY, "Ready for implementation")
    d.schedule(("idle",))

    proc = scheduled(d)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[SCHEDULER] idle" in proc.stderr
    assert d.sessions == []


def test_ac5_unreadable_starts_no_new_work_and_work_in_flight_still_runs(d):
    d.add_story(20, "In merge")
    d.add_pr(920, 20)
    d.add_story(STORY, "Ready for implementation")
    d.schedule(("unreadable", None, "facts: GitHub is down"))

    proc = scheduled(d)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[SCHEDULER] unreadable: facts: GitHub is down" in proc.stderr
    assert "merge 920" in d.phases
    assert d.sessions == []


def test_ac5_an_output_that_cannot_be_read_counts_as_unreadable(d):
    (d.mock / "scheduler_1.json").write_text("not json", encoding="utf-8")
    (d.mock / "scheduler_1.err").write_text("", encoding="utf-8")
    (d.mock / "scheduler_1.rc").write_text("3")
    d.add_story(STORY, "Ready for implementation")

    proc = scheduled(d)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[SCHEDULER] unreadable: hpw dispatcher scheduler next exited 3 without a result" in proc.stderr
    assert d.sessions == []


# ── AC6: order, and a new call after a finished cycle ────────────────────────


def test_ac6_work_in_flight_runs_before_the_scheduler_decision(d):
    d.add_story(20, "In merge")
    d.add_pr(920, 20)
    d.add_story(STORY, "Ready for implementation")
    d.add_pr(PR, STORY)
    d.schedule(("implement", STORY))

    proc = scheduled(d)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    seq = d.sequence
    assert seq.index("merge 920") < seq.index("scheduler") < seq.index(f"session implement {STORY}")


def test_ac6_the_next_story_after_a_finished_cycle_comes_from_a_new_scheduler_call(d):
    d.add_story(STORY, "Ready for implementation")
    d.add_pr(PR, STORY)
    d.add_story(OTHER, "Ready for implementation")
    d.add_pr(PR + 1, OTHER)
    d.schedule(("implement", STORY), ("implement", OTHER), ("idle",))

    proc = scheduled(d)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {STORY}", f"implement {OTHER}"]
    assert len(d.scheduler_calls) == 3
    # The second call sees the first story in flight ("In review" after the hand-off).
    assert d.scheduler_calls[1].endswith(f"--in-implementation {STORY}")


def test_ac6_the_board_order_does_not_choose_the_next_story(d):
    d.add_story(OTHER, "Ready for implementation")  # first on the board
    d.add_story(STORY, "Ready for implementation")
    d.add_pr(PR, STORY)
    d.schedule(("implement", STORY), ("idle",))

    proc = scheduled(d)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {STORY}"]


def test_ac6_a_story_this_run_already_started_is_not_started_again(d):
    d.add_story(STORY, "Ready for implementation")
    d.add_pr(PR, STORY)
    d.schedule(("implement", STORY), ("implement", STORY))

    proc = scheduled(d)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {STORY}"]
    assert f"Scheduler: implement #{STORY}, which this run already started or skipped" in proc.stdout


def test_ac6_a_non_executable_choice_asks_the_scheduler_again(d):
    d.add_story(STORY, "Ready for implementation", labels=["type:epic"])
    d.add_story(OTHER, "Ready for implementation")
    d.add_pr(PR, OTHER)
    d.schedule(("implement", STORY), ("implement", OTHER), ("idle",))

    proc = scheduled(d)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"implement {OTHER}"]
    assert len(d.scheduler_calls) == 3


# ── AC7: the gates, for both kinds ───────────────────────────────────────────


@pytest.mark.parametrize("kind", ["refine", "implement"])
def test_ac7_below_the_reserve_no_session_starts(d, kind):
    d.add_story(STORY, "Ready for implementation")
    d.schedule((kind, STORY))

    proc = scheduled(d, seq="1")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.claude_calls == [] and d.sessions == []


@pytest.mark.parametrize("kind", ["refine", "implement"])
def test_ac7_at_the_usage_limit_no_session_starts(d, kind):
    d.add_story(STORY, "Ready for implementation")
    d.schedule((kind, STORY))
    (d.mock / "usage" / "sessions").write_text(f"{_today()}:1\n", encoding="utf-8", newline="\n")

    proc = scheduled(d, env={"USAGE_LIMIT_SESSIONS_PER_DAY": "1", "USAGE_LIMIT_TODAY": _today()})

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.claude_calls == [] and d.sessions == []
    assert "[USAGE LIMIT SKIP]" in proc.stdout


def _today() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _step(name: str) -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    match = re.search(rf"^      - name: {re.escape(name)}\n(.*?)(?=^      - name: |\Z)", text, re.MULTILINE | re.DOTALL)
    assert match, name
    return match.group(1)


def test_ac7_with_the_dispatcher_disabled_neither_the_poll_nor_the_invoke_step_runs():
    for name in ("Run dispatcher poll", "Run dispatcher invoke", "Install Claude Code"):
        condition = re.search(r"^        if: (.+)$", _step(name), re.MULTILINE).group(1)
        assert condition.startswith("steps.guard.outputs.enabled == 'true'"), (name, condition)


# ── AC8: no merged spec ──────────────────────────────────────────────────────


def test_ac8_no_merged_spec_is_in_the_run_log_and_no_comment_is_posted(d):
    d.add_story(STORY, "Ready for refinement")
    d.schedule_from_facts({
        "result": "read",
        "epics": [{"number": 306, "spec": {"state": "missing", "reason": "no specs/306-*/spec.md on main"}}],
        "issues": [{"number": STORY, "type": "story", "epic": 306, "refined": False, "reasons": []}],
    })

    proc = scheduled(d)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[SCHEDULER] skip #306 no-merged-spec: no specs/306-*/spec.md on main" in proc.stderr
    assert "[SCHEDULER] idle" in proc.stderr
    assert d.sessions == []
    assert d.posted(306) == [] and d.posted(STORY) == []
    assert not [line for line in (d.mock / "gh.log").read_text(encoding="utf-8").splitlines() if "comments" in line
                and "POST" in line]


# ── AC10: the workflow condition and the invoke step's arguments ─────────────


def test_ac10_the_invoke_step_condition_keeps_its_meaning():
    condition = re.search(r"^        if: (.+)$", _step("Run dispatcher invoke"), re.MULTILINE).group(1)
    assert condition == ("steps.guard.outputs.enabled == 'true' && (steps.poll.outputs.issue_number != '' "
                         "|| steps.poll.outputs.resumable == 'true' || inputs.refine_issue != '')")
    assert "NEXT_ISSUE" not in _step("Run dispatcher invoke")


# ── AC11: the docs ────────────────────────────────────────────────────────────

DOCS = REPO_ROOT / "docs" / "DISPATCHER-CONFIG.md"


def _doc_section(title: str) -> str:
    text = DOCS.read_text(encoding="utf-8")
    match = re.search(rf"^## {re.escape(title)}\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    assert match, title
    return match.group(1)


def test_ac11_dispatcher_config_has_a_scheduler_section():
    section = _doc_section("Scheduler: new work from GitHub facts")
    for words in ("hpw dispatcher scheduler next", "Order of actions in a run", "Ready buffer", "READY_BUFFER_MIN",
                  "READY_BUFFER_MAX", "WIP limit", "WIP_LIMIT_IMPLEMENTATION", "File overlap", "No-spec rule",
                  "no-merged-spec", "`idle`", "`unreadable`", "next_action", "scheduler_result"):
        assert words in section, words


def test_ac11_dispatcher_config_no_longer_describes_the_refinement_notification():
    text = DOCS.read_text(encoding="utf-8")
    assert "### Ready for refinement notification state" not in text
    assert "| `ready_for_refinement` |" not in text
    assert "| `notified-refinement` |" not in text
    assert "the #306 scheduler will use it" not in text
