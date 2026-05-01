"""Dry-run merge planning built on canonical pre-merge readiness."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .pre_merge import PreMergeSummary


MERGE_METHODS = ("merge", "squash", "rebase")


@dataclass(frozen=True)
class MergePrSummary:
    pre_merge: PreMergeSummary
    method: str
    mode: str = "dry_run"

    @property
    def would_merge(self) -> bool:
        return self.pre_merge.hard_gate_passed

    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        return self.pre_merge.blocking_reasons

    def to_dict(self) -> dict[str, Any]:
        approval = self.pre_merge.approval
        return {
            "command": "merge-pr",
            "mode": self.mode,
            "dry_run": True,
            "would_merge": self.would_merge,
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
            "readiness": self.pre_merge.to_dict(),
        }


def summarize_merge_pr(pre_merge: PreMergeSummary, *, method: str) -> MergePrSummary:
    if method not in MERGE_METHODS:
        raise ValueError(f"unsupported merge method: {method}")
    return MergePrSummary(pre_merge=pre_merge, method=method)
