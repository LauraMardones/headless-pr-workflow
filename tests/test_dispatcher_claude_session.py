"""Claude sessions run Claude Code on the subscription token (issue #314, ADR-012).

Drives the real ``scripts/dispatcher-invoke.sh`` end to end with the fakes in
``tests/dispatcher_fakes.py``: the fake ``claude`` on ``CLAUDE_BIN`` keeps the
argv, stdin and environment of every call, and its stream-json output can be
replaced per test. The allowance script is stubbed to ``start``.
"""

from __future__ import annotations

import time

import pytest

from tests.dispatcher_fakes import HAVE_TOOLS, REPO_ROOT, Dispatcher

pytestmark = pytest.mark.skipif(not HAVE_TOOLS, reason="requires bash, jq and timeout")

STORY = 500
PR = 900
HEAD = "a" * 40
NEW_HEAD = "b" * 40
TIERS = {"claude-code-haiku": "haiku", "claude-code-sonnet": "sonnet", "claude-code-opus": "opus"}

INIT = {"type": "system", "subtype": "init", "apiKeySource": "none", "model": "fake"}


def result(subtype="success", is_error=False, text="done", **extra):
    return {"type": "result", "subtype": subtype, "is_error": is_error, "num_turns": 4,
            "duration_ms": 12400, "result": text,
            "usage": {"input_tokens": 10, "cache_creation_input_tokens": 100,
                      "cache_read_input_tokens": 1000, "output_tokens": 7}, **extra}


def tool_use(name, tool_input):
    return {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "working"},
        {"type": "tool_use", "id": "t", "name": name, "input": tool_input}]}}


def expected_argv(alias: str) -> list[str]:
    return ["-p", "--output-format", "stream-json", "--verbose",
            "--permission-mode", "bypassPermissions", "--strict-mcp-config",
            "--model", alias, "--max-turns", "3"]  # AGENT_MAX_TURNS=3 in the fakes


@pytest.fixture
def d(tmp_path):
    """A Claude-tier story ready for implementation, with its PR already open."""
    dispatcher = Dispatcher(tmp_path)
    dispatcher.add_story(STORY, "Ready for implementation")
    dispatcher.add_pr(PR, STORY)
    return dispatcher


def assert_failed_closed(d: Dispatcher, proc) -> None:
    """A failed /implement: blocker posted, no hand-off, no board write after E1's own."""
    assert proc.returncode != 0
    assert d.sessions == [f"implement {STORY}"]
    assert d.board_writes == [f"{STORY}=In implementation"]
    blockers = d.posted(STORY)
    assert len(blockers) == 1 and "## Blocked Declaration" in blockers[0]
    assert "[HANDOFF]" not in proc.stdout


# ── Starting a session (AC1–AC4) ────────────────────────────────────────────


@pytest.mark.parametrize("tier,alias", TIERS.items())
def test_session_starts_with_the_ac1_flags_and_the_tier_alias(tmp_path, tier, alias):
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "Ready for implementation", executor=tier)
    d.add_pr(PR, STORY)

    proc = d.run_issue(STORY)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    calls = d.claude_calls
    assert len(calls) == 1
    assert calls[0]["argv"] == expected_argv(alias)
    assert "--bare" not in calls[0]["argv"]
    assert d.session_providers == ["implement claude-code"]
    assert f"[HANDOFF] #{STORY}: handed to the Codex review loop" in proc.stdout


def test_prompt_on_stdin_is_the_substituted_command_file(d):
    proc = d.run_issue(STORY)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    raw = (REPO_ROOT / ".claude" / "commands" / "implement.md").read_bytes().decode("utf-8")
    # What `$(sed "s/\$ARGUMENTS/<n>/g" file)` gives: trailing newlines removed.
    assert d.claude_calls[0]["stdin"] == raw.replace("$ARGUMENTS", str(STORY)).rstrip("\n")


def test_review_of_a_codex_story_runs_on_opus(tmp_path):
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "Ready for implementation", executor="codex")
    d.add_pr(PR, STORY)

    proc = d.run_issue(STORY)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.session_providers == ["implement openai", "review claude-code", "merge openai", "cleanup openai"]
    calls = d.claude_calls
    assert len(calls) == 1
    assert calls[0]["argv"] == expected_argv("opus")
    assert calls[0]["stdin"].startswith(f"Review PR #{PR}")


def test_session_inherits_gh_token(d):
    proc = d.run_issue(STORY)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.claude_calls[0]["env"]["GH_TOKEN"] == "fake-token"


# ── Token and metered-key guard (AC6–AC9) ───────────────────────────────────


def test_token_with_stray_whitespace_reaches_claude_cleaned(d):
    proc = d.run_issue(STORY, env={"CLAUDE_CODE_OAUTH_TOKEN": " sk-oat-SECRET-123 \t\r\n"})

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.claude_calls[0]["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-oat-SECRET-123"
    assert "sk-oat-SECRET-123" not in proc.stdout + proc.stderr


@pytest.mark.parametrize("token", ["", " \t\r\n "])
def test_missing_token_fails_before_claude_starts(d, token):
    proc = d.run_issue(STORY, env={"CLAUDE_CODE_OAUTH_TOKEN": token})

    assert proc.returncode != 0
    assert "Error: Secret 'CLAUDE_CODE_OAUTH_TOKEN' is not set in the environment." in proc.stderr
    assert d.claude_calls == []
    assert d.sessions == []
    assert len(d.posted(STORY)) == 1  # the mid-cycle blocker


def test_anthropic_variables_are_scrubbed_from_the_session(d):
    proc = d.run_issue(STORY, env={"ANTHROPIC_" + "API_KEY": "sk-ant-api-METERED",
                                   "ANTHROPIC_AUTH_TOKEN": "other", "ANTHROPIC_BASE_URL": "https://proxy.test"})

    assert proc.returncode == 0, proc.stdout + proc.stderr
    env = d.claude_calls[0]["env"]
    assert [k for k in env if k.startswith("ANTHROPIC_")] == []
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "fake-subscription-token"


@pytest.mark.parametrize("source", ["ANTHROPIC_" + "API_KEY", "apiKeyHelper", None])
def test_api_key_source_other_than_none_fails_closed(d, source):
    init = dict(INIT, apiKeySource=source) if source else {k: v for k, v in INIT.items() if k != "apiKeySource"}
    d.claude_stream(init, result())

    proc = d.run_issue(STORY)

    assert_failed_closed(d, proc)
    shown = source if source else "missing"
    assert f"[SESSION ERROR] apiKeySource={shown}; only the subscription token may be used" in proc.stderr


@pytest.mark.parametrize("events,exit_code", [
    ([INIT, result("error_during_execution", True, "API Error: 401 OAuth access token is invalid")], 1),
    ([INIT, result("error_during_execution", True, "failed", api_error_status=401)], 1),
])
def test_rejected_token_fails_closed_with_the_setup_token_hint(d, events, exit_code):
    d.claude_stream(*events, exit_code=exit_code)

    proc = d.run_issue(STORY)

    assert_failed_closed(d, proc)
    assert ("the subscription token was rejected (401). Run 'claude setup-token' again and save the "
            "CLAUDE_CODE_OAUTH_TOKEN secret with nothing before or after the token") in proc.stderr


def test_token_never_appears_in_the_log(d):
    token = "sk-oat-VISIBLE-ONLY-TO-CLAUDE"
    d.claude_stream(INIT, tool_use("Bash", {"command": f"echo {token}"}), result(text=f"used {token}"))

    proc = d.run_issue(STORY, env={"CLAUDE_CODE_OAUTH_TOKEN": token})

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert token not in proc.stdout + proc.stderr
    assert "[session tool] Bash: echo [redacted]" in proc.stderr


# ── Result handling (AC10–AC12) ─────────────────────────────────────────────


def test_success_returns_zero_with_the_result_text(d):
    d.claude_stream(INIT, result(text="IMPLEMENTED-AND-HANDED-OFF"))

    proc = d.run_issue(STORY)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "IMPLEMENTED-AND-HANDED-OFF" in proc.stdout
    assert f"✓ /implement completed for #{STORY}" in proc.stdout
    assert d.board_writes == [f"{STORY}=In implementation", f"{STORY}=In review"]


@pytest.mark.parametrize("events,exit_code,reason", [
    ([INIT, result()], 1, "Claude Code exited with code 1"),
    ([INIT, tool_use("Bash", {"command": "ls"})], 0, "the stream has no result message"),
    ([INIT, result("error_max_turns", False, None)], 0, "result subtype error_max_turns, is_error false"),
    ([INIT, result("success", True)], 0, "result subtype success, is_error true"),
    (["not json", INIT, result("error_during_execution", True)], 0, "subtype error_during_execution"),
])
def test_unsuccessful_session_fails_closed(d, events, exit_code, reason):
    d.claude_stream(*events, exit_code=exit_code)

    proc = d.run_issue(STORY)

    assert_failed_closed(d, proc)
    assert "[SESSION ERROR]" in proc.stderr and reason in proc.stderr


def test_rate_limit_fails_closed_with_its_own_line(d):
    d.claude_stream(INIT, result("error_during_execution", True, "limit reached", error="rate_limit"), exit_code=1)

    proc = d.run_issue(STORY)

    assert_failed_closed(d, proc)
    assert f"[SESSION RATE LIMIT] /implement #{STORY} stopped at a provider usage limit; resume is #316" in proc.stderr


# ── Wall clock (AC5) ────────────────────────────────────────────────────────


def test_session_past_the_wall_clock_is_stopped(d):
    d.claude_sleeps(120)

    started = time.monotonic()
    # Long enough for the pre-flight before the session on a slow runner.
    proc = d.run_issue(STORY, env={"AGENT_MAX_WALLCLOCK_SECONDS": "20"})

    assert time.monotonic() - started < 100
    assert_failed_closed(d, proc)
    assert "stopped by the wall-clock bound" in proc.stderr


def test_no_time_left_does_not_start_claude(d):
    proc = d.run_issue(STORY, env={"DISPATCH_JOB_START_TS": str(int(time.time()) - 1000),
                                   "AGENT_MAX_WALLCLOCK_SECONDS": "600"})

    assert proc.returncode != 0
    assert d.claude_calls == []
    assert "not started: no time left of AGENT_MAX_WALLCLOCK_SECONDS" in proc.stderr
    assert len(d.posted(STORY)) == 1


# ── Log (AC13) ──────────────────────────────────────────────────────────────


def test_log_has_one_line_per_tool_call_and_the_summary(d):
    d.claude_stream(
        INIT,
        tool_use("Bash", {"command": "git status"}),
        tool_use("Read", {"file_path": "README.md"}),
        tool_use("Bash", {"command": "x" * 300 + "\nsecond line"}),
        result(),
    )

    proc = d.run_issue(STORY)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    tool_lines = [line for line in proc.stderr.splitlines() if line.startswith("[session tool] ")]
    assert tool_lines == [
        "[session tool] Bash: git status",
        '[session tool] Read: {"file_path":"README.md"}',
        "[session tool] Bash: " + "x" * 200,
    ]
    assert (f"[SESSION] /implement #{STORY} on sonnet: success, 4 turns, 12 s, apiKeySource none, "
            "input 1110 / output 7 tokens") in proc.stderr
    assert '"subtype": "init"' not in proc.stderr and '"subtype":"init"' not in proc.stderr


def test_stream_file_is_removed(d, tmp_path):
    proc = d.run_issue(STORY, env={"TMPDIR": tmp_path.as_posix()})

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert list(tmp_path.rglob("stream.jsonl")) == []


# ── Dry run (AC18) ──────────────────────────────────────────────────────────


def test_dry_run_names_claude_code_and_starts_nothing(d):
    proc = d.run_issue(STORY, "--dry-run")

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"[DRY RUN] Would start Claude Code (sonnet) on the subscription for /implement {STORY}" in proc.stdout
    assert d.claude_calls == []


# ── Resume pass (#293) ──────────────────────────────────────────────────────


def test_resume_pass_sessions_run_on_claude_code(tmp_path):
    d = Dispatcher(tmp_path)
    d.add_story(STORY, "In implementation")
    d.add_pr(PR, STORY)
    d.add_findings(PR, HEAD)
    d.add_story(600, "In merge")
    d.add_pr(960, 600)
    d.on_session("implement", f"""
jq 'map(if .number == {PR} then .head.sha = "{NEW_HEAD}" else . end)' "$MOCK_DIR/pulls.json" > "$MOCK_DIR/p.tmp" && mv "$MOCK_DIR/p.tmp" "$MOCK_DIR/pulls.json"
jq '. + {{"ITEM_{STORY}": "In review"}}' "$MOCK_DIR/state.json" > "$MOCK_DIR/s.tmp" && mv "$MOCK_DIR/s.tmp" "$MOCK_DIR/state.json"
""")

    proc = d.resume()

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert d.session_providers == ["merge claude-code", "cleanup claude-code", "implement claude-code"]
    assert [call["argv"] for call in d.claude_calls] == [expected_argv("sonnet")] * 3
