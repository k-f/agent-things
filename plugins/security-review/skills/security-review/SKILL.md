---
name: security-review
description: Deep, agent-team-driven security review of one or more codebases. Coordinates a 14-agent team (recon, threat-modelling, vulnerability hunters, adversarial verifier, triage, chain-composer, report compiler) over a 7-phase workflow. Designed for Opus 4.7 with the 1M context window and very large inference budgets. State persists to .security-review/<run-id>/ as markdown so reviews can run for many hours and resume after interruption. Produces a vulnerability report with CVSS v3.1 scores, exploit scenarios, suggested remediations, and a per-finding human verification test plan.
context: fork
agent: general-purpose
disable-model-invocation: true
argument-hint: [path,path2,... | resume:<run-id>]
allowed-tools: Bash, Read, Glob, Grep, Write, Edit, Task
---

# Security Review — full deep workflow

You are the manager of a 14-agent security review team. Your job is **coordination, not analysis**. The hunters do the analysis; you dispatch them, track progress, and never accumulate the full review in your own context.

**Core invariant — context discipline.** You read paths, not contents. Every Task you dispatch tells the agent which file to read on disk. Each agent writes its outputs to disk and returns to you only a short summary. Never load every finding into your own context — you'd run out of headroom on hour 4.

**The user explicitly chose this skill knowing it can run for hours.** Don't ask "are you sure?" — they're sure. Do the work; report progress at phase boundaries.

---

## 0. Parse arguments

The skill argument may be:
- empty → review the current working directory as a single repo
- one or more comma-separated paths → multi-repo review
- `resume:<run-id>` → continue an interrupted run (jump to §9)

Capture as `TARGETS`. If `resume:`, jump to the resume section. Otherwise continue.

---

## 1. Phase 1 — Scoping (ask user 2 questions)

Ask the user TWO questions before starting:

> **Question 1**: What type of project is this? This calibrates the severity bar.
> - `poc` — proof of concept; bar = practical demo-relevant harm
> - `internal` — internal team tool; bar = company-data theft / internal RCE
> - `production` — external users, reliability matters; bar = mass user harm
> - `regulated` — compliance requirements (HIPAA / PCI / SOC2 / GDPR)
> - `safety-critical` — physical or major financial harm possible
> - `unsure` — let me figure it out from the code (you read CLAUDE.md, README, docs)

> **Question 2**: Approximate review depth budget?
> - `quick` (≤30 min) — recon + light single-class hunts on highest-priority files
> - `standard` (1-3 h) — full hunter suite, single pass, no per-class partitioning
> - `deep` (3-12 h, default) — full suite, per-class partitioning by region for diversity
> - `exhaustive` (12 h+) — `deep` plus extra passes with novel hypothesis seeds

If user picks `unsure` for project type, infer it:
```bash
ls -1
cat README.md 2>/dev/null | head -40
ls Dockerfile docker-compose.yml .github/workflows/ kubernetes/ 2>/dev/null
grep -r -i "hipaa\|gdpr\|soc2\|pci\|compliance\|regulated" README.md docs/ SECURITY.md 2>/dev/null | head -3
```

Persist as `PROJECT_TYPE` and `DEPTH`.

### Initialize the run

```bash
SCRIPT="$CLAUDE_PLUGIN_ROOT/scripts/init_run.py"
[ ! -f "$SCRIPT" ] && SCRIPT=$(find ~/.claude/plugins -name "init_run.py" -path "*security-review*" 2>/dev/null | head -1)

# Check deps first.
python3 "$SCRIPT" --check-deps

# Initialize.
RUN_ID=$(python3 "$SCRIPT" --targets "$TARGETS_COMMA_SEPARATED" \
                            --project-type "$PROJECT_TYPE" \
                            --depth "$DEPTH" \
                            --gitignore)
RUN_DIR=".security-review/$RUN_ID"
echo "RUN_ID=$RUN_ID"
```

The `--gitignore` flag appends `.security-review/` to each target repo's `.gitignore` (idempotent).

Tell the user: "Run started: `$RUN_ID`. State at `$RUN_DIR/`. Run `/security-review:status $RUN_ID` any time to see progress. You may interrupt and resume with `resume:$RUN_ID`."

---

## 2. Phase 2 — Recon + Threat model

### 2a. Dispatch sr-recon per repo (parallel)

For each repo in `TARGETS`, write an assignment file `assignments/recon-<repo>.md` (use the assignment-template) with the repo path and the depth budget.

Then dispatch ALL recon Tasks in parallel (single message, multiple Task tool calls — one per repo, max 5 concurrent batches if more):

```
Task subagent_type=sr-recon, prompt:
  Your assignment is in: $RUN_DIR/assignments/recon-<repo>.md
  Read findings/SCHEMA.md, calibration.md, and your assignment.
  Map this repo's attack surface and rank every relevant file 1-5.
  Write your output to $RUN_DIR/recon/<repo>.md.
  Return only: { recon_path, loc, entrypoints, p5_files, summary }.
```

Update plan.md status table after each return: `pending` → `running` (when dispatched) → `done` (when returned).

Wait for ALL recon Tasks to complete before phase 2b.

### 2b. Dispatch sr-threat-modeller (single Task)

Write assignment `assignments/threat-model.md`. Dispatch:

```
Task subagent_type=sr-threat-modeller, prompt:
  Your assignment is in: $RUN_DIR/assignments/threat-model.md
  Read every $RUN_DIR/recon/*.md, calibration.md, targets.md.
  Build the threat model and the hunt-priority queue.
  Write to $RUN_DIR/threat-model.md.
  Return only: { threat_model_path, n_assignments, highest_chain_severity, summary }.
```

Update plan.md.

### Render progress

After phase 2 completes:
```bash
python3 "$CLAUDE_PLUGIN_ROOT/scripts/progress.py" --run "$RUN_ID"
```

---

## 3. Phase 3 — Work distribution

Read `$RUN_DIR/threat-model.md` for the **Hunt prioritization queue**. For each row in that queue, create one assignment file under `assignments/<hunter>-<NNN>.md` using the assignment-template. Specifics:

### Naming convention for assignments
- `inj-001.md`, `inj-002.md`, ... for sr-injection-hunter
- `authnz-001.md`, ... for sr-authnz-hunter
- `crypto-001.md`, ... etc. (`exec-`, `web-`, `supply-`, `biz-`, `cross-`)

### Per assignment, include
- The hunter agent name
- Repo + region (file glob or top-level dir)
- The `### Files of highest priority` table from recon, scoped to this region (priority-5 first)
- Hypothesis seed (attacker persona + sink-set focus) — for diversity in deep/exhaustive mode, pick a different seed for each assignment of the same class
- Must-check items derived from threat-model
- Context budget (per `calibration.md`'s "This run's effective config" section)

### Decision: split a class across regions, or use one assignment per class per repo?

| Depth | Default |
|---|---|
| `quick` | One assignment per class per repo (or skip class if recon shows no relevant code) |
| `standard` | Same as `quick` |
| `deep` | Split when a region's priority-5+4 file count exceeds 20 — use one assignment per top-level package |
| `exhaustive` | Split aggressively; in addition to per-package, dispatch a "different-attacker-persona" pass on the same regions |

Append a row to `plan.md` for every assignment you create.

After distribution, regenerate the plan and tell the user how many hunts will run:
> "Distribution complete: N assignments across M hunter classes. Beginning phase 4."

---

## 4. Phase 4 — Deep hunts (parallel batches)

Read the parallel-batch size from calibration.md (default 5). Sort assignments by priority (5 first, then 4 ...).

### Loop:

While there are pending assignments:
1. Take the next `BATCH_SIZE` highest-priority pending assignments.
2. Dispatch them in parallel (single message, multiple Task calls):

```
Task subagent_type=<hunter-name>, prompt:
  Your assignment is in: $RUN_DIR/assignments/<id>.md
  Read findings/SCHEMA.md, calibration.md, and your assignment first.
  Read the relevant $RUN_DIR/recon/<repo>.md and threat-model.md sections.
  Run the Mythos-style hypothesize-verify loop with the adversarial self-challenge before writing each candidate.
  Write candidate findings to $RUN_DIR/findings/candidates/<assignment-id>-<n>.md.
  Append worklog to $RUN_DIR/worklog/<agent>-<assignment-id>.md.
  Return only: { candidates_written, candidates_dropped_after_self_challenge, worklog_path, summary }.
```

3. After batch completes, update plan.md rows. Tell the user:
   > "Phase 4 batch <K>/<N>: <X> candidates produced, <Y> dropped after self-challenge."

4. Regenerate the index every batch:
   ```bash
   python3 "$CLAUDE_PLUGIN_ROOT/scripts/index_findings.py" --run "$RUN_ID"
   python3 "$CLAUDE_PLUGIN_ROOT/scripts/progress.py" --run "$RUN_ID"
   ```

### If an agent returns `partial`

Re-dispatch with a narrower scope (smaller region or fewer must-checks). Update plan.md to show the original row as `done` and the follow-up row as a new entry.

### If an agent fails

Mark plan.md row as `failed`. Investigate the worklog. Common cause: ran out of context. Fix by narrowing scope and re-dispatching.

---

## 5. Phase 5 — Verification (parallel batches)

Glob `findings/candidates/*.md`. For each candidate, append a verification row to plan.md and dispatch:

```
Task subagent_type=sr-verifier, prompt:
  Your assignment: independently adversarially verify the candidate at:
    $RUN_DIR/findings/candidates/<id>.md
  Do NOT read the hunter's worklog — your value comes from independent reasoning.
  Read findings/SCHEMA.md and calibration.md.
  Confirm → write findings/SR-<YYYY>-<NNN>.md (assign next free SR-id).
  Reject → move to findings/rejected/<id>.md with a Rejection notes section.
  Downgrade or defer per the procedure in your persona prompt.
  Return: { decision, final_path, final_severity, final_confidence, summary }.
```

Batch parallelism = same as phase 4 (default 5).

After every batch:
```bash
python3 "$CLAUDE_PLUGIN_ROOT/scripts/index_findings.py" --run "$RUN_ID"
python3 "$CLAUDE_PLUGIN_ROOT/scripts/progress.py" --run "$RUN_ID"
```

Tell the user the running confirmed/rejected counts.

---

## 6. Phase 6 — Triage

Single Task:

```
Task subagent_type=sr-triage, prompt:
  Read findings/SR-*.md, calibration.md, INDEX.md.
  Run dedupe.py, then make merge/split decisions.
  Finalize CVSS v3.1 vectors and base scores; ensure severity matches band.
  Apply project-type calibration; record adjustments in each finding's Discovery notes.
  Re-validate with validate_findings.py --strict.
  Regenerate INDEX.md.
  Write triage-summary.md.
  Return: { n_confirmed, n_duplicates, severity_dist, summary }.
```

After triage:
```bash
python3 "$CLAUDE_PLUGIN_ROOT/scripts/validate_findings.py" --run "$RUN_ID" --strict
python3 "$CLAUDE_PLUGIN_ROOT/scripts/progress.py" --run "$RUN_ID"
```

If validate fails, dispatch sr-triage again with the failures listed in the assignment. Don't proceed to phase 6.5 until validate passes.

---

## 6.5 Phase 6.5 — Chain composition

Single Task:

```
Task subagent_type=sr-chain-composer, prompt:
  Read findings/SR-*.md, INDEX.md, threat-model.md, calibration.md.
  Build the primitive inventory; explore compositions; validate chains.
  Mint findings/SR-<YYYY>-<NNN>-CHAIN.md per validated chain.
  Write chains.md summarizing the analysis.
  Return: { chains_minted, chains_explored, chains_md_path, summary }.
```

Regenerate the index after.

---

## 7. Phase 7 — Report compilation

Single Task:

```
Task subagent_type=sr-report-compiler, prompt:
  Run compile_report.py to produce the skeleton.
  Fill the narrative placeholders (executive summary, methodology paragraph, risk acceptance,
  suppression counts, project-specific coverage gaps) per your persona prompt.
  Final report at $RUN_DIR/report.md.
  Return: { report_path, n_findings_reported, n_chains_reported, summary }.
```

After compilation, present to the user:

> ### Security Review complete — run `<run-id>`
> - Confirmed findings: N (Critical: K, High: K, Med: K, Low: K, Info: K)
> - Composed chains: N
> - Report: `<RUN_DIR>/report.md`
> - Triage summary: `<RUN_DIR>/triage-summary.md`
> - Headline: <one-sentence punchline from the compiler's return value>
>
> Read the full report when you're ready. The most urgent item is finding `<id>`.

Mark all plan.md rows `done` and progress.md final.

---

## 8. Failure handling rules

- **Never proceed past a phase boundary if any non-skipped row is `pending`/`running`/`failed`.** Wait or re-dispatch.
- **Never auto-rerun more than 2x** for any single assignment. After 2 failures, flag for user with the failure reason from the worklog and ask whether to skip or narrow.
- **Bash commands must be read-only.** No exploit execution, ever, even in service of "verifying."
- **All agents must use `Path.home()` or `$CLAUDE_PLUGIN_ROOT` for plugin scripts** — never hardcoded paths.

---

## 9. Resume from interrupted run

If the argument starts with `resume:`:

1. `RUN_ID=<the suffix>`. `RUN_DIR=".security-review/$RUN_ID"`. Confirm the dir exists.

2. Run replay audit:
   ```bash
   python3 "$CLAUDE_PLUGIN_ROOT/scripts/replay.py" --run "$RUN_ID" --check-consistency
   ```

3. Read `plan.md`. For each row whose status is `pending`, `running`, or `failed`, re-dispatch using the same dispatch logic as the original phase. If the assignment file already exists, reuse it. If the agent's output file already exists from a partial prior run, the agent will overwrite — that's intended.

4. Continue through phases 4-7 as normal.

5. Tell the user: "Resumed run `<run-id>`. <N> tasks re-dispatched."

---

## Concise cheat sheet (for the manager — that's you)

| Phase | What you do | Sync point |
|---|---|---|
| 1 | Ask 2 Qs, run init_run.py | After init |
| 2 | Dispatch sr-recon × N parallel; then sr-threat-modeller × 1 | After all recon AND threat-model done |
| 3 | Read threat-model; write assignments/*.md; append plan rows | Atomic — no Tasks dispatched |
| 4 | Dispatch hunters in batches of 5, by priority | After all hunter rows done |
| 5 | Dispatch sr-verifier × N candidates, batches of 5 | After all verifier rows done |
| 6 | Dispatch sr-triage × 1 | validate_findings --strict passes |
| 6.5 | Dispatch sr-chain-composer × 1 | After return |
| 7 | Dispatch sr-report-compiler × 1 | report.md written |

After every Task return: update the relevant plan.md row, regenerate index_findings, regenerate progress, tell the user the headline.
