"""Facts reader: ``hpw dispatcher facts read`` (issue #283, ADR-010, ADR-014).

Every path runs against a Python fake gateway that records its calls: no network, no real
``gh``, and the reader must never call a write. The gateway read operations are covered
against a Python stand-in for ``gh`` at the end.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from headless_pr_workflow.dispatcher import cli
from headless_pr_workflow.dispatcher.facts import SPEC_LINE, hard_dependencies, parse_sections, read_facts
from headless_pr_workflow.dispatcher.gateway import GatewayError, GhGateway

PO = "LauraMardones"
TOKEN = "ghp_secretTOKENvalue123"
SPEC_PATH = "specs/10-epic/spec.md"
SPEC_SHA = "a" * 40
RECORD_OK = "b" * 40
ROOT = Path(__file__).resolve().parents[1]


def spec_line(path=SPEC_PATH, sha=RECORD_OK[:7]):
    return f"Spec: [`{path}`](https://github.com/o/r/blob/{sha}/{path}) at `{sha}`"


def story_body(*, hard="- None.", parent=10, spec=None, feature=20, dependencies=True):
    spec = spec_line() if spec is None else spec
    text = f"Parent epic: #{parent} Epic: x\nFeature group: #{feature} Feature: y\n{spec}\n\n## Goal\n\nx\n"
    if dependencies:
        text += f"\n## Dependencies\n\nHard depends on:\n\n{hard}\n\nSoft depends on:\n\n- #99\n"
    return text


def summary(result="pass", command="refine"):
    return f"## Session Summary\nCommand: {command}\nIssue / PR: #1\nChecks run: refine-check — {result}\nBlockers: none"


BLOCKER = "## Blocked Declaration\nType: decision\nQuestion: x"


class FakeGateway:
    def __init__(self) -> None:
        self.issues: list[dict] = []
        self.comments: dict[int, list[dict]] = {}
        self.states: dict[int, str] = {}
        self.dirs: list[str] | None = ["10-epic"]
        self.commits: dict[str, dict | None] = {SPEC_PATH: {"sha": SPEC_SHA, "date": "2026-11-01T00:00:00Z"}}
        self.compares: dict[str, str] = {RECORD_OK[:7]: "identical"}
        self.fail: set[str] = set()
        self.calls: list[str] = []
        self.clock = 0

    def add_issue(self, number, labels, body="", title=None, **extra):
        issue = {"number": number, "title": title or f"T{number}", "body": body,
                 "labels": [{"name": n} for n in labels]}  # fmt: skip
        issue.update(extra)
        self.issues.append(issue)
        return issue

    def comment(self, number, body, login=PO, assoc="NONE", kind="User"):
        self.clock += 1
        self.comments.setdefault(number, []).append({
            "user": {"login": login, "type": kind}, "author_association": assoc, "body": body,
            "created_at": f"2026-11-02T10:00:{self.clock:02d}Z", "html_url": f"https://x/{number}#{self.clock}"})  # fmt: skip

    # reads
    def list_open_issues(self):
        self.calls.append("list_open_issues")
        if "issues" in self.fail:
            raise GatewayError("read open issues", "HTTP 500")
        return self.issues

    def list_comments(self, number):
        self.calls.append(f"comments {number}")
        if f"comments {number}" in self.fail:
            raise GatewayError(f"read comments on #{number}", "HTTP 500")
        return list(self.comments.get(number, []))

    def issue_state(self, number):
        self.calls.append(f"state {number}")
        if f"state {number}" in self.fail:
            raise GatewayError(f"read state of #{number}", "HTTP 500")
        if number not in self.states:
            raise GatewayError(f"read state of #{number}", "HTTP 404", not_found=True)
        return self.states[number]

    def list_dir(self, path):
        self.calls.append(f"list_dir {path}")
        if "specs" in self.fail:
            raise GatewayError("list specs on main", "HTTP 500")
        if self.dirs is None:
            raise GatewayError("list specs on main", "Not Found (HTTP 404)", not_found=True)
        return [{"name": n, "type": "dir"} for n in self.dirs] + [{"name": "README.md", "type": "file"}]

    def newest_commit(self, path):
        self.calls.append(f"commit {path}")
        if "history" in self.fail:
            raise GatewayError(f"read newest commit of {path}", "HTTP 500")
        return self.commits.get(path)

    def compare_commits(self, base, head):
        self.calls.append(f"compare {base[:7]} {head}")
        if head in self.fail:
            raise GatewayError(f"compare {base} with {head}", "HTTP 500")
        if head not in self.compares:
            raise GatewayError(f"compare {base} with {head}", "Not Found (HTTP 404)", not_found=True)
        return self.compares[head]

    # any write the reader might try
    def __getattr__(self, name):
        if name.startswith(("post_", "set_", "add_label", "remove_", "create_")):
            def forbidden(*a, **k):
                self.calls.append(f"WRITE {name}")
                raise AssertionError(f"facts read called {name}")
            return forbidden
        raise AttributeError(name)


@pytest.fixture
def gw():
    g = FakeGateway()
    g.add_issue(10, ["type:epic", "prio:P1"], spec_line())
    return g


@pytest.fixture(autouse=True)
def _po(monkeypatch):
    monkeypatch.setenv("DECISION_BLOCKER_PO_LOGIN", PO)
    monkeypatch.delenv("GH_TOKEN", raising=False)


def run(capsys, gateway, *argv):
    rc = cli.main(["facts", "read", "--repo", "o/r", *argv], gateway_factory=lambda repo: gateway)
    out, err = capsys.readouterr()
    lines = out.splitlines()
    assert len(lines) == 1, out
    assert not any(c.startswith("WRITE") for c in gateway.calls)
    return rc, json.loads(lines[0]), err


def story(gw, number=30, *, summary_text=summary(), body=None, labels=("type:story", "prio:P2")):
    gw.add_issue(number, list(labels), story_body() if body is None else body)
    if summary_text:
        gw.comment(number, summary_text)
    return number


def facts_of(out, number):
    return next(i for i in out["issues"] if i["number"] == number)


# ── Epic spec (AC 2) ──────────────────────────────────────────────────────────


def test_epic_spec_merged(gw, capsys):
    rc, out, _ = run(capsys, gw)
    epic = out["epics"][0]
    assert rc == 0 and out["ok"] and out["result"] == "read" and out["error"] is None
    assert (out["block"], out["operation"]) == ("facts", "read")
    assert epic["spec"] == {"state": "merged", "path": SPEC_PATH, "commit": SPEC_SHA,
                            "committed_at": "2026-11-01T00:00:00Z", "reason": None}  # fmt: skip
    assert epic["record"]["state"] == "current" and epic["stale"] is False and epic["prio"] == "prio:P1"


def test_epic_spec_missing_names_the_expected_path(gw, capsys):
    gw.dirs = ["11-other"]
    rc, out, err = run(capsys, gw)
    spec = out["epics"][0]["spec"]
    assert rc == 0 and spec["state"] == "missing" and "specs/10-*/spec.md" in spec["reason"]
    assert "[FACTS] epic #10 spec=missing expected specs/10-*/spec.md" in err


def test_epic_spec_folder_without_spec_file_is_missing_and_prefix_is_exact(gw, capsys):
    gw.dirs = ["10-epic", "100-other"]
    gw.commits = {}  # specs/10-epic has no spec.md
    _, out, _ = run(capsys, gw)
    assert out["epics"][0]["spec"]["state"] == "missing"


def test_epic_spec_ambiguous(gw, capsys):
    gw.dirs = ["10-epic", "10-again"]
    gw.commits["specs/10-again/spec.md"] = {"sha": "c" * 40, "date": "2026-11-03T00:00:00Z"}
    _, out, _ = run(capsys, gw)
    spec = out["epics"][0]["spec"]
    assert spec["state"] == "ambiguous" and "specs/10-*/spec.md" in spec["reason"] and spec["path"] is None


def test_missing_specs_folder_means_no_merged_spec(gw, capsys):
    gw.dirs = None
    rc, out, _ = run(capsys, gw)
    assert rc == 0 and out["epics"][0]["spec"]["state"] == "missing"


# ── spec record (AC 3) ────────────────────────────────────────────────────────


@pytest.mark.parametrize("status, state", [("identical", "current"), ("ahead", "current"), ("behind", "stale"),
                                           ("diverged", "stale")])  # fmt: skip
def test_record_states_from_compare(gw, capsys, status, state):
    gw.compares[RECORD_OK[:7]] = status
    story(gw)
    _, out, _ = run(capsys, gw)
    assert facts_of(out, 30)["record"] == {"state": state, "path": SPEC_PATH, "commit": RECORD_OK[:7]}
    assert facts_of(out, 30)["stale"] is (state == "stale")


def test_record_compare_runs_spec_commit_against_record_commit(gw, capsys):
    story(gw)
    run(capsys, gw)
    assert f"compare {SPEC_SHA[:7]} {RECORD_OK[:7]}" in gw.calls


@pytest.mark.parametrize("spec, state", [
    ("", "missing"),
    ("Spec: see the spec", "invalid"),
    (spec_line("specs/11-x/spec.md"), "invalid"),
    (spec_line(sha="deadbee"), "invalid"),  # GitHub does not know the commit
])  # fmt: skip
def test_record_missing_and_invalid(gw, capsys, spec, state):
    gw.add_issue(30, ["type:story", "prio:P2"], story_body(spec=spec or "no spec line"))
    gw.comment(30, summary())
    _, out, _ = run(capsys, gw)
    entry = facts_of(out, 30)
    assert entry["record"]["state"] == state
    assert entry["stale"] is (state == "invalid") and entry["refined"] is False and f"record-{state}" in entry["reasons"]


def test_record_compare_failure_makes_that_issue_unreadable(gw, capsys):
    gw.fail.add(RECORD_OK[:7])
    story(gw)
    story(gw, 31, body=story_body(spec="Spec: none"))
    rc, out, _ = run(capsys, gw)
    entry = facts_of(out, 30)
    assert rc == 0 and entry["reasons"] == [f"unreadable: compare {SPEC_SHA} with {RECORD_OK[:7]}"]
    assert not (entry["refined"] or entry["ready"] or entry["stale"])
    assert "record-invalid" in facts_of(out, 31)["reasons"]  # the others are still reported


def test_epic_own_record_is_reported(gw, capsys):
    gw.issues[0]["body"] = spec_line(sha="deadbee")
    _, out, _ = run(capsys, gw)
    assert out["epics"][0]["record"]["state"] == "invalid" and out["epics"][0]["stale"] is True


# ── refinement and decision blocker (AC 4, 6) ─────────────────────────────────


def test_summary_pass_fail_and_none(gw, capsys):
    story(gw, 30)
    story(gw, 31, summary_text=summary("fail: spec_record"))
    story(gw, 32, summary_text=None)
    story(gw, 33, summary_text=summary("unreadable"))
    _, out, _ = run(capsys, gw)
    assert [facts_of(out, n)["refinement"]["refine_check"] for n in (30, 31, 32, 33)] == ["pass", "fail", "none", "unreadable"]
    assert facts_of(out, 30)["refined"] and facts_of(out, 30)["refinement"]["created_at"]
    for n in (31, 32, 33):
        assert not facts_of(out, n)["refined"] and "no-passing-summary" in facts_of(out, n)["reasons"]


def test_latest_summary_counts(gw, capsys):
    story(gw, 30, summary_text=summary())
    gw.comment(30, summary("fail: x", command="refine-story"))
    _, out, _ = run(capsys, gw)
    assert facts_of(out, 30)["refinement"]["refine_check"] == "fail"


def test_untrusted_authors_are_ignored(gw, capsys):
    story(gw, 30, summary_text=None)
    gw.comment(30, summary(), login="stranger")
    gw.comment(30, summary(), login="stranger", assoc="CONTRIBUTOR")
    _, out, _ = run(capsys, gw)
    assert facts_of(out, 30)["refinement"]["refine_check"] == "none"


@pytest.mark.parametrize("kw", [{"assoc": "OWNER"}, {"assoc": "MEMBER"}, {"assoc": "COLLABORATOR"}, {"kind": "Bot"}])
def test_trusted_authors_count(gw, capsys, kw):
    story(gw, 30, summary_text=None)
    gw.comment(30, summary(), login="someone", **kw)
    _, out, _ = run(capsys, gw)
    assert facts_of(out, 30)["refinement"]["refine_check"] == "pass"


def test_open_decision_blocker(gw, capsys):
    story(gw)
    gw.comment(30, BLOCKER)
    _, out, _ = run(capsys, gw)
    entry = facts_of(out, 30)
    assert entry["decision_blocker"]["state"] == "open" and not entry["refined"]
    assert "open-decision-blocker" in entry["reasons"]


def test_blocker_by_a_stranger_is_ignored(gw, capsys):
    story(gw)
    gw.comment(30, BLOCKER, login="stranger")
    _, out, _ = run(capsys, gw)
    assert facts_of(out, 30)["decision_blocker"]["state"] == "none" and facts_of(out, 30)["refined"]


def test_unblocked_by_the_po_needs_a_later_summary(gw, capsys):
    story(gw, summary_text=None)
    gw.comment(30, summary())
    gw.comment(30, BLOCKER)
    gw.comment(30, "Go with A.\n/unblock\n")
    _, out, _ = run(capsys, gw)
    entry = facts_of(out, 30)
    assert entry["decision_blocker"]["state"] == "unblocked" and entry["decision_blocker"]["unblocked_at"]
    assert not entry["refined"] and "summary-before-unblock" in entry["reasons"]
    gw.comment(30, summary())  # the resumed refinement
    _, out, _ = run(capsys, gw)
    assert facts_of(out, 30)["refined"] and facts_of(out, 30)["ready"]


@pytest.mark.parametrize("text, login", [("I will /unblock this later", PO), ("unblocked", PO), ("/unblock", "stranger")])
def test_unblock_must_be_a_line_by_the_po(gw, capsys, text, login):
    story(gw)
    gw.comment(30, BLOCKER)
    gw.comment(30, text, login=login)
    _, out, _ = run(capsys, gw)
    assert facts_of(out, 30)["decision_blocker"]["state"] == "open"


def test_no_po_login_fails_closed(gw, capsys, monkeypatch):
    monkeypatch.delenv("DECISION_BLOCKER_PO_LOGIN")
    story(gw)
    gw.comment(30, BLOCKER, login="x", assoc="OWNER")
    gw.comment(30, "/unblock", login="x", assoc="OWNER")
    _, out, _ = run(capsys, gw)
    assert facts_of(out, 30)["decision_blocker"]["state"] == "open"


# ── dependencies, ready (AC 5, 7) ─────────────────────────────────────────────


def test_dependencies_all_closed_is_ready(gw, capsys):
    gw.states = {40: "closed", 41: "closed"}
    story(gw, body=story_body(hard="- #40\n- #41"))
    _, out, err = run(capsys, gw)
    entry = facts_of(out, 30)
    assert entry["dependencies"] == {"state": "read", "items": [{"number": 40, "state": "closed"},
                                                                 {"number": 41, "state": "closed"}]}  # fmt: skip
    assert entry["refined"] and entry["ready"] and entry["reasons"] == []
    assert "[FACTS] #30 story refined=yes ready=yes stale=no" in err


def test_open_dependency_blocks(gw, capsys):
    gw.states = {40: "closed", 41: "open"}
    story(gw, body=story_body(hard="- #40\n- #41"))
    _, out, err = run(capsys, gw)
    entry = facts_of(out, 30)
    assert entry["refined"] and not entry["ready"] and entry["reasons"] == ["blocked-by #41"]
    assert "refined=yes ready=no stale=no blocked-by #41" in err


def test_unresolvable_dependency_never_counts_as_closed(gw, capsys):
    gw.states = {40: "closed"}
    gw.fail.add("state 42")
    story(gw, body=story_body(hard="- #40\n- #41\n- #42"))
    _, out, _ = run(capsys, gw)
    entry = facts_of(out, 30)
    assert not entry["ready"] and entry["reasons"] == ["unresolvable #41", "unresolvable #42"]
    assert [i["state"] for i in entry["dependencies"]["items"]] == ["closed", "unresolvable", "unresolvable"]


def test_none_dependency_is_ready(gw, capsys):
    story(gw)
    _, out, _ = run(capsys, gw)
    assert facts_of(out, 30)["dependencies"] == {"state": "read", "items": []} and facts_of(out, 30)["ready"]


@pytest.mark.parametrize("body", [story_body(dependencies=False), story_body().replace("Hard depends on:", "Needs:")])
def test_missing_dependencies_section_or_line_is_unreadable(gw, capsys, body):
    story(gw, body=body)
    _, out, _ = run(capsys, gw)
    entry = facts_of(out, 30)
    assert entry["dependencies"]["state"] == "unreadable" and not entry["ready"] and entry["refined"]
    assert entry["reasons"] == ["dependencies-unreadable"]


def test_a_refined_story_becomes_ready_when_its_last_dependency_closes(gw, capsys):
    gw.states = {40: "open"}
    story(gw, body=story_body(hard="- #40"))
    _, before, _ = run(capsys, gw)
    gw.states[40] = "closed"  # nothing else changes
    _, after, _ = run(capsys, gw)
    assert not facts_of(before, 30)["ready"] and facts_of(after, 30)["ready"]
    assert {k: v for k, v in facts_of(before, 30).items() if k not in ("ready", "reasons", "dependencies")} == {
        k: v for k, v in facts_of(after, 30).items() if k not in ("ready", "reasons", "dependencies")}


def test_epic_and_feature_are_never_ready_and_features_carry_the_prefix(gw, capsys):
    gw.add_issue(20, ["type:feature", "prio:P1"], story_body().replace("Feature group:", "X:") + "\nCriterion prefix: F3\n")
    gw.comment(20, summary())
    _, out, _ = run(capsys, gw)
    feature = facts_of(out, 20)
    assert feature["refined"] and feature["ready"] is False and feature["criterion_prefix"] == "F3"
    assert feature["dependencies"] is None and "ready" not in out["epics"][0]


def test_story_entry_fields(gw, capsys):
    story(gw, 30)
    _, out, _ = run(capsys, gw)
    entry = facts_of(out, 30)
    assert (entry["title"], entry["type"], entry["prio"], entry["epic"], entry["feature"]) == ("T30", "story", "prio:P2", 10, 20)


# ── stale, and an Epic with no merged spec (AC 6, 8) ──────────────────────────


def test_stale_story_is_not_refined_or_ready(gw, capsys):
    gw.compares[RECORD_OK[:7]] = "behind"
    story(gw)
    _, out, _ = run(capsys, gw)
    entry = facts_of(out, 30)
    assert entry["stale"] and not entry["refined"] and not entry["ready"] and entry["reasons"] == ["record-stale"]


@pytest.mark.parametrize("dirs", [["11-x"], ["10-a", "10-b"]])
def test_no_merged_spec_needs_no_record_and_is_never_stale(gw, capsys, dirs):
    gw.dirs = dirs
    gw.commits = {f"specs/{d}/spec.md": {"sha": "c" * 40, "date": "2026-11-03T00:00:00Z"} for d in dirs}
    gw.add_issue(30, ["type:story", "prio:P2"], story_body(spec="no record here"))
    gw.comment(30, summary())
    gw.add_issue(31, ["type:story", "prio:P2"], story_body(spec=spec_line("specs/99-x/spec.md")))
    gw.comment(31, summary())
    _, out, _ = run(capsys, gw)
    for n in (30, 31):
        entry = facts_of(out, n)
        assert entry["refined"] and entry["ready"] and not entry["stale"]
    assert not any(c.startswith("compare") for c in gw.calls)


def test_no_merged_spec_still_needs_summary_and_no_open_blocker(gw, capsys):
    gw.dirs = []
    story(gw, 30, summary_text=summary("fail: x"))
    story(gw, 31)
    gw.comment(31, BLOCKER)
    _, out, _ = run(capsys, gw)
    assert not facts_of(out, 30)["refined"] and not facts_of(out, 31)["refined"]


# ── issues that cannot be placed (AC 9) ───────────────────────────────────────


def test_unplaced_and_unreadable_issues_are_reported_and_exit_is_zero(gw, capsys):
    gw.add_issue(30, ["type:story"], "no parent line\n" + story_body().split("\n", 1)[1])
    gw.add_issue(31, ["type:story"], story_body(parent=77))
    story(gw, 32)
    gw.fail.add("comments 32")
    gw.add_issue(33, ["type:story"], story_body(), title="no body").pop("body")
    story(gw, 34)
    rc, out, err = run(capsys, gw)
    assert rc == 0 and out["result"] == "read"
    assert facts_of(out, 30)["reasons"] == ["no-parent"]
    assert facts_of(out, 31)["reasons"] == ["parent-not-open-epic"]
    assert facts_of(out, 32)["reasons"] == ["unreadable: read comments on #32"]
    assert facts_of(out, 33)["reasons"] == ["unreadable: body"]
    for n in (30, 31, 32, 33):
        entry = facts_of(out, n)
        assert not (entry["refined"] or entry["ready"] or entry["stale"])
    assert facts_of(out, 34)["ready"]
    assert "[FACTS] #31 story refined=no ready=no stale=no parent-not-open-epic" in err


def test_pull_requests_and_untyped_issues_are_not_reported(gw, capsys):
    gw.add_issue(50, ["bug"], story_body())
    _, out, _ = run(capsys, gw)
    assert out["issues"] == []


# ── --epic, failures, arguments, dry run (AC 1) ───────────────────────────────


def test_epic_filter(gw, capsys):
    gw.add_issue(11, ["type:epic"], "")
    story(gw, 30)
    gw.add_issue(31, ["type:story"], story_body(parent=11))
    _, out, _ = run(capsys, gw, "--epic", "10")
    assert [e["number"] for e in out["epics"]] == [10] and [i["number"] for i in out["issues"]] == [30]


@pytest.mark.parametrize("step", ["issues", "specs", "history"])
def test_unreadable_inputs_exit_3_with_empty_lists(gw, capsys, step):
    story(gw)
    gw.fail.add(step)
    rc, out, err = run(capsys, gw)
    assert rc == 3 and out["ok"] is False and out["result"] == "unreadable"
    assert out["epics"] == [] and out["issues"] == [] and out["error"]
    assert "[FACTS] unreadable:" in err


@pytest.mark.parametrize("argv", [[], ["--repo", "bad"], ["--epic", "x"], ["--epic", "0"], ["--bogus"]])
def test_invalid_arguments_exit_2(capsys, argv):
    gw = FakeGateway()
    rc = cli.main(["facts", "read", *argv], gateway_factory=lambda repo: gw)
    out = json.loads(capsys.readouterr().out)
    assert rc == 2 and out["result"] == "invalid" and out["block"] == "facts" and gw.calls == []


def test_missing_operation_is_invalid(capsys):
    assert cli.main(["facts"], gateway_factory=lambda repo: FakeGateway()) == 2


def test_dry_run_prints_the_same_output(gw, capsys):
    story(gw)
    rc1, out1, _ = run(capsys, gw)
    rc2, out2, _ = run(capsys, gw, "--dry-run")
    assert rc1 == rc2 == 0 and out1 == out2


def test_read_facts_in_process_matches_the_command(gw, capsys):
    story(gw)
    _, out, _ = run(capsys, gw)
    assert read_facts(gw, "o/r").to_dict() == out


def test_no_token_in_stdout_or_stderr(gw, capsys, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    story(gw)
    gw.fail.add("comments 30")
    run(capsys, gw)
    gw.fail.add("issues")
    gw.issues[0]["title"] = "x"
    original = gw.list_open_issues
    gw.list_open_issues = lambda: (_ for _ in ()).throw(GatewayError("read open issues", f"HTTP 401 token {TOKEN}"))
    rc = cli.main(["facts", "read", "--repo", "o/r"], gateway_factory=lambda repo: gw)
    out, err = capsys.readouterr()
    gw.list_open_issues = original
    assert rc == 3 and TOKEN not in out + err


# ── parity with scripts/refine-check ──────────────────────────────────────────


@pytest.fixture(scope="module")
def refine_check():
    path = ROOT / "scripts" / "refine-check"
    loader = importlib.machinery.SourceFileLoader("refine_check_for_facts", str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


BODIES = [
    story_body(),
    story_body(hard="- #40\n- #41 (note)\n  - #42"),
    story_body(hard="- #5\n- #30"),
    story_body(dependencies=False),
    story_body().replace("Hard depends on:", "**Hard depends on:**"),
    "## Dependencies\n\nSoft depends on:\n\n- #1\n",
    "## Dependencies\n\nHard depends on:\n- #7\nRevalidate after dependency closes: no\n- #8\n",
    "```\n## Dependencies\nHard depends on:\n- #9\n```\n",
    "Spec: [`specs/10-epic/spec.md`](https://x/y) at `abc1234` extra note\n",
    "Spec: [`specs/10-epic/spec.md`](https://x/y) at `xyz`\n",
    "Spec: specs/10-epic/spec.md\n",
    "\r\n".join(story_body().split("\n")),
]


@pytest.mark.parametrize("body", BODIES)
def test_parsers_agree_with_refine_check(refine_check, body):
    sections = refine_check.parse_sections(body)
    assert parse_sections(body) == sections
    mine = hard_dependencies(parse_sections(body), 30)
    expected = refine_check.hard_dependencies(sections, 30)
    assert (mine or []) == expected
    found = SPEC_LINE.search(body.replace("\r\n", "\n"))
    other = refine_check.SPEC_LINE.search(body.replace("\r\n", "\n"))
    assert (found.groups() if found else None) == (other.groups() if other else None)


# ── the real gateway against a stand-in gh ────────────────────────────────────

FAKE_GH = r'''
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
url = next((a for a in args if a.startswith("repos/")), "")
mode = os.environ.get("FAKE_GH_MODE", "ok")
def fail(text):
    print(text, file=sys.stderr)
    sys.exit(1)
if mode == "fail":
    fail("gh: HTTP 500")
if "/issues?state=open" in url:
    print(json.dumps([{"number": 1}, {"number": 2, "pull_request": {}}]) + json.dumps([{"number": 3}]))
elif url.endswith("/issues/5"):
    print(json.dumps({"state": "closed"}))
elif url.endswith("/issues/6"):
    print(json.dumps({"state": "weird"}))
elif "/contents/specs" in url:
    print(json.dumps([{"name": "10-epic", "type": "dir"}]) if mode != "garbage" else "not json")
elif "/contents/nothing" in url:
    fail("gh: Not Found (HTTP 404)")
elif "/commits?path=specs/10-epic/spec.md" in url:
    print(json.dumps([{"sha": "abc", "commit": {"committer": {"date": "2026-11-01T00:00:00Z"}}}]))
elif "/commits?path=specs/none" in url:
    print("[]")
elif "/compare/abc...def" in url:
    print(json.dumps({"status": "ahead"}))
elif "/compare/" in url:
    fail("gh: Not Found (HTTP 404)")
else:
    fail("gh: Not Found (HTTP 404)")
'''


@pytest.fixture
def real(tmp_path, monkeypatch):
    script = tmp_path / "gh.py"
    script.write_text(FAKE_GH, encoding="utf-8")
    log = tmp_path / "log"
    monkeypatch.setenv("FAKE_GH_LOG", str(log))
    monkeypatch.delenv("FAKE_GH_MODE", raising=False)
    gateway = GhGateway("o/r", gh_command=(sys.executable, str(script)))
    return gateway, lambda: [json.loads(line) for line in log.read_text().splitlines()]


def test_real_gateway_read_calls(real):
    gateway, calls = real
    assert [i["number"] for i in gateway.list_open_issues()] == [1, 3]  # the pull request is left out, both pages read
    assert gateway.issue_state(5) == "closed"
    assert gateway.list_dir("specs") == [{"name": "10-epic", "type": "dir"}]
    assert gateway.newest_commit("specs/10-epic/spec.md") == {"sha": "abc", "date": "2026-11-01T00:00:00Z"}
    assert gateway.newest_commit("specs/none/spec.md") is None
    assert gateway.compare_commits("abc", "def") == "ahead"
    assert calls() == [
        ["api", "--paginate", "repos/o/r/issues?state=open&per_page=100"],
        ["api", "repos/o/r/issues/5"],
        ["api", "repos/o/r/contents/specs?ref=main"],
        ["api", "repos/o/r/commits?path=specs/10-epic/spec.md&sha=main&per_page=1"],
        ["api", "repos/o/r/commits?path=specs/none/spec.md&sha=main&per_page=1"],
        ["api", "repos/o/r/compare/abc...def"],
    ]
    assert all("-X" not in c and "--input" not in c for c in calls())  # reads only


def test_real_gateway_errors_name_the_step(real, monkeypatch):
    gateway, _ = real
    with pytest.raises(GatewayError) as error:
        gateway.compare_commits("abc", "zzz")
    assert error.value.step == "compare abc with zzz" and error.value.not_found
    with pytest.raises(GatewayError) as error:
        gateway.list_dir("nothing")
    assert error.value.not_found
    with pytest.raises(GatewayError, match="could not be parsed"):
        gateway.issue_state(6)
    monkeypatch.setenv("FAKE_GH_MODE", "garbage")
    with pytest.raises(GatewayError, match="could not be parsed"):
        gateway.list_dir("specs")
    monkeypatch.setenv("FAKE_GH_MODE", "fail")
    for call in (gateway.list_open_issues, lambda: gateway.issue_state(5), lambda: gateway.newest_commit("x"),
                 lambda: gateway.compare_commits("a", "b")):  # fmt: skip
        with pytest.raises(GatewayError, match="HTTP 500"):
            call()
