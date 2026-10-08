---
name: project-skill-issue
description: Audit the current project's Claude Code configuration and delegation readiness — CLAUDE.md, skills, agents, hooks, CI, docs and production posture — calibrated to the project type. Read-only. Run on its own or via /skill-issue:skill-issue.
context: fork
agent: general-purpose
disable-model-invocation: true
argument-hint: "[poc|internal|production|regulated|safety-critical|library]"
allowed-tools: Bash, Read, Glob, Grep
disallowed-tools: Write, Edit, NotebookEdit
---

# Project Skill Issue — Configuration Audit

You cannot ask the user anything during this run. Parse the arguments, state assumptions,
and produce one complete report. Read-only: do not modify the project.

## 1. Parse arguments

Arguments: `$ARGUMENTS`

Lowercase the first token. If it is one of `poc`, `internal`, `production`, `regulated`,
`safety-critical`, `library`, that is `PROJECT_TYPE` (given). Anything else → ignore it and
list it in the report header.

Set `PROJECT_DIR` = output of `pwd`.

If `PROJECT_TYPE` was not given, infer it following
`${CLAUDE_PLUGIN_ROOT}/references/project-types.md` and record the one-sentence reason.

## 2. Run the rubric

Read `${CLAUDE_PLUGIN_ROOT}/references/project-rubric.md` and follow Steps 1–9 with the
inputs above.

## 3. Output

```markdown
# Project Skill Issue Report

> **Project type: `<id>` (<given|inferred>)** — <one-sentence reason>
> <Ignored arguments: ..., if any>

<Project Analysis section from the rubric's Step 9 template>
```
