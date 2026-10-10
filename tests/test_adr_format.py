"""Deterministic header checks for every docs/decisions/ADR-*.md file (issue #311)."""

import re
from pathlib import Path

import pytest

DECISIONS_DIR = Path(__file__).resolve().parent.parent / "docs" / "decisions"
ADR_FILES = sorted(DECISIONS_DIR.glob("ADR-*.md"))

HEADING_RE = re.compile(r"^# ADR-(\d+): \S")
NAME_RE = re.compile(r"^ADR-(\d+)")


def check_heading(name: str, text: str) -> str | None:
    lines = text.splitlines()
    match = HEADING_RE.match(lines[0]) if lines else None
    if not match:
        return "first line is not '# ADR-NNN: <title>'"
    file_number = NAME_RE.match(name).group(1)
    if int(match.group(1)) != int(file_number):
        return f"heading number {match.group(1)} != file name number {file_number}"
    return None


def check_status(text: str) -> str | None:
    lines = [l for l in text.splitlines() if l.startswith("**Status:**")]
    if len(lines) != 1:
        return f"expected exactly one **Status:** line, found {len(lines)}"
    if not lines[0][len("**Status:**"):].strip():
        return "**Status:** line has no text"
    return None


def check_date(text: str) -> str | None:
    if not any(l.startswith("**Date:**") for l in text.splitlines()):
        return "no **Date:** line"
    return None


GOOD = "# ADR-042: Title\n\n**Status:** Accepted\n**Date:** 2026-01-01\n"


def test_adr_files_found():
    assert ADR_FILES, f"no ADR files found in {DECISIONS_DIR}"


@pytest.mark.parametrize("path", ADR_FILES, ids=lambda p: p.name)
def test_adr_header_is_well_formed(path):
    text = path.read_text(encoding="utf-8")
    assert check_heading(path.name, text) is None, check_heading(path.name, text)
    assert check_status(text) is None, check_status(text)
    assert check_date(text) is None, check_date(text)


def test_sample_good_passes():
    assert check_heading("ADR-042-x.md", GOOD) is None
    assert check_status(GOOD) is None
    assert check_date(GOOD) is None


@pytest.mark.parametrize(
    "text",
    [
        "ADR-042: Title\n",
        "# ADR-042 Title\n",
        "# ADR-043: Title\n",
        "",
        "\n# ADR-042: Title\n",
    ],
)
def test_heading_check_fails_on_malformed(text, tmp_path):
    sample = tmp_path / "ADR-042-sample.md"
    sample.write_text(text, encoding="utf-8")
    assert check_heading(sample.name, sample.read_text()) is not None


@pytest.mark.parametrize(
    "text",
    [
        "# ADR-042: T\n**Date:** 2026-01-01\n",
        "# ADR-042: T\n**Status:**   \n",
        "# ADR-042: T\n**Status:**\n",
        "# ADR-042: T\n**Status:** A\n**Status:** B\n",
    ],
)
def test_status_check_fails_on_malformed(text, tmp_path):
    sample = tmp_path / "ADR-042-sample.md"
    sample.write_text(text, encoding="utf-8")
    assert check_status(sample.read_text()) is not None


def test_date_check_fails_when_missing(tmp_path):
    sample = tmp_path / "ADR-042-sample.md"
    sample.write_text("# ADR-042: T\n**Status:** Accepted\n", encoding="utf-8")
    assert check_date(sample.read_text()) is not None
