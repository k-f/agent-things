---
name: review-file
description: Single-file security review. Fast (minutes, not hours). Skips the full agent team — runs an inline manager that dispatches the relevant subset of hunters scoped to one file plus its directly-imported context. Useful for spot-checking a single module before commit.
context: fork
agent: general-purpose
disable-model-invocation: true
argument-hint: <path-to-file>
allowed-tools: Bash, Read, Glob, Grep, Write, Edit, Task
---

# Security Review — single file

Lightweight scoped review. Trades coverage for speed. Use when you want to vet one file before committing, not for a comprehensive audit.

## Procedure

1. Parse argument as `FILE`. Require a file path. Confirm it exists and is a regular file.

2. Determine relevant hunter classes by inspecting the file's content briefly:
   ```bash
   head -200 "$FILE"
   ```
   Pick the subset of hunters whose vulnerability classes plausibly apply:
   - Always: `sr-injection-hunter`, `sr-codeexec-hunter`, `sr-supplychain-secrets-hunter`
   - If file imports / uses crypto: add `sr-crypto-hunter`
   - If file is a route handler / web framework code: add `sr-authnz-hunter`, `sr-web-hunter`
   - If file implements money / state-machine / permissions: add `sr-businesslogic-hunter`

3. Initialize a minimal run dir using `init_run.py` with `--targets <containing-repo> --depth quick`.

4. Skip phases 2-3 (no recon, no threat model). Synthesize a minimal recon stub at `recon/<repo>.md` listing just this file as priority 5.

5. Write one assignment per chosen hunter, scoped to `FILE` only. Hypothesis seed: "default" (no diversity needed at this scale).

6. Dispatch hunters (at most 3 in parallel — file scope is small). Wait.

7. Verify candidates with `sr-verifier`.

8. Skip `sr-chain-composer` (single-file scope rarely produces meaningful chains).

9. Run `sr-triage` to finalize CVSS + calibration.

10. Run `sr-report-compiler` to produce a mini report at `<run-dir>/report.md`.

11. Print the full report inline (since it's small — typical: 1-5 findings).

## What this skill skips vs the full review
- Multi-repo analysis (uses just the file's containing repo)
- Threat modelling (no need at file scope)
- Recon (synthesized stub)
- Chain composition (rarely meaningful at this scope)
- Per-class partitioning / hypothesis-seed diversity
- Cross-repo analyst

## What this skill keeps
- The hypothesize-verify loop with adversarial self-challenge
- The independent verifier pass
- The schema for findings (full CVSS, exploit scenarios, test plans)
- Project-type calibration
- The exclusion list (no DoS, no rate-limiting-without-impact, etc.)
