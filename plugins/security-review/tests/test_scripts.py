"""Tests for the security-review plugin scripts.

Run from the repo root:
    python3 -m unittest discover -s plugins/security-review/tests -v

Stdlib only, Python 3.8+. Each test runs the scripts as subprocesses (their real CLI) inside a
temporary directory, with CLAUDE_PLUGIN_ROOT unset so template lookup uses the checkout.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import List

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = PLUGIN_ROOT / "scripts"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

PLAN_TABLE_HEADER = (
    "| Phase | Step | Agent | Assignment | Status | Started | Finished | Notes |\n"
    "|---|---|---|---|---|---|---|---|\n"
)


def run(script: str, args: List[str], cwd: Path, env_extra=None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_ROOT"}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script)] + args,
        cwd=str(cwd), capture_output=True, text=True, env=env, timeout=60,
    )


class TempDirTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="sr-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def make_run_dir(self, fixture: str = "") -> Path:
        """A bare run dir with findings/ tree, optionally seeded from a fixture dir."""
        run_dir = self.tmp / ".security-review" / "20260101-000000-abcdef"
        for sub in ("findings/candidates", "findings/rejected", "worklog"):
            (run_dir / sub).mkdir(parents=True, exist_ok=True)
        if fixture:
            for fp in (FIXTURES / fixture).glob("*.md"):
                shutil.copy(fp, run_dir / "findings" / fp.name)
        return run_dir


class InitRunTest(TempDirTest):
    def init(self, *extra: str) -> subprocess.CompletedProcess:
        (self.tmp / "repo").mkdir(exist_ok=True)
        (self.tmp / "repo" / "app.py").write_text("print('hi')\n")
        return run("init_run.py", ["--targets", "repo", "--project-type", "internal", *extra], self.tmp)

    def test_creates_run_tree(self) -> None:
        r = self.init("--depth", "quick")
        self.assertEqual(r.returncode, 0, r.stderr)
        run_id = r.stdout.strip()
        self.assertRegex(run_id, r"^\d{8}-\d{6}-[0-9a-f]{6}$")
        run_dir = self.tmp / ".security-review" / run_id
        for rel in ("recon", "assignments", "worklog", "findings/candidates", "findings/rejected",
                    "findings/SCHEMA.md", "findings/INDEX.md", "plan.md", "calibration.md",
                    "targets.md", "progress.md", "worklog/manager.md"):
            self.assertTrue((run_dir / rel).exists(), rel)

    def test_plan_has_no_placeholder_rows(self) -> None:
        r = self.init()
        plan = (self.tmp / ".security-review" / r.stdout.strip() / "plan.md").read_text()
        self.assertNotIn("<RUN_ID>", plan)
        self.assertNotIn("<MODEL_PLAN>", plan)
        self.assertNotIn("<repo-1>", plan)
        self.assertNotIn("<TARGET_1>", plan)
        rows = plan.split(PLAN_TABLE_HEADER, 1)[1].split("\n\n", 1)[0].strip().splitlines()
        self.assertEqual(len(rows), 1, rows)
        self.assertIn("| 1 | scoping | (manager) |", rows[0])
        self.assertIn("| done |", rows[0])

    def test_default_depth_is_standard_with_model_plan(self) -> None:
        r = self.init()
        self.assertEqual(r.returncode, 0, r.stderr)
        run_dir = self.tmp / ".security-review" / r.stdout.strip()
        cal = (run_dir / "calibration.md").read_text()
        self.assertIn("- Depth: `standard`", cal)
        self.assertIn("| sr-injection-hunter | `sonnet` |", cal)
        self.assertIn("| sr-authnz-hunter | (omit — agent default) |", cal)
        self.assertIn("Never overridden: sr-threat-modeller, sr-verifier, sr-triage, sr-chain-composer", cal)

    def test_model_plan_by_depth(self) -> None:
        for depth, expect_sonnet, expect_default in (
            ("quick", "sr-businesslogic-hunter", None),
            ("deep", None, "sr-injection-hunter"),
            ("exhaustive", None, "sr-web-hunter"),
        ):
            with self.subTest(depth=depth):
                r = self.init("--depth", depth)
                self.assertEqual(r.returncode, 0, r.stderr)
                cal = (self.tmp / ".security-review" / r.stdout.strip() / "calibration.md").read_text()
                if expect_sonnet:
                    self.assertIn(f"| {expect_sonnet} | `sonnet` |", cal)
                if expect_default:
                    self.assertIn(f"| {expect_default} | (omit — agent default) |", cal)
                    self.assertNotIn("`sonnet` |", cal)

    def test_rejects_unresolved_type(self) -> None:
        (self.tmp / "repo").mkdir()
        for t in ("unsure", "infer"):
            r = run("init_run.py", ["--targets", "repo", "--project-type", t], self.tmp)
            self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.tmp / ".security-review").exists())

    def test_missing_target(self) -> None:
        r = run("init_run.py", ["--targets", "nope", "--project-type", "poc"], self.tmp)
        self.assertEqual(r.returncode, 2)
        self.assertIn("does not exist", r.stderr)

    def test_gitignore_idempotent(self) -> None:
        self.init("--gitignore")
        self.init("--gitignore")
        gi = (self.tmp / "repo" / ".gitignore").read_text().splitlines()
        self.assertEqual(gi.count(".security-review/"), 1)

    def test_check_deps(self) -> None:
        r = run("init_run.py", ["--check-deps"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("templates/: found", r.stderr)
        self.assertFalse((self.tmp / ".security-review").exists())


class ValidateFindingsTest(TempDirTest):
    def test_valid_findings_pass_strict(self) -> None:
        run_dir = self.make_run_dir("valid")
        r = run("validate_findings.py", ["--run-dir", str(run_dir), "--strict"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ok    SR-2026-001.md", r.stdout)
        self.assertIn("ok    SR-2026-003-CHAIN.md", r.stdout)

    def test_body_with_markdown_table_is_not_truncated(self) -> None:
        # Regression: the body used to be cut at the first "---" (table separator), so every
        # section before it was reported missing.
        run_dir = self.make_run_dir("valid")
        r = run("validate_findings.py", ["--run-dir", str(run_dir)], self.tmp)
        self.assertNotIn("missing section", r.stdout)

    def test_malformed_finding_fails(self) -> None:
        run_dir = self.make_run_dir("invalid")
        r = run("validate_findings.py", ["--run-dir", str(run_dir)], self.tmp)
        self.assertEqual(r.returncode, 2)
        line = next(l for l in r.stdout.splitlines() if "SR-2026-020.md" in l)
        self.assertTrue(line.startswith("fail"), line)
        for expected in ("missing frontmatter keys", "cvss vector malformed",
                         "confidence out of range", "missing section: ## Exploit scenario"):
            self.assertIn(expected, line)

    def test_severity_band_mismatch_warns_and_fails_strict(self) -> None:
        run_dir = self.make_run_dir()
        shutil.copy(FIXTURES / "invalid" / "SR-2026-021.md", run_dir / "findings")
        r = run("validate_findings.py", ["--run-dir", str(run_dir)], self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("warn  SR-2026-021.md", r.stdout)
        self.assertIn("severity='critical' but cvss score 7.5", r.stdout)
        r = run("validate_findings.py", ["--run-dir", str(run_dir), "--strict"], self.tmp)
        self.assertEqual(r.returncode, 2)

    def test_chain_without_constituents_fails(self) -> None:
        run_dir = self.make_run_dir()
        text = (FIXTURES / "valid" / "SR-2026-003-CHAIN.md").read_text()
        text = re.sub(r"chain_constituents:\n(  - .*\n)+", "chain_constituents: []\n", text)
        (run_dir / "findings" / "SR-2026-003-CHAIN.md").write_text(text)
        r = run("validate_findings.py", ["--run-dir", str(run_dir)], self.tmp)
        self.assertIn("CHAIN finding has empty chain_constituents list", r.stdout)

    def test_run_id_resolves_under_cwd(self) -> None:
        run_dir = self.make_run_dir("valid")
        r = run("validate_findings.py", ["--run", run_dir.name], self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class DedupeTest(TempDirTest):
    def test_merges_duplicates_only(self) -> None:
        run_dir = self.make_run_dir("dupes")
        r = run("dedupe.py", ["--run-dir", str(run_dir)], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        summary = (run_dir / "triage-summary.md").read_text()
        self.assertIn("Suggested merges: 1.", summary)
        self.assertIn("### Cluster 1 — 2 findings", summary)
        self.assertIn("`SR-2026-010`", summary)
        self.assertIn("`SR-2026-011`", summary)
        self.assertNotIn("`SR-2026-012`", summary)
        self.assertIn("keep `SR-2026-010`", summary)

    def test_rerun_replaces_section_and_keeps_triage_decisions(self) -> None:
        run_dir = self.make_run_dir("dupes")
        run("dedupe.py", ["--run-dir", str(run_dir)], self.tmp)
        with (run_dir / "triage-summary.md").open("a") as f:
            f.write("\n## Triage decisions\nKept SR-2026-010.\n")
        run("dedupe.py", ["--run-dir", str(run_dir)], self.tmp)
        summary = (run_dir / "triage-summary.md").read_text()
        self.assertEqual(summary.count("## Dedup suggestions"), 1)
        self.assertIn("## Triage decisions\nKept SR-2026-010.", summary)


class IndexFindingsTest(TempDirTest):
    def test_index_sorted_with_lines(self) -> None:
        run_dir = self.make_run_dir("valid")
        r = run("index_findings.py", ["--run-dir", str(run_dir)], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        index = (run_dir / "findings" / "INDEX.md").read_text()
        rows = [l for l in index.splitlines() if l.startswith("| `SR-")]
        self.assertEqual([r.split("`")[1] for r in rows],
                         ["SR-2026-003-CHAIN", "SR-2026-001", "SR-2026-002"])
        # Regression: `lines:` was only found when it was the last frontmatter line.
        self.assertIn("`app/routes/users.py:47-58`", index)
        self.assertIn("`app/templates/error.html:12`", index)


class CompileReportTest(TempDirTest):
    def test_report_from_fixtures(self) -> None:
        run_dir = self.make_run_dir("valid")
        (run_dir / "targets.md").write_text("# Targets — run x\n\n| Repo | Path |\n|---|---|\n| api | `/x` |\n")
        (run_dir / "findings" / "rejected" / "inj-001-2.md").write_text("rejected\n")
        r = run("compile_report.py", ["--run-dir", str(run_dir), "--include-rejected"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        report = (run_dir / "report.md").read_text()
        for heading in ("## 1. Executive Summary", "## 2. Scope", "## 3. Methodology",
                        "## 4. Findings by Severity", "## 4b. Composed Exploit Chains",
                        "## 5. Detailed Findings", "## 7. Suppressed / Excluded Findings",
                        "## 8. Coverage Gaps", "## 9. Appendices"):
            self.assertIn(heading, report)
        self.assertIn("| Critical | 1 |", report)
        self.assertIn("| High | 1 |", report)
        self.assertIn("| Medium | 1 |", report)
        self.assertIn("### `SR-2026-001` — SQL injection in user search", report)
        self.assertIn("File: `app/routes/users.py:47-58`", report)
        self.assertIn("### `SR-2026-003-CHAIN`", report)
        self.assertIn("Verifier rejected: **1**", report)
        self.assertIn("`findings/rejected/inj-001-2.md`", report)
        self.assertIn("| api | `/x` |", report)
        # Frontmatter must not leak into the report body.
        self.assertNotIn("cvss_v3_1_vector:", report)

    def test_missing_findings_dir(self) -> None:
        r = run("compile_report.py", ["--run-dir", str(self.tmp / "nope")], self.tmp)
        self.assertEqual(r.returncode, 2)


class ProgressTest(TempDirTest):
    def test_renders_counts_and_manager_notes(self) -> None:
        (self.tmp / "repo").mkdir()
        r = run("init_run.py", ["--targets", "repo", "--project-type", "production"], self.tmp)
        run_id = r.stdout.strip()
        run_dir = self.tmp / ".security-review" / run_id
        plan = run_dir / "plan.md"
        plan.write_text(plan.read_text().replace(
            "calibration written |\n",
            "calibration written |\n"
            "| 2 | recon | sr-recon | recon/repo.md | done | | | |\n"
            "| 4 | hunt | sr-injection-hunter | assignments/inj-001.md | running | | | model=sonnet |\n"
            "| 4 | hunt | sr-authnz-hunter | assignments/authnz-001.md | failed | | | retry 1/2 |\n",
        ))
        shutil.copy(FIXTURES / "valid" / "SR-2026-001.md", run_dir / "findings")
        (run_dir / "findings" / "candidates" / "inj-001-1.md").write_text("x\n")
        with (run_dir / "worklog" / "manager.md").open("a") as f:
            f.write("- 2026-01-01 00:00:00 phase 4 batch 1/2: 1 candidates, 0 dropped\n")

        r = run("progress.py", ["--run", run_id], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn(f"# Security Review Progress — run {run_id}", out)
        self.assertIn("| 4 | 0 | 1 | 0 | 0 | 1 | 0 |", out)
        self.assertIn("Candidates (pre-verification): **1**", out)
        self.assertIn("Confirmed (post-verification): **1**", out)
        self.assertIn("## In-flight", out)
        self.assertIn("## Failed", out)
        self.assertIn("## Recent manager notes", out)
        self.assertIn("phase 4 batch 1/2", out)
        # Regression: the plan's status legend used to leak into the dashboard header.
        self.assertNotIn("Status legend", out)
        self.assertEqual((run_dir / "progress.md").read_text(), out)

    def test_most_recent_run_and_no_runs(self) -> None:
        r = run("progress.py", [], self.tmp)
        self.assertEqual(r.returncode, 2)
        self.make_run_dir()
        r = run("progress.py", ["--check"], self.tmp)
        self.assertEqual(r.returncode, 0)
        self.assertTrue(r.stdout.strip().endswith("20260101-000000-abcdef"))


class ReplayTest(TempDirTest):
    def test_lists_redispatch_candidates_including_partial(self) -> None:
        run_dir = self.make_run_dir()
        (run_dir / "plan.md").write_text(
            "# Plan\n\n" + PLAN_TABLE_HEADER
            + "| 4 | hunt | sr-injection-hunter | assignments/inj-001.md | done | | | |\n"
            + "| 4 | hunt | sr-web-hunter | assignments/web-001.md | partial | | | |\n"
            + "| 4 | hunt | sr-authnz-hunter | assignments/authnz-001.md | failed | | | |\n"
            + "| 5 | verify | sr-verifier | findings/candidates/inj-001-1.md | pending | | | |\n"
        )
        (run_dir / "findings" / "candidates" / "inj-001-1.md").write_text("x\n")
        r = run("replay.py", ["--run-dir", str(run_dir), "--check-consistency"], self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("## Re-dispatch candidates (3)", r.stdout)
        self.assertIn("assignments/web-001.md", r.stdout)
        self.assertNotIn("inj-001.md` · status=done", r.stdout)
        self.assertIn("Orphan candidates (not yet verified): 1", r.stdout)


class HelpTest(unittest.TestCase):
    def test_every_script_has_help(self) -> None:
        for script in sorted(SCRIPTS.glob("*.py")):
            with self.subTest(script=script.name):
                r = subprocess.run([sys.executable, str(script), "--help"],
                                   capture_output=True, text=True, timeout=30)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("usage:", r.stdout)


if __name__ == "__main__":
    unittest.main()
