from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def section(text: str, heading: str, next_heading: str) -> str:
    start = text.index(heading)
    return text[start : text.index(next_heading, start)]


def test_refine_story_requires_mapping_in_standard_and_body_format() -> None:
    command = read(".claude/commands/refine-story.md")

    standard = section(command, "## Refinement Standard", "## Epic and Feature Requirements Mapping")
    assert "- Epic and Feature Requirements Mapping" in standard

    body_format = section(command, "## Issue Body Format", "## Final Reply Format")
    assert "## Epic and Feature Requirements Mapping" in body_format
    assert body_format.index("## Acceptance Criteria") < body_format.index(
        "## Epic and Feature Requirements Mapping"
    ) < body_format.index("## Command Surface")
    assert "Not in this story:" in body_format

    assert "each as exactly one of" in command
    assert "reference the criterion's number" in command
    assert "an entry under `Not in this story` with a reason" in command


def test_refine_story_requires_linked_follow_up_for_deferred_requirement() -> None:
    command = read(".claude/commands/refine-story.md")

    assert "deferred to #N" in command
    assert "A deferred requirement must link an existing follow-up issue" in command
    assert "must be linked before the story moves to `Ready for implementation`" in command


def test_refine_story_requires_split_decision() -> None:
    command = read(".claude/commands/refine-story.md")

    assert "when the story adds a new script, a workflow step and docs together" in command
    assert "record a `Split decision:` line in `## Decisions`" in command
    assert "Split decision:" in section(command, "## Issue Body Format", "## Final Reply Format")


def test_refine_story_requires_cross_cutting_statements_in_story() -> None:
    command = read(".claude/commands/refine-story.md")

    assert (
        "time bounds, delivery guarantees and similar statements an executor needs "
        "must be copied into the story's Scope or Acceptance Criteria"
    ) in command


def test_refinement_pipeline_describes_the_same_rules() -> None:
    documentation = read("docs/REFINEMENT-PIPELINE.md")

    assert "## Epic and Feature Requirements Mapping" in documentation
    assert "`deferred to #N`" in documentation
    assert "must link an existing follow-up issue before the story moves to `Ready for implementation`" in documentation
    assert "records a `Split decision:` line in `## Decisions`" in documentation
    assert (
        "Time bounds, delivery guarantees and similar statements an executor needs "
        "must be copied into the story's Scope or Acceptance Criteria"
    ) in documentation
