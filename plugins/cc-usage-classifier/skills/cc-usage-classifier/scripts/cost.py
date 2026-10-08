#!/usr/bin/env python3
"""
cc-usage-classifier — per-model cost engine (Component 2).

Prices each session with PER-MODEL custom rates from pricing.json (USD per
million tokens). Each model's tokens are matched to that model's rates; an
unpriced model is warned about and never silently priced as another model.

session_cost = Σ_models Σ_token_types ( tokens[model][type] × rate[model][type] )

Long-context tier: a rate card may carry a "long_context" block
({"threshold_tokens": T, "input": ..., ...}). extract.py records, per model,
the tokens of requests whose prompt (input + cache_read + cache_write)
exceeded T in record["long_context_tokens_by_model"][model][str(T)]; those
tokens are priced at the long-context rates, the remainder at the standard
rates.

Importable: price_record(record, pricing) -> record with cost fields added.
CLI: python3 cost.py --in extracted.jsonl --pricing pricing.json [--out priced.jsonl]
     python3 cost.py --in extracted.jsonl --check-unpriced   (no side effects)
"""

import argparse
import json
import re
import sys
from pathlib import Path

# token type -> fallback rate key (for unsplit cache_write data)
RATE_FALLBACK = {"cache_write": "cache_write_5m"}

DEFAULT_PRICING = Path(__file__).resolve().parent.parent / "pricing.json"


def strip_date_suffix(model_id):
    """Drop a trailing release-date suffix, keeping the version.

    claude-haiku-4-5-20251001 -> claude-haiku-4-5
    claude-opus-4-8           -> claude-opus-4-8  (no date suffix)
    """
    return re.sub(r"-\d{6,}$", "", model_id or "")


def family_key(model_id):
    """Coarse '<family>-<major>-x' fallback (use only as a last resort).

    NOTE: within a major version, price tiers can differ (e.g. Opus 4.1 is
    $15/$75 but Opus 4.5+ is $5/$25; Opus 5 is $5/$25 but Opus 5.5 is $4/$20),
    so this is intentionally the LAST key tried and the bundled pricing.json
    defines no '-x' keys: an unknown version is warned about, not guessed.
    claude-opus-5-7 -> claude-opus-5-x (only priced if that key exists).
    """
    m = re.match(r"(claude-(?:opus|sonnet|haiku|fable|mythos))-(\d+)", model_id or "")
    return f"{m.group(1)}-{m.group(2)}-x" if m else None


def candidate_keys(model_id):
    """Pricing keys to try, most specific first."""
    keys = []
    for k in (model_id, strip_date_suffix(model_id), family_key(model_id)):
        if k and k not in keys:
            keys.append(k)
    return keys


def lookup_rates(model_id, pricing):
    """Return (rates_dict, matched_key) or (None, None) if unpriced.

    Tries the exact id, then the date-stripped version, then the coarse
    family key. Version-specific keys therefore win over family fallbacks.
    """
    for key in candidate_keys(model_id):
        if key in pricing and isinstance(pricing[key], dict):
            return pricing[key], key
    return None, None


def _rate(rates, ttype):
    rate_key = ttype if ttype in rates else RATE_FALLBACK.get(ttype)
    return (rates.get(rate_key, 0.0) if rate_key else 0.0) or 0.0


def price_record(record, pricing, warn=None):
    """Add per-model and total cost to a record. Returns the record."""
    tokens_by_model = record.get("tokens_by_model", {}) or {}
    long_by_model = record.get("long_context_tokens_by_model")
    scanned = {str(t) for t in record.get("long_context_thresholds", [])}
    cost_by_model = {}
    unpriced = []
    total = 0.0
    for model, toks in tokens_by_model.items():
        rates, matched = lookup_rates(model, pricing)
        if rates is None:
            unpriced.append(model)
            cost_by_model[model] = {"by_type": {}, "total": 0.0,
                                    "unpriced": True}
            if warn:
                warn(f"unpriced model id: {model!r} — left at $0.00")
            continue
        lc = rates.get("long_context")
        long_toks, long_rates = {}, None
        if isinstance(lc, dict) and lc.get("threshold_tokens"):
            key = str(int(lc["threshold_tokens"]))
            if long_by_model is None or key not in scanned:
                if warn:
                    warn(f"{model!r}: long-context tier (> {key} prompt tokens) "
                         "not applied — re-run extract.py with this pricing.json")
            else:
                long_toks = (long_by_model.get(model) or {}).get(key, {}) or {}
                long_rates = lc
        by_type = {}
        model_total = 0.0
        for ttype, ntok in toks.items():
            ntok = ntok or 0
            nlong = min(long_toks.get(ttype, 0) or 0, ntok) if long_rates else 0
            c = (ntok - nlong) * _rate(rates, ttype) / 1_000_000.0
            if nlong:
                lr = _rate(long_rates, ttype) or _rate(rates, ttype)
                c += nlong * lr / 1_000_000.0
            by_type[ttype] = round(c, 6)
            model_total += c
        entry = {
            "by_type": by_type,
            "total": round(model_total, 6),
            "priced_as": matched,
        }
        if long_rates and any(long_toks.values()):
            entry["long_context_tokens"] = dict(long_toks)
        cost_by_model[model] = entry
        total += model_total
    record["cost_by_model"] = cost_by_model
    record["session_cost_usd"] = round(total, 6)
    record["unpriced_models"] = unpriced
    return record


def unpriced_share(records, pricing):
    """Return (total_tokens, {unpriced_model: tokens}) across records."""
    grand = 0
    unpriced = {}
    for rec in records:
        for model, toks in (rec.get("tokens_by_model") or {}).items():
            n = sum(v or 0 for v in toks.values())
            grand += n
            if lookup_rates(model, pricing)[0] is None:
                unpriced[model] = unpriced.get(model, 0) + n
    return grand, unpriced


def load_pricing(path):
    data = json.loads(Path(path).read_text())
    # strip comment keys
    return {k: v for k, v in data.items() if not k.startswith("_")}


def main():
    ap = argparse.ArgumentParser(description="Per-model cost engine")
    ap.add_argument("--in", dest="infile", required=True,
                    help="extracted.jsonl from extract.py")
    ap.add_argument("--pricing", default=str(DEFAULT_PRICING))
    ap.add_argument("--out", default=None, help="output jsonl (default: stdout)")
    ap.add_argument("--check-unpriced", action="store_true",
                    help="Print unpriced model ids and their token share as "
                         "JSON; write nothing")
    args = ap.parse_args()

    pricing = load_pricing(args.pricing)
    if args.check_unpriced:
        recs = [json.loads(l) for l in open(args.infile, encoding="utf-8")
                if l.strip()]
        grand, unpriced = unpriced_share(recs, pricing)
        print(json.dumps({
            "total_tokens": grand,
            "unpriced": {m: {"tokens": n,
                             "share": round(n / grand, 4) if grand else 0.0}
                         for m, n in sorted(unpriced.items())},
        }, indent=2))
        return
    warned = set()

    def warn(msg):
        if msg not in warned:
            warned.add(msg)
            print(f"WARNING: {msg}", file=sys.stderr)

    out = open(args.out, "w", encoding="utf-8") if args.out else sys.stdout
    grand = 0.0
    n = 0
    for line in open(args.infile, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        price_record(rec, pricing, warn)
        grand += rec["session_cost_usd"]
        n += 1
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
    if args.out:
        out.close()
    print(f"Priced {n} sessions, grand total ${grand:.4f}", file=sys.stderr)


if __name__ == "__main__":
    main()
