"""Shared hard-gate policy for PR review and approval semantics.

The approval gate passes when a reviewer independent of the implementer found
no blockers on the exact current head SHA (ADR-007). Three kinds of evidence
count, each failing closed: a formal GitHub approval, a clean Codex review,
or a separate-session review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .github import LinkedIssue, PullRequestContext, ReviewSummary, TimelineEvent


# Required phrases of a separate-session review summary (ADR-007 path 2). The
# recognizer and `separate_session_review_text` both use these constants, so
# the generated text always matches. Matching is case-insensitive.
SEPARATE_SESSION_REVIEW_MARKER = "solo-maintainer override accepted"
NO_BLOCKERS_PHRASE = "No blockers remain for {head_sha}"
APPROVAL_TO_RELY_ON_PHRASE = "approval to rely on for the current head SHA"
REVIEWER_PROVIDER_LABEL = "Reviewer provider:"
REVIEWER_PROVIDERS = ("claude", "codex")
EXECUTOR_LABEL_PREFIX = "executor:"
CLAUDE_EXECUTOR_LABEL_PREFIX = "executor:claude-code-"
CODEX_EXECUTOR_LABEL = "executor:codex"
CODEX_BOT_LOGIN = "chatgpt-codex-connector"
# The Codex review trigger phrase (ADR-006 -> Decision). A PR conversation
# comment whose first line is this phrase starts one Codex review. It is posted
# only as such a comment, by `/implement` or by the bookkeeping job, and never
# written into an issue, a PR description or any other comment.
CODEX_REVIEW_TRIGGER_PHRASE = "@codex review"
CODEX_EVENT_KINDS = ("clean", "findings", "usage-limit", "unknown")

_REVIEWER_PROVIDER_LINE = re.compile(
    rf"^[ \t]*{re.escape(REVIEWER_PROVIDER_LABEL)}[ \t]*(.*?)[ \t]*$", re.IGNORECASE | re.MULTILINE
)
_FULL_SHA = re.compile(r"[0-9a-fA-F]{40}")
_CODEX_CLEAN_START = re.compile(r"^Codex Review:\s*Didn['’]t find any major issues\.")
_CODEX_REVIEWED_COMMIT = re.compile(r"\*\*Reviewed commit:\*\*[ \t]*`([0-9a-fA-F]{7,40})`")
_CODEX_USAGE_LIMIT_START = "You have reached your Codex usage limits."
_CODEX_FINDINGS_START = "### 💡 Codex Review"


@dataclass(frozen=True)
class CodexReviewSummary:
    status: str
    comment_url: str | None
    reviewed_commit_prefix: str | None
    reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "comment_url": self.comment_url,
            "reviewed_commit_prefix": self.reviewed_commit_prefix,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class SeparateSessionReviewSummary:
    status: str
    review_author: str | None
    review_state: str | None
    review_commit_oid: str | None
    review_submitted_at: str | None
    reviewer_provider: str | None = None
    implementer_provider: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "review_author": self.review_author,
            "review_state": self.review_state,
            "review_commit_oid": self.review_commit_oid,
            "review_submitted_at": self.review_submitted_at,
            "reviewer_provider": self.reviewer_provider,
            "implementer_provider": self.implementer_provider,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ReviewPolicySummary:
    number: int
    title: str
    url: str
    head_ref_oid: str
    latest_review_sha: str | None
    latest_review_state: str | None
    latest_review_author: str | None
    latest_approval_sha: str | None
    approval_status: str
    codex_review: CodexReviewSummary
    separate_session_review: SeparateSessionReviewSummary
    approval_source: str | None
    satisfied_by: str | None
    hard_gate_passed: bool
    blocking_reason: str | None
    blocking_reasons: tuple[str, ...]
    reviews: tuple[ReviewSummary, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "url": self.url,
            "head_ref_oid": self.head_ref_oid,
            "latest_review_sha": self.latest_review_sha,
            "latest_review_state": self.latest_review_state,
            "latest_review_author": self.latest_review_author,
            "latest_approval_sha": self.latest_approval_sha,
            "approval_status": self.approval_status,
            "codex_review": self.codex_review.to_dict(),
            "separate_session_review": self.separate_session_review.to_dict(),
            "approval_source": self.approval_source,
            "satisfied_by": self.satisfied_by,
            "hard_gate_passed": self.hard_gate_passed,
            "blocking_reason": self.blocking_reason,
            "blocking_reasons": list(self.blocking_reasons),
            "reviews": [review.to_dict() for review in self.reviews],
        }


def summarize_review_policy(context: PullRequestContext) -> ReviewPolicySummary:
    latest_review = _latest_review_with_sha(context)
    latest_approval_sha = context.latest_approval_sha
    approval_status = _approval_status(context.head_ref_oid, latest_approval_sha)
    codex_review = evaluate_codex_review(context)
    separate_session_review = evaluate_separate_session_review(context)

    approval_source: str | None = None
    satisfied_by: str | None = None
    blocking_reasons: tuple[str, ...] = ()

    if context.review_decision == "CHANGES_REQUESTED":
        blocking_reasons = ("GitHub review decision is CHANGES_REQUESTED for the current PR head.",)
    elif not context.head_ref_oid:
        blocking_reasons = ("Current PR head SHA is unknown, so approval cannot be verified.",)
    elif approval_status == "current":
        approval_source = "formal"
        satisfied_by = "formal-approval"
    elif codex_review.status == "accepted":
        approval_source = "codex-review"
        satisfied_by = "codex-review"
    elif separate_session_review.status == "accepted":
        approval_source = "separate-session-review"
        satisfied_by = "separate-session-review"
    else:
        blocking_reasons = _blocking_reasons(
            approval_status,
            codex_review,
            separate_session_review,
            latest_review.state if latest_review else None,
        )

    return ReviewPolicySummary(
        number=context.number,
        title=context.title,
        url=context.url,
        head_ref_oid=context.head_ref_oid,
        latest_review_sha=latest_review.commit_oid if latest_review else None,
        latest_review_state=latest_review.state if latest_review else None,
        latest_review_author=latest_review.author if latest_review else None,
        latest_approval_sha=latest_approval_sha,
        approval_status=approval_status,
        codex_review=codex_review,
        separate_session_review=separate_session_review,
        approval_source=approval_source,
        satisfied_by=satisfied_by,
        hard_gate_passed=not blocking_reasons,
        blocking_reason=blocking_reasons[0] if blocking_reasons else None,
        blocking_reasons=blocking_reasons,
        reviews=context.latest_reviews,
    )


def evaluate_codex_review(context: PullRequestContext) -> CodexReviewSummary:
    """Recognize a clean Codex review of the current head (ADR-007 path 1)."""

    if context.review_evidence_error:
        return CodexReviewSummary(
            status="invalid",
            comment_url=None,
            reviewed_commit_prefix=None,
            reason=f"Codex review cannot be verified: {context.review_evidence_error}",
        )

    bot_comments = [
        (index, event)
        for index, event in enumerate(context.timeline)
        if event.kind == "comment" and _is_codex_bot(event)
    ]
    clean_comments = [(index, event) for index, event in bot_comments if _codex_comment_kind(event.body) == "clean"]
    candidate_index, candidate = clean_comments[-1] if clean_comments else (None, None)
    comment_url = candidate.url if candidate else None
    prefix = _codex_reviewed_commit(candidate.body) if candidate else None

    not_applicable_reason = _codex_not_applicable_reason(context.linked_issues)
    if not_applicable_reason is not None:
        return CodexReviewSummary("not-applicable", comment_url, prefix, not_applicable_reason)

    if candidate is None or candidate_index is None or prefix is None:
        if bot_comments:
            latest_bot_comment = bot_comments[-1][1]
            kind = _codex_comment_kind(latest_bot_comment.body)
            detail = "a usage-limit reply" if kind == "usage-limit" else "in a format the recognizer does not know"
            return CodexReviewSummary(
                "invalid",
                latest_bot_comment.url,
                None,
                f"latest Codex comment is {detail}, not a clean review result",
            )
        return CodexReviewSummary(
            "missing",
            None,
            None,
            "no clean Codex review comment by the Codex bot exists on this PR",
        )

    if candidate.editor is not None and not _is_codex_actor(candidate.editor, candidate.editor_type):
        return CodexReviewSummary(
            "invalid",
            comment_url,
            prefix,
            f"clean Codex review comment was edited by another account ({candidate.editor})",
        )

    head_sha = context.head_ref_oid.lower()
    if not head_sha or not head_sha.startswith(prefix):
        return CodexReviewSummary(
            "stale",
            comment_url,
            prefix,
            f"clean Codex review names commit {prefix}, which is not the current PR head SHA",
        )

    matching_commits = {oid.lower() for oid in context.commit_oids if oid.lower().startswith(prefix)}
    if matching_commits != {head_sha}:
        return CodexReviewSummary(
            "ambiguous",
            comment_url,
            prefix,
            f"clean Codex review names commit prefix {prefix}, which matches more than one commit on the PR",
        )

    later_events = context.timeline[candidate_index + 1 :]
    if any(event.kind in {"commit", "force-push"} for event in later_events):
        return CodexReviewSummary(
            "stale",
            comment_url,
            prefix,
            "a commit or force-push was added to the PR after the clean Codex review comment",
        )

    for event in later_events:
        if not _is_codex_bot(event):
            continue
        if event.kind == "review":
            return CodexReviewSummary(
                "superseded",
                comment_url,
                prefix,
                "a newer Codex review submission follows the clean Codex review comment",
            )
        if event.kind == "comment" and _codex_comment_kind(event.body) not in {"clean", "usage-limit"}:
            return CodexReviewSummary(
                "superseded",
                comment_url,
                prefix,
                "a newer Codex comment that is not a clean result follows the clean Codex review comment",
            )

    return CodexReviewSummary("accepted", comment_url, prefix, None)


def evaluate_separate_session_review(context: PullRequestContext) -> SeparateSessionReviewSummary:
    """Recognize a SHA-bound separate-session review (ADR-007 path 2)."""

    stale_review: ReviewSummary | None = None

    for review in reversed(context.latest_reviews):
        if review.state != "COMMENTED":
            continue

        body = (review.body or "").strip()
        if not body:
            continue

        if review.commit_oid == context.head_ref_oid and _looks_like_separate_session_review(body):
            return _evaluate_current_head_separate_session_review(context, review)

        if review.commit_oid == context.head_ref_oid:
            continue

        if stale_review is None and _looks_like_separate_session_review(body):
            stale_review = review

    if stale_review is not None:
        return SeparateSessionReviewSummary(
            status="stale",
            review_author=stale_review.author,
            review_state=stale_review.state,
            review_commit_oid=stale_review.commit_oid,
            review_submitted_at=stale_review.submitted_at,
            reviewer_provider=reviewer_provider(stale_review.body),
            reason="separate-session review was recorded on a previous PR head SHA",
        )

    return SeparateSessionReviewSummary(
        status="missing",
        review_author=None,
        review_state=None,
        review_commit_oid=None,
        review_submitted_at=None,
    )


def is_accepted_separate_session_review(review: ReviewSummary, *, head_sha: str) -> bool:
    """Text-level check: required phrases plus a valid reviewing-provider line.

    The comparison with the implementing provider needs the PR context and is
    made by `evaluate_separate_session_review`.
    """

    return _has_required_phrases(review, head_sha=head_sha) and reviewer_provider(review.body) is not None


def separate_session_review_text(head_sha: str | None, reviewer_provider: str | None) -> str:
    """Return the separate-session review summary to post for a head SHA.

    The text is built from the phrase constants the recognizer checks, so a
    `COMMENTED` review on `head_sha` with this body always has the required
    phrases and a valid reviewing-provider line. Raises `ValueError` unless
    `head_sha` is a full 40-character hexadecimal SHA and `reviewer_provider`
    is one of `REVIEWER_PROVIDERS`.
    """

    if not head_sha or not _FULL_SHA.fullmatch(head_sha):
        raise ValueError("head SHA must be a full 40-character hexadecimal commit SHA")
    if reviewer_provider not in REVIEWER_PROVIDERS:
        raise ValueError(f"reviewer provider must be one of: {', '.join(REVIEWER_PROVIDERS)}")

    head_sha = head_sha.lower()
    return (
        f"Reviewed head SHA `{head_sha}`.\n\n"
        f"{NO_BLOCKERS_PHRASE.format(head_sha=head_sha)}.\n\n"
        f"{SEPARATE_SESSION_REVIEW_MARKER}.\n\n"
        f"{REVIEWER_PROVIDER_LABEL} {reviewer_provider}\n\n"
        f"This solo-maintainer override is the {APPROVAL_TO_RELY_ON_PHRASE}.\n"
    )


def reviewer_provider(body: str | None) -> str | None:
    """Return the stated reviewing provider, or None when it is absent, invalid or contradictory."""

    values = {match.group(1).lower() for match in _REVIEWER_PROVIDER_LINE.finditer(body or "")}
    if len(values) != 1:
        return None
    value = next(iter(values))
    return value if value in REVIEWER_PROVIDERS else None


def _evaluate_current_head_separate_session_review(
    context: PullRequestContext,
    review: ReviewSummary,
) -> SeparateSessionReviewSummary:
    provider = reviewer_provider(review.body)
    implementers: dict[str, int] = {}
    reason: str | None = None

    if not _has_required_phrases(review, head_sha=context.head_ref_oid):
        reason = "current-head review comment does not contain the required separate-session review phrases"
    elif provider is None:
        reason = (
            "separate-session review does not state a valid reviewing provider on its own line "
            "(`Reviewer provider: claude` or `Reviewer provider: codex`)"
        )
    elif context.review_evidence_error:
        reason = f"separate-session review cannot be verified: {context.review_evidence_error}"
    else:
        implementers = _implementer_providers(context.linked_issues)
        if provider in implementers:
            reason = (
                f"separate-session review states reviewing provider '{provider}', which is the provider that "
                f"implemented the PR (executor label on linked story #{implementers[provider]})"
            )

    return SeparateSessionReviewSummary(
        status="accepted" if reason is None else "invalid",
        review_author=review.author,
        review_state=review.state,
        review_commit_oid=review.commit_oid,
        review_submitted_at=review.submitted_at,
        reviewer_provider=provider,
        implementer_provider=",".join(sorted(implementers)) or None,
        reason=reason,
    )


def _latest_review_with_sha(context: PullRequestContext) -> ReviewSummary | None:
    for review in reversed(context.latest_reviews):
        if review.commit_oid:
            return review
    return None


def _approval_status(head_sha: str, approval_sha: str | None) -> str:
    if not head_sha:
        return "unknown-head"
    if not approval_sha:
        return "missing"
    if approval_sha == head_sha:
        return "current"
    return "stale"


def _looks_like_separate_session_review(body: str) -> bool:
    return "solo-maintainer override" in body.lower()


def _has_required_phrases(review: ReviewSummary, *, head_sha: str) -> bool:
    if review.state != "COMMENTED" or not head_sha or review.commit_oid != head_sha:
        return False

    body = (review.body or "").lower()
    return (
        SEPARATE_SESSION_REVIEW_MARKER in body
        and NO_BLOCKERS_PHRASE.format(head_sha=head_sha).lower() in body
        and APPROVAL_TO_RELY_ON_PHRASE.lower() in body
    )


def _executor_labels(issue: LinkedIssue) -> tuple[str, ...]:
    return tuple(label for label in issue.labels if label.lower().startswith(EXECUTOR_LABEL_PREFIX))


def _executor_provider(label: str) -> str | None:
    normalized = label.lower()
    if normalized.startswith(CLAUDE_EXECUTOR_LABEL_PREFIX):
        return "claude"
    if normalized == CODEX_EXECUTOR_LABEL:
        return "codex"
    return None


def _implementer_providers(linked_issues: tuple[LinkedIssue, ...]) -> dict[str, int]:
    providers: dict[str, int] = {}
    for issue in linked_issues:
        for label in _executor_labels(issue):
            provider = _executor_provider(label)
            if provider is not None:
                providers.setdefault(provider, issue.number)
    return providers


def _codex_not_applicable_reason(linked_issues: tuple[LinkedIssue, ...]) -> str | None:
    if not linked_issues:
        return (
            "a Codex review (path 1) needs a linked story to identify the implementer and this PR has none; "
            "a separate-session review (path 2) or a formal approval (path 3) is required"
        )
    for issue in linked_issues:
        labels = _executor_labels(issue)
        if len(labels) != 1:
            found = "no `executor:` label" if not labels else f"{len(labels)} `executor:` labels"
            return (
                f"a Codex review (path 1) needs exactly one `executor:` label on linked story #{issue.number}, "
                f"which carries {found}; path 2 or path 3 is required"
            )
        if _executor_provider(labels[0]) != "claude":
            return (
                f"a Codex review (path 1) counts only for Claude-implemented PRs and linked story #{issue.number} "
                f"carries `{labels[0]}`; path 2 or path 3 is required"
            )
    return None


def classify_codex_event(event: TimelineEvent) -> str | None:
    """Classify one Codex bot timeline event as one of `CODEX_EVENT_KINDS`.

    Returns None for an event that is not a comment or review by the Codex
    bot. A clean result and a usage-limit reply are PR conversation comments;
    findings are a PR review submission. Anything else by the bot is `unknown`.
    """

    if not _is_codex_bot(event):
        return None
    if event.kind == "comment":
        return _codex_comment_kind(event.body)
    if event.kind == "review":
        return _codex_review_kind(event.body)
    return None


def codex_reviewed_commit(event: TimelineEvent) -> str | None:
    """Return the lower-case commit prefix a Codex result names, or None when it names none or several."""

    return _codex_reviewed_commit(event.body)


def is_codex_review_request(event: TimelineEvent) -> bool:
    """True for a PR conversation comment by a non-bot account whose first line is the trigger phrase."""

    if event.kind != "comment" or event.author_type == "Bot":
        return False
    lines = (event.body or "").strip().splitlines()
    return bool(lines) and lines[0].strip().lower() == CODEX_REVIEW_TRIGGER_PHRASE


def _is_codex_actor(login: str | None, actor_type: str | None) -> bool:
    if actor_type != "Bot" or not login:
        return False
    normalized = login[: -len("[bot]")] if login.endswith("[bot]") else login
    return normalized == CODEX_BOT_LOGIN


def _is_codex_bot(event: TimelineEvent) -> bool:
    return _is_codex_actor(event.author, event.author_type)


def _codex_comment_kind(body: str | None) -> str:
    text = (body or "").strip()
    if _CODEX_CLEAN_START.match(text) and _codex_reviewed_commit(text) is not None:
        return "clean"
    if text.startswith(_CODEX_USAGE_LIMIT_START):
        return "usage-limit"
    return "unknown"


def _codex_review_kind(body: str | None) -> str:
    text = (body or "").strip()
    if text.startswith(_CODEX_FINDINGS_START) and _codex_reviewed_commit(text) is not None:
        return "findings"
    return "unknown"


def _codex_reviewed_commit(body: str | None) -> str | None:
    prefixes = {match.group(1).lower() for match in _CODEX_REVIEWED_COMMIT.finditer(body or "")}
    return next(iter(prefixes)) if len(prefixes) == 1 else None


def _blocking_reasons(
    approval_status: str,
    codex_review: CodexReviewSummary,
    separate_session_review: SeparateSessionReviewSummary,
    latest_review_state: str | None,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if separate_session_review.status in {"invalid", "stale"} and separate_session_review.reason:
        reasons.append(separate_session_review.reason)
    if approval_status == "stale":
        reasons.append("formal approval is stale for the current PR head SHA")
    if _codex_review_had_evidence(codex_review) and codex_review.reason:
        reasons.append(codex_review.reason)
    if reasons:
        return tuple(reasons)
    if latest_review_state == "COMMENTED":
        return (
            "comment-only review exists without formal approval, a clean Codex review "
            "or an accepted separate-session review",
        )
    return ("no formal approval, clean Codex review or accepted separate-session review exists for the current PR head SHA",)


def _codex_review_had_evidence(codex_review: CodexReviewSummary) -> bool:
    if codex_review.status in {"stale", "ambiguous", "superseded", "invalid"}:
        return True
    return codex_review.status == "not-applicable" and codex_review.comment_url is not None
