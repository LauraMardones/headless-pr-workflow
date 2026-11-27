"""The no-feature note is described where the skip reason is (issue #616, AC 7)."""

from __future__ import annotations

from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"


def test_skip_reason_table_names_the_note_and_the_marker():
    row = next(line for line in (DOCS / "commands/dispatcher-scheduler.md").read_text().splitlines()
               if line.startswith("| `no-feature` |"))
    assert "note" in row and "action=no-feature-note" in row and "once per issue" in row


def test_config_doc_describes_the_note():
    text = (DOCS / "DISPATCHER-CONFIG.md").read_text()
    section = text[text.index("**The `no-feature` note"):]
    section = section[:section.index("**Retired.**")]
    for needle in ("action=no-feature-note", "Once per issue", "DISPATCHER_ENABLED", "No Slack event",
                   "Feature group: #<n>", "later run tries again"):
        assert needle in section, needle
