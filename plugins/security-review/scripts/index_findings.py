#!/usr/bin/env python3
"""Regenerate findings/INDEX.md from on-disk finding files.

The manager calls this after each phase so it can read a small index instead of
loading every finding file into context. Pure mechanical operation: parses YAML
frontmatter from each `findings/SR-*.md`, emits a markdown table.

Usage:
    python3 index_findings.py --run <run-id>            # under cwd/.security-review/
    python3 index_findings.py --run-dir <abs-path>      # absolute path to a run
    python3 index_findings.py --check                   # validate without writing

No external dependencies. Python 3.8+. Hand-rolls a tiny YAML frontmatter parser
to avoid pulling in PyYAML.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional


def parse_frontmatter(text: str) -> Dict[str, str]:
    """Parse YAML-ish frontmatter into a flat dict of strings.

    Only handles the subset our schema uses: scalar values and simple list values.
    Lists are stored as their original raw text (we only need scalars for the index).
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    body = text[3:end].strip("\n")
    out: Dict[str, str] = {}
    current_key: Optional[str] = None
    for line in body.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith(" ") or line.startswith("\t") or line.startswith("-"):
            # continuation / list item — ignore for index purposes
            continue
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        # Strip surrounding quotes.
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
            val = val[1:-1]
        out[key] = val
        current_key = key
    return out


def first_affected(text: str) -> str:
    """Extract the first 'file' under affected: in frontmatter, best-effort."""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end == -1:
        return ""
    body = text[3:end]
    in_affected = False
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("affected:"):
            in_affected = True
            continue
        if in_affected:
            if not (line.startswith(" ") or line.startswith("\t") or line.startswith("-")):
                in_affected = False
                continue
            m = re.search(r"file:\s*(.+)$", line)
            if m:
                v = m.group(1).strip().strip('"').strip("'")
                lines_match = re.search(r"lines:\s*(.+)$", body[body.find(line):])
                if lines_match:
                    lines_v = lines_match.group(1).split("\n")[0].strip().strip('"').strip("'")
                    return f"{v}:{lines_v}"
                return v
    return ""


def collect(findings_dir: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for fp in sorted(findings_dir.glob("SR-*.md")):
        try:
            text = fp.read_text()
        except OSError as e:
            print(f"warning: could not read {fp}: {e}", file=sys.stderr)
            continue
        fm = parse_frontmatter(text)
        if not fm.get("id"):
            print(f"warning: missing id in {fp}", file=sys.stderr)
            continue
        rows.append({
            "id": fm.get("id", fp.stem),
            "severity": fm.get("severity", "?"),
            "cvss": fm.get("cvss_v3_1_score", "?"),
            "title": fm.get("title", "(no title)"),
            "file": first_affected(text),
            "status": fm.get("status", "?"),
            "_path": str(fp),
        })
    return rows


def severity_rank(s: str) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(s.lower(), 5)


def render(rows: List[Dict[str, str]], run_id: str) -> str:
    rows_sorted = sorted(rows, key=lambda r: (severity_rank(r["severity"]), r["id"]))
    out = [f"# Findings index — run {run_id}", ""]
    out.append("| ID | Severity | CVSS | Title | File | Status |")
    out.append("|---|---|---|---|---|---|")
    for r in rows_sorted:
        title = r["title"].replace("|", "\\|")
        file_ = r["file"].replace("|", "\\|")
        out.append(
            f"| `{r['id']}` | {r['severity']} | {r['cvss']} | "
            f"{title} | `{file_}` | {r['status']} |"
        )
    if not rows_sorted:
        out.append("| _(no findings yet)_ | | | | | |")
    return "\n".join(out) + "\n"


def resolve_run(run: Optional[str], run_dir: Optional[str]) -> Path:
    if run_dir:
        return Path(run_dir).resolve()
    if run:
        return (Path.cwd() / ".security-review" / run).resolve()
    raise SystemExit("error: provide --run <run-id> or --run-dir <path>")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run", help="Run id under cwd/.security-review/")
    p.add_argument("--run-dir", help="Absolute path to run directory")
    p.add_argument("--check", action="store_true", help="Print row count to stderr; do not write")
    args = p.parse_args()

    run_dir = resolve_run(args.run, args.run_dir)
    findings_dir = run_dir / "findings"
    if not findings_dir.is_dir():
        print(f"error: {findings_dir} does not exist", file=sys.stderr)
        return 2

    rows = collect(findings_dir)
    print(f"indexed {len(rows)} confirmed findings", file=sys.stderr)
    if args.check:
        return 0

    run_id = run_dir.name
    out = render(rows, run_id)
    (findings_dir / "INDEX.md").write_text(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
