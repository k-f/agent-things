---
name: review-file
description: Single-file security review. Fast (minutes, not hours). Skips the full agent team — runs an inline manager that dispatches the relevant subset of hunters scoped to one file plus its directly-imported context. Useful for spot-checking a single module before commit.
context: fork
agent: general-purpose
disable-model-invocation: true
argument-hint: "<path-to-file> [type=poc|internal|production|regulated|safety-critical]"
allowed-tools: Bash, Read, Glob, Grep, Write, Edit, Task
---

# Security Review — single file

Lightweight scoped review. Trades coverage for speed. Use to vet one file before committing, not for a comprehensive audit.

You run as a forked subagent: you can't pose questions mid-run — nobody is there to answer. Shell state doesn't persist between Bash calls — use literal values. Dispatch plugin agents by their namespaced name: `subagent_type: security-review:sr-<agent>`.

- `SCRIPT_DIR` = `${CLAUDE_PLUGIN_ROOT}/scripts`
- Raw arguments: `$ARGUMENTS`

## Procedure

1. **Parse arguments** (order-independent): `type=<t>` → `PROJECT_TYPE` (default `infer`); the one bare token → `FILE`. Missing `FILE`, more than one bare token, a non-regular file, or an invalid value → return an error naming the problem.

2. **Pick hunter classes** from a quick look (`head -200 "$FILE"`):
   - Always: `sr-injection-hunter`, `sr-codeexec-hunter`, `sr-supplychain-secrets-hunter`
   - Imports / uses crypto: add `sr-crypto-hunter`
   - Route handler / web framework code: add `sr-authnz-hunter`, `sr-web-hunter`
   - Money / state-machine / permissions logic: add `sr-businesslogic-hunter`

3. **Repo and project type.** `REPO_ROOT=$(git -C "$(dirname "$FILE")" rev-parse --show-toplevel 2>/dev/null || dirname "$FILE")`. If `PROJECT_TYPE` is `infer`, apply the inference block in `${CLAUDE_PLUGIN_ROOT}/references/procedure.md` §1a to `REPO_ROOT` and keep the one-sentence rationale. Initialize:
   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/init_run.py" --targets "<REPO_ROOT>" --project-type "<PROJECT_TYPE>" --depth quick
   ```
   stdout is `RUN_ID`; `RUN_DIR=<abs cwd>/.security-review/<RUN_ID>`. Log type (given/inferred + rationale) to `$RUN_DIR/worklog/manager.md`; if inferred, append the "Project type inference" section to `calibration.md` (procedure §1b).

4. Skip phases 2–3. Write a recon stub at `$RUN_DIR/recon/<repo>.md` listing just this file as priority 5.

5. One assignment per chosen hunter, scoped to `FILE`. Hypothesis seed: "default".

6. Dispatch hunters (≤3 in parallel). Depth is `quick`, so pass `model: sonnet` on each hunter Task (model plan in calibration.md).

7. Verify candidates with `sr-verifier` (no model override; dispatch as procedure §5).

8. Skip `sr-chain-composer`.

9. `sr-triage` (procedure §6), then `sr-report-compiler` (procedure §7) for a mini report at `$RUN_DIR/report.md`.

10. Return the full report inline (typically 1–5 findings) plus the manager-log lines.

## Skips vs the full review
Multi-repo analysis · threat modelling · real recon (stubbed) · chain composition · per-class partitioning / seed diversity · cross-repo analyst.

## Keeps
Hypothesize-verify loop with adversarial self-challenge · independent verifier · full finding schema (CVSS, exploit scenario, test plan) · project-type calibration · the exclusion list (no DoS, no rate-limiting-without-impact, etc.).
