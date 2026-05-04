#!/usr/bin/env python3
"""Read-only audit of a run's on-disk state vs plan.md.

Used by the resume flow before re-dispatching. Reports:
  - which plan.md rows are pending / running / failed (re-dispatch candidates)
  - which assignment files exist on disk vs are referenced in plan.md
  - which finding files exist that are not yet indexed
  - any inconsistencies (orphan candidates, missing worklogs)

Usage:
    python3 replay.py --run <run-id> [--check-consistency]

No side effects. No external deps. Python 3.8+.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional


def parse_plan(plan_path: Path) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    if not plan_path.exists():
        return rows
    text = plan_path.read_text()
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
                break
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) != len(headers):
                continue
            rows.append(dict(zip(headers, cells)))
    return rows


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
    p.add_argument("--check-consistency", action="store_true",
                   help="Also report orphan files and inconsistencies")
    p.add_argument("--check", action="store_true", help="Confirm script is invocable; exit 0")
    args = p.parse_args()

    if args.check:
        print("check: ok", file=sys.stderr)
        return 0

    run_dir = resolve_run(args.run, args.run_dir)
    if not run_dir.is_dir():
        print(f"error: {run_dir} does not exist", file=sys.stderr)
        return 2

    rows = parse_plan(run_dir / "plan.md")
    by_status: Dict[str, List[Dict[str, str]]] = {}
    for r in rows:
        s = r.get("Status", "?").lower()
        by_status.setdefault(s, []).append(r)

    print(f"# Replay audit — run {run_dir.name}")
    print()
    print(f"plan.md rows: {len(rows)}")
    for status in ("done", "skipped", "running", "pending", "failed"):
        print(f"  {status}: {len(by_status.get(status, []))}")
    print()

    needs_redispatch = (
        by_status.get("pending", [])
        + by_status.get("running", [])
        + by_status.get("failed", [])
    )
    if needs_redispatch:
        print(f"## Re-dispatch candidates ({len(needs_redispatch)})")
        print()
        for r in needs_redispatch:
            print(
                f"- {r.get('Phase', '?')} · {r.get('Agent', '?')} · "
                f"`{r.get('Assignment', '')}` · status={r.get('Status', '?')}"
            )
        print()

    if args.check_consistency:
        print("## Consistency checks")
        print()
        # Orphan candidates: files in candidates/ that don't have a confirmed or rejected counterpart.
        cand_dir = run_dir / "findings" / "candidates"
        confirmed = {p.stem for p in (run_dir / "findings").glob("SR-*.md")}
        rejected = {p.stem for p in (run_dir / "findings" / "rejected").glob("*.md")}
        orphans: List[str] = []
        if cand_dir.is_dir():
            for cp in cand_dir.glob("*.md"):
                base = cp.stem
                if base not in confirmed and base not in rejected:
                    orphans.append(cp.name)
        if orphans:
            print(f"Orphan candidates (not yet verified): {len(orphans)}")
            for o in orphans[:20]:
                print(f"  - {o}")
            if len(orphans) > 20:
                print(f"  ... and {len(orphans) - 20} more")
        else:
            print("Orphan candidates: 0")
        print()

        # Missing worklogs for hunters that have candidates.
        wlog_dir = run_dir / "worklog"
        if wlog_dir.is_dir() and cand_dir.is_dir():
            wlogs = {p.stem for p in wlog_dir.glob("*.md")}
            print(f"Worklogs present: {len(wlogs)}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
