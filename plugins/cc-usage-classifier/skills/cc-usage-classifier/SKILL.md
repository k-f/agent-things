---
name: cc-usage-classifier
description: >-
  Classify and cost local Claude Code sessions from ~/.claude/projects
  transcripts: per-model token/cost accounting, activity tags, outcome
  verdicts, Jira/PR identifiers, repo/branch and feature usage, rolled up into
  a summary report with model-choice ROI insights. Spawns Haiku subagents for
  classification. Local data only — no OpenTelemetry required.
context: fork
disable-model-invocation: true
argument-hint: "[--last-days N | --since YYYY-MM-DD --until YYYY-MM-DD] [--projects-dir DIR] [--jira-projects ABC,DEF] [--redact-prompts] [--no-incremental] [--out-dir DIR]"
allowed-tools: Bash, Read, Write, Edit, Agent, Task
---

# Claude Code Usage Classifier

You produce a per-session dataset that combines deterministic **per-model
token/cost accounting** with a model-judged **classification** of each session
(activity tags, summary, outcome). The deterministic core is Python; you only
spawn Haiku subagents for the judgement calls (tags, summary, outcome verdict).

**Granularity is the session. Cost is per session, per model — never collapsed
to one model and never apportioned across stages within a session.**

You run as a forked subagent: you cannot ask the user anything mid-run, and
your final message is the only thing they see. Never stop to ask or wait for
confirmation — use the defaults below, proceed, and list every assumption you
made in the final result.

Paths: scripts live in `${CLAUDE_SKILL_DIR}/scripts/`. Shell variables do not
persist between Bash calls, so wherever this file says `<OUT_DIR>`, write the
literal `out_dir` value printed by Step 1, always inside double quotes.

---

## Step 1 — Extract (deterministic)

User arguments must never be interpreted by the shell. Hand them to the
extractor through a file, which it splits with `shlex` and validates against a
flag allowlist (unknown flags, positionals, bad dates/numbers, and paths with
shell-special characters are rejected with exit code 2).

1. Run `mkdir -p "$HOME/.claude/cc-usage-classifier" && echo "$HOME"`.
2. With the `Write` tool, write `<HOME>/.claude/cc-usage-classifier/args-${CLAUDE_SESSION_ID}.txt`
   containing the user's arguments, which are (verbatim, between the fences):

   ```
   $ARGUMENTS
   ```

   If they are already flags, copy them unchanged. If they are plain language
   ("last 30 days", "since May 1", "only ABC tickets"), write the equivalent
   flags instead. If empty, write an empty file. Allowed flags:

   | Flag | Value |
   |---|---|
   | `--last-days N` | number of days back from now (UTC) |
   | `--since D` / `--until D` | `YYYY-MM-DD` or ISO 8601; date-only `--until` includes the whole day; `--since` wins over `--last-days` |
   | `--projects-dir P` | transcripts root (default `~/.claude/projects`) |
   | `--out-dir P` | output dir (default `~/.claude/cc-usage-classifier/out`) |
   | `--jira-projects A,B` | Jira project-key allowlist |
   | `--redact-prompts` | store no prompt/assistant/bash text |
   | `--no-incremental` | reclassify everything |

3. Run:

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/extract.py" --args-file "$HOME/.claude/cc-usage-classifier/args-${CLAUDE_SESSION_ID}.txt"
   ```

If it exits 2, the arguments were rejected: return the error and the flag
table above as your result and stop (do not fall back to an unscoped run — a
wrong range misreports and spends classification calls).

The extractor reads every transcript, deduplicates usage per `requestId` per
model, splits out long-context requests for tiered pricing, computes
repo/branch/features/identifiers/outcome signals, and prints JSON:
`{ "todo": [...], "cached": [...], "total": N, "out_dir": "...",
"batches": [...], "classifications_dir": "...", "range": {since, until,
skipped_out_of_range} }`. Warnings also go to `<OUT_DIR>/logs/extract.log`.

- `todo` = new or changed sessions → need (re)classification. Any stale
  classification file for them has already been deleted.
- `cached` = unchanged sessions → **do not reclassify** (incremental).
- `batches` = `<OUT_DIR>/batches/batch-NNN.json` files, each a JSON array of
  at most 20 todo sessions' classifier inputs (and ~150 KB).

A session is in range when its activity window overlaps the range. Filtering
happens here, so every downstream output is scoped to the window. If `total`
is 0, return that no transcripts were found (and that `--projects-dir` can
point elsewhere) and stop.

---

## Step 2 — Check pricing coverage

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/cost.py" --in "<OUT_DIR>/extracted.jsonl" --check-unpriced
```

This lists model ids in the data with no rate in
`${CLAUDE_SKILL_DIR}/pricing.json` and their share of all tokens. Do not
invent rates and do not stop: unpriced models are left at $0.00, `report.py`
puts a warning banner at the top of `summary.md` when they exceed 5% of
tokens, and you repeat that warning in your final result. Matching is
version-specific (exact id → date-stripped id → a `claude-<family>-<major>-x`
key only if one is defined); tiers differ within a major version, so never
price one version as another.

---

## Step 3 — Classify todo sessions in batches (Haiku subagents)

Skip this step if `todo` is empty. Never classify `cached` sessions.

For each path in `batches`, spawn one subagent with the `Agent` tool
(`subagent_type: general-purpose`, `model: haiku`). Send **4–6 Agent calls per
message** in parallel, wait for them, then send the next group. Prompt
template (fill in the three paths literally):

> You classify Claude Code sessions. Work only from the files named here.
>
> 1. Read the taxonomy: `${CLAUDE_SKILL_DIR}/references/taxonomy.md`.
> 2. Read the batch file `<BATCH_PATH>`: a JSON array of sessions, each with
>    `session_id`, `classifier_payload`, `outcome_signals`,
>    `identifier_candidates`, `features`, `repo`, `branch`. If the file is
>    longer than one Read returns, keep reading with `offset` until the end.
> 3. For **each** session, use the Write tool to create
>    `<OUT_DIR>/classifications/<session_id>.json` containing only this JSON
>    object (no prose, no code fences):
>    `{"session_id": "...", "tags": ["<one or more taxonomy tags>"],
>    "summary": "<1–2 sentences on what was requested>",
>    "outcome": "successful|partial|abandoned|unknown",
>    "outcome_confidence": 0.0,
>    "outcome_justification": "<one line citing the specific signals used>",
>    "identifiers": [{"type": "jira|github_pr", "value": "...", "confidence": "..."}]}`
>
> Rules: tags MUST be taxonomy tags; emit several when the session spans
> several activities — do not force one. Anchor `successful` on hard signals
> (`commit_created`, `tests_passed`, `pr_created`); prefer `unknown` over
> guessing. Keep only identifiers present in `identifier_candidates`; drop
> false positives. `outcome_confidence` is a number in [0, 1].
>
> Reply with one line: `wrote N of M` plus any session_ids you could not
> classify. Do not repeat the classifications in your reply. If the Write tool
> is denied, instead reply with one classification JSON object per line
> (JSONL, each with its `session_id`) and nothing else.

If a subagent returns JSONL instead of writing files, write each line to
`<OUT_DIR>/classifications/<session_id>.json` yourself.

When all batches are done, validate:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/validate.py" --out-dir "<OUT_DIR>" --write-retry-batch
```

It checks every batched session's file against the schema and the taxonomy
tags and prints `{expected, ok, missing, malformed, retry_batches}`. If
`retry_batches` is non-empty, run **one** retry round: one subagent per retry
file, same prompt. Then run it once more with `--quarantine` (instead of
`--write-retry-batch`): files still malformed move to
`classifications/invalid/`, and `report.py` gives those and any still-missing
sessions the deterministic fallback (tags `[]`, outcome from hard signals).
Count them for the final result.

---

## Step 4 — Merge, price, and roll up

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/report.py" --out-dir "<OUT_DIR>" --pricing "${CLAUDE_SKILL_DIR}/pricing.json"
```

This merges each session's classification (fresh subagent JSON → cache →
deterministic fallback), applies **per-model** pricing (including the
long-context tier where a rate card defines one), and writes:

- `<OUT_DIR>/sessions.jsonl` — full enriched record incl. per-model token/cost.
- `<OUT_DIR>/sessions.csv` — flattened; tags as a `|`-delimited list.
- `<OUT_DIR>/summary.md` — unpriced-model banner (if any), roll-ups (grand
  totals deduped; per-tag **non-additive**; by model / outcome / repo / day /
  week; feature adoption).

Warnings (`WARNING: unpriced model`, long-context tier not applied) go to
stderr and `<OUT_DIR>/logs/report.log`; carry them into the final result.

`report.py` leaves an empty **`## Insights & assessment`** section at the end of
`summary.md`, bounded by `<!-- INSIGHTS:START -->` / `<!-- INSIGHTS:END -->`.
You fill it in next.

---

## Step 5 — Write data-driven insights into the report

`Read` `<OUT_DIR>/sessions.csv` (one row per session: `tags`, `outcome`,
`outcome_confidence`, `models_used`, `session_cost_usd`, `repo`, `skills`,
`subagents_used`, `day`, …), the tables already in `<OUT_DIR>/summary.md`, and
`${CLAUDE_SKILL_DIR}/pricing.json`. Synthesise concise insights and **replace
the text between the `<!-- INSIGHTS:START -->` and `<!-- INSIGHTS:END -->`
markers** in `summary.md` (use `Edit`; keep the markers).

Write 4 short subsections. **Every claim must cite a number from the data**
(session counts, $ amounts, percentages, model ids) — no generic advice.

1. **What work is being done** — the dominant activity tags and how they relate
   (e.g. "debugging + testing co-occur in N sessions"); which repos/tags
   concentrate spend. Use the by-tag table but restate it as prose, and respect
   that tags are **non-additive**.
2. **Models being used** — the per-model token/cost split; which model carries
   most spend; subagent vs main-agent usage if present.
3. **Value / ROI assessment, factoring in model choice** — this is the point.
   Judge whether the model tier fits the work and outcome. Rank tiers by the
   actual per-MTok rates in `pricing.json` for the models present (the
   `priced_as` key in each `cost_by_model` entry) — never quote rates from
   memory. Families run Fable/Mythos → Opus → Sonnet → Haiku, but rates move
   between versions (a newer Opus can be cheaper than an older one), so
   compare the numbers. Look for:
   - **Over-powered work**: top-rate sessions spent on low-complexity tags
     (`documentation`, `analysis/design`, `research`, simple Q&A) that a
     cheaper priced model likely handles — quantify the $ at stake by
     re-pricing those tokens at the cheaper model's rates, and name it.
   - **Low-return spend**: sessions with high cost but `abandoned`/`partial`
     outcomes, or expensive sessions with low `outcome_confidence` — call out
     the wasted $.
   - **Good fit / high return**: expensive models on hard work
     (`debugging`, `implementation`, `refactor`) that ended `successful`, or
     cheap models doing a lot — affirm these.
   - **Appropriately matched**: note where tier and work align so the user
     isn't told to change something that's fine.
   Frame ROI as *value delivered relative to model cost*, and be **calibrated**:
   a one-off $0.50 session is not worth optimising; a recurring pattern of
   $X across N sessions is. Give **specific, actionable** recommendations
   (e.g. "route doc-only sessions to <cheaper model> — would have saved ~$Y
   across N sessions") and flag where the data is too thin to conclude.
4. **Caveats** — pricing assumptions (list prices from `pricing.json`; fast
   mode, batch and data-residency pricing not modelled), the heuristic nature
   of outcome/agent-team signals, classifier fallbacks, and any unpriced
   models with their token share, so the assessment isn't over-trusted.

Keep the whole section tight (roughly 150–350 words). Do not alter the
deterministic tables above the Insights section.

---

## Step 6 — Final result

Your final message is the deliverable. `Read` `<OUT_DIR>/summary.md` and
return:

- Any **unpriced-model warning** first (models + token share), if present.
- Scope and assumptions: the flags used (or "all sessions, default paths"),
  the `range` from Step 1 incl. sessions skipped out of range, how
  plain-language arguments were translated, and how many sessions got the
  deterministic fallback.
- Grand totals (sessions, tokens, cost) — note these are **deduplicated**.
- The **by-model** cost split.
- The **by-tag** table, explicitly noting it is **non-additive** (multi-tag
  sessions are counted under each tag, so the column intentionally exceeds the
  grand total — it is not a partition).
- Outcome distribution and feature-adoption shares.
- The **Insights & assessment** from Step 5 — especially the model-choice ROI
  findings with the specific dollar figures and any recommended routing.
- Paths to `summary.md`, `sessions.jsonl` / `sessions.csv`, and the two tuning
  knobs: `pricing.json` and `references/taxonomy.md`.

---

## Invariants — do not violate

- **Local only.** Everything runs on-machine; subagents see only the bounded
  batch payloads, never raw transcripts or file contents.
- **No shell interpretation of user text.** Arguments reach `extract.py` only
  via `--args-file`; never paste them into a command line.
- **Per-model accounting.** Token totals and cost are aggregated per model id
  then summed. A model with no price is warned about, never priced as another.
- **Deduplicate by requestId** before summing usage (the extractor does this;
  trust it). Cache token fields are reliable; input/output are the ones that
  corrupt under naive summation.
- **Incremental.** Never reclassify a `cached` session. Re-runs must re-use the
  cache and only spend Haiku calls on changed/new sessions.
- **Model only for judgement.** Tags, summary, and the outcome verdict come
  from the subagents. Everything else (parsing, dedup, cost maths, repo/branch,
  identifier regex, feature detection, outcome *signals*) is the deterministic
  Python — do not recompute it yourself.
- **Multi-tag must not inflate the grand total.** Grand totals count each
  session once; only the per-tag table is non-additive.

## Notes

- `--redact-prompts` drops all prompt/assistant/bash text from the payload and
  outputs (counts and tool summaries remain) — use it when prompt text must not
  be stored.
- `references/taxonomy.md` is the classification rubric; edit it to retune tags
  and outcome definitions without code changes (`validate.py` reads the tag
  list from its `## Tags` section).
- `pricing.json` rate cards may carry a `long_context` block
  (`threshold_tokens` + rates): requests whose prompt (input + cache read +
  cache write) exceeds the threshold are billed at those rates.
- Tests: `python3 -m unittest discover -s "${CLAUDE_PLUGIN_ROOT}/tests" -v`.
- `ccusage` is **not** used by this skill at runtime; it was only a
  development-time check that the per-model token aggregation reconciles.
