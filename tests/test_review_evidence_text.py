"""Separate-session review summary text: helper, recognizer and docs stay in step (#324)."""

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

from headless_pr_workflow import cli
from headless_pr_workflow.approval_check import summarize_approval_check
from headless_pr_workflow.catalog import find_command
from headless_pr_workflow.review_policy import (
    APPROVAL_TO_RELY_ON_PHRASE,
    NO_BLOCKERS_PHRASE,
    REVIEWER_PROVIDER_LABEL,
    SEPARATE_SESSION_REVIEW_MARKER,
    is_accepted_separate_session_review,
    separate_session_review_text,
)

from tests.github_scenarios import build_pr_context, build_review, linked_story


ROOT = Path(__file__).resolve().parents[1]
SESSION_SUMMARY = ROOT / "scripts" / "session-summary.sh"
HEAD_SHA = "95f2943d098d3b8d393962b750edfb854ad311d9"
EXPECTED_TEXT = (
    f"Reviewed head SHA `{HEAD_SHA}`.\n"
    "\n"
    f"No blockers remain for {HEAD_SHA}.\n"
    "\n"
    "solo-maintainer override accepted.\n"
    "\n"
    "Reviewer provider: codex\n"
    "\n"
    "This solo-maintainer override is the approval to rely on for the current head SHA.\n"
)
# Review body recorded on PR #299 at 2026-10-11T11:22:54Z. It carried the
# evidence only in the `Deviation:` line, in the session's own words.
PR_299_REJECTED_BODY = (
    "## Session Summary\n"
    "Command: review\n"
    "Issue/PR: #299\n"
    f"Head: {HEAD_SHA}\n"
    "Checks: GitHub status checks: none reported\n"
    "Blockers: none\n"
    "Next: merge\n"
    f"Deviation: solo-maintainer override accepted; no blockers remain for {HEAD_SHA}; "
    "formal GitHub approval is unavailable because GitHub rejected self-approval and no independent "
    "approver is available for this pull request; this solo-maintainer override is the approval to "
    "rely on for the current head SHA\n"
)
SESSION_SUMMARY_BLOCK = (
    "## Session Summary\n"
    "Command: review\n"
    "Issue/PR: #299\n"
    f"Head: {HEAD_SHA}\n"
    "Checks: pytest=pass\n"
    "Blockers: none\n"
    "Next: merge\n"
    "Deviation: solo-maintainer override recorded above; Reviewer provider: codex\n"
)


def _review(body: str, *, state: str = "COMMENTED", commit_oid: str = HEAD_SHA):
    return build_review(state=state, commit_oid=commit_oid, body=body)


def _approval(body: str):
    """Evaluate the body as the only review of a Claude-implemented PR at HEAD_SHA."""

    context = build_pr_context(
        head_ref_oid=HEAD_SHA,
        latest_reviews=(_review(body),),
        linked_issues=(linked_story("executor:claude-code-sonnet"),),
    )
    return summarize_approval_check(context)


def _template(path: str) -> str:
    """Return the one fenced block in the file that holds the review summary template."""

    text = (ROOT / path).read_text(encoding="utf-8")
    blocks = [
        textwrap.dedent(block)
        for block in re.findall(r"^[ \t]*```text\n(.*?)^[ \t]*```[ \t]*$", text, re.MULTILINE | re.DOTALL)
        if "Reviewed head SHA" in block
    ]
    assert len(blocks) == 1, path
    return blocks[0]


def test_helper_prints_the_documented_text():
    assert separate_session_review_text(HEAD_SHA, "codex") == EXPECTED_TEXT


def test_helper_text_uses_the_recognizer_phrase_constants():
    text = separate_session_review_text(HEAD_SHA, "claude")

    assert SEPARATE_SESSION_REVIEW_MARKER in text
    assert NO_BLOCKERS_PHRASE.format(head_sha=HEAD_SHA) in text
    assert APPROVAL_TO_RELY_ON_PHRASE in text
    assert f"{REVIEWER_PROVIDER_LABEL} claude\n" in text


def test_helper_lowercases_the_head_sha():
    assert separate_session_review_text(HEAD_SHA.upper(), "codex") == EXPECTED_TEXT


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_helper_text_is_accepted_by_the_recognizer(provider):
    review = _review(separate_session_review_text(HEAD_SHA, provider))

    assert is_accepted_separate_session_review(review, head_sha=HEAD_SHA)


def test_helper_text_passes_the_approval_gate():
    approval = _approval(separate_session_review_text(HEAD_SHA, "codex"))

    assert approval.separate_session_review.status == "accepted"
    assert approval.hard_gate_passed


def test_helper_text_followed_by_a_session_summary_is_accepted():
    approval = _approval(separate_session_review_text(HEAD_SHA, "codex") + "\n" + SESSION_SUMMARY_BLOCK)

    assert approval.separate_session_review.status == "accepted"
    assert approval.separate_session_review.reviewer_provider == "codex"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not available")
def test_helper_text_followed_by_generated_session_summary_is_accepted():
    # Fed on stdin with LF endings so a CRLF checkout on Windows runs it too.
    script = SESSION_SUMMARY.read_bytes().replace(b"\r\n", b"\n")
    result = subprocess.run(
        [
            "bash",
            "-s",
            "--",
            "--command", "review",
            "--pr", "299",
            "--head", HEAD_SHA,
            "--checks", "pytest=pass",
            "--blockers", "none",
            "--next", "merge",
            "--deviation", "solo-maintainer override recorded above; Reviewer provider: codex",
        ],
        input=script,
        capture_output=True,
        check=False,
    )
    summary = result.stdout.decode("utf-8").replace("\r\n", "\n")
    assert result.returncode == 0, result.stderr
    assert summary.startswith("## Session Summary\n")
    assert "Deviation: solo-maintainer override recorded above" in summary

    approval = _approval(separate_session_review_text(HEAD_SHA, "codex") + "\n" + summary)

    assert approval.separate_session_review.status == "accepted"


@pytest.mark.parametrize(
    "head_sha",
    [
        pytest.param(None, id="missing"),
        pytest.param("", id="empty"),
        pytest.param(HEAD_SHA[:7], id="abbreviated"),
        pytest.param(HEAD_SHA[:39], id="39-characters"),
        pytest.param(HEAD_SHA + "0", id="41-characters"),
        pytest.param("g" * 40, id="not-hexadecimal"),
        pytest.param("<head-sha>", id="placeholder"),
    ],
)
def test_helper_rejects_anything_but_a_full_hex_sha(head_sha):
    with pytest.raises(ValueError, match="full 40-character hexadecimal"):
        separate_session_review_text(head_sha, "codex")


@pytest.mark.parametrize("provider", [None, "", "gemini", "Codex", "claude or codex"])
def test_helper_rejects_an_invalid_reviewer_provider(provider):
    with pytest.raises(ValueError, match="reviewer provider must be one of: claude, codex"):
        separate_session_review_text(HEAD_SHA, provider)


@pytest.mark.parametrize(
    ("required", "altered"),
    [
        pytest.param("solo-maintainer override accepted.", "solo-maintainer override applies.", id="marker"),
        pytest.param(f"No blockers remain for {HEAD_SHA}.", "No blockers remain.", id="no-blockers-without-sha"),
        pytest.param(
            f"No blockers remain for {HEAD_SHA}.",
            f"No blockers remain for {HEAD_SHA[:7]}.",
            id="no-blockers-abbreviated-sha",
        ),
        pytest.param(
            "is the approval to rely on for the current head SHA.",
            "is the approval for the current head SHA.",
            id="approval-to-rely-on",
        ),
        pytest.param("Reviewer provider: codex\n", "", id="reviewer-provider-line"),
        pytest.param("Reviewer provider: codex\n", "Reviewed by codex.\n", id="reviewer-provider-paraphrased"),
    ],
)
def test_text_is_rejected_when_one_required_element_is_altered(required, altered):
    assert required in EXPECTED_TEXT
    body = EXPECTED_TEXT.replace(required, altered)

    assert not is_accepted_separate_session_review(_review(body), head_sha=HEAD_SHA)
    approval = _approval(body)
    assert approval.separate_session_review.status == "invalid"
    assert not approval.hard_gate_passed


def test_pr_299_wording_is_still_rejected():
    assert not is_accepted_separate_session_review(_review(PR_299_REJECTED_BODY), head_sha=HEAD_SHA)

    approval = _approval(PR_299_REJECTED_BODY)

    assert approval.separate_session_review.status == "invalid"
    assert not approval.hard_gate_passed


@pytest.mark.parametrize("path", ["docs/MERGE-POLICY.md", ".claude/commands/review.md"])
@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_documented_template_matches_the_helper(path, provider):
    template = _template(path)

    assert template.count("<head-sha>") == 2
    assert template.count("<claude|codex>") == 1
    filled = template.replace("<head-sha>", HEAD_SHA).replace("<claude|codex>", provider)

    assert filled == separate_session_review_text(HEAD_SHA, provider)


def test_merge_policy_lists_every_required_element_as_required_for_automation():
    policy = (ROOT / "docs" / "MERGE-POLICY.md").read_text(encoding="utf-8")
    path_2 = policy.split("### Path 2: Separate-Session Review", maxsplit=1)[1].split("\n### ", maxsplit=1)[0]

    assert "Automation requires all three of these phrases" in path_2
    assert f"  - `{SEPARATE_SESSION_REVIEW_MARKER}`" in path_2
    assert f"  - `{NO_BLOCKERS_PHRASE.format(head_sha='<head-sha>').lower()}`" in path_2
    assert f"  - `{APPROVAL_TO_RELY_ON_PHRASE}`" in path_2
    assert f"`{REVIEWER_PROVIDER_LABEL} claude` or `{REVIEWER_PROVIDER_LABEL} codex`. Automation requires this line too." in path_2
    assert "Use it verbatim" in path_2
    assert "hpw review-evidence-text <head-sha> --reviewer-provider <claude|codex>" in path_2


def test_cli_prints_only_the_text(capsys):
    exit_code = cli.main(["review-evidence-text", HEAD_SHA, "--reviewer-provider", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == EXPECTED_TEXT
    assert captured.err == ""


def test_cli_json_output(capsys):
    exit_code = cli.main(["review-evidence-text", HEAD_SHA, "--reviewer-provider", "codex", "--json"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out) == {
        "head_sha": HEAD_SHA,
        "reviewer_provider": "codex",
        "text": EXPECTED_TEXT,
    }


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["--reviewer-provider", "codex"], id="missing-sha"),
        pytest.param([HEAD_SHA[:7], "--reviewer-provider", "codex"], id="abbreviated-sha"),
        pytest.param(["z" * 40, "--reviewer-provider", "codex"], id="non-hex-sha"),
        pytest.param([HEAD_SHA], id="missing-provider"),
        pytest.param([HEAD_SHA[:7], "--reviewer-provider", "codex", "--json"], id="abbreviated-sha-json"),
    ],
)
def test_cli_fails_with_empty_stdout_on_invalid_input(argv, capsys):
    exit_code = cli.main(["review-evidence-text", *argv])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert captured.err.startswith("review-evidence-text: ")


def test_cli_makes_no_github_call(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise AssertionError("review-evidence-text must not read GitHub")

    monkeypatch.setattr(cli, "fetch_pr_context", fail)

    assert cli.main(["review-evidence-text", HEAD_SHA, "--reviewer-provider", "claude"]) == 0
    assert "Reviewer provider: claude" in capsys.readouterr().out


def test_command_is_registered_in_the_catalog_and_docs():
    command = find_command("review-evidence-text")

    assert command is not None
    assert command.status == "implemented"
    assert "| `review-evidence-text` |" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "| `review-evidence-text` |" in (ROOT / "docs" / "PR-AUTOMATION-MAP.md").read_text(encoding="utf-8")
