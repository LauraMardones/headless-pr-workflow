"""Workflow contract for the Codex review bookkeeping job (issue #292).

The job does deterministic bookkeeping with the PO's project token. It must
never run a model, never run PR code, and never let two runs act together.

The workflow is parsed textually (PyYAML is not a project dependency).
"""

from __future__ import annotations

import re
from pathlib import Path

from headless_pr_workflow.codex_review_bookkeeping import MAX_REQUESTS_ENV, RETRY_HOURS_ENV
from headless_pr_workflow.review_policy import CODEX_BOT_LOGIN, CODEX_REVIEW_TRIGGER_PHRASE

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "codex-review-bookkeeping.yml"
BOT = f"{CODEX_BOT_LOGIN}[bot]"


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _code() -> str:
    """The workflow without comment lines."""
    return "\n".join(line for line in _workflow().splitlines() if not line.lstrip().startswith("#"))


def _block(key: str) -> str:
    """The indented body of a top-level key."""
    match = re.search(rf"^{re.escape(key)}:\n((?:(?:  .*)?\n)+)", _code() + "\n", re.MULTILINE)
    assert match, f"top-level {key!r} block missing"
    return match.group(1)


def test_four_triggers():
    on = _block("on")

    assert re.findall(r"^  (\w+):", on, re.MULTILINE) == [
        "issue_comment",
        "pull_request_review",
        "schedule",
        "workflow_dispatch",
    ]
    assert re.search(r"^  issue_comment:\n    types: \[created\]$", on, re.MULTILINE)
    assert re.search(r"^  pull_request_review:\n    types: \[submitted\]$", on, re.MULTILINE)
    assert re.search(r'^  schedule:\n    - cron: "\*/15 \* \* \* \*"$', on, re.MULTILINE)


def test_event_runs_start_only_for_the_codex_bot():
    condition = re.search(r"^    if: >-\n((?:      .+\n)+)", _code(), re.MULTILINE)
    assert condition, "job-level `if:` is required"
    clauses = [clause.strip() for clause in condition.group(1).split("||")]

    assert clauses == [
        "github.event_name == 'schedule'",
        "github.event_name == 'workflow_dispatch'",
        "(github.event_name == 'issue_comment' && github.event.issue.pull_request && "
        f"github.event.comment.user.type == 'Bot' && github.event.comment.user.login == '{BOT}')",
        "(github.event_name == 'pull_request_review' && "
        f"github.event.review.user.type == 'Bot' && github.event.review.user.login == '{BOT}')",
    ]
    # The condition guards the only job, so no step runs for another author's event.
    assert len(re.findall(r"^  \w[\w-]*:\n    ", _block("jobs"), re.MULTILINE)) == 1


def test_one_run_at_a_time_for_the_repository_without_cancelling():
    body = _block("concurrency")
    group = re.search(r"^  group:\s*(.+)$", body, re.MULTILINE)

    assert group and group.group(1) == "codex-review-bookkeeping-${{ github.repository }}"
    # The group must not vary by event, PR, ref or run, or two runs could act on one PR.
    for varying in ("event", "run_id", "ref", "sha", "number"):
        assert varying not in group.group(1)
    assert re.search(r"^  cancel-in-progress:\s*false\s*$", body, re.MULTILINE)


def test_checks_out_the_default_branch_never_the_pr():
    code = _code()
    checkouts = re.findall(r"uses: actions/checkout@\S+\n((?:        .+\n)*)", code)

    assert len(checkouts) == 1
    assert [line.strip() for line in checkouts[0].splitlines()] == [
        "with:",
        "ref: ${{ github.event.repository.default_branch }}",
    ]
    for pr_ref in ("head.sha", "head.ref", "head_ref", "refs/pull", "pull_request_target"):
        assert pr_ref not in code, pr_ref


def _step(name: str) -> str:
    match = re.search(rf"^      - name: {re.escape(name)}\n((?:        .*\n|\n)+)", _code() + "\n", re.MULTILINE)
    assert match, name
    return match.group(1)


def test_webhook_secret_is_used_by_the_notify_step_alone():
    notify = _step("Notify the PO of the failure")
    bookkeeping = _step("Run Codex review bookkeeping")

    assert "SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}" in notify
    assert "secrets." not in bookkeeping
    assert "SLACK_WEBHOOK_URL" not in bookkeeping
    assert "GH_TOKEN: ${{ secrets.PROJECT_TOKEN }}" in _code().split("steps:")[0]
    assert "if: failure() && github.event_name != 'workflow_dispatch'" in notify
    assert "pull_request" not in notify and "checkout" not in notify


def test_exit_code_3_is_a_warning_and_a_green_run():
    script = _step("Run Codex review bookkeeping")

    assert 'if [ "$status" -eq 3 ]; then' in script
    assert "::warning::" in script.split('-eq 3 ]; then')[1].split("fi")[0]
    assert 'exit "$status"' in script


def test_errors_file_is_passed_only_when_the_default_branch_hpw_has_it():
    script = _step("Run Codex review bookkeeping")

    probe = script.index("--help")
    assert "grep -q -e '--errors-file'" in script
    assert script.index("set -- --errors-file") > probe
    assert script.index("set --\n") > probe
    # Never hard-wired onto the command lines, which main's hpw may not accept.
    assert not re.search(r"hpw codex-review-bookkeeping [^\n]*--repo[^\n]*--errors-file", script)


def test_project_token_is_the_only_secret_and_no_model_is_referenced():
    text = _workflow()

    assert set(re.findall(r"secrets\.(\w+)", text)) == {"PROJECT_TOKEN", "SLACK_WEBHOOK_URL"}
    assert "GH_TOKEN: ${{ secrets.PROJECT_TOKEN }}" in text
    assert "secrets: inherit" not in text
    # Checked on the whole file, comments included.
    for forbidden in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY_CODEX", "CLAUDE_CODE_OAUTH_TOKEN", "claude", "codex exec"):
        assert forbidden.lower() not in text.lower(), forbidden
    assert "npm install" not in text and "pip install" not in text
    assert CODEX_REVIEW_TRIGGER_PHRASE not in text.lower()


def test_event_payload_reaches_the_script_only_through_the_environment():
    code = _code()
    script = _step("Run Codex review bookkeeping").split("run: |\n")[1]

    assert "${{" not in script
    assert len(re.findall(r"^\s+run:", code, re.MULTILINE)) == 2  # the command step and the notify step
    assert "PR_NUMBER: ${{ github.event.issue.number || github.event.pull_request.number || inputs.pr_number }}" in code
    assert 'sh scripts/hpw codex-review-bookkeeping "$PR_NUMBER" --repo "$REPOSITORY" "$@"' in script
    assert 'sh scripts/hpw codex-review-bookkeeping --all --repo "$REPOSITORY" "$@"' in script
    # A failing command must fail the run: no `|| true`, no `continue-on-error`.
    assert "|| true" not in code and "continue-on-error" not in code


def test_skips_with_a_notice_when_the_default_branch_lacks_the_command():
    """On the PR that introduces the command, the default branch cannot run it yet."""
    code = _code()
    lines = [line.strip() for line in _step("Run Codex review bookkeeping").split("run: |\n")[1].splitlines()]
    module = "src/headless_pr_workflow/codex_review_bookkeeping.py"

    assert (REPO_ROOT / module).is_file()
    assert lines[:4] == [
        f"if [ ! -f {module} ]; then",
        'echo "::notice::The default branch has no codex-review-bookkeeping command yet; nothing to do."',
        "exit 0",
        "fi",
    ]
    # Past the guard, only exit code 3 (a warning) ends the step green; any other failure fails the run.
    assert [line for line in lines if line.startswith("exit")] == ["exit 0", "exit 0", 'exit "$status"']


def test_bounds_come_from_repository_variables_and_no_state_is_kept_on_the_runner():
    code = _code()

    assert f"{RETRY_HOURS_ENV}: ${{{{ vars.{RETRY_HOURS_ENV} }}}}" in code
    assert f"{MAX_REQUESTS_ENV}: ${{{{ vars.{MAX_REQUESTS_ENV} }}}}" in code
    assert "actions/cache" not in code and "upload-artifact" not in code


def test_merge_policy_states_the_thread_rule_and_keeps_the_blocking_sentence():
    policy = (REPO_ROOT / "docs" / "MERGE-POLICY.md").read_text(encoding="utf-8")

    assert "Unresolved Codex finding threads keep blocking through the review-thread gate" in policy
    assert "resolves a Codex-opened finding thread from an older head" in policy
    assert "not a waiver" in policy
    for never in ("other than the Codex bot", "review of the current head", "cannot read"):
        assert never in policy


def test_workflow_doc_and_known_limits_describe_the_same_rule():
    loop = (REPO_ROOT / "docs" / "HEADLESS-PR-WORKFLOW.md").read_text(encoding="utf-8")
    config = (REPO_ROOT / "docs" / "DISPATCHER-CONFIG.md").read_text(encoding="utf-8")

    assert "resolve the threads, post the Session Summary, move the story" in loop
    assert "`Type: external` blocker on the story naming each remaining thread" in loop
    assert "can move a story to `In merge` while Codex review threads" not in config
    assert "`resolveReviewThread`" in config
