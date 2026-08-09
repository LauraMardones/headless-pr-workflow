from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_command_validates_exactly_one_positive_issue_number_before_mutation() -> None:
    command = read(".claude/commands/verify-closure.md")

    assert "Require exactly one token" in command
    assert "`[1-9][0-9]*`" in command
    assert "Missing, extra, zero, negative, flag-like, or non-numeric input" in command
    assert "stop before any GitHub comment" in command


def test_command_verifies_repository_target_type_and_fresh_main() -> None:
    command = read(".claude/commands/verify-closure.md")

    assert "exact repository" in command
    assert "identity `LauraMardones/headless-pr-workflow`" in command
    assert "Require it to exist and be open" in command
    assert "Require exactly one supported label" in command
    assert "`type:feature` or `type:epic`" in command
    assert "`git rev-parse HEAD`" in command
    assert "require the full local SHA to equal the full" in command
    assert "remote `main` SHA" in command
    assert "immediately before the permitted comment mutation" in command


def test_feature_branch_inventories_children_and_merged_prs() -> None:
    command = read(".claude/commands/verify-closure.md")

    assert "Query GitHub-native sub-issue relationships" in command
    assert "Search open and closed issues" in command
    assert "Combine and deduplicate by issue number" in command
    assert "rather than choosing one source silently" in command
    assert "Include all open and closed direct children" in command
    assert "An unmerged or merely mentioned PR is not delivered evidence" in command


def test_epic_branch_fails_closed_for_open_child_feature() -> None:
    command = read(".claude/commands/verify-closure.md")

    assert "Include every direct child Feature" in command
    assert "Require each child to have exactly the `type:feature` label and to be closed" in command
    assert "Any open child Feature blocks a ready-for-PO result" in command
    assert "map delivery to the Epic goal and every Epic criterion" in command


def test_criterion_matrix_requires_concrete_non_stale_evidence() -> None:
    command = read(".claude/commands/verify-closure.md")

    assert "Create exactly one row per declared criterion" in command
    assert "repository file paths with line ranges at the verified SHA" in command
    assert "exact test commands with recorded passing outcomes" in command
    assert "Narrative confidence" in command
    assert "environment-limited check is not passing evidence" in command
    assert "unproven or contradicted row `FAIL` or `BLOCKED`" in command


def test_required_checks_classify_failures_and_environment_limits() -> None:
    command = read(".claude/commands/verify-closure.md")

    assert "`python -m pytest tests/test_verify_closure_command.py`" in command
    assert "`python -m pytest`" in command
    assert "classify its result as `PASS`, `FAIL`, or" in command
    assert "`BLOCKED`" in command
    assert "A non-zero test result is `FAIL`" in command
    assert "environment limits is `BLOCKED`, never `PASS`" in command


def test_failed_verification_neither_posts_summary_nor_requests_po() -> None:
    command = read(".claude/commands/verify-closure.md")

    assert "do not post the authoritative technical delivery summary" in command
    assert "do not request PO product confirmation" in command
    assert "do not mutate repository or issue lifecycle state" in command
    assert "return a human-readable blocker summary" in command


def test_success_summary_has_required_contract_and_stops_before_close() -> None:
    command = read(".claude/commands/verify-closure.md")

    for heading in (
        "### Child and merged PR inventory",
        "### Criterion evidence matrix",
        "### Checks",
        "### Blockers",
        "### Residual risk",
        "### Next action",
    ):
        assert heading in command

    assert "PO product confirmation is required. Is this what you wanted?" in command
    assert "Stop without detecting confirmation" in command
    assert "performing a close or lifecycle-state mutation" in command


def test_same_sha_verification_is_idempotent_and_new_sha_is_reverified() -> None:
    command = read(".claude/commands/verify-closure.md")

    assert "<!-- verify-closure:issue=$ARGUMENTS;main=<FULL_MAIN_SHA> -->" in command
    assert "fetch all current issue comments and search for an exact marker" in command
    assert "return its immutable comment URL and stop without posting" in command
    assert "If more than one exists" in command
    assert "A marker for an older SHA does not satisfy the current run" in command


def test_workflow_docs_describe_read_only_phase_and_po_handoff() -> None:
    agents = read("AGENTS.md")
    status = read("docs/PROJECT-STATUS.md")

    assert "To verify Feature or Epic closure" in agents
    assert "`.claude/commands/verify-closure.md`" in agents
    assert "single successful\ntechnical-evidence comment" in agents
    assert "### Starting deterministic closure verification" in status
    assert "Run `/verify-closure <issue-number>`" in status
    assert "Missing, failed, stale, ambiguous, or" in status
    assert "environment-limited evidence" in status
    assert "returns the existing comment instead of duplicating" in status
    assert "asks the PO for product" in status
    assert "confirmation and stops" in status
