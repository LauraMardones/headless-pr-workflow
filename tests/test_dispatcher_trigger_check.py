"""Stop alert for the dispatcher trigger (issue #339, ADR-013 → Decision 4).

Runs scripts/dispatcher-trigger-check.sh and scripts/dispatcher-notify-retry.sh
for real with a fake ``gh`` (runs list, issue state) and a fake slack-notify.sh,
and checks scripts/slack-notify.sh's payload and the workflow contract.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "dispatcher.yml"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(
    not (BASH and shutil.which("jq")), reason="bash and jq required"
)

FAKE_GH = r"""#!/usr/bin/env bash
echo "gh $*" >> "$MOCK_DIR/gh.log"
if [[ "$1 $2" == "run list" ]]; then
    [[ -f "$MOCK_DIR/runs_fail" ]] && { echo "HTTP 502: Bad Gateway token=SECRET" >&2; exit 1; }
    jq_expr=""
    while [[ $# -gt 0 ]]; do [[ "$1" == --jq ]] && jq_expr="$2"; shift; done
    if [[ -n "$jq_expr" ]]; then jq -r "$jq_expr" "$MOCK_DIR/runs.json"; else cat "$MOCK_DIR/runs.json"; fi
    exit 0
fi
echo open
"""

FAKE_SLACK = r"""#!/usr/bin/env bash
echo "$1 $2" >> "$MOCK_DIR/slack.log"
[[ -e "$MOCK_DIR/slack_fail" ]] && exit 1
exit 0
"""


def iso(minutes_ago: float) -> str:
    t = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


class Sandbox:
    def __init__(self, tmp: Path):
        self.root = tmp
        self.mock = tmp / "mock"
        self.scripts = tmp / "scripts"
        self.state = tmp / "state"
        for d in (self.mock, self.scripts, self.state, tmp / "bin"):
            d.mkdir()
        for name in ("dispatcher-trigger-check.sh", "dispatcher-notify-retry.sh"):
            shutil.copy(REPO_ROOT / "scripts" / name, self.scripts / name)
        (self.scripts / "slack-notify.sh").write_text(FAKE_SLACK)
        (tmp / "bin" / "gh").write_text(FAKE_GH)
        for p in [*self.scripts.glob("*.sh"), tmp / "bin" / "gh"]:
            p.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": f"{tmp / 'bin'}:{os.environ['PATH']}",
            "MOCK_DIR": str(self.mock),
            "DISPATCHER_STATE_DIR": str(self.state),
            "SLACK_WEBHOOK_URL": "x",
        }
        self.set_runs(None)

    def set_runs(self, minutes_ago: float | None, run_id: int = 1):
        runs = [] if minutes_ago is None else [{"databaseId": run_id, "createdAt": iso(minutes_ago)}]
        (self.mock / "runs.json").write_text(json.dumps(runs))
        return runs[0]["createdAt"] if runs else "none"

    def run(self, script: str, *args: str):
        return subprocess.run(
            [BASH, str(self.scripts / script), *args],
            env=self.env, capture_output=True, text=True,
        )

    def check(self, *args: str):
        return self.run("dispatcher-trigger-check.sh", "--repo", "o/r", *args)

    def retry(self, *args: str):
        return self.run("dispatcher-notify-retry.sh", "run", "--repo", "o/r", *args)

    def events(self) -> list[dict]:
        f = self.state / "o" / "r" / "actionable-events"
        return [json.loads(x) for x in f.read_text().splitlines() if x] if f.exists() else []

    def slack(self) -> list[str]:
        f = self.mock / "slack.log"
        return f.read_text().splitlines() if f.exists() else []


@pytest.fixture
def sb(tmp_path):
    return Sandbox(tmp_path)


def test_fresh_run_queues_nothing(sb):
    sb.set_runs(3, run_id=42)
    r = sb.check()
    assert r.returncode == 0
    assert "Fresh" in r.stderr and "42" in r.stderr and "3 minutes" in r.stderr
    assert sb.events() == []
    assert r.stdout == ""


def test_stale_run_queues_exactly_one_event_keyed_by_creation_time(sb):
    created = sb.set_runs(40)
    r = sb.check()
    assert r.returncode == 0
    assert "stopped" in r.stderr
    events = sb.events()
    assert len(events) == 1
    assert events[0]["key"] == f"dispatch_trigger_stopped:0:{created}"
    ctx = json.loads(events[0]["context"])
    assert ctx["last_created_at"] == created
    assert ctx["age"].endswith("minutes")
    assert "gh run list --workflow dispatcher.yml --branch main --event workflow_dispatch --limit 5" == ctx["check_command"]
    assert ctx["docs_url"].endswith("docs/DISPATCHER-CONFIG.md#dispatcher-trigger")


def test_stale_run_seen_twice_is_one_event_and_one_message(sb):
    sb.set_runs(40)
    sb.check()
    sb.check()
    assert len(sb.events()) == 1
    assert sb.retry().returncode == 0
    sb.check()
    assert sb.retry().returncode == 0
    assert len(sb.slack()) == 1
    assert sb.slack()[0].startswith("dispatch_trigger_stopped ")
    assert sb.events()[0]["delivered"] is True


def test_threshold_boundary_is_strictly_older_than_threshold(sb):
    sb.set_runs(14)
    sb.check()
    assert sb.events() == []
    sb.set_runs(16)
    sb.check()
    assert len(sb.events()) == 1


def test_threshold_option(sb):
    sb.set_runs(8)
    sb.check("--threshold-minutes", "5")
    assert len(sb.events()) == 1


def test_no_dispatched_run_queues_one_event_keyed_none(sb):
    sb.set_runs(None)
    r = sb.check()
    assert r.returncode == 0
    sb.check()
    events = sb.events()
    assert [e["key"] for e in events] == ["dispatch_trigger_stopped:0:none"]
    assert json.loads(events[0]["context"])["age"] == "none"


def test_newer_run_resolves_and_next_gap_queues_new_event(sb):
    first = sb.set_runs(40)
    sb.check()
    # still stale: retry delivers, keeps the record
    sb.retry()
    assert len(sb.events()) == 1 and len(sb.slack()) == 1
    # scheduler recovers: a newer dispatched run exists
    sb.set_runs(2, run_id=2)
    r = sb.retry()
    assert r.returncode == 0
    assert "Resolved, dropping" in r.stderr
    assert sb.events() == []
    # a later gap is a new event with a new key
    second = sb.set_runs(30, run_id=3)
    assert second != first
    sb.check()
    sb.retry()
    assert [e["key"] for e in sb.events()] == [f"dispatch_trigger_stopped:0:{second}"]
    assert len(sb.slack()) == 2


def test_none_event_is_resolved_by_any_dispatched_run(sb):
    sb.set_runs(None)
    sb.check()
    assert len(sb.events()) == 1
    sb.set_runs(1)
    sb.retry()
    assert sb.events() == []


def test_unresolved_while_lookup_fails_or_no_newer_run(sb):
    sb.set_runs(40)
    sb.check()
    (sb.mock / "runs_fail").write_text("")
    sb.retry()
    assert len(sb.events()) == 1  # a failed lookup never drops the event
    (sb.mock / "runs_fail").unlink()
    sb.retry()
    assert len(sb.events()) == 1  # same newest run: still unresolved


def test_dry_run_reports_and_queues_nothing(sb):
    sb.set_runs(40)
    r = sb.check("--dry-run")
    assert r.returncode == 0
    assert "Would queue" in r.stderr
    assert sb.events() == []
    assert not (sb.state / "o").exists()
    sb.set_runs(None)
    assert "Would queue" in sb.check("--dry-run").stderr


def test_api_failure_warns_exits_zero_and_queues_nothing(sb):
    sb.set_runs(40)
    (sb.mock / "runs_fail").write_text("")
    r = sb.check()
    assert r.returncode == 0
    assert "Warning:" in r.stderr
    assert sb.events() == []


def test_unreadable_run_list_warns_and_queues_nothing(sb):
    (sb.mock / "runs.json").write_text("not json")
    r = sb.check()
    assert r.returncode == 0 and "Warning:" in r.stderr
    assert sb.events() == []


def test_unwritable_state_and_failed_delivery_exits_nonzero(sb):
    sb.set_runs(40)
    (sb.state / "o").write_text("a file where a directory is needed")
    (sb.mock / "slack_fail").write_text("")
    assert sb.check().returncode == 1


def test_unwritable_state_delivers_synchronously(sb):
    sb.set_runs(40)
    (sb.state / "o").write_text("a file where a directory is needed")
    assert sb.check().returncode == 0
    assert len(sb.slack()) == 1


def test_usage_errors(sb):
    assert sb.run("dispatcher-trigger-check.sh").returncode == 2
    assert sb.check("--threshold-minutes", "x").returncode == 2
    assert sb.check("--bogus").returncode == 2


def test_persistent_delivery_failure_is_red_without_issue_comment(sb):
    sb.set_runs(40)
    sb.check()
    (sb.mock / "slack_fail").write_text("")
    codes = [sb.retry().returncode for _ in range(3)]
    assert codes == [0, 0, 1]
    assert "api repos/o/r/issues/0/comments" not in (sb.mock / "gh.log").read_text()


# ─── scripts/slack-notify.sh ─────────────────────────────────────────────────


def test_slack_payload_for_dispatch_trigger_stopped(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "curl").write_text(
        '#!/usr/bin/env bash\nwhile [[ $# -gt 0 ]]; do [[ "$1" == --data ]] && printf %s "$2" > "$OUT"; shift; done\n'
    )
    (bin_dir / "curl").chmod(0o755)
    ctx = {
        "age": "40 minutes",
        "last_created_at": "2026-10-16T10:00:00Z",
        "check_command": "gh run list --workflow dispatcher.yml --branch main --event workflow_dispatch --limit 5",
        "docs_url": "https://example.test/docs#dispatcher-trigger",
    }
    out = tmp_path / "payload.json"
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "SLACK_WEBHOOK_URL": "https://hooks.test/x", "OUT": str(out)}
    r = subprocess.run(
        [BASH, str(REPO_ROOT / "scripts" / "slack-notify.sh"), "dispatch_trigger_stopped", json.dumps(ctx)],
        env=env, capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    text = json.dumps(json.loads(out.read_text()))
    for needle in ("40 minutes", "2026-10-16T10:00:00Z", "--event workflow_dispatch", "https://example.test/docs#dispatcher-trigger"):
        assert needle in text


# ─── .github/workflows/dispatcher.yml contract ───────────────────────────────

STEP_RE = re.compile(r"^      - name: (.+)$", re.MULTILINE)


def workflow_steps() -> list[tuple[str, str]]:
    text = WORKFLOW.read_text()
    ms = list(STEP_RE.finditer(text))
    return [
        (m.group(1).strip(), text[m.start(): ms[i + 1].start() if i + 1 < len(ms) else len(text)])
        for i, m in enumerate(ms)
    ]


def test_workflow_step_order_and_conditions():
    steps = dict(workflow_steps())
    names = [n for n, _ in workflow_steps()]
    check, retry = "Check the dispatch trigger", "Retry actionable Slack notifications"
    assert names.index("Restore refinement notification state") < names.index(check) < names.index(retry)
    cond = lambda b: re.search(r"^        if: (.+)$", b, re.MULTILINE).group(1)
    assert cond(steps[check]) == cond(steps[retry])
    assert "steps.guard.outputs.enabled == 'true'" in cond(steps[check])
    assert "scripts/dispatcher-trigger-check.sh" in steps[check]
    assert '--dry-run' in steps[check] and "state_writer" in steps[check]


def test_workflow_triggers_concurrency_permissions_unchanged():
    text = WORKFLOW.read_text()
    assert re.search(r'^on:\n  schedule:\n    - cron: "\*/5 \* \* \* \*"\n  workflow_dispatch:\n', text, re.MULTILINE)
    assert re.search(
        r"^concurrency:\n  group: dispatcher-\$\{\{ github\.repository \}\}\n  cancel-in-progress: false\n",
        text, re.MULTILINE,
    )
    assert not re.search(r"^\s*permissions:", text, re.MULTILINE)
    assert "issue #339" in text
