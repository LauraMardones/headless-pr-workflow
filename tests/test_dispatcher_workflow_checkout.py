"""Workflow contract for git authentication in dispatcher sessions (issue #337, AC3).

Reads .github/workflows/dispatcher.yml as text, the way
tests/test_dispatcher_workflow_allowance.py does. PyYAML is not a dev
dependency, so steps are split on their ``- name:`` lines.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "dispatcher.yml"

STEP_RE = re.compile(r"^      - name: (.+)$", re.MULTILINE)
GIT_AUTH_STEP = "Configure git to authenticate through gh"


def steps() -> list[tuple[str, str]]:
    text = WORKFLOW.read_text()
    matches = list(STEP_RE.finditer(text))
    out = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append((m.group(1).strip(), text[m.start():end]))
    return out


def step(name: str) -> str:
    for n, body in steps():
        if n == name:
            return body
    raise AssertionError(f"step {name!r} not found in {WORKFLOW}")


def step_if(body: str) -> str:
    m = re.search(r"^        if: (.+)$", body, re.MULTILINE)
    assert m, body
    return m.group(1).strip()


def run_text(body: str) -> str:
    return body.split("        run:", 1)[1] if "        run:" in body else ""


def test_checkout_keeps_no_credentials_and_full_history():
    body = step("Checkout")
    assert "        with:" in body, body
    assert re.search(r"^          persist-credentials: false$", body, re.MULTILINE), body
    assert re.search(r"^          fetch-depth: 0$", body, re.MULTILINE), body


def test_checkout_has_no_token_input():
    body = step("Checkout")
    assert not re.search(r"^          token:", body, re.MULTILINE), body
    assert "secrets." not in body, body


def test_git_auth_step_runs_gh_setup_git_with_project_token():
    body = step(GIT_AUTH_STEP)
    assert re.search(r"^        env:\n          GH_TOKEN: \$\{\{ secrets\.PROJECT_TOKEN \}\}$", body, re.MULTILINE), body
    assert re.search(r"^        run: gh auth setup-git\s*$", body, re.MULTILINE), body


def test_git_auth_step_has_the_invoke_condition():
    assert step_if(step(GIT_AUTH_STEP)) == step_if(step("Run dispatcher invoke"))


def test_git_auth_step_runs_after_checkout_and_before_invoke():
    names = [n for n, _ in steps()]
    assert names.index("Checkout") < names.index(GIT_AUTH_STEP) < names.index("Run dispatcher invoke")


def test_no_step_writes_or_prints_a_secret_in_its_run_script():
    # A secret reaches a step only through its env: block; a run script that
    # names a secret could echo it or write it into git config.
    for name, body in steps():
        assert "secrets." not in run_text(body), (name, run_text(body))
