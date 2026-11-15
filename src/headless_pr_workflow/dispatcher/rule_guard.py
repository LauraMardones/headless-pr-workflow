"""Guard against a second implementation of a registered dispatcher rule (issue #570).

For each rule in ``rules.RULES`` the guard looks for a *definition* of one of the
rule's markers (``def marker``, shell ``marker() {``, or an assignment
``marker=`` / ``marker =``) in the code under ``scripts/`` and ``src/``. A
definition in a file the registry does not list for that rule is a new copy. A
registered location whose file no longer contains its marker is stale: the copy
was removed, so the registry must drop it and the allowlist only shrinks.

Limits (see docs/DISPATCHER-DECISIONS.md): the match is on names, so a renamed
copy, an inline copy with no named definition and a call to a marker are not
found; a marker that is a generic word can match an unrelated definition, so
register distinctive markers only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from headless_pr_workflow.dispatcher.rules import RULES, Rule

SCAN_DIRS = ("scripts", "src")
REGISTRY = "src/headless_pr_workflow/dispatcher/rules.py"


def definition_pattern(marker: str) -> re.Pattern[str]:
    name = re.escape(marker)
    return re.compile(
        rf"\bdef\s+{name}\b"
        rf"|^[ \t]*(?:function\s+)?{name}[ \t]*\(\)\s*\{{"
        rf"|^[ \t]*function\s+{name}\b"
        rf"|^\s*(?:export\s+|readonly\s+|declare\s+-\w+\s+)?{name}\+?="
        rf"|^{name}\s*(?::[^=\n]+)?=(?!=)",
        re.MULTILINE,
    )


def code_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for directory in SCAN_DIRS:
        for path in sorted((root / directory).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                files.append(path)
    return files


def find_violations(root: Path, rules: Iterable[Rule] = RULES) -> list[str]:
    """Return one message per new copy and per stale allowlist entry."""
    files = {path.relative_to(root).as_posix(): path.read_text(encoding="utf-8", errors="replace")
             for path in code_files(root)}
    problems: list[str] = []
    for rule in rules:
        registered = {impl.path for impl in rule.implementations}
        for marker in sorted({impl.marker for impl in rule.implementations}):
            pattern = definition_pattern(marker)
            for rel, text in files.items():
                if rel in registered or rel == REGISTRY:
                    continue
                if pattern.search(text):
                    problems.append(f"{rule.name}: new copy: {rel} defines {marker!r} (owner {rule.owner})")
        for impl in rule.duplicates:
            path = root / impl.path
            if not path.is_file() or impl.marker not in path.read_text(encoding="utf-8", errors="replace"):
                problems.append(f"{rule.name}: stale allowlist entry: {impl.path} no longer contains {impl.marker!r}")
    return problems
