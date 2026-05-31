# Token Economics Lens

## Perspective

Analyze the per-session token cost and documentation overhead of each story, and ensure documentation work is clustered by file rather than scattered across stories. Verify that executor model tier assignments match story complexity.

## Trigger Labels

(none — this lens is always loaded as a default)

## Lens Questions

**1. Per-session overhead analysis**

For each proposed story, estimate the per-session overhead cost:
- Tool definitions (GitHub MCP, Bash, file I/O, etc.) repeated in setup
- Long context setup (reading docs, understanding codebase patterns, reproducing issues)
- Tool invocations that don't ship (exploratory reads, failed attempts, debug output)

Are any stories heavy on overhead relative to deliverable code? If a story would spend 60%+ of token budget on setup and exploration rather than shipping code, consider:
- Splitting it differently to isolate the exploratory phase
- Merging it with a related story to amortize setup cost
- Adding prerequisites to reduce setup complexity in this story

**2. Documentation cluster merging check**

Identify all stories that write to the same file (especially `.md` files, `pyproject.toml`, config files, or source modules):
- Do these stories all modify the same documentation file?
- Would they benefit from being executed sequentially or in a single story?
- Are there coordination or rebase conflicts if they're split?

If multiple stories modify the same file, ask: "Can any of these stories be merged without losing clarity or parallelism?" Merging can eliminate coordination overhead and reduce per-session setup cost.

**3. Executor model tier alignment**

Review the `executor:` label assigned to each story against its complexity:

**Haiku tier** (documentation, config, boilerplate):
- Documentation-only changes (README, CONTRIBUTING, ADR, guides)
- Configuration files (pyproject.toml, .gitignore, settings.json entries)
- Boilerplate code (test fixtures, CI/CD workflow files, comment updates)
- Small bug fixes (<50 lines of logic)

**Sonnet tier** (logic, integration, feature work):
- Feature implementation (new user-facing commands, API changes)
- Logic changes and business rule updates
- Integration work (connecting multiple modules, wiring dependencies)
- Moderate refactoring (50-200 lines changed)
- Most story-sized work falls here

**Opus tier** (architecture, design review, complex decisions):
- Architecture design and major refactors (200+ lines, system-wide impact)
- High-stakes review (security, performance, maintainability trade-offs)
- Resolving complex dependency deadlocks
- Designing new abstractions or protocols

For each story, verify:
- Does the assigned tier match the work complexity?
- If labeled `executor:claude-code-haiku` but contains feature logic, should it be `executor:claude-code-sonnet`?
- If labeled `executor:claude-code-sonnet` but is purely documentation, should it be `executor:claude-code-haiku`?

Correct misalignment before delivery to avoid rejection due to tier overload or underutilization.
