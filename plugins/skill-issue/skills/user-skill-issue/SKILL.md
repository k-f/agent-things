---
name: user-skill-issue
description: Analyse your Claude Code interaction history to surface AI-fluency patterns, prompting habits and delegation depth. Scores from session structure (tool runs, subagents, skills, interrupts) and quoted messages. Read-only. Run on its own or via /skill-issue:skill-issue.
context: fork
agent: general-purpose
disable-model-invocation: true
argument-hint: "[current|all]"
allowed-tools: Bash, Read, Glob, Grep
disallowed-tools: Write, Edit, NotebookEdit
---

# User Skill Issue — Interaction Log Analysis

You cannot ask the user anything during this run. Parse the arguments, state assumptions,
and produce one complete report.

## 1. Parse arguments

Arguments: `$ARGUMENTS`

Split on whitespace, lowercase each token:
- `current` or `all` → `SCOPE`. Missing → `current`.
- A project-type id (`poc`, `internal`, `production`, `regulated`, `safety-critical`,
  `library`) → `PROJECT_TYPE` (given).
- Anything else → ignore, and list the ignored tokens in the report header.

Set:
- `PROJECT_DIR` = output of `pwd`
- `SCRIPT` = `${CLAUDE_PLUGIN_ROOT}/scripts/extract_user_messages.py`
- `RUBRIC` = `${CLAUDE_PLUGIN_ROOT}/references/user-rubric.md`

If `PROJECT_TYPE` was not given, infer it following
`${CLAUDE_PLUGIN_ROOT}/references/project-types.md` (keep it brief: the type only affects D6).

## 2. Run the rubric

Read `RUBRIC` and follow Steps 1–6 with the inputs above. `PROJECT_CONTEXT` is not supplied
here, so build it yourself in Step 2.

Never edit `~/.claude/settings.json`; retention is reported as a recommendation with the
snippet from the rubric.

## 3. Output

```markdown
# User Skill Issue Report

> Scope: `<current|all>` · **Project type: `<id>` (<given|inferred>)** — <one-sentence reason>
> <Ignored arguments: ..., if any>

<User Analysis section from the rubric's Step 6 template>
```
