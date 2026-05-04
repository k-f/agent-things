---
name: review-cross-repo
description: Multi-repo security review with the cross-repo trust-boundary analyst enabled. Use when reviewing two or more codebases that interact (client+server, microservice mesh, producer+consumer). The cross-repo analyst surfaces gaps where one side trusts data the other doesn't sanitize, schema-disagreement bugs, and authn/authz transitivity flaws across services.
context: fork
agent: general-purpose
disable-model-invocation: true
argument-hint: <path1,path2[,path3,...]>
allowed-tools: Bash, Read, Glob, Grep, Write, Edit, Task
---

# Security Review — multi-repo

This skill is a convenience alias for `/security-review:security-review` with multiple targets. It guarantees the `sr-cross-repo-analyst` is dispatched in phase 4.

## Procedure

1. Parse the argument as a comma-separated list of paths. Require at least 2.
2. Confirm each path exists and is a directory.
3. Hand off to the parent skill's procedure: read `$CLAUDE_PLUGIN_ROOT/skills/security-review/SKILL.md` and follow it from §1 onward, using `TARGETS=<paths-comma-separated>`.

In phase 3 (work distribution), ensure at least one assignment exists for `sr-cross-repo-analyst`. The threat-modeller will normally include it, but verify and add if missing. The cross-repo analyst's assignment must reference every repo's `recon/<repo>.md` file.
