from dataclasses import replace
from unittest.mock import Mock

import pytest

from headless_pr_workflow.closure_verification import (
    CheckResult,
    ClosurePartialFailure,
    Comment,
    EvidenceRow,
    Issue,
    PullRequest,
    TechnicalSummary,
    VerificationBlocked,
    continue_closure,
    parse_issue_number,
    verify_closure,
)


SHA = "a" * 40
SUMMARY_TIME = "2026-08-09T10:00:00Z"
CONFIRMATION_TIME = "2026-08-09T10:01:00Z"


@pytest.fixture
def github() -> Mock:
    target = Issue(
        10,
        "Feature",
        "OPEN",
        frozenset({"type:feature"}),
        declared_criteria=("Criterion one",),
    )
    child = Issue(11, "Story", "CLOSED", frozenset({"type:story"}), 10)
    client = Mock()
    client.repository_name.return_value = "LauraMardones/headless-pr-workflow"
    client.issue.return_value = target
    client.native_children.return_value = [child]
    client.metadata_children.return_value = [child]
    client.linked_pull_requests.return_value = [PullRequest(20, True, "b" * 40)]
    client.main_sha.return_value = SHA
    client.summary_urls.return_value = []
    return client


@pytest.fixture
def local() -> Mock:
    checkout = Mock()
    checkout.head_sha.return_value = SHA
    checkout.evidence_rows.return_value = [
        EvidenceRow("Criterion one", "PASS", ("src/example.py:1-5",))
    ]
    checkout.check_results.return_value = [
        CheckResult("python -m pytest tests/test_verify_closure_command.py", "PASS"),
        CheckResult("python -m pytest", "PASS"),
    ]
    return checkout


@pytest.mark.parametrize("argument", ["", "1 2", "0", "-1", "abc", "--json"])
def test_invalid_arguments_fail_before_collaborators_are_called(
    argument: str, github: Mock, local: Mock
) -> None:
    with pytest.raises(VerificationBlocked, match="exactly one positive integer"):
        verify_closure(argument, github, local)
    github.repository_name.assert_not_called()
    local.head_sha.assert_not_called()


def test_positive_issue_number_is_accepted() -> None:
    assert parse_issue_number("232") == 232


def test_feature_success_inventories_children_and_only_merged_prs(
    github: Mock, local: Mock
) -> None:
    github.linked_pull_requests.return_value = [
        PullRequest(20, True, "b" * 40),
        PullRequest(21, False, None),
    ]

    result = verify_closure("10", github, local)

    assert result.target_type == "type:feature"
    assert [item.issue.number for item in result.inventory] == [11]
    assert [pr.number for pr in result.inventory[0].merged_prs] == [20]
    assert result.should_post_summary is True
    github.native_children.assert_called_once_with(10)
    github.metadata_children.assert_called_once_with(10)


def test_epic_success_requires_closed_feature_children(
    github: Mock, local: Mock
) -> None:
    epic = Issue(
        10,
        "Epic",
        "OPEN",
        frozenset({"type:epic"}),
        declared_criteria=("Criterion one",),
    )
    feature = Issue(11, "Feature", "CLOSED", frozenset({"type:feature"}), 10)
    github.issue.return_value = epic
    github.native_children.return_value = [feature]
    github.metadata_children.return_value = [feature]

    result = verify_closure("10", github, local)

    assert result.target_type == "type:epic"
    assert result.inventory[0].issue == feature


@pytest.mark.parametrize(
    "labels",
    [frozenset(), frozenset({"type:story"}), frozenset({"type:feature", "type:epic"})],
)
def test_invalid_or_ambiguous_target_type_blocks(
    labels: frozenset[str], github: Mock, local: Mock
) -> None:
    github.issue.return_value = Issue(10, "Target", "OPEN", labels)
    with pytest.raises(VerificationBlocked, match="exactly one supported type"):
        verify_closure("10", github, local)


def test_stale_sha_blocks_before_inventory(github: Mock, local: Mock) -> None:
    local.head_sha.return_value = "c" * 40
    with pytest.raises(VerificationBlocked, match="does not match remote main"):
        verify_closure("10", github, local)
    github.native_children.assert_not_called()


def test_conflicting_child_sources_block(github: Mock, local: Mock) -> None:
    native = Issue(11, "Story", "CLOSED", frozenset({"type:story"}), 10)
    conflicting = replace(native, title="Different child data")
    github.native_children.return_value = [native]
    github.metadata_children.return_value = [conflicting]
    with pytest.raises(VerificationBlocked, match="conflicting parent evidence"):
        verify_closure("10", github, local)


def test_open_child_feature_blocks_epic(github: Mock, local: Mock) -> None:
    epic = Issue(
        10,
        "Epic",
        "OPEN",
        frozenset({"type:epic"}),
        declared_criteria=("Criterion one",),
    )
    feature = Issue(11, "Feature", "OPEN", frozenset({"type:feature"}), 10)
    github.issue.return_value = epic
    github.native_children.return_value = [feature]
    github.metadata_children.return_value = [feature]
    with pytest.raises(VerificationBlocked, match="open or invalid child Features"):
        verify_closure("10", github, local)


def test_mixed_type_child_blocks_epic(github: Mock, local: Mock) -> None:
    epic = Issue(
        10,
        "Epic",
        "OPEN",
        frozenset({"type:epic"}),
        declared_criteria=("Criterion one",),
    )
    feature = Issue(
        11,
        "Ambiguous Feature",
        "CLOSED",
        frozenset({"type:feature", "type:epic"}),
        10,
    )
    github.issue.return_value = epic
    github.native_children.return_value = [feature]
    github.metadata_children.return_value = [feature]

    with pytest.raises(VerificationBlocked, match="open or invalid child Features"):
        verify_closure("10", github, local)


def test_missing_declared_criterion_row_blocks(github: Mock, local: Mock) -> None:
    target = replace(
        github.issue.return_value,
        declared_criteria=("Criterion one", "Criterion two"),
    )
    github.issue.return_value = target
    with pytest.raises(VerificationBlocked, match="exactly match declared criteria"):
        verify_closure("10", github, local)


def test_extra_criterion_row_blocks(github: Mock, local: Mock) -> None:
    local.evidence_rows.return_value = [
        EvidenceRow("Criterion one", "PASS", ("src/example.py:1",)),
        EvidenceRow("Undeclared criterion", "PASS", ("src/example.py:2",)),
    ]
    with pytest.raises(VerificationBlocked, match="exactly match declared criteria"):
        verify_closure("10", github, local)


def test_empty_evidence_for_declared_criterion_blocks(
    github: Mock, local: Mock
) -> None:
    local.evidence_rows.return_value = [EvidenceRow("Criterion one", "PASS", ())]
    with pytest.raises(VerificationBlocked, match="criterion is not proven"):
        verify_closure("10", github, local)


def test_failed_criterion_blocks(github: Mock, local: Mock) -> None:
    local.evidence_rows.return_value = [
        EvidenceRow("Criterion one", "FAIL", ("test failed",))
    ]
    with pytest.raises(VerificationBlocked, match="criterion is not proven"):
        verify_closure("10", github, local)


def test_environment_limited_check_blocks(github: Mock, local: Mock) -> None:
    local.check_results.return_value = [CheckResult("python -m pytest", "BLOCKED")]
    with pytest.raises(VerificationBlocked, match="check is BLOCKED"):
        verify_closure("10", github, local)


def test_missing_required_checks_blocks_arbitrary_passing_command(
    github: Mock, local: Mock
) -> None:
    local.check_results.return_value = [
        CheckResult("echo not-the-required-tests", "PASS")
    ]
    with pytest.raises(VerificationBlocked, match="required check is missing") as error:
        verify_closure("10", github, local)
    assert "python -m pytest tests/test_verify_closure_command.py" in str(error.value)
    assert "python -m pytest" in str(error.value)


def test_changed_main_on_final_refresh_blocks(github: Mock, local: Mock) -> None:
    github.main_sha.side_effect = [SHA, "d" * 40]
    with pytest.raises(VerificationBlocked, match="main changed during verification"):
        verify_closure("10", github, local)


def test_changed_criteria_on_final_refresh_blocks(github: Mock, local: Mock) -> None:
    initial = github.issue.return_value
    refreshed = replace(
        initial,
        declared_criteria=("Criterion one", "New criterion"),
    )
    github.issue.side_effect = [initial, refreshed]

    with pytest.raises(
        VerificationBlocked, match="declared criteria changed during verification"
    ):
        verify_closure("10", github, local)
    github.summary_urls.assert_not_called()


def test_repeated_verification_returns_existing_summary(
    github: Mock, local: Mock
) -> None:
    url = (
        "https://github.com/LauraMardones/headless-pr-workflow/issues/10#issuecomment-1"
    )
    github.summary_urls.return_value = [url]

    result = verify_closure("10", github, local)

    assert result.should_post_summary is False
    assert result.existing_summary_url == url
    github.summary_urls.assert_called_once_with(
        f"<!-- verify-closure:issue=10;main={SHA} -->"
    )


def test_duplicate_authoritative_summaries_block(github: Mock, local: Mock) -> None:
    github.summary_urls.return_value = ["url-1", "url-2"]
    with pytest.raises(VerificationBlocked, match="multiple authoritative summaries"):
        verify_closure("10", github, local)


@pytest.fixture
def closure_github() -> Mock:
    client = Mock()
    client.repository_name.return_value = "LauraMardones/headless-pr-workflow"
    client.issue.return_value = Issue(
        10, "Feature", "OPEN", frozenset({"type:feature"})
    )
    client.main_sha.return_value = SHA
    client.technical_summaries.return_value = [
        TechnicalSummary(
            10,
            "type:feature",
            SHA,
            SUMMARY_TIME,
            "https://example.test/summary",
            ("python -m pytest",),
        )
    ]
    client.comments.return_value = [
        Comment(
            "LauraMardones",
            "Product confirmed for Feature #10.",
            CONFIRMATION_TIME,
            "https://example.test/confirmation",
        )
    ]
    client.child_features.return_value = []
    client.closing_evidence_urls.return_value = []
    client.post_closing_evidence.return_value = "https://example.test/closing"
    return client


def test_valid_feature_confirmation_closes_and_posts_evidence(
    closure_github: Mock,
) -> None:
    result = continue_closure("10", closure_github)

    assert result.action == "CLOSED"
    closure_github.close_issue.assert_called_once_with(10)
    body = closure_github.post_closing_evidence.call_args.args[1]
    assert f"<!-- verify-closure-close:issue=10;main={SHA} -->" in body
    assert "Close result: closed" in body
    assert "https://example.test/confirmation" in body


def test_valid_epic_approval_rechecks_closed_features(closure_github: Mock) -> None:
    closure_github.issue.return_value = Issue(
        10, "Epic", "OPEN", frozenset({"type:epic"})
    )
    closure_github.technical_summaries.return_value = [
        TechnicalSummary(10, "type:epic", SHA, SUMMARY_TIME, "summary")
    ]
    closure_github.comments.return_value = [
        Comment(
            "LauraMardones",
            "Product approved for Epic #10.",
            CONFIRMATION_TIME,
            "confirmation",
        )
    ]
    closure_github.child_features.return_value = [
        Issue(11, "Feature", "CLOSED", frozenset({"type:feature"}), 10)
    ]

    assert continue_closure("10", closure_github).action == "CLOSED"
    assert closure_github.child_features.call_count == 2
    closure_github.child_features.assert_called_with(10)


@pytest.mark.parametrize(
    ("comment", "message"),
    [
        (None, "exactly one fresh"),
        (
            Comment(
                "LauraMardones",
                "Product confirmed for Feature #10.",
                "2026-08-09T09:59:00Z",
                "old",
            ),
            "exactly one fresh",
        ),
        (
            Comment(
                "someone-else",
                "Product confirmed for Feature #10.",
                CONFIRMATION_TIME,
                "wrong-author",
            ),
            "exactly one fresh",
        ),
        (
            Comment(
                "LauraMardones",
                "Product confirmed for Feature #11.",
                CONFIRMATION_TIME,
                "wrong-target",
            ),
            "exactly one fresh",
        ),
        (
            Comment(
                "LauraMardones",
                "Looks good! Product confirmed for Feature #10.",
                CONFIRMATION_TIME,
                "ambiguous",
            ),
            "exactly one fresh",
        ),
        (
            Comment(
                "LauraMardones",
                "Product confirmed for Feature #10.",
                CONFIRMATION_TIME,
                "edited",
                edited=True,
            ),
            "exactly one fresh",
        ),
    ],
)
def test_invalid_confirmation_blocks_without_mutation(
    closure_github: Mock, comment: Comment | None, message: str
) -> None:
    closure_github.comments.return_value = [] if comment is None else [comment]

    with pytest.raises(VerificationBlocked, match=message):
        continue_closure("10", closure_github)
    closure_github.close_issue.assert_not_called()
    closure_github.post_closing_evidence.assert_not_called()


def test_multiple_valid_confirmations_are_ambiguous(closure_github: Mock) -> None:
    closure_github.comments.return_value *= 2
    with pytest.raises(VerificationBlocked, match="exactly one fresh"):
        continue_closure("10", closure_github)


def test_stale_summary_sha_blocks(closure_github: Mock) -> None:
    closure_github.main_sha.return_value = "b" * 40
    with pytest.raises(VerificationBlocked, match="rerun technical verification"):
        continue_closure("10", closure_github)
    closure_github.close_issue.assert_not_called()


def test_state_change_on_final_refresh_blocks(closure_github: Mock) -> None:
    initial = closure_github.issue.return_value
    closure_github.issue.side_effect = [initial, replace(initial, state="CLOSED")]

    with pytest.raises(VerificationBlocked, match="state changed before closure"):
        continue_closure("10", closure_github)
    closure_github.close_issue.assert_not_called()


def test_confirmation_change_on_final_refresh_blocks(closure_github: Mock) -> None:
    closure_github.comments.side_effect = [closure_github.comments.return_value, []]

    with pytest.raises(VerificationBlocked, match="confirmation changed"):
        continue_closure("10", closure_github)
    closure_github.close_issue.assert_not_called()


def test_open_epic_child_blocks_before_mutation(closure_github: Mock) -> None:
    closure_github.issue.return_value = Issue(
        10, "Epic", "OPEN", frozenset({"type:epic"})
    )
    closure_github.technical_summaries.return_value = [
        TechnicalSummary(10, "type:epic", SHA, SUMMARY_TIME, "summary")
    ]
    closure_github.comments.return_value = [
        Comment(
            "LauraMardones",
            "Product approved for Epic #10.",
            CONFIRMATION_TIME,
            "confirmation",
        )
    ]
    closure_github.child_features.return_value = [
        Issue(11, "Feature", "OPEN", frozenset({"type:feature"}), 10)
    ]
    with pytest.raises(VerificationBlocked, match="#11"):
        continue_closure("10", closure_github)
    closure_github.close_issue.assert_not_called()


def test_already_closed_with_evidence_is_noop(closure_github: Mock) -> None:
    closure_github.issue.return_value = replace(
        closure_github.issue.return_value, state="CLOSED"
    )
    closure_github.closing_evidence_urls.return_value = ["existing-evidence"]

    result = continue_closure("10", closure_github)

    assert result.action == "NOOP"
    assert result.evidence_url == "existing-evidence"
    closure_github.close_issue.assert_not_called()
    closure_github.post_closing_evidence.assert_not_called()


def test_already_closed_without_evidence_repairs_comment(closure_github: Mock) -> None:
    closure_github.issue.return_value = replace(
        closure_github.issue.return_value, state="CLOSED"
    )

    result = continue_closure("10", closure_github)

    assert result.action == "REPAIRED"
    closure_github.close_issue.assert_not_called()
    closure_github.post_closing_evidence.assert_called_once()


def test_close_failure_does_not_post_success_evidence(closure_github: Mock) -> None:
    closure_github.close_issue.side_effect = RuntimeError("API failed")
    with pytest.raises(RuntimeError, match="API failed"):
        continue_closure("10", closure_github)
    closure_github.post_closing_evidence.assert_not_called()


def test_comment_failure_after_close_reports_repair_action(
    closure_github: Mock,
) -> None:
    closure_github.post_closing_evidence.side_effect = RuntimeError("API failed")
    with pytest.raises(ClosurePartialFailure, match="rerun to repair"):
        continue_closure("10", closure_github)
    closure_github.close_issue.assert_called_once_with(10)
