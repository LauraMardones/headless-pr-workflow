"""The duplicate-rule guard (issue #570): fixtures for AC 1, AC 2 and AC 3."""

from __future__ import annotations

from pathlib import Path

from headless_pr_workflow.dispatcher import rule_guard
from headless_pr_workflow.dispatcher.rules import Implementation, Rule

ROOT = Path(__file__).resolve().parents[1]
OWNER = "scripts/lib/owner.sh"
COPY = "scripts/old-copy.sh"


def fixture_rule(*, with_copy: bool = True) -> Rule:
    owner = Implementation(OWNER, "linked_pr_match")
    impls = (owner, Implementation(COPY, "linked_pr_match")) if with_copy else (owner,)
    return Rule(name="fixture_rule", decides="x", owner=OWNER, implementations=impls,
                duplicates=impls[1:])


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def tree(tmp_path: Path) -> Path:
    write(tmp_path, OWNER, "linked_pr_match() { jq . ; }\n")
    write(tmp_path, COPY, "linked_pr_match() { jq . ; }\n")
    (tmp_path / "src").mkdir()
    return tmp_path


def test_guard_passes_when_every_copy_is_allowlisted(tmp_path: Path) -> None:
    assert rule_guard.find_violations(tree(tmp_path), [fixture_rule()]) == []


def test_guard_fails_on_a_new_copy_outside_the_owner(tmp_path: Path) -> None:
    root = tree(tmp_path)
    write(root, "scripts/new-script.sh", "linked_pr_match() { jq '.[]' ; }\n")
    problems = rule_guard.find_violations(root, [fixture_rule()])
    assert len(problems) == 1 and "new copy: scripts/new-script.sh" in problems[0]


def test_guard_recognises_python_and_assignment_definitions(tmp_path: Path) -> None:
    root = tree(tmp_path)
    write(root, "src/a.py", "def linked_pr_match(x):\n    return x\n")
    write(root, "scripts/b.sh", "linked_pr_match='x'\n")
    write(root, "scripts/c.sh", "# a call only\nlinked_pr_match \"$1\"\n")
    problems = rule_guard.find_violations(root, [fixture_rule()])
    assert sorted(p.split(": new copy: ")[1].split(" ")[0] for p in problems) == ["scripts/b.sh", "src/a.py"]


def test_guard_ignores_zero_argument_calls_and_finds_brace_next_line(tmp_path: Path) -> None:
    root = tree(tmp_path)
    write(root, "src/caller.py", "from owner import linked_pr_match\nlinked_pr_match()\nx = linked_pr_match()\n")
    write(root, "scripts/caller.sh", "if linked_pr_match(); then :; fi\n")
    write(root, "scripts/brace.sh", "linked_pr_match()\n{\n  :\n}\n")
    write(root, "scripts/fn.sh", "function linked_pr_match {\n  :\n}\n")
    problems = rule_guard.find_violations(root, [fixture_rule()])
    assert sorted(p.split(": new copy: ")[1].split(" ")[0] for p in problems) == ["scripts/brace.sh", "scripts/fn.sh"]


def test_guard_fails_when_an_allowlisted_location_lost_its_rule(tmp_path: Path) -> None:
    root = tree(tmp_path)
    write(root, COPY, "echo nothing\n")
    problems = rule_guard.find_violations(root, [fixture_rule()])
    assert len(problems) == 1 and "stale allowlist entry" in problems[0] and COPY in problems[0]


def test_guard_passes_on_the_repository_with_the_registry_allowlist() -> None:
    assert rule_guard.find_violations(ROOT) == []
