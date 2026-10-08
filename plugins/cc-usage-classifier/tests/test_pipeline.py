"""End-to-end and unit tests for cc-usage-classifier (stdlib unittest only).

Run: python3 -m unittest discover -s plugins/cc-usage-classifier/tests -v
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent / "skills" / "cc-usage-classifier"
SCRIPTS = SKILL / "scripts"
FIXTURE = HERE / "fixtures" / "projects"
PRICING = SKILL / "pricing.json"

sys.path.insert(0, str(SCRIPTS))
import cost  # noqa: E402
import extract  # noqa: E402

OPUS_SID, HAIKU_SID = "sess-opus55", "sess-haiku"
UNKNOWN_SID, SONNET_SID = "sess-unknown", "sess-sonnet"


def run(script, *args, home=None):
    env = dict(os.environ)
    if home:
        env["HOME"] = home  # keep default paths away from the real ~/.claude
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args],
                          capture_output=True, text=True, env=env)


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ccuc-test-")
        self.out = Path(self.tmp) / "out"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def extract(self, *extra, projects=FIXTURE):
        p = run("extract.py", "--projects-dir", str(projects),
                "--out-dir", str(self.out), *extra, home=self.tmp)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def records(self, name="extracted.jsonl"):
        return {r["session_id"]: r for r in map(
            json.loads, (self.out / name).read_text().splitlines())}

    # --- extraction -------------------------------------------------------

    def test_extract_runs_and_writes_outputs(self):
        res = self.extract()
        self.assertEqual(res["total"], 4)
        self.assertEqual(sorted(res["todo"]),
                         sorted([OPUS_SID, HAIKU_SID, UNKNOWN_SID, SONNET_SID]))
        self.assertTrue((self.out / "extracted.jsonl").exists())
        self.assertTrue((self.out / "logs" / "extract.log").exists())

    def test_dedup_by_request_id(self):
        self.extract()
        r = self.records()[OPUS_SID]
        toks = r["tokens_by_model"]["claude-opus-5-5"]
        # req_dup appears twice with identical usage: counted once
        self.assertEqual(toks["input"], 110)
        self.assertEqual(toks["output"], 70)
        self.assertEqual(toks["cache_read"], 3000)
        self.assertEqual(toks["cache_write_5m"], 200)
        self.assertEqual(toks["cache_write"], 300)
        self.assertEqual(r["assistant_turn_count"], 2)

    def test_long_context_split_is_per_request(self):
        self.extract()
        r = self.records()[HAIKU_SID]
        self.assertEqual(r["long_context_thresholds"], [100000])
        lc = r["long_context_tokens_by_model"]["claude-haiku-5-5-20260901"]
        # only the 125K-prompt request is over the threshold
        self.assertEqual(lc["100000"],
                         {"input": 5000, "output": 1000, "cache_read": 120000})

    def test_date_range_filtering(self):
        res = self.extract("--since", "2026-09-01", "--until", "2026-09-15")
        self.assertEqual(res["todo"], [OPUS_SID])
        self.assertEqual(res["range"]["skipped_out_of_range"], 3)
        res = self.extract("--until", "2026-09-20")  # date-only: whole day
        self.assertIn(HAIKU_SID, res["todo"])
        self.assertNotIn(SONNET_SID, res["todo"])

    # --- cost -------------------------------------------------------------

    def test_cost_per_model_including_5x_and_long_context(self):
        self.extract()
        pricing = cost.load_pricing(PRICING)
        recs = self.records()
        opus = cost.price_record(recs[OPUS_SID], pricing)
        # 110*4 + 70*20 + 3000*0.2 + 200*5 + 300*5 (unsplit write @5m rate)
        self.assertAlmostEqual(opus["session_cost_usd"], 4940 / 1e6, places=9)
        self.assertEqual(opus["cost_by_model"]["claude-opus-5-5"]["priced_as"],
                         "claude-opus-5-5")
        haiku = cost.price_record(recs[HAIKU_SID], pricing)
        # long request at 0.5/2.5/0.05: 2500 + 2500 + 6000;
        # short request at 0.1/0.5: 100 + 100
        self.assertAlmostEqual(haiku["session_cost_usd"], 11200 / 1e6, places=9)
        cbm = haiku["cost_by_model"]["claude-haiku-5-5-20260901"]
        self.assertEqual(cbm["priced_as"], "claude-haiku-5-5")
        self.assertEqual(cbm["long_context_tokens"]["cache_read"], 120000)
        unk = cost.price_record(recs[UNKNOWN_SID], pricing)
        self.assertEqual(unk["unpriced_models"], ["claude-zeta-9"])
        self.assertEqual(unk["session_cost_usd"], 0.0)

    def test_long_context_warns_when_not_split(self):
        rec = {"tokens_by_model": {"claude-haiku-5-5": {"input": 1000}}}
        warnings = []
        cost.price_record(rec, cost.load_pricing(PRICING), warnings.append)
        self.assertTrue(any("long-context tier" in w for w in warnings))
        self.assertAlmostEqual(rec["session_cost_usd"], 1000 * 0.1 / 1e6)

    def test_unknown_5x_version_is_not_guessed(self):
        pricing = cost.load_pricing(PRICING)
        self.assertEqual(cost.lookup_rates("claude-opus-5-7", pricing),
                         (None, None))
        self.assertEqual(cost.family_key("claude-opus-5-7"), "claude-opus-5-x")
        self.assertEqual(cost.lookup_rates("claude-opus-5-20260101", pricing)[1],
                         "claude-opus-5")

    # --- report -----------------------------------------------------------

    def test_report_puts_unpriced_warning_at_top(self):
        self.extract()
        p = run("report.py", "--out-dir", str(self.out),
                "--pricing", str(PRICING), home=self.tmp)
        self.assertEqual(p.returncode, 0, p.stderr)
        head = "\n".join((self.out / "summary.md").read_text()
                         .splitlines()[:8])
        self.assertIn("WARNING", head)
        self.assertIn("claude-zeta-9", head)
        self.assertTrue((self.out / "logs" / "report.log").exists())
        sessions = self.records("sessions.jsonl")
        self.assertAlmostEqual(sessions[HAIKU_SID]["session_cost_usd"],
                               11200 / 1e6, places=9)

    def test_report_no_banner_when_unpriced_share_small(self):
        self.extract("--since", "2026-09-01")  # excludes the unknown model
        run("report.py", "--out-dir", str(self.out),
            "--pricing", str(PRICING), home=self.tmp)
        self.assertNotIn("WARNING", (self.out / "summary.md").read_text())

    # --- argument handling ------------------------------------------------

    def test_args_string_parses_quoted_values(self):
        a = extract.parse_args(["--args-string",
                                '--projects-dir "/data/my projects" '
                                '--last-days 7 --jira-projects ABC,DEF'])
        self.assertEqual(a.projects_dir, "/data/my projects")
        self.assertEqual(a.last_days, 7.0)
        self.assertEqual(a.jira_projects, "ABC,DEF")

    def test_args_rejects_unknown_flags_and_metacharacters(self):
        bad = [
            "--evil 1",
            "--last-days 7 ; touch PWNED",
            "--since 2026-01-01 $(touch PWNED)",
            "--projects-dir '/x;touch PWNED'",
            "--out-dir '$HOME/out'",
            "--jira-projects '`id`'",
            "--last-days abc",
            "--since yesterday",
            "--pricing /etc/passwd",
            "--proj /x",  # abbreviations are disabled
            "--last-days 7 'unterminated",
            "--out-dir '/x\n'",  # trailing newline must not pass the path check
            "--jira-projects 'ABC\n'",
        ]
        for s in bad:
            with self.subTest(s=s):
                with self.assertRaises(SystemExit) as cm, \
                        contextlib.redirect_stderr(io.StringIO()):
                    extract.parse_args(["--args-string", s])
                self.assertNotEqual(cm.exception.code, 0)

    def test_args_file_text_never_reaches_a_shell(self):
        marker = Path(self.tmp) / "PWNED"
        argf = Path(self.tmp) / "args.txt"
        argf.write_text(f"--last-days 7 $(touch {marker}) `touch {marker}`")
        p = run("extract.py", "--args-file", str(argf), "--check-args",
                home=self.tmp)
        self.assertEqual(p.returncode, 2)
        self.assertFalse(marker.exists())
        argf.write_text("--last-days 7 --redact-prompts\n")
        p = run("extract.py", "--args-file", str(argf), "--check-args",
                home=self.tmp)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(json.loads(p.stdout)["redact_prompts"])

    # --- batching + validation -------------------------------------------

    def _many_sessions(self, n):
        proj = Path(self.tmp) / "projects" / "-home-dev-many"
        proj.mkdir(parents=True)
        for i in range(n):
            entries = [
                {"type": "user", "timestamp": "2026-09-01T00:00:00Z",
                 "cwd": "/home/dev/many",
                 "message": {"role": "user", "content": f"task {i}"}},
                {"type": "assistant", "timestamp": "2026-09-01T00:00:01Z",
                 "requestId": f"r{i}",
                 "message": {"model": "claude-sonnet-5", "content": [],
                             "usage": {"input_tokens": 10,
                                       "output_tokens": 5}}},
            ]
            (proj / f"s{i:03d}.jsonl").write_text(
                "\n".join(json.dumps(e) for e in entries))
        return proj.parent

    def test_batches_hold_at_most_20_sessions(self):
        res = self.extract(projects=self._many_sessions(45))
        self.assertEqual(len(res["batches"]), 3)
        sizes = [len(json.loads(Path(b).read_text())) for b in res["batches"]]
        self.assertEqual(sizes, [20, 20, 5])
        self.assertTrue(all(Path(b).name.startswith("batch-")
                            for b in res["batches"]))

    def test_batch_byte_cap(self):
        out = Path(self.tmp) / "o"
        recs = {f"s{i}": {"classifier_payload": {"x": "y" * 1000},
                          "outcome_signals": {}, "identifier_candidates": [],
                          "features": {}, "repo": {}, "branch": {}}
                for i in range(5)}
        paths = extract.write_batches(out, recs, list(recs), 20,
                                      max_bytes=2500)
        sizes = [len(json.loads(Path(p).read_text())) for p in paths]
        self.assertEqual(sum(sizes), 5)
        self.assertTrue(all(s <= 2 for s in sizes))

    def test_validator_flags_missing_and_malformed(self):
        res = self.extract()
        cdir = Path(res["classifications_dir"])
        good = {"tags": ["engineering", "debugging"], "summary": "Fixed it.",
                "outcome": "successful", "outcome_confidence": 0.8,
                "outcome_justification": "commit_created", "identifiers": []}
        (cdir / f"{OPUS_SID}.json").write_text(json.dumps(good))
        (cdir / f"{HAIKU_SID}.json").write_text(
            json.dumps(dict(good, tags=["made-up-tag"])))
        (cdir / f"{SONNET_SID}.json").write_text("{not json")
        p = run("validate.py", "--out-dir", str(self.out),
                "--write-retry-batch", home=self.tmp)
        self.assertEqual(p.returncode, 0, p.stderr)
        v = json.loads(p.stdout)
        self.assertEqual(v["ok"], 1)
        self.assertEqual(v["missing"], [UNKNOWN_SID])
        self.assertEqual(sorted(v["malformed"]), [HAIKU_SID, SONNET_SID])
        self.assertIn("made-up-tag", v["malformed"][HAIKU_SID])
        retry = json.loads(Path(v["retry_batches"][0]).read_text())
        self.assertEqual(sorted(i["session_id"] for i in retry),
                         sorted([UNKNOWN_SID, HAIKU_SID, SONNET_SID]))
        # quarantine -> report falls back deterministically, never crashes
        run("validate.py", "--out-dir", str(self.out), "--quarantine",
            home=self.tmp)
        self.assertFalse((cdir / f"{SONNET_SID}.json").exists())
        p = run("report.py", "--out-dir", str(self.out),
                "--pricing", str(PRICING), home=self.tmp)
        self.assertEqual(p.returncode, 0, p.stderr)
        s = self.records("sessions.jsonl")
        self.assertEqual(s[OPUS_SID]["classification"]["source"], "subagent")
        self.assertEqual(s[SONNET_SID]["classification"]["source"],
                         "deterministic")

    def test_rerun_drops_stale_classification_for_changed_session(self):
        res = self.extract()
        cdir = Path(res["classifications_dir"])
        (cdir / f"{OPUS_SID}.json").write_text(json.dumps(
            {"tags": ["engineering"], "summary": "old", "outcome": "unknown",
             "outcome_confidence": 0.2}))
        res = self.extract("--no-incremental")
        self.assertIn(OPUS_SID, res["todo"])
        self.assertFalse((cdir / f"{OPUS_SID}.json").exists())


if __name__ == "__main__":
    unittest.main()
