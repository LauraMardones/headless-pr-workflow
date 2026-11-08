"""The decisions moved out of ``.github/workflows/dispatcher.yml`` (issue #434, #368 → F6, ADR-014 → Block 3).

Two layers: ``scripts/dispatcher-workflow-step.sh`` run step by step against fake scripts that log
their arguments, and the workflow file as text (every ``run:`` is one command; cache keys, saves and
the state-writer rule that keep state across cancelled and re-run workflows).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.dispatcher_fakes import BASH, needs_tools
from tests.script_tools import host_env
from tests.test_dispatcher_workflow_allowance import step, step_if, steps

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "dispatcher-workflow-step.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "dispatcher.yml"

pytestmark = needs_tools

LOGGER = '#!/usr/bin/env bash\necho "{name} $*" >> "$STEP_LOG"\n'
POLL = ('#!/usr/bin/env bash\necho "poll $*" >> "$STEP_LOG"\n'
        '[ -n "${POLL_FAIL:-}" ] && exit 3\ncat "$POLL_JSON_FILE"\n')
SLACK = '#!/usr/bin/env bash\necho "slack $*" >> "$STEP_LOG"\n[ -n "${SLACK_FAIL:-}" ] && exit 1\nexit 0\n'
USAGE = ('#!/usr/bin/env bash\necho "usage-limit $*" >> "$STEP_LOG"\n'
         '[ "$1" = status ] && echo \'{"sessions_today":10,"limit":10}\'\nexit 0\n')
GH = '#!/usr/bin/env bash\necho "gh $*" >> "$STEP_LOG"\n'


class Sandbox:
    def __init__(self, tmp: Path):
        self.root = tmp
        self.scripts = tmp / "scripts"
        self.bin = tmp / "bin"
        self.scripts.mkdir()
        self.bin.mkdir()
        shutil.copy(SCRIPT, self.scripts / SCRIPT.name)
        for name, text in {
            "dispatcher-poll.sh": POLL, "dispatcher-invoke.sh": LOGGER.format(name="invoke"),
            "dispatcher-trigger-check.sh": LOGGER.format(name="trigger-check"),
            "dispatcher-notify-retry.sh": LOGGER.format(name="retry"), "slack-notify.sh": SLACK,
            "dispatcher-usage-limit.sh": USAGE,
        }.items():
            (self.scripts / name).write_text(text, newline="\n")
        (self.bin / "gh").write_text(GH, newline="\n")
        for path in [*self.scripts.glob("*.sh"), self.bin / "gh"]:
            path.chmod(0o755)
        self.log = tmp / "step.log"
        self.output = tmp / "github_output"
        self.env_file = tmp / "github_env"
        self.poll_json = tmp / "poll.json"
        self.set_poll({"next_issue": None, "resumable": False})

    def set_poll(self, value: dict) -> None:
        self.poll_json.write_text(json.dumps(value))

    def run(self, step_name: str, **env):
        environ = {**host_env(self.bin.as_posix()), "STEP_LOG": self.log.as_posix(), "GITHUB_OUTPUT": self.output.as_posix(),
                   "GITHUB_ENV": self.env_file.as_posix(), "POLL_JSON_FILE": self.poll_json.as_posix(), "REPO": "o/r", **env}
        return subprocess.run([BASH, (self.scripts / SCRIPT.name).as_posix(), step_name], env=environ,
                              capture_output=True, text=True)

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def outputs(self) -> dict[str, str]:
        text = self.output.read_text() if self.output.exists() else ""
        return dict(line.split("=", 1) for line in text.splitlines())


@pytest.fixture
def sb(tmp_path):
    return Sandbox(tmp_path)


# ── guard ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value,expected", [("true", "true"), ("false", "false"), ("", "false"), ("TRUE", "false"), ("1", "false")])
def test_guard_enables_only_on_the_exact_value_true(sb, value, expected):
    proc = sb.run("guard", DISPATCHER_ENABLED=value)
    assert proc.returncode == 0 and sb.outputs() == {"enabled": expected}
    assert ("Dispatcher disabled" in proc.stderr) is (expected == "false")


def test_guard_with_the_variable_unset_is_disabled(sb):
    assert sb.run("guard").returncode == 0 and sb.outputs() == {"enabled": "false"}


# ── scope: the state-writer rule ─────────────────────────────────────────────


@pytest.mark.parametrize("event,ref,default,writer", [
    ("schedule", "refs/heads/main", "main", "true"),
    ("schedule", "refs/heads/feature", "main", "true"),          # every scheduled run is the writer
    ("workflow_dispatch", "refs/heads/main", "main", "true"),    # a manual run on the default branch
    ("workflow_dispatch", "refs/heads/feature", "main", "false"),
    ("workflow_dispatch", "refs/tags/v1", "main", "false"),
    ("workflow_dispatch", "refs/heads/main", "trunk", "false"),  # the default branch is another one
    ("workflow_dispatch", "refs/heads/main-2", "main", "false"),
    ("workflow_dispatch", "refs/heads/main", "", "false"),       # an unknown default branch is never the writer
])
def test_scope_is_the_state_writer_only_for_schedule_or_the_default_branch(sb, event, ref, default, writer):
    proc = sb.run("scope", EVENT_NAME=event, REF=ref, DEFAULT_BRANCH=default)
    assert proc.returncode == 0 and sb.outputs() == {"state_writer": writer}
    assert ("read-only" in proc.stderr) is (writer == "false")


# ── cache dates ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("step_name,variable", [("cache-date", "TODAY"), ("cache-save-date", "SAVE_DAY")])
def test_cache_dates_are_the_utc_day_in_the_environment_file(sb, step_name, variable):
    before = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    proc = sb.run(step_name)
    after = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    assert proc.returncode == 0
    line = sb.env_file.read_text().strip()
    assert re.fullmatch(rf"{variable}=\d{{4}}-\d{{2}}-\d{{2}}", line)
    assert line.split("=")[1] in (before, after)


def test_steps_that_write_the_runner_files_refuse_to_run_without_them(sb):
    for step_name, name in (("guard", "GITHUB_OUTPUT"), ("cache-date", "GITHUB_ENV")):
        proc = sb.run(step_name, **{name: ""})
        assert proc.returncode == 2 and name in proc.stderr


# ── poll ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("writer,flag", [("true", ""), ("false", " --dry-run"), ("", " --dry-run")])
def test_poll_runs_dry_unless_the_state_writer(sb, writer, flag):
    assert sb.run("poll", STATE_WRITER=writer).returncode == 0
    assert sb.calls() == [f"poll --repo o/r{flag}"]


@pytest.mark.parametrize("poll,issue,resumable", [
    ({"next_issue": 42, "resumable": False}, "42", "false"),
    ({"next_issue": None, "resumable": True}, "", "true"),
    ({"next_issue": 7, "resumable": True}, "7", "true"),
    ({"next_issue": None, "resumable": False}, "", "false"),
])
def test_poll_reports_the_next_issue_and_whether_work_is_resumable(sb, poll, issue, resumable):
    sb.set_poll(poll)
    assert sb.run("poll", STATE_WRITER="true").returncode == 0
    assert sb.outputs() == {"issue_number": issue, "resumable": resumable}


@pytest.mark.parametrize("body", ["{}", '{"resumable": "yes"}', "not json", ""])
def test_a_poll_that_does_not_report_resumable_is_a_failed_step(sb, body):
    sb.poll_json.write_text(body)
    proc = sb.run("poll", STATE_WRITER="true")
    assert proc.returncode != 0 and not sb.outputs()


def test_a_failed_poll_is_a_failed_step_without_outputs(sb):
    proc = sb.run("poll", STATE_WRITER="true", POLL_FAIL="1")
    assert proc.returncode == 3 and not sb.outputs()


# ── invoke, trigger check, retry ─────────────────────────────────────────────


@pytest.mark.parametrize("writer,flag", [("true", ""), ("false", " --dry-run")])
@pytest.mark.parametrize("issue", ["42", ""])
def test_invoke_runs_the_scheduled_form_and_runs_dry_unless_the_state_writer(sb, writer, flag, issue):
    """Issue #474: the invoke step asks the scheduler itself; the poll's choice is not passed on."""
    assert sb.run("invoke", STATE_WRITER=writer, NEXT_ISSUE=issue).returncode == 0
    assert sb.calls() == [f"invoke --repo o/r{flag}"]


@pytest.mark.parametrize("writer,flag", [("true", ""), ("false", " --dry-run")])
def test_invoke_with_a_refine_input_runs_only_the_refine_form(sb, writer, flag):
    assert sb.run("invoke", STATE_WRITER=writer, REFINE_ISSUE="12").returncode == 0
    assert sb.calls() == [f"invoke --repo o/r{flag} --refine 12"]


def test_invoke_passes_the_exit_code_through(sb):
    (sb.scripts / "dispatcher-invoke.sh").write_text("#!/usr/bin/env bash\nexit 7\n", newline="\n")
    assert sb.run("invoke", STATE_WRITER="true", NEXT_ISSUE="").returncode == 7


@pytest.mark.parametrize("writer,flag", [("true", ""), ("false", " --dry-run")])
def test_trigger_check_and_retry_run_dry_unless_the_state_writer(sb, writer, flag):
    assert sb.run("trigger-check", STATE_WRITER=writer).returncode == 0
    assert sb.run("retry", STATE_WRITER=writer).returncode == 0
    assert sb.calls() == [f"trigger-check --repo o/r{flag}", f"retry run --repo o/r{flag}"]


@pytest.mark.parametrize("step_name", ["poll", "invoke", "trigger-check", "retry", "usage-limit-notify"])
def test_steps_that_need_the_repository_refuse_without_it(sb, step_name):
    proc = sb.run(step_name, REPO="", STATE_WRITER="true")
    assert proc.returncode == 2 and "REPO" in proc.stderr and sb.calls() == []


# ── usage limit notification ─────────────────────────────────────────────────


def test_usage_limit_notification_builds_the_context_and_marks_after_the_send(sb):
    assert sb.run("usage-limit-notify").returncode == 0
    calls = sb.calls()
    assert calls[0] == "usage-limit status"
    slack = json.loads(calls[1].split(" usage_limit_reached ", 1)[1])
    assert slack == {"sessions_today": 10, "limit": 10, "settings_url": "https://github.com/o/r/settings/variables/actions"}
    assert calls[2] == "usage-limit mark-notified" and len(calls) == 3


def test_usage_limit_notification_that_is_not_delivered_writes_no_marker_and_fails(sb):
    proc = sb.run("usage-limit-notify", SLACK_FAIL="1")
    assert proc.returncode != 0 and "not delivered" in proc.stderr
    assert "usage-limit mark-notified" not in sb.calls()


def test_usage_limit_notification_with_a_failing_status_sends_nothing(sb):
    (sb.scripts / "dispatcher-usage-limit.sh").write_text("#!/usr/bin/env bash\nexit 2\n", newline="\n")
    assert sb.run("usage-limit-notify").returncode != 0 and not any(c.startswith("slack") for c in sb.calls())


# ── git auth and usage ───────────────────────────────────────────────────────


def test_git_auth_runs_gh_auth_setup_git(sb):
    assert sb.run("git-auth").returncode == 0 and sb.calls() == ["gh auth setup-git"]


@pytest.mark.parametrize("args", [[], ["nonsense"], ["poll", "extra"]])
def test_invalid_usage_exits_two(sb, args):
    proc = subprocess.run([BASH, (sb.scripts / SCRIPT.name).as_posix(), *args], env=host_env(sb.bin.as_posix()),
                          capture_output=True, text=True)
    assert proc.returncode == 2 and ("usage" in proc.stderr or "unknown step" in proc.stderr)


# ── the workflow file ────────────────────────────────────────────────────────

NPM = re.compile(r"^npm install -g @anthropic-ai/claude-code@\d+\.\d+\.\d+$")
SCRIPT_CALL = re.compile(r"^bash scripts/[A-Za-z0-9_.-]+\.sh( [A-Za-z0-9_-]+)*$")


def run_lines(body: str) -> list[str]:
    return re.findall(r"^ {8}run:(.*)$", body, re.MULTILINE)


def test_every_run_in_the_workflow_is_one_command():
    seen = 0
    for name, body in steps():
        for line in run_lines(body):
            command = line.strip()
            seen += 1
            assert command and command not in ("|", ">", "|-"), f"{name}: a multi-line run:"
            assert SCRIPT_CALL.match(command) or NPM.match(command), f"{name}: {command}"
            assert not re.search(r"(^|\s)gh\s", command), name
    assert seen == 11
    assert "run: |" not in WORKFLOW.read_text()


def test_every_called_script_exists_and_is_executable_in_git():
    for _, body in steps():
        for line in run_lines(body):
            match = re.match(r"\s*bash (scripts/\S+)", line)
            if match:
                assert (REPO_ROOT / match.group(1)).is_file(), match.group(1)


def cache_saves() -> list[tuple[str, str]]:
    return [(name, body) for name, body in steps() if "uses: actions/cache/save@" in body]


def test_every_cache_save_key_has_the_run_id_and_the_run_attempt():
    saves = cache_saves()
    assert len(saves) == 3
    for name, body in saves:
        key = re.search(r"^ {10}key: (.+)$", body, re.MULTILINE).group(1)
        assert "${{ github.run_id }}-${{ github.run_attempt }}" in key, name


def test_every_state_save_runs_after_a_failed_or_cancelled_step():
    for name, body in cache_saves():
        condition = step_if(body)
        assert "always()" in condition, name
        assert "!cancelled()" not in condition, name


def test_the_poll_state_save_has_always_and_keeps_its_guards():
    condition = step_if(step("Save poll notification state"))
    assert condition.startswith("${{ always() && steps.poll.outcome == 'success'")
    assert "steps.scope.outputs.state_writer == 'true'" in condition


def test_each_restore_matches_by_prefix_so_a_rerun_gets_the_newest_snapshot():
    restores = [(n, b) for n, b in steps() if "uses: actions/cache/restore@" in b]
    assert len(restores) == 2
    for name, body in restores:
        key = re.search(r"^ {10}key: (.+)$", body, re.MULTILINE).group(1)
        prefix = re.search(r"restore-keys: \|\n\s+(\S+)", body).group(1)
        assert key.startswith(prefix) and "github.run_attempt" in key, name
        assert "github.run_id" not in prefix and "github.run_attempt" not in prefix, name


def test_only_the_state_writer_saves_the_notification_state():
    for name in ("Save poll notification state", "Save refinement notification state"):
        assert "steps.scope.outputs.state_writer == 'true'" in step_if(step(name)), name
    # the session cache is ref-scoped and its runs are --dry-run off the default branch (ADR-014 rule 3)
    assert "state_writer" not in step_if(step("Save session state cache"))


def test_the_checks_hold_on_the_workflow_at_this_head():
    proc = subprocess.run([BASH, (REPO_ROOT / "scripts" / "dispatcher-change-check.sh").as_posix(), "--files",
                           WORKFLOW.as_posix()], capture_output=True, text=True, env=host_env(), cwd=REPO_ROOT)
    assert proc.returncode == 0 and "PASS" in proc.stdout and "saved-state" in proc.stdout, proc.stdout
