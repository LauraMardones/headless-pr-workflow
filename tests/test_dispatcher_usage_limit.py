"""The usage limit: sessions per UTC day (issue #320, F308.5 and F308.6).

Three layers: the counter script on its own (a temporary state directory and a
fixed date), the real ``scripts/dispatcher-invoke.sh`` driven with the fakes in
``tests/dispatcher_fakes.py``, and the workflow file and Slack event as text and
payload.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.dispatcher_fakes import BASH, Dispatcher, needs_tools
from tests.script_tools import host_env
from tests.test_dispatcher_review_loop import new_head_on_implement_once
from tests.test_dispatcher_workflow_allowance import step, step_if

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "dispatcher-usage-limit.sh"
NOTIFY = REPO_ROOT / "scripts" / "slack-notify.sh"
TODAY = "2026-10-14"
ISSUE = 500
PR = 900

pytestmark = needs_tools


def usage(tmp_path, command, limit="2", today=TODAY):
    env = {**host_env(), "USAGE_LIMIT_STATE_DIR": str(tmp_path / "state"), "USAGE_LIMIT_TODAY": today}
    env.pop("USAGE_LIMIT_SESSIONS_PER_DAY", None)
    if limit is not None:
        env["USAGE_LIMIT_SESSIONS_PER_DAY"] = limit
    return subprocess.run([BASH, SCRIPT.as_posix(), command], env=env, capture_output=True, text=True)


# ── the counter script ──────────────────────────────────────────────────────


def test_ac1_third_start_is_refused(tmp_path):
    for _ in range(2):
        assert usage(tmp_path, "check").returncode == 0
        usage(tmp_path, "record")
    third = usage(tmp_path, "check")
    assert third.returncode == 1
    assert third.stdout.split() == ["2", "2"]


def test_ac6_a_counter_dated_yesterday_allows_a_start(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "sessions").write_text("2026-10-13:99\n")
    assert usage(tmp_path, "check").returncode == 0
    usage(tmp_path, "record")
    assert (tmp_path / "state" / "sessions").read_text() == f"{TODAY}:1\n"


def test_ac9_zero_means_no_session_and_equal_is_a_refusal(tmp_path):
    assert usage(tmp_path, "check", limit="0").returncode == 1
    usage(tmp_path, "record")
    assert usage(tmp_path, "check", limit="1").returncode == 1
    assert usage(tmp_path, "check", limit="2").returncode == 0


@pytest.mark.parametrize("value", [None, "", "abc", "-1", "7.5", "10 ", "1e3", " 3"])
def test_ac10_missing_or_invalid_value_is_an_error(tmp_path, value):
    proc = usage(tmp_path, "check", limit=value)
    assert proc.returncode == 2
    assert "[USAGE LIMIT ERROR] USAGE_LIMIT_SESSIONS_PER_DAY" in proc.stderr
    assert f'(value: "{value or ""}")' in proc.stderr


def test_ac11_leading_zero_is_decimal(tmp_path):
    for _ in range(7):
        usage(tmp_path, "record")
    assert usage(tmp_path, "check", limit="08").returncode == 0
    usage(tmp_path, "record")
    assert usage(tmp_path, "check", limit="08").returncode == 1


def test_ac15_marker_is_written_only_by_mark_notified(tmp_path):
    assert usage(tmp_path, "notify-due").returncode == 0
    assert usage(tmp_path, "notify-due").returncode == 0  # a failed send leaves it due
    usage(tmp_path, "mark-notified")
    assert usage(tmp_path, "notify-due").returncode == 1
    assert usage(tmp_path, "notify-due", today="2026-10-15").returncode == 0


# ── invoke call sites ───────────────────────────────────────────────────────


@pytest.fixture
def d(tmp_path):
    """A Claude-implemented story ready for /implement."""
    dispatcher = Dispatcher(tmp_path)
    dispatcher.add_story(ISSUE, "Ready for implementation")
    dispatcher.add_pr(PR, ISSUE)
    return dispatcher


@pytest.fixture
def r(tmp_path):
    """A Codex-implemented story \"In review\": /review (a session), then /merge and /cleanup (scripts)."""
    dispatcher = Dispatcher(tmp_path)
    dispatcher.add_story(ISSUE, "In review", executor="codex")
    dispatcher.add_pr(PR, ISSUE)
    dispatcher.session_sets_status("review", ISSUE, "In merge")
    return dispatcher


def seed(d, count, day=None):
    day = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (d.mock / "usage" / "sessions").write_text(f"{day}:{count}\n")


def test_ac2_every_session_kind_counts_once(d):
    d.add_story(900, "In review", executor="codex")
    d.add_pr(910, 900)
    proc = d.run_issue(ISSUE, seq="0,0,0,0")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # The resume pass reviews the Codex PR first. /merge and /cleanup are scripts since #264: they count nothing.
    assert d.sessions == ["review 910", f"implement {ISSUE}"]
    assert d.usage_count == 2


def fix_round_due(d):
    d.add_story(700, "Ready for implementation")
    d.add_pr(970, 700)
    d.add_story(ISSUE, "In implementation")
    d.add_pr(PR, ISSUE)
    d.add_findings(PR, "a" * 40)


def test_ac2_fix_round_counts(tmp_path):
    d = Dispatcher(tmp_path)
    fix_round_due(d)
    new_head_on_implement_once(d)
    proc = d.run_issue(700)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions[0] == f"implement {ISSUE}"  # the fix round
    assert d.usage_count == len(d.sessions) == 2  # fix round, then story 700


def test_ac12_stop_before_a_fix_round_writes_nothing(tmp_path):
    d = Dispatcher(tmp_path)
    fix_round_due(d)
    seed(d, 1)
    proc = d.run_issue(700, env={"USAGE_LIMIT_SESSIONS_PER_DAY": "1"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"[USAGE LIMIT SKIP] #{ISSUE}: /implement not started" in proc.stdout
    assert d.sessions == [] and d.board_writes == []
    assert d.status(ISSUE) == "In implementation" and d.status(700) == "Ready for implementation"
    assert d.posted(ISSUE) == [] and d.posted(700) == []


def test_ac1_ac5_limit_stops_third_start_before_the_allowance_probe(d):
    seed(d, 2)
    proc = d.run_issue(ISSUE, env={"USAGE_LIMIT_SESSIONS_PER_DAY": "2"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == []
    assert d.allowance_calls == 0
    assert d.usage_count == 2
    assert f"[USAGE LIMIT SKIP] #{ISSUE}: /implement not started; 2 of 2 sessions today" in proc.stdout
    assert d.status(ISSUE) == "Ready for implementation"
    assert d.slack_calls == []  # the workflow sends it, not the invoke step


def test_ac3_a_failing_session_still_counts(d):
    d.fail("implement")
    proc = d.run_issue(ISSUE, seq="0")
    assert proc.returncode != 0
    assert d.usage_count == 1


def test_ac4_a_start_refused_by_the_reserve_does_not_count(d):
    proc = d.run_issue(ISSUE, seq="1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[RESERVE SKIP]" in proc.stdout
    assert d.usage_count == 0


def test_ac6_a_counter_dated_yesterday_allows_a_start(d):
    seed(d, 50, day=(datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d"))
    proc = d.run_issue(ISSUE, seq="0,0,0,0", env={"USAGE_LIMIT_SESSIONS_PER_DAY": "2"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions[0] == f"implement {ISSUE}"


def test_ac7_dry_run_neither_counts_nor_notifies(d):
    seed(d, 5)
    proc = d.run_issue(ISSUE, "--dry-run", env={"USAGE_LIMIT_SESSIONS_PER_DAY": "2"})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "[DRY RUN] Would check usage limit before /implement" in proc.stdout
    assert d.usage_count == 5
    assert "usage_limit_reached" not in d.usage_output


def test_ac8_lowering_the_variable_below_the_count_refuses(d):
    seed(d, 3)
    assert d.run_issue(ISSUE, seq="0,0,0,0", env={"USAGE_LIMIT_SESSIONS_PER_DAY": "10"}).returncode == 0
    assert d.sessions
    lowered = d.run_issue(ISSUE, env={"USAGE_LIMIT_SESSIONS_PER_DAY": "3"})
    assert "[USAGE LIMIT SKIP]" in lowered.stdout


@pytest.mark.parametrize("value", ["", "abc", "-1", "7.5", "10 ", "1e3"])
def test_ac10_invalid_value_fails_the_job_without_slack(d, value):
    proc = d.run_issue(ISSUE, env={"USAGE_LIMIT_SESSIONS_PER_DAY": value})
    assert proc.returncode != 0
    assert "[USAGE LIMIT ERROR] USAGE_LIMIT_SESSIONS_PER_DAY" in proc.stderr
    assert d.sessions == [] and d.slack_calls == []
    assert d.status(ISSUE) == "Ready for implementation"


def test_ac10_missing_variable_fails_the_job(d):
    proc = d.run_issue(ISSUE, env={"USAGE_LIMIT_SESSIONS_PER_DAY": ""})
    assert proc.returncode != 0 and d.sessions == []


def test_ac11_leading_zero_allows_eight(d):
    seed(d, 7)
    proc = d.run_issue(ISSUE, seq="0", env={"USAGE_LIMIT_SESSIONS_PER_DAY": "08"})
    assert d.sessions[:1] == [f"implement {ISSUE}"]
    d.add_story(900, "In review", executor="codex")
    d.add_pr(910, 900)
    proc = d.resume(seq="0", env={"USAGE_LIMIT_SESSIONS_PER_DAY": "08"})
    assert "[USAGE LIMIT SKIP]" in proc.stdout  # the second session sees 8 of 8


# /merge and /cleanup are scripts since #264: no model session, so the limit never stops them.
def test_ac12_stop_before_review_comments_once_and_ends_the_run(r):
    seed(r, 1)
    env = {"USAGE_LIMIT_SESSIONS_PER_DAY": "1"}
    proc = r.resume(seq="0", env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert r.sessions == []
    comments = r.posted(ISSUE)
    assert len(comments) == 1
    assert comments[0].startswith("## Usage limit")
    assert f"/review was not started for #{ISSUE}" in comments[0]
    assert "1 of 1 sessions today" in comments[0]
    assert "00:00 UTC" in comments[0]
    # the same stop again: the comment is the newest one, so it is not posted twice
    r.resume(seq="0", env=env)
    assert len(r.posted(ISSUE)) == 1


def test_ac13_notification_is_due_on_the_first_stop_of_the_day_only(d):
    seed(d, 2)
    env = {"USAGE_LIMIT_SESSIONS_PER_DAY": "2"}
    first = d.run_issue(ISSUE, env=env)
    assert first.returncode == 0
    assert d.usage_output.count("usage_limit_reached=true") == 1
    # the workflow sent it and wrote the marker
    subprocess.run([BASH, SCRIPT.as_posix(), "mark-notified"], check=True,
                   env={**host_env(), "USAGE_LIMIT_STATE_DIR": str(d.mock / "usage")})
    d.run_issue(ISSUE, env=env)
    assert d.usage_output.count("usage_limit_reached=true") == 1


def test_ac15_without_a_marker_the_next_run_asks_again(d):
    seed(d, 2)
    env = {"USAGE_LIMIT_SESSIONS_PER_DAY": "2"}
    d.run_issue(ISSUE, env=env)
    d.run_issue(ISSUE, env=env)  # the send failed, so no marker was written
    assert d.usage_output.count("usage_limit_reached=true") == 2


# ── workflow and Slack event ────────────────────────────────────────────────


def test_ac8_workflow_wires_the_variable_into_the_invoke_step():
    env_block = step("Run dispatcher invoke").split("        run:", 1)[0]
    assert "USAGE_LIMIT_SESSIONS_PER_DAY: ${{ vars.USAGE_LIMIT_SESSIONS_PER_DAY }}" in env_block
    assert "BUDGET" not in env_block


def test_ac14_ac15_notify_step_runs_in_the_same_run_and_marks_after_a_send():
    body = step("Notify usage limit reached")
    assert "steps.invoke.outputs.usage_limit_reached == 'true'" in step_if(body)
    send = body.index("slack-notify.sh usage_limit_reached")
    mark = body.index("dispatcher-usage-limit.sh mark-notified")
    assert send < mark  # under bash -e a failed send never reaches the marker
    names = [m.group(1).strip() for m in re.finditer(r"^      - name: (.+)$", (REPO_ROOT / ".github/workflows/dispatcher.yml").read_text(), re.M)]
    assert names.index("Run dispatcher invoke") < names.index("Notify usage limit reached") < names.index("Save session state cache")


def test_ac16_state_is_saved_even_when_the_invoke_step_fails():
    condition = step_if(step("Save session state cache"))
    assert condition.startswith("always() && steps.guard.outputs.enabled == 'true'")
    assert "steps.poll.outputs.issue_number != ''" in condition


def test_ac19_no_budget_names_in_the_workflow_state_steps():
    text = (REPO_ROOT / ".github/workflows/dispatcher.yml").read_text()
    assert "budget" not in text.lower()
    assert ".dispatcher-budget" not in (REPO_ROOT / ".gitignore").read_text()
    assert ".dispatcher-usage/" in (REPO_ROOT / ".gitignore").read_text().splitlines()


FORBIDDEN = ("BUDGET_DAILY_", "budget_cap_reached", "dispatcher-budget", "all_budget_blocked")


def test_ac18_no_token_budget_names_remain():
    for directory in ("scripts", ".github/workflows"):
        for path in sorted((REPO_ROOT / directory).rglob("*")):
            if not path.is_file():
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                for name in FORBIDDEN:
                    assert name not in line, f"{path.relative_to(REPO_ROOT)}:{number}: {name}"
    assert not (REPO_ROOT / "scripts" / "dispatcher-budget.sh").exists()


def test_usage_limit_reached_event_payload(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@" > "$MOCK_OUT"\nexit "${MOCK_RC:-0}"\n')
    curl.chmod(0o755)
    out = tmp_path / "curl.out"
    env = {**host_env(bin_dir.as_posix()), "MOCK_OUT": str(out),
           "SLACK_WEBHOOK_URL": "https://hooks.slack.test/x"}
    ctx = json.dumps({"sessions_today": 10, "limit": 10, "settings_url": "https://example.test/vars"})
    proc = subprocess.run([BASH, NOTIFY.as_posix(), "usage_limit_reached", ctx], env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    payload = out.read_text()
    assert "10 of 10" in payload and "00:00 UTC" in payload and "https://example.test/vars" in payload
    assert "Unrecognised" not in proc.stderr
    failing = subprocess.run([BASH, NOTIFY.as_posix(), "usage_limit_reached", ctx], env={**env, "MOCK_RC": "22"},
                             capture_output=True, text=True)
    assert failing.returncode != 0


# ── review findings on the first head ───────────────────────────────────────


def test_a_counter_that_cannot_be_written_starts_nothing(d):
    proc = d.run_issue(ISSUE, seq="0,0,0,0", env={"USAGE_LIMIT_STATE_DIR": "/dev/null/usage"})
    assert d.sessions == []
    assert "[USAGE LIMIT ERROR] could not record the session start" in proc.stderr


def test_cache_keys_differ_per_attempt_and_the_save_uses_the_day_it_ends_on():
    for name in ("Restore session state cache", "Save session state cache"):
        assert "${{ github.run_attempt }}" in step(name), name
    assert "env.SAVE_DAY" in step("Save session state cache")
    assert "env.TODAY" not in step("Save session state cache").split("key:", 1)[1]
    assert "date -u" in step("Set cache save date")
    assert step_if(step("Set cache save date")).startswith("always()")


def test_an_invalid_limit_before_cleanup_still_notifies_the_closure(tmp_path):
    d = Dispatcher(tmp_path)
    d.add_story(ISSUE, "In merge", executor="codex", body="Feature group: #50 Feature: something")
    d.add_pr(PR, ISSUE, merged=True, state="closed")
    d.add_story(700, "Ready for implementation")  # the run's own target needs a session, so the limit is read
    proc = d.run_issue(700, seq="0,0,0,0", env={"USAGE_LIMIT_SESSIONS_PER_DAY": ""})
    assert "[USAGE LIMIT ERROR]" in proc.stderr
    assert d.sessions == []
    assert "Feature #50: all 1 stories Done." in proc.stdout


def test_docs_say_the_script_only_phases_are_not_counted_or_stopped():
    # /merge and /cleanup start no model session since #264 (Codex review of #341).
    doc = (REPO_ROOT / "docs" / "DISPATCHER-CONFIG.md").read_text(encoding="utf-8")
    section = doc.split("## Usage limit", 1)[1].split("\n## ", 1)[0]
    assert "script-only phases" in section
    assert "`/review`, `/merge` and `/cleanup`" not in section
    assert "before `/review`, `/merge` or `/cleanup`" not in section
