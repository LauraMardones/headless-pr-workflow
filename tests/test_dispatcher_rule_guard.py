"""The duplicate-rule guard (issue #570): fixtures for AC 1, AC 2 and AC 3."""

from __future__ import annotations

from pathlib import Path

from headless_pr_workflow.dispatcher import rule_guard
from headless_pr_workflow.dispatcher.rules import RULES, Implementation, Rule

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


def test_628_marker_holds_has_one_implementation_and_an_empty_allowlist() -> None:
    rule = next(r for r in RULES if r.name == "marker_holds")
    assert rule.owner == "src/headless_pr_workflow/dispatcher/marker.py" and rule.duplicates == ()
    conflict_round = (ROOT / "scripts" / "lib" / "conflict-round.sh").read_text(encoding="utf-8")
    assert "def holds(" not in conflict_round and "dispatcher marker decide" in conflict_round


def test_628_a_second_marker_holds_copy_fails_the_guard(tmp_path: Path) -> None:
    rule = next(r for r in RULES if r.name == "marker_holds")
    write(tmp_path, rule.owner, "def decide_holds(comments):\n    return 'holds'\n")
    write(tmp_path, "scripts/lib/conflict-round.sh", "jq 'def decide_holds($m): . ;'\n")
    problems = rule_guard.find_violations(tmp_path, [rule])
    assert problems == [f"marker_holds: new copy: scripts/lib/conflict-round.sh defines 'decide_holds' (owner {rule.owner})"]


def test_649_open_blocker_has_one_implementation_and_an_empty_allowlist() -> None:
    rule = next(r for r in RULES if r.name == "open_blocker")
    assert rule.owner == "src/headless_pr_workflow/dispatcher/session_blocker.py" and rule.duplicates == ()
    assert [impl.marker for impl in rule.implementations] == ["decide_open_blocker"]
    conflict_round = (ROOT / "scripts" / "lib" / "conflict-round.sh").read_text(encoding="utf-8")
    assert "def open_blocker" not in conflict_round and "dispatcher session-blocker decide" in conflict_round
    invoke = (ROOT / "scripts" / "dispatcher-invoke.sh").read_text(encoding="utf-8")
    assert '"$HPW_BIN" dispatcher session-blocker decide --type decision' in invoke
    assert rule_guard.find_violations(ROOT, [rule]) == []


def test_649_a_second_open_blocker_copy_fails_the_guard(tmp_path: Path) -> None:
    rule = next(r for r in RULES if r.name == "open_blocker")
    write(tmp_path, rule.owner, "def decide_open_blocker(facts, comments):\n    return None\n")
    write(tmp_path, "src/headless_pr_workflow/dispatcher/status.py", "def decide_open_blocker(comments):\n    return None\n")
    problems = rule_guard.find_violations(tmp_path, [rule])
    assert problems == [f"open_blocker: new copy: src/headless_pr_workflow/dispatcher/status.py defines "
                        f"'decide_open_blocker' (owner {rule.owner})"]


def test_650_merge_gates_pass_has_one_implementation_and_an_empty_allowlist() -> None:
    rule = next(r for r in RULES if r.name == "merge_gates_pass")
    assert rule.owner == "src/headless_pr_workflow/dispatcher/gateway.py" and rule.duplicates == ()
    assert [impl.marker for impl in rule.implementations] == ["decide_merge_gates"]
    in_flight = (ROOT / "scripts" / "lib" / "in-flight.sh").read_text(encoding="utf-8")
    assert "mergeable=[^,]+" not in in_flight and '"$HPW_BIN" dispatcher merge-gates decide' in in_flight
    gateway = (ROOT / rule.owner).read_text(encoding="utf-8")
    shim = gateway.split("class GhGateway", 1)[1].split("    def merge_gates_pass(self", 1)[1].split("\n    def ", 1)[0]
    assert "decide_merge_gates(" in shim and "result.passed" not in shim
    assert rule_guard.find_violations(ROOT, [rule]) == []


def test_650_a_second_merge_gates_copy_fails_the_guard(tmp_path: Path) -> None:
    rule = next(r for r in RULES if r.name == "merge_gates_pass")
    write(tmp_path, rule.owner, "def decide_merge_gates(gate, head):\n    return {}\n")
    write(tmp_path, "scripts/lib/in-flight.sh", "decide_merge_gates() {\n    return 0\n}\n")
    problems = rule_guard.find_violations(tmp_path, [rule])
    assert problems == [f"merge_gates_pass: new copy: scripts/lib/in-flight.sh defines 'decide_merge_gates' "
                        f"(owner {rule.owner})"]


def test_652_fix_round_rules_have_one_implementation_and_an_empty_allowlist() -> None:
    for name in ("fix_round_due", "fix_round_attempt_once_per_head"):
        rule = next(r for r in RULES if r.name == name)
        assert rule.owner == "src/headless_pr_workflow/dispatcher/in_flight.py" and rule.duplicates == (), name
        assert [impl.marker for impl in rule.implementations] == ["decide_fix_round"], name
        assert rule_guard.find_violations(ROOT, [rule]) == [], name
    conflict_round = (ROOT / "scripts" / "lib" / "conflict-round.sh").read_text(encoding="utf-8")
    assert "dispatcher in-flight fix-round" in conflict_round and "$moves" not in conflict_round
    invoke = (ROOT / "scripts" / "dispatcher-invoke.sh").read_text(encoding="utf-8")
    assert '"$HPW_BIN" dispatcher in-flight fix-round' in invoke
    for gone in ("fix_round_due() {", "fix_round_heads() {", "head_in_list() {", "fix_round_attempt_superseded() {"):
        assert gone not in invoke, gone


def test_652_a_second_fix_round_copy_fails_the_guard(tmp_path: Path) -> None:
    rule = next(r for r in RULES if r.name == "fix_round_due")
    write(tmp_path, rule.owner, "def decide_fix_round(story, prc):\n    return {}\n")
    write(tmp_path, "scripts/dispatcher-invoke.sh", "decide_fix_round() {\n    return 0\n}\n")
    problems = rule_guard.find_violations(tmp_path, [rule])
    assert problems == [f"fix_round_due: new copy: scripts/dispatcher-invoke.sh defines 'decide_fix_round' "
                        f"(owner {rule.owner})"]
