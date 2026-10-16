import pytest

from headless_pr_workflow.github.pr_context import REVIEW_EVIDENCE_NOT_FETCHED, PullRequestContext
from headless_pr_workflow.approval_check import summarize_approval_check
from headless_pr_workflow.pre_merge import summarize_pre_merge
from headless_pr_workflow.re_review_needed import summarize_re_review_needed
from headless_pr_workflow.review_sha import summarize_review_sha

from tests.github_scenarios import (
    CODEX_USAGE_LIMIT_BODY,
    HEAD_SHA,
    OLD_SHA,
    build_pr_context,
    build_review,
    codex_clean_body,
    codex_comment,
    codex_findings_review,
    codex_pr_378_clean_receipt_body,
    commit_event,
    force_push_event,
    linked_story,
    scenario_codex_review,
    scenario_pr_298_timeline,
    separate_session_review_body,
    with_context,
    scenario_changes_requested,
    scenario_comment_only_review,
    scenario_current_approval,
    scenario_separate_session_review,
    scenario_stale_approval,
)


@pytest.mark.parametrize(
    ("context", "expected_hard_gate", "expected_approval_status", "expected_override_status", "expected_blocking_reason"),
    [
        (scenario_current_approval(head_sha="head123"), True, "current", "missing", None),
        (
            scenario_stale_approval(head_sha="new-head", approval_sha="old-head"),
            False,
            "stale",
            "missing",
            "formal approval is stale for the current PR head SHA",
        ),
        (
            scenario_changes_requested(head_sha="head123"),
            False,
            "current",
            "missing",
            "GitHub review decision is CHANGES_REQUESTED for the current PR head.",
        ),
        (
            scenario_separate_session_review(head_sha="head123"),
            True,
            "missing",
            "accepted",
            None,
        ),
        (
            scenario_comment_only_review(head_sha="head123"),
            False,
            "missing",
            "missing",
            "comment-only review exists without formal approval, a clean Codex review or an accepted separate-session review",
        ),
        (
            build_pr_context(head_ref_oid=""),
            False,
            "unknown-head",
            "missing",
            "Current PR head SHA is unknown, so approval cannot be verified.",
        ),
    ],
)
def test_review_gate_truth_stays_aligned_across_commands(
    context,
    expected_hard_gate,
    expected_approval_status,
    expected_override_status,
    expected_blocking_reason,
):
    review_sha = summarize_review_sha(context)
    approval = summarize_approval_check(context)
    re_review = summarize_re_review_needed(context)
    pre_merge = summarize_pre_merge(context, expected_base_ref_name="main", required_check_names=())
    approval_gate = next(check for check in pre_merge.checks if check.code == "approval-current-head")

    assert review_sha.approval_status == expected_approval_status
    assert approval.approval_status == expected_approval_status
    assert re_review.approval_status == expected_approval_status
    assert approval.separate_session_review.status == expected_override_status
    assert re_review.separate_session_review.status == expected_override_status

    assert review_sha.hard_gate_passed is expected_hard_gate
    assert approval.hard_gate_passed is expected_hard_gate
    assert re_review.hard_gate_passed is expected_hard_gate
    assert re_review.re_review_needed is (not expected_hard_gate)
    assert pre_merge.approval.hard_gate_passed is expected_hard_gate
    assert approval_gate.ok is expected_hard_gate

    if expected_blocking_reason is None:
        assert approval.blocking_reason is None
        assert approval.blocking_reasons == ()
        assert re_review.blocking_reason is None
        assert re_review.blocking_reasons == ()
        assert approval_gate.details == ()
    else:
        assert approval.blocking_reason == expected_blocking_reason
        assert approval.blocking_reasons == (expected_blocking_reason,)
        assert re_review.blocking_reason == expected_blocking_reason
        assert re_review.blocking_reasons == (expected_blocking_reason,)
        assert expected_blocking_reason in pre_merge.blocking_reasons
        assert approval_gate.details == (expected_blocking_reason,)


CLAUDE_STORY = (linked_story("executor:claude-code-sonnet"),)
# A second commit that shares the 10-character prefix Codex names for the head.
PREFIX_TWIN_SHA = HEAD_SHA[:10] + "f" * 30


def _gate_views(context):
    """The approval gate as each command reports it."""

    pre_merge = summarize_pre_merge(context, expected_base_ref_name="main", required_check_names=())
    approval_gate = next(check for check in pre_merge.checks if check.code == "approval-current-head")
    return (
        summarize_approval_check(context),
        summarize_re_review_needed(context),
        summarize_review_sha(context),
        pre_merge,
        approval_gate,
    )


def _assert_gate_fails_everywhere(context):
    approval, re_review, review_sha, pre_merge, approval_gate = _gate_views(context)

    assert approval.hard_gate_passed is False
    assert approval.approval_source is None
    assert approval.satisfied_by is None
    assert re_review.hard_gate_passed is False
    assert re_review.re_review_needed is True
    assert review_sha.hard_gate_passed is False
    assert pre_merge.hard_gate_passed is False
    assert approval_gate.ok is False
    return approval


def _assert_gate_passes_everywhere(context, *, approval_source, satisfied_by):
    approval, re_review, review_sha, pre_merge, approval_gate = _gate_views(context)

    assert approval.hard_gate_passed is True
    assert approval.approval_source == approval_source
    assert approval.satisfied_by == satisfied_by
    assert approval.blocking_reasons == ()
    assert re_review.hard_gate_passed is True
    assert re_review.approval_source == approval_source
    assert review_sha.hard_gate_passed is True
    assert pre_merge.approval.approval_source == approval_source
    assert approval_gate.ok is True
    return approval


def _path_2_context(*, head_sha="head123", reviewer_provider="codex", linked_issues=(), **overrides):
    return build_pr_context(
        head_ref_oid=head_sha,
        latest_reviews=(
            build_review(
                state="COMMENTED",
                commit_oid=head_sha,
                body=separate_session_review_body(head_sha=head_sha, reviewer_provider=reviewer_provider),
            ),
        ),
        linked_issues=linked_issues,
        **overrides,
    )


# AC1


def test_clean_codex_review_on_current_head_passes_the_gate():
    context = scenario_codex_review()

    approval = _assert_gate_passes_everywhere(context, approval_source="codex-review", satisfied_by="codex-review")

    assert approval.approval_status == "missing"
    assert approval.separate_session_review.status == "missing"
    assert approval.codex_review.to_dict() == {
        "status": "accepted",
        "comment_url": "https://github.com/owner/repo/pull/123#issuecomment-1",
        "reviewed_commit_prefix": HEAD_SHA[:10],
        "reason": None,
    }


@pytest.mark.parametrize("closing", ["You're on a roll.", "What shall we delve into next?", "Another round soon, please!"])
def test_clean_codex_review_accepts_the_varying_closing_sentence(closing):
    context = scenario_codex_review(timeline=(commit_event(HEAD_SHA), codex_comment(codex_clean_body(closing=closing))))

    _assert_gate_passes_everywhere(context, approval_source="codex-review", satisfied_by="codex-review")


def test_receipt_format_clean_codex_review_passes_the_gate():
    # #382: the PR #378 clean comment starts with the codex-review-receipt:v1 marker.
    context = scenario_codex_review(
        timeline=(commit_event(HEAD_SHA), codex_comment(codex_pr_378_clean_receipt_body(HEAD_SHA[:10]))),
    )

    approval = _assert_gate_passes_everywhere(context, approval_source="codex-review", satisfied_by="codex-review")

    assert approval.codex_review.reviewed_commit_prefix == HEAD_SHA[:10]


def test_pr_298_real_timeline_passes_with_codex_review():
    context = scenario_pr_298_timeline()

    approval = _assert_gate_passes_everywhere(context, approval_source="codex-review", satisfied_by="codex-review")

    assert approval.codex_review.reviewed_commit_prefix == "8e77192341"


def test_usage_limit_reply_after_clean_review_does_not_supersede_it():
    context = scenario_codex_review(
        timeline=(commit_event(HEAD_SHA), codex_comment(), codex_comment(CODEX_USAGE_LIMIT_BODY)),
    )

    _assert_gate_passes_everywhere(context, approval_source="codex-review", satisfied_by="codex-review")


# AC2 (a)-(k): each case fails path 1 and, with no other evidence, the gate.


@pytest.mark.parametrize(
    ("context", "expected_status", "expected_reason"),
    [
        pytest.param(
            scenario_codex_review(
                timeline=(commit_event(OLD_SHA), codex_comment(codex_clean_body(OLD_SHA[:10])), commit_event(HEAD_SHA)),
            ),
            "stale",
            f"clean Codex review names commit {OLD_SHA[:10]}, which is not the current PR head SHA",
            id="a-named-commit-is-not-the-head",
        ),
        pytest.param(
            scenario_codex_review(
                timeline=(commit_event(PREFIX_TWIN_SHA), commit_event(HEAD_SHA), codex_comment()),
            ),
            "ambiguous",
            f"clean Codex review names commit prefix {HEAD_SHA[:10]}, which matches more than one commit on the PR",
            id="b-prefix-matches-two-commits",
        ),
        pytest.param(
            scenario_codex_review(
                timeline=(commit_event(HEAD_SHA), codex_comment(), codex_findings_review(HEAD_SHA)),
            ),
            "superseded",
            "a newer Codex review submission follows the clean Codex review comment",
            id="c-newer-findings-review",
        ),
        pytest.param(
            scenario_codex_review(
                timeline=(commit_event(HEAD_SHA), codex_comment(), codex_comment("Codex found two problems in this change.")),
            ),
            "superseded",
            "a newer Codex comment that is not a clean result follows the clean Codex review comment",
            id="c-newer-comment-with-findings",
        ),
        pytest.param(
            scenario_codex_review(linked_issues=(linked_story("executor:codex"),)),
            "not-applicable",
            "a Codex review (path 1) counts only for Claude-implemented PRs and linked story #290 carries "
            "`executor:codex`; path 2 or path 3 is required",
            id="d-story-implemented-by-codex",
        ),
        pytest.param(
            # A thumbs-up reaction is not a comment, so the timeline holds no Codex evidence at all.
            scenario_codex_review(timeline=(commit_event(HEAD_SHA),)),
            "missing",
            None,
            id="e-only-a-reaction",
        ),
        pytest.param(
            scenario_codex_review(
                timeline=(
                    commit_event(HEAD_SHA),
                    codex_comment(),
                    force_push_event(before=HEAD_SHA, after=OLD_SHA),
                    force_push_event(before=OLD_SHA, after=HEAD_SHA),
                ),
            ),
            "stale",
            "a commit or force-push was added to the PR after the clean Codex review comment",
            id="f-force-push-after-comment-back-to-named-head",
        ),
        pytest.param(
            scenario_codex_review(
                timeline=(commit_event(HEAD_SHA), codex_comment(), commit_event(OLD_SHA), commit_event(HEAD_SHA)),
            ),
            "stale",
            "a commit or force-push was added to the PR after the clean Codex review comment",
            id="f-commit-after-comment",
        ),
        pytest.param(
            scenario_codex_review(
                timeline=(commit_event(HEAD_SHA), codex_comment(author="LauraMardones", author_type="User")),
            ),
            "missing",
            None,
            id="g-human-quoting-the-codex-text",
        ),
        pytest.param(
            scenario_codex_review(timeline=(commit_event(HEAD_SHA), codex_comment(author_type="User"))),
            "missing",
            None,
            id="g-user-account-with-the-bot-login",
        ),
        pytest.param(
            scenario_codex_review(timeline=(commit_event(HEAD_SHA), codex_comment(CODEX_USAGE_LIMIT_BODY))),
            "invalid",
            "latest Codex comment is a usage-limit reply, not a clean review result",
            id="h-usage-limit-reply",
        ),
        pytest.param(
            scenario_codex_review(
                timeline=(commit_event(HEAD_SHA), codex_comment("Codex could not complete this request.")),
            ),
            "invalid",
            "latest Codex comment is in a format the recognizer does not know, not a clean review result",
            id="h-unknown-bot-comment-format",
        ),
        pytest.param(
            scenario_codex_review(
                timeline=(
                    commit_event(HEAD_SHA),
                    codex_comment("Codex Review: Didn't find any major issues. You're on a roll."),
                ),
            ),
            "invalid",
            "latest Codex comment is in a format the recognizer does not know, not a clean review result",
            id="h-clean-text-without-reviewed-commit",
        ),
        pytest.param(
            scenario_codex_review(linked_issues=()),
            "not-applicable",
            "a Codex review (path 1) needs a linked story to identify the implementer and this PR has none; "
            "a separate-session review (path 2) or a formal approval (path 3) is required",
            id="i-no-linked-story",
        ),
        pytest.param(
            scenario_codex_review(linked_issues=(linked_story(),)),
            "not-applicable",
            "a Codex review (path 1) needs exactly one `executor:` label on linked story #290, "
            "which carries no `executor:` label; path 2 or path 3 is required",
            id="i-story-without-executor-label",
        ),
        pytest.param(
            scenario_codex_review(
                linked_issues=(linked_story("executor:claude-code-sonnet", "executor:claude-code-haiku"),),
            ),
            "not-applicable",
            "a Codex review (path 1) needs exactly one `executor:` label on linked story #290, "
            "which carries 2 `executor:` labels; path 2 or path 3 is required",
            id="i-story-with-two-executor-labels",
        ),
        pytest.param(
            scenario_codex_review(
                linked_issues=(linked_story("executor:claude-code-sonnet"), linked_story(number=291)),
            ),
            "not-applicable",
            "a Codex review (path 1) needs exactly one `executor:` label on linked story #291, "
            "which carries no `executor:` label; path 2 or path 3 is required",
            id="i-second-linked-story-without-executor-label",
        ),
        pytest.param(
            scenario_codex_review(
                timeline=(commit_event(HEAD_SHA), codex_comment(editor="LauraMardones", editor_type="User")),
            ),
            "invalid",
            "clean Codex review comment was edited by another account (LauraMardones)",
            id="j-bot-comment-edited-by-another-account",
        ),
        pytest.param(
            scenario_codex_review(review_evidence_error="PR timeline pagination could not be followed to the end"),
            "invalid",
            "Codex review cannot be verified: PR timeline pagination could not be followed to the end",
            id="k-evidence-not-read-completely",
        ),
    ],
)
def test_rejected_codex_review_fails_the_gate_with_a_specific_reason(context, expected_status, expected_reason):
    approval = _assert_gate_fails_everywhere(context)

    assert approval.codex_review.status == expected_status
    if expected_reason is None:
        assert approval.blocking_reasons == (
            "no formal approval, clean Codex review or accepted separate-session review exists for the current PR head SHA",
        )
    else:
        assert approval.codex_review.reason == expected_reason
        assert approval.blocking_reasons == (expected_reason,)
        assert approval.blocking_reason == expected_reason


def test_default_context_without_fetched_evidence_fails_path_1_and_path_2_closed():
    fetched = _path_2_context(head_sha=HEAD_SHA, timeline=(commit_event(HEAD_SHA), codex_comment()), linked_issues=CLAUDE_STORY)
    # A context built without the new inputs keeps the dataclass default: nothing was fetched.
    assert PullRequestContext.__dataclass_fields__["review_evidence_error"].default == REVIEW_EVIDENCE_NOT_FETCHED
    unfetched = with_context(fetched, review_evidence_error=REVIEW_EVIDENCE_NOT_FETCHED)

    approval = _assert_gate_fails_everywhere(unfetched)

    assert approval.codex_review.status == "invalid"
    assert approval.separate_session_review.status == "invalid"
    assert "were not fetched" in approval.codex_review.reason
    assert "were not fetched" in approval.separate_session_review.reason


# AC3


def test_storyless_pr_with_clean_codex_review_needs_path_2_or_path_3():
    storyless = scenario_codex_review(linked_issues=())

    approval = _assert_gate_fails_everywhere(storyless)

    assert approval.codex_review.status == "not-applicable"
    assert approval.codex_review.comment_url == "https://github.com/owner/repo/pull/123#issuecomment-1"
    assert "needs a linked story" in approval.blocking_reason
    assert "path 2" in approval.blocking_reason and "path 3" in approval.blocking_reason


def test_storyless_pr_passes_with_path_2_evidence():
    storyless = _path_2_context(head_sha=HEAD_SHA, timeline=(commit_event(HEAD_SHA), codex_comment()))

    approval = _assert_gate_passes_everywhere(
        storyless,
        approval_source="separate-session-review",
        satisfied_by="separate-session-review",
    )

    assert approval.codex_review.status == "not-applicable"


def test_storyless_pr_passes_with_path_3_evidence():
    storyless = scenario_codex_review(linked_issues=(), latest_reviews=(build_review(commit_oid=HEAD_SHA),))

    approval = _assert_gate_passes_everywhere(storyless, approval_source="formal", satisfied_by="formal-approval")

    assert approval.codex_review.status == "not-applicable"


# AC4


@pytest.mark.parametrize(
    ("reviewer_provider", "linked_issues", "expected_implementer"),
    [
        pytest.param("codex", (linked_story("executor:claude-code-sonnet"),), "claude", id="codex-reviews-claude-work"),
        pytest.param("claude", (linked_story("executor:codex"),), "codex", id="claude-reviews-codex-work"),
        pytest.param("claude", (), None, id="no-linked-story"),
        pytest.param("codex", (linked_story(),), None, id="linked-story-without-executor-label"),
    ],
)
def test_separate_session_review_passes_without_the_no_independent_approver_sentence(
    reviewer_provider,
    linked_issues,
    expected_implementer,
):
    context = _path_2_context(reviewer_provider=reviewer_provider, linked_issues=linked_issues)
    assert "no independent" not in context.latest_reviews[0].body.lower()

    approval = _assert_gate_passes_everywhere(
        context,
        approval_source="separate-session-review",
        satisfied_by="separate-session-review",
    )

    assert approval.separate_session_review.status == "accepted"
    assert approval.separate_session_review.reviewer_provider == reviewer_provider
    assert approval.separate_session_review.implementer_provider == expected_implementer
    assert approval.separate_session_review.reason is None


@pytest.mark.parametrize(
    "reviewer_provider",
    [
        pytest.param(None, id="no-provider-line"),
        pytest.param("gemini", id="unknown-provider"),
        pytest.param("", id="empty-provider"),
        pytest.param("claude or codex", id="two-providers-on-one-line"),
    ],
)
def test_separate_session_review_fails_without_a_valid_reviewer_provider(reviewer_provider):
    approval = _assert_gate_fails_everywhere(_path_2_context(reviewer_provider=reviewer_provider))

    expected_reason = (
        "separate-session review does not state a valid reviewing provider on its own line "
        "(`Reviewer provider: claude` or `Reviewer provider: codex`)"
    )
    assert approval.separate_session_review.status == "invalid"
    assert approval.separate_session_review.reviewer_provider is None
    assert approval.separate_session_review.reason == expected_reason
    assert approval.blocking_reasons == (expected_reason,)


def test_separate_session_review_fails_when_two_provider_lines_disagree():
    head_sha = "head123"
    body = separate_session_review_body(head_sha=head_sha, reviewer_provider="codex") + "Reviewer provider: claude\n"
    context = build_pr_context(
        head_ref_oid=head_sha,
        latest_reviews=(build_review(state="COMMENTED", commit_oid=head_sha, body=body),),
    )

    approval = _assert_gate_fails_everywhere(context)

    assert approval.separate_session_review.status == "invalid"
    assert approval.separate_session_review.reviewer_provider is None


@pytest.mark.parametrize(
    ("reviewer_provider", "linked_issues"),
    [
        pytest.param("claude", (linked_story("executor:claude-code-sonnet"),), id="claude-reviews-claude-work"),
        pytest.param("codex", (linked_story("executor:codex"),), id="codex-reviews-codex-work"),
        pytest.param(
            "codex",
            (linked_story("executor:claude-code-sonnet"), linked_story("executor:codex", number=291)),
            id="provider-matches-second-linked-story",
        ),
    ],
)
def test_separate_session_review_fails_when_reviewer_is_the_implementing_provider(reviewer_provider, linked_issues):
    approval = _assert_gate_fails_everywhere(
        _path_2_context(reviewer_provider=reviewer_provider, linked_issues=linked_issues)
    )

    assert approval.separate_session_review.status == "invalid"
    assert approval.separate_session_review.reviewer_provider == reviewer_provider
    assert reviewer_provider in approval.separate_session_review.implementer_provider.split(",")
    assert (
        f"states reviewing provider '{reviewer_provider}', which is the provider that implemented the PR"
        in approval.separate_session_review.reason
    )
    assert approval.blocking_reasons == (approval.separate_session_review.reason,)


def test_separate_session_review_recorded_on_an_earlier_head_is_stale():
    context = build_pr_context(
        head_ref_oid="new-head",
        latest_reviews=(
            build_review(
                state="COMMENTED",
                commit_oid="old-head",
                body=separate_session_review_body(head_sha="old-head"),
            ),
        ),
    )

    approval = _assert_gate_fails_everywhere(context)

    assert approval.separate_session_review.status == "stale"
    assert approval.blocking_reasons == ("separate-session review was recorded on a previous PR head SHA",)


def test_separate_session_review_fails_closed_when_linked_stories_cannot_be_read():
    approval = _assert_gate_fails_everywhere(
        _path_2_context(review_evidence_error="linked issues could not be read completely")
    )

    assert approval.separate_session_review.status == "invalid"
    assert approval.separate_session_review.reason == (
        "separate-session review cannot be verified: linked issues could not be read completely"
    )
    assert approval.codex_review.status == "invalid"
    assert len(approval.blocking_reasons) == 2


# AC5


def test_formal_approval_passes_without_the_new_review_inputs():
    context = scenario_current_approval(head_sha="head123", review_evidence_error="GitHub GraphQL request failed")

    approval = _assert_gate_passes_everywhere(context, approval_source="formal", satisfied_by="formal-approval")

    assert approval.codex_review.status == "invalid"


def test_formal_approval_is_reported_first_when_every_path_is_valid():
    context = _path_2_context(head_sha=HEAD_SHA, timeline=(commit_event(HEAD_SHA), codex_comment()), linked_issues=CLAUDE_STORY)
    formal_approval = build_review(commit_oid=HEAD_SHA, submitted_at="2026-04-21T11:00:00Z")
    context = with_context(context, latest_reviews=(*context.latest_reviews, formal_approval))

    approval = _assert_gate_passes_everywhere(context, approval_source="formal", satisfied_by="formal-approval")

    assert approval.codex_review.status == "accepted"
    assert approval.separate_session_review.status == "accepted"


def test_codex_review_is_reported_before_separate_session_review():
    context = _path_2_context(head_sha=HEAD_SHA, timeline=(commit_event(HEAD_SHA), codex_comment()), linked_issues=CLAUDE_STORY)

    approval = _assert_gate_passes_everywhere(context, approval_source="codex-review", satisfied_by="codex-review")

    assert approval.separate_session_review.status == "accepted"


def test_stale_formal_approval_fails_and_names_each_rejected_path():
    context = scenario_codex_review(
        timeline=(commit_event(HEAD_SHA), codex_comment(), codex_findings_review(HEAD_SHA)),
        latest_reviews=(build_review(commit_oid=OLD_SHA),),
    )

    approval = _assert_gate_fails_everywhere(context)

    assert approval.approval_status == "stale"
    assert approval.blocking_reasons == (
        "formal approval is stale for the current PR head SHA",
        "a newer Codex review submission follows the clean Codex review comment",
    )


# AC6: a blocking state wins over valid path 1 and path 2 evidence.


@pytest.mark.parametrize(
    "context",
    [
        pytest.param(scenario_codex_review(review_decision="CHANGES_REQUESTED"), id="valid-codex-review"),
        pytest.param(_path_2_context(review_decision="CHANGES_REQUESTED"), id="valid-separate-session-review"),
    ],
)
def test_changes_requested_blocks_valid_independent_review_evidence(context):
    approval = _assert_gate_fails_everywhere(context)

    assert approval.blocking_reasons == ("GitHub review decision is CHANGES_REQUESTED for the current PR head.",)


@pytest.mark.parametrize(
    "context",
    [
        pytest.param(scenario_codex_review(head_sha="", commit_oids=(HEAD_SHA,)), id="codex-review"),
        pytest.param(_path_2_context(head_sha=""), id="separate-session-review"),
    ],
)
def test_unknown_head_blocks_independent_review_evidence(context):
    approval = _assert_gate_fails_everywhere(context)

    assert approval.approval_status == "unknown-head"
    assert approval.codex_review.status != "accepted"
    assert approval.separate_session_review.status != "accepted"
    assert approval.blocking_reasons == ("Current PR head SHA is unknown, so approval cannot be verified.",)


@pytest.mark.parametrize(
    ("context", "approval_source"),
    [
        pytest.param(scenario_codex_review(is_draft=True), "codex-review", id="valid-codex-review"),
        pytest.param(_path_2_context(is_draft=True), "separate-session-review", id="valid-separate-session-review"),
    ],
)
def test_draft_blocks_merge_when_independent_review_evidence_is_valid(context, approval_source):
    pre_merge = summarize_pre_merge(context, expected_base_ref_name="main", required_check_names=())

    assert pre_merge.approval.approval_source == approval_source
    assert next(check for check in pre_merge.checks if check.code == "not-draft").ok is False
    assert "PR is draft." in pre_merge.blocking_reasons
    assert pre_merge.hard_gate_passed is False

