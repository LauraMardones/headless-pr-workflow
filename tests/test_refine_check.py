from __future__ import annotations

import importlib.util
import json
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "refine-check"
REPO = "owner/repo"


@pytest.fixture
def mod():
    loader = SourceFileLoader("refine_check", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


STORY_SECTIONS = [
    "Goal", "User Story / Problem Statement", "Business Value", "Why", "Scope", "Out of Scope",
    "Acceptance Criteria", "Epic and Feature Requirements Mapping", "Test Expectations",
    "Documentation Impact", "Dependencies", "Assumptions", "Risks / Blockers", "Technical Notes",
    "Definition of Done",
]


SPEC_LINE = "Spec: [`specs/10-epic/spec.md`](https://github.com/owner/repo/blob/abc1234/specs/10-epic/spec.md) at `abc1234`"


def body(*, status="Ready for implementation.", skip=(), hard="- None.", questions="None.", header=True, spec=SPEC_LINE):
    lines = []
    if header:
        lines += ["Parent epic: #10 Epic", "Feature group: #11 Feature", f"PO status: {status}"]
        if spec:
            lines.append(spec)
        lines.append("")
    for name in STORY_SECTIONS:
        if name in skip:
            continue
        text = f"Hard depends on:\n\n{hard}\n\nSoft depends on:\n\n- #99" if name == "Dependencies" else "text"
        lines += [f"## {name}", "", text, ""]
    lines += ["## Open Questions", "", questions, ""]
    return "\n".join(lines)


def make_issue(mod, *, labels=("type:story", "prio:P2", "executor:claude-code-sonnet"), title="Story: x", **kw):
    return mod.Issue(number=5, title=title, body=body(**kw), labels=tuple(labels), state="OPEN")


class Env:
    def __init__(self, mod, issue, board="Ready for implementation", states=None, repo_labels=None, spec=True):
        self.mod, self.issue, self.board, self.spec = mod, issue, board, spec
        self.states = states or {}
        self.repo_labels = repo_labels if repo_labels is not None else {"executor:claude-code-sonnet"}

    def issue_reader(self, repo, number):
        if number == 5:
            return self.issue
        return self.mod.Issue(number, "t", "", (), self.states.get(number, "CLOSED"))

    def spec_reader(self, repo, path, ref):
        if isinstance(self.spec, Exception):
            raise self.spec
        return self.spec

    def run(self):
        _, checks = self.mod.run_checks(
            self.issue, REPO, issue_reader=self.issue_reader,
            labels_reader=lambda repo: self.repo_labels, board_reader=lambda repo, n: self.board,
            spec_reader=self.spec_reader,
        )
        return {c.id: c for c in checks}


def failing(checks):
    return sorted(c.id for c in checks.values() if c.status == "fail")


def test_all_pass(mod):
    checks = Env(mod, make_issue(mod)).run()
    assert failing(checks) == []
    assert set(checks) >= {"type_label", "executor_label", "prio_label", "po_status_board",
                           "hard_dependencies", "required_sections", "open_questions", "parent_refs",
                           "spec_record", "feature_criteria"}


@pytest.mark.parametrize(
    "case, labels, expected",
    [
        ("no type", ("prio:P2", "executor:claude-code-sonnet"), "type_label"),
        ("two types", ("type:story", "type:bug", "prio:P2", "executor:claude-code-sonnet"), "type_label"),
        ("no executor", ("type:story", "prio:P2"), "executor_label"),
        ("two executors", ("type:story", "prio:P2", "executor:claude-code-sonnet", "executor:codex"), "executor_label"),
        ("no prio", ("type:story", "executor:claude-code-sonnet"), "prio_label"),
    ],
)
def test_label_failures(mod, case, labels, expected):
    assert expected in failing(Env(mod, make_issue(mod, labels=labels)).run()), case


def test_title_prefix_must_match_type_label(mod):
    checks = Env(mod, make_issue(mod, title="Bug: x")).run()
    assert failing(checks) == ["type_label"]


def test_executor_label_must_exist_in_repository(mod):
    assert failing(Env(mod, make_issue(mod), repo_labels=set()).run()) == ["executor_label"]


def test_epic_needs_no_executor(mod):
    issue = mod.Issue(5, "Epic: x", "PO status: Refined\n", ("type:epic", "prio:P2"), "OPEN")
    checks = Env(mod, issue, board="Refined").run()
    assert checks["executor_label"].status == "skip"
    assert checks["parent_refs"].status == "pass"


def test_body_status_must_match_board(mod):
    assert failing(Env(mod, make_issue(mod), board="Ready for refinement").run()) == ["po_status_board"]


def test_missing_po_status_fails(mod):
    assert failing(Env(mod, make_issue(mod, header=False)).run()) == ["parent_refs", "po_status_board", "spec_record"]


def test_ready_with_open_hard_dependency_fails(mod):
    env = Env(mod, make_issue(mod, hard="- #7 thing"), states={7: "OPEN"})
    assert failing(env.run()) == ["hard_dependencies"]


def test_closed_hard_dependency_and_open_soft_dependency_pass(mod):
    env = Env(mod, make_issue(mod, hard="- #7 thing"), states={7: "CLOSED", 99: "OPEN"})
    assert failing(env.run()) == []


def test_refined_needs_blocked_by_marker(mod):
    env = Env(mod, make_issue(mod, status="Refined", hard="- #7"), board="Refined", states={7: "OPEN"})
    assert failing(env.run()) == ["hard_dependencies"]
    env = Env(mod, make_issue(mod, status="Refined; blocked by #7", hard="- #7"), board="Refined", states={7: "OPEN"})
    assert failing(env.run()) == []


def test_stale_blocked_by_marker_fails(mod):
    env = Env(mod, make_issue(mod, status="Refined; blocked by #7", hard="- #7"), board="Refined", states={7: "CLOSED"})
    assert failing(env.run()) == ["hard_dependencies"]


@pytest.mark.parametrize("skip", [("Acceptance Criteria",), ("Definition of Done", "Scope")])
def test_missing_section_fails(mod, skip):
    checks = Env(mod, make_issue(mod, skip=skip)).run()
    assert failing(checks) == ["required_sections"]
    assert all(name in checks["required_sections"].detail for name in skip)


def test_empty_section_fails(mod):
    issue = make_issue(mod)
    issue = mod.Issue(5, issue.title, issue.body.replace("## Scope\n\ntext", "## Scope\n\n"), issue.labels, "OPEN")
    assert failing(Env(mod, issue).run()) == ["required_sections"]


def test_heading_inside_code_fence_is_not_a_section(mod):
    sections = mod.parse_sections("## A\n```\n## B\n```\ntext\n")
    assert list(sections) == ["A"]


def test_open_question_blocks_ready_only(mod):
    q = "### Which?\n**Context:** x"
    assert failing(Env(mod, make_issue(mod, questions=q)).run()) == ["open_questions"]
    env = Env(mod, make_issue(mod, status="In refinement", questions=q), board="In refinement")
    assert failing(env.run()) == []


@pytest.mark.parametrize("q", [
    "- https://github.com/owner/repo/issues/5#issuecomment-1",
    "Blocked: https://github.com/owner/repo/issues/5#issuecomment-1",
])
def test_decision_blocker_link_blocks_ready_only(mod, q):
    assert failing(Env(mod, make_issue(mod, questions=q)).run()) == ["open_questions"]
    env = Env(mod, make_issue(mod, status="In refinement", questions=q), board="In refinement")
    assert failing(env.run()) == []


@pytest.mark.parametrize("q", ["None.", "none", "- None."])
def test_none_open_questions_pass_ready(mod, q):
    assert failing(Env(mod, make_issue(mod, questions=q)).run()) == []


def test_parent_refs_required_for_ready(mod):
    issue = make_issue(mod)
    issue = mod.Issue(5, issue.title, issue.body.replace("Parent epic: #10 Epic\n", ""), issue.labels, "OPEN")
    assert failing(Env(mod, issue).run()) == ["parent_refs", "spec_record"]


def test_unresolvable_parent_fails(mod):
    env = Env(mod, make_issue(mod))
    base = env.issue_reader

    def reader(repo, number):
        if number == 10:
            raise mod.ReadError("issue #10: Could not resolve to an issue")
        return base(repo, number)

    env.issue_reader = reader
    assert failing(env.run()) == ["parent_refs"]


def test_issue_264_replay(mod):
    """No executor label, board at Ready for refinement, body says ready."""
    issue = make_issue(mod, labels=("type:story", "prio:P2"))
    checks = Env(mod, issue, board="Ready for refinement").run()
    assert failing(checks) == ["executor_label", "po_status_board"]
    assert mod.verdict_line(5, list(checks.values())).startswith(
        "Refine check: #5 fail (2 of 9): executor_label, po_status_board")


def patch_reads(mod, monkeypatch, issue, *, board="Ready for implementation", fail=None):
    def boom(*a, **k):
        raise mod.ReadError(f"{fail}: unreadable")

    monkeypatch.setattr(mod, "read_issue", boom if fail == "issue" else lambda repo, n: issue if n == 5 else mod.Issue(n, "t", "", (), "CLOSED"))
    monkeypatch.setattr(mod, "read_repo_labels", boom if fail == "labels" else lambda repo: {"executor:claude-code-sonnet"})
    monkeypatch.setattr(mod, "spec_exists", boom if fail == "spec" else lambda repo, path, ref: True)
    monkeypatch.setattr(mod, "read_board_status", boom if fail == "board" else lambda repo, n: board)


def test_main_exit_0_human_first_line_is_verdict(mod, monkeypatch, capsys):
    patch_reads(mod, monkeypatch, make_issue(mod))
    assert mod.main(["--issue", "5", "--repo", REPO]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "Refine check: #5 pass"
    assert lines[1].startswith("type_label: pass - ")


def test_main_exit_1_json(mod, monkeypatch, capsys):
    patch_reads(mod, monkeypatch, make_issue(mod), board="Backlog")
    assert mod.main(["--issue", "5", "--repo", REPO, "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert (data["issue"], data["type"], data["verdict"], data["exit_code"]) == (5, "story", "fail", 1)
    assert [c["id"] for c in data["checks"]] == [
        "type_label", "executor_label", "prio_label", "po_status_board",
        "hard_dependencies", "required_sections", "open_questions", "parent_refs",
        "spec_record", "feature_criteria"]
    assert {c["id"]: c["status"] for c in data["checks"]}["po_status_board"] == "fail"


@pytest.mark.parametrize("what", ["issue", "labels", "board", "spec"])
@pytest.mark.parametrize("as_json", [False, True])
def test_unreadable_is_exit_2_never_pass(mod, monkeypatch, capsys, what, as_json):
    patch_reads(mod, monkeypatch, make_issue(mod), fail=what)
    args = ["--issue", "5", "--repo", REPO] + (["--json"] if as_json else [])
    assert mod.main(args) == 2
    out = capsys.readouterr().out
    assert "unreadable" in out and "pass" not in out.lower().replace("unreadable", "")


def test_unreadable_dependency_is_exit_2(mod, monkeypatch, capsys):
    issue = make_issue(mod, hard="- #7")

    def reader(repo, n):
        if n == 5:
            return issue
        raise mod.ReadError("issue #7: HTTP 502")

    patch_reads(mod, monkeypatch, issue)
    monkeypatch.setattr(mod, "read_issue", reader)
    assert mod.main(["--issue", "5", "--repo", REPO]) == 2
    assert "issue #7" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [[], ["--issue", "0"], ["--issue", "x"], ["--issue", "5", "--repo", "bad"]])
def test_invalid_arguments_exit_2(mod, monkeypatch, argv):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    with pytest.raises(SystemExit) as exc:
        code = mod.main(argv)
        raise SystemExit(code)
    assert exc.value.code == 2


def test_missing_repo_exit_2(mod, monkeypatch):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert mod.main(["--issue", "5"]) == 2


# --- spec_record ---------------------------------------------------------------------

def spec_line(path="specs/10-epic/spec.md", commit="abc1234"):
    return f"Spec: [`{path}`](https://github.com/owner/repo/blob/{commit}/{path}) at `{commit}`"


def with_body(mod, issue, text, labels=None):
    return mod.Issue(issue.number, issue.title, text, labels or issue.labels, issue.state)


def spec_check(mod, issue, spec=True):
    return Env(mod, issue, spec=spec).run()["spec_record"]


def test_spec_record_pass_with_optional_note(mod):
    note = spec_line() + " (unchanged on main)"
    check = spec_check(mod, make_issue(mod, spec=note))
    assert (check.status, check.detail) == ("pass", "specs/10-epic/spec.md at abc1234")


@pytest.mark.parametrize("spec, detail", [
    (None, "no 'Spec:' line"),
    ("Spec: refined against abc1234", "malformed"),
    ("Spec: [`specs/10-epic/spec.md`](https://x) at `abc12`", "malformed"),
    ("Spec: [`specs/10-epic/spec.md`](https://x) at `abc1234" + "0" * 40 + "`", "malformed"),
    ("Spec: [`specs/10-epic/spec.md`](https://x)", "malformed"),
])
def test_spec_record_missing_or_malformed_fails(mod, spec, detail):
    check = spec_check(mod, make_issue(mod, spec=spec))
    assert check.status == "fail" and detail in check.detail


@pytest.mark.parametrize("path", ["specs/11-epic/spec.md", "specs/10/spec.md", "specs/10-epic/other.md", "docs/10-epic/spec.md"])
def test_spec_record_wrong_epic_path_fails(mod, path):
    check = spec_check(mod, make_issue(mod, spec=spec_line(path)))
    assert check.status == "fail" and "specs/10-*/spec.md" in check.detail


def test_spec_record_uses_own_number_for_an_epic(mod):
    labels = ("type:epic", "prio:P2")
    ok = mod.Issue(5, "Epic: x", f"PO status: Refined\n{spec_line('specs/5-e/spec.md')}\n", labels, "OPEN")
    bad = mod.Issue(5, "Epic: x", f"PO status: Refined\n{spec_line('specs/10-e/spec.md')}\n", labels, "OPEN")
    assert spec_check(mod, ok).status == "pass"
    assert spec_check(mod, bad).status == "fail"


def test_spec_record_non_epic_needs_parent_epic(mod):
    issue = make_issue(mod)
    text = issue.body.replace("Parent epic: #10 Epic\n", "")
    check = spec_check(mod, with_body(mod, issue, text))
    assert check.status == "fail" and "Parent epic" in check.detail


def test_spec_record_not_found_at_commit_fails(mod):
    check = spec_check(mod, make_issue(mod), spec=False)
    assert check.status == "fail" and "does not exist at abc1234" in check.detail


def test_spec_exists_tells_not_found_from_other_errors(mod, monkeypatch):
    def fake(answer, ok=False):
        def _gh(args, what):
            if ok:
                return answer
            raise mod.ReadError(f"{what}: {answer}")
        return _gh

    monkeypatch.setattr(mod, "_gh", fake("file", ok=True))
    assert mod.spec_exists(REPO, "specs/1-a/spec.md", "abc1234") is True
    monkeypatch.setattr(mod, "_gh", fake("gh: Not Found (HTTP 404)"))
    assert mod.spec_exists(REPO, "specs/1-a/spec.md", "abc1234") is False
    monkeypatch.setattr(mod, "_gh", fake("HTTP 502"))
    with pytest.raises(mod.ReadError):
        mod.spec_exists(REPO, "specs/1-a/spec.md", "abc1234")


def test_spec_read_error_other_than_not_found_is_exit_2(mod, monkeypatch, capsys):
    patch_reads(mod, monkeypatch, make_issue(mod), fail="spec")
    assert mod.main(["--issue", "5", "--repo", REPO]) == 2
    assert "spec" in capsys.readouterr().out


# --- feature_criteria ----------------------------------------------------------------

FEATURE_SECTIONS = ["Goal", "Why", "Scope", "Out of Scope", "Success Criteria", "Dependencies", "Assumptions"]


def feature_issue(mod, *, prefix="Criterion prefix: F2", criteria=None, labels=("type:feature", "prio:P2")):
    criteria = criteria if criteria is not None else [
        "- [ ] **F2.1** (serves E1, E3) - first", "- [ ] **F2.2** (serves E2) - second\n  continued"]
    lines = ["Parent epic: #10 Epic", "PO status: In refinement", spec_line(), prefix, ""]
    for name in FEATURE_SECTIONS:
        lines += [f"## {name}", "", "\n".join(criteria) if name == "Success Criteria" else "text", ""]
    return mod.Issue(5, "Feature: x", "\n".join(lines), labels, "OPEN")


def criteria_check(mod, issue):
    return Env(mod, issue, board="In refinement").run()["feature_criteria"]


def test_feature_criteria_pass(mod):
    assert criteria_check(mod, feature_issue(mod)).status == "pass"


def test_feature_criteria_missing_prefix_fails(mod):
    check = criteria_check(mod, feature_issue(mod, prefix=""))
    assert check.status == "fail" and "Criterion prefix" in check.detail


@pytest.mark.parametrize("item, position", [
    ("- [ ] **F3.1** (serves E1) - wrong prefix", 2),
    ("- [ ] **F2.3** - no serves", 2),
    ("- [ ] **F2.3** (serves F1.1) - no epic id", 2),
    ("- [ ] plain text", 2),
])
def test_feature_criteria_bad_item_fails(mod, item, position):
    issue = feature_issue(mod, criteria=["- [ ] **F2.1** (serves E1) - ok", item])
    check = criteria_check(mod, issue)
    assert check.status == "fail" and f"item {position} does not match '**F2.<m>** (serves E<n>)'" in check.detail


def test_feature_criteria_skipped_for_other_types(mod):
    assert Env(mod, make_issue(mod)).run()["feature_criteria"].status == "skip"
