---
name: review-repo
description: Single-repo security review. Equivalent to /security-review:security-review with exactly one repo path; the cross-repo analyst is skipped.
context: fork
agent: general-purpose
disable-model-invocation: true
argument-hint: "[path] [type=poc|internal|production|regulated|safety-critical] [depth=quick|standard|deep|exhaustive]"
allowed-tools: Bash, Read, Glob, Grep, Write, Edit, Task
---

# Security Review — single repo

You run as a forked subagent: you can't pose questions mid-run — nobody is there to answer.

- `PLUGIN_ROOT` = `${CLAUDE_PLUGIN_ROOT}`
- `SCRIPT_DIR` = `${CLAUDE_PLUGIN_ROOT}/scripts`
- Raw arguments: `$ARGUMENTS`

## Parse arguments

Order-independent tokens: `type=<t>` → `PROJECT_TYPE`, `depth=<d>` → `DEPTH`; the one remaining bare token is `TARGET`. Defaults: `TARGET` = cwd, `PROJECT_TYPE` = `infer`, `DEPTH` = `standard`. Allowed values are the same as `/security-review:security-review`.

Return an error (don't guess) if: more than one bare token or a comma in it (use `/security-review:review-cross-repo` for several repos), `TARGET` isn't an existing directory, or a key/value is invalid.

## Run

Set `TARGETS=<TARGET>`, then read `${CLAUDE_PLUGIN_ROOT}/references/procedure.md` and follow it from §1. sr-cross-repo-analyst is never dispatched (it needs ≥2 repos).
