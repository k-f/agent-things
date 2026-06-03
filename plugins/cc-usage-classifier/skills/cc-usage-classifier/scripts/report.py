#!/usr/bin/env python3
"""
cc-usage-classifier — merge + cost + roll-up reporter (Component 8).

Pipeline position: runs AFTER extract.py and AFTER the Haiku subagents have
written their per-session classification JSON files.

  1. Read extracted.jsonl (deterministic records).
  2. Merge each session's classification, in priority order:
       a. classifications/<session_id>.json written this run by a subagent
       b. cached classification in cache.json (unchanged sessions)
       c. deterministic fallback (tags=[], outcome from hard signals/unknown)
  3. Apply per-model pricing (cost.py).
  4. Write sessions.jsonl, sessions.csv, summary.md.
  5. Persist merged classifications back into cache.json (keyed by hash) so
     re-runs are incremental and re-classify nothing unchanged.

Usage:
  python3 report.py [--out-dir DIR] [--pricing pricing.json]
"""

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cost import load_pricing, price_record  # noqa: E402

DEFAULT_OUT_DIR = Path.home() / ".claude" / "cc-usage-classifier" / "out"
DEFAULT_PRICING = Path(__file__).resolve().parent.parent / "pricing.json"

VALID_OUTCOMES = {"successful", "partial", "abandoned", "unknown"}
TOKEN_TYPES = ["input", "output", "cache_read",
               "cache_write_5m", "cache_write_1h", "cache_write"]

# Canonical tag vocabulary (top-level + sub-tags). "analysis/design" legitimately
# contains a slash, so compound expansion below must preserve it.
CANON_TAGS = {
    "engineering", "testing", "business", "personal-productivity",
    "analysis/design", "implementation", "debugging", "refactor", "review",
    "devops", "data", "documentation", "research",
}


def normalize_tags(tags):
    """Map a subagent's tags onto the canonical vocabulary.

    Handles chatty variants like "engineering/implementation" (expands to
    "engineering" + "implementation") while preserving "analysis/design".
    Unknown tags are kept as-is so nothing is silently dropped.
    """
    out = []
    for raw in tags:
        t = str(raw).strip()
        if not t:
            continue
        if t in CANON_TAGS:
            out.append(t)
            continue
        # compound "top/sub" — expand each recognised part
        if "/" in t and t != "analysis/design":
            parts = [p.strip() for p in t.split("/") if p.strip()]
            if all(p in CANON_TAGS for p in parts):
                out.extend(parts)
                continue
        out.append(t)
    # de-dup, preserve order
    seen = set()
    return [t for t in out if not (t in seen or seen.add(t))]


def parse_classification_json(text):
    """Parse a subagent classification that may be wrapped in code fences."""
    text = text.strip()
    if text.startswith("```"):
        # drop opening fence line and trailing fence
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return json.loads(text)


def deterministic_outcome(signals):
    """Fallback outcome when no model verdict is available.

    Anchors 'successful' only on hard signals; otherwise 'unknown'.
    """
    if signals.get("commit_created") and signals.get("tests_passed") is True:
        return "successful", 0.7, "commit created and tests passed (deterministic)"
    if signals.get("pr_created"):
        return "successful", 0.5, "PR created (deterministic)"
    if signals.get("last_turn_was_error"):
        return "partial", 0.4, "session ended on a tool error (deterministic)"
    return "unknown", 0.2, "no hard success signal (deterministic fallback)"


def load_classification(session_id, class_dir):
    path = class_dir / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        data = parse_classification_json(path.read_text())
    except Exception as exc:
        print(f"Warning: bad classification {path}: {exc}", file=sys.stderr)
        return None
    # validate / normalise
    tags = data.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    tags = normalize_tags(tags)
    outcome = data.get("outcome")
    if outcome not in VALID_OUTCOMES:
        outcome = "unknown"
    return {
        "tags": [str(t) for t in tags],
        "summary": str(data.get("summary", "")).strip(),
        "outcome": outcome,
        "outcome_confidence": float(data.get("outcome_confidence", 0.0) or 0.0),
        "outcome_justification": str(data.get("outcome_justification",
                                              data.get("justification", ""))).strip(),
        "identifiers": data.get("identifiers", []),
        "source": "subagent",
    }


def merge(record, class_dir, cache):
    sid = record["session_id"]
    cls = load_classification(sid, class_dir)
    if cls is None:
        cached = cache.get(sid, {})
        if cached.get("hash") == record["file_hash"] and cached.get("classification"):
            cls = dict(cached["classification"])
            cls["source"] = "cache"
    if cls is None:
        outcome, conf, just = deterministic_outcome(record["outcome_signals"])
        cls = {
            "tags": [], "summary": "", "outcome": outcome,
            "outcome_confidence": conf, "outcome_justification": just,
            "identifiers": record.get("identifier_candidates", []),
            "source": "deterministic",
        }
    record["classification"] = cls
    return record


def flatten_for_csv(rec):
    cls = rec["classification"]
    repo = rec["repo"]
    branch = rec["branch"]
    feats = rec["features"]
    day = (rec["first_timestamp"] or "")[:10]
    return {
        "session_id": rec["session_id"],
        "project_path": rec["project_path"],
        "day": day,
        "models_used": "|".join(rec["models_used"]),
        "duration_seconds": rec["duration_seconds"],
        "human_prompt_count": rec["human_prompt_count"],
        "assistant_turn_count": rec["assistant_turn_count"],
        "session_cost_usd": rec.get("session_cost_usd", 0.0),
        "tags": "|".join(cls["tags"]),
        "summary": cls["summary"],
        "outcome": cls["outcome"],
        "outcome_confidence": cls["outcome_confidence"],
        "repo_name": repo["name"],
        "repo_source": repo["source"],
        "starting_branch": branch["starting_branch"],
        "working_branch": branch["working_branch"],
        "branch_created_in_session": branch["branch_created_in_session"],
        "subagents_used": feats["subagents_used"],
        "agent_teams_used": feats["agent_teams_used"],
        "skills_used": feats["skills_used"],
        "skills": "|".join(feats["skills"]),
        "identifiers": "|".join(
            f"{i['type']}:{i['value']}" for i in cls.get("identifiers", [])
        ),
        "classification_source": cls["source"],
    }


def iso_week(day):
    try:
        d = datetime.fromisoformat(day)
        y, w, _ = d.isocalendar()
        return f"{y}-W{w:02d}"
    except Exception:
        return "unknown"


def write_summary(records, path):
    n = len(records)
    grand_cost = sum(r.get("session_cost_usd", 0.0) for r in records)
    # deduped token totals (each session once), per model
    model_tokens = defaultdict(lambda: defaultdict(int))
    model_cost = defaultdict(float)
    for r in records:
        for model, toks in r.get("tokens_by_model", {}).items():
            for t, v in toks.items():
                model_tokens[model][t] += v
        for model, cb in r.get("cost_by_model", {}).items():
            model_cost[model] += cb.get("total", 0.0)
    grand_tokens = sum(sum(t.values()) for t in model_tokens.values())

    lines = []
    L = lines.append
    L("# Claude Code Usage — Classification Report\n")
    L(f"_Generated {datetime.now().isoformat(timespec='seconds')}_\n")
    L("## Grand totals (deduplicated — each session counted once)\n")
    ts = [(r["first_timestamp"] or "")[:10] for r in records if r.get("first_timestamp")]
    ts += [(r["last_timestamp"] or "")[:10] for r in records if r.get("last_timestamp")]
    ts = [t for t in ts if t]
    if ts:
        L(f"- Date range covered: **{min(ts)} → {max(ts)}**")
    L(f"- Sessions: **{n}**")
    L(f"- Total tokens: **{grand_tokens:,}**")
    L(f"- Total cost: **${grand_cost:.4f}**\n")

    L("## Cost & tokens by model\n")
    L("| Model | Input | Output | Cache read | Cache write | Cost (USD) |")
    L("|---|--:|--:|--:|--:|--:|")
    for model in sorted(model_tokens):
        t = model_tokens[model]
        cw = t.get("cache_write_5m", 0) + t.get("cache_write_1h", 0) + t.get("cache_write", 0)
        L(f"| {model} | {t.get('input',0):,} | {t.get('output',0):,} | "
          f"{t.get('cache_read',0):,} | {cw:,} | ${model_cost[model]:.4f} |")
    L("")

    # by outcome
    L("## By outcome\n")
    oc = Counter(r["classification"]["outcome"] for r in records)
    oc_cost = defaultdict(float)
    for r in records:
        oc_cost[r["classification"]["outcome"]] += r.get("session_cost_usd", 0.0)
    L("| Outcome | Sessions | Cost (USD) |")
    L("|---|--:|--:|")
    for outcome in ("successful", "partial", "abandoned", "unknown"):
        if oc.get(outcome):
            L(f"| {outcome} | {oc[outcome]} | ${oc_cost[outcome]:.4f} |")
    L("")

    # by tag — NON-ADDITIVE
    L("## By activity tag — NON-ADDITIVE\n")
    L("> Multi-tag sessions appear under each of their tags, so these rows "
      "**sum to more than the grand total by design**. Do not read tag cost "
      "as a partition of the total.\n")
    tag_count = Counter()
    tag_cost = defaultdict(float)
    for r in records:
        for tag in set(r["classification"]["tags"]):
            tag_count[tag] += 1
            tag_cost[tag] += r.get("session_cost_usd", 0.0)
    L("| Tag | Sessions | Cost (USD) |")
    L("|---|--:|--:|")
    for tag, c in tag_count.most_common():
        L(f"| {tag} | {c} | ${tag_cost[tag]:.4f} |")
    if not tag_count:
        L("| _(no tags yet — run classification)_ | 0 | $0.00 |")
    L("")

    # by repo
    L("## By repo\n")
    repo_count = Counter()
    repo_cost = defaultdict(float)
    for r in records:
        rn = r["repo"]["name"] or "(unknown)"
        repo_count[rn] += 1
        repo_cost[rn] += r.get("session_cost_usd", 0.0)
    L("| Repo | Sessions | Cost (USD) |")
    L("|---|--:|--:|")
    for rn, c in repo_count.most_common():
        L(f"| {rn} | {c} | ${repo_cost[rn]:.4f} |")
    L("")

    # by day / week
    L("## By day\n")
    day_count = Counter()
    day_cost = defaultdict(float)
    for r in records:
        day = (r["first_timestamp"] or "unknown")[:10]
        day_count[day] += 1
        day_cost[day] += r.get("session_cost_usd", 0.0)
    L("| Day | Sessions | Cost (USD) |")
    L("|---|--:|--:|")
    for day in sorted(day_count):
        L(f"| {day} | {day_count[day]} | ${day_cost[day]:.4f} |")
    L("")
    L("## By ISO week\n")
    wk_count = Counter()
    wk_cost = defaultdict(float)
    for r in records:
        wk = iso_week((r["first_timestamp"] or "")[:10])
        wk_count[wk] += 1
        wk_cost[wk] += r.get("session_cost_usd", 0.0)
    L("| Week | Sessions | Cost (USD) |")
    L("|---|--:|--:|")
    for wk in sorted(wk_count):
        L(f"| {wk} | {wk_count[wk]} | ${wk_cost[wk]:.4f} |")
    L("")

    # feature adoption
    L("## Feature adoption\n")
    L("| Feature | Sessions using | Share | Cost of those sessions |")
    L("|---|--:|--:|--:|")
    for label, key in (("Subagents", "subagents_used"),
                       ("Agent teams (heuristic)", "agent_teams_used"),
                       ("Skills", "skills_used")):
        using = [r for r in records if r["features"].get(key)]
        cost = sum(r.get("session_cost_usd", 0.0) for r in using)
        share = (len(using) / n * 100) if n else 0
        L(f"| {label} | {len(using)} | {share:.0f}% | ${cost:.4f} |")
    L("")
    unpriced = sorted({m for r in records for m in r.get("unpriced_models", [])})
    if unpriced:
        L("## ⚠️ Unpriced models (fill in pricing.json)\n")
        for m in unpriced:
            L(f"- `{m}`")
        L("")

    Path(path).write_text("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description="Merge, price, and roll up sessions")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--pricing", default=str(DEFAULT_PRICING))
    args = ap.parse_args()

    out_dir = Path(args.out_dir).expanduser()
    extracted = out_dir / "extracted.jsonl"
    if not extracted.exists():
        print(f"No extracted.jsonl in {out_dir}; run extract.py first",
              file=sys.stderr)
        sys.exit(1)

    class_dir = out_dir / "classifications"
    class_dir.mkdir(exist_ok=True)
    cache_path = out_dir / "cache.json"
    cache = {}
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text())
        except Exception:
            cache = {}

    pricing = load_pricing(args.pricing)
    warned = set()

    def warn(msg):
        if msg not in warned:
            warned.add(msg)
            print(f"WARNING: {msg}", file=sys.stderr)

    records = []
    for line in open(extracted, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        merge(rec, class_dir, cache)
        price_record(rec, pricing, warn)
        records.append(rec)

    # write sessions.jsonl
    with open(out_dir / "sessions.jsonl", "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # write sessions.csv
    if records:
        rows = [flatten_for_csv(r) for r in records]
        with open(out_dir / "sessions.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)

    # summary
    write_summary(records, out_dir / "summary.md")

    # persist classifications into cache (incremental)
    for rec in records:
        sid = rec["session_id"]
        cls = dict(rec["classification"])
        cls.pop("source", None)
        cache[sid] = {"hash": rec["file_hash"], "classification": cls}
    cache_path.write_text(json.dumps(cache, indent=2))

    print(f"Wrote sessions.jsonl, sessions.csv, summary.md to {out_dir} "
          f"({len(records)} sessions)", file=sys.stderr)


if __name__ == "__main__":
    main()
