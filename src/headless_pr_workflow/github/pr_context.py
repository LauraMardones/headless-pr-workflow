"""GitHub PR context fetching and normalization."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PR_CONTEXT_FIELDS: tuple[str, ...] = (
    "additions",
    "baseRefName",
    "baseRefOid",
    "changedFiles",
    "closed",
    "createdAt",
    "deletions",
    "headRefName",
    "headRefOid",
    "headRepository",
    "headRepositoryOwner",
    "isCrossRepository",
    "isDraft",
    "labels",
    "latestReviews",
    "maintainerCanModify",
    "mergeStateStatus",
    "mergeable",
    "number",
    "reviewDecision",
    "reviewRequests",
    "reviews",
    "state",
    "statusCheckRollup",
    "title",
    "updatedAt",
    "url",
)


class GHCommandError(RuntimeError):
    """Raised when a GitHub CLI command fails."""

    def __init__(self, command: list[str], returncode: int | None, stderr: str, *, error: str = "gh-command-failed") -> None:
        self.command = command
        self.returncode = returncode
        self.error = error
        self.stderr = stderr.strip()
        if returncode is None:
            super().__init__(self.stderr)
        else:
            super().__init__(f"GitHub CLI failed with exit code {returncode}: {self.stderr}")


@dataclass(frozen=True)
class ReviewSummary:
    author: str | None
    state: str | None
    submitted_at: str | None
    commit_oid: str | None
    body: str | None = None
    source_surface: str | None = None
    source_surfaces: tuple[str, ...] = ()

    @classmethod
    def from_raw(cls, review: dict[str, Any], *, source_surface: str | None = None) -> "ReviewSummary":
        author = review.get("author")
        source_surfaces = () if source_surface is None else (source_surface,)
        return cls(
            author=_login(author),
            state=review.get("state"),
            submitted_at=review.get("submittedAt"),
            commit_oid=_nested_get(review, "commit", "oid"),
            body=review.get("body"),
            source_surface=source_surface,
            source_surfaces=source_surfaces,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CheckSummary:
    name: str | None
    workflow: str | None
    status: str | None
    conclusion: str | None
    state: str | None
    bucket: str
    url: str | None

    @classmethod
    def from_raw(cls, check: dict[str, Any]) -> "CheckSummary":
        status = check.get("status")
        conclusion = check.get("conclusion")
        state = check.get("state")
        return cls(
            name=check.get("name") or check.get("context") or check.get("displayName"),
            workflow=check.get("workflowName") or _nested_get(check, "workflow", "name"),
            status=status,
            conclusion=conclusion,
            state=state,
            bucket=_check_bucket(status=status, conclusion=conclusion, state=state),
            url=check.get("detailsUrl") or check.get("targetUrl") or check.get("url"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RequiredStatusChecks:
    names: tuple[str, ...]
    status: str
    source: str = "branch-protection"
    message: str | None = None

    @property
    def available(self) -> bool:
        return self.status != "unavailable"

    @property
    def configured(self) -> bool:
        return bool(self.names)

    def to_dict(self) -> dict[str, Any]:
        return {
            "names": list(self.names),
            "status": self.status,
            "source": self.source,
            "available": self.available,
            "configured": self.configured,
            "message": self.message,
        }


@dataclass(frozen=True)
class PullRequestContext:
    number: int
    title: str
    state: str
    url: str
    base_ref_name: str
    base_ref_oid: str | None
    head_ref_name: str
    head_ref_oid: str
    head_repository: str | None
    head_repository_owner: str | None
    is_cross_repository: bool
    is_draft: bool
    merge_state_status: str | None
    mergeable: str | None
    review_decision: str | None
    changed_files: int | None
    additions: int | None
    deletions: int | None
    labels: tuple[str, ...]
    latest_reviews: tuple[ReviewSummary, ...]
    review_requests: tuple[str, ...]
    status_checks: tuple[CheckSummary, ...]
    raw: dict[str, Any]

    @property
    def latest_approval_sha(self) -> str | None:
        for review in reversed(self.latest_reviews):
            if review.state == "APPROVED" and review.commit_oid:
                return review.commit_oid
        return None

    @property
    def check_counts(self) -> dict[str, int]:
        counts = {"success": 0, "failure": 0, "pending": 0, "skipped": 0, "unknown": 0}
        for check in self.status_checks:
            counts[check.bucket] = counts.get(check.bucket, 0) + 1
        return counts

    def to_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        payload = {
            "number": self.number,
            "title": self.title,
            "state": self.state,
            "url": self.url,
            "base_ref_name": self.base_ref_name,
            "base_ref_oid": self.base_ref_oid,
            "head_ref_name": self.head_ref_name,
            "head_ref_oid": self.head_ref_oid,
            "head_repository": self.head_repository,
            "head_repository_owner": self.head_repository_owner,
            "is_cross_repository": self.is_cross_repository,
            "is_draft": self.is_draft,
            "merge_state_status": self.merge_state_status,
            "mergeable": self.mergeable,
            "review_decision": self.review_decision,
            "changed_files": self.changed_files,
            "additions": self.additions,
            "deletions": self.deletions,
            "labels": list(self.labels),
            "latest_approval_sha": self.latest_approval_sha,
            "latest_reviews": [review.to_dict() for review in self.latest_reviews],
            "review_requests": list(self.review_requests),
            "status_checks": [check.to_dict() for check in self.status_checks],
            "check_counts": self.check_counts,
        }
        if include_raw:
            payload["raw"] = self.raw
        return payload


def fetch_pr_context(target: str | None = None, *, repo: str | None = None) -> PullRequestContext:
    """Fetch PR context from GitHub using the GitHub CLI."""

    command = ["gh", "pr", "view"]
    if target:
        command.append(target)
    if repo:
        command.extend(["--repo", repo])
    command.extend(["--json", ",".join(PR_CONTEXT_FIELDS)])

    try:
        result = subprocess.run(command, capture_output=True, encoding="utf-8", check=False, env=_gh_env())
    except FileNotFoundError as error:
        raise GHCommandError(command, None, "GitHub CLI executable not found: gh", error="gh-not-found") from error

    if result.returncode != 0:
        raise GHCommandError(command, result.returncode, result.stderr)

    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise GHCommandError(command, result.returncode, f"GitHub CLI returned invalid JSON: {error.msg}", error="gh-invalid-json") from error

    try:
        return parse_pr_context(raw)
    except (KeyError, TypeError, ValueError) as error:
        raise GHCommandError(command, result.returncode, f"GitHub PR payload could not be parsed: {error}", error="gh-parse-failed") from error


def fetch_repo_default_branch(repo: str | None = None) -> str:
    """Fetch the repository default branch from GitHub."""

    command = ["gh", "repo", "view"]
    if repo:
        command.append(repo)
    command.extend(["--json", "defaultBranchRef"])

    try:
        result = subprocess.run(command, capture_output=True, encoding="utf-8", check=False, env=_gh_env())
    except FileNotFoundError as error:
        raise GHCommandError(command, None, "GitHub CLI executable not found: gh", error="gh-not-found") from error

    if result.returncode != 0:
        raise GHCommandError(command, result.returncode, result.stderr)

    try:
        raw = json.loads(result.stdout)
        default_branch_ref = raw["defaultBranchRef"]
        branch_name = default_branch_ref["name"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise GHCommandError(command, result.returncode, f"GitHub repo payload could not be parsed: {error}", error="gh-parse-failed") from error

    if not branch_name:
        raise GHCommandError(command, result.returncode, "GitHub repo payload did not include a default branch name.", error="gh-parse-failed")

    return branch_name


def fetch_required_status_checks(repo: str, branch: str) -> tuple[str, ...]:
    return fetch_required_status_check_context(repo, branch).names


def fetch_required_status_check_context(repo: str, branch: str) -> RequiredStatusChecks:
    """Fetch required status check names for a branch when GitHub exposes them.

    Repositories without branch protection support cannot require checks through
    this GitHub feature, so those cases return an empty set of required checks.
    """

    command = ["gh", "api", f"repos/{repo}/branches/{branch}/protection"]

    try:
        result = subprocess.run(command, capture_output=True, encoding="utf-8", check=False, env=_gh_env())
    except FileNotFoundError as error:
        raise GHCommandError(command, None, "GitHub CLI executable not found: gh", error="gh-not-found") from error

    if result.returncode != 0:
        payload = _json_or_none(result.stdout)
        unavailable_status = _protection_unavailable_status(payload)
        if unavailable_status == "not_configured":
            return RequiredStatusChecks(names=(), status="not_configured", message=_protection_message(payload))
        if unavailable_status == "unavailable":
            return RequiredStatusChecks(names=(), status="unavailable", message=_protection_message(payload) or result.stderr.strip() or None)
        raise GHCommandError(command, result.returncode, result.stderr)

    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise GHCommandError(command, result.returncode, f"GitHub branch protection payload could not be parsed: {error.msg}", error="gh-parse-failed") from error

    required_status_checks = raw.get("required_status_checks")
    if not isinstance(required_status_checks, dict):
        return RequiredStatusChecks(names=(), status="not_configured")

    checks = required_status_checks.get("checks")
    if isinstance(checks, list):
        names = [check.get("context") for check in checks if isinstance(check, dict) and isinstance(check.get("context"), str)]
        return _required_status_checks_from_names(names)

    contexts = required_status_checks.get("contexts")
    if isinstance(contexts, list):
        names = [context for context in contexts if isinstance(context, str)]
        return _required_status_checks_from_names(names)

    return RequiredStatusChecks(names=(), status="not_configured")


def parse_pr_context(raw: dict[str, Any]) -> PullRequestContext:
    """Normalize the subset of `gh pr view --json` needed by core commands."""

    latest_reviews = _normalize_reviews(raw)
    status_checks = tuple(CheckSummary.from_raw(check) for check in _status_nodes(raw.get("statusCheckRollup")))

    head_repository_owner = _login(raw.get("headRepositoryOwner"))

    return PullRequestContext(
        number=int(raw["number"]),
        title=raw.get("title") or "",
        state=raw.get("state") or "",
        url=raw.get("url") or "",
        base_ref_name=raw.get("baseRefName") or "",
        base_ref_oid=raw.get("baseRefOid"),
        head_ref_name=raw.get("headRefName") or "",
        head_ref_oid=raw.get("headRefOid") or "",
        head_repository=_repository_name(raw.get("headRepository"), owner=head_repository_owner),
        head_repository_owner=head_repository_owner,
        is_cross_repository=bool(raw.get("isCrossRepository")),
        is_draft=bool(raw.get("isDraft")),
        merge_state_status=raw.get("mergeStateStatus"),
        mergeable=raw.get("mergeable"),
        review_decision=raw.get("reviewDecision"),
        changed_files=raw.get("changedFiles"),
        additions=raw.get("additions"),
        deletions=raw.get("deletions"),
        labels=tuple(_label_name(label) for label in raw.get("labels") or () if _label_name(label)),
        latest_reviews=latest_reviews,
        review_requests=tuple(_review_request_name(request) for request in raw.get("reviewRequests") or () if _review_request_name(request)),
        status_checks=status_checks,
        raw=raw,
    )


def _normalize_reviews(raw: dict[str, Any]) -> tuple[ReviewSummary, ...]:
    merged_reviews: list[tuple[ReviewSummary, int]] = []

    for source_surface in ("latestReviews", "reviews"):
        review_source = raw.get(source_surface)
        if not isinstance(review_source, list):
            continue

        for review in review_source:
            if not isinstance(review, dict):
                continue

            candidate = ReviewSummary.from_raw(review, source_surface=source_surface)
            matching_indexes = [
                index
                for index, (existing, _) in enumerate(merged_reviews)
                if _same_review(existing, candidate)
            ]
            if len(matching_indexes) == 1:
                index = matching_indexes[0]
                existing, ordinal = merged_reviews[index]
                merged_reviews[index] = (_merge_reviews(existing, candidate), ordinal)
            else:
                merged_reviews.append((candidate, len(merged_reviews)))

    ordered_reviews = sorted(merged_reviews, key=lambda item: _review_sort_key(item[0], item[1]))
    return tuple(review for review, _ in ordered_reviews)


def _same_review(left: ReviewSummary, right: ReviewSummary) -> bool:
    if left.author != right.author or left.state != right.state:
        return False
    if _review_fields_conflict(left, right):
        return False
    if _shared_review_field_count(left, right) > 0:
        return True
    return _complements_missing_review_fields(left, right)


def _merge_reviews(existing: ReviewSummary, incoming: ReviewSummary) -> ReviewSummary:
    existing_priority = _review_surface_priority(existing.source_surface)
    incoming_priority = _review_surface_priority(incoming.source_surface)
    if incoming_priority >= existing_priority:
        preferred = incoming
        fallback = existing
    else:
        preferred = existing
        fallback = incoming

    source_surfaces = tuple(
        surface
        for surface in ("latestReviews", "reviews")
        if surface in {*existing.source_surfaces, *incoming.source_surfaces}
    )

    return ReviewSummary(
        author=preferred.author or fallback.author,
        state=preferred.state or fallback.state,
        submitted_at=preferred.submitted_at or fallback.submitted_at,
        commit_oid=preferred.commit_oid or fallback.commit_oid,
        body=preferred.body if preferred.body not in (None, "") else fallback.body,
        source_surface=preferred.source_surface or fallback.source_surface,
        source_surfaces=source_surfaces,
    )


def _review_sort_key(review: ReviewSummary, ordinal: int) -> tuple[bool, str, int]:
    return (review.submitted_at is None, review.submitted_at or "", ordinal)


def _review_surface_priority(source_surface: str | None) -> int:
    if source_surface == "reviews":
        return 2
    if source_surface == "latestReviews":
        return 1
    return 0


def _review_fields_conflict(left: ReviewSummary, right: ReviewSummary) -> bool:
    for left_value, right_value in (
        (left.submitted_at, right.submitted_at),
        (left.commit_oid, right.commit_oid),
        (_review_body_key(left.body), _review_body_key(right.body)),
    ):
        if left_value and right_value and left_value != right_value:
            return True
    return False


def _shared_review_field_count(left: ReviewSummary, right: ReviewSummary) -> int:
    shared_fields = 0
    for left_value, right_value in (
        (left.submitted_at, right.submitted_at),
        (left.commit_oid, right.commit_oid),
        (_review_body_key(left.body), _review_body_key(right.body)),
    ):
        if left_value and right_value and left_value == right_value:
            shared_fields += 1
    return shared_fields


def _complements_missing_review_fields(left: ReviewSummary, right: ReviewSummary) -> bool:
    if bool(left.submitted_at) == bool(right.submitted_at):
        return False
    return _review_field_count(left) > 0 and _review_field_count(right) > 0 and _review_union_field_count(left, right) >= 2


def _review_field_count(review: ReviewSummary) -> int:
    return sum(
        1
        for value in (
            review.submitted_at,
            review.commit_oid,
            _review_body_key(review.body),
        )
        if value
    )


def _review_union_field_count(left: ReviewSummary, right: ReviewSummary) -> int:
    return sum(
        1
        for left_value, right_value in (
            (left.submitted_at, right.submitted_at),
            (left.commit_oid, right.commit_oid),
            (_review_body_key(left.body), _review_body_key(right.body)),
        )
        if left_value or right_value
    )


def _review_body_key(body: str | None) -> str | None:
    normalized = (body or "").strip()
    return normalized or None


def _status_nodes(status_check_rollup: Any) -> tuple[dict[str, Any], ...]:
    if not status_check_rollup:
        return ()
    if isinstance(status_check_rollup, list):
        return tuple(node for node in status_check_rollup if isinstance(node, dict))
    if not isinstance(status_check_rollup, dict):
        return ()

    nodes = status_check_rollup.get("nodes")
    if isinstance(nodes, list):
        return tuple(node for node in nodes if isinstance(node, dict))

    contexts = status_check_rollup.get("contexts")
    if isinstance(contexts, dict) and isinstance(contexts.get("nodes"), list):
        return tuple(node for node in contexts["nodes"] if isinstance(node, dict))

    return ()


def _json_or_none(payload: str) -> dict[str, Any] | None:
    try:
        loaded = json.loads(payload)
    except json.JSONDecodeError:
        return None
    return loaded if isinstance(loaded, dict) else None


def _required_status_checks_from_names(names: list[str]) -> RequiredStatusChecks:
    deduped_names = tuple(dict.fromkeys(name for name in names if name))
    status = "configured" if deduped_names else "not_configured"
    return RequiredStatusChecks(names=deduped_names, status=status)


def _protection_unavailable_status(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    status = str(payload.get("status") or "")
    message = str(payload.get("message") or "").lower()
    if status == "404":
        return "not_configured"
    if status == "403" and "upgrade to github pro" in message:
        return "unavailable"
    return None


def _protection_message(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    message = payload.get("message")
    return message if isinstance(message, str) and message else None


def _gh_env() -> dict[str, str]:
    """Return an environment that lets `gh` inspect the current checkout safely.

    This avoids mutating global Git config when a repo is shared across local
    users or assistant sandboxes with different Windows identities.
    """

    env = os.environ.copy()
    try:
        config_count = int(env.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        config_count = 0

    env[f"GIT_CONFIG_KEY_{config_count}"] = "safe.directory"
    env[f"GIT_CONFIG_VALUE_{config_count}"] = str(Path.cwd())
    env["GIT_CONFIG_COUNT"] = str(config_count + 1)
    return env


def _check_bucket(*, status: str | None, conclusion: str | None, state: str | None) -> str:
    normalized = {value.upper() for value in (status, conclusion, state) if isinstance(value, str)}
    if normalized & {"FAILURE", "FAILED", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}:
        return "failure"
    if normalized & {"PENDING", "QUEUED", "IN_PROGRESS", "REQUESTED", "WAITING", "EXPECTED"}:
        return "pending"
    if normalized & {"SKIPPED", "NEUTRAL"}:
        return "skipped"
    if normalized & {"SUCCESS"}:
        return "success"
    return "unknown"


def _nested_get(raw: dict[str, Any], *path: str) -> Any:
    current: Any = raw
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _login(raw: Any) -> str | None:
    if isinstance(raw, dict):
        return raw.get("login")
    return None


def _repository_name(raw: Any, *, owner: str | None = None) -> str | None:
    if not isinstance(raw, dict):
        return None
    name_with_owner = raw.get("nameWithOwner")
    if name_with_owner:
        return name_with_owner
    name = raw.get("name")
    if owner and name:
        return f"{owner}/{name}"
    return name


def _label_name(raw: Any) -> str | None:
    if isinstance(raw, dict):
        return raw.get("name")
    if isinstance(raw, str):
        return raw
    return None


def _review_request_name(raw: Any) -> str | None:
    if not isinstance(raw, dict):
        return None
    if raw.get("login"):
        return raw["login"]
    reviewer = raw.get("reviewer")
    team = raw.get("team")
    return _login(reviewer) or (team.get("slug") if isinstance(team, dict) else None)
