#!/usr/bin/env bash
# scripts/codex-eligibility.sh
#
# The eligibility hook of the local Codex worker (issue #542, ADR-012 → Codex Plus
# amendment). Set CODEX_WORKER_ELIGIBILITY_COMMAND to this script's path; the worker
# calls it before it starts a session. Setup and variables:
# docs/DISPATCHER-CONFIG.md → "Local Codex worker" → "Eligibility command".
#
# Usage:
#   scripts/codex-eligibility.sh --repo <owner/repo> --issue <n> --command refine|implement
#
# Exit codes: 0 eligible; 1 not eligible, stdout {"reason": "<word>", "detail": "<text>"};
# 2 unreadable (invalid arguments or an unexpected error: nothing starts).
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(dirname "$SCRIPT_DIR")
PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONPATH

exec python -m headless_pr_workflow.dispatcher.codex_reserve "$@"
