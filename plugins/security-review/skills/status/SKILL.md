---
name: status
description: Pretty-print the current progress.md of a security review run. Cheap — no Task fan-out, just runs progress.py. Use to check on a long-running review at any time.
context: fork
agent: general-purpose
disable-model-invocation: true
argument-hint: "[run-id]"
allowed-tools: Bash, Read, Glob
---

# Security Review — status

Read-only. Raw arguments: `$ARGUMENTS`

1. `RUN_ID` = the first argument token, if any. Render (with no `RUN_ID`, progress.py picks the most recent run under cwd):
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/progress.py" --run "<RUN_ID>"   # or without --run when RUN_ID is empty
   ```

2. Return the rendered dashboard, followed by one line:
   - any plan.md row `running` → "Still in progress. If the review session died, resume with `/security-review:security-review resume:<run-id>`."
   - `report.md` exists in the run dir → "Final report: `.security-review/<run-id>/report.md`"
