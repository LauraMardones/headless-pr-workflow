"""Event delivery and the shared board-writer job contract (#573).

Workflow structure is read as text, matching the other workflow tests: no
YAML dependency or GitHub credentials are needed. Script decisions execute
the production guard/scope/status-sync against the existing fake gateway.
"""

from __future__ import annotations

import re
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread
from time import perf_counter

import pytest

from headless_pr_workflow.dispatcher.status import derive_status
from tests.dispatcher_fakes import needs_tools
from tests.test_dispatcher_workflow_step import Sandbox

ROOT = Path(__file__).resolve().parents[1]
EVENT = ROOT / ".github/workflows/board-status.yml"
DISPATCH = ROOT / ".github/workflows/dispatcher.yml"
GROUP = "board-status-${{ github.repository }}"
EVENTS = {
    "pull_request_target": {"opened", "reopened", "converted_to_draft", "ready_for_review", "synchronize", "closed"},
    "pull_request_review": {"submitted", "dismissed"},
    "workflow_run": {"completed"},
    "issues": {"opened", "closed", "reopened"},
    "issue_comment": {"created"},
}


def triggers(text):
    block = text.split("\non:\n", 1)[1].split("\npermissions:", 1)[0]
    found = {}
    for name, body in re.findall(r"^  (\w+):\n((?:    .*\n|  #.*\n)*)", block, re.M):
        types = re.search(r"types: \[([^]]+)\]", body)
        assert types, name
        found[name] = {value.strip() for value in types[1].split(",")}
    return found


def job(text):
    return text.split("\n  board-status:\n", 1)[1]


def assert_events(text):
    assert triggers(text) == EVENTS


def test_every_status_event_and_no_self_triggering_event():
    assert_events(EVENT.read_text(encoding="utf-8"))


@pytest.mark.parametrize("event", EVENTS)
def test_missing_event_fails_the_contract(event):
    text = EVENT.read_text(encoding="utf-8")
    assert pytest.raises(AssertionError, assert_events, text.replace(f"  {event}:\n", "  missing:\n"))


@pytest.mark.parametrize("extra", ["labeled", "edited"])
def test_label_or_edit_trigger_fails_the_contract(extra):
    text = EVENT.read_text(encoding="utf-8").replace("types: [created]", f"types: [created, {extra}]")
    assert pytest.raises(AssertionError, assert_events, text)


def test_required_actions_check_completion_uses_workflow_run():
    text = EVENT.read_text(encoding="utf-8")
    # GitHub excludes Actions-created suites from check_run/check_suite events.
    # workflow_run:completed is delivered even for Actions-created checks and re-runs.
    assert "    workflows: [tests]\n    types: [completed]" in text
    assert "name: tests" in (ROOT / ".github/workflows/tests.yml").read_text()
    assert not ({"check_run", "check_suite"} & triggers(text).keys())


def test_webhook_only_thread_event_is_not_an_invalid_actions_trigger():
    # GitHub's Actions event reference does not support this webhook. PO
    # option A on #573 explicitly leaves thread-only changes to the per-run write.
    assert "pull_request_review_thread" not in triggers(EVENT.read_text(encoding="utf-8"))


def test_events_run_only_existing_step_commands_and_no_session():
    text = EVENT.read_text(encoding="utf-8")
    assert re.findall(r"^\s+run: (.*)$", text, re.M) == [
        f"bash scripts/dispatcher-workflow-step.sh {name}" for name in ("guard", "scope", "status-sync")
    ]
    assert re.findall(r"^\s+uses: (.*)$", text, re.M) == ["actions/checkout@v4"]
    assert "run: |" not in text


def assert_group(text):
    block = job(text)
    assert f"    concurrency:\n      group: {GROUP}\n      cancel-in-progress: false\n" in block
    assert len(re.findall(r"^\s*concurrency:", block, re.M)) == 1


def test_writers_share_one_non_cancelling_group_outside_dispatch():
    event = EVENT.read_text(encoding="utf-8")
    dispatch = DISPATCH.read_text(encoding="utf-8")
    for text in (event, dispatch):
        assert_group(text)
    assert "\nconcurrency:" not in event
    assert "\nconcurrency:\n  group: dispatcher-${{ github.repository }}\n  cancel-in-progress: false" in dispatch
    assert "status-sync" not in dispatch.split("\n  dispatch:\n", 1)[1].split("\n  board-status:\n", 1)[0]


@pytest.mark.parametrize("path", [EVENT, DISPATCH])
@pytest.mark.parametrize("old,new", [(GROUP, "another-group"), ("cancel-in-progress: false", "cancel-in-progress: true")])
def test_leaving_the_writer_group_or_cancelling_a_write_fails(path, old, new):
    text = path.read_text(encoding="utf-8").replace(old, new)
    assert pytest.raises(AssertionError, assert_group, text)


def test_per_run_write_uses_outputs_even_after_dispatch_failed_or_had_no_work():
    text = DISPATCH.read_text(encoding="utf-8")
    assert "      enabled: ${{ steps.guard.outputs.enabled }}" in text
    assert "      state_writer: ${{ steps.scope.outputs.state_writer }}" in text
    block = job(text)
    assert "    needs: dispatch" in block
    assert "    if: ${{ !cancelled() && needs.dispatch.outputs.enabled == 'true' }}" in block
    assert "STATE_WRITER: ${{ needs.dispatch.outputs.state_writer }}" in block
    assert "steps.poll" not in block and "steps.invoke" not in block
    assert "continue-on-error" not in block


@pytest.mark.parametrize("path", [EVENT, DISPATCH])
def test_writer_job_has_token_po_login_timeout_and_no_cached_facts(path):
    block = job(path.read_text(encoding="utf-8"))
    for line in ("GH_TOKEN: ${{ secrets.PROJECT_TOKEN }}", "DECISION_BLOCKER_PO_LOGIN: LauraMardones",
                 "REPO: ${{ github.repository }}", "timeout-minutes: 10", "persist-credentials: false"):
        assert line in block
    assert "actions/cache" not in block
    assert block.count("run: bash scripts/dispatcher-workflow-step.sh status-sync") == 1


def test_event_checkout_and_scope_use_the_trusted_base_not_the_head():
    text = EVENT.read_text(encoding="utf-8")
    assert "ref: ${{ github.event.pull_request.base.ref || github.ref }}" in text
    assert "REF: ${{ github.event.pull_request && format('refs/heads/{0}', github.event.pull_request.base.ref) || github.ref }}" in text
    assert "DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}" in text
    assert "pull_request.head" not in text
    # The mutations of every event family use the dispatch job's PAT, not its job token.
    dispatch_env = DISPATCH.read_text(encoding="utf-8").split("    steps:", 1)[0]
    assert "GH_TOKEN: ${{ secrets.PROJECT_TOKEN }}" in dispatch_env


@needs_tools
@pytest.mark.parametrize("enabled", [None, "false", "true"])
def test_event_pause_guard_is_wired_and_executes(tmp_path, monkeypatch, enabled):
    monkeypatch.delenv("DISPATCHER_ENABLED", raising=False)
    sb = Sandbox(tmp_path)
    env = {} if enabled is None else {"DISPATCHER_ENABLED": enabled}
    assert sb.run("guard", **env).returncode == 0
    assert sb.outputs()["enabled"] == str(enabled == "true").lower()
    text = EVENT.read_text(encoding="utf-8")
    write = text.split("      - name: Write the board status\n", 1)[1]
    assert "if: ${{ !cancelled() && steps.guard.outputs.enabled == 'true' }}" in write
    assert "STATE_WRITER: ${{ steps.scope.outputs.state_writer }}" in write
    if sb.outputs()["enabled"] == "true":
        assert sb.run("status-sync", STATE_WRITER="true").returncode == 0
    assert len(sb.calls()) == int(enabled == "true")


@needs_tools
@pytest.mark.parametrize("event", EVENTS)
@pytest.mark.parametrize("base,writer", [("main", "true"), ("release", "false")])
def test_event_scope_and_actual_status_command(tmp_path, event, base, writer):
    sb = Sandbox(tmp_path)
    assert sb.run("scope", EVENT_NAME=event, REF=f"refs/heads/{base}", DEFAULT_BRANCH="main").returncode == 0
    assert sb.outputs()["state_writer"] == writer
    assert sb.run("status-sync", STATE_WRITER=writer).returncode == 0
    flag = "" if writer == "true" else " --dry-run"
    assert sb.calls() == [f"hpw dispatcher status sync --repo o/r{flag}"]


@pytest.mark.parametrize("draft,expected", [(True, "In implementation"), (False, "In review")])
def test_recorded_story_stages_while_the_dispatcher_session_is_still_running(draft, expected):
    # PR #568's recorded stage facts: opened draft, then non-draft before merge.
    facts = {"type": "story", "state": "open", "refined": True, "spec_merged": True,
             "ready": True, "prs": [{"number": 568, "draft": draft, "gates_pass": False}]}
    assert derive_status(facts)[0] == expected


def test_recorded_story_event_to_verified_board_write_replay(monkeypatch):
    """Measure a local replay of #490/#568 through the production board writer.

    This measures a local serialized queue and fake-GitHub read/write transport,
    not GitHub runner scheduling or network latency. AC 2 separately requires
    actual hosted event, queue/start and verified-write timestamps before the
    implementation handoff; this replay cannot satisfy that timing requirement.
    The timestamps of the source events are retained separately from replay times.
    """
    from headless_pr_workflow.dispatcher.board_sync import sync_board
    from tests.test_dispatcher_board_sync import SETTINGS, SyncGateway
    from tests.test_dispatcher_facts_block import PO, spec_line, story_body, summary

    monkeypatch.setenv("DECISION_BLOCKER_PO_LOGIN", PO)
    gateway = SyncGateway()
    gateway.add_issue(10, ["type:epic"], spec_line())
    gateway.comment(10, summary())
    gateway.add_issue(20, ["type:feature"], story_body(feature=20))
    gateway.comment(20, summary())
    gateway.add_issue(490, ["type:story"], story_body())
    gateway.comment(490, summary())
    gateway.card(10, "In progress", kind="epic")
    gateway.card(20, "In progress", kind="feature")
    gateway.card(490, "Ready to implement")
    assert sync_board(gateway, "o/r", settings=SETTINGS).exit_code == 0

    running, finish = Event(), Event()
    def session():
        running.set()
        finish.wait(120)
    dispatcher = Thread(target=session)
    dispatcher.start()
    rows = []
    try:
        assert running.wait(5)
        # One executor models the shared writer group; the session has its own
        # lane, so waiting for the session cannot serialize these writes.
        with ThreadPoolExecutor(max_workers=1) as writer:
            for event, source_at, expected in (
                ("opened", "2026-11-15T09:43:41Z", "In implementation"),
                ("ready_for_review", "2026-11-15T09:48:20Z", "In review"),
                ("closed/merged", "2026-11-15T10:06:43Z", "Done"),
            ):
                if event == "opened":
                    gateway.add_pr(568, "Closes #490", draft=True)
                elif event == "ready_for_review":
                    gateway.prs[0]["draft"] = False
                    gateway.gates[568] = False
                else:
                    gateway.issues = [i for i in gateway.issues if i["number"] != 490]
                    gateway.add_closed(490, ["type:story"], story_body())
                    gateway.prs.clear()
                    gateway.board[490]["state"] = "closed"

                event_at = datetime.now(timezone.utc).isoformat()
                submitted = perf_counter()
                def write():
                    started = perf_counter()
                    result = sync_board(gateway, "o/r", settings=SETTINGS)
                    ended = perf_counter()
                    return result, started, ended, datetime.now(timezone.utc).isoformat()
                result, started, ended, changed_at = writer.submit(write).result(timeout=120)
                assert result.exit_code == 0 and gateway.board[490]["status"] == expected
                correction = next(c for c in result.fields["corrections"] if c["issue"] == 490)
                assert correction["result"] == "written"
                assert dispatcher.is_alive() and not finish.is_set()
                assert 0 <= started - submitted <= ended - submitted < 120
                rows.append({"event": event, "source_event_at": source_at, "replay_event_at": event_at,
                             "board_changed_at": changed_at, "status": expected,
                             "queue_ms": round((started - submitted) * 1000, 3),
                             "sync_ms": round((ended - started) * 1000, 3),
                             "total_ms": round((ended - submitted) * 1000, 3)})
    finally:
        finish.set()
        dispatcher.join(timeout=5)
    print("BOARD_STATUS_REPLAY " + json.dumps(rows))
