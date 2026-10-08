# Security Review — shared manager procedure

Loaded by `security-review`, `review-repo` and `review-cross-repo`. The calling skill has already resolved these inputs and handed them to you:

| Input | Meaning |
|---|---|
| `PLUGIN_ROOT` | Absolute plugin root (the calling skill substituted `${CLAUDE_PLUGIN_ROOT}`) |
| `SCRIPT_DIR` | `$PLUGIN_ROOT/scripts` |
| `TARGETS` | Comma-separated repo paths (default: cwd) |
| `PROJECT_TYPE` | `poc` \| `internal` \| `production` \| `regulated` \| `safety-critical` \| `infer` |
| `DEPTH` | `quick` \| `standard` \| `deep` \| `exhaustive` |
| `RESUME_ID` | Set only for `resume:<run-id>` → jump to §9 |

You are the manager of a 14-agent security review team. Your job is **coordination, not analysis**. The hunters do the analysis; you dispatch them, track progress, and never accumulate the full review in your own context.

**Context discipline.** You read paths, not contents. Every Task you dispatch tells the agent which file to read on disk. Each agent writes its outputs to disk and returns only a short summary. Never load every finding into your own context.

**You run as a forked subagent.** Nobody answers questions or reads intermediate messages mid-run. Every decision below has a default — take it and record it. Status lines go to the **manager log** `$RUN_DIR/worklog/manager.md` (append one line per event, formatted `- <YYYY-MM-DD HH:MM:SS> <text>`; init_run.py creates the file); `progress.py` shows its tail, so `/security-review:status` surfaces it while you run. Your single final message (§7) is the only thing the user sees from you.

**Shell state does not persist between Bash calls.** Write commands with literal values for `SCRIPT_DIR`, `RUN_ID` and `RUN_DIR` (or re-assign them at the top of each command). Don't rely on an `export` from an earlier call.

**When dispatching agents, put the absolute paths in the prompt** — `Run directory: <abs RUN_DIR>` and `Scripts dir: <abs SCRIPT_DIR>`. Agents can't see your variables.

**Plugin agents are namespaced.** Dispatch with `subagent_type: security-review:<agent>` (e.g. `security-review:sr-recon`). Where this document or a skill names an agent bare (`sr-verifier`), that is the agent; the dispatch name always carries the `security-review:` prefix.

---

## 1. Phase 1 — Scoping

### 1a. Resolve project type (no questions)

If `PROJECT_TYPE` is `infer`, infer it from the first target BEFORE calling init_run.py:

```bash
T="<first target>"   # don't cd — the working directory persists and decides where .security-review/ lands
ls -1 "$T"; head -40 "$T/README.md" 2>/dev/null; head -40 "$T/CLAUDE.md" 2>/dev/null
ls -d "$T"/Dockerfile "$T"/docker-compose.yml "$T"/.github/workflows "$T"/kubernetes "$T"/k8s "$T"/helm "$T"/terraform 2>/dev/null
grep -r -i -l "hipaa\|gdpr\|soc ?2\|pci\|compliance\|regulated\|iec 61508\|iso 26262\|do-178" "$T/README.md" "$T/docs" "$T/SECURITY.md" 2>/dev/null | head -3
```

Pick one concrete type from `{poc, internal, production, regulated, safety-critical}` and write a one-sentence rationale citing the evidence (e.g. "Dockerfile + k8s manifests + public API docs → `production`"). No signal at all → `internal`, rationale "no deployment or compliance signals found; defaulted". `infer` is never passed to init_run.py.

Keep the rationale as `TYPE_NOTE`; it goes into the manager log, calibration.md, and the final result.

### 1b. Initialize the run

```bash
python3 "<SCRIPT_DIR>/init_run.py" --check-deps
python3 "<SCRIPT_DIR>/init_run.py" --targets "<TARGETS>" --project-type "<PROJECT_TYPE>" --depth "<DEPTH>" --gitignore
```

stdout is the `RUN_ID`. `RUN_DIR` = `<absolute cwd>/.security-review/<RUN_ID>`. `--gitignore` appends `.security-review/` to each target repo's `.gitignore` (idempotent).

init_run.py writes the **model plan** for this depth into `calibration.md` ("This run's effective config") and the `plan.md` header — see §4a.

If the type was inferred, append to `$RUN_DIR/calibration.md`:

```
## Project type inference
Inferred `<type>`: <TYPE_NOTE>. Re-run with `type=<other>` (or `/security-review:triage-only <run-id> type=<other>`) if wrong.
```

First manager-log lines:

```
- <ts> run <RUN_ID> started · targets=<TARGETS> · type=<PROJECT_TYPE> (<"given" | "inferred: " + TYPE_NOTE>) · depth=<DEPTH>
- <ts> check progress: /security-review:status <RUN_ID> · resume after interruption: /security-review:security-review resume:<RUN_ID>
```

| Depth | Scope | Rough wall-clock |
|---|---|---|
| `quick` | recon + one light pass per relevant class on highest-priority files | ≤30 min |
| `standard` (default) | full hunter suite, single pass, no per-class partitioning | 1–3 h |
| `deep` | full suite, per-class partitioning by region, diverse hypothesis seeds | 3–12 h |
| `exhaustive` | `deep` + extra passes with novel attacker personas | 12 h+ |

---

## 2. Phase 2 — Recon + threat model

### 2a. sr-recon per repo (parallel)

For each repo in `TARGETS`, write `assignments/recon-<repo>.md` from `$PLUGIN_ROOT/templates/assignment-template.md` (repo path, depth budget). Add a plan.md row per repo. Dispatch all recon Tasks in one message (max 5 at a time):

```
Task subagent_type=security-review:sr-recon, prompt:
  Run directory: <RUN_DIR>
  Scripts dir: <SCRIPT_DIR>
  All paths in your persona prompt that look like findings/..., recon/..., assignments/...,
  worklog/..., calibration.md, threat-model.md, targets.md live UNDER the run directory.
  Your assignment is at <RUN_DIR>/assignments/recon-<repo>.md — read it first, then
  <RUN_DIR>/findings/SCHEMA.md and <RUN_DIR>/calibration.md.
  Map this repo's attack surface and rank every relevant file 1-5.
  Write your output to <RUN_DIR>/recon/<repo>.md.
  Return only: { recon_path, loc, entrypoints, p5_files, summary }.
```

Update plan.md rows `pending` → `running` (dispatched) → `done` (returned). Wait for all recon before 2b.

### 2b. sr-threat-modeller (single Task)

Write `assignments/tm-001.md`, add a plan row, dispatch:

```
Task subagent_type=security-review:sr-threat-modeller, prompt:
  Run directory: <RUN_DIR>
  All paths in your persona prompt are under the run directory.
  Your assignment is at <RUN_DIR>/assignments/tm-001.md.
  Read every <RUN_DIR>/recon/*.md, <RUN_DIR>/calibration.md, <RUN_DIR>/targets.md.
  Build the threat model and the hunt-priority queue.
  Write to <RUN_DIR>/threat-model.md.
  Return only: { threat_model_path, n_assignments, highest_chain_severity, summary }.
```

Then `python3 "<SCRIPT_DIR>/progress.py" --run "<RUN_ID>"`.

---

## 3. Phase 3 — Work distribution

Read the **Hunt prioritization queue** in `threat-model.md`. For each row create `assignments/<prefix>-<NNN>.md` from the assignment template.

Prefixes: `inj-` sr-injection-hunter · `authnz-` sr-authnz-hunter · `crypto-` sr-crypto-hunter · `exec-` sr-codeexec-hunter · `web-` sr-web-hunter · `supply-` sr-supplychain-secrets-hunter · `biz-` sr-businesslogic-hunter · `cross-` sr-cross-repo-analyst.

Each assignment includes: hunter agent name; repo + region; the recon `### Files of highest priority` rows scoped to the region (priority 5 first); hypothesis seed (attacker persona + sink-set focus — in deep/exhaustive, a different seed per assignment of the same class); must-check items from the threat model; context budget from calibration.md.

| Depth | Split rule |
|---|---|
| `quick` | One assignment per class per repo; skip a class if recon shows no relevant code |
| `standard` | Same as `quick` |
| `deep` | Split when a region's priority-5+4 file count exceeds 20 — one assignment per top-level package |
| `exhaustive` | Split aggressively; also a "different-attacker-persona" pass on the same regions |

Append a plan.md row per assignment. Manager log: `distribution complete: N assignments across M hunter classes`.

---

## 4. Phase 4 — Deep hunts (parallel batches)

### 4a. Model plan

Hunters are the bulk of the token spend. The plan is keyed off `DEPTH` and recorded by init_run.py in `calibration.md` → "This run's effective config" → `Model plan`. Apply it by passing the Task tool's per-call `model` parameter, which overrides the agent's frontmatter default:

| Depth | Hunter dispatch `model` |
|---|---|
| `quick` | `sonnet` for every hunter |
| `standard` | `sonnet` for sr-injection-hunter, sr-web-hunter, sr-crypto-hunter, sr-supplychain-secrets-hunter; omit (agent default) for sr-authnz-hunter, sr-businesslogic-hunter, sr-codeexec-hunter, sr-cross-repo-analyst |
| `deep`, `exhaustive` | omit — agent default |

Why `standard` splits: source→sink classes are pattern-led and the strong verifier filters their false positives, while authz / business-logic / code-exec / cross-repo bugs are intent-vs-implementation reasoning where a weaker hunter misses them outright — a miss the verifier can't recover.

**Never** override the model for sr-threat-modeller, sr-verifier, sr-triage, or sr-chain-composer — they are the precision gates and keep their agent default at every depth. sr-recon and sr-report-compiler already default to `sonnet`.

### 4b. Dispatch loop

Batch size = `Parallel hunter batch` from calibration.md. Sort pending assignments by priority (5 first). While any are pending, dispatch the next batch in one message:

```
Task subagent_type=security-review:<hunter-name>, model=<from the model plan, or omit>, prompt:
  Run directory: <RUN_DIR>
  All paths in your persona prompt are under the run directory.
  Your assignment is at <RUN_DIR>/assignments/<id>.md.
  Read <RUN_DIR>/findings/SCHEMA.md, <RUN_DIR>/calibration.md, your assignment first.
  Read the relevant <RUN_DIR>/recon/<repo>.md and <RUN_DIR>/threat-model.md sections.
  Run the hypothesize-verify loop with the adversarial self-challenge before writing each candidate.
  Write candidate findings to <RUN_DIR>/findings/candidates/<assignment-id>-<n>.md.
  Append worklog to <RUN_DIR>/worklog/<agent>-<assignment-id>.md.
  Return only: { status, candidates_written, candidates_dropped_after_self_challenge, worklog_path, summary }.
```

Record the model actually used in the plan row's Notes (e.g. `model=sonnet`). After each batch: update rows, append `phase 4 batch K/N: X candidates, Y dropped after self-challenge` to the manager log, then:

```bash
python3 "<SCRIPT_DIR>/index_findings.py" --run "<RUN_ID>"
python3 "<SCRIPT_DIR>/progress.py" --run "<RUN_ID>"
```

### `partial` returns

Mark the row `partial` and append a successor row for a narrowed re-dispatch. `partial` blocks the phase transition until the successor reaches `done`; then set the original to `partial-superseded`.

### Failures

Mark the row `failed`, read the worklog tail (usual cause: ran out of context), narrow the scope, re-dispatch. Track retries in Notes (`retry 1/2`). After 2 failures, mark the row `skipped` with Notes `failed twice: <reason>` and log it — do not stall waiting for a decision. Skipped-after-failure rows are listed as coverage gaps in the report (§7).

---

## 5. Phase 5 — Verification (parallel batches)

For each `findings/candidates/*.md`, add a plan row and dispatch (same batch size; no model override):

```
Task subagent_type=security-review:sr-verifier, prompt:
  Run directory: <RUN_DIR>
  All paths in your persona prompt are under the run directory.
  Independently and adversarially verify the candidate at:
    <RUN_DIR>/findings/candidates/<id>.md
  Do NOT read the hunter's worklog — your value comes from independent reasoning.
  Read <RUN_DIR>/findings/SCHEMA.md and <RUN_DIR>/calibration.md.
  Confirm → write <RUN_DIR>/findings/SR-<YYYY>-<NNN>.md (next free SR-id by globbing existing files).
  Reject → move to <RUN_DIR>/findings/rejected/<id>.md with a Rejection notes section.
  Downgrade or defer per your persona prompt.
  Return: { decision, final_path, final_severity, final_confidence, summary }.
```

After every batch run index_findings.py and progress.py, and log running confirmed/rejected counts.

---

## 6. Phase 6 — Triage

```
Task subagent_type=security-review:sr-triage, prompt:
  Run directory: <RUN_DIR>
  Scripts dir: <SCRIPT_DIR>
  Run id: <RUN_ID>
  All paths in your persona prompt are under the run directory.
  Read <RUN_DIR>/findings/SR-*.md, <RUN_DIR>/calibration.md, <RUN_DIR>/findings/INDEX.md.
  Run <SCRIPT_DIR>/dedupe.py --run <RUN_ID>, then make merge/split decisions.
  Finalize CVSS v3.1 vectors and base scores; ensure severity matches band.
  Apply project-type calibration; record adjustments in each finding's Discovery notes.
  Re-validate with <SCRIPT_DIR>/validate_findings.py --run <RUN_ID> --strict.
  Regenerate <RUN_DIR>/findings/INDEX.md via <SCRIPT_DIR>/index_findings.py.
  Append a "## Triage decisions" section to <RUN_DIR>/triage-summary.md (dedupe.py already wrote
  "## Dedup suggestions" — do not overwrite).
  Return: { n_confirmed, n_duplicates, severity_dist, summary }.
```

Then:

```bash
python3 "<SCRIPT_DIR>/validate_findings.py" --run "<RUN_ID>" --strict
python3 "<SCRIPT_DIR>/progress.py" --run "<RUN_ID>"
```

If validation fails, re-dispatch sr-triage with the failures listed (counts toward the 2-retry limit). Don't start 6.5 until it passes; if it still fails after 2 retries, continue and list the failing files in the final result.

## 6.5 Phase 6.5 — Chain composition

Skip if there are fewer than 2 confirmed findings (log it).

```
Task subagent_type=security-review:sr-chain-composer, prompt:
  Run directory: <RUN_DIR>
  All paths in your persona prompt are under the run directory.
  Read <RUN_DIR>/findings/SR-*.md, <RUN_DIR>/findings/INDEX.md,
       <RUN_DIR>/threat-model.md, <RUN_DIR>/calibration.md.
  Build the primitive inventory; explore compositions; validate chains.
  Mint <RUN_DIR>/findings/SR-<YYYY>-<NNN>-CHAIN.md per validated chain (chain_constituents must
  list the SR-IDs of the confirmed constituents).
  Write <RUN_DIR>/chains.md summarizing the analysis.
  Return: { chains_minted, chains_explored, chains_md_path, summary }.
```

Regenerate the index after.

---

## 7. Phase 7 — Report and final result

```
Task subagent_type=security-review:sr-report-compiler, prompt:
  Run directory: <RUN_DIR>
  Scripts dir: <SCRIPT_DIR>
  Run id: <RUN_ID>
  All paths in your persona prompt are under the run directory.
  Run: python3 <SCRIPT_DIR>/compile_report.py --run <RUN_ID>
  Fill the narrative placeholders (executive summary, methodology, risk acceptance,
  suppression counts, project-specific coverage gaps) per your persona prompt.
  The methodology paragraph must state the project type and whether it was given or inferred
  (quote the "Project type inference" section of calibration.md if present), the depth, and the
  model plan. List plan.md rows marked `skipped` with `failed twice` as coverage gaps.
  Return: { report_path, n_findings_reported, n_chains_reported, headline, summary }.
```

Mark remaining plan.md rows `done`, run progress.py a final time, then return this as your **only** final message:

```
### Security Review complete — run `<RUN_ID>`
- Project type: `<type>` (<given | inferred: TYPE_NOTE>) · Depth: `<DEPTH>` · Model plan: <one line>
- Confirmed findings: N (Critical K, High K, Medium K, Low K, Info K)
- Composed chains: N
- Most urgent: `<SR-id>` — <one line>
- Headline: <compiler's headline>
- Report: `<RUN_DIR>/report.md` · Triage summary: `<RUN_DIR>/triage-summary.md`
- Skipped / degraded: <rows skipped after failures, validation failures, or "none">

Manager log:
<contents of <RUN_DIR>/worklog/manager.md, or its last 40 lines if longer>
```

---

## 8. Rules

- **Never cross a phase boundary while any non-skipped row is `pending` / `running` / `failed` / `partial`.** Wait, re-dispatch, or (after 2 failures) skip with a logged reason.
- **Never auto-rerun more than 2× per assignment.**
- **Bash is read-only against the targets.** No exploit execution, ever, even to "verify".
- **No fixed scratch paths.** Everything the run writes lives under `$RUN_DIR/`; never a shared system temp directory, so concurrent runs can't collide.

---

## 9. Resume an interrupted run

1. `RUN_ID=<RESUME_ID>`, `RUN_DIR=<abs cwd>/.security-review/<RUN_ID>`. If it doesn't exist, return an error listing `ls .security-review/`.
2. Read `PROJECT_TYPE` and `DEPTH` from `$RUN_DIR/calibration.md` (the latest "Calibration change" section wins) — ignore any `type=`/`depth=` passed alongside `resume:`, and log that you did.
3. Audit:
   ```bash
   python3 "<SCRIPT_DIR>/replay.py" --run "<RUN_ID>" --check-consistency
   ```
4. For every plan.md row in `pending`, `running`, `partial` or `failed`, re-dispatch with the original phase's logic (reuse existing assignment files; apply the model plan from calibration.md). Before re-dispatching a hunter row, clear its stale candidates:
   ```bash
   rm -f "<RUN_DIR>/findings/candidates/<assignment-id>-"*.md
   ```
   Safe: candidates are named from the assignment id, and anything already promoted to `findings/SR-*.md` is untouched. Verifier, triage, chain-composer and report-compiler rows are idempotent.
5. Log `resumed run <RUN_ID>: N tasks re-dispatched` and continue through phases 4–7.

---

## Cheat sheet

| Phase | You do | Sync point |
|---|---|---|
| 1 | Resolve type (infer if needed), init_run.py | After init |
| 2 | sr-recon × repos (parallel), then sr-threat-modeller × 1 | All recon + threat model done |
| 3 | Write assignments + plan rows | No Tasks dispatched |
| 4 | Hunters in batches, model per §4a | All hunter rows done/skipped/partial-superseded |
| 5 | sr-verifier × candidates | All verifier rows done |
| 6 | sr-triage × 1 | validate_findings --strict passes |
| 6.5 | sr-chain-composer × 1 | Return |
| 7 | sr-report-compiler × 1, final result | report.md written |

After every Task return: update its plan.md row, regenerate index + progress, append the headline to the manager log.
