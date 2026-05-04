#!/usr/bin/env python3
"""Bootstrap a security-review run directory.

Creates `.security-review/<run-id>/` in the current working directory and seeds
it with templates copied from the plugin's templates/ folder. Writes plan.md,
calibration.md, targets.md, and an empty findings/ tree.

Usage:
    python3 init_run.py --targets path1[,path2,...] \
                        --project-type {poc|internal|production|regulated|safety-critical|unsure} \
                        --depth {quick|standard|deep|exhaustive} \
                        [--gitignore]

    python3 init_run.py --check-deps     # diagnostics only, no side effects

Stdout: the run-id (so the parent skill can capture it).
Stderr: progress notes and warnings.

No external dependencies. Python 3.8+.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

VALID_PROJECT_TYPES = {"poc", "internal", "production", "regulated", "safety-critical", "unsure"}
VALID_DEPTHS = {"quick", "standard", "deep", "exhaustive"}

DEPTH_BUDGETS = {
    "quick":      {"hunter_ctx": 200_000, "verify_ctx": 150_000, "recon_ctx": 100_000, "parallel": 3},
    "standard":   {"hunter_ctx": 400_000, "verify_ctx": 300_000, "recon_ctx": 200_000, "parallel": 5},
    "deep":       {"hunter_ctx": 600_000, "verify_ctx": 400_000, "recon_ctx": 300_000, "parallel": 5},
    "exhaustive": {"hunter_ctx": 800_000, "verify_ctx": 500_000, "recon_ctx": 400_000, "parallel": 5},
}


def find_templates_dir() -> Path:
    """Locate the bundled templates/ directory.

    Checks (in order):
      1. CLAUDE_PLUGIN_ROOT env var (set when running via /plugin install)
      2. relative to this script (when running from a checkout / --plugin-dir)
    """
    env_root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if env_root:
        candidate = Path(env_root) / "templates"
        if candidate.is_dir():
            return candidate
    here = Path(__file__).resolve().parent
    candidate = here.parent / "templates"
    if candidate.is_dir():
        return candidate
    raise FileNotFoundError(
        "Could not locate plugin templates/ directory. "
        "Set CLAUDE_PLUGIN_ROOT or run from a checkout."
    )


def short_hash(seed: str) -> str:
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()[:6]


def loc_count(path: Path) -> int:
    """Approximate LOC under a path. Best-effort, skips errors silently."""
    if not path.is_dir():
        return 0
    total = 0
    excluded_dirs = {".git", "node_modules", "vendor", "dist", "build", "__pycache__", ".venv", "venv"}
    excluded_suffixes = {".lock", ".min.js", ".min.css", ".map", ".sum"}
    for root, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = [d for d in dirs if d not in excluded_dirs and not d.startswith(".")]
        for fn in files:
            if any(fn.endswith(s) for s in excluded_suffixes):
                continue
            fp = Path(root) / fn
            try:
                with fp.open("rb") as f:
                    total += sum(1 for _ in f)
            except (OSError, PermissionError):
                continue
    return total


def git_head(path: Path) -> str:
    if not (path / ".git").exists():
        return "(not a git repo)"
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 else "(git error)"
    except (subprocess.SubprocessError, FileNotFoundError):
        return "(git unavailable)"


def ensure_gitignore(repo_root: Path, prompt_ok: bool) -> None:
    """Ensure .security-review/ is gitignored at repo root. Idempotent."""
    gi = repo_root / ".gitignore"
    line = ".security-review/"
    existing = ""
    if gi.exists():
        try:
            existing = gi.read_text()
        except OSError:
            print(f"warning: could not read {gi}", file=sys.stderr)
            return
        if any(l.strip() == line for l in existing.splitlines()):
            return  # already there
    if not prompt_ok:
        print(f"note: .security-review/ NOT added to {gi} (--gitignore not set)", file=sys.stderr)
        return
    try:
        with gi.open("a") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write("# security-review plugin run state\n")
            f.write(f"{line}\n")
        print(f"appended {line} to {gi}", file=sys.stderr)
    except OSError as e:
        print(f"warning: could not update {gi}: {e}", file=sys.stderr)


def check_deps() -> int:
    """Print versions of required tools. Returns 0 if all present."""
    print(f"python: {sys.version.split()[0]}", file=sys.stderr)
    ok = True
    for tool in ("git", "find", "grep", "wc"):
        try:
            r = subprocess.run([tool, "--version"], capture_output=True, text=True, timeout=3)
            ver = r.stdout.splitlines()[0] if r.stdout else r.stderr.splitlines()[0] if r.stderr else "(unknown)"
            print(f"{tool}: {ver}", file=sys.stderr)
        except (FileNotFoundError, subprocess.SubprocessError):
            print(f"{tool}: NOT FOUND", file=sys.stderr)
            ok = False
    try:
        find_templates_dir()
        print("templates/: found", file=sys.stderr)
    except FileNotFoundError as e:
        print(f"templates/: {e}", file=sys.stderr)
        ok = False
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--targets", help="Comma-separated repo paths to review")
    p.add_argument("--project-type", choices=sorted(VALID_PROJECT_TYPES))
    p.add_argument("--depth", choices=sorted(VALID_DEPTHS), default="deep")
    p.add_argument("--gitignore", action="store_true",
                   help="Append .security-review/ to .gitignore in each target repo")
    p.add_argument("--check-deps", action="store_true", help="Diagnostics only; no side effects")
    p.add_argument("--state-root", default=".",
                   help="Where to create .security-review/ (default: cwd)")
    args = p.parse_args()

    if args.check_deps:
        return check_deps()

    if not args.targets or not args.project_type:
        p.error("--targets and --project-type are required (unless --check-deps)")

    targets = [Path(t).resolve() for t in args.targets.split(",") if t.strip()]
    for t in targets:
        if not t.exists():
            print(f"error: target does not exist: {t}", file=sys.stderr)
            return 2

    state_root = Path(args.state_root).resolve()
    now = dt.datetime.now()
    run_id = f"{now:%Y%m%d-%H%M%S}-{short_hash(str(targets) + now.isoformat())}"
    run_dir = state_root / ".security-review" / run_id

    try:
        templates = find_templates_dir()
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    # Create directory tree.
    for sub in ("recon", "assignments", "worklog",
                "findings", "findings/candidates", "findings/rejected"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)

    # Copy schema and templates.
    shutil.copy(templates / "finding-SCHEMA.md", run_dir / "findings" / "SCHEMA.md")
    shutil.copy(templates / "plan.md", run_dir / "plan.md")
    shutil.copy(templates / "calibration.md", run_dir / "calibration.md")

    # Write targets.md.
    budgets = DEPTH_BUDGETS[args.depth]
    targets_lines = [
        f"# Targets — run {run_id}",
        "",
        f"Started: {now:%Y-%m-%d %H:%M:%S}",
        f"Project type: {args.project_type}",
        f"Depth: {args.depth}",
        "",
        "| Repo | Path | Commit | LOC |",
        "|---|---|---|---|",
    ]
    for t in targets:
        targets_lines.append(f"| {t.name} | `{t}` | `{git_head(t)}` | {loc_count(t)} |")
    (run_dir / "targets.md").write_text("\n".join(targets_lines) + "\n")

    # Write empty findings/INDEX.md (regenerated later by index_findings.py).
    (run_dir / "findings" / "INDEX.md").write_text(
        f"# Findings index — run {run_id}\n\n"
        "| ID | Severity | CVSS | Title | File | Status |\n"
        "|---|---|---|---|---|---|\n"
        "_(populated by index_findings.py after each phase)_\n"
    )

    # Initial progress.md.
    (run_dir / "progress.md").write_text(
        f"# Security Review Progress — run {run_id}\n\n"
        f"Started: {now:%Y-%m-%d %H:%M:%S}\n"
        f"Project type: {args.project_type} · Depth: {args.depth}\n"
        f"Targets: {len(targets)} repo(s)\n\n"
        "_(regenerated by progress.py after each phase)_\n"
    )

    # Append run-specific config to calibration.md.
    cal_path = run_dir / "calibration.md"
    cal_text = cal_path.read_text()
    cal_text = cal_text.replace("`<RUN_ID>`", f"`{run_id}`")
    cal_text += (
        "\n\n## This run's effective config\n\n"
        f"- Project type: `{args.project_type}`\n"
        f"- Depth: `{args.depth}`\n"
        f"- Hunter context budget: `{budgets['hunter_ctx']:,}` tokens\n"
        f"- Verifier context budget: `{budgets['verify_ctx']:,}` tokens\n"
        f"- Recon context budget: `{budgets['recon_ctx']:,}` tokens\n"
        f"- Parallel hunter batch: `{budgets['parallel']}`\n"
        f"- Confidence threshold for report: `0.8`\n"
    )
    cal_path.write_text(cal_text)

    # Update plan.md heading + targets list.
    plan_path = run_dir / "plan.md"
    plan_text = plan_path.read_text()
    plan_text = plan_text.replace("`<RUN_ID>`", f"`{run_id}`")
    plan_text = plan_text.replace("`<START_TS>`", f"`{now:%Y-%m-%d %H:%M:%S}`")
    plan_text = plan_text.replace("`<PROJECT_TYPE>`", f"`{args.project_type}`")
    plan_text = plan_text.replace("`<DEPTH>`", f"`{args.depth}`")
    targets_block = "\n".join(
        f"> - `{t}` (commit `{git_head(t)}`, {loc_count(t)} LOC)" for t in targets
    )
    plan_text = re.sub(
        r"> Targets:\n(?:> - .*?\n)+",
        f"> Targets:\n{targets_block}\n",
        plan_text,
        count=1,
    )
    plan_path.write_text(plan_text)

    # Optional .gitignore handling per-repo.
    if args.gitignore:
        for t in targets:
            ensure_gitignore(t, prompt_ok=True)

    print(f"run dir: {run_dir}", file=sys.stderr)
    print(run_id)  # stdout
    return 0


if __name__ == "__main__":
    sys.exit(main())
