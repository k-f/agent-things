#!/usr/bin/env python3
"""Render a human-readable progress dashboard for a security-review run.

Reads plan.md and counts files in findings/ directories. Writes/overwrites
progress.md with a summary the user can `cat` or open from the editor.

Usage:
    python3 progress.py --run <run-id>
    python3 progress.py                       # default: most recent run under cwd
    python3 progress.py --watch               # re-render every 30s (Ctrl-C to stop)

No external dependencies. Python 3.8+.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def find_most_recent(state_root: Path) -> Optional[Path]:
    base = state_root / ".security-review"
    if not base.is_dir():
        return None
    runs = [p for p in base.iterdir() if p.is_dir()]
    if not runs:
        return None
    return max(runs, key=lambda p: p.stat().st_mtime)


def parse_plan(plan_path: Path) -> Tuple[str, List[Dict[str, str]]]:
    """Return (header_block, [row dicts])."""
    if not plan_path.exists():
        return "", []
    text = plan_path.read_text()
    header_lines: List[str] = []
    rows: List[Dict[str, str]] = []
    in_table = False
    headers: List[str] = []
    for line in text.splitlines():
        if not in_table and line.startswith("| Phase | Step | Agent"):
            in_table = True
            headers = [h.strip() for h in line.strip("|").split("|")]
            continue
        if in_table and re.match(r"^\|[-: ]+\|", line):
            continue
        if in_table:
            if not line.strip().startswith("|"):
                in_table = False
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) != len(headers):
                continue
            rows.append(dict(zip(headers, cells)))
        else:
            header_lines.append(line)
    return "\n".join(header_lines).strip(), rows


def count_phase(rows: List[Dict[str, str]]) -> Dict[str, Dict[str, int]]:
    phases: Dict[str, Dict[str, int]] = {}
    for r in rows:
        phase = r.get("Phase", "?")
        status = r.get("Status", "?").lower()
        d = phases.setdefault(phase, {
            "pending": 0, "running": 0, "done": 0, "failed": 0,
            "skipped": 0, "partial": 0, "partial-superseded": 0,
        })
        if status in d:
            d[status] += 1
    return phases


def render(run_dir: Path) -> str:
    plan_path = run_dir / "plan.md"
    header, rows = parse_plan(plan_path)
    phases = count_phase(rows)

    findings_dir = run_dir / "findings"
    n_confirmed = len(list(findings_dir.glob("SR-*.md"))) if findings_dir.is_dir() else 0
    n_candidates = (
        len(list((findings_dir / "candidates").glob("*.md")))
        if (findings_dir / "candidates").is_dir() else 0
    )
    n_rejected = (
        len(list((findings_dir / "rejected").glob("*.md")))
        if (findings_dir / "rejected").is_dir() else 0
    )

    out: List[str] = []
    out.append(f"# Security Review Progress — run {run_dir.name}")
    out.append("")
    if header:
        # Strip the leading "# heading" line from the plan's preamble.
        for line in header.splitlines():
            if line.startswith("#") and not line.startswith("##"):
                continue
            out.append(line)
        out.append("")
    out.append(f"_Rendered: {dt.datetime.now():%Y-%m-%d %H:%M:%S}_")
    out.append("")

    # Phase summary
    out.append("## Phase summary")
    out.append("")
    out.append("| Phase | pending | running | done | partial | failed | skipped |")
    out.append("|---|---|---|---|---|---|---|")
    for phase in sorted(phases.keys(), key=lambda p: (str(p),)):
        d = phases[phase]
        partial_total = d.get("partial", 0) + d.get("partial-superseded", 0)
        out.append(
            f"| {phase} | {d['pending']} | {d['running']} | "
            f"{d['done']} | {partial_total} | {d['failed']} | {d['skipped']} |"
        )
    if not phases:
        out.append("| _(no rows yet)_ | | | | | | |")
    out.append("")

    # Findings counts
    out.append("## Findings so far")
    out.append("")
    out.append(f"- Candidates (pre-verification): **{n_candidates}**")
    out.append(f"- Confirmed (post-verification): **{n_confirmed}**")
    out.append(f"- Rejected by verifier: **{n_rejected}**")
    out.append("")

    # In-flight
    in_flight = [r for r in rows if r.get("Status", "").lower() == "running"]
    if in_flight:
        out.append("## In-flight")
        out.append("")
        for r in in_flight[:20]:
            out.append(
                f"- {r.get('Phase', '?')} · {r.get('Agent', '?')} · "
                f"`{r.get('Assignment', '')}`"
            )
        if len(in_flight) > 20:
            out.append(f"- _… and {len(in_flight) - 20} more_")
        out.append("")

    # Failed
    failed = [r for r in rows if r.get("Status", "").lower() == "failed"]
    if failed:
        out.append("## Failed (investigate before re-dispatch)")
        out.append("")
        for r in failed[:20]:
            out.append(
                f"- {r.get('Phase', '?')} · {r.get('Agent', '?')} · "
                f"`{r.get('Assignment', '')}` · {r.get('Notes', '')}"
            )
        out.append("")

    return "\n".join(out) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run", help="Run id under cwd/.security-review/")
    p.add_argument("--run-dir", help="Absolute path to run directory")
    p.add_argument("--state-root", default=".", help="Where .security-review/ lives (default: cwd)")
    p.add_argument("--watch", action="store_true", help="Re-render every 30s")
    p.add_argument("--check", action="store_true", help="Print most-recent run path; exit 0")
    args = p.parse_args()

    state_root = Path(args.state_root).resolve()

    if args.run_dir:
        run_dir = Path(args.run_dir).resolve()
    elif args.run:
        run_dir = state_root / ".security-review" / args.run
    else:
        rec = find_most_recent(state_root)
        if not rec:
            print(f"no runs found under {state_root}/.security-review/", file=sys.stderr)
            return 2
        run_dir = rec

    if args.check:
        print(run_dir)
        return 0

    if not run_dir.is_dir():
        print(f"error: {run_dir} does not exist", file=sys.stderr)
        return 2

    if args.watch:
        try:
            while True:
                out = render(run_dir)
                (run_dir / "progress.md").write_text(out)
                print(f"\033[2J\033[H{out}")  # clear-screen + render to terminal
                time.sleep(30)
        except KeyboardInterrupt:
            print("\nstopped.", file=sys.stderr)
            return 0
    else:
        out = render(run_dir)
        (run_dir / "progress.md").write_text(out)
        sys.stdout.write(out)
        return 0


if __name__ == "__main__":
    sys.exit(main())
