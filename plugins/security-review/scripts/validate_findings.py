#!/usr/bin/env python3
"""Validate finding files against the schema.

Checks for every `findings/SR-*.md` (and optionally candidates):
  - required frontmatter fields present
  - CVSS v3.1 vector syntactically plausible
  - severity matches CVSS-derived band (Critical 9.0+, High 7.0–8.9, Medium 4.0–6.9, Low 0.1–3.9)
  - confidence in [0.0, 1.0]
  - all required body sections present in expected order

Usage:
    python3 validate_findings.py --run <run-id>            [--strict] [--include-candidates]
    python3 validate_findings.py --run-dir <abs-path>      [--strict]
    python3 validate_findings.py --check                   # exits 0 if find no files (CI-safe)

Stdout: one line per finding ("ok" / "warn" / "fail" + reasons)
Exit code: 0 if all ok (or --check with no files); 1 if warnings; 2 if failures (and --strict).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REQUIRED_FRONTMATTER_KEYS = {
    "id", "title", "status", "severity", "cvss_v3_1_vector", "cvss_v3_1_score",
    "cwe", "owasp_top_10_2021", "confidence", "discovered_by",
}
# Note: `affected`, `preconditions`, `references`, `tags`, `verified_by`, `chain_constituents`
# are recommended but not strictly required (see finding-SCHEMA.md "Optional fields"); validate
# does not error on their absence. `verified_by` is required for `status: confirmed`, enforced
# inline below.
REQUIRED_SECTIONS = [
    "## Summary",
    "## Detailed description",
    "## Exploit scenario",
    "## Suggested remediation",
    "## Verification test plan",
    "## Discovery notes",
]
SEVERITY_BANDS: List[Tuple[str, float, float]] = [
    ("critical", 9.0, 10.0),
    ("high",     7.0, 8.9),
    ("medium",   4.0, 6.9),
    ("low",      0.1, 3.9),
    ("info",     0.0, 0.0),
]
CVSS_VECTOR_RE = re.compile(
    r"^CVSS:3\.1/AV:[NALP]/AC:[LH]/PR:[NLH]/UI:[NR]/S:[UC]/C:[NLH]/I:[NLH]/A:[NLH]"
    r"(/E:[XUPFH])?(/RL:[XOTWU])?(/RC:[XURC])?"
    r"(/CR:[XLMH])?(/IR:[XLMH])?(/AR:[XLMH])?$"
)


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


def severity_for_score(score: float) -> str:
    if score >= 9.0:
        return "critical"
    if score >= 7.0:
        return "high"
    if score >= 4.0:
        return "medium"
    if score >= 0.1:
        return "low"
    return "info"


def validate_one(path: Path) -> Tuple[str, List[str]]:
    """Returns (level, messages). level ∈ {"ok", "warn", "fail"}."""
    msgs: List[str] = []
    try:
        text = path.read_text()
    except OSError as e:
        return "fail", [f"could not read: {e}"]
    fm = parse_frontmatter(text)
    if not fm:
        return "fail", ["no frontmatter"]

    missing = REQUIRED_FRONTMATTER_KEYS - fm.keys()
    if missing:
        msgs.append(f"missing frontmatter keys: {sorted(missing)}")
    # `verified_by` required when status is confirmed.
    if fm.get("status", "").lower() == "confirmed" and not fm.get("verified_by"):
        msgs.append("status=confirmed but verified_by is missing")

    # CVSS vector
    vec = fm.get("cvss_v3_1_vector", "")
    if vec and not CVSS_VECTOR_RE.match(vec):
        msgs.append(f"cvss vector malformed: {vec!r}")

    # Score band ↔ severity
    sev = fm.get("severity", "").lower()
    score_str = fm.get("cvss_v3_1_score", "")
    try:
        score = float(score_str)
        derived = severity_for_score(score)
        if sev and sev != derived:
            msgs.append(f"severity={sev!r} but cvss score {score} → {derived!r}")
    except ValueError:
        if score_str:
            msgs.append(f"cvss_v3_1_score not numeric: {score_str!r}")

    # Confidence
    try:
        conf = float(fm.get("confidence", "nan"))
        if not (0.0 <= conf <= 1.0):
            msgs.append(f"confidence out of range: {conf}")
    except ValueError:
        msgs.append(f"confidence not numeric: {fm.get('confidence')!r}")

    # Required sections (presence + order)
    body = text.split("\n---", 1)[-1].split("---", 1)[-1] if text.startswith("---") else text
    last_idx = -1
    for sec in REQUIRED_SECTIONS:
        idx = body.find(sec)
        if idx == -1:
            msgs.append(f"missing section: {sec}")
            continue
        if idx < last_idx:
            msgs.append(f"section out of order: {sec}")
        last_idx = idx

    # Verification test plan must have substantive content
    vtp_idx = body.find("## Verification test plan")
    if vtp_idx >= 0:
        next_idx = body.find("\n## ", vtp_idx + 1)
        section = body[vtp_idx:next_idx if next_idx > 0 else None]
        if len(section.strip()) < 200:
            msgs.append("verification test plan too short (< 200 chars); needs concrete steps")

    # Chain findings must list constituents.
    fid = fm.get("id", "")
    if fid.endswith("-CHAIN"):
        # chain_constituents may be a YAML list — re-scan frontmatter raw text.
        raw_fm = text[3:text.find("\n---", 3)] if text.startswith("---") else ""
        in_cc = False
        any_constituent = False
        for line in raw_fm.splitlines():
            s = line.strip()
            if s.startswith("chain_constituents:"):
                in_cc = True
                # inline form: chain_constituents: [SR-2026-001, SR-2026-002]
                inline = s.split(":", 1)[1].strip()
                if inline.startswith("[") and "]" in inline:
                    inner = inline[1:inline.find("]")].strip()
                    if inner and inner not in ("", "[]"):
                        any_constituent = True
                continue
            if in_cc:
                if line.startswith("  -") or line.startswith("\t-") or line.startswith("- "):
                    any_constituent = True
                elif line and not line.startswith(" ") and not line.startswith("\t"):
                    in_cc = False
        if not any_constituent:
            msgs.append("CHAIN finding has empty chain_constituents list")

    if any("missing" in m or "malformed" in m or "out of range" in m for m in msgs):
        return "fail", msgs
    if msgs:
        return "warn", msgs
    return "ok", []


def resolve_run(run: Optional[str], run_dir: Optional[str]) -> Optional[Path]:
    if run_dir:
        return Path(run_dir).resolve()
    if run:
        return (Path.cwd() / ".security-review" / run).resolve()
    return None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run", help="Run id under cwd/.security-review/")
    p.add_argument("--run-dir", help="Absolute path to run directory")
    p.add_argument("--strict", action="store_true", help="Exit 2 on warnings, not just failures")
    p.add_argument("--include-candidates", action="store_true",
                   help="Also validate findings/candidates/")
    p.add_argument("--check", action="store_true",
                   help="Self-test: validate the bundled finding-SCHEMA.md is parseable; exit 0")
    args = p.parse_args()

    if args.check:
        # Self-test path: confirm we can parse the schema header.
        here = Path(__file__).resolve().parent
        schema = here.parent / "templates" / "finding-SCHEMA.md"
        if not schema.exists():
            print(f"check: schema not found at {schema}", file=sys.stderr)
            return 2
        print("check: schema parseable", file=sys.stderr)
        return 0

    run_dir = resolve_run(args.run, args.run_dir)
    if run_dir is None:
        p.error("provide --run or --run-dir")

    findings_dir = run_dir / "findings"
    if not findings_dir.is_dir():
        print(f"error: {findings_dir} does not exist", file=sys.stderr)
        return 2

    files = list(findings_dir.glob("SR-*.md"))
    if args.include_candidates:
        files += list((findings_dir / "candidates").glob("*.md"))

    n_ok = n_warn = n_fail = 0
    for fp in sorted(files):
        level, msgs = validate_one(fp)
        if level == "ok":
            print(f"ok    {fp.name}")
            n_ok += 1
        elif level == "warn":
            print(f"warn  {fp.name}: {'; '.join(msgs)}")
            n_warn += 1
        else:
            print(f"fail  {fp.name}: {'; '.join(msgs)}")
            n_fail += 1

    print(f"\nsummary: {n_ok} ok, {n_warn} warn, {n_fail} fail", file=sys.stderr)
    if n_fail > 0:
        return 2
    if n_warn > 0 and args.strict:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
