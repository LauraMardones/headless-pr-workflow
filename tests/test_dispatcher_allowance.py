"""Tests for scripts/dispatcher-allowance.sh (issue #317, ADR-012).

A stub ``claude`` on ``CLAUDE_BIN`` prints fixture stream-json lines shaped
like the trial's ``rate_limit_event`` and records how it was called. Every
case also checks that stdout is exactly one JSON object with every field of
the issue's Output Contract.
"""

from __future__ import annotations

import calendar
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "dispatcher-allowance.sh"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None or shutil.which("timeout") is None,
    reason="requires bash, jq and timeout",
)

TOKEN = "sk-ant-oat01-SECRETVALUE"
FIVE_RESET = calendar.timegm((2026, 10, 10, 15, 0, 0))  # 2026-10-10T15:00:00Z
WEEK_RESET = calendar.timegm((2026, 10, 14, 8, 0, 0))   # 2026-10-14T08:00:00Z

STUB = r"""#!/usr/bin/env bash
{
  printf 'CALLED\n'
  printf 'TOKEN=[%s]\n' "${CLAUDE_CODE_OAUTH_TOKEN-<unset>}"
  printf 'ANTHROPIC_API_KEY=%s\n' "${ANTHROPIC_API_KEY-<unset>}"
  printf 'ANTHROPIC_AUTH_TOKEN=%s\n' "${ANTHROPIC_AUTH_TOKEN-<unset>}"
  printf 'ARGS='; printf '[%s]' "$@"; printf '\n'
} >> "$STUB_LOG"
[[ -n "${STUB_SLEEP:-}" ]] && sleep "$STUB_SLEEP"
[[ -f "${STUB_STDOUT:-/nonexistent}" ]] && cat "$STUB_STDOUT"
[[ -n "${STUB_STDERR:-}" ]] && printf '%s\n' "$STUB_STDERR" >&2
exit "${STUB_EXIT:-0}"
"""

CONTRACT_KEYS = {
    "decision", "source", "reason", "five_hour", "weekly", "api_key_source", "overage_status",
}
WINDOW_KEYS = {"utilization", "remaining_percent", "reserve_percent", "resets_at"}
EXIT_FOR = {"start": 0, "refuse": 1, "error": 2}


def init_line(api_key_source="none"):
    msg = {"type": "system", "subtype": "init", "model": "claude-haiku", "tools": []}
    if api_key_source is not None:
        msg["apiKeySource"] = api_key_source
    return msg


def rate_limit_line(five=0.21, week=0.29, overage="rejected", windows="default"):
    info = {"status": "allowed"}
    if windows == "default":
        windows = {
            "five_hour": {"utilization": five, "resetsAt": FIVE_RESET},
            "seven_day": {"utilization": week, "resetsAt": WEEK_RESET},
        }
    if windows is not None:
        info["unifiedWindows"] = windows
    if overage is not None:
        info["overageStatus"] = overage
    return {"type": "rate_limit_event", "rate_limit_info": info}


def result_line(**extra):
    msg = {"type": "result", "subtype": "success", "is_error": False, "result": "OK",
           "duration_ms": 401, "usage": {"input_tokens": 5, "output_tokens": 2}}
    msg.update(extra)
    return msg


class Env:
    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.bin = tmp_path / "claude-stub"
        self.bin.write_text(STUB)
        self.bin.chmod(0o755)
        self.log = tmp_path / "stub.log"
        self.stdout_fixture = tmp_path / "stream.jsonl"
        self.state = tmp_path / "state"
        self.extra: dict[str, str] = {}

    def lines(self, *msgs, raw: str = ""):
        text = "".join(json.dumps(m) + "\n" for m in msgs) + raw
        self.stdout_fixture.write_text(text)

    def run(self, token: str | None = TOKEN, **env):
        full = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.tmp),
            "CLAUDE_BIN": str(self.bin),
            "STUB_LOG": str(self.log),
            "STUB_STDOUT": str(self.stdout_fixture),
            "ALLOWANCE_STATE_DIR": str(self.state),
        }
        if token is not None:
            full["CLAUDE_CODE_OAUTH_TOKEN"] = token
        full.update(self.extra)
        full.update({k: str(v) for k, v in env.items()})
        proc = subprocess.run(["bash", str(SCRIPT)], env=full, capture_output=True,
                              text=True, timeout=60)
        out = check_contract(proc)
        return proc, out

    @property
    def calls(self) -> int:
        if not self.log.exists():
            return 0
        return self.log.read_text().count("CALLED\n")

    @property
    def hold(self) -> Path:
        return self.state / "hold.json"


def check_contract(proc):
    stdout = proc.stdout.strip()
    assert stdout, f"no stdout; stderr={proc.stderr!r}"
    assert len(stdout.splitlines()) == 1, stdout
    out = json.loads(stdout)
    assert isinstance(out, dict)
    assert set(out) == CONTRACT_KEYS
    for key in ("five_hour", "weekly"):
        assert set(out[key]) == WINDOW_KEYS
    assert out["decision"] in EXIT_FOR
    assert proc.returncode == EXIT_FOR[out["decision"]]
    assert out["source"] in {"probe", "hold", "none"}
    if out["decision"] == "start":
        assert out["reason"] == ""
    else:
        assert out["reason"]
    return out


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


# ─── Decision (AC6) ──────────────────────────────────────────────────────────


def test_both_windows_above_reserve_start(env):
    env.lines(init_line(), rate_limit_line(0.21, 0.29), result_line())
    proc, out = env.run()
    assert proc.returncode == 0
    assert out["decision"] == "start" and out["source"] == "probe"
    assert out["five_hour"] == {"utilization": 0.21, "remaining_percent": 79,
                                "reserve_percent": 10, "resets_at": "2026-10-10T15:00:00Z"}
    assert out["weekly"]["remaining_percent"] == 71
    assert out["weekly"]["reserve_percent"] == 20
    assert out["weekly"]["resets_at"] == "2026-10-14T08:00:00Z"
    assert out["api_key_source"] == "none"
    assert out["overage_status"] == "rejected"
    assert "[RESERVE] five-hour 79% left (reserve 10%), weekly 71% left (reserve 20%) — start" in proc.stderr
    assert env.calls == 1
    assert not env.hold.exists()


def test_five_hour_below_reserve_refuse(env):
    env.lines(init_line(), rate_limit_line(0.95, 0.29), result_line())
    proc, out = env.run()
    assert proc.returncode == 1
    assert out["decision"] == "refuse" and out["source"] == "probe"
    assert "five-hour 5% left, below reserve 10%" in out["reason"]
    assert "[RESERVE SKIP] five-hour 5% left, below reserve 10%; resets 2026-10-10T15:00Z" in proc.stderr


def test_weekly_below_reserve_refuse(env):
    env.lines(init_line(), rate_limit_line(0.21, 0.85), result_line())
    proc, out = env.run()
    assert proc.returncode == 1
    assert "[RESERVE SKIP] weekly 15% left, below reserve 20%; resets 2026-10-14T08:00Z" in proc.stderr
    assert "five-hour" not in out["reason"]


def test_exactly_at_reserve_is_start(env):
    # 0.9 and 0.8 are not exact in binary floating point: equality must still start.
    env.lines(init_line(), rate_limit_line(0.9, 0.8), result_line())
    proc, out = env.run()
    assert proc.returncode == 0, proc.stderr
    assert out["five_hour"]["remaining_percent"] == 10
    assert out["weekly"]["remaining_percent"] == 20


def test_utilization_above_one_refuse(env):
    env.lines(init_line(), rate_limit_line(1.2, 0.1), result_line())
    proc, out = env.run()
    assert proc.returncode == 1
    assert out["five_hour"]["remaining_percent"] == 0


# ─── Reserve settings (AC1, AC2, AC3) ────────────────────────────────────────


@pytest.mark.parametrize("five,week", [(None, None), ("", "")])
def test_reserve_defaults_when_unset_or_empty(env, five, week):
    env.lines(init_line(), rate_limit_line(0.21, 0.29), result_line())
    extra = {}
    if five is not None:
        extra = {"RESERVE_FIVE_HOUR_PERCENT": five, "RESERVE_WEEKLY_PERCENT": week}
    proc, out = env.run(**extra)
    assert out["five_hour"]["reserve_percent"] == 10
    assert out["weekly"]["reserve_percent"] == 20


def test_custom_reserve_values_are_used(env):
    # 79% left in the five-hour window: start under 79, refuse under 80.
    env.lines(init_line(), rate_limit_line(0.21, 0.29), result_line())
    proc, out = env.run(RESERVE_FIVE_HOUR_PERCENT="79", RESERVE_WEEKLY_PERCENT="0")
    assert proc.returncode == 0
    assert out["five_hour"]["reserve_percent"] == 79
    assert out["weekly"]["reserve_percent"] == 0
    proc, out = env.run(RESERVE_FIVE_HOUR_PERCENT="80", RESERVE_WEEKLY_PERCENT="0")
    assert proc.returncode == 1
    assert "five-hour 79% left, below reserve 80%" in out["reason"]


@pytest.mark.parametrize("var", ["RESERVE_FIVE_HOUR_PERCENT", "RESERVE_WEEKLY_PERCENT"])
@pytest.mark.parametrize("value", ["abc", "-1", "101", "10%", "7.5"])
def test_invalid_reserve_is_error_without_probe(env, var, value):
    env.lines(init_line(), rate_limit_line(), result_line())
    proc, out = env.run(**{var: value})
    assert proc.returncode == 2
    assert out["source"] == "none"
    assert var in out["reason"] and f"'{value}'" in out["reason"]
    assert "[ALLOWANCE ERROR]" in proc.stderr
    assert env.calls == 0


def test_raised_reserve_ignores_old_hold_and_refuses(env):
    env.lines(init_line(), rate_limit_line(0.21, 0.85), result_line())
    env.run()  # weekly 15% < 20%: refuse, hold written under (10, 20)
    assert env.hold.exists() and env.calls == 1
    env.lines(init_line(), rate_limit_line(0.21, 0.29), result_line())
    proc, out = env.run(RESERVE_FIVE_HOUR_PERCENT="85")
    assert env.calls == 2  # hold ignored: written under different reserve values
    assert proc.returncode == 1 and out["source"] == "probe"
    assert "five-hour 79% left, below reserve 85%" in out["reason"]


# ─── Fail closed (AC7) ───────────────────────────────────────────────────────


def test_no_rate_limit_event_error(env):
    env.lines(init_line(), result_line())
    proc, out = env.run()
    assert proc.returncode == 2 and out["source"] == "probe"
    assert "no rate_limit_event" in out["reason"]


def test_no_unified_windows_error(env):
    env.lines(init_line(), rate_limit_line(windows=None), result_line())
    proc, out = env.run()
    assert proc.returncode == 2
    assert "unifiedWindows" in out["reason"]


def test_missing_seven_day_error(env):
    windows = {"five_hour": {"utilization": 0.2, "resetsAt": FIVE_RESET}}
    env.lines(init_line(), rate_limit_line(windows=windows), result_line())
    proc, out = env.run()
    assert proc.returncode == 2
    assert "seven_day" in out["reason"]


@pytest.mark.parametrize("bad", ["0.2", None, True])
def test_non_numeric_utilization_error(env, bad):
    windows = {"five_hour": {"utilization": 0.2, "resetsAt": FIVE_RESET},
               "seven_day": {"utilization": bad, "resetsAt": WEEK_RESET}}
    env.lines(init_line(), rate_limit_line(windows=windows), result_line())
    proc, out = env.run()
    assert proc.returncode == 2
    assert "seven_day.utilization" in out["reason"]


def test_stub_exits_nonzero_error(env):
    env.lines(init_line(), rate_limit_line(), result_line(subtype="error_during_execution", is_error=True))
    proc, out = env.run(STUB_EXIT="3")
    assert proc.returncode == 2
    assert "exited with code 3" in out["reason"]


def test_stub_exits_nonzero_without_output_error(env):
    proc, out = env.run(STUB_EXIT="1", STUB_STDOUT="")
    assert proc.returncode == 2
    assert "exited with code 1" in out["reason"]


def test_probe_timeout_error(env):
    env.lines(init_line(), rate_limit_line(), result_line())
    proc, out = env.run(STUB_SLEEP="5", ALLOWANCE_PROBE_TIMEOUT="1")
    assert proc.returncode == 2
    assert "timed out after 1s" in out["reason"]


def test_non_json_lines_and_order_do_not_matter(env):
    env.lines(result_line(), rate_limit_line(0.21, 0.29), init_line(), raw="not json\n")
    proc, out = env.run()
    assert proc.returncode == 0


# ─── Token (AC7, AC8, AC11, AC21) ────────────────────────────────────────────


@pytest.mark.parametrize("token", [None, "", " \t\r\n "])
def test_missing_token_error_without_probe(env, token):
    proc, out = env.run(token=token)
    assert proc.returncode == 2 and out["source"] == "none"
    assert "CLAUDE_CODE_OAUTH_TOKEN" in out["reason"]
    assert env.calls == 0


def test_401_error_names_setup_token(env):
    env.lines(init_line(),
              result_line(subtype="success", is_error=True,
                          result="Failed to authenticate. API Error: 401 OAuth access token is invalid"))
    proc, out = env.run(STUB_EXIT="1")
    assert proc.returncode == 2
    assert "token was rejected" in out["reason"]
    assert "claude setup-token" in out["reason"]
    assert "nothing before or after the token" in out["reason"]


def test_token_whitespace_is_removed(env):
    env.lines(init_line(), rate_limit_line(), result_line())
    proc, _ = env.run(token=" sk-ant-\toat01-SECRET VALUE \r\n")
    assert proc.returncode == 0
    assert "TOKEN=[sk-ant-oat01-SECRETVALUE]" in env.log.read_text()
    assert "length 24 after whitespace removal" in proc.stderr


@pytest.mark.parametrize("scenario", ["start", "refuse", "error", "401"])
def test_token_never_printed(env, scenario):
    fixtures = {
        "start": (init_line(), rate_limit_line(), result_line()),
        "refuse": (init_line(), rate_limit_line(0.99, 0.99), result_line()),
        "error": (init_line(),),
        "401": (init_line(), result_line(is_error=True, result="API Error: 401 " + TOKEN)),
    }
    env.lines(*fixtures[scenario])
    proc, _ = env.run(token=TOKEN + "\n", STUB_STDERR="debug " + TOKEN)
    assert TOKEN not in proc.stdout
    assert TOKEN not in proc.stderr


# ─── Metered-key guard (AC5) ─────────────────────────────────────────────────


def test_api_keys_removed_from_probe_env(env):
    env.lines(init_line(), rate_limit_line(), result_line())
    proc, _ = env.run(ANTHROPIC_API_KEY="sk-ant-api-METERED", ANTHROPIC_AUTH_TOKEN="other")
    assert proc.returncode == 0
    log = env.log.read_text()
    assert "ANTHROPIC_API_KEY=<unset>" in log
    assert "ANTHROPIC_AUTH_TOKEN=<unset>" in log


@pytest.mark.parametrize("source", ["ANTHROPIC_API_KEY", "apiKeyHelper", None])
def test_api_key_source_not_none_error(env, source):
    env.lines(init_line(api_key_source=source), rate_limit_line(), result_line())
    proc, out = env.run()
    assert proc.returncode == 2
    assert "apiKeySource" in out["reason"]


def test_probe_command_line(env):
    env.lines(init_line(), rate_limit_line(), result_line())
    env.run()
    args = env.log.read_text().split("ARGS=", 1)[1].splitlines()[0]
    assert "[-p]" in args
    assert "[--model][haiku]" in args
    assert "[--tools][]" in args
    assert "[--output-format][stream-json]" in args
    assert "[--verbose]" in args
    assert "--bare" not in args


# ─── Overage guard (AC10) ────────────────────────────────────────────────────


@pytest.mark.parametrize("status", ["org_level_enabled", "allowed", ""])
def test_overage_not_rejected_error(env, status):
    env.lines(init_line(), rate_limit_line(overage=status), result_line())
    proc, out = env.run()
    assert proc.returncode == 2
    assert "usage credits look turned on" in out["reason"]


def test_overage_absent_decided_by_numbers(env):
    env.lines(init_line(), rate_limit_line(0.21, 0.29, overage=None), result_line())
    proc, out = env.run()
    assert proc.returncode == 0
    assert out["overage_status"] is None


# ─── Probe at a usage limit (AC9) ────────────────────────────────────────────


def test_rate_limit_result_without_event_refuse(env):
    env.lines(init_line(), result_line(subtype="error_during_execution", is_error=True,
                                       error="rate_limit", result="usage limit reached"))
    proc, out = env.run(STUB_EXIT="1")
    assert proc.returncode == 1
    assert out["decision"] == "refuse" and out["source"] == "probe"
    assert "usage limit" in out["reason"]


# ─── Hold until reset (AC12, AC13) ───────────────────────────────────────────


def test_refusal_writes_hold(env):
    env.lines(init_line(), rate_limit_line(0.95, 0.85), result_line())
    env.run()
    hold = json.loads(env.hold.read_text())
    assert hold["until"] == max(FIVE_RESET, WEEK_RESET)
    assert hold["reserve_five_hour_percent"] == 10
    assert hold["reserve_weekly_percent"] == 20


def test_hold_until_is_latest_reset_among_windows_below(env):
    env.lines(init_line(), rate_limit_line(0.95, 0.29), result_line())
    env.run()
    assert json.loads(env.hold.read_text())["until"] == FIVE_RESET


def future_hold(env, until_offset=3600, five=10, week=20):
    env.state.mkdir(parents=True, exist_ok=True)
    until = int(time.time()) + until_offset
    env.hold.write_text(json.dumps({
        "until": until, "reserve_five_hour_percent": five, "reserve_weekly_percent": week,
        "five_hour": {"utilization": 0.5, "resets_at": until},
        "weekly": {"utilization": 0.85, "resets_at": until},
    }))
    return until


def test_active_hold_refuses_without_probe(env):
    env.lines(init_line(), rate_limit_line(0.21, 0.29), result_line())
    future_hold(env)
    proc, out = env.run()
    assert proc.returncode == 1
    assert out["source"] == "hold"
    assert out["weekly"]["remaining_percent"] == 15
    assert "[RESERVE HOLD] below reserve until" in proc.stderr
    assert env.calls == 0


def test_second_call_after_refusal_uses_hold(env):
    # Reset times far in the future so the hold is still active on the second call.
    future = int(time.time()) + 7200
    env.lines(init_line(), {"type": "rate_limit_event", "rate_limit_info": {
        "overageStatus": "rejected",
        "unifiedWindows": {"five_hour": {"utilization": 0.21, "resetsAt": future},
                           "seven_day": {"utilization": 0.85, "resetsAt": future}}}},
        result_line())
    env.run()
    proc, out = env.run()
    assert env.calls == 1
    assert out["source"] == "hold" and proc.returncode == 1


def test_expired_hold_probes(env):
    env.lines(init_line(), rate_limit_line(0.21, 0.29), result_line())
    future_hold(env, until_offset=-10)
    proc, out = env.run()
    assert env.calls == 1
    assert proc.returncode == 0


def test_changed_reserve_probes(env):
    env.lines(init_line(), rate_limit_line(0.21, 0.29), result_line())
    future_hold(env)
    proc, out = env.run(RESERVE_WEEKLY_PERCENT="15")
    assert env.calls == 1
    assert out["source"] == "probe"


@pytest.mark.parametrize("content", ["{not json", "[]", '{"until": "tomorrow"}', "",
                                     '{"until": 99999999999}\n{"until": 99999999999}'])
def test_malformed_hold_probes(env, content):
    env.lines(init_line(), rate_limit_line(0.21, 0.29), result_line())
    env.state.mkdir(parents=True)
    env.hold.write_text(content)
    proc, out = env.run()
    assert env.calls == 1
    assert out["source"] == "probe" and proc.returncode == 0
