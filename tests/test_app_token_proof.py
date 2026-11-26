"""The App token proof script and workflow (issue #664, F3.1).

The script runs under Git Bash with a fake ``gh``; the workflow is parsed textually
(PyYAML is not a project dependency).
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from headless_pr_workflow.dispatcher.gateway import BOARD_ITEMS_QUERY
from tests.script_tools import BASH, requires, script_path, windows_env

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "app-token-proof.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "app-token-proof.yml"
REPO = "octo/demo"
TOKEN = "ghs_SECRETTOKENVALUE123"
CALLS = ["board-read", "board-write", "branch-protection", "review-requests"]

pytestmark = requires("bash", "jq")

# A fake gh. FAKE_REFUSE is a space-separated list of calls (or "all") that answer HTTP 403.
FAKE_GH = r"""#!/usr/bin/env bash
all="${*//$'\n'/ }"
printf '%s\n' "$all" >> "$FAKE_LOG"
if [[ "$1" == api && "$2" == graphql ]]; then
    if [[ "$all" == *"updateProjectV2("* ]]; then call=board-write
    elif [[ "$all" == *shortDescription* ]]; then call=board-write
    else call=board-read; fi
elif [[ "$1" == api ]]; then call=branch-protection
elif [[ "$1 $2" == "pr list" ]]; then call=pr-list
else call=review-requests; fi
for refused in $FAKE_REFUSE; do
    if [[ "$refused" == all || "$refused" == "$call" ]]; then
        echo "gh: ${FAKE_ERROR:-Resource not accessible by integration} (HTTP 403)" >&2
        exit 1
    fi
done
case "$all" in
    *"updateProjectV2("*) echo '{"data":{"updateProjectV2":{"projectV2":{"id":"P1"}}}}' ;;
    *shortDescription*) echo "{\"data\":{\"repository\":{\"projectsV2\":{\"nodes\":[{\"id\":\"P1\",\"shortDescription\":${FAKE_DESCRIPTION:-\"desc\"}}]}}}}" ;;
    "api graphql"*) echo '{"data":{"repository":{"projectsV2":{"nodes":[{"id":"P1"}]}}}}' ;;
    "api repos/"*) echo '{"url":"x"}' ;;
    "pr list"*) echo "${FAKE_PR-7}" ;;
    "pr view"*) echo '{"reviewRequests":[]}' ;;
esac
"""


def run(tmp_path: Path, *args: str, refuse: str = "", env: dict[str, str] | None = None):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    gh = fake_bin / "gh"
    gh.write_text(FAKE_GH, encoding="utf-8", newline="\n")
    gh.chmod(0o755)
    log = tmp_path / "gh.log"
    log.write_text("", encoding="utf-8")
    full_env = {
        "PATH": script_path(str(fake_bin)),
        "HOME": str(tmp_path),
        "GH_TOKEN": TOKEN,
        "FAKE_LOG": str(log),
        "FAKE_REFUSE": refuse,
        **windows_env(),
        **(env or {}),
    }
    proc = subprocess.run([BASH, str(SCRIPT), *args], env=full_env, capture_output=True, text=True, timeout=60)
    return proc, log.read_text(encoding="utf-8").splitlines()


def lines(proc) -> list[str]:
    return proc.stdout.splitlines()


def test_all_answered_prints_four_ok_lines(tmp_path):
    proc, _ = run(tmp_path, "--repo", REPO)

    assert proc.returncode == 0
    assert lines(proc) == [f"[APP PROOF] {call}: ok" for call in CALLS]


def test_all_refused_prints_four_refused_lines(tmp_path):
    proc, _ = run(tmp_path, "--repo", REPO, refuse="all")

    assert proc.returncode == 0
    assert lines(proc) == [
        f"[APP PROOF] {call}: refused 403 Resource not accessible by integration" for call in CALLS
    ]


def test_one_refused_leaves_the_other_lines_ok(tmp_path):
    proc, _ = run(tmp_path, "--repo", REPO, refuse="branch-protection")

    assert proc.returncode == 0
    assert lines(proc) == [
        "[APP PROOF] board-read: ok",
        "[APP PROOF] board-write: ok",
        "[APP PROOF] branch-protection: refused 403 Resource not accessible by integration",
        "[APP PROOF] review-requests: ok",
    ]


def test_board_write_writes_back_the_value_it_read(tmp_path):
    _, log = run(tmp_path, "--repo", REPO, env={"FAKE_DESCRIPTION": '"hello world"'})

    mutations = [line for line in log if "updateProjectV2(" in line]
    assert len(mutations) == 1
    assert "projectId=P1" in mutations[0]
    assert "shortDescription=hello world" in mutations[0]


def test_board_write_writes_a_null_description_as_the_empty_string(tmp_path):
    _, log = run(tmp_path, "--repo", REPO, env={"FAKE_DESCRIPTION": "null"})

    mutations = [line for line in log if "updateProjectV2(" in line]
    assert len(mutations) == 1
    assert mutations[0].rstrip().endswith("shortDescription=")


def test_no_call_is_a_status_write(tmp_path):
    _, log = run(tmp_path, "--repo", REPO)

    for forbidden in ("updateProjectV2ItemFieldValue", "singleSelectOptionId", "gh project item-edit"):
        assert not any(forbidden in line for line in log)
        assert forbidden not in SCRIPT.read_text(encoding="utf-8")
        assert forbidden not in WORKFLOW.read_text(encoding="utf-8")


def test_board_read_uses_the_dispatcher_query():
    assert BOARD_ITEMS_QUERY in SCRIPT.read_text(encoding="utf-8")


def test_no_open_pr_skips_review_requests(tmp_path):
    proc, log = run(tmp_path, "--repo", REPO, env={"FAKE_PR": ""})

    assert proc.returncode == 0
    out = lines(proc)
    assert out[:3] == [f"[APP PROOF] {call}: ok" for call in CALLS[:3]]
    assert re.fullmatch(r"\[APP PROOF\] review-requests: skipped \S.*", out[3])
    assert len(out) == 4
    assert not any(line.startswith("pr view") for line in log)


def test_pr_argument_is_used_without_listing(tmp_path):
    proc, log = run(tmp_path, "--repo", REPO, "--pr", "12")

    assert lines(proc)[3] == "[APP PROOF] review-requests: ok"
    assert any(line.startswith("pr view 12 ") for line in log)
    assert not any(line.startswith("pr list") for line in log)


def test_graphql_errors_under_http_200_are_a_refusal(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    gh = fake_bin / "gh"
    gh.write_text(
        '#!/usr/bin/env bash\n'
        'if [[ "$1" == api && "$2" == graphql ]]; then\n'
        '  echo \'{"errors":[{"message":"Resource not accessible by personal access token"}]}\'; exit 0\n'
        'fi\nexit 1\n',
        encoding="utf-8", newline="\n",
    )
    gh.chmod(0o755)
    env = {"PATH": script_path(str(fake_bin)), "HOME": str(tmp_path), "GH_TOKEN": TOKEN, **windows_env()}
    proc = subprocess.run([BASH, str(SCRIPT), "--repo", REPO, "--pr", "1"], env=env, capture_output=True,
                          text=True, timeout=60)

    assert proc.returncode == 0
    assert lines(proc)[0] == "[APP PROOF] board-read: refused 200 Resource not accessible by personal access token"
    assert lines(proc)[1] == "[APP PROOF] board-write: refused 200 Resource not accessible by personal access token"


def test_the_token_is_masked_and_messages_are_cut(tmp_path):
    error = f"bad credentials {TOKEN} " + "x" * 400
    proc, _ = run(tmp_path, "--repo", REPO, refuse="all", env={"FAKE_ERROR": error})

    assert proc.returncode == 0
    assert TOKEN not in proc.stdout
    assert TOKEN not in proc.stderr
    assert "***" in proc.stdout
    for line in lines(proc):
        assert len(line.split(": refused 403 ", 1)[1]) <= 200


@pytest.mark.parametrize("args", [[], ["--repo"], ["--repo", "nodash"], ["--repo", REPO, "--pr", "x"],
                                  ["--repo", REPO, "--bogus"]])
def test_invalid_arguments_exit_2_without_calls(tmp_path, args):
    proc, log = run(tmp_path, *args)

    assert proc.returncode == 2
    assert proc.stdout == ""
    assert log == []


# ── the workflow, as text ───────────────────────────────────────────────────


def _code() -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _block(key: str) -> str:
    match = re.search(rf"^{re.escape(key)}:\n((?:(?:  .*)?\n)+)", _code() + "\n", re.MULTILINE)
    assert match, f"top-level {key!r} block missing"
    return match.group(1)


def _steps() -> list[str]:
    body = _code().split("    steps:\n", 1)[1]
    return re.split(r"^      - ", body, flags=re.MULTILINE)[1:]


def test_workflow_triggers_and_permissions():
    on = _block("on")

    assert re.findall(r"^  (\w+):", on, re.MULTILINE) == ["workflow_dispatch", "push"]
    assert re.search(r"^    branches: \[main\]$", on, re.MULTILINE)
    assert re.search(r'^    paths:\n      - "\.github/workflows/app-token-proof\.yml"\n?$', on, re.MULTILINE)
    assert "pr_number" in on
    assert _block("permissions").strip() == "contents: read"
    for trigger in ("schedule", "pull_request", "issue_comment"):
        assert trigger not in on


def test_installation_token_is_created_in_its_own_step():
    steps = _steps()
    holders = [s for s in steps if "secrets." in s]

    assert len(holders) == 1
    assert "uses: actions/create-github-app-token@v" in holders[0]
    assert "secrets.APP_ID" in holders[0]
    assert "secrets.APP_PRIVATE_KEY" in holders[0]
    assert _code().count("secrets.APP_ID") == 1
    assert _code().count("secrets.APP_PRIVATE_KEY") == 1


def test_token_reaches_only_the_proof_step_as_gh_token():
    steps = _steps()
    users = [s for s in steps if "steps.app-token.outputs.token" in s]

    assert len(users) == 1
    assert re.search(r"^          GH_TOKEN: \$\{\{ steps\.app-token\.outputs\.token \}\}$", users[0], re.MULTILINE)
    assert "scripts/app-token-proof.sh --repo" in users[0]
    assert "GH_TOKEN" not in "".join(s for s in steps if s not in users)


def test_workflow_never_fails_on_a_refused_call_and_uses_no_project_token():
    code = _code()

    assert "PROJECT_TOKEN" not in WORKFLOW.read_text(encoding="utf-8")
    assert "PROJECT_TOKEN" not in SCRIPT.read_text(encoding="utf-8")
    for fragment in ("continue-on-error", "exit 1", "set -e"):
        assert fragment not in code
    assert "GITHUB_STEP_SUMMARY" in code
    assert not re.search(r"^\s*set -[a-z]*e", SCRIPT.read_text(encoding="utf-8"), re.MULTILINE)
