#!/usr/bin/env python3
"""
cc-usage-classifier — per-model cost engine (Component 2).

Prices each session with PER-MODEL custom rates from pricing.json (USD per
million tokens). Each model's tokens are matched to that model's rates; an
unpriced model is warned about and never silently priced as another model.

session_cost = Σ_models Σ_token_types ( tokens[model][type] × rate[model][type] )

Importable: price_record(record, pricing) -> record with cost fields added.
CLI: python3 cost.py --in extracted.jsonl --pricing pricing.json [--out priced.jsonl]
"""

import argparse
import json
import re
import sys
from pathlib import Path

# token type -> fallback rate key (for unsplit cache_write data)
RATE_FALLBACK = {"cache_write": "cache_write_5m"}

DEFAULT_PRICING = Path(__file__).resolve().parent.parent / "pricing.json"


def normalize_model(model_id):
    """Map a concrete model id to a pricing family key.

    claude-opus-4-8            -> claude-opus-4-x
    claude-sonnet-4-6          -> claude-sonnet-4-x
    claude-haiku-4-5-20251001  -> claude-haiku-4-x
    """
    if not model_id:
        return model_id
    m = re.match(r"(claude-(?:opus|sonnet|haiku))-(\d+)", model_id)
    if m:
        return f"{m.group(1)}-{m.group(2)}-x"
    return model_id


def lookup_rates(model_id, pricing):
    """Return (rates_dict, matched_key) or (None, None) if unpriced."""
    if model_id in pricing and isinstance(pricing[model_id], dict):
        return pricing[model_id], model_id
    norm = normalize_model(model_id)
    if norm in pricing and isinstance(pricing[norm], dict):
        return pricing[norm], norm
    return None, None


def price_record(record, pricing, warn=None):
    """Add per-model and total cost to a record. Returns the record."""
    tokens_by_model = record.get("tokens_by_model", {}) or {}
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
        by_type = {}
        model_total = 0.0
        for ttype, ntok in toks.items():
            rate_key = ttype if ttype in rates else RATE_FALLBACK.get(ttype)
            rate = rates.get(rate_key, 0.0) if rate_key else 0.0
            c = (ntok or 0) * (rate or 0.0) / 1_000_000.0
            by_type[ttype] = round(c, 6)
            model_total += c
        cost_by_model[model] = {
            "by_type": by_type,
            "total": round(model_total, 6),
            "priced_as": matched,
        }
        total += model_total
    record["cost_by_model"] = cost_by_model
    record["session_cost_usd"] = round(total, 6)
    record["unpriced_models"] = unpriced
    return record


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
    args = ap.parse_args()

    pricing = load_pricing(args.pricing)
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
