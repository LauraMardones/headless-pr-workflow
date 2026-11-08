"""Scheduler rule: ``hpw dispatcher scheduler next`` (issue #473, ADR-010, ADR-014).

``choose_next`` is tested over hand-built facts: no gateway at all. The command is tested
through the fake gateway of tests/test_dispatcher_facts_block.py, which fails on any write.
"""

from __future__ import annotations

import inspect
import json

import pytest

from headless_pr_workflow.dispatcher import cli
from headless_pr_workflow.dispatcher.gateway import GatewayError
from headless_pr_workflow.dispatcher.scheduler import InvalidLimits, Limits, choose_next, load_limits, next_action

from tests.test_dispatcher_facts_block import PO, TOKEN, FakeGateway, spec_line, story_body, summary

SPEC_AT = "2026-11-01T00:00:00Z"
BEFORE = "2026-10-01T00:00:00Z"
AFTER = "2026-11-02T00:00:00Z"
LATER = "2026-11-03T00:00:00Z"
LIMITS = Limits(wip=2, buffer_min=2, buffer_max=4)
ENV = {"WIP_LIMIT_IMPLEMENTATION": "2", "READY_BUFFER_MIN": "2", "READY_BUFFER_MAX": "4"}


def passed(at=AFTER):
    return {"created_at": at, "refine_check": "pass", "url": None}


def failed(at=AFTER):
    return {"created_at": at, "refine_check": "fail", "url": None}


NONE = {"created_at": None, "refine_check": "none", "url": None}
NO_BLOCKER = {"state": "none", "created_at": None, "unblocked_at": None, "url": None}
OPEN = {"state": "open", "created_at": AFTER, "unblocked_at": None, "url": None}
UNBLOCKED = {"state": "unblocked", "created_at": AFTER, "unblocked_at": LATER, "url": None}


def epic(number=10, *, prio="prio:P1", spec="merged", committed=SPEC_AT, record="current", refinement=None,
         blocker=None):  # fmt: skip
    merged = spec == "merged"
    return {
        "number": number, "title": f"E{number}", "prio": prio,
        "spec": {"state": spec, "path": f"specs/{number}-x/spec.md" if merged else None, "commit": "a" * 40 if merged else None,
                 "committed_at": committed if merged else None,
                 "reason": None if merged else f"no specs/{number}-*/spec.md on main"},
        "record": {"state": record, "path": None, "commit": None},
        "stale": merged and record in ("stale", "invalid"),
        "refinement": passed() if refinement is None else refinement,
        "decision_blocker": NO_BLOCKER if blocker is None else blocker,
    }  # fmt: skip


def issue(number, *, kind="story", parent=10, feature=20, prio=None, refined=True, ready=None, stale=False,
          prefix=None, files=(), refinement=None, blocker=None, reasons=None):  # fmt: skip
    ready = (refined and kind != "feature") if ready is None else ready
    if refinement is None:
        refinement = passed() if refined or stale else NONE
    return {
        "number": number, "title": f"T{number}", "type": kind, "prio": prio, "epic": parent,
        "feature": feature if kind != "feature" else None, "criterion_prefix": prefix if kind == "feature" else None,
        "record": {"state": "stale" if stale else "current", "path": None, "commit": None},
        "refinement": refinement, "decision_blocker": NO_BLOCKER if blocker is None else blocker,
        "dependencies": None, "files_affected": None if files is None else list(files),
        "refined": refined, "ready": ready, "stale": stale, "reasons": [] if reasons is None else reasons,
    }  # fmt: skip


def feature(number=20, **kw):
    kw.setdefault("refined", True)
    return issue(number, kind="feature", **kw)


def facts(epics, issues):
    return {"ok": True, "block": "facts", "operation": "read", "result": "read", "epics": epics, "issues": issues,
            "error": None}  # fmt: skip


def decide(epics, issues, active=(), limits=LIMITS):
    return choose_next(facts(epics, issues), in_implementation=list(active), limits=limits)


def chosen(decision):
    a = decision.action
    return None if a is None else (a["kind"], a["issue"], a["step"], a["reason"])


def skips(decision):
    return {s["issue"]: s["reason"] for s in decision.skipped}


# ── AC 2: limits ──────────────────────────────────────────────────────────────


def test_limits_read_like_limits_sh():
    assert load_limits({"WIP_LIMIT_IMPLEMENTATION": "08", "READY_BUFFER_MIN": "0", "READY_BUFFER_MAX": "0"}) == Limits(8, 0, 0)


@pytest.mark.parametrize("name, value, text", [
    ("WIP_LIMIT_IMPLEMENTATION", None, "WIP_LIMIT_IMPLEMENTATION is not set or is empty (value: unset)"),
    ("READY_BUFFER_MIN", "", 'READY_BUFFER_MIN is not set or is empty (value: "")'),
    ("READY_BUFFER_MAX", "x3", 'READY_BUFFER_MAX is not a whole number (value: "x3")'),
    ("READY_BUFFER_MAX", "-1", 'READY_BUFFER_MAX is not a whole number (value: "-1")'),
    ("READY_BUFFER_MIN", " 2", 'READY_BUFFER_MIN is not a whole number (value: " 2")'),
    ("WIP_LIMIT_IMPLEMENTATION", "0", 'WIP_LIMIT_IMPLEMENTATION must be 1 or more (value: "0")'),
    ("WIP_LIMIT_IMPLEMENTATION", "00", 'WIP_LIMIT_IMPLEMENTATION must be 1 or more (value: "00")'),
    ("READY_BUFFER_MIN", "5", "READY_BUFFER_MIN (5) is greater than READY_BUFFER_MAX (4)"),
])  # fmt: skip
def test_invalid_limits_name_the_variable_and_value(name, value, text):
    env = dict(ENV)
    if value is None:
        env.pop(name)
    else:
        env[name] = value
    with pytest.raises(InvalidLimits) as error:
        load_limits(env)
    assert text in str(error.value)


# ── AC 3: order of steps ──────────────────────────────────────────────────────


def test_re_refinement_before_epic_refinement():
    d = decide([epic(10, record="missing"), epic(11, record="current")], [feature(21, parent=11, stale=True, refined=False)])
    assert chosen(d) == ("refine", 21, 2, "stale-record")


def test_epic_refinement_before_feature_refinement():
    d = decide([epic(10, record="missing"), epic(11)], [feature(21, parent=11, refined=False)])
    assert chosen(d) == ("refine", 10, 3, "new-spec") and d.action["type"] == "epic" and d.action["epic"] == 10


def test_feature_refinement_before_implementation_start():
    d = decide([epic()], [feature(20), feature(21, refined=False), issue(30)])
    assert chosen(d) == ("refine", 21, 4, "not-refined")


def test_implementation_start_before_story_refinement():
    d = decide([epic()], [feature(20), issue(30), issue(31, refined=False)])
    assert chosen(d) == ("implement", 30, 5, "ready")
    assert d.action == {"kind": "implement", "issue": 30, "type": "story", "epic": 10, "step": 5, "reason": "ready"}


def test_story_refinement_when_nothing_else():
    d = decide([epic()], [feature(20), issue(31, refined=False)])
    assert chosen(d) == ("refine", 31, 6, "not-refined") and d.result == "refine"


def test_idle():
    d = decide([epic()], [feature(20)])
    assert d.action is None and d.result == "idle"


def test_within_step_2_epic_then_feature_then_story_in_implementation_then_resume():
    epics_ = [epic(10), epic(11, record="stale")]
    items = [feature(20), feature(21, stale=True, refined=False), issue(30, stale=True, refined=False),
             issue(31, refined=False, refinement=passed(AFTER), blocker=UNBLOCKED)]  # fmt: skip
    assert chosen(decide(epics_, items, active=[30])) == ("refine", 11, 2, "scope-change")
    assert chosen(decide(epics_[:1], items, active=[30])) == ("refine", 21, 2, "stale-record")
    items[1] = feature(21)
    assert chosen(decide(epics_[:1], items, active=[30])) == ("refine", 30, 2, "stale-record")
    assert chosen(decide(epics_[:1], items)) == ("refine", 31, 2, "unblocked")


# ── AC 4: Epic refinement after a spec merge ──────────────────────────────────


def test_new_spec_epic_is_step_3():
    d = decide([epic(record="missing", refinement=NONE)], [])
    assert chosen(d) == ("refine", 10, 3, "new-spec")


@pytest.mark.parametrize("record", ["stale", "invalid"])
def test_changed_spec_epic_is_step_2(record):
    assert chosen(decide([epic(record=record)], [])) == ("refine", 10, 2, "scope-change")


def test_current_epic_is_not_chosen():
    assert decide([epic()], []).action is None


@pytest.mark.parametrize("record", ["missing", "stale", "invalid", "unreadable"])
def test_features_and_stories_wait_for_the_epic_record(record):
    e = epic(record=record, blocker=OPEN)  # the Epic itself cannot run either
    if record == "unreadable":
        e["record"]["reason"] = "unreadable: compare a with b"
    d = decide([e], [feature(20, refined=False), feature(21), issue(30, feature=21, refined=False)])
    assert d.action is None
    assert skips(d)[20] == skips(d)[30] == "epic-not-refined"
    assert skips(d)[10] == ("unreadable: compare a with b" if record == "unreadable" else "open-decision-blocker")


# ── AC 5: no refinement without a merged spec ─────────────────────────────────


@pytest.mark.parametrize("state", ["missing", "ambiguous"])
def test_no_merged_spec_refines_nothing_but_implements(state):
    e = epic(spec=state, record="unchecked")
    items = [feature(20, refined=False), issue(30, feature=20, refined=False), issue(31, feature=20)]
    d = decide([e], items)
    assert chosen(d) == ("implement", 31, 5, "ready")
    assert d.skipped == [{"issue": 10, "reason": "no-merged-spec: no specs/10-*/spec.md on main"}]
    d = decide([e], items[:2])
    assert d.action is None and [s["issue"] for s in d.skipped] == [10]
    assert "specs/10-*/spec.md" in d.skipped[0]["reason"]


# ── AC 6: Feature before story ────────────────────────────────────────────────


def test_story_waits_for_its_feature():
    d = decide([epic()], [feature(20, refined=False, blocker=OPEN), issue(30, refined=False)])
    assert d.action is None and skips(d) == {20: "open-decision-blocker", 30: "feature-not-refined #20"}


def test_story_waits_for_a_stale_feature_and_for_a_missing_feature():
    d = decide([epic()], [feature(20, stale=True, refined=False, refinement=failed()), issue(30, refined=False),
                          issue(31, feature=99, refined=False)])  # fmt: skip
    assert skips(d) == {20: "last-refinement-failed", 30: "feature-not-refined #20", 31: "feature-not-refined #99"}


def test_story_without_feature_group():
    d = decide([epic()], [issue(30, feature=None, refined=False)])
    assert d.action is None and skips(d) == {30: "no-feature"}


# ── AC 7: ready buffer ────────────────────────────────────────────────────────


def buffer_items(ready, waiting, parents=(10,)):
    items, n = [feature(20, parent=parents[0])], 100
    for i in range(ready):
        items.append(issue(n, parent=parents[i % len(parents)]))
        n += 1
    for i in range(waiting):
        items.append(issue(n, parent=parents[i % len(parents)], ready=False))
        n += 1
    items.append(issue(30, refined=False))
    return items


def test_buffer_counts_skip_implementation_and_stories_in_implementation():
    items = buffer_items(1, 1) + [issue(40), issue(41, ready=False), feature(21)]
    d = decide([epic()], items, active=[40, 41], limits=Limits(2, 3, 9))
    assert d.counts == {"ready": 1, "waiting": 1, "in_implementation": 2}
    assert skips(d)[100] == "wip-limit" and chosen(d) == ("refine", 30, 6, "not-refined")


def test_buffer_ready_equal_to_min_refines_nothing():
    d = decide([epic()], buffer_items(2, 0), active=[1, 2], limits=Limits(2, 2, 9))
    assert d.action is None and skips(d)[30] == "buffer-full"


def test_buffer_one_below_min_and_one_below_max_refines_one():
    d = decide([epic()], buffer_items(1, 2), active=[1, 2], limits=Limits(2, 2, 4))
    assert chosen(d) == ("refine", 30, 6, "not-refined")


def test_buffer_ready_plus_waiting_equal_to_max_refines_nothing():
    d = decide([epic()], buffer_items(1, 3), active=[1, 2], limits=Limits(2, 2, 4))
    assert d.action is None and skips(d)[30] == "buffer-full"


def test_buffer_counts_stories_of_two_epics_together():
    items = buffer_items(2, 0, parents=(10, 11)) + [feature(21, parent=11)]
    d = decide([epic(10), epic(11)], items, active=[1, 2], limits=Limits(2, 2, 9))
    assert d.counts["ready"] == 2 and d.action is None and skips(d)[30] == "buffer-full"


# ── AC 8: re-refinement after a scope change ──────────────────────────────────


def test_stale_feature_is_step_2():
    assert chosen(decide([epic()], [feature(20, stale=True, refined=False)])) == ("refine", 20, 2, "stale-record")


def test_stale_story_in_implementation_is_step_2_even_with_a_full_buffer():
    d = decide([epic()], [feature(20), issue(30, stale=True, refined=False)], active=[30], limits=Limits(2, 0, 0))
    assert chosen(d) == ("refine", 30, 2, "stale-record")


def test_other_stale_story_is_step_6_before_never_refined_and_within_the_buffer():
    items = [feature(20), issue(31, refined=False), issue(32, stale=True, refined=False)]
    assert chosen(decide([epic()], items, active=[1, 2])) == ("refine", 32, 6, "stale-record")
    d = decide([epic()], items, active=[1, 2], limits=Limits(2, 0, 0))
    assert d.action is None and skips(d) == {31: "buffer-full", 32: "buffer-full"}


def test_stale_story_is_never_implemented():
    d = decide([epic()], [feature(20), issue(30, stale=True, refined=False, ready=True)], limits=Limits(2, 0, 0))
    assert d.action is None


def test_story_in_implementation_that_is_not_stale_is_left_to_step_1():
    d = decide([epic()], [feature(20), issue(30, refined=False, blocker=UNBLOCKED)], active=[30])
    assert d.action is None and d.skipped == []


# ── AC 9: resume after /unblock ───────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["epic", "feature", "story"])
def test_unblocked_is_step_2_also_with_a_full_buffer(kind):
    e = epic(blocker=UNBLOCKED, refinement=failed()) if kind == "epic" else epic()
    items = [feature(20, refined=kind != "feature", blocker=UNBLOCKED if kind == "feature" else None)]
    if kind == "story":
        items.append(issue(30, refined=False, refinement=passed(AFTER), blocker=UNBLOCKED))
    d = decide([e], items, limits=Limits(2, 0, 0))
    assert chosen(d) == ("refine", {"epic": 10, "feature": 20, "story": 30}[kind], 2, "unblocked")


def test_refinement_after_unblock_is_not_restarted():
    d = decide([epic()], [feature(20, refined=False, refinement=failed("2026-11-04T00:00:00Z"), blocker=UNBLOCKED)])
    assert d.action is None and skips(d) == {20: "last-refinement-failed"}


def test_open_decision_blocker_is_not_chosen():
    d = decide([epic(record="missing", blocker=OPEN)], [])
    assert d.action is None and skips(d) == {10: "open-decision-blocker"}


# ── AC 10: each refinement is started once ────────────────────────────────────


@pytest.mark.parametrize("make", [
    lambda r: ([epic(record="missing", refinement=r)], []),
    lambda r: ([epic()], [feature(20, refined=False, refinement=r)]),
    lambda r: ([epic()], [feature(21), issue(30, feature=21, refined=False, refinement=r)]),
])  # fmt: skip
def test_failed_refinement_after_the_spec_commit_is_not_restarted(make):
    for check in ("fail", "unreadable"):
        d = decide(*make({"created_at": AFTER, "refine_check": check, "url": None}), active=[1, 2])
        assert d.action is None and "last-refinement-failed" in skips(d).values()
    d = decide(*make(failed(BEFORE)), active=[1, 2])  # a newer spec commit: refine again
    assert d.action is not None and d.action["kind"] == "refine"


# ── AC 11: implementation start ───────────────────────────────────────────────


def test_wip_limit():
    d = decide([epic()], [feature(20), issue(30), issue(40), issue(41)], active=[40, 41])
    assert d.action is None and skips(d) == {30: "wip-limit"}


def test_file_overlap_tries_the_next_candidate():
    items = [feature(20), issue(30, files=["a.py", "b.py"]), issue(31, files=["c.py"]),
             issue(40, ready=False, files=["x.py", "b.py"])]  # fmt: skip
    d = decide([epic()], items, active=[40])
    assert chosen(d) == ("implement", 31, 5, "ready") and skips(d) == {30: "file-overlap #40 b.py"}
    assert d.notes == []


@pytest.mark.parametrize("mine, theirs, missing", [(None, ["a.py"], 30), (["a.py"], None, 40)])
def test_missing_files_section_means_no_overlap_check_with_a_note(mine, theirs, missing):
    d = decide([epic()], [feature(20), issue(30, files=mine), issue(40, ready=False, files=theirs)], active=[40])
    assert chosen(d) == ("implement", 30, 5, "ready") and d.notes == [f"files-affected-missing #{missing}"]


def test_ready_story_not_in_implementation_only():
    d = decide([epic()], [feature(20), issue(30)], active=[30])
    assert d.action is None and d.counts == {"ready": 0, "waiting": 0, "in_implementation": 1}


# ── AC 12: order within a step ────────────────────────────────────────────────


def test_tie_break_epic_prio_label():
    d = decide([epic(10, prio="prio:P2", record="missing"), epic(11, prio=None, record="missing"),
                epic(12, prio="prio:P0", record="missing")], [])  # fmt: skip
    assert chosen(d) == ("refine", 12, 3, "new-spec")


def test_tie_break_earlier_spec_merge():
    d = decide([epic(10, record="missing", committed="2026-11-05T00:00:00Z"),
                epic(11, record="missing", committed="2026-11-04T00:00:00Z")], [])  # fmt: skip
    assert chosen(d) == ("refine", 11, 3, "new-spec")


def test_tie_break_epic_without_merged_spec_last():
    items = [issue(30, parent=10, feature=20), issue(31, parent=11, feature=21)]
    d = decide([epic(10, spec="missing", record="unchecked"), epic(11)], items)
    assert chosen(d) == ("implement", 31, 5, "ready")


def test_tie_break_issue_prio_label():
    items = [issue(30, prio=None), issue(31, prio="prio:P2"), issue(32, prio="prio:P1")]
    assert chosen(decide([epic()], [feature(20)] + items)) == ("implement", 32, 5, "ready")


def test_tie_break_feature_criterion_prefix():
    items = [feature(20, prefix="F10"), feature(21, prefix="F2"), feature(22, prefix=None),
             issue(30, feature=22), issue(31, feature=20), issue(32, feature=21)]  # fmt: skip
    assert chosen(decide([epic()], items)) == ("implement", 32, 5, "ready")
    features = [feature(23, prefix=None, refined=False), feature(24, prefix="F3", refined=False)]
    assert chosen(decide([epic()], features)) == ("refine", 24, 4, "not-refined")


def test_tie_break_lower_issue_number():
    assert chosen(decide([epic()], [feature(20), issue(32), issue(31)])) == ("implement", 31, 5, "ready")


def test_epic_prio_before_issue_prio():
    items = [feature(20, parent=10), feature(21, parent=11), issue(30, parent=10, prio="prio:P0"),
             issue(31, parent=11, feature=21, prio="prio:P2")]  # fmt: skip
    d = decide([epic(10, prio="prio:P2"), epic(11, prio="prio:P0")], items)
    assert chosen(d) == ("implement", 31, 5, "ready")


# ── unreadable issues ─────────────────────────────────────────────────────────


def test_unreadable_issue_and_epic_comments_are_skipped():
    e = epic(record="missing")
    e["refinement"] = None
    d = decide([e, epic(11)], [feature(21, parent=11, refined=False, reasons=["unreadable: read comments on #21"])])
    assert d.action is None
    assert skips(d) == {10: "unreadable: comments", 21: "unreadable: read comments on #21"}


# ── AC 1: the command ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("DECISION_BLOCKER_PO_LOGIN", PO)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def gw():
    g = FakeGateway()
    g.add_issue(10, ["type:epic", "prio:P1"], spec_line())
    g.comment(10, summary())
    g.add_issue(20, ["type:feature"], story_body().replace("Feature group:", "X:") + "\nCriterion prefix: F1\n")
    g.comment(20, summary())
    return g


def run(capsys, gateway, *argv):
    rc = cli.main(["scheduler", "next", "--repo", "o/r", *argv], gateway_factory=lambda repo: gateway)
    out, err = capsys.readouterr()
    lines = out.splitlines()
    assert len(lines) == 1, out
    assert not any(c.startswith("WRITE") for c in gateway.calls)
    return rc, json.loads(lines[0]), err


KEYS = ["ok", "block", "operation", "result", "action", "skipped", "notes", "counts", "limits", "error"]


def test_command_implement_exit_0(gw, capsys):
    gw.add_issue(30, ["type:story"], story_body() + "\n## Files affected\n\n- `a.py`\n")
    gw.comment(30, summary())
    rc, out, err = run(capsys, gw)
    assert rc == 0 and list(out) == KEYS and out["ok"] is True
    assert (out["block"], out["operation"], out["result"]) == ("scheduler", "next", "implement")
    assert out["action"] == {"kind": "implement", "issue": 30, "type": "story", "epic": 10, "step": 5, "reason": "ready"}
    assert out["limits"] == {"WIP_LIMIT_IMPLEMENTATION": 2, "READY_BUFFER_MIN": 2, "READY_BUFFER_MAX": 4}
    assert out["counts"] == {"ready": 1, "waiting": 0, "in_implementation": 0} and out["error"] is None
    assert "[SCHEDULER] implement #30 step=5 reason=ready" in err
    assert "list_open_issues" in gw.calls


def test_command_refine_exit_0_with_skips_on_stderr(gw, capsys):
    gw.add_issue(30, ["type:story"], story_body())
    gw.add_issue(31, ["type:story"], story_body(feature=99))
    rc, out, err = run(capsys, gw)
    assert rc == 0 and out["result"] == "refine" and out["action"]["issue"] == 30
    assert out["skipped"] == [{"issue": 31, "reason": "feature-not-refined #99"}]
    assert "[SCHEDULER] refine #30 step=6 reason=not-refined" in err and "[SCHEDULER] skip #31 feature-not-refined #99" in err


def test_command_idle_exit_1(gw, capsys):
    rc, out, err = run(capsys, gw)
    assert rc == 1 and out["ok"] is False and out["result"] == "idle" and out["action"] is None
    assert "[SCHEDULER] idle" in err


def test_command_in_implementation_and_dry_run(gw, capsys):
    gw.add_issue(30, ["type:story"], story_body())
    gw.comment(30, summary())
    rc, out, _ = run(capsys, gw, "--in-implementation", "30,31", "--dry-run")
    assert rc == 1 and out["counts"]["in_implementation"] == 2
    rc2, out2, _ = run(capsys, gw, "--in-implementation", "30,31")
    assert (rc, out) == (rc2, out2)
    rc3, out3, _ = run(capsys, gw, "--in-implementation", "")
    assert rc3 == 0 and out3["counts"]["in_implementation"] == 0


@pytest.mark.parametrize("argv", [[], ["--repo", "bad"], ["--repo", "o/r", "--in-implementation", "x"],
                                  ["--repo", "o/r", "--in-implementation", "0"],
                                  ["--repo", "o/r", "--in-implementation", "3,,4"], ["--repo", "o/r", "--bogus"]])
def test_invalid_arguments_exit_2(capsys, argv):
    gw = FakeGateway()
    rc = cli.main(["scheduler", "next", *argv], gateway_factory=lambda repo: gw)
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["result"] == "invalid" and out["block"] == "scheduler" and out["operation"] == "next"
    assert gw.calls == []


def test_missing_operation_is_invalid(capsys):
    assert cli.main(["scheduler"], gateway_factory=lambda repo: FakeGateway()) == 2


@pytest.mark.parametrize("name, value", [("WIP_LIMIT_IMPLEMENTATION", ""), ("READY_BUFFER_MAX", "many")])
def test_command_invalid_limits_exit_3_without_reading(gw, capsys, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    rc, out, err = run(capsys, gw)
    assert rc == 3 and out["result"] == "unreadable" and out["action"] is None and out["limits"] is None
    assert name in out["error"] and (f'"{value}"' in out["error"]) and gw.calls == []
    assert "[SCHEDULER] unreadable: limits:" in err


def test_command_unreadable_facts_exit_3(gw, capsys):
    gw.fail.add("issues")
    rc, out, _ = run(capsys, gw)
    assert rc == 3 and out["result"] == "unreadable" and out["action"] is None and "read open issues" in out["error"]
    assert out["limits"]["WIP_LIMIT_IMPLEMENTATION"] == 2


def test_facts_file_read(gw, capsys, tmp_path):
    gw.add_issue(30, ["type:story"], story_body())
    gw.comment(30, summary())
    cli.main(["facts", "read", "--repo", "o/r"], gateway_factory=lambda repo: gw)
    path = tmp_path / "facts.json"
    path.write_text(capsys.readouterr().out, encoding="utf-8")
    unused = FakeGateway()
    rc, out, _ = run(capsys, unused, "--facts-file", str(path))
    assert rc == 0 and out["action"]["issue"] == 30 and unused.calls == []


@pytest.mark.parametrize("text", [
    json.dumps({"ok": False, "block": "facts", "operation": "read", "result": "unreadable", "epics": [], "issues": [],
                "error": "read open issues: HTTP 500"}),
    "not json", "[]", json.dumps({"result": "read", "epics": [], "issues": [{"title": "no number"}]}),
])  # fmt: skip
def test_facts_file_not_read_exit_3(capsys, tmp_path, text):
    path = tmp_path / "facts.json"
    path.write_text(text, encoding="utf-8")
    gw = FakeGateway()
    rc, out, _ = run(capsys, gw, "--facts-file", str(path))
    assert rc == 3 and out["result"] == "unreadable" and out["error"].startswith("facts:") and gw.calls == []


def test_facts_file_missing_exit_3(capsys, tmp_path):
    rc, out, _ = run(capsys, FakeGateway(), "--facts-file", str(tmp_path / "nope.json"))
    assert rc == 3 and "read facts file" in out["error"]


def test_next_action_in_process_matches_the_command(gw, capsys):
    rc, out, _ = run(capsys, gw)
    assert next_action(gw, "o/r", in_implementation=[]).to_dict() == out


def test_choose_next_takes_no_gateway():
    assert list(inspect.signature(choose_next).parameters) == ["facts", "in_implementation", "limits", "exclude"]


def test_no_token_in_output(gw, capsys, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    gw.fail.add("issues")
    gw.list_open_issues = lambda: (_ for _ in ()).throw(GatewayError("read open issues", f"token {TOKEN}"))
    rc = cli.main(["scheduler", "next", "--repo", "o/r"], gateway_factory=lambda repo: gw)
    out, err = capsys.readouterr()
    assert rc == 3 and TOKEN not in out + err
