---
name: triage-only
description: Re-run triage / chain composition / report compilation against an existing on-disk run. Useful when hunters were good but the report needs regeneration, the project-type calibration needs to change, or after manually editing finding files.
context: fork
agent: general-purpose
disable-model-invocation: true
argument-hint: "<run-id> [type=poc|internal|production|regulated|safety-critical] [reason=\"...\"]"
allowed-tools: Bash, Read, Glob, Grep, Write, Edit, Task
---

# Re-run triage and report

Re-runs phases 6, 6.5 and 7 against an existing run dir. Does not re-run hunters or verifiers. You run as a forked subagent: you can't pose questions mid-run — nobody is there to answer. Dispatch plugin agents by their namespaced name: `subagent_type: security-review:sr-<agent>`.

- `SCRIPT_DIR` = `${CLAUDE_PLUGIN_ROOT}/scripts`
- Raw arguments: `$ARGUMENTS`

## Procedure

1. **Parse arguments.** First bare token → `RUN_ID` (required). `type=<t>` → new `PROJECT_TYPE` (a bare second token that is a valid type is accepted too). `reason=<text>` (may be quoted, may contain spaces) → `REASON` (default "not stated"). Confirm `.security-review/<RUN_ID>/` exists; otherwise return an error listing `ls .security-review/`. `RUN_DIR` = its absolute path.

2. **Calibration change** (only if a new type was given and differs from the current one). Per `calibration.md`'s contract, don't overwrite the project-type line — append:
   ```
   ## Calibration change — <timestamp>
   Project type changed from `<old>` to `<new>` because <REASON>.
   This re-triage applies the new severity bar; prior triage decisions remain in finding history.
   ```

3. Audit:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/replay.py" --run "<RUN_ID>" --check-consistency
   ```

4. `ls "<RUN_DIR>"/findings/SR-*.md 2>/dev/null | head`. None → return "No confirmed findings in run `<RUN_ID>`; nothing to triage." and stop.

5. Read `${CLAUDE_PLUGIN_ROOT}/references/procedure.md` and dispatch, in order and using its prompts: `sr-triage` (§6), then — once validation passes — `sr-chain-composer` (§6.5), then `sr-report-compiler` (§7). No model overrides: these three always use their agent defaults.

6. Append each step's headline to `<RUN_DIR>/worklog/manager.md` and return: the new report path, severity counts, any calibration change applied, and those log lines.

## When to use this

- You added or edited a finding manually
- You want to re-calibrate (e.g. system moved from PoC → production)
- compile_report.py's output format changed and you want to regenerate
- An earlier triage pass was interrupted before chain composition or report
