---
name: review-cross-repo
description: Multi-repo security review with the cross-repo trust-boundary analyst enabled. Use when reviewing two or more codebases that interact (client+server, microservice mesh, producer+consumer). The cross-repo analyst surfaces gaps where one side trusts data the other doesn't sanitize, schema-disagreement bugs, and authn/authz transitivity flaws across services.
context: fork
agent: general-purpose
disable-model-invocation: true
argument-hint: "<path1,path2[,...]> [type=poc|internal|production|regulated|safety-critical] [depth=quick|standard|deep|exhaustive]"
allowed-tools: Bash, Read, Glob, Grep, Write, Edit, Task
---

# Security Review — multi-repo

You run as a forked subagent: you can't pose questions mid-run — nobody is there to answer.

- `PLUGIN_ROOT` = `${CLAUDE_PLUGIN_ROOT}`
- `SCRIPT_DIR` = `${CLAUDE_PLUGIN_ROOT}/scripts`
- Raw arguments: `$ARGUMENTS`

## Parse arguments

Order-independent tokens: `type=<t>` → `PROJECT_TYPE`, `depth=<d>` → `DEPTH`; bare tokens are paths (comma-separated and/or space-separated) concatenated into `TARGETS`. Defaults: `PROJECT_TYPE` = `infer` (inferred from the first repo; when repos disagree, use the most demanding type and say so in the rationale), `DEPTH` = `standard`.

Return an error (don't guess) if fewer than 2 paths are given, any path isn't an existing directory, or a key/value is invalid.

## Run

Read `${CLAUDE_PLUGIN_ROOT}/references/procedure.md` and follow it from §1 with these `TARGETS`.

Additional rule for phase 3: at least one assignment must exist for `sr-cross-repo-analyst`. The threat-modeller normally includes it; add it if missing. Its assignment must reference every repo's `recon/<repo>.md`.
