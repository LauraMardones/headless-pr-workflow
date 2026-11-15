from pathlib import Path
import pytest


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
    assert "A deferred requirement that an existing issue delivers links it" in command
    assert "hpw catch-all-issue create" in command
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


def test_refine_story_sources_epic_scope_from_spec_and_traces_every_ac() -> None:
    command = read(".claude/commands/refine-story.md")
    documentation = read("docs/REFINEMENT-PIPELINE.md")

    assert "Use the Epic issue body only as a legacy fallback when the Epic has no spec" in command
    assert "every acceptance criterion of the story must appear in the mapping" in command
    assert "name the Feature criterion it serves" in command
    assert "adds scope and is not allowed" in command
    assert "only as a legacy fallback when no spec exists" in documentation
    assert "names the Feature criterion it serves (ADR-009)" in documentation


# --- catch-all sort (#551) ---------------------------------------------------------

def _command(name: str) -> str:
    return (Path(__file__).resolve().parents[1] / ".claude" / "commands" / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["refine-story.md", "refine-bug.md"])
def test_refine_commands_carry_the_catch_all_sort_step(name: str) -> None:
    text = _command(name)
    assert "## Catch-all sort" in text
    step = text.split("## Catch-all sort", 1)[1].split("\n## ", 1)[0]
    assert "CATCH_ALL_FEATURE" in step and "src/headless_pr_workflow/catch_all.py" in step
    assert step.index("**Moved:**") < step.index("**Maintenance:**") < step.index("**Decision:**")
    assert "Moved adds no criterion" in step and "Keep `Found under:` and `Origin:`" in step
    assert "open Feature" in step and "open Epic with a merged spec" in step
    assert "Rewrite `Parent epic:`, `Feature group:` and the spec record" in step
    assert "without the PO" in step and "exactly one `prio:` and one `executor:` label" in step
    assert "`Type: decision` Blocked Declaration" in step and "body and its criteria stay unchanged" in step
    assert "### Catch-all sort — YYYY-MM-DD" in step
    for line in ("Catch-all sort: moved to #<feature> (F<k>.<m>)", "Catch-all sort: maintenance M<n>",
                 "Catch-all sort: decision M<n>", "decision general-line"):
        assert line in step
    before_body_write = text.index("## Catch-all sort") < text.index("## Core Instructions")
    assert before_body_write


def test_refine_task_has_no_catch_all_sort_step() -> None:
    assert "Catch-all sort" not in _command("refine-task.md")


def test_pipeline_doc_names_the_catch_all_sort_and_check() -> None:
    doc = (Path(__file__).resolve().parents[1] / "docs" / "REFINEMENT-PIPELINE.md").read_text(encoding="utf-8")
    sched = (Path(__file__).resolve().parents[1] / "docs" / "commands" / "dispatcher-scheduler.md").read_text(encoding="utf-8")
    assert "### Catch-all sort" in doc and "`catch_all_sort`" in doc
    assert "Catch-all issues" in sched and "catch_all_sort" in sched


@pytest.mark.parametrize("name", ("refine-story", "refine-bug", "refine-task"))
def test_refine_commands_state_the_size_gate(name: str) -> None:
    command = read(f".claude/commands/{name}.md")

    assert "## Files affected" in command
    assert "More than 10 distinct paths, or more than 5 distinct `tests/` paths" in command
    assert "Split decision: keep together because <reason>" in command
    assert "at least `executor:claude-code-sonnet`" in command
    assert "at most 3 listed paths" in command
    assert "taking the stronger minimum" in command


def test_lens_and_pipeline_state_the_same_thresholds() -> None:
    lens = read(".claude/commands/refinement-lenses/token-economics.md")
    pipeline = read("docs/REFINEMENT-PIPELINE.md")

    assert "more than 10 distinct files, or more than 5 distinct `tests/` files" in lens
    assert "at most 3 listed files" in lens
    assert "stronger minimum" in lens
    for text in (pipeline,):
        assert "more than 10 distinct paths or more than 5 distinct `tests/` paths" in text
        assert "more than 3 listed paths" in text
        assert "stronger minimum" in text
        for check_id in ("files_affected", "size_split", "haiku_limit"):
            assert check_id in text


@pytest.mark.parametrize("name", ("refine-story", "refine-bug", "refine-task"))
def test_refine_commands_state_the_orchestration_executor_rule(name: str) -> None:
    command = read(f".claude/commands/{name}.md")

    assert "`flow:orchestration` or `cp:critical-path`" in command
    assert "exactly the label `executor:claude-code-opus`" in command
    assert "`orchestration_executor`" in command
    assert "taking the stronger minimum" in command
    assert "#367" not in command


def test_pipeline_and_lens_state_the_orchestration_executor_rule() -> None:
    pipeline = read("docs/REFINEMENT-PIPELINE.md")
    lens = read(".claude/commands/refinement-lenses/token-economics.md")

    assert "`orchestration_executor`" in pipeline
    assert "exactly `executor:claude-code-opus`" in pipeline
    assert "#367" not in pipeline
    assert "`orchestration_executor`" in lens and "`executor:claude-code-opus`" in lens
