"""The scopes of `PROJECT_TOKEN` are listed in one place and everything else points to it (issue #329)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from headless_pr_workflow.codex_review_bookkeeping import TOKEN_SCOPES_DOC


REPO_ROOT = Path(__file__).resolve().parents[1]
TOKEN_DOC = "docs/DISPATCHER-CONFIG.md"
TOKEN_HEADING = "## GitHub Token — `PROJECT_TOKEN`"
TOKEN_ANCHOR = "DISPATCHER-CONFIG.md#github-token--project_token"
REQUIRED_SCOPES = {"repo", "project", "workflow", "read:org"}
POINTING_FILES = [
    "README.md",
    "docs/HEADLESS-PR-WORKFLOW.md",
    ".github/workflows/codex-review-bookkeeping.yml",
    "scripts/dispatcher-invoke.sh",
]
SCOPE_NAME = re.compile(r"`((?:read:|write:|admin:)?(?:repo|project|workflow|org|discussion))`")


def read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def token_section() -> str:
    text = read(TOKEN_DOC)
    start = text.index(TOKEN_HEADING + "\n")
    end = text.find("\n## ", start + len(TOKEN_HEADING))
    return text[start : end if end != -1 else len(text)]


def scope_rows(text: str) -> dict[str, str]:
    """Rows of a `| Scope | Reason |` table: scope name -> reason."""

    rows = re.findall(r"^\| `([^`|]+)` \| ([^|\n]+) \|$", text, flags=re.MULTILINE)
    return {scope: reason.strip() for scope, reason in rows}


def test_token_section_lists_the_four_scopes_each_with_a_reason():
    rows = scope_rows(token_section())

    assert set(rows) == REQUIRED_SCOPES
    assert all(rows.values())
    assert "PR read" in rows["read:org"]


def test_dispatcher_config_has_exactly_one_scope_list():
    text = read(TOKEN_DOC)

    assert text.count(TOKEN_HEADING + "\n") == 1
    assert "## GitHub Authentication Token" not in text
    assert len(re.findall(r"^\| Scope \|", text, flags=re.MULTILINE)) == 1
    assert not re.search(r"^\|.*scopes? \|$", text, flags=re.MULTILINE | re.IGNORECASE)


@pytest.mark.parametrize("path", [TOKEN_DOC, *POINTING_FILES])
def test_read_project_is_not_given_as_a_scope_of_the_token(path):
    assert "read:project" not in read(path)


@pytest.mark.parametrize("path", POINTING_FILES)
def test_other_places_point_to_the_token_section_instead_of_listing_scopes(path):
    text = read(path)
    mentions = [line for line in text.splitlines() if "PROJECT_TOKEN" in line and "secrets.PROJECT_TOKEN" not in line]

    assert "DISPATCHER-CONFIG.md" in text and "GitHub Token — `PROJECT_TOKEN`" in text
    assert mentions, f"{path} does not mention PROJECT_TOKEN"
    for line in mentions:
        assert SCOPE_NAME.findall(line) == [], f"{path} names a scope next to PROJECT_TOKEN: {line.strip()}"


@pytest.mark.parametrize("path", ["README.md", "docs/HEADLESS-PR-WORKFLOW.md"])
def test_markdown_pointers_link_to_the_section_anchor(path):
    assert TOKEN_ANCHOR in read(path)


def test_readme_names_the_bookkeeping_workflow_as_a_user_of_the_secret():
    (row,) = [line for line in read("README.md").splitlines() if line.startswith("| `PROJECT_TOKEN` |")]

    assert ".github/workflows/codex-review-bookkeeping.yml" in row


def test_the_error_message_names_a_section_that_exists():
    path, _, heading = TOKEN_SCOPES_DOC.partition(" → ")

    assert path == TOKEN_DOC
    assert heading.strip('"') == TOKEN_HEADING.removeprefix("## ")
