#!/usr/bin/env python3
"""
cc-usage-classifier — check classifier subagent output.

Every session listed in <out-dir>/batches/batch-*.json should have a
classification at <out-dir>/classifications/<session_id>.json. This checks
each one against the output schema and the taxonomy tags, and reports which
are missing or malformed.

Usage:
  python3 validate.py --out-dir DIR [--taxonomy taxonomy.md]
        (check only; no side effects)
  python3 validate.py --out-dir DIR --write-retry-batch
        also write batches/retry-NNN.json holding the failed sessions'
        inputs (<= 20 per file) for one retry round
  python3 validate.py --out-dir DIR --quarantine
        move still-malformed files to classifications/invalid/ so report.py
        uses its deterministic fallback for them

Stdout: JSON {"expected": N, "ok": N, "missing": [...], "malformed":
              {session_id: reason}, "retry_batches": [...]}
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from report import (DEFAULT_OUT_DIR, VALID_OUTCOMES,  # noqa: E402
                    normalize_tags, parse_classification_json)

DEFAULT_TAXONOMY = (Path(__file__).resolve().parent.parent
                    / "references" / "taxonomy.md")
RETRY_BATCH_SIZE = 20
TAG_BULLET_RE = re.compile(r"^\s*-\s+\*\*([^*]+)\*\*")


def taxonomy_tags(path):
    """Tags defined as '- **tag** —' bullets in the '## Tags' section."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(f"cannot read taxonomy {path}: {exc}")
    tags, in_tags = set(), False
    for line in text.splitlines():
        if line.startswith("## "):
            in_tags = line.strip().lower() == "## tags"
            continue
        if in_tags:
            m = TAG_BULLET_RE.match(line)
            if m:
                tags.add(m.group(1).strip())
    if not tags:
        raise SystemExit(f"no tags found in {path} ('## Tags' bullets)")
    return tags


def check_one(path, session_id, allowed_tags):
    """Return None if valid, else a short reason string."""
    try:
        data = parse_classification_json(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — any parse failure is "malformed"
        return f"not valid JSON: {exc}"
    if not isinstance(data, dict):
        return "not a JSON object"
    sid = data.get("session_id")
    if sid is not None and sid != session_id:
        return f"session_id mismatch ({sid!r})"
    tags = data.get("tags")
    if not isinstance(tags, list) or not tags or not all(
            isinstance(t, str) for t in tags):
        return "tags must be a non-empty list of strings"
    unknown = [t for t in normalize_tags(tags) if t not in allowed_tags]
    if unknown:
        return f"tags not in taxonomy: {unknown}"
    if data.get("outcome") not in VALID_OUTCOMES:
        return f"bad outcome {data.get('outcome')!r}"
    conf = data.get("outcome_confidence")
    if isinstance(conf, bool) or not isinstance(conf, (int, float)) \
            or not 0.0 <= conf <= 1.0:
        return f"outcome_confidence must be a number in [0,1], got {conf!r}"
    if not isinstance(data.get("summary"), str):
        return "summary must be a string"
    ids = data.get("identifiers", [])
    if not isinstance(ids, list):
        return "identifiers must be a list"
    return None


def load_batch_items(bdir, pattern="batch-*.json"):
    items = {}
    for path in sorted(bdir.glob(pattern)):
        try:
            for item in json.loads(path.read_text(encoding="utf-8")):
                items[item["session_id"]] = item
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"Warning: unreadable batch {path}: {exc}", file=sys.stderr)
    return items


def main(argv=None):
    ap = argparse.ArgumentParser(description="Validate classifier output")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--taxonomy", default=str(DEFAULT_TAXONOMY))
    ap.add_argument("--write-retry-batch", action="store_true")
    ap.add_argument("--quarantine", action="store_true")
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir).expanduser()
    bdir = out_dir / "batches"
    cdir = out_dir / "classifications"
    allowed = taxonomy_tags(args.taxonomy)
    items = load_batch_items(bdir)

    missing, malformed, ok = [], {}, 0
    for sid in items:
        path = cdir / f"{sid}.json"
        if not path.exists():
            missing.append(sid)
            continue
        reason = check_one(path, sid, allowed)
        if reason:
            malformed[sid] = reason
        else:
            ok += 1

    retry_paths = []
    if args.write_retry_batch:
        for old in bdir.glob("retry-*.json"):
            old.unlink()
        failed = missing + sorted(malformed)
        for i in range(0, len(failed), RETRY_BATCH_SIZE):
            chunk = [items[s] for s in failed[i:i + RETRY_BATCH_SIZE]]
            path = bdir / f"retry-{i // RETRY_BATCH_SIZE + 1:03d}.json"
            path.write_text(json.dumps(chunk, ensure_ascii=False, indent=1),
                            encoding="utf-8")
            retry_paths.append(str(path))

    if args.quarantine and malformed:
        qdir = cdir / "invalid"
        qdir.mkdir(parents=True, exist_ok=True)
        for sid in malformed:
            (cdir / f"{sid}.json").replace(qdir / f"{sid}.json")

    print(json.dumps({
        "expected": len(items), "ok": ok, "missing": missing,
        "malformed": malformed, "retry_batches": retry_paths,
        "quarantined": sorted(malformed) if args.quarantine else [],
    }, indent=2))


if __name__ == "__main__":
    main()
