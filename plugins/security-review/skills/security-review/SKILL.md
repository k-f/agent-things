---
name: security-review
description: Deep, agent-team-driven security review of one or more codebases. Coordinates a 14-agent team (recon, threat-modelling, vulnerability hunters, adversarial verifier, triage, chain-composer, report compiler) over a 7-phase workflow. Designed for a large-context Opus-class model. State persists to .security-review/<run-id>/ as markdown so long reviews can resume after interruption. Produces a vulnerability report with CVSS v3.1 scores, exploit scenarios, suggested remediations, and a per-finding human verification test plan.
context: fork
agent: general-purpose
disable-model-invocation: true
argument-hint: "[path,path2,...] [type=poc|internal|production|regulated|safety-critical] [depth=quick|standard|deep|exhaustive] | resume:<run-id>"
allowed-tools: Bash, Read, Glob, Grep, Write, Edit, Task
---

# Security Review — full workflow

You run as a forked subagent: you can't pose questions mid-run — nobody is there to answer. Everything you need is in the arguments below or has a default.

- `PLUGIN_ROOT` = `${CLAUDE_PLUGIN_ROOT}`
- `SCRIPT_DIR` = `${CLAUDE_PLUGIN_ROOT}/scripts`
- Raw arguments: `$ARGUMENTS`

## Parse arguments

Split the raw arguments on whitespace. Tokens are order-independent:

| Token | Effect |
|---|---|
| `resume:<run-id>` | `RESUME_ID=<run-id>`; other tokens are ignored (the run's stored type/depth win) |
| `type=<t>` | `PROJECT_TYPE=<t>`; `t` ∈ `poc`, `internal`, `production`, `regulated`, `safety-critical` (`unsure` is accepted and means `infer`) |
| `depth=<d>` | `DEPTH=<d>`; `d` ∈ `quick`, `standard`, `deep`, `exhaustive` |
| anything else | a target path or comma-separated list; multiple such tokens are concatenated into `TARGETS` |

Defaults:
- `TARGETS` → the current working directory.
- `PROJECT_TYPE` → `infer` (procedure §1a infers it from the code and records why).
- `DEPTH` → `standard`: a full hunter suite in 1–3 h. `deep` (3–12 h) and `exhaustive` are opt-in so nobody gets an hours-long, expensive run by surprise.

Invalid input (unknown `key=`, value outside the allowed set, a target path that doesn't exist) → stop and return a one-paragraph error naming the bad token and the accepted forms. Don't guess.

## Run

Read `${CLAUDE_PLUGIN_ROOT}/references/procedure.md` and follow it, using the values above. With `RESUME_ID` set, go straight to its §9.
