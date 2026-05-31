# Refinement Lens Template

This file documents the standard format for all refinement lenses used in HPW refinement commands.

## Lens Format

Each lens is a markdown file in `.claude/commands/refinement-lenses/` that applies a specific analytical perspective to a feature or epic during refinement.

### Required Sections

#### `# Lens Name`

The display name of the lens (e.g., "Token Economics", "Architecture Review", "UX Validation").

#### `## Trigger Labels` (optional)

A list of GitHub labels that, when present on the issue, automatically trigger this lens to be applied during refinement. If this section is absent or empty, the lens is only loaded if referenced as a default.

Example:
```md
## Trigger Labels

- `scope:distributed`
- `area:auth`
```

#### `## Perspective`

A one-sentence description of the analytical viewpoint this lens brings. Explains what lens-specific questions the executor will ask.

Example:
```md
## Perspective

Analyze the per-session token cost and documentation overhead of each story, and ensure documentation work is clustered by file rather than scattered across stories.
```

#### `## Lens Questions`

The specific questions the executor should ask during refinement when this lens is active. Each question should be answerable during or immediately after the breakdown step.

Questions should:
- Reference the breakdown output (seed stories/features just created)
- Be actionable — the executor should be able to modify the breakdown in response
- Avoid yes/no questions in favor of diagnostic questions that might reveal scope problems

Example:
```md
## Lens Questions

1. **Per-session token analysis**: For each story, estimate the per-session overhead (tool definitions, long context setup, tool invocations that don't ship). Are any stories heavy on overhead relative to deliverable code? Can overhead be moved to shared documentation or eliminated?

2. **Documentation clustering**: Identify all stories that write to the same file (especially `.md`, `.toml`, `pyproject.toml`, or other config). Are they in the same story, or split across multiple stories? If split, consider merging them into one story to avoid coordination overhead.

3. **Model tier alignment**: For each story, assess whether the assigned executor tier (haiku, sonnet, opus per `executor:` label) matches the story's complexity. Does the story fit haiku's scope (documentation, boilerplate, small config changes)? If not, should the story be moved to sonnet tier?
```

## Lens Selection During Refinement

During the lens selection step in `/refine-feature` and `/refine-epic`:

1. Read all `.md` files in `.claude/commands/refinement-lenses/`
2. Always apply the `token-economics.md` lens (default)
3. Additionally apply any lens whose `## Trigger Labels` match the current issue's GitHub labels
4. For each active lens, present its `## Lens Questions` to the executor before the breakdown step

## Lens File Naming

Lens files must:
- Be placed in `.claude/commands/refinement-lenses/`
- Have a `.md` extension
- Use lowercase filenames with hyphens (e.g., `token-economics.md`, `architecture-review.md`)
- Begin with `_` only for the template file (`_template.md`)

## Adding a New Lens

To add a new lens:

1. Create a new `.md` file in `.claude/commands/refinement-lenses/`
2. Follow the template format above
3. Do not add the lens to a hardcoded list in the refine commands — the commands will automatically discover and load it based on trigger labels
4. Update this template if the format needs clarification or extension
