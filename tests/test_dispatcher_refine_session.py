"""``dispatcher-invoke.sh --refine <n>`` starts one refinement session (issue #319, F2.5, F2.6, F2.9).

Drives the real ``scripts/dispatcher-invoke.sh`` with the fakes in ``tests/dispatcher_fakes.py``.
The fake ``claude`` recognises the ``/refine`` prompt and logs it as ``refine <n>``. The workflow
is read as text, like ``tests/test_dispatcher_workflow_allowance.py``.
"""

from __future__ import annotations

import re

import pytest

from tests.dispatcher_fakes import BASH, PO, REPO_ROOT, Dispatcher, needs_tools
from tests.test_dispatcher_claude_session import INIT, expected_argv, result
from tests.test_dispatcher_usage_limit_pause import LIMITED, OK, PAUSE, RESET_TEXT
from tests.test_dispatcher_workflow_allowance import step, step_if

pytestmark = needs_tools

ISSUE = 600
OTHER = 601
SEQ_REFUSE = "1"
SEQ_ERROR = "2"
DOCS = REPO_ROOT / "docs" / "DISPATCHER-CONFIG.md"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "dispatcher.yml"
INVOKE = REPO_ROOT / "scripts" / "dispatcher-invoke.sh"
FORBIDDEN_GH = ("pulls", "git/refs", "git/")


def story(tmp_path, labels, status="In refinement"):
    d = Dispatcher(tmp_path)
    d.add_story(ISSUE, status, executor=None, labels=labels)
    return d


@pytest.fixture
def d(tmp_path):
    return story(tmp_path, ["type:story", "executor:claude-code-haiku"])


# ── AC1: the command line, the model per type, the environment ──────────────


@pytest.mark.parametrize("labels,alias", [
    (["type:epic"], "opus"),
    (["type:feature"], "opus"),
    (["type:story", "executor:claude-code-opus"], "sonnet"),  # the executor: label never picks the tier
    (["type:task"], "sonnet"),
    (["type:bug"], "sonnet"),
    ([], "sonnet"),
])
def test_refine_runs_the_prompt_on_the_tier_of_the_issue_type(tmp_path, labels, alias):
    d = story(tmp_path, labels)

    proc = d.run_refine(ISSUE)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"refine {ISSUE}"]
    [call] = d.claude_calls
    assert call["argv"] == expected_argv(alias)
    assert "--bare" not in call["argv"]
    template = (REPO_ROOT / ".claude" / "commands" / "refine.md").read_text(encoding="utf-8")
    assert call["stdin"].rstrip("\n") == template.replace("$ARGUMENTS", str(ISSUE)).rstrip("\n")
    assert call["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "fake-subscription-token"
    assert not [k for k in call["env"] if k.startswith(("ANTHROPIC_", "OPENAI_"))]
    assert f"[REFINE] #{ISSUE}: starting /refine on {alias}" in proc.stdout
    assert f"[REFINE] #{ISSUE}: /refine finished" in proc.stdout


def test_refine_scrubs_anthropic_variables_from_the_session(d):
    proc = d.run_refine(ISSUE, env={"ANTHROPIC_" + "API_KEY": "k", "ANTHROPIC_BASE_URL": "https://proxy.test"})

    assert proc.returncode == 0, proc.stdout + proc.stderr
    [call] = d.claude_calls
    assert not [k for k in call["env"] if k.startswith(("ANTHROPIC_", "OPENAI_"))]


def test_native_issue_type_picks_the_tier_when_there_is_no_type_label(tmp_path):
    d = story(tmp_path, [])
    issue = d._read(f"issue_{ISSUE}.json")
    d._write(f"issue_{ISSUE}.json", {**issue, "type": {"name": "Feature"}})

    assert d.run_refine(ISSUE).returncode == 0
    assert d.claude_calls[0]["argv"] == expected_argv("opus")


def test_dry_run_prints_the_start_and_starts_nothing(d):
    proc = d.run_refine(ISSUE, "--dry-run")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"[DRY RUN] Would start Claude Code (sonnet) on the subscription for /refine {ISSUE}" in proc.stdout
    assert d.claude_calls == [] and d.sessions == [] and d.usage_count == 0


# ── Command Surface: invalid arguments ──────────────────────────────────────


@pytest.mark.parametrize("value", ["0", "-3", "abc", "12x", "", "1 2", "$(id)"])
def test_refine_needs_a_positive_issue_number(d, value):
    proc = d.run_refine(value)

    assert proc.returncode == 1
    assert "--refine requires a positive issue number" in proc.stderr
    assert d.claude_calls == []


@pytest.mark.parametrize("extra", [("--issue", str(OTHER)), ("--resume",)])
def test_refine_cannot_be_combined(d, extra):
    proc = d.run("--refine", str(ISSUE), *extra)

    assert proc.returncode == 1
    assert "--refine cannot be combined with --issue or --resume" in proc.stderr
    assert d.claude_calls == []


# ── AC2: the pause switch ───────────────────────────────────────────────────


def test_guard_reports_disabled_without_the_variable_so_nothing_leads_to_refine():
    from tests.test_dispatcher_workflow_step import Sandbox  # noqa: PLC0415 (heavy fixture, one use)
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        sb = Sandbox(Path(tmp))
        for value in ("", "false", "TRUE"):
            sb.output.write_text("")
            proc = sb.run("guard", DISPATCHER_ENABLED=value)
            assert proc.returncode == 0 and sb.outputs() == {"enabled": "false"}


def test_every_step_that_leads_to_refine_is_gated_on_the_guard():
    text = WORKFLOW.read_text(encoding="utf-8")
    names = re.findall(r"^      - name: (.+)$", text, re.MULTILINE)
    gated = [n for n in names if "refine_issue" in step(n) and n != "Guard — check DISPATCHER_ENABLED"]
    assert gated, "no step reads refine_issue"
    for name in gated:
        assert "steps.guard.outputs.enabled == 'true'" in step_if(step(name)), name
    # the input reaches the script only through the invoke step's env
    assert "REFINE_ISSUE: ${{ inputs.refine_issue }}" in step("Run dispatcher invoke")
    assert text.count("inputs.refine_issue") == len(re.findall(r"inputs\.refine_issue", text))
    assert "REFINE_ISSUE" not in step("Run dispatcher poll")


# ── AC3: the allowance check ────────────────────────────────────────────────


def test_allowance_below_the_reserve_starts_nothing(d):
    proc = d.run_refine(ISSUE, seq=SEQ_REFUSE)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.allowance_calls == 1 and d.claude_calls == [] and d.sessions == []
    assert f"[RESERVE SKIP] #{ISSUE}: /refine not started" in proc.stdout
    [comment] = d.posted(ISSUE)
    assert comment.startswith("## Subscription allowance check")
    assert f"/refine was not started for #{ISSUE}:" in comment
    assert d.usage_count == 0


def test_an_unreadable_allowance_fails_the_run_and_starts_nothing(d):
    proc = d.run_refine(ISSUE, seq=SEQ_ERROR)

    assert proc.returncode == 1
    assert f"[ALLOWANCE ERROR] #{ISSUE}: /refine not started" in proc.stderr
    assert d.claude_calls == []


# ── AC4: the usage limit ────────────────────────────────────────────────────


def test_a_refinement_counts_once_when_it_starts(d):
    assert d.run_refine(ISSUE).returncode == 0
    assert d.usage_count == 1


def test_the_limit_refuses_the_start_and_sets_the_output_once(d):
    env = {"USAGE_LIMIT_SESSIONS_PER_DAY": "1"}
    assert d.run_refine(ISSUE, env=env).returncode == 0
    assert d.usage_count == 1

    proc = d.run_refine(ISSUE, env=env)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"[USAGE LIMIT SKIP] #{ISSUE}: /refine not started; 1 of 1 sessions today" in proc.stdout
    assert d.usage_count == 1 and len(d.claude_calls) == 1
    assert d.usage_output.count("usage_limit_reached=true") == 1
    assert d.posted(ISSUE)[0].startswith("## Usage limit")


# ── AC5, AC6: one session, nothing else ─────────────────────────────────────


def test_refine_starts_one_session_and_nothing_else(tmp_path):
    d = story(tmp_path, ["type:story", "executor:claude-code-sonnet"])
    d.add_story(OTHER, "Ready to implement")
    d.add_story(OTHER + 1, "Mergeable")
    d.add_pr(901, OTHER + 1)
    d.add_story(OTHER + 2, "In implementation")

    proc = d.run_refine(ISSUE)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(d.claude_calls) == 1
    assert d.sessions == [f"refine {ISSUE}"]
    assert d.phases == []                 # no /merge, no /cleanup
    assert d.board_writes == []           # no board status write
    assert "Checking for resumable work" not in proc.stdout
    assert "Searching for next" not in proc.stdout


def test_refine_makes_no_git_push_branch_or_pr_call(tmp_path):
    d = story(tmp_path, ["type:epic"])
    d.fake_git()

    proc = d.run_refine(ISSUE)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not [c for c in d.git_calls if any(w in c for w in ("push", "checkout", "branch", "switch"))]
    assert d.destructive_calls == []
    gh_log = (d.mock / "gh.log").read_text(encoding="utf-8")
    assert not re.search(r"-X (POST|PATCH|PUT|DELETE) \S*(pulls|git/)", gh_log)
    assert "pr create" not in gh_log
    assert d.board_writes == []


def test_the_workflow_concurrency_group_is_unchanged():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert ("concurrency:\n  group: dispatcher-${{ github.repository }}\n"
            "  cancel-in-progress: false\n") in text


# ── AC7: /unblock after a refinement decision blocker ───────────────────────

BLOCKER = ("## Blocked Declaration\nType: decision\nDeclared by: claude\nQuestion: which?\n"
           "Options:\nA) a — x\nB) b — y\nRecommendation: A")


def test_a_refinement_after_unblock_starts_fresh_and_moves_nothing(tmp_path):
    d = story(tmp_path, ["type:story"], status="In refinement")
    d.add_comment(ISSUE, BLOCKER)
    d.add_comment(ISSUE, "A\n/unblock", login=PO)

    proc = d.run_refine(ISSUE)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"refine {ISSUE}"]
    assert d.board_writes == []
    assert d.status(ISSUE) == "In refinement"
    assert d.posted(ISSUE) == []        # no CLAIM, no CONFIRM
    assert "Attempting to resolve" not in proc.stdout + proc.stderr


def test_the_existing_resume_does_not_move_a_refinement_blocker(tmp_path):
    d = story(tmp_path, ["type:story"], status="In refinement")
    d.add_comment(ISSUE, BLOCKER)
    d.add_comment(ISSUE, "A\n/unblock", login=PO)

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.board_writes == [] and d.status(ISSUE) == "In refinement"
    assert d.posted(ISSUE) == [] and d.sessions == []


# ── AC8: the provider usage limit ───────────────────────────────────────────


def test_a_provider_limit_pauses_and_a_later_start_runs_a_new_session(d):
    d.claude_stream(*LIMITED, exit_code=1)

    proc = d.run_refine(ISSUE)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.sessions == [f"refine {ISSUE}"]
    [record] = d.posted(ISSUE)
    assert record.startswith(PAUSE)
    assert f"`/refine {ISSUE}` for #{ISSUE}" in record and f"Resets: {RESET_TEXT}" in record
    assert "Blocked Declaration" not in record
    assert d.board_writes == [] and d.queued_events == [] and d.slack_calls == []
    assert f"[SESSION RATE LIMIT] /refine #{ISSUE} stopped at a provider usage limit" in proc.stderr
    assert f"[REFINE] #{ISSUE}: /refine finished" not in proc.stdout

    d.claude_stream(*OK)
    again = d.run_refine(ISSUE)

    assert again.returncode == 0, again.stdout + again.stderr
    assert d.sessions == [f"refine {ISSUE}", f"refine {ISSUE}"]
    assert d.usage_count == 2


# ── Failure, AC9 ────────────────────────────────────────────────────────────


def test_a_failed_session_is_reported_like_a_failed_implement(d):
    d.fail("refine")

    proc = d.run_refine(ISSUE)

    assert proc.returncode == 1
    [blocker] = d.posted(ISSUE)
    assert "## Blocked Declaration" in blocker
    assert "Unblocked when: /refine completes successfully" in blocker
    assert [e["event"] for e in d.queued_events] == ["po_blocker"]
    assert d.board_writes == []


def test_a_rejected_token_gives_the_implementation_text(d):
    d.claude_stream(INIT, result("error_during_execution", True, api_error_status=401,
                                 text="API Error: 401"), exit_code=1)

    proc = d.run_refine(ISSUE)

    assert proc.returncode == 1
    assert (f"[SESSION ERROR] /refine #{ISSUE}: the subscription token was rejected (401). Run "
            "'claude setup-token' again and save the CLAUDE_CODE_OAUTH_TOKEN secret with nothing "
            "before or after the token") in proc.stderr


def test_a_session_that_left_a_decision_blocker_is_announced(d):
    d.add_comment(ISSUE, BLOCKER)

    proc = d.run_refine(ISSUE)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert [e["event"] for e in d.queued_events] == ["decision_blocker"]


# ── AC10: the workflow ──────────────────────────────────────────────────────


def test_the_workflow_has_an_optional_refine_issue_input():
    text = WORKFLOW.read_text(encoding="utf-8")
    block = text.split("workflow_dispatch:", 1)[1].split("\nconcurrency:", 1)[0]
    assert re.search(r"refine_issue:\n(?:        .*\n)*?        required: false\n(?:        .*\n)*?        default: \"\"", block)
    assert "type: string" in block


def test_the_invoke_step_passes_the_input_and_the_script_prefers_it():
    body = step("Run dispatcher invoke")
    assert "REFINE_ISSUE: ${{ inputs.refine_issue }}" in body
    assert "run: bash scripts/dispatcher-workflow-step.sh invoke" in body
    for name in ("Restore session state cache", "Install Claude Code", "Configure git to authenticate through gh",
                 "Run dispatcher invoke", "Save session state cache"):
        assert "inputs.refine_issue != ''" in step_if(step(name)), name
    # the poll-driven conditions are kept, so an empty input leaves the run as it was
    assert "steps.poll.outputs.issue_number != '' || steps.poll.outputs.resumable == 'true'" in step_if(body)


def test_the_header_names_the_refine_mode():
    head = INVOKE.read_text(encoding="utf-8").split("set -euo pipefail", 1)[0]
    assert "--refine <issue-number>" in head and "issue #319" in head


# ── AC12: the docs ──────────────────────────────────────────────────────────


def test_the_config_doc_describes_refinement_sessions():
    text = DOCS.read_text(encoding="utf-8")
    assert re.search(r"^## Refinement sessions$", text, re.MULTILINE)
    section = text.split("## Refinement sessions", 1)[1].split("\n## ", 1)[0]
    for needle in ("--refine", "refine_issue", "opus", "sonnet", "no branch", "board status", "#474"):
        assert needle in section, needle
