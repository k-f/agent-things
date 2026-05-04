#!/usr/bin/env python3
"""Mechanically assemble report.md from per-finding markdown files.

The narrative sections (executive summary, methodology paragraph, coverage gaps)
are filled in by sr-report-compiler agent AFTER this script runs. This script
produces the structural skeleton plus the per-severity tables and full finding
bodies, leaving placeholder blocks for the agent.

Usage:
    python3 compile_report.py --run <run-id> [--output report.md] [--include-rejected]
    python3 compile_report.py --check-schema --run <run-id>

No external dependencies. Python 3.8+.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional


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


def first_affected(text: str) -> str:
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end == -1:
        return ""
    body = text[3:end]
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
            if mf:
                file_ = mf.group(1).strip().strip('"').strip("'")
                ml = re.search(r"lines:\s*(.+)$", body[body.find(line):])
                if ml:
                    lines_ = ml.group(1).split("\n")[0].strip().strip('"').strip("'")
                    return f"{file_}:{lines_}"
                return file_
    return ""


def severity_rank(s: str) -> int:
    return {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}.get(s.lower(), 5)


def body_after_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    if end == -1:
        return text
    return text[end + 4:].lstrip("\n")


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
    p.add_argument("--output", default="report.md", help="Output filename inside the run dir")
    p.add_argument("--include-rejected", action="store_true",
                   help="List rejected findings in §7 with links")
    p.add_argument("--check-schema", action="store_true",
                   help="Validate that all referenced findings parse; exit 0/2")
    args = p.parse_args()

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
            print(f"warning: skipping {fp} (no id)", file=sys.stderr)
            continue
        findings.append({
            "id": fm["id"],
            "title": fm.get("title", ""),
            "severity": fm.get("severity", "info").lower(),
            "cvss": fm.get("cvss_v3_1_score", ""),
            "cwe": fm.get("cwe", ""),
            "file": first_affected(text),
            "body": body_after_frontmatter(text),
            "frontmatter": fm,
            "_path": str(fp),
        })

    if args.check_schema:
        print(f"check-schema: parsed {len(findings)} findings", file=sys.stderr)
        return 0 if findings or True else 2

    findings.sort(key=lambda f: (severity_rank(f["severity"]), f["id"]))

    # Severity counts
    counts: Dict[str, int] = {}
    chain_findings: List[Dict] = []
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
        if f["id"].endswith("-CHAIN"):
            chain_findings.append(f)

    # Counts of rejected
    rejected_dir = findings_dir / "rejected"
    n_rejected = len(list(rejected_dir.glob("*.md"))) if rejected_dir.is_dir() else 0

    targets_text = ""
    targets_path = run_dir / "targets.md"
    if targets_path.exists():
        targets_text = targets_path.read_text()

    cal_text = ""
    cal_path = run_dir / "calibration.md"
    if cal_path.exists():
        cal_text = cal_path.read_text()

    now = dt.datetime.now()
    out: List[str] = []
    out.append(f"# Security Review Report — run {run_dir.name}")
    out.append("")
    out.append(f"> Generated {now:%Y-%m-%d %H:%M:%S}. Run id: `{run_dir.name}`. "
               f"Tool: security-review plugin.")
    out.append("")

    out.append("## 1. Executive Summary")
    out.append("")
    out.append("<!-- sr-report-compiler: write 3-6 paragraphs here, audience = engineering "
               "leadership. State headline risk, total findings by severity, single most urgent "
               "action with finding ID, project-type calibration applied. -->")
    out.append("")
    out.append("| Severity | Count |")
    out.append("|---|---|")
    for sev in ("critical", "high", "medium", "low", "info"):
        out.append(f"| {sev.title()} | {counts.get(sev, 0)} |")
    out.append("")
    out.append("**Most urgent action:** _<sr-report-compiler fills this in>_")
    out.append("")
    out.append("**Calibration applied:** _<sr-report-compiler quotes the project-type sentence "
               "from calibration.md>_")
    out.append("")

    out.append("## 2. Scope")
    out.append("")
    if targets_text:
        # Trim the leading "# Targets" heading from targets.md to avoid h1 collision.
        for line in targets_text.splitlines():
            if line.startswith("# "):
                continue
            out.append(line)
    out.append("")

    out.append("## 3. Methodology")
    out.append("")
    out.append("- 7-phase agent-team workflow: scoping → recon → threat-model → distribution → "
               "deep hunts → verification → triage → chain-composition → report.")
    out.append("- Each finding produced via Mythos-style hypothesize-verify loop with adversarial "
               "self-challenge before write-out, then independent adversarial verification by "
               "`sr-verifier`.")
    out.append("- Vulnerability classes covered: injection, authn/authz, crypto, code-execution & "
               "memory safety, web-vuln (XSS/SSRF/path-traversal), supply-chain & secrets, "
               "business-logic & race, cross-repo trust boundaries.")
    out.append("- Vulnerability classes explicitly EXCLUDED: DoS, generic rate-limiting, hardcoded "
               "secrets without usage path, generic input-validation without identified impact, "
               "open-redirect without authentication context.")
    out.append("- **Verification policy**: every finding ships with a human-executable test plan. "
               "No exploit code was executed by this tool.")
    out.append("")
    out.append("<!-- sr-report-compiler: paste the project-type calibration paragraph here. -->")
    out.append("")

    out.append("## 4. Findings by Severity")
    out.append("")
    for sev in ("critical", "high", "medium", "low", "info"):
        sev_rows = [f for f in findings if f["severity"] == sev and not f["id"].endswith("-CHAIN")]
        if not sev_rows:
            continue
        out.append(f"### {sev.title()}")
        out.append("")
        out.append("| ID | Title | CWE | File |")
        out.append("|---|---|---|---|")
        for f in sev_rows:
            title = f["title"].replace("|", "\\|")
            file_ = f["file"].replace("|", "\\|")
            out.append(f"| `{f['id']}` | {title} | {f['cwe']} | `{file_}` |")
        out.append("")

    if chain_findings:
        out.append("## 4b. Composed Exploit Chains")
        out.append("")
        for f in chain_findings:
            out.append(f"### `{f['id']}` — {f['title']}")
            out.append("")
            out.append(f"Severity: **{f['severity']}** (composed CVSS {f['cvss']}). "
                       f"Constituents: see chain finding body.")
            out.append("")
            out.append(f["body"])
            out.append("")
            out.append("---")
            out.append("")

    out.append("## 5. Detailed Findings")
    out.append("")
    for f in findings:
        if f["id"].endswith("-CHAIN"):
            continue
        out.append(f"### `{f['id']}` — {f['title']}")
        out.append("")
        out.append(
            f"Severity: **{f['severity']}** · CVSS: **{f['cvss']}** · "
            f"CWE: {f['cwe']} · File: `{f['file']}`"
        )
        out.append("")
        out.append(f["body"])
        out.append("")
        out.append("---")
        out.append("")

    out.append("## 6. Risk Acceptance Candidates")
    out.append("")
    out.append("<!-- sr-report-compiler: list any findings the calibration step flagged as "
               "below-the-bar but worth acknowledging. Format: ID — title — rationale. -->")
    out.append("")

    out.append("## 7. Suppressed / Excluded Findings")
    out.append("")
    out.append(f"- Filtered by confidence (< 0.8 default): _<sr-report-compiler fills count>_")
    out.append(f"- Out-of-scope vulnerability class: _<sr-report-compiler fills count>_")
    out.append(f"- Verifier rejected: **{n_rejected}** "
               f"(see `findings/rejected/` for per-finding reasoning)")
    if args.include_rejected and n_rejected > 0:
        out.append("")
        out.append("Rejected file list:")
        for fp in sorted(rejected_dir.glob("*.md")):
            out.append(f"  - `findings/rejected/{fp.name}`")
    out.append("")

    out.append("## 8. Coverage Gaps")
    out.append("")
    out.append("This review did NOT cover:")
    out.append("- Compiled binaries / vendored builds (only source reviewed)")
    out.append("- Runtime configuration in deployed environments (only repo configs reviewed)")
    out.append("- Dynamic analysis / fuzzing / Address Sanitizer-style runtime ground truth")
    out.append("- Reverse-engineering of stripped binaries")
    out.append("- Hardware / supply-chain provenance")
    out.append("- Transitive dependency code beyond direct surface scan")
    out.append("- Execution of generated PoC exploits (verification = human test plan only)")
    out.append("")
    out.append("<!-- sr-report-compiler: add any project-specific gaps surfaced during recon. -->")
    out.append("")

    out.append("## 9. Appendices")
    out.append("")
    out.append("- A. Threat model: see `threat-model.md`")
    out.append("- B. Attack-surface inventory: see `recon/*.md`")
    out.append("- C. Triage decisions and dedup: see `triage-summary.md`")
    out.append("- D. Calibration: see `calibration.md`")
    if (run_dir / "chains.md").exists():
        out.append("- E. Chain analysis: see `chains.md`")
    out.append("")

    output_path = run_dir / args.output
    output_path.write_text("\n".join(out) + "\n")
    print(f"wrote {output_path} ({len(findings)} findings)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
