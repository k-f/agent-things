---
name: cc-usage-classifier
description: >-
  Analyse, classify, and cost the user's local Claude Code usage from
  ~/.claude/projects transcripts — one enriched record per session. USE THIS
  whenever the user wants to understand what work they've been doing in Claude
  Code or what it cost, e.g. "analyse my Claude Code usage", "classify my
  sessions", "what work have I been doing in Claude Code", "cost by type of
  work", "how much am I spending in Claude Code", "break down my Claude Code
  sessions", "which of my sessions were debugging vs implementation". Produces
  per-model token/cost accounting plus activity tags, a summary, an outcome
  verdict, extracted Jira/PR identifiers, repo/branch, and feature-usage flags.
  Local data only — no OpenTelemetry required.
context: fork
argument-hint: "[--projects-dir DIR] [--jira-projects ABC,DEF] [--redact-prompts] [--full]"
allowed-tools: Bash, Read, Write, Edit, Task
---

# Claude Code Usage Classifier

You produce a per-session dataset that combines deterministic **per-model
token/cost accounting** with a model-judged **classification** of each session
(activity tags, summary, outcome). The deterministic core is Python; you only
spawn Haiku subagents for the judgement calls (tags, summary, outcome verdict).

**Granularity is the session. Cost is per session, per model — never collapsed
to one model and never apportioned across stages within a session.**

`$SKILL_DIR` below = the directory containing this SKILL.md. Resolve it once:

```bash
SKILL_DIR="$(dirname "$(find ~ /root -path '*/cc-usage-classifier/SKILL.md' 2>/dev/null | head -1)")"
# Fallback if the skill is installed elsewhere; otherwise set it from the path
# Claude Code reported when loading this skill.
```

If that lookup is empty, use the absolute path of the folder this file lives in.

---

## Step 0 — Confirm pricing is filled in

The cost numbers are only meaningful if `pricing.json` has real rates.

1. `Read` `$SKILL_DIR/pricing.json`.
2. If every rate is still `0.0`, tell the user costs will be **$0.00** until
   they fill in `pricing.json` (USD per million tokens, per model id), and ask
   whether to proceed anyway or pause so they can edit it. Do **not** invent
   rates. If they want public list prices, offer to fill them but get
   confirmation of the exact figures first.
3. Note any model ids in their data that aren't priced (the engine matches
   version-specific keys first — exact id, then date-stripped version, then a
   coarse `claude-<family>-<major>-x` fallback). Versions matter: e.g. Opus
   4.5–4.8 are a different tier from Opus 4.0/4.1, so don't price by family.

---

## Step 1 — Extract (deterministic)

Run the extractor. It reads every transcript, deduplicates usage per
`requestId` per model, computes repo/branch/features/identifiers/outcome
signals, and writes bounded classifier payloads.

```bash
python3 "$SKILL_DIR/scripts/extract.py" \
  ${ARGUMENTS:-} \
  2>/tmp/cc_extract.err
```

Pass through any user arguments: `--projects-dir`, `--jira-projects`,
`--redact-prompts`, `--no-incremental`. Default output dir is
`~/.claude/cc-usage-classifier/out`.

The command prints JSON: `{ "todo": [...], "cached": [...], "total": N,
"out_dir": "...", "extracted": "..." }`.

- `todo` = sessions that are new or changed → need (re)classification.
- `cached` = unchanged sessions → **do not reclassify** (incremental).

Capture `out_dir` and the `todo` list. If `total` is 0, tell the user no
transcripts were found (and that `--projects-dir` can point elsewhere) and stop.

---

## Step 2 — Classify each todo session with a Haiku subagent

For **every session_id in `todo`**, spawn a Haiku subagent. Run them in
**parallel batches** (up to ~6 Task calls per message) for throughput. Skip
sessions in `cached` entirely.

For each session, first pull its record from `extracted.jsonl`:

```bash
python3 - "$OUT_DIR/extracted.jsonl" "$SESSION_ID" <<'PY'
import json,sys
path,sid=sys.argv[1],sys.argv[2]
for line in open(path):
    r=json.loads(line)
    if r["session_id"]==sid:
        print(json.dumps({
            "session_id": r["session_id"],
            "classifier_payload": r["classifier_payload"],
            "outcome_signals": r["outcome_signals"],
            "identifier_candidates": r["identifier_candidates"],
            "features": r["features"],
            "repo": r["repo"], "branch": r["branch"],
        }, ensure_ascii=False))
        break
PY
```

Then spawn the subagent with the `Task` tool, **pinned to a Haiku model**
(`model: claude-haiku-4-5`), passing it the JSON above **and** the taxonomy.
Read `$SKILL_DIR/references/taxonomy.md` once and include its content in each
prompt (or instruct the subagent to read that exact path).

Subagent prompt template:

> You are a strict JSON classifier for one Claude Code session. Apply the
> taxonomy below. Return **only** a single JSON object, no prose, no code
> fences.
>
> Taxonomy:
> ```
> <contents of references/taxonomy.md>
> ```
>
> Session data:
> ```json
> <the per-session JSON from above>
> ```
>
> Output exactly this shape:
> ```json
> {
>   "tags": ["<one or more taxonomy tags>"],
>   "summary": "<1–2 sentences on what was requested>",
>   "outcome": "successful|partial|abandoned|unknown",
>   "outcome_confidence": 0.0,
>   "outcome_justification": "<one line citing the specific signals used>",
>   "identifiers": [ {"type":"jira|github_pr","value":"...","confidence":"..."} ]
> }
> ```
> Rules: tags MUST come from the taxonomy; emit multiple when the session spans
> multiple activities — do not force one. Anchor `successful` on hard signals
> (commit_created, tests_passed, pr_created); prefer `unknown` over guessing.
> Only keep identifiers present in identifier_candidates; drop false positives.

When a subagent returns, **write its JSON verbatim** to
`$OUT_DIR/classifications/<session_id>.json` (create the dir if needed). If a
subagent returns malformed JSON, retry once; if it still fails, write a
minimal `{"tags":[],"outcome":"unknown","outcome_confidence":0.2,
"summary":"","outcome_justification":"classifier failed"}` so the pipeline
still completes, and note it.

Do not classify `cached` sessions — their prior classification is reused
automatically by Step 3.

---

## Step 3 — Merge, price, and roll up

```bash
python3 "$SKILL_DIR/scripts/report.py" --out-dir "$OUT_DIR" \
  --pricing "$SKILL_DIR/pricing.json" 2>/tmp/cc_report.err
```

This merges each session's classification (fresh subagent JSON → cache →
deterministic fallback), applies **per-model** pricing, and writes:

- `$OUT_DIR/sessions.jsonl` — full enriched record incl. per-model token/cost.
- `$OUT_DIR/sessions.csv` — flattened; tags as a `|`-delimited list.
- `$OUT_DIR/summary.md` — roll-ups (grand totals deduped; per-tag **non-additive**;
  by model / outcome / repo / day / week; feature adoption).

Check `/tmp/cc_report.err` for `WARNING: unpriced model` lines and surface them.

---

## Step 4 — Report back

`Read` `$OUT_DIR/summary.md` and present the highlights to the user:

- Grand totals (sessions, tokens, cost) — note these are **deduplicated**.
- The **by-model** cost split (e.g. Haiku-subagent spend vs Sonnet/Opus main).
- The **by-tag** table, explicitly reminding the user it is **non-additive**
  (multi-tag sessions are counted under each tag, so the columns intentionally
  exceed the grand total — it is not a partition).
- Outcome distribution and feature-adoption shares.
- Point them at `sessions.jsonl` / `sessions.csv` for the full data, and at
  `pricing.json` / `references/taxonomy.md` as the two tuning knobs.

---

## Invariants — do not violate

- **Local only.** Everything runs on-machine; subagents see only the bounded
  payloads, never raw transcripts or file contents.
- **Per-model accounting.** Token totals and cost are aggregated per model id
  then summed. A model with no price is warned about, never priced as another.
- **Deduplicate by requestId** before summing usage (the extractor does this;
  trust it). Cache token fields are reliable; input/output are the ones that
  corrupt under naive summation.
- **Incremental.** Never reclassify a `cached` session. Re-runs must re-use the
  cache and only spend Haiku calls on changed/new sessions.
- **Model only for judgement.** Tags, summary, and the outcome verdict come
  from the subagent. Everything else (parsing, dedup, cost maths, repo/branch,
  identifier regex, feature detection, outcome *signals*) is the deterministic
  Python — do not recompute it yourself.
- **Multi-tag must not inflate the grand total.** Grand totals count each
  session once; only the per-tag table is non-additive.

## Notes

- `--redact-prompts` drops all prompt/assistant/bash text from the payload and
  outputs (counts and tool summaries remain) — use it when prompt text must not
  be stored.
- `references/taxonomy.md` is the classification rubric; edit it to retune tags
  and outcome definitions without code changes.
- `ccusage` is **not** used by this skill at runtime; it was only a
  development-time check that the per-model token aggregation reconciles.
