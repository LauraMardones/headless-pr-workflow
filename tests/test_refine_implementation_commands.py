from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
COMMANDS = ("refine-story", "refine-task", "refine-bug")
TASK_BUG = ("refine-task", "refine-bug")
REMOVED_PHRASES = (
    "requires PO input",
    "When a PO closes an open question",
    "list it under Open Questions",
    "list the milestone decision under Open Questions",
    "list the decision under Open Questions",
    "add it as an Open Question",
    "add them as Open Questions instead of guessing",
    "Act as a senior product owner",
    "Assume the reader has no engineering background",
    "Language must be non-technical",
)


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def command(name: str) -> str:
    return read(f".claude/commands/{name}.md")


def body_format(name: str) -> str:
    text = command(name)
    start = text.index("## Issue Body Format")
    return text[start : text.index("## Refinement Completeness Gate", start)]


@pytest.mark.parametrize("name", COMMANDS)
def test_ac1_role_and_pointer_to_the_shared_rules(name: str) -> None:
    text = command(name)
    assert text.startswith("Act as the Tech Lead (`docs/ROLES.md`) with GitHub access.")
    pointer = "Follow the `## Tech Lead rules` in `.claude/commands/refine.md`"
    assert pointer in text
    assert "Follow them before any issue write" in text
    assert text.index(pointer) < text.index("## Core Instructions")
    assert "## Tech Lead rules" in command("refine")


@pytest.mark.parametrize("name", COMMANDS)
def test_ac2_no_question_to_the_po_inside_the_session(name: str) -> None:
    text = command(name)
    for phrase in REMOVED_PHRASES:
        assert phrase not in text, phrase
    assert "Ask the PO nothing in the session" in text
    assert "becomes a `Type: decision` Blocked Declaration" in text
    assert "the Tech Lead chooses among the existing labels and records the choice under `## Decisions`" in text
    assert "record the milestone decision under `## Decisions`" in text
    assert "The affected acceptance criterion stays unchanged while the blocker is open" in text
    assert "lists the link to each blocker comment" in text
    assert "reads `None.`" in text
    assert "- <link to each decision blocker comment, or `None.`>" in body_format(name)


@pytest.mark.parametrize("name", COMMANDS)
def test_ac3_body_format_has_the_spec_record_and_feature_group(name: str) -> None:
    body = body_format(name)
    assert "Spec: [`specs/{EPIC_NUMBER}-{slug}/spec.md`]({blob link at the commit}) at `{commit}`" in body
    assert "Feature group: #{FEATURE_NUMBER} {FEATURE_TITLE}" in body
    assert "(if applicable)" not in body
    assert "Feature group: #<n>" in command(name)


@pytest.mark.parametrize("name", TASK_BUG)
def test_ac4_task_and_bug_require_the_mapping(name: str) -> None:
    kind = name.removeprefix("refine-")
    text = command(name)
    standard = text[text.index("## Refinement Standard") : text.index("## Epic and Feature Requirements Mapping")]
    assert "- Epic and Feature Requirements Mapping" in standard

    body = body_format(name)
    assert body.index("## Acceptance Criteria") < body.index("## Epic and Feature Requirements Mapping") < body.index(
        "## Command Surface"
    )
    assert "AC {n} serves {Feature criterion" in body
    assert f"Not in this {kind}:" in body

    assert "each as exactly one of" in text
    assert f"an entry under `Not in this {kind}` with a reason" in text
    assert "`deferred to #N`" in text and "`not applicable`" in text
    assert f"every acceptance criterion of the {kind} must appear in the mapping" in text
    assert "name the Feature criterion it serves" in text
    assert "adds scope and is not allowed" in text


def test_ac4_story_keeps_the_mapping_rules() -> None:
    text = command("refine-story")
    assert "adds scope and is not allowed" in text
    assert "Not in this story:" in body_format("refine-story")


def test_ac6_pipeline_shape_starts_at_the_spec_merge() -> None:
    text = read("docs/REFINEMENT-PIPELINE.md")
    start = text.index("## Pipeline Shape")
    shape = text[start : text.index("### `/refine-story`", start)]
    assert "spec-kit" not in shape and "gh issue create" not in shape
    assert "spec-kit" not in text and "gh issue create" not in text
    order = ["/spec", "spec PR merged by the PO", "spec check", "/refine-epic", "/refine-feature",
             "/refine-story /", "Ready for implementation", "/implement", "/review", "/merge",
             "usage findings", "next refinement"]
    positions = [shape.index(item) for item in order]
    assert positions == sorted(positions)


def test_ac7_escalation_triggers_are_decision_blockers() -> None:
    text = read("docs/REFINEMENT-PIPELINE.md")
    start = text.index("## Escalation Triggers")
    triggers = text[start:]
    assert "Human Approver" not in text
    rows = [line for line in triggers.splitlines() if line.startswith("| ") and not line.startswith("| Condition")]
    assert len(rows) == 6
    for row in rows:
        assert "`Type: decision` Blocked Declaration" in row
    assert "PROJECT-STATUS.md#blocked-status-protocol" in triggers
    assert "asks nothing in the session" in triggers


def test_ac8_no_big_bang_section_and_always_just_in_time() -> None:
    text = read("docs/REFINEMENT-PIPELINE.md")
    assert "When Big-Bang Refinement May Be Appropriate" not in text
    assert "Story refinement is always just-in-time against the ready buffer" in text
    assert "A small Epic simply has all its stories in the buffer at once" in text
    assert "appropriate only when" not in text


def test_ac9_usage_finding_rule() -> None:
    text = read("docs/REFINEMENT-PIPELINE.md")
    start = text.index("## Usage-Feedback Loop")
    loop = text[start : text.index("## Pipeline Shape", start)]
    assert "affects scope or an acceptance criterion becomes a `Type: decision` Blocked Declaration" in loop
    assert "Any other finding is noted under the issue's `## Decisions` and refinement continues" in loop
    assert "incorporate the finding into the refinement before" not in loop
    assert "a decision blocker is open" in text and "Human Approver decision" not in text
    assert "changed through the repository variables, not through a project decision" in text


def test_ac10_documents_describe_the_coverage_check() -> None:
    pipeline = read("docs/REFINEMENT-PIPELINE.md")
    assert "`scripts/criteria-check --epic <n>" in pipeline
    assert "Exit `0`: no finding. Exit `1`: one or more findings. Exit `2`:" in pipeline
    assert "`AC <n> serves F<k>.<m>`" in pipeline
    assert "`/refine-story`, `/refine-task` and `/refine-bug` write a `## Epic and Feature Requirements Mapping`" in pipeline
    readme = read("specs/README.md")
    assert "| Story, Task, Bug | Refinement, just-in-time | Testable behavior | At least one Feature criterion |" in readme
    assert "`scripts/criteria-check --epic <n>`" in readme
