---
name: review-diff
description: PR / diff-scoped security review. Mirrors the public claude-code-security-review GitHub Action behaviour. Reviews only files changed in a git diff range, with sufficient surrounding context for dataflow analysis. Fast (minutes). Suitable for pre-commit / CI use.
context: fork
agent: general-purpose
disable-model-invocation: true
argument-hint: [base-ref] [head-ref]
allowed-tools: Bash, Read, Glob, Grep, Write, Edit, Task
---

# Security Review — diff-scoped

Reviews only what's changed in a git diff range. Default range is `main..HEAD`. This is the closest analogue of the public claude-code-security-review GitHub Action — keep the mantra: "better to miss some theoretical issues than flood the report with false positives."

## Procedure

0. Locate plugin scripts:
   ```bash
   SCRIPT_DIR="${CLAUDE_PLUGIN_ROOT:-}/scripts"
   [ ! -d "$SCRIPT_DIR" ] && SCRIPT_DIR=$(dirname $(find ~/.claude/plugins -name "init_run.py" -path "*security-review*" 2>/dev/null | head -1))
   ```

1. Parse arguments. Defaults: `BASE=main`, `HEAD=HEAD`. Override from positional args.

2. Confirm we're inside a git repository:
   ```bash
   git rev-parse --git-dir 2>/dev/null
   ```

3. Compute the changed file list and changed line ranges:
   ```bash
   git diff --name-only "$BASE...$HEAD" | grep -v -E '^(node_modules|vendor|dist|build)/' > /tmp/sr-diff-files
   git diff -U10 "$BASE...$HEAD" > /tmp/sr-diff.patch
   ```

4. If no files changed (after exclusions), tell the user and exit.

5. Determine project type: ask the user briefly (poc / internal / production / regulated / safety-critical), or default to `internal` if they want to skip. Initialize a minimal run dir:
   ```bash
   RUN_ID=$(python3 "$SCRIPT_DIR/init_run.py" --targets . \
                                                --project-type "$PROJECT_TYPE" \
                                                --depth quick)
   ```

6. Synthesize recon: list all changed files at priority 5 (every changed line gets full attention), and any file that imports / is imported by changed files at priority 3.

7. Write one assignment per relevant hunter class (same logic as `review-file` for class selection, but scoped across the changed-file set).

   Each assignment includes:
   - The full list of changed files
   - The full diff patch path (`/tmp/sr-diff.patch`) for context on what specifically changed
   - Instruction: "Focus on the changed lines and the dataflow paths that touch them. Do NOT report findings whose root cause is outside the diff (those are pre-existing issues — out of scope for a PR review)."

8. Dispatch hunters in parallel (batch 5).

9. Verify candidates with `sr-verifier`.

10. Skip `sr-chain-composer` for diff scope (chains usually need context outside the diff).

11. Run `sr-triage` to finalize.

12. Run `sr-report-compiler` to produce a mini report.

## Output

Print the report inline. The full markdown report is at `.security-review/<run-id>/report.md`; per-finding files are at `.security-review/<run-id>/findings/SR-*.md` for downstream tooling that wants to parse them directly.

## Pre-existing-issue rule

If a hunter or verifier identifies a finding whose root cause exists at lines NOT touched by the diff, drop it from the report. The PR author isn't responsible for pre-existing issues, and a PR review isn't the right time to surface them. Note such drops in the worklog so users running `/security-review:security-review` later will surface them.
