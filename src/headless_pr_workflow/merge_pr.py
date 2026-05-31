"""Merge planning and guarded execution built on canonical pre-merge readiness."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any

from .github.pr_context import GHCommandError, PullRequestContext, _gh_env
from .pre_merge import PreMergeSummary


MERGE_METHODS = ("merge", "squash", "rebase")
MERGE_METHOD_FLAGS = {
    "merge": "--merge",
    "squash": "--squash",
    "rebase": "--rebase",
}


@dataclass(frozen=True)
class MergeCommandResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": list(self.command),
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "ok": self.ok,
        }


@dataclass(frozen=True)
class PostMergeVerification:
    number: int
    url: str
    state: str
    head_sha: str
    base_branch: str
    merged: bool
    message: str

    @classmethod
    def from_context(cls, context: PullRequestContext) -> "PostMergeVerification":
        merged = _context_reports_merged(context)
        state = context.state or "unknown"
        message = "GitHub reports PR is merged." if merged else f"GitHub does not report PR as merged (state={state})."
        return cls(
            number=context.number,
            url=context.url,
            state=state,
            head_sha=context.head_ref_oid,
            base_branch=context.base_ref_name,
            merged=merged,
            message=message,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "url": self.url,
            "state": self.state,
            "head_sha": self.head_sha,
            "base_branch": self.base_branch,
            "merged": self.merged,
            "message": self.message,
        }


@dataclass(frozen=True)
class MergePrSummary:
    pre_merge: PreMergeSummary
    method: str
    mode: str = "dry_run"
    merge_result: MergeCommandResult | None = None
    post_merge: PostMergeVerification | None = None

    @property
    def would_merge(self) -> bool:
        return self.pre_merge.hard_gate_passed

    @property
    def dry_run(self) -> bool:
        return self.mode == "dry_run"

    @property
    def mutation_attempted(self) -> bool:
        return self.merge_result is not None

    @property
    def merged(self) -> bool:
        return bool(self.post_merge and self.post_merge.merged)

    @property
    def execution_succeeded(self) -> bool:
        if self.dry_run:
            return self.would_merge
        return self.would_merge and bool(self.merge_result and self.merge_result.ok) and self.merged

    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        reasons = list(self.pre_merge.blocking_reasons)
        if self.merge_result is not None and not self.merge_result.ok:
            reasons.append("GitHub merge command failed.")
        if self.merge_result is not None and self.merge_result.ok and self.post_merge is not None and not self.post_merge.merged:
            reasons.append(self.post_merge.message)
        return tuple(reasons)

    def to_dict(self) -> dict[str, Any]:
        approval = self.pre_merge.approval
        return {
            "command": "merge-pr",
            "mode": self.mode,
            "dry_run": self.dry_run,
            "execute": self.mode == "execute",
            "would_merge": self.would_merge,
            "mutation_attempted": self.mutation_attempted,
            "merged": self.merged,
            "selected_method": self.method,
            "number": self.pre_merge.number,
            "url": self.pre_merge.url,
            "head_sha": self.pre_merge.head_ref_oid,
            "base_branch": self.pre_merge.base_ref_name,
            "approval_review_source": {
                "approval_source": approval.approval_source,
                "satisfied_by": approval.satisfied_by,
                "approval_status": approval.approval_status,
                "latest_review_sha": approval.latest_review_sha,
                "latest_review_state": approval.latest_review_state,
                "latest_review_author": approval.latest_review_author,
                "latest_approval_sha": approval.latest_approval_sha,
            },
            "blocking_reasons": list(self.blocking_reasons),
            "merge_command": self.merge_result.to_dict() if self.merge_result else None,
            "post_merge_verification": self.post_merge.to_dict() if self.post_merge else None,
            "readiness": self.pre_merge.to_dict(),
        }


def summarize_merge_pr(
    pre_merge: PreMergeSummary,
    *,
    method: str,
    mode: str = "dry_run",
    merge_result: MergeCommandResult | None = None,
    post_merge: PostMergeVerification | None = None,
) -> MergePrSummary:
    if method not in MERGE_METHODS:
        raise ValueError(f"unsupported merge method: {method}")
    if mode not in {"dry_run", "execute"}:
        raise ValueError(f"unsupported merge-pr mode: {mode}")
    return MergePrSummary(
        pre_merge=pre_merge,
        method=method,
        mode=mode,
        merge_result=merge_result,
        post_merge=post_merge,
    )


def run_github_merge(*, number: int, repo: str | None, method: str, head_sha: str) -> MergeCommandResult:
    if method not in MERGE_METHODS:
        raise ValueError(f"unsupported merge method: {method}")
    command = ["gh", "pr", "merge", str(number)]
    if repo:
        command.extend(["--repo", repo])
    command.extend([MERGE_METHOD_FLAGS[method], "--match-head-commit", head_sha])

    try:
        result = subprocess.run(command, capture_output=True, encoding="utf-8", check=False, env=_gh_env())
    except FileNotFoundError as error:
        raise GHCommandError(command, None, "GitHub CLI executable not found: gh. Install from https://cli.github.com and authenticate with 'gh auth login'.", error="gh-not-found") from error

    return MergeCommandResult(
        command=tuple(command),
        returncode=result.returncode,
        stdout=result.stdout.strip(),
        stderr=result.stderr.strip(),
    )


def _context_reports_merged(context: PullRequestContext) -> bool:
    if context.state == "MERGED":
        return True
    if context.raw.get("merged") is True:
        return True
    return bool(context.raw.get("mergedAt"))
