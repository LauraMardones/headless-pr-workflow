"""Tell the PO when a Codex review bookkeeping run fails (issue #330).

Run by the notify step of `.github/workflows/codex-review-bookkeeping.yml`
after a failed run. One `dispatcher_error` Slack message per failure streak,
then one reminder per UTC calendar day while the streak lasts. The rule reads
only the workflow's own run history; nothing is stored.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

WORKFLOW_FILE = "codex-review-bookkeeping.yml"
HISTORY_LIMIT = 30
MAX_LISTED_PRS = 3
MAX_ERROR_CHARS = 200
_TOKEN_LIKE = re.compile(
    r"gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|xox[a-z]-[A-Za-z0-9-]+|https://hooks\.slack\.com/\S+"
)


def should_notify(history: Sequence[Mapping[str, Any]], now: datetime) -> bool:
    """Whether a failing run now starts a streak or is its first of a new UTC day.

    `history` lists the earlier runs, newest first. Only runs that finished as
    a success or a failure count; cancelled, skipped and unfinished ones are
    ignored.
    """

    for run in history:
        conclusion = run.get("conclusion")
        if conclusion == "success":
            return True
        if conclusion == "failure":
            return _utc_day(str(run.get("createdAt", ""))) < now.astimezone(timezone.utc).date()
    return True


def _utc_day(timestamp: str):
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(timezone.utc).date()


def mask(text: str) -> str:
    """One line, token-like strings masked, cut to a fixed length."""

    text = _TOKEN_LIKE.sub("***", " ".join(text.split()))
    return text if len(text) <= MAX_ERROR_CHARS else text[: MAX_ERROR_CHARS - 3] + "..."


def build_description(errors: Sequence[Mapping[str, Any]]) -> str:
    if not errors:
        return "The Codex review bookkeeping run failed before it reported a PR; open the run for the cause."
    lines = []
    for error in errors[:MAX_LISTED_PRS]:
        pr = error.get("pr")
        where = f"PR #{pr}" if pr is not None else "no PR read yet"
        lines.append(f"{where}, step `{mask(str(error.get('step', 'unknown')))}`: {mask(str(error.get('error', '')))}")
    if len(errors) > MAX_LISTED_PRS:
        lines.append(f"and {len(errors) - MAX_LISTED_PRS} more")
    return "Codex review bookkeeping failed. " + "; ".join(lines)


def load_errors(path: str | None) -> tuple[bool, list[dict[str, Any]]]:
    """Return (dry_run, errors). A missing or unreadable file means no error was reported."""

    try:
        data = json.loads(Path(path or "").read_text(encoding="utf-8"))
        return bool(data.get("dry_run")), list(data.get("errors") or [])
    except (OSError, ValueError, AttributeError, TypeError):
        return False, []


def fetch_history(repo: str, env: Mapping[str, str]) -> list[dict[str, Any]]:
    # No --branch filter: pull_request_review runs are recorded under the PR's
    # ref, not the default branch, and they belong to the streak too.
    result = subprocess.run(
        ["gh", "run", "list", "--repo", repo, "--workflow", WORKFLOW_FILE,
         "--limit", str(HISTORY_LIMIT), "--json", "conclusion,createdAt"],
        capture_output=True, text=True, check=False, env=dict(env),
    )
    if result.returncode != 0:
        raise RuntimeError("gh run list failed")
    return list(json.loads(result.stdout))


def notify(
    env: Mapping[str, str],
    *,
    now: datetime | None = None,
    history_reader: Callable[[str, Mapping[str, str]], list[dict[str, Any]]] = fetch_history,
) -> int:
    """Send the message when the repeat rule says so. Never fails the run."""

    now = now or datetime.now(timezone.utc)
    dry_run, errors = load_errors(env.get("ERRORS_FILE"))
    if dry_run:
        return 0
    try:
        send = should_notify(history_reader(env.get("REPOSITORY", ""), env), now)
    except (RuntimeError, ValueError, OSError):
        send = True
        print("::warning::The run history could not be read, so the once-a-day rule was not applied.")
    if not send:
        return 0
    context = json.dumps({"error_description": build_description(errors), "last_action": env.get("RUN_URL", "(unknown)")})
    script = env.get("SLACK_NOTIFY_SCRIPT", "scripts/slack-notify.sh")
    try:
        sent = subprocess.run(["bash", script, "dispatcher_error", context], env=dict(env), check=False).returncode == 0
    except OSError:
        sent = False
    if not sent:
        print("::warning::The PO was not notified: the Slack message could not be sent (is SLACK_WEBHOOK_URL set?).")
    return 0


def main() -> int:
    return notify(os.environ)


if __name__ == "__main__":
    sys.exit(main())
