---
name: triage-only
description: Re-run triage / chain composition / report compilation against an existing on-disk run. Useful when hunters were good but the report needs regeneration, the project-type calibration needs to change, or after manually editing finding files.
context: fork
agent: general-purpose
disable-model-invocation: true
argument-hint: <run-id> [project-type]
allowed-tools: Bash, Read, Glob, Grep, Write, Edit, Task
---

# Re-run triage and report

Re-runs phases 6, 6.5, and 7 against an existing run dir. Does not re-run hunters or verifiers.

## Procedure

1. Parse `RUN_ID` from the first argument. Confirm `.security-review/<RUN_ID>/` exists.

2. If a second argument is provided, treat it as the new `PROJECT_TYPE`. Per `calibration.md`'s own contract ("If calibration must change, append a new section with timestamp and rationale"), do NOT overwrite the existing project-type line — instead append a new section to `calibration.md`:
   ```
   ## Calibration change — <timestamp>
   Project type changed from `<old>` to `<new>` because <user-supplied reason or "unspecified">.
   This re-triage applies the new severity bar; prior triage decisions remain in finding history.
   ```
   The new project type takes effect for the re-run; the original is preserved as audit history.

3. Run replay audit:
   ```bash
   python3 "$CLAUDE_PLUGIN_ROOT/scripts/replay.py" --run "$RUN_ID" --check-consistency
   ```

4. Confirm there are confirmed findings to triage:
   ```bash
   ls .security-review/$RUN_ID/findings/SR-*.md 2>/dev/null | head
   ```
   If none, tell the user and exit.

5. Dispatch `sr-triage` (per the parent skill's §6).

6. After triage passes validate, dispatch `sr-chain-composer` (parent §6.5).

7. Dispatch `sr-report-compiler` (parent §7).

8. Tell the user the new report is at `.security-review/$RUN_ID/report.md`.

## When to use this

- You added a new finding manually (rare, but possible)
- You want to re-calibrate (e.g. system moved from PoC → production)
- The compile_report.py output format changed and you want to regenerate
- An earlier triage pass was interrupted before chain composition or report
- You want to inspect chain composition independently of the rest of the workflow
