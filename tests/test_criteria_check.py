from __future__ import annotations

import importlib.util
import json
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "criteria-check"
REPO = "owner/repo"
EPIC = 10
SPEC_PATH = "specs/10-epic/spec.md"

SPEC = """# Spec

## Acceptance Criteria

Intro text.

- **E1:** First.
  - **Verified by:** evidence.
- **E2:** Second.
"""


@pytest.fixture
def mod():
    loader = SourceFileLoader("criteria_check", str(SCRIPT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec and spec.loader
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


def issue(mod, number, body, *labels, state="OPEN", reason=""):
    return mod.Issue(number, f"t{number}", body, tuple(labels), state, reason)


def feature(mod, number=11, prefix="F1", criteria=None, **kw):
    criteria = criteria if criteria is not None else [
        "- [ ] **F1.1** (serves E1) — a", "- [x] **F1.2** (serves E2, E1) — b"]
    head = f"Parent epic: #{EPIC}\n" + (f"Criterion prefix: {prefix}\n" if prefix else "")
    return issue(mod, number, head + "\n## Success Criteria\n\n" + "\n".join(criteria) + "\n", "type:feature", **kw)


def story(mod, number=20, ac=None, mapping=None, group="Feature group: #11\n", label="type:story", **kw):
    ac = ac if ac is not None else ["- [ ] **AC 1 — a.** text", "- [ ] **AC 2 — b.** text"]
    mapping = mapping if mapping is not None else "- AC 1 serves F1.1. AC 2 serves F1.2.\n\nNot in this story:\n\n- AC 3 serves F9.9"
    body = (f"Parent epic: #{EPIC}\n{group}\n## Acceptance Criteria\n\n" + "\n".join(ac)
            + f"\n\n## Epic and Feature Requirements Mapping\n\n{mapping}\n")
    return issue(mod, number, body, label, **kw)


def check(mod, *issues, spec=SPEC):
    return mod.check_epic(EPIC, SPEC_PATH, "main", spec, list(issues))


def ids(result):
    return [(f.id, f.issue, f.criterion) for f in result.findings]


def test_clean_epic_passes(mod):
    result = check(mod, feature(mod), story(mod))
    assert result.findings == []
    assert result.coverage == [("E1", [("F1.1", 11), ("F1.2", 11)]), ("E2", [("F1.2", 11)])]
    lines = mod.render_text(EPIC, result)
    assert lines[0] == "#10: pass - 0 findings"
    assert lines[1] == f"spec: {SPEC_PATH} @ main"
    assert "E1: covered by F1.1 (#11), F1.2 (#11)" in lines


def test_uncovered_epic_criterion_and_empty_spec(mod):
    result = check(mod, feature(mod, criteria=["- **F1.1** (serves E1) — a"]), story(mod, ac=["- **AC 1** a"], mapping="AC 1 serves F1.1"))
    assert ids(result) == [("epic-criterion-uncovered", None, "E2")]
    assert "E2: not covered" in mod.render_text(EPIC, result)
    assert ids(check(mod, spec="## Acceptance Criteria\n\n- nothing\n")) == [("spec-no-epic-criteria", None, None)]


@pytest.mark.parametrize("feat,expected", [
    (dict(prefix=None), ("feature-prefix-missing", 11, None)),
    (dict(criteria=["- **F1.1** (serves E1, E2) — a", "- not an id"]), ("feature-criterion-unparsed", 11, None)),
    (dict(criteria=["- **F2.1** (serves E1, E2) — a"]), ("feature-criterion-wrong-prefix", 11, "F2.1")),
    (dict(criteria=["- **F1.1** — a", "- **F1.2** (serves E1, E2)"]), ("feature-criterion-no-parent", 11, "F1.1")),
    (dict(criteria=["- **F1.1** (serves Q1) — a", "- **F1.2** (serves E1, E2)"]), ("feature-criterion-no-parent", 11, "F1.1")),
    (dict(criteria=["- **F1.1** (serves E1, E9) — a", "- **F1.2** (serves E2)"]), ("feature-criterion-unknown-parent", 11, "F1.1")),
    (dict(criteria=["- **F1.1** (serves E1) — a", "- **F1.1** (serves E2) — b"]), ("criterion-duplicate", 11, "F1.1")),
])
def test_feature_findings(mod, feat, expected):
    result = check(mod, feature(mod, **feat))
    assert expected in ids(result)
    assert all(f.issue in (11, None) for f in result.findings)


def test_duplicate_prefix_names_the_later_feature(mod):
    result = check(mod, feature(mod, 11), feature(mod, 12, criteria=["- **F1.9** (serves E2) — a"]))
    assert ("feature-prefix-duplicate", 12, "F1") in ids(result)


def test_story_without_mapping(mod):
    result = check(mod, feature(mod), story(mod, mapping="nothing here"))
    assert ids(result) == [("story-criterion-no-parent", 20, "AC 1"), ("story-criterion-no-parent", 20, "AC 2")]


def test_story_mapped_only_to_epic_criterion(mod):
    result = check(mod, feature(mod), story(mod, mapping="AC 1 serves E2. AC 2 serves F1.2."))
    assert ids(result) == [("story-criterion-no-parent", 20, "AC 1")]


def test_story_unknown_and_foreign_parent(mod):
    other = feature(mod, 12, prefix="F2", criteria=["- **F2.1** (serves E2) — a"])
    result = check(mod, feature(mod), other, story(mod, mapping="AC 1 serves F1.9. AC 2 serves F2.1."))
    assert ids(result) == [("story-criterion-unknown-parent", 20, "AC 1"), ("story-criterion-unknown-parent", 20, "AC 2")]


@pytest.mark.parametrize("group", ["", "Feature group: #99\n", "Feature group: #5\n"])
def test_story_feature_missing_or_wrong(mod, group):
    other = issue(mod, 5, "no parent line", "type:feature")
    result = check(mod, feature(mod), other, story(mod, group=group))
    assert ids(result) == [("issue-feature-missing", 20, None)]


def test_duplicate_story_criterion(mod):
    result = check(mod, feature(mod), story(mod, ac=["- **AC 1** a", "- **AC 1** b"], mapping="AC 1 serves F1.1"))
    assert ids(result) == [("criterion-duplicate", 20, "AC 1")]


def test_ac_id_forms_and_mapping_forms(mod):
    feat = feature(mod, criteria=[f"- **F1.{n}** (serves E1, E2) — x" for n in range(1, 6)])
    ac = ["- [ ] **AC 3 — named.** text", "- positional second", "- **AC 7**: seventh", "- fourth"]
    mapping = ("- AC 3 serves F1.1, F1.2 and F1.3 (partly) [see x].\n- AC 2 serves F1.4. AC 7 serves F1.5\n"
               "- AC 4 serves F1.1 and F1.2\n\nNot in this story:\n\n- AC 9 serves F1.4")
    result = check(mod, feat, story(mod, ac=ac, mapping=mapping))
    assert result.findings == []
    assert mod.story_mapping(story(mod, mapping=mapping).body)["AC 3"] == ["F1.1", "F1.2", "F1.3"]
    assert "AC 9" not in mod.story_mapping(story(mod, mapping=mapping).body)
    assert [c.id for c in mod.story_criteria(story(mod, ac=ac).body)] == ["AC 3", "AC 2", "AC 7", "AC 4"]


def test_which_issues_count(mod):
    foreign = issue(mod, 30, "Parent epic: #99\n## Acceptance Criteria\n\n- x\n", "type:story")
    dropped = story(mod, 31, mapping="none", state="CLOSED", reason="NOT_PLANNED")
    done = story(mod, 32, mapping="none", state="CLOSED", reason="COMPLETED")
    draft = issue(mod, 33, f"Parent epic: #{EPIC}\n## Goal\n\ntext\n", "type:task")
    result = check(mod, feature(mod), foreign, dropped, done, draft)
    assert {f.issue for f in result.findings} == {32}
    assert result.skipped == [(33, "no acceptance criteria")]
    assert "skip: #33 - no acceptance criteria" in mod.render_text(EPIC, result)


class Fakes:
    def __init__(self, mod, **over):
        self.refs = []
        self.items = over.get("items", [feature(mod), story(mod)])
        self.epic = over.get("epic", issue(mod, EPIC, "", "type:epic"))
        self.spec_error = over.get("spec_error")
        self.list_error = over.get("list_error")
        self.epic_error = over.get("epic_error")
        self.mod = mod

    def issue_reader(self, repo, number):
        if self.epic_error:
            raise self.mod.ReadError(self.epic_error)
        return self.epic

    def spec_reader(self, repo, epic, ref):
        self.refs.append(ref)
        if self.spec_error:
            raise self.mod.ReadError(self.spec_error)
        return SPEC_PATH, SPEC, ref or "main"

    def list_reader(self, repo, limit):
        if self.list_error:
            raise self.mod.ReadError(self.list_error)
        return self.items

    def kw(self):
        return dict(issue_reader=self.issue_reader, spec_reader=self.spec_reader, list_reader=self.list_reader)


def run_main(mod, fakes, *argv):
    return mod.main(["--epic", str(EPIC), "--repo", REPO, *argv], **fakes.kw())


def test_main_text_and_json_pass(mod, capsys):
    fakes = Fakes(mod)
    assert run_main(mod, fakes) == 0
    assert capsys.readouterr().out.startswith("#10: pass - 0 findings\n")
    assert fakes.refs == [None]
    assert run_main(mod, fakes, "--ref", "abc123") == 0
    assert fakes.refs == [None, "abc123"]


def test_main_json_for_failing_epic(mod, capsys):
    fakes = Fakes(mod, items=[feature(mod, criteria=["- **F1.1** (serves E1) — a"]), story(mod, ac=["- x"], mapping="")])
    assert run_main(mod, fakes, "--json", "--ref", "abc") == 1
    data = json.loads(capsys.readouterr().out)
    assert set(data) == {"epic", "repo", "spec", "verdict", "exit_code", "coverage", "findings", "skipped", "error"}
    assert (data["epic"], data["repo"], data["verdict"], data["exit_code"], data["error"]) == (EPIC, REPO, "fail", 1, None)
    assert data["spec"] == {"path": SPEC_PATH, "ref": "abc"}
    assert data["coverage"][0] == {"epic_criterion": "E1", "features": [{"criterion": "F1.1", "issue": 11}]}
    assert data["coverage"][1] == {"epic_criterion": "E2", "features": []}
    assert {"id": "epic-criterion-uncovered", "issue": None, "criterion": "E2",
            "detail": "no Feature criterion serves it"} in data["findings"]
    assert data["skipped"] == []


def test_main_text_failure_lines(mod, capsys):
    fakes = Fakes(mod, items=[feature(mod, criteria=["- **F1.1** (serves E1) — a"])])
    assert run_main(mod, fakes) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "#10: fail - 1 findings"
    assert "epic-criterion-uncovered: - E2 - no Feature criterion serves it" in out


@pytest.mark.parametrize("over", [
    dict(epic_error="issue #10: not found"),
    dict(epic=None),
    dict(spec_error="spec for #10: expected exactly one specs/10-* folder, found none"),
    dict(spec_error="specs/10-epic/spec.md at main: HTTP 404"),
    dict(list_error="issue list: HTTP 500"),
    dict(list_error="issue list: payload could not be parsed (x)"),
    dict(items="many"),
])
@pytest.mark.parametrize("as_json", [False, True])
def test_read_failures_exit_two(mod, capsys, over, as_json):
    if "epic" in over:
        over["epic"] = None
    fakes = Fakes(mod, **{k: v for k, v in over.items() if k not in ("epic", "items")})
    if "epic" in over:
        fakes.epic = issue(mod, EPIC, "", "type:story")
    if "items" in over:
        fakes.items = [feature(mod, n + 100) for n in range(mod.ISSUE_LIMIT)]
    assert run_main(mod, fakes, *(["--json"] if as_json else [])) == 2
    captured = capsys.readouterr()
    assert "pass" not in captured.out
    assert len(captured.err.strip().splitlines()) == 1
    if as_json:
        data = json.loads(captured.out)
        assert (data["verdict"], data["exit_code"]) == ("unreadable", 2)
        assert data["error"] and data["spec"] is None
    else:
        assert captured.out.startswith("#10: unreadable - ")


@pytest.mark.parametrize("argv", [[], ["--epic", "0"], ["--epic", "x"], ["--epic", "1", "--repo", "bad"],
                                  ["--epic", "1", "--ref", "-x"]])
def test_invalid_arguments_exit_two(mod, capsys, argv):
    with pytest.raises(SystemExit) as stop:
        mod.main(argv)
    assert stop.value.code == 2
    assert "pass" not in capsys.readouterr().out


def test_missing_repo_exits_two(mod, monkeypatch, capsys):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert mod.main(["--epic", "10"]) == 2
    assert "pass" not in capsys.readouterr().out


def test_read_spec_requires_exactly_one_folder(mod, monkeypatch):
    def fake(listing):
        def gh(args, what):
            return json.dumps(listing) if "contents/specs?ref=main" in args[-1] else "spec text"
        return gh

    for listing in ([], [{"name": "10-a", "type": "dir"}, {"name": "10-b", "type": "dir"}], [{"name": "100-a", "type": "dir"}]):
        monkeypatch.setattr(mod, "_gh", fake(listing))
        with pytest.raises(mod.ReadError):
            mod.read_spec(REPO, 10, "main")
    monkeypatch.setattr(mod, "_gh", fake([{"name": "10-a", "type": "dir"}, {"name": "11-b", "type": "dir"}]))
    assert mod.read_spec(REPO, 10, "main") == ("specs/10-a/spec.md", "spec text", "main")
