---
name: review-diff
description: PR / diff-scoped security review. Mirrors the public claude-code-security-review GitHub Action behaviour. Reviews only files changed in a git diff range (default - merge-base with the repo's default branch to HEAD), with enough surrounding context for dataflow analysis. Fast (minutes). Suitable for pre-commit / CI use.
context: fork
agent: general-purpose
disable-model-invocation: true
argument-hint: "[base-ref] [head-ref] [type=poc|internal|production|regulated|safety-critical]"
allowed-tools: Bash, Read, Glob, Grep, Write, Edit, Task
---

# Security Review — diff-scoped

Reviews only what changed between a base and a head ref. Closest analogue of the public claude-code-security-review GitHub Action — keep its mantra: "better to miss some theoretical issues than flood the report with false positives."

You run as a forked subagent: you can't pose questions mid-run — nobody is there to answer. Shell state doesn't persist between Bash calls — use literal values. Dispatch plugin agents by their namespaced name: `subagent_type: security-review:sr-<agent>`.

- `SCRIPT_DIR` = `${CLAUDE_PLUGIN_ROOT}/scripts`
- Raw arguments: `$ARGUMENTS`

## Procedure

1. **Parse arguments** (order-independent for `key=value`): `type=<t>` → `PROJECT_TYPE` (default `infer`); first bare token → `BASE_ARG`, second → `HEAD_ARG` (default `HEAD`). Anything else → return an error naming the token.

2. **Confirm a git repo**: `git rev-parse --show-toplevel` (fail → return "not a git repository"). Run the remaining git commands from that top level.

3. **Resolve the range.** Positional args override; otherwise pick the default branch's merge-base:
   ```bash
   HEAD_REF="${HEAD_ARG:-HEAD}"
   git rev-parse --verify -q "$HEAD_REF^{commit}" >/dev/null || { echo "bad head ref: $HEAD_REF"; exit 1; }
   BASE_REF="<BASE_ARG if given>"
   if [ -z "$BASE_REF" ]; then
     if [ -n "${GITHUB_BASE_REF:-}" ]; then BASE_REF="origin/$GITHUB_BASE_REF"; fi        # GitHub Actions PR
     [ -z "$BASE_REF" ] && BASE_REF=$(git symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null)
     for c in main master origin/main origin/master; do
       [ -n "$BASE_REF" ] && break
       git rev-parse --verify -q "$c^{commit}" >/dev/null && BASE_REF="$c"
     done
     [ -z "$BASE_REF" ] && BASE_REF=$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null)
   fi
   if [ -n "$BASE_REF" ]; then
     BASE=$(git merge-base "$HEAD_REF" "$BASE_REF") || { echo "no merge-base between $HEAD_REF and $BASE_REF (shallow clone? fetch more history or pass a base ref)"; exit 1; }
   elif git rev-parse --verify -q "$HEAD_REF~1" >/dev/null; then
     BASE=$(git rev-parse "$HEAD_REF~1"); BASE_REF="$HEAD_REF~1 (fallback: no default branch or upstream found)"
   else
     echo "cannot determine a base: no default branch, no upstream, single-commit history — pass a base ref"; exit 1
   fi
   echo "BASE_REF=$BASE_REF BASE=$BASE HEAD_REF=$HEAD_REF"
   ```
   Why the default branch before `@{upstream}`: a pushed feature branch's upstream is usually its own remote copy, whose merge-base would hide every already-pushed commit of the PR. Works in detached HEAD (CI checkouts) because nothing depends on the current branch name. If a `BASE_REF` exists but is equal to `HEAD` (e.g. you're on `main`), the diff is empty — that's reported, not an error. Uncommitted changes are not included; say so in the result if `git status --porcelain` is non-empty.

4. **Bail early if nothing changed** (nothing is written yet):
   ```bash
   git diff --name-only "$BASE" "$HEAD_REF" | grep -v -E '(^|/)(node_modules|vendor|dist|build)/' | wc -l
   ```
   Zero → return "No reviewable changes between `<BASE_REF>` and `<HEAD_REF>`." and stop.

5. **Project type.** If `infer`, apply the inference block in `${CLAUDE_PLUGIN_ROOT}/references/procedure.md` §1a to the repo top level (no questions; record the one-sentence rationale).

6. **Init the run, then write the diff into it** (per-run paths — concurrent reviews never collide):
   ```bash
   RUN_ID=$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/init_run.py" --targets . --project-type "<PROJECT_TYPE>" --depth quick)
   RUN_DIR="$(pwd)/.security-review/$RUN_ID"; mkdir -p "$RUN_DIR/diff"
   git diff --name-only "<BASE>" "<HEAD_REF>" | grep -v -E '(^|/)(node_modules|vendor|dist|build)/' > "$RUN_DIR/diff/changed-files.txt"
   git diff -U10 "<BASE>" "<HEAD_REF>" > "$RUN_DIR/diff/diff.patch"
   printf 'base_ref: %s\nbase: %s\nhead_ref: %s\nhead: %s\n' "<BASE_REF>" "<BASE>" "<HEAD_REF>" "$(git rev-parse <HEAD_REF>)" > "$RUN_DIR/diff/range.txt"
   ```
   If the type was inferred, append the "Project type inference" section to `calibration.md` as in procedure §1b. Log every decision (range, type, model plan) to `$RUN_DIR/worklog/manager.md`.

7. **Synthesize recon** at `$RUN_DIR/recon/<repo>.md`: every changed file at priority 5; files that import / are imported by changed files at priority 3.

8. **Write one assignment per relevant hunter class** (class selection as in `review-file`, applied across the changed-file set). Each includes:
   - `$RUN_DIR/diff/changed-files.txt` and `$RUN_DIR/diff/diff.patch`
   - Instruction: "Focus on the changed lines and the dataflow paths that touch them. In scope: (a) flaws introduced by the diff; (b) **pre-existing flaws the diff newly makes reachable, exploitable, or more severe** — a new route/caller into an old sink, a removed or weakened guard/validator, widened input (new parameter, relaxed type/schema, new deserialization path), or changed auth/permission/trust logic. Attribute (b) to the changed lines that enable it and cite both the diff hunk and the old sink. Out of scope: flaws whose root cause is outside the diff and whose reachability/severity the diff does not change — note those in your worklog only."

9. **Dispatch hunters** in parallel (batch per calibration.md). Depth is `quick`, so per the model plan in calibration.md pass `model: sonnet` on each hunter Task.

10. **Verify** each candidate with `sr-verifier` (no model override). Add to the dispatch prompt: "Diff-scoped run: the diff is at `<RUN_DIR>/diff/diff.patch`. A candidate whose sink predates the diff is valid if the diff newly makes it reachable, exploitable, or more severe — confirm that causal link from the hunk; reject only if the flaw is equally reachable and severe without the diff."

11. **Skip `sr-chain-composer`** (chains usually need context outside the diff).

12. **Triage** with `sr-triage` (dispatch as in procedure §6, plus the same diff-scope sentence as step 10, and: "for a type-(b) finding, Discovery notes must name the enabling hunk").

13. **Report** with `sr-report-compiler` (procedure §7 dispatch) — a mini report; the methodology paragraph states the range (`range.txt`) and the scope rule.

## Output

Return the report inline, followed by the manager-log lines. The full report is at `.security-review/<run-id>/report.md`; per-finding files at `.security-review/<run-id>/findings/SR-*.md` for tooling that parses them.

## Pre-existing-issue rule

- **Introduced by the diff** → in scope.
- **Pre-existing, but the diff newly makes it reachable, exploitable, or more severe** (new route to an old sink, removed guard, widened input, changed auth) → **in scope; must be reported**, attributed to the diff's change. The author's change is what turned a latent flaw into a live one.
- **Pre-existing and unaffected by the diff** → drop from the report. The PR author isn't responsible for it. Record the drop in the worklog so a later `/security-review:security-review` run surfaces it.
