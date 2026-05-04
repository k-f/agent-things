#!/usr/bin/env python3
"""Fuzzy-cluster confirmed findings to suggest dedup merges.

Clusters by (cwe, file, function, normalized title). Pure stdlib (uses
difflib.SequenceMatcher for title similarity). Outputs a markdown report
appended to triage-summary.md for sr-triage to act on.

Usage:
    python3 dedupe.py --run <run-id> [--threshold 0.85] [--dry-run]

--dry-run (default) just writes suggestions; sr-triage applies them.

No external dependencies. Python 3.8+.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def parse_frontmatter(text: str) -> Dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    body = text[3:end].strip("\n")
    out: Dict[str, str] = {}
    for line in body.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith(" ") or line.startswith("\t") or line.startswith("-"):
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
            val = val[1:-1]
        out[key] = val
    return out


def first_affected(text: str) -> Tuple[str, str]:
    """Extract first (file, function) under affected: best-effort."""
    if not text.startswith("---"):
        return "", ""
    end = text.find("\n---", 3)
    if end == -1:
        return "", ""
    body = text[3:end]
    file_, func = "", ""
    in_aff = False
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("affected:"):
            in_aff = True
            continue
        if in_aff:
            if not (line.startswith(" ") or line.startswith("\t") or line.startswith("-")):
                break
            mf = re.search(r"file:\s*(.+)$", line)
            if mf and not file_:
                file_ = mf.group(1).strip().strip('"').strip("'")
            mfn = re.search(r"function:\s*(.+)$", line)
            if mfn and not func:
                func = mfn.group(1).strip().strip('"').strip("'")
    return file_, func


def normalize_title(t: str) -> str:
    t = re.sub(r"[^A-Za-z0-9 ]", " ", t.lower())
    t = re.sub(r"\s+", " ", t).strip()
    # Drop trivial words.
    stop = {"a", "an", "the", "in", "via", "of", "on", "for", "with"}
    return " ".join(w for w in t.split() if w not in stop)


def title_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def cluster(findings: List[Dict], threshold: float) -> List[List[Dict]]:
    """Greedy clustering: same (cwe, file) AND title sim ≥ threshold → same cluster."""
    clusters: List[List[Dict]] = []
    for f in findings:
        placed = False
        for c in clusters:
            head = c[0]
            same_cwe = (f["cwe"] == head["cwe"]) and f["cwe"]
            same_file = (f["file"] == head["file"]) and f["file"]
            if same_cwe and same_file and title_similarity(f["title"], head["title"]) >= threshold:
                c.append(f)
                placed = True
                break
        if not placed:
            clusters.append([f])
    return clusters


def resolve_run(run: Optional[str], run_dir: Optional[str]) -> Path:
    if run_dir:
        return Path(run_dir).resolve()
    if run:
        return (Path.cwd() / ".security-review" / run).resolve()
    raise SystemExit("error: provide --run or --run-dir")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run", help="Run id under cwd/.security-review/")
    p.add_argument("--run-dir", help="Absolute path to run directory")
    p.add_argument("--threshold", type=float, default=0.85,
                   help="Title similarity threshold (0.0–1.0). Default 0.85.")
    p.add_argument("--dry-run", action="store_true", default=True,
                   help="Write suggestions only; do not modify findings (default)")
    p.add_argument("--check", action="store_true", help="Self-test: confirm difflib available")
    args = p.parse_args()

    if args.check:
        import difflib  # noqa
        print("check: difflib available", file=sys.stderr)
        return 0

    run_dir = resolve_run(args.run, args.run_dir)
    findings_dir = run_dir / "findings"
    if not findings_dir.is_dir():
        print(f"error: {findings_dir} does not exist", file=sys.stderr)
        return 2

    findings: List[Dict] = []
    for fp in sorted(findings_dir.glob("SR-*.md")):
        try:
            text = fp.read_text()
        except OSError:
            continue
        fm = parse_frontmatter(text)
        if not fm.get("id"):
            continue
        file_, func = first_affected(text)
        findings.append({
            "id": fm["id"],
            "title": fm.get("title", ""),
            "cwe": fm.get("cwe", ""),
            "severity": fm.get("severity", ""),
            "file": file_,
            "function": func,
            "_path": str(fp),
        })

    clusters = cluster(findings, args.threshold)
    multi = [c for c in clusters if len(c) > 1]

    out_lines = [
        "## Dedup suggestions",
        f"Threshold: {args.threshold}.  Clusters: {len(clusters)}.  "
        f"Suggested merges: {len(multi)}.",
        "",
    ]
    if not multi:
        out_lines.append("_No fuzzy-duplicate clusters found._")
    for i, c in enumerate(multi, 1):
        out_lines.append(f"### Cluster {i} — {len(c)} findings")
        out_lines.append(f"CWE: `{c[0]['cwe']}`. File: `{c[0]['file']}`.")
        out_lines.append("")
        for f in c:
            out_lines.append(f"- `{f['id']}` — {f['title']} ({f['severity']})")
        out_lines.append("")
        out_lines.append(f"_Suggestion_: keep `{c[0]['id']}`; mark others "
                         f"`status: duplicate-of:{c[0]['id']}`.")
        out_lines.append("")

    out_text = "\n".join(out_lines) + "\n"
    summary_path = run_dir / "triage-summary.md"
    if summary_path.exists():
        existing = summary_path.read_text()
        # Replace any prior dedup section; keep the rest.
        existing = re.sub(
            r"## Dedup suggestions[\s\S]*?(?=\n## |\Z)", "", existing
        ).rstrip() + "\n\n"
        summary_path.write_text(existing + out_text)
    else:
        summary_path.write_text(f"# Triage summary — run {run_dir.name}\n\n" + out_text)

    print(f"wrote {len(multi)} merge suggestions to {summary_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
