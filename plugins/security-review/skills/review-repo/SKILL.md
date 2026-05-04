---
name: review-repo
description: Single-repo security review. Equivalent to the full /security-review:security-review with a single repo path. Convenience wrapper that hard-codes the target to one repo.
context: fork
agent: general-purpose
disable-model-invocation: true
argument-hint: [path]
allowed-tools: Bash, Read, Glob, Grep, Write, Edit, Task
---

# Security Review — single repo

This skill is a convenience alias for `/security-review:security-review` with exactly one target.

## Procedure

1. Parse the argument as `TARGET`. If empty, default to the current working directory.
2. Confirm the path exists and is a directory.
3. Hand off to the parent skill's procedure: read `$CLAUDE_PLUGIN_ROOT/skills/security-review/SKILL.md` and follow it from §1 onward, using `TARGETS=<TARGET>`.

There is no behavioural difference from the parent skill when only one target is in scope. This skill exists so users can be explicit about intent. The cross-repo analyst is automatically skipped (it requires ≥2 repos).
