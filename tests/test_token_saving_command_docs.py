from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_implement_command_uses_self_review_helpers_and_generated_handoff() -> None:
    command = read(".claude/commands/implement.md")

    assert "scripts/ac-summary.sh --issue $ARGUMENTS" in command
    assert "exit `2` means no checklist was found" in command
    assert "do not copy the extracted AC/DoD checklist" in command
    assert "bash scripts/dispatcher-change-check.sh --files <changed-files...>" in command
    assert "fix every `FAIL` (exit `1`)" in command
    assert "treat exit `2` as an invocation or input blocker" in command
    assert "scripts/session-summary.sh --command implement" in command
    assert "do not append “What was implemented”, “AC coverage”" in command


def test_review_command_uses_generated_handoff_and_phase_next_actions() -> None:
    command = read(".claude/commands/review.md")

    assert "scripts/session-summary.sh --command review" in command
    assert "--next implementation" in command
    assert "--next merge" in command
    assert "Do not add any recap section" in command


def test_merge_command_fails_closed_and_keeps_evidence_separate() -> None:
    command = read(".claude/commands/merge.md")

    assert "python3 scripts/merge-gate-summary --pr $ARGUMENTS" in command
    assert "only exit `0` permits a merge" in command
    assert "exit `1` means" in command
    assert "exit `2` means" in command
    assert "scripts/session-summary.sh --command merge" in command
    assert "Do not place the merge-gate line inside the generated block" in command


def test_refinement_pipeline_documents_compact_safe_handoffs() -> None:
    documentation = read("docs/REFINEMENT-PIPELINE.md")

    assert "## Token-saving conventions" in documentation
    assert "blockers, deviations, residual risk, and decisions" in documentation
    assert "issue and/or PR number" in documentation
    assert "relevant fresh head SHA" in documentation
    assert "checks, blockers, or next action" in documentation
    assert "must not be inserted as an unsupported field" in documentation


def test_agents_guidance_prefers_focused_local_tests_and_full_ci() -> None:
    agents = read("AGENTS.md")

    assert "Run focused tests locally; the full suite runs in CI." in agents
    assert "python -m pytest tests/test_example.py" in agents
    assert "`conftest.py`, `tests/dispatcher_fakes.py`" in agents
    assert "The full CI suite is mandatory in either case." in agents
    assert "The complete suite is `python -m pytest`." in agents
    assert "Run the test suite with:" not in agents


def test_implement_requires_green_current_head_pytest_before_review_handoff() -> None:
    command = read(".claude/commands/implement.md")

    assert "run focused local tests before the final push" in command
    assert "read the required `pytest` check for that exact head SHA" in command
    assert "older-head result never does" in command
    assert "keep the PR Draft and the story \"In implementation\"" in command
    assert "re-reads the PR head and check" in command
    assert "keep this session alive and poll the current head's `pytest` check" in command
    assert command.index("keep this session alive and poll") < command.index(
        "mark the PR Ready for review"
    )
    assert command.index("read the required `pytest` check") < command.index(
        "mark the PR Ready for review"
    )


def test_project_status_requires_green_pytest_for_review_submission() -> None:
    status = read("docs/PROJECT-STATUS.md")

    assert "after the required `pytest` check is green for the current PR head SHA" in status
    assert "keeps the item In implementation" in status
    assert "python -m pytest" in read(".github/workflows/tests.yml")
