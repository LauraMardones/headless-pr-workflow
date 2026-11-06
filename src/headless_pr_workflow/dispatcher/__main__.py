"""``python -m headless_pr_workflow.dispatcher`` — what ``scripts/hpw dispatcher`` runs."""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
