from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def step_15_text() -> str:
    command = read(".claude/commands/implement.md")
    start = command.index("15. Before requesting review")
    end = command.index("16. When implementation is complete")
    return command[start:end]


def test_premortem_lives_in_self_review_before_ready_for_review() -> None:
    command = read(".claude/commands/implement.md")
    premortem = command.index("failure-path pre-mortem")

    assert command.index("15. Before requesting review") < premortem
    assert premortem < command.index("16. When implementation is complete")
    assert premortem < command.index("mark the PR Ready for review")


def test_premortem_names_every_trigger_category() -> None:
    step = step_15_text()

    assert "an external call (GitHub REST/GraphQL, `gh`, `git push`/`fetch`, any network call)" in step
    assert "a GitHub mutation (comment, label, status, review, thread resolution, board write, merge, branch)" in step
    assert "a persisted-state write (files used as state, Actions cache, artifacts, comments or markers read back later as state)" in step
    assert "a GitHub Actions workflow step or condition that runs one of these (`if:`, `always()`, step order, concurrency)" in step


def test_premortem_checks_every_failure_case() -> None:
    step = step_15_text()

    assert "(1) the operation fails;" in step
    assert "(2) it partly succeeds;" in step
    assert "(3) it is retried after an earlier attempt succeeded or partly succeeded;" in step
    assert "(4) the job or workflow is re-run;" in step
    assert "(5) the job or workflow is cancelled mid-way;" in step
    assert "(6) the state it reads is missing, stale or unreadable;" in step
    assert "(7) the PR head SHA changes between read and mutation" in step


def test_premortem_requires_exactly_one_outcome_per_case() -> None:
    step = step_15_text()

    assert "give each operation and case exactly one outcome" in step
    assert "**handled** (the code handles it, with a deterministic test where a script-level test is possible)" in step
    assert "**not applicable** (one clause of reason, for example \"read-only call\")" in step
    assert "**residual risk** (not handled or not testable, for example workflow-level behaviour pytest cannot reach)" in step
    assert "a case with no outcome means the pre-mortem is incomplete" in step
    assert "keep the per-case list in this session, not in the PR or any comment" in step


def test_premortem_records_summary_deviation_lines() -> None:
    step = step_15_text()

    assert "`--deviation` flags" in step
    assert "`pre-mortem: <n> operations checked; residual risks: <count|none>`" in step
    assert "`pre-mortem: not applicable — the diff adds no external call, GitHub mutation, persisted-state write or workflow step`" in step
    assert "`residual risk: <operation> — <case> — <why it is accepted>`" in step


def test_handoff_requires_premortem_and_residual_risk_lines() -> None:
    command = read(".claude/commands/implement.md")
    handoff_start = command.index("16. When implementation is complete")
    handoff = command[handoff_start:]

    assert "the step 15 `pre-mortem:` line is always required" in handoff
    assert "each residual risk gets its own `residual risk:` line" in handoff
