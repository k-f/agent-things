---
name: status
description: Pretty-print the current progress.md of a security review run. Cheap — no Task fan-out, just runs progress.py. Use to check on a long-running review at any time.
context: fork
agent: general-purpose
disable-model-invocation: true
argument-hint: [run-id]
allowed-tools: Bash, Read, Glob
---

# Security Review — status

## Procedure

1. Parse `RUN_ID` from the first argument. If absent, find the most recent run.

2. Render and print:
   ```bash
   python3 "$CLAUDE_PLUGIN_ROOT/scripts/progress.py" \
       ${RUN_ID:+--run "$RUN_ID"}
   ```

3. If a run is currently in progress (any plan.md row with status `running`), tell the user:
   > "This run is still in progress. To resume after a crash: `/security-review:security-review resume:<run-id>`"

   If the run is fully done:
   > "Final report: `.security-review/<run-id>/report.md`"

That's it. This skill is read-only and trivial — it exists so users have an obvious affordance for checking status without reading multiple files.
