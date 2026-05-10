"""Post-review delta report built from GitHub review evidence and compare data."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .github import GHCommandError, PullRequestContext, ReviewSummary
from .review_policy import is_accepted_solo_maintainer_override


@dataclass(frozen=True)
class CommitComparisonFile:
    path: str
    status: str | None
    additions: int | None
    deletions: int | None
    changes: int | None
    previous_path: str | None = None

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "CommitComparisonFile":
        return cls(
            path=raw.get("filename") or "",
            status=raw.get("status"),
            additions=_int_or_none(raw.get("additions")),
            deletions=_int_or_none(raw.get("deletions")),
            changes=_int_or_none(raw.get("changes")),
            previous_path=raw.get("previous_filename"),
        )


@dataclass(frozen=True)
class CommitComparison:
    base_sha: str
    head_sha: str
    status: str | None
    ahead_by: int | None
    behind_by: int | None
    total_commits: int | None
    files: tuple[CommitComparisonFile, ...]

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "CommitComparison":
        files = tuple(
            CommitComparisonFile.from_raw(file)
            for file in raw.get("files") or ()
            if isinstance(file, dict)
        )
        return cls(
            base_sha=_nested_get(raw, "base_commit", "sha") or "",
            head_sha=_nested_get(raw, "commits", -1, "sha") or _nested_get(raw, "head_commit", "sha") or "",
            status=raw.get("status"),
            ahead_by=_int_or_none(raw.get("ahead_by")),
            behind_by=_int_or_none(raw.get("behind_by")),
            total_commits=_int_or_none(raw.get("total_commits")),
            files=files,
        )

    @property
    def changed_file_count(self) -> int:
        return len(self.files)

    @property
    def additions(self) -> int | None:
        return _sum_known(file.additions for file in self.files)

    @property
    def deletions(self) -> int | None:
        return _sum_known(file.deletions for file in self.files)


@dataclass(frozen=True)
class ReviewDeltaBaseline:
    sha: str
    source: str
    review_state: str | None
    review_author: str | None
    review_submitted_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sha": self.sha,
            "source": self.source,
            "review_state": self.review_state,
            "review_author": self.review_author,
            "review_submitted_at": self.review_submitted_at,
        }


@dataclass(frozen=True)
class ReviewDeltaFile:
    path: str
    status: str | None
    additions: int | None
    deletions: int | None
    changes: int | None
    previous_path: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status,
            "additions": self.additions,
            "deletions": self.deletions,
            "changes": self.changes,
            "previous_path": self.previous_path,
        }


@dataclass(frozen=True)
class ReviewDeltaSummary:
    number: int
    title: str
    url: str
    repository: str | None
    head_ref_name: str
    current_head_sha: str
    baseline: ReviewDeltaBaseline | None
    status: str
    delta_exists: bool
    changed_file_count: int | None
    additions: int | None
    deletions: int | None
    files: tuple[ReviewDeltaFile, ...]
    messages: tuple[str, ...]
    error: str | None = None

    @property
    def report_generated(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "url": self.url,
            "repository": self.repository,
            "baseline_sha": self.baseline.sha if self.baseline else None,
            "baseline_source": self.baseline.source if self.baseline else None,
            "baseline_review_state": self.baseline.review_state if self.baseline else None,
            "baseline_review_author": self.baseline.review_author if self.baseline else None,
            "baseline_review_submitted_at": self.baseline.review_submitted_at if self.baseline else None,
            "baseline": self.baseline.to_dict() if self.baseline else None,
            "head_ref_name": self.head_ref_name,
            "current_head_sha": self.current_head_sha,
            "status": self.status,
            "delta_exists": self.delta_exists,
            "unchanged_head": self.status == "unchanged",
            "missing_baseline": self.status == "missing-baseline",
            "changed_file_count": self.changed_file_count,
            "additions": self.additions,
            "deletions": self.deletions,
            "files": [file.to_dict() for file in self.files],
            "messages": list(self.messages),
            "error": self.error,
        }


def select_review_delta_baseline(context: PullRequestContext) -> ReviewDeltaBaseline | None:
    """Return the newest SHA-bound review evidence usable as a delta baseline."""

    for review in reversed(context.latest_reviews):
        baseline = _baseline_from_review(review)
        if baseline is not None:
            return baseline
    return None


def summarize_review_delta(context: PullRequestContext, comparison: CommitComparison | None = None) -> ReviewDeltaSummary:
    baseline = select_review_delta_baseline(context)
    repository = context.head_repository

    if baseline is None:
        message = f"No reviewed or approved SHA could be found for PR #{context.number}."
        return ReviewDeltaSummary(
            number=context.number,
            title=context.title,
            url=context.url,
            repository=repository,
            head_ref_name=context.head_ref_name,
            current_head_sha=context.head_ref_oid,
            baseline=None,
            status="missing-baseline",
            delta_exists=False,
            changed_file_count=None,
            additions=None,
            deletions=None,
            files=(),
            messages=(message,),
            error="missing-baseline",
        )

    if baseline.sha == context.head_ref_oid:
        return ReviewDeltaSummary(
            number=context.number,
            title=context.title,
            url=context.url,
            repository=repository,
            head_ref_name=context.head_ref_name,
            current_head_sha=context.head_ref_oid,
            baseline=baseline,
            status="unchanged",
            delta_exists=False,
            changed_file_count=0,
            additions=0,
            deletions=0,
            files=(),
            messages=("No post-review delta exists; current head matches the review baseline.",),
        )

    if comparison is None:
        raise ValueError("comparison is required when the baseline differs from the current head")

    files = tuple(
        ReviewDeltaFile(
            path=file.path,
            status=file.status,
            additions=file.additions,
            deletions=file.deletions,
            changes=file.changes,
            previous_path=file.previous_path,
        )
        for file in comparison.files
    )
    return ReviewDeltaSummary(
        number=context.number,
        title=context.title,
        url=context.url,
        repository=repository,
        head_ref_name=context.head_ref_name,
        current_head_sha=context.head_ref_oid,
        baseline=baseline,
        status="delta",
        delta_exists=True,
        changed_file_count=comparison.changed_file_count,
        additions=comparison.additions,
        deletions=comparison.deletions,
        files=files,
        messages=(f"Post-review delta exists between baseline SHA {baseline.sha} and current head SHA {context.head_ref_oid}.",),
    )


def comparison_failure_summary(context: PullRequestContext, error: str) -> ReviewDeltaSummary:
    baseline = select_review_delta_baseline(context)
    message = (
        f"GitHub comparison failed for baseline SHA {baseline.sha if baseline else 'unknown'} "
        f"and current head SHA {context.head_ref_oid or 'unknown'}."
    )
    return ReviewDeltaSummary(
        number=context.number,
        title=context.title,
        url=context.url,
        repository=context.head_repository,
        head_ref_name=context.head_ref_name,
        current_head_sha=context.head_ref_oid,
        baseline=baseline,
        status="comparison-failed",
        delta_exists=False,
        changed_file_count=None,
        additions=None,
        deletions=None,
        files=(),
        messages=(message,),
        error=error,
    )


def fetch_commit_comparison(repo: str, base_sha: str, head_sha: str) -> CommitComparison:
    """Fetch a GitHub compare summary for two commit SHAs."""

    command = ["gh", "api", f"repos/{repo}/compare/{base_sha}...{head_sha}"]

    try:
        result = subprocess.run(command, capture_output=True, encoding="utf-8", check=False, env=_gh_env())
    except FileNotFoundError as error:
        raise GHCommandError(command, None, "GitHub CLI executable not found: gh", error="gh-not-found") from error

    if result.returncode != 0:
        raise GHCommandError(command, result.returncode, result.stderr)

    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise GHCommandError(command, result.returncode, f"GitHub compare payload could not be parsed: {error.msg}", error="gh-invalid-json") from error

    return parse_commit_comparison(raw, base_sha=base_sha, head_sha=head_sha)


def parse_commit_comparison(raw: dict[str, Any], *, base_sha: str, head_sha: str) -> CommitComparison:
    comparison = CommitComparison.from_raw(raw)
    return CommitComparison(
        base_sha=comparison.base_sha or base_sha,
        head_sha=comparison.head_sha or head_sha,
        status=comparison.status,
        ahead_by=comparison.ahead_by,
        behind_by=comparison.behind_by,
        total_commits=comparison.total_commits,
        files=comparison.files,
    )


def _baseline_from_review(review: ReviewSummary) -> ReviewDeltaBaseline | None:
    if not review.commit_oid:
        return None
    if review.state == "APPROVED":
        return ReviewDeltaBaseline(
            sha=review.commit_oid,
            source="formal-approval",
            review_state=review.state,
            review_author=review.author,
            review_submitted_at=review.submitted_at,
        )
    if is_accepted_solo_maintainer_override(review, head_sha=review.commit_oid):
        return ReviewDeltaBaseline(
            sha=review.commit_oid,
            source="solo-maintainer-override",
            review_state=review.state,
            review_author=review.author,
            review_submitted_at=review.submitted_at,
        )
    return None


def _nested_get(raw: dict[str, Any], *path: str | int) -> Any:
    current: Any = raw
    for key in path:
        if isinstance(key, int):
            if not isinstance(current, list):
                return None
            try:
                current = current[key]
            except IndexError:
                return None
            continue
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _int_or_none(raw: Any) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _sum_known(values: Any) -> int | None:
    known = [value for value in values if value is not None]
    if not known:
        return None
    return sum(known)


def _gh_env() -> dict[str, str]:
    env = os.environ.copy()
    try:
        config_count = int(env.get("GIT_CONFIG_COUNT", "0"))
    except ValueError:
        config_count = 0

    env[f"GIT_CONFIG_KEY_{config_count}"] = "safe.directory"
    env[f"GIT_CONFIG_VALUE_{config_count}"] = str(Path.cwd())
    env["GIT_CONFIG_COUNT"] = str(config_count + 1)
    return env
