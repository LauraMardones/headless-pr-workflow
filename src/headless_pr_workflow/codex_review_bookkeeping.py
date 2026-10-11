"""Deterministic follow-up to a Codex review of a Claude-implemented PR (ADR-006).

The job reads the current state of one PR and decides what to do next: move the
story on the board and post the Session Summary, post a bounded review
re-request, or post a blocker. No model is involved. The decision is a pure
function of GitHub facts and the current time; every mutation is idempotent
through a hidden marker in the comment it posts, so all state lives on GitHub.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

from .ci_summary import CiSummary, summarize_ci
from .github import (
    GHCommandError,
    LinkedIssue,
    PullRequestContext,
    TimelineEvent,
    fetch_pr_context,
    fetch_required_status_check_context,
)
from .github.pr_context import _gh_env
from .review_policy import (
    CLAUDE_EXECUTOR_LABEL_PREFIX,
    CODEX_REVIEW_TRIGGER_PHRASE,
    EXECUTOR_LABEL_PREFIX,
    classify_codex_event,
    codex_reviewed_commit,
    evaluate_codex_review,
    is_codex_review_request,
)


COMMAND_NAME = "codex-review-bookkeeping"
STATUS_IN_REVIEW = "In review"
STATUS_IN_MERGE = "In merge"
STATUS_IN_IMPLEMENTATION = "In implementation"

ACTION_MOVE_IN_MERGE = "move-in-merge"
ACTION_MOVE_IN_IMPLEMENTATION = "move-in-implementation"
ACTION_REQUEST_REVIEW = "request-review"
ACTION_BLOCKER_RESOURCE = "blocker-resource"
ACTION_BLOCKER_EXTERNAL = "blocker-external"
ACTION_NONE = "none"
MOVE_TARGETS = {
    ACTION_MOVE_IN_MERGE: STATUS_IN_MERGE,
    ACTION_MOVE_IN_IMPLEMENTATION: STATUS_IN_IMPLEMENTATION,
}

APPLIED = "applied"
ALREADY_PRESENT = "already-present"
DRY_RUN = "dry-run"
NOT_NEEDED = "not-needed"

RETRY_HOURS_ENV = "CODEX_REVIEW_RETRY_HOURS"
MAX_REQUESTS_ENV = "CODEX_REVIEW_MAX_REQUESTS"
DEFAULT_RETRY_HOURS = 5.0
DEFAULT_MAX_REQUESTS = 3

MARKER_PREFIX = f"<!-- hpw:{COMMAND_NAME}"
AUTOMATION_DEVIATION = f"produced by the {COMMAND_NAME} job without a model session"
DECLARED_BY = f"{COMMAND_NAME} (GitHub Actions)"
BLOCKER_OWNER = "PO (@LauraMardones)"
SESSION_SUMMARY_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "session-summary.sh"

STORY_BOARD_QUERY = """
query($owner: String!, $name: String!, $number: Int!) {
  repository(owner: $owner, name: $name) {
    issue(number: $number) {
      title
      projectItems(first: 20) {
        pageInfo { hasNextPage }
        nodes {
          id
          isArchived
          project { id }
          fieldValueByName(name: "Status") {
            ... on ProjectV2ItemFieldSingleSelectValue {
              name
              field { ... on ProjectV2SingleSelectField { id options { id name } } }
            }
          }
        }
      }
    }
  }
}
"""
SET_STATUS_MUTATION = """
mutation($projectId: ID!, $itemId: ID!, $fieldId: ID!, $optionId: String!) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $projectId
    itemId: $itemId
    fieldId: $fieldId
    value: { singleSelectOptionId: $optionId }
  }) { projectV2Item { id } }
}
"""


class BookkeepingError(RuntimeError):
    """A read was incomplete, a board option is missing or a mutation failed."""

    def __init__(self, step: str, message: str) -> None:
        self.step = step
        self.message = message
        super().__init__(f"{step}: {message}")


@dataclass(frozen=True)
class BookkeepingConfig:
    retry_hours: float = DEFAULT_RETRY_HOURS
    max_requests: int = DEFAULT_MAX_REQUESTS

    @property
    def back_off(self) -> timedelta:
        return timedelta(hours=self.retry_hours)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "BookkeepingConfig":
        """Read the bounds from the environment; an unset or empty variable keeps its default."""

        env = os.environ if env is None else env
        retry_text = (env.get(RETRY_HOURS_ENV) or "").strip()
        max_text = (env.get(MAX_REQUESTS_ENV) or "").strip()
        try:
            retry_hours = float(retry_text) if retry_text else DEFAULT_RETRY_HOURS
        except ValueError:
            retry_hours = -1.0
        # `not >= 0` also rejects NaN.
        if not retry_hours >= 0 or retry_hours == float("inf"):
            raise BookkeepingError("read configuration", f"{RETRY_HOURS_ENV} must be a number of hours, 0 or more")
        try:
            max_requests = int(max_text) if max_text else DEFAULT_MAX_REQUESTS
        except ValueError:
            max_requests = -1
        if max_requests < 0:
            raise BookkeepingError("read configuration", f"{MAX_REQUESTS_ENV} must be a whole number, 0 or more")
        return cls(retry_hours=retry_hours, max_requests=max_requests)


@dataclass(frozen=True)
class StoryBoard:
    """A story's title and its position on the project board."""

    number: int
    title: str
    status: str | None
    item_id: str | None = None
    project_id: str | None = None
    field_id: str | None = None
    options: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    pr: int
    head_sha: str
    story: int | None
    story_status: str | None
    codex_result: str
    codex_url: str | None
    action: str
    reason: str
    requests_posted: int
    max_requests: int
    # Hidden marker that makes the action's comment idempotent; None for `none`.
    marker: str | None = None
    summary_checks: str | None = None
    summary_blockers: str | None = None
    blocker_unblocked_when: str | None = None

    @property
    def summary_next(self) -> str | None:
        if self.action == ACTION_MOVE_IN_MERGE:
            return "merge"
        if self.action == ACTION_MOVE_IN_IMPLEMENTATION:
            return "implementation"
        return None


@dataclass(frozen=True)
class Outcome:
    decision: Decision
    applied: str
    comment_urls: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        decision = self.decision
        return {
            "pr": decision.pr,
            "head_sha": decision.head_sha,
            "story": decision.story,
            "story_status": decision.story_status,
            "codex_result": {"result": decision.codex_result, "url": decision.codex_url},
            "action": decision.action,
            "reason": decision.reason,
            "requests_posted": decision.requests_posted,
            "max_requests": decision.max_requests,
            "applied": {"state": self.applied, "comment_urls": list(self.comment_urls)},
        }

    def to_line(self) -> str:
        decision = self.decision
        return (
            f"PR #{decision.pr}: result={decision.codex_result} action={decision.action} "
            f"applied={self.applied} - {decision.reason}"
        )


class GitHubGateway(Protocol):
    """Everything the job reads from or writes to GitHub, so tests can replace it."""

    def fetch_context(self, pr: int) -> PullRequestContext: ...

    def fetch_ci(self, context: PullRequestContext) -> CiSummary: ...

    def fetch_story(self, number: int) -> StoryBoard: ...

    def fetch_issue_comments(self, number: int) -> tuple[str, ...]: ...

    def post_comment(self, number: int, body: str) -> str: ...

    def set_story_status(self, story: StoryBoard, status: str) -> None: ...

    def session_summary(self, arguments: tuple[str, ...]) -> str: ...

    def list_open_ready_prs(self) -> tuple[int, ...]: ...


def action_marker(action: str, head_sha: str, *, source: str | None = None) -> str:
    """Hidden marker naming the PR head and the kind of action a comment records."""

    suffix = f" source={source}" if source else ""
    return f"{MARKER_PREFIX} action={action} head={head_sha}{suffix} -->"


def review_request_body(head_sha: str, *, ordinal: int) -> str:
    """A review request posted by the job: the trigger phrase first, the marker on a later line."""

    marker = action_marker(ACTION_REQUEST_REVIEW, head_sha, source=f"request-{ordinal}")
    return f"{CODEX_REVIEW_TRIGGER_PHRASE}\n\n{marker}\n"


def linked_story_or_reason(context: PullRequestContext) -> tuple[LinkedIssue | None, str | None]:
    """Return the one Claude-implemented story the PR closes, or why the job does not act."""

    if context.state != "OPEN":
        return None, f"PR is {context.state.lower() or 'not open'}, not open."
    if context.is_draft:
        return None, "PR is a draft."
    if not context.linked_issues:
        return None, "PR has no linked story."
    if len(context.linked_issues) != 1:
        numbers = ", ".join(f"#{issue.number}" for issue in context.linked_issues)
        return None, f"PR has more than one linked story ({numbers})."
    story = context.linked_issues[0]
    labels = [label for label in story.labels if label.lower().startswith(EXECUTOR_LABEL_PREFIX)]
    if not labels:
        return None, f"Linked story #{story.number} has no `executor:` label."
    if len(labels) != 1:
        return None, f"Linked story #{story.number} has {len(labels)} `executor:` labels."
    if not labels[0].lower().startswith(CLAUDE_EXECUTOR_LABEL_PREFIX):
        return None, f"Linked story #{story.number} carries `{labels[0]}`, so `/review` handles it, not this job."
    return story, None


def decide(
    context: PullRequestContext,
    *,
    story_status: str | None,
    ci: CiSummary | None,
    now: datetime,
    config: BookkeepingConfig = BookkeepingConfig(),
) -> Decision:
    """Decide the one action for a PR from GitHub facts and the current time.

    Raises `BookkeepingError` when the facts are incomplete, so the job never
    acts on a partial read.
    """

    if context.review_evidence_error:
        raise BookkeepingError("read PR", context.review_evidence_error)

    head_sha = context.head_ref_oid
    head_events = _current_head_events(context)
    requests = [event for event in head_events if is_codex_review_request(event)]
    request_marker = f"{MARKER_PREFIX} action={ACTION_REQUEST_REVIEW} head={head_sha} "
    requests_posted = sum(1 for event in requests if request_marker in (event.body or ""))
    story, reason = linked_story_or_reason(context)
    sole_linked = context.linked_issues[0].number if len(context.linked_issues) == 1 else None

    def result(action: str, codex_result: str, codex_url: str | None, why: str, **extra: Any) -> Decision:
        return Decision(
            pr=context.number,
            head_sha=head_sha,
            story=sole_linked,
            story_status=story_status,
            codex_result=codex_result,
            codex_url=codex_url,
            action=action,
            reason=why,
            requests_posted=requests_posted,
            max_requests=config.max_requests,
            **extra,
        )

    codex_result, codex_event = _codex_result(context, head_events)
    codex_url = codex_event.url if codex_event else None

    if story is None:
        return result(ACTION_NONE, codex_result, codex_url, reason or "PR has no linked story.")
    if story_status != STATUS_IN_REVIEW:
        shown = f"`{story_status}`" if story_status else "not set on the project board"
        return result(
            ACTION_NONE,
            codex_result,
            codex_url,
            f"Story #{story.number} is {shown}, not `{STATUS_IN_REVIEW}`.",
        )
    if not head_sha:
        raise BookkeepingError("read PR", "current PR head SHA is unknown")

    def request_or_blocker(why: str) -> Decision:
        if requests_posted >= config.max_requests:
            marker = action_marker(ACTION_BLOCKER_RESOURCE, head_sha)
            return result(
                ACTION_BLOCKER_RESOURCE,
                codex_result,
                codex_url,
                f"{why} The job already posted {requests_posted} of {config.max_requests} review requests for this head.",
                marker=marker,
                blocker_unblocked_when=(
                    f"Codex posts a review result for head {head_sha} on PR #{context.number}; "
                    "after the Codex usage limit has reset, a new review request comment on the PR starts one"
                ),
            )
        return result(ACTION_REQUEST_REVIEW, codex_result, codex_url, why)

    if codex_result == "clean":
        return _clean_decision(context, ci, result, codex_url)

    if codex_result == "findings":
        return result(
            ACTION_MOVE_IN_IMPLEMENTATION,
            codex_result,
            codex_url,
            "Codex submitted a review with findings for the current head.",
            marker=action_marker(ACTION_MOVE_IN_IMPLEMENTATION, head_sha, source=codex_url),
            summary_checks=f"Codex review of the current head: findings ({codex_url})",
            summary_blockers=f"Codex review findings: {codex_url}",
        )

    if codex_result == "unknown":
        detail = evaluate_codex_review(context).reason if _is_clean(codex_event) else None
        why = f"The latest Codex reply ({codex_url}) is not a result the recognizer accepts"
        return result(
            ACTION_BLOCKER_EXTERNAL,
            codex_result,
            codex_url,
            f"{why}: {detail}." if detail else f"{why}: its format is unknown.",
            marker=action_marker(ACTION_BLOCKER_EXTERNAL, head_sha, source=codex_url),
            blocker_unblocked_when=(
                f"the Codex reply at {codex_url} is understood (the recognizer in "
                f"`src/headless_pr_workflow/review_policy.py` knows its format) or a newer Codex review result "
                f"for head {head_sha} is posted on PR #{context.number}"
            ),
        )

    if codex_result == "usage-limit":
        if not _back_off_passed(codex_event, now, config):
            return result(
                ACTION_NONE,
                codex_result,
                codex_url,
                f"Codex hit its usage limit; a new request is posted {config.retry_hours:g} hours after that reply.",
            )
        return request_or_blocker(f"Codex hit its usage limit and the {config.retry_hours:g}-hour back-off has passed.")

    if codex_result == "pending":
        if not _back_off_passed(codex_event, now, config):
            return result(
                ACTION_NONE,
                codex_result,
                codex_url,
                "A review request for the current head is pending and the back-off has not passed.",
            )
        return request_or_blocker(
            f"The latest review request got no answer within the {config.retry_hours:g}-hour back-off."
        )

    return request_or_blocker("No review request exists for the current head.")


def run_bookkeeping(
    pr: int,
    *,
    github: GitHubGateway,
    now: datetime,
    config: BookkeepingConfig = BookkeepingConfig(),
    dry_run: bool = False,
) -> Outcome:
    """Read one PR fresh, decide and apply the decision."""

    context = github.fetch_context(pr)
    if context.review_evidence_error:
        raise BookkeepingError("read PR", context.review_evidence_error)
    linked, _ = linked_story_or_reason(context)

    story: StoryBoard | None = None
    ci: CiSummary | None = None
    story_comments: tuple[str, ...] = ()
    if linked is not None:
        story = github.fetch_story(linked.number)
        if story.status == STATUS_IN_REVIEW:
            ci = github.fetch_ci(context)
            story_comments = github.fetch_issue_comments(linked.number)

    decision = decide(
        context,
        story_status=story.status if story else None,
        ci=ci,
        now=now,
        config=config,
    )
    return _apply(decision, context=context, story=story, story_comments=story_comments, github=github, dry_run=dry_run)


def _apply(
    decision: Decision,
    *,
    context: PullRequestContext,
    story: StoryBoard | None,
    story_comments: tuple[str, ...],
    github: GitHubGateway,
    dry_run: bool,
) -> Outcome:
    if decision.action == ACTION_NONE:
        return Outcome(decision, NOT_NEEDED)
    if story is None or (decision.marker is None and decision.action != ACTION_REQUEST_REVIEW):
        raise BookkeepingError("apply decision", f"decision `{decision.action}` is incomplete")

    if decision.action in MOVE_TARGETS:
        target = MOVE_TARGETS[decision.action]
        # Checked before any mutation, so a missing option never leaves a summary without its move.
        if not story.item_id or not story.project_id or not story.field_id or target not in story.options:
            raise BookkeepingError("look up board option", f"the project board has no `{target}` status option")
        summary_present = any(decision.marker in (event.body or "") for event in context.conversation_comments)
        if dry_run:
            return Outcome(decision, DRY_RUN)
        current = github.fetch_story(story.number)
        if current.status != STATUS_IN_REVIEW:
            raise BookkeepingError(
                "move story",
                f"story #{story.number} left `{STATUS_IN_REVIEW}` during the run; nothing was posted or moved",
            )
        urls: tuple[str, ...] = ()
        if not summary_present:
            summary = github.session_summary(_session_summary_arguments(decision))
            urls = (github.post_comment(decision.pr, f"{summary}\n{decision.marker}\n"),)
        github.set_story_status(story, target)
        return Outcome(decision, APPLIED, urls)

    if decision.action == ACTION_REQUEST_REVIEW:
        if dry_run:
            return Outcome(decision, DRY_RUN)
        body = review_request_body(decision.head_sha, ordinal=decision.requests_posted + 1)
        return Outcome(decision, APPLIED, (github.post_comment(decision.pr, body),))

    if any(decision.marker in body for body in story_comments):
        return Outcome(decision, ALREADY_PRESENT)
    if dry_run:
        return Outcome(decision, DRY_RUN)
    return Outcome(decision, APPLIED, (github.post_comment(story.number, blocker_body(decision, story)),))


def blocker_body(decision: Decision, story: StoryBoard) -> str:
    """A Blocked Declaration in the format of `docs/PROJECT-STATUS.md`, posted on the story."""

    blocker_type = "resource" if decision.action == ACTION_BLOCKER_RESOURCE else "external"
    return (
        "## Blocked Declaration\n"
        f"Type: {blocker_type}\n"
        f"Declared by: {DECLARED_BY}\n"
        f"Blocks: #{story.number} — {story.title}\n"
        f"Unblocked when: {decision.blocker_unblocked_when}\n"
        f"Owner: {BLOCKER_OWNER}\n"
        f"State of in-progress work: PR #{decision.pr} at head {decision.head_sha}; "
        f"the story stays in `{STATUS_IN_REVIEW}`. {decision.reason}\n"
        "Resume instruction: n/a\n"
        f"{decision.marker}\n"
    )


def _session_summary_arguments(decision: Decision) -> tuple[str, ...]:
    return (
        "--command", "review",
        "--issue", str(decision.story),
        "--pr", str(decision.pr),
        "--head", decision.head_sha,
        "--checks", decision.summary_checks or "none",
        "--blockers", decision.summary_blockers or "none",
        "--next", decision.summary_next or "implementation",
        "--deviation", AUTOMATION_DEVIATION,
    )  # fmt: skip


def _clean_decision(context: PullRequestContext, ci: CiSummary | None, result: Any, codex_url: str | None) -> Decision:
    if ci is None:
        raise BookkeepingError("read required checks", "required check state was not read")
    status = ci.required_check_status
    names = ", ".join(ci.required_checks.names) or "none configured"
    if ci.required_checks_satisfied or status == "not_configured":
        return result(
            ACTION_MOVE_IN_MERGE,
            "clean",
            codex_url,
            "Codex found no major issues on the current head and the required checks are green.",
            marker=action_marker(ACTION_MOVE_IN_MERGE, context.head_ref_oid, source=codex_url),
            summary_checks=f"Codex review of the current head: clean ({codex_url}); required checks: {status} ({names})",
            summary_blockers="none",
        )
    if status in {"pending", "missing"}:
        return result(
            ACTION_NONE,
            "clean",
            codex_url,
            f"Codex found no major issues, but a required check is still running ({status}: {names}).",
        )
    if status == "failing":
        failing = set(ci.check_buckets.get("failing", ()))
        failed = ", ".join(name for name in ci.required_checks.names if name in failing) or names
        return result(
            ACTION_MOVE_IN_IMPLEMENTATION,
            "clean",
            codex_url,
            f"Codex found no major issues, but a required check failed: {failed}.",
            marker=action_marker(ACTION_MOVE_IN_IMPLEMENTATION, context.head_ref_oid, source="required-checks"),
            summary_checks=f"Codex review of the current head: clean ({codex_url}); required checks: failing ({failed})",
            summary_blockers=f"required check failed: {failed}",
        )
    detail = "; ".join(ci.messages) or status
    raise BookkeepingError("read required checks", f"required check state is `{status}`: {detail}")


def _current_head_events(context: PullRequestContext) -> tuple[TimelineEvent, ...]:
    """Events after the last commit or force-push: the ordering rule the recognizer uses."""

    last_push = -1
    for index, event in enumerate(context.timeline):
        if event.kind in {"commit", "force-push"}:
            last_push = index
    return context.timeline[last_push + 1 :]


def _codex_result(
    context: PullRequestContext,
    head_events: tuple[TimelineEvent, ...],
) -> tuple[str, TimelineEvent | None]:
    """The Codex result for the current head and the event it was read from.

    `clean` exactly when the approval gate's recognizer accepts it, so the job
    and the gate cannot disagree. A clean comment it rejects for another reason
    than naming an older commit is `unknown`.
    """

    accepted = evaluate_codex_review(context)
    if accepted.status == "accepted":
        clean = [event for event in head_events if _is_clean(event) and event.url == accepted.comment_url]
        return "clean", clean[-1] if clean else TimelineEvent(kind="comment", url=accepted.comment_url)

    head_sha = context.head_ref_oid.lower()
    latest_index = -1
    latest: TimelineEvent | None = None
    latest_request_index = -1
    latest_request: TimelineEvent | None = None
    for index, event in enumerate(head_events):
        if is_codex_review_request(event):
            latest_request_index, latest_request = index, event
            continue
        kind = classify_codex_event(event)
        if kind is None or _names_older_commit(event, kind, head_sha):
            continue
        latest_index, latest = index, event

    if latest is None or latest_request_index > latest_index:
        return ("pending", latest_request) if latest_request else ("none", None)

    kind = classify_codex_event(latest) or "unknown"
    # A clean comment reaching this point was rejected by the recognizer.
    return ("unknown" if kind == "clean" else kind), latest


def _names_older_commit(event: TimelineEvent, kind: str, head_sha: str) -> bool:
    if kind not in {"clean", "findings"}:
        return False
    if event.kind == "review" and event.oid:
        return event.oid.lower() != head_sha
    prefix = codex_reviewed_commit(event)
    return prefix is not None and not head_sha.startswith(prefix)


def _is_clean(event: TimelineEvent | None) -> bool:
    return event is not None and classify_codex_event(event) == "clean"


def _back_off_passed(event: TimelineEvent | None, now: datetime, config: BookkeepingConfig) -> bool:
    created_at = _parse_time(event.created_at if event else None)
    return now - created_at >= config.back_off


def _parse_time(value: str | None) -> datetime:
    try:
        parsed = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
    except ValueError:
        raise BookkeepingError("read PR", f"a comment timestamp could not be read: {value!r}") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class GhCliGateway:
    """`GitHubGateway` backed by the authenticated `gh` CLI."""

    def __init__(self, repo: str) -> None:
        owner, _, name = repo.partition("/")
        if not owner or not name or "/" in name:
            raise BookkeepingError("read arguments", "--repo must be in OWNER/REPO format")
        self.repo = repo
        self.owner = owner
        self.name = name

    def fetch_context(self, pr: int) -> PullRequestContext:
        try:
            return fetch_pr_context(str(pr), repo=self.repo)
        except GHCommandError as error:
            raise BookkeepingError("read PR", str(error)) from error

    def fetch_ci(self, context: PullRequestContext) -> CiSummary:
        try:
            required_checks = fetch_required_status_check_context(self.repo, context.base_ref_name)
        except GHCommandError as error:
            raise BookkeepingError("read required checks", str(error)) from error
        return summarize_ci(context, required_checks=required_checks)

    def fetch_story(self, number: int) -> StoryBoard:
        step = "read story board state"
        payload = self._json(
            step,
            ["api", "graphql", "-f", f"query={STORY_BOARD_QUERY}", "-f", f"owner={self.owner}",
             "-f", f"name={self.name}", "-F", f"number={number}"],
        )  # fmt: skip
        return parse_story_board(payload, number=number, step=step)

    def fetch_issue_comments(self, number: int) -> tuple[str, ...]:
        step = "read story comments"
        output = self._run(
            step,
            ["api", f"repos/{self.repo}/issues/{number}/comments", "--paginate", "--jq", ".[] | {body}"],
        )
        bodies: list[str] = []
        for line in output.splitlines():
            if not line.strip():
                continue
            try:
                bodies.append(str(json.loads(line).get("body") or ""))
            except (json.JSONDecodeError, AttributeError):
                raise BookkeepingError(step, "GitHub returned a comment that could not be parsed") from None
        return tuple(bodies)

    def post_comment(self, number: int, body: str) -> str:
        step = f"post comment on #{number}"
        payload = self._json(
            step,
            ["api", f"repos/{self.repo}/issues/{number}/comments", "--method", "POST", "--input", "-"],
            stdin=json.dumps({"body": body}),
        )
        url = payload.get("html_url")
        if not isinstance(url, str) or not url:
            raise BookkeepingError(step, "GitHub did not confirm the comment")
        return url

    def set_story_status(self, story: StoryBoard, status: str) -> None:
        step = f"move story #{story.number} to `{status}`"
        payload = self._json(
            step,
            ["api", "graphql", "-f", f"query={SET_STATUS_MUTATION}", "-f", f"projectId={story.project_id}",
             "-f", f"itemId={story.item_id}", "-f", f"fieldId={story.field_id}",
             "-f", f"optionId={story.options[status]}"],
        )  # fmt: skip
        item = (((payload.get("data") or {}).get("updateProjectV2ItemFieldValue") or {}).get("projectV2Item") or {})
        if payload.get("errors") or not item.get("id"):
            raise BookkeepingError(step, "GitHub did not confirm the board update")

    def session_summary(self, arguments: tuple[str, ...]) -> str:
        return run_session_summary(arguments)

    def list_open_ready_prs(self) -> tuple[int, ...]:
        step = "list open pull requests"
        output = self._run(
            step,
            ["pr", "list", "--repo", self.repo, "--state", "open", "--limit", "1000", "--json", "number,isDraft"],
        )
        try:
            return tuple(sorted(int(pr["number"]) for pr in json.loads(output) if not pr.get("isDraft")))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            raise BookkeepingError(step, "GitHub returned a pull request list that could not be parsed") from None

    def _json(self, step: str, arguments: list[str], *, stdin: str | None = None) -> dict[str, Any]:
        output = self._run(step, arguments, stdin=stdin)
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            payload = None
        if not isinstance(payload, dict):
            raise BookkeepingError(step, "GitHub returned a response that could not be parsed")
        return payload

    def _run(self, step: str, arguments: list[str], *, stdin: str | None = None) -> str:
        try:
            result = subprocess.run(
                ["gh", *arguments], input=stdin, capture_output=True, encoding="utf-8", check=False, env=_gh_env()
            )
        except FileNotFoundError:
            raise BookkeepingError(step, "GitHub CLI executable not found: gh") from None
        if result.returncode != 0:
            detail = (result.stderr or "").strip().splitlines()
            raise BookkeepingError(step, detail[-1] if detail else f"gh exited with code {result.returncode}")
        return result.stdout or ""


def parse_story_board(payload: dict[str, Any], *, number: int, step: str = "read story board state") -> StoryBoard:
    """Normalize the `STORY_BOARD_QUERY` response; fail when it is partial or ambiguous."""

    issue = (((payload.get("data") or {}).get("repository") or {}).get("issue")) if not payload.get("errors") else None
    items = issue.get("projectItems") if isinstance(issue, dict) else None
    nodes = items.get("nodes") if isinstance(items, dict) else None
    if not isinstance(nodes, list) or (items.get("pageInfo") or {}).get("hasNextPage") is not False:
        raise BookkeepingError(step, f"project items of story #{number} could not be read completely")
    title = str(issue.get("title") or "")
    active = [node for node in nodes if isinstance(node, dict) and not node.get("isArchived")]
    if not active:
        return StoryBoard(number=number, title=title, status=None)
    if len(active) != 1:
        raise BookkeepingError(step, f"story #{number} is on {len(active)} project boards; expected one")

    item = active[0]
    value = item.get("fieldValueByName") or {}
    status_field = value.get("field") or {}
    options = status_field.get("options") or []
    return StoryBoard(
        number=number,
        title=title,
        status=value.get("name"),
        item_id=item.get("id"),
        project_id=(item.get("project") or {}).get("id"),
        field_id=status_field.get("id"),
        options={option["name"]: option["id"] for option in options if isinstance(option, dict) and option.get("name")},
    )


def run_session_summary(arguments: tuple[str, ...]) -> str:
    """Return the exact stdout of `scripts/session-summary.sh` for the arguments."""

    step = "generate Session Summary"
    try:
        # Fed on stdin with LF endings so a CRLF checkout on Windows runs it too.
        script = SESSION_SUMMARY_SCRIPT.read_bytes().replace(b"\r\n", b"\n")
        result = subprocess.run(["bash", "-s", "--", *arguments], input=script, capture_output=True, check=False)
    except OSError as error:
        raise BookkeepingError(step, f"scripts/session-summary.sh could not be run: {error}") from error
    stdout = result.stdout.decode("utf-8")
    if result.returncode != 0 or not stdout.strip():
        detail = result.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise BookkeepingError(step, detail[0] if detail else f"session-summary.sh exited with code {result.returncode}")
    return stdout
