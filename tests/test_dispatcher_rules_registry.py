"""The dispatcher decision inventory and the rule registry name the same rules (issue #555, AC 1, AC 2).

``docs/DISPATCHER-DECISIONS.md`` is the written inventory and
``headless_pr_workflow.dispatcher.rules`` the machine-readable registry. The
tests fail when a rule is in one and not in the other, when a registered
implementation marker is no longer found in its file, or when a duplicate is
not one of the rule's implementations outside its owning module.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from headless_pr_workflow.dispatcher import rules

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "docs" / "DISPATCHER-DECISIONS.md"
CONFIG = ROOT / "docs" / "DISPATCHER-CONFIG.md"
ADR_014 = ROOT / "docs" / "decisions" / "ADR-014-dispatcher-building-blocks.md"
SECTION_TITLE = "## Decision rules"
ROW = re.compile(r"^\| `([a-z][a-z0-9_]*)` \|", re.MULTILINE)


def section(text: str) -> str:
    start = text.index(SECTION_TITLE)
    end = text.find("\n## ", start + len(SECTION_TITLE))
    return text[start:] if end == -1 else text[start:end]


def inventory_rules() -> list[str]:
    return ROW.findall(section(INVENTORY.read_text(encoding="utf-8")))


def registry_rules() -> list[str]:
    return [rule.name for rule in rules.RULES]


def test_inventory_and_registry_name_the_same_rules() -> None:
    inventory, registry = inventory_rules(), registry_rules()
    assert inventory, "no rule rows found under ## Decision rules"
    assert sorted(set(inventory) - set(registry)) == [], "in the inventory, not in the registry"
    assert sorted(set(registry) - set(inventory)) == [], "in the registry, not in the inventory"


def test_rule_names_are_unique() -> None:
    for names in (inventory_rules(), registry_rules()):
        assert len(names) == len(set(names))


def test_comparison_fails_on_a_missing_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    registered = rules.RULES
    monkeypatch.setattr(rules, "RULES", registered[1:])
    with pytest.raises(AssertionError, match="not in the registry"):
        test_inventory_and_registry_name_the_same_rules()
    monkeypatch.setattr(rules, "RULES", registered + (rules.Rule(
        name="extra_rule", decides="x", owner="scripts/x.sh",
        implementations=(rules.Implementation("scripts/x.sh", "x"),)),))
    with pytest.raises(AssertionError, match="not in the inventory"):
        test_inventory_and_registry_name_the_same_rules()


@pytest.mark.parametrize("rule", rules.RULES, ids=lambda rule: rule.name)
def test_every_implementation_marker_is_in_its_file(rule: rules.Rule) -> None:
    assert rule.implementations, rule.name
    for impl in rule.implementations:
        path = ROOT / impl.path
        assert path.is_file(), f"{rule.name}: {impl.path} does not exist"
        assert impl.marker in path.read_text(encoding="utf-8"), f"{rule.name}: {impl.marker!r} not in {impl.path}"


@pytest.mark.parametrize("rule", rules.RULES, ids=lambda rule: rule.name)
def test_owner_and_duplicates_are_consistent(rule: rules.Rule) -> None:
    paths = {impl.path for impl in rule.implementations}
    assert rule.owner in paths, f"{rule.name}: owner {rule.owner} has no implementation"
    outside_owner = {impl for impl in rule.implementations if impl.path != rule.owner}
    assert set(rule.duplicates) == outside_owner, f"{rule.name}: duplicates must be every copy outside the owner"


def test_inventory_rows_name_each_implementation_marker() -> None:
    text = section(INVENTORY.read_text(encoding="utf-8"))
    rows = {match.group(1): line for line in text.splitlines() if (match := ROW.match(line))}
    for rule in rules.RULES:
        for impl in rule.implementations:
            assert impl.marker in rows[rule.name], f"{rule.name}: {impl.marker!r} missing from its inventory row"


def test_inventory_is_linked_from_config_and_adr_014() -> None:
    assert "DISPATCHER-DECISIONS.md" in CONFIG.read_text(encoding="utf-8")
    assert "DISPATCHER-DECISIONS.md" in ADR_014.read_text(encoding="utf-8")


@pytest.mark.parametrize("rule", rules.RULES, ids=lambda rule: rule.name)
def test_entry_points_and_tests_are_real(rule: rules.Rule) -> None:
    assert rule.entry_points and set(rule.entry_points) <= set(rules.ENTRY_POINTS), rule.name
    assert rule.tests, rule.name
    for test in rule.tests:
        assert (ROOT / test).is_file(), f"{rule.name}: {test} does not exist"


def test_inventory_marks_exactly_the_suspected_disagreements() -> None:
    text = section(INVENTORY.read_text(encoding="utf-8"))
    rows = {match.group(1): line for line in text.splitlines() if (match := ROW.match(line))}
    for rule in rules.RULES:
        assert ("**Suspected:**" in rows[rule.name]) == bool(rule.divergence), rule.name
