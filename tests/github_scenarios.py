from __future__ import annotations

from dataclasses import replace
from typing import Any

from headless_pr_workflow.review_policy import (
    CODEX_REVIEW_TRIGGER_PHRASE,
    REVIEWER_PROVIDER_LABEL,
    separate_session_review_text,
)
from headless_pr_workflow.github.pr_context import (
    CheckSummary,
    LinkedIssue,
    PullRequestContext,
    ReviewSummary,
    TimelineEvent,
)


CODEX_BOT = "chatgpt-codex-connector"
HEAD_SHA = "8e771923416dbcc1820f2dd359a262e8b0fc4a9d"
OLD_SHA = "a56a59201772551b814c656020cdbaef98b08bb0"
PR_298_COMMITS = (
    "ab667c97d9bec69cd3c444fc8f4a1c3cdbffc864",
    "1823d34d30e18e32b8cab3519a1be75737fa873e",
    OLD_SHA,
    HEAD_SHA,
)
# Real Codex reply formats (PR #298, PR #287, issue #288), recorded 2026-09-27.
CODEX_USAGE_LIMIT_BODY = (
    "You have reached your Codex usage limits. You can see your limits in the "
    "[Codex usage dashboard](https://chatgpt.com/codex/settings/usage)."
)


def build_review(
    *,
    author: str = "reviewer",
    state: str = "APPROVED",
    submitted_at: str = "2026-04-21T10:00:00Z",
    commit_oid: str | None = "head123",
    body: str | None = None,
) -> ReviewSummary:
    return ReviewSummary(
        author=author,
        state=state,
        submitted_at=submitted_at,
        commit_oid=commit_oid,
        body=body,
    )


def build_check(
    *,
    name: str,
    bucket: str,
    status: str | None = None,
    conclusion: str | None = None,
    state: str | None = None,
    workflow: str | None = None,
    url: str | None = None,
) -> CheckSummary:
    return CheckSummary(
        name=name,
        workflow=workflow,
        status=status,
        conclusion=conclusion,
        state=state,
        bucket=bucket,
        url=url,
    )


def build_pr_context(
    *,
    number: int = 123,
    title: str = "Scenario PR",
    state: str = "OPEN",
    url: str = "https://github.com/owner/repo/pull/123",
    base_ref_name: str = "main",
    base_ref_oid: str | None = "base123",
    head_ref_name: str = "feature/scenario",
    head_ref_oid: str = "head123",
    head_repository: str | None = "owner/repo",
    head_repository_owner: str | None = "owner",
    is_cross_repository: bool = False,
    is_draft: bool = False,
    merge_state_status: str | None = "CLEAN",
    mergeable: str | None = "MERGEABLE",
    review_decision: str | None = None,
    changed_files: int | None = 1,
    additions: int | None = 1,
    deletions: int | None = 0,
    labels: tuple[str, ...] = (),
    latest_reviews: tuple[ReviewSummary, ...] = (),
    review_requests: tuple[str, ...] = (),
    status_checks: tuple[CheckSummary, ...] = (),
    raw: dict[str, Any] | None = None,
    timeline: tuple[TimelineEvent, ...] = (),
    commit_oids: tuple[str, ...] | None = None,
    linked_issues: tuple[LinkedIssue, ...] = (),
    review_evidence_error: str | None = None,
) -> PullRequestContext:
    if commit_oids is None:
        timeline_oids = [oid for event in timeline if event.kind in {"commit", "force-push"} for oid in (event.before_oid, event.oid)]
        commit_oids = tuple(dict.fromkeys(oid for oid in (*timeline_oids, head_ref_oid) if oid))
    return PullRequestContext(
        number=number,
        title=title,
        state=state,
        url=url,
        base_ref_name=base_ref_name,
        base_ref_oid=base_ref_oid,
        head_ref_name=head_ref_name,
        head_ref_oid=head_ref_oid,
        head_repository=head_repository,
        head_repository_owner=head_repository_owner,
        is_cross_repository=is_cross_repository,
        is_draft=is_draft,
        merge_state_status=merge_state_status,
        mergeable=mergeable,
        review_decision=review_decision,
        changed_files=changed_files,
        additions=additions,
        deletions=deletions,
        labels=labels,
        latest_reviews=latest_reviews,
        review_requests=review_requests,
        status_checks=status_checks,
        raw={} if raw is None else raw,
        timeline=timeline,
        commit_oids=commit_oids,
        linked_issues=linked_issues,
        review_evidence_error=review_evidence_error,
    )


def with_context(context: PullRequestContext, /, **overrides: Any) -> PullRequestContext:
    return replace(context, **overrides)


def scenario_draft_pr(**overrides: Any) -> PullRequestContext:
    return build_pr_context(is_draft=True, **overrides)


def scenario_current_approval(*, head_sha: str = "head123", **overrides: Any) -> PullRequestContext:
    return build_pr_context(
        head_ref_oid=head_sha,
        latest_reviews=(build_review(commit_oid=head_sha),),
        **overrides,
    )


def scenario_stale_approval(
    *,
    head_sha: str = "new-head",
    approval_sha: str = "old-head",
    **overrides: Any,
) -> PullRequestContext:
    return build_pr_context(
        head_ref_oid=head_sha,
        latest_reviews=(build_review(commit_oid=approval_sha),),
        **overrides,
    )


def scenario_comment_only_review(
    *,
    head_sha: str = "head123",
    body: str | None = "Looks reasonable, but please double-check the docs.",
    **overrides: Any,
) -> PullRequestContext:
    return build_pr_context(
        head_ref_oid=head_sha,
        latest_reviews=(build_review(state="COMMENTED", commit_oid=head_sha, body=body),),
        **overrides,
    )


def scenario_separate_session_review(
    *,
    head_sha: str = "head123",
    reviewer_provider: str | None = "codex",
    **overrides: Any,
) -> PullRequestContext:
    body = separate_session_review_body(head_sha=head_sha, reviewer_provider=reviewer_provider)
    return build_pr_context(
        head_ref_oid=head_sha,
        latest_reviews=(build_review(state="COMMENTED", commit_oid=head_sha, body=body),),
        raw={"reviews": [{"body": body, "commit": {"oid": head_sha}}]},
        **overrides,
    )


def linked_story(*labels: str, number: int = 290) -> LinkedIssue:
    return LinkedIssue(number=number, repository="owner/repo", labels=("type:story", *labels))


def commit_event(oid: str) -> TimelineEvent:
    return TimelineEvent(kind="commit", oid=oid)


def force_push_event(*, before: str, after: str) -> TimelineEvent:
    return TimelineEvent(kind="force-push", oid=after, before_oid=before, created_at="2026-09-28T10:00:00Z")


def codex_clean_body(prefix: str = HEAD_SHA[:10], *, closing: str = "You're on a roll.") -> str:
    return (
        f"Codex Review: Didn't find any major issues. {closing}\n\n"
        f"**Reviewed commit:** `{prefix}`\n\n"
        "<details> <summary>\u2139\ufe0f About Codex in GitHub</summary>\n<br/>\n\n"
        "[Your team has set up Codex to review pull requests in this repo]"
        "(https://chatgpt.com/codex/settings/general).\n</details>"
    )


def codex_findings_body(prefix: str) -> str:
    return (
        "### \U0001f4a1 Codex Review\n\n"
        "Here are some automated review suggestions for this pull request.\n\n"
        f"**Reviewed commit:** `{prefix}`\n\n"
        "<details> <summary>\u2139\ufe0f About Codex in GitHub</summary>\n</details>"
    )


def codex_comment(
    body: str | None = None,
    *,
    author: str = CODEX_BOT,
    author_type: str = "Bot",
    editor: str | None = None,
    editor_type: str | None = None,
    url: str = "https://github.com/owner/repo/pull/123#issuecomment-1",
) -> TimelineEvent:
    return TimelineEvent(
        kind="comment",
        author=author,
        author_type=author_type,
        editor=editor,
        editor_type=editor_type,
        body=codex_clean_body() if body is None else body,
        created_at="2026-09-28T09:00:00Z",
        url=url,
    )


def codex_findings_review(commit_oid: str) -> TimelineEvent:
    return TimelineEvent(
        kind="review",
        oid=commit_oid,
        author=CODEX_BOT,
        author_type="Bot",
        body=codex_findings_body(commit_oid[:10]),
        state="COMMENTED",
        created_at="2026-09-28T08:00:00Z",
        url="https://github.com/owner/repo/pull/123#pullrequestreview-1",
    )


def scenario_codex_review(
    *,
    head_sha: str = HEAD_SHA,
    timeline: tuple[TimelineEvent, ...] | None = None,
    linked_issues: tuple[LinkedIssue, ...] | None = None,
    **overrides: Any,
) -> PullRequestContext:
    """A PR whose only approval evidence is a clean Codex comment on the current head."""

    return build_pr_context(
        head_ref_oid=head_sha,
        timeline=(commit_event(head_sha), codex_comment()) if timeline is None else timeline,
        linked_issues=(linked_story("executor:claude-code-sonnet"),) if linked_issues is None else linked_issues,
        **overrides,
    )


def scenario_pr_298_timeline(**overrides: Any) -> PullRequestContext:
    """The real timeline of PR #298: four commits, three findings reviews, one clean comment."""

    first, second, third, head = PR_298_COMMITS
    return scenario_codex_review(
        head_sha=head,
        timeline=(
            commit_event(first),
            codex_findings_review(first),
            commit_event(second),
            codex_findings_review(second),
            commit_event(third),
            codex_findings_review(third),
            commit_event(head),
            codex_comment(),
        ),
        **overrides,
    )


def scenario_changes_requested(*, head_sha: str = "head123", **overrides: Any) -> PullRequestContext:
    return scenario_current_approval(head_sha=head_sha, review_decision="CHANGES_REQUESTED", **overrides)


def scenario_failing_checks(
    *,
    head_sha: str = "head123",
    check_name: str = "unit",
    **overrides: Any,
) -> PullRequestContext:
    return scenario_current_approval(
        head_sha=head_sha,
        status_checks=(build_check(name=check_name, bucket="failure", status="COMPLETED", conclusion="FAILURE"),),
        **overrides,
    )


def scenario_pending_checks(
    *,
    head_sha: str = "head123",
    check_name: str = "lint",
    **overrides: Any,
) -> PullRequestContext:
    return scenario_current_approval(
        head_sha=head_sha,
        status_checks=(build_check(name=check_name, bucket="pending", state="PENDING"),),
        **overrides,
    )


def scenario_empty_status_rollup(*, head_sha: str = "head123", **overrides: Any) -> PullRequestContext:
    return scenario_current_approval(head_sha=head_sha, status_checks=(), **overrides)


def scenario_mergeable_unknown(*, head_sha: str = "head123", **overrides: Any) -> PullRequestContext:
    return scenario_current_approval(head_sha=head_sha, mergeable="UNKNOWN", **overrides)


def scenario_merged_pr(*, head_sha: str = "head123", **overrides: Any) -> PullRequestContext:
    return scenario_current_approval(
        head_sha=head_sha,
        state="MERGED",
        mergeable="UNKNOWN",
        merge_state_status="UNKNOWN",
        raw={"merged": True},
        **overrides,
    )


def scenario_dirty_merge_state(*, head_sha: str = "head123", **overrides: Any) -> PullRequestContext:
    return scenario_current_approval(head_sha=head_sha, merge_state_status="DIRTY", **overrides)


def separate_session_review_body(*, head_sha: str, reviewer_provider: str | None = "codex") -> str:
    """Build the path 2 body from the helper's text, so the wording has one source.

    Scenarios use short fake SHAs and invalid providers that the helper
    rejects, so its output for a valid pair is rewritten.
    """

    provider_line = f"{REVIEWER_PROVIDER_LABEL} codex\n\n"
    body = separate_session_review_text(HEAD_SHA, "codex")
    assert provider_line in body
    replacement = "" if reviewer_provider is None else f"{REVIEWER_PROVIDER_LABEL} {reviewer_provider}\n\n"
    return body.replace(provider_line, replacement).replace(HEAD_SHA, head_sha)


def complete_review_evidence(
    *,
    timeline_items: tuple[dict[str, Any], ...] = (),
    linked_issues: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    """Raw `reviewEvidence` payload, as `fetch_review_evidence` returns it after a complete read."""

    return {
        "error": None,
        "repository": "owner/repo",
        "timelineItems": list(timeline_items),
        "closingIssuesReferences": {"pageInfo": {"hasNextPage": False}, "nodes": list(linked_issues)},
    }


# Real Codex payloads used by the bookkeeping job (#292), recorded 2026-09-27.
# The footer Codex appends to review results names the trigger phrase, so it is
# built from the shared constant instead of repeating the phrase here.
def _codex_about_footer() -> str:
    return (
        "<details> <summary>ℹ️ About Codex in GitHub</summary>\n<br/>\n\n"
        "[Your team has set up Codex to review pull requests in this repo]"
        "(https://chatgpt.com/codex/cloud/settings/general). Reviews are triggered when you\n"
        "- Open a pull request for review\n- Mark a draft as ready\n"
        f'- Comment "{CODEX_REVIEW_TRIGGER_PHRASE}".\n\n'
        "If Codex has suggestions, it will comment; otherwise it will react with \U0001f44d.\n\n\n\n\n"
        'Codex can also answer questions or update the PR. Try commenting "@codex address that feedback".\n'
        "            \n</details>"
    )


def codex_pr_287_clean_body(prefix: str = "40a27a562f") -> str:
    """The clean comment on PR #287 (issuecomment-5856117552)."""

    return (
        "Codex Review: Didn't find any major issues. What shall we delve into next?\n\n"
        f"**Reviewed commit:** `{prefix}`\n\n{_codex_about_footer()}"
    )


def codex_pr_298_findings_body(prefix: str = PR_298_COMMITS[0][:10]) -> str:
    """The findings review on PR #298 (review 5331469921, state COMMENTED)."""

    return (
        "\n### \U0001f4a1 Codex Review\n\n"
        "Here are some automated review suggestions for this pull request.\n\n"
        f"**Reviewed commit:** `{prefix}`\n    \n\n{_codex_about_footer()}"
    )


CODEX_RECEIPT_MARKER = "<!-- codex-review-receipt:v1 -->"


def codex_pr_378_clean_receipt_body(prefix: str = "eab639b146") -> str:
    """The receipt-format clean comment on PR #378 (issuecomment-6211124392)."""

    return (
        f"{CODEX_RECEIPT_MARKER}\n\nCodex Review: Didn't find any major issues. Bravo.\n\n"
        f"**Reviewed commit:** `{prefix}`\n\n{_codex_about_footer()}"
    )


def codex_pr_378_findings_receipt_body(prefix: str = "42d332fe1c") -> str:
    """The receipt-format findings review on PR #378 (review 5541676665, state COMMENTED)."""

    return (
        f"{CODEX_RECEIPT_MARKER}\n\n\n### \U0001f4a1 Codex Review\n\n"
        "Here are some automated review suggestions for this pull request.\n\n"
        f"**Reviewed commit:** `{prefix}`\n    \n\n{_codex_about_footer()}"
    )


def codex_status_body(commit: str = "ae24ec5", *, status: str = "\U0001f504 **Running** since 2026-10-11T14:48:57Z") -> str:
    """The progress comment Codex keeps up to date, as on PR #327 (issuecomment-6109911085)."""

    return (
        "<!-- codex-pull-request-review-summary -->\n\n## Codex Review Summary\n\n"
        "This comment shows the latest Codex review activity on this pull request.\n\n"
        "| Review | Status | Commit | Review trigger |\n| --- | --- | --- | --- |\n"
        f"| \U0001f4dd **Code Review** | {status} | `{commit}` | Manual request |\n\n\n\n{_codex_about_footer()}"
    )


# A Codex task reply, as on PR #297 (issuecomment-5857960089): not a review result.
CODEX_TASK_REPLY_BODY = (
    "### Summary\n\n"
    "* No additional changes were necessary: the current branch already contains the requested ADR-006 wording.\n"
    "* No new commit or pull request was created because the working tree contains no uncommitted changes.\n\n"
    "**Testing**\n\n"
    "* ✅ `git diff --check HEAD^ HEAD`\n\n"
    " [View task →](https://chatgpt.com/s/cd_6ab94d606ee88191a3165a708d83e7a8)"
)


def codex_pr_298_findings_review(
    commit_oid: str = PR_298_COMMITS[0],
    *,
    created_at: str = "2026-09-27T18:10:27Z",
) -> TimelineEvent:
    return TimelineEvent(
        kind="review",
        oid=commit_oid,
        author=CODEX_BOT,
        author_type="Bot",
        body=codex_pr_298_findings_body(commit_oid[:10]),
        state="COMMENTED",
        created_at=created_at,
        url="https://github.com/LauraMardones/headless-pr-workflow/pull/298#pullrequestreview-5331469921",
    )


def conversation_comment(
    body: str,
    *,
    created_at: str,
    author: str = "LauraMardones",
    author_type: str = "User",
    url: str = "https://github.com/owner/repo/pull/123#issuecomment-100",
) -> TimelineEvent:
    return TimelineEvent(kind="comment", author=author, author_type=author_type, body=body, created_at=created_at, url=url)
