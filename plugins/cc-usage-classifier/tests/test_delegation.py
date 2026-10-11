"""Subagent / workflow accounting tests for cc-usage-classifier.

Uses the shared delegation fixture generator at <repo>/tests/fixtures/
make_delegation_fixture.py (one session with subagents, a nested agent, a
3-agent workflow run and a legacy top-level agent file, plus a plain session).

Run: python3 -m unittest discover -s plugins/cc-usage-classifier/tests -v
"""

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
PRICING = SKILL / "pricing.json"
REPO_FIXTURES = HERE.parents[2] / "tests" / "fixtures"

sys.path.insert(0, str(SCRIPTS))
import cost  # noqa: E402

fx = None
if (REPO_FIXTURES / "make_delegation_fixture.py").exists():
    sys.path.insert(0, str(REPO_FIXTURES))
    import make_delegation_fixture as fx  # noqa: E402

SKIP_MSG = ("delegation fixture generator not found at %s "
            "(tests/fixtures/make_delegation_fixture.py at the repo root)" % REPO_FIXTURES)


def run(script, *args, home):
    env = dict(os.environ, HOME=home)
    return subprocess.run([sys.executable, str(SCRIPTS / script), *args],
                          capture_output=True, text=True, env=env)


@unittest.skipIf(fx is None, SKIP_MSG)
class DelegationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ccuc-deleg-")
        self.projects = Path(self.tmp) / "projects"
        self.root = fx.build(self.projects)
        self.out = Path(self.tmp) / "out"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def extract(self, *extra):
        p = run("extract.py", "--projects-dir", str(self.projects),
                "--out-dir", str(self.out), *extra, home=self.tmp)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def records(self, name="extracted.jsonl"):
        return {r["session_id"]: r for r in map(
            json.loads, (self.out / name).read_text().splitlines())}

    def orch(self):
        self.extract()
        return self.records()[fx.SID]

    # --- sessions ---------------------------------------------------------

    def test_agent_files_are_not_sessions(self):
        res = self.extract()
        self.assertEqual(res["total"], fx.EXPECTED["sessions"])
        self.assertEqual(sorted(res["todo"]), sorted([fx.SID, fx.PLAIN_SID]))
        self.assertFalse(any(s.startswith("agent-") for s in res["todo"]))

    def test_legacy_top_level_agent_attached(self):
        r = self.orch()
        legacy = [a for a in r["agents"] if a["agent_id"] == "alegacy1"]
        self.assertEqual(len(legacy), 1)
        self.assertEqual(legacy[0]["models"], ["claude-haiku-4-5"])
        self.assertIn("claude-haiku-4-5", r["tokens_by_model"])
        self.assertEqual(len(r["agents"]), fx.EXPECTED["agents"])

    # --- tokens + cost ----------------------------------------------------

    def test_per_model_tokens_include_agents_deduped(self):
        r = self.orch()
        per = fx.EXPECTED["tokens_per_request"]
        expected = {m: {k: n * v for k, v in per.items()}
                    for m, n in fx.EXPECTED["requests_by_model"].items()}
        self.assertEqual(r["tokens_by_model"], expected)
        # req_m1 is replayed inside workflow agent awf1: counted once, on main
        self.assertEqual(r["request_count"],
                         sum(fx.EXPECTED["requests_by_model"].values()))
        self.assertEqual(r["assistant_turn_count"], fx.EXPECTED["main_thread_requests"])
        awf1 = next(a for a in r["agents"] if a["agent_id"] == "awf1")
        self.assertEqual(awf1["models"], ["claude-sonnet-5-5"])
        self.assertEqual(awf1["requests"], 1)

    def test_tokens_by_agent_source_partition(self):
        r = self.orch()
        src = r["tokens_by_agent_source"]
        self.assertEqual(set(src), {"main", "agent", "workflow", "team"})
        self.assertEqual(src["main"], {"claude-opus-5-5":
                                       {"input": 400, "output": 200, "cache_read": 4000}})
        # aexp1 sonnet x2, anest1 haiku-5-5 x1, alegacy1 haiku-4-5 x1
        self.assertEqual(src["agent"]["claude-sonnet-5-5"]["input"], 200)
        self.assertEqual(src["agent"]["claude-haiku-5-5"]["input"], 100)
        self.assertEqual(src["agent"]["claude-haiku-4-5"]["input"], 100)
        # awf1 + awf2 sonnet, awf3 haiku-5-5
        self.assertEqual(src["workflow"], {
            "claude-sonnet-5-5": {"input": 200, "output": 100, "cache_read": 2000},
            "claude-haiku-5-5": {"input": 100, "output": 50, "cache_read": 1000}})
        self.assertEqual(src["team"], {})
        # sources partition the session total
        summed = {}
        for by_model in src.values():
            for m, t in by_model.items():
                for k, v in t.items():
                    summed.setdefault(m, {}).setdefault(k, 0)
                    summed[m][k] += v
        self.assertEqual(summed, r["tokens_by_model"])

    def test_cost_includes_agents_at_per_model_rates(self):
        r = self.orch()
        pricing = cost.load_pricing(PRICING)
        per = fx.EXPECTED["tokens_per_request"]
        want = 0.0
        for model, n in fx.EXPECTED["requests_by_model"].items():
            rates = pricing[model]
            want += n * sum(per[k] * rates[k] for k in per) / 1e6
        cost.price_record(r, pricing)
        self.assertAlmostEqual(r["session_cost_usd"], want, places=9)
        self.assertEqual(r["unpriced_models"], [])
        by_src = r["cost_by_agent_source"]
        self.assertAlmostEqual(sum(by_src.values()), r["session_cost_usd"], places=6)
        main = 4 * sum(per[k] * pricing["claude-opus-5-5"][k] for k in per) / 1e6
        self.assertAlmostEqual(by_src["main"], main, places=9)
        self.assertAlmostEqual(r["delegated_cost_usd"], want - main, places=6)
        self.assertAlmostEqual(sum(a["cost_usd"] for a in r["agents"]),
                               r["delegated_cost_usd"], places=6)
        self.assertAlmostEqual(sum(r["cost_by_agent_type"].values()),
                               r["delegated_cost_usd"], places=6)
        wf = r["workflow_runs"][0]
        self.assertAlmostEqual(wf["cost_usd"], by_src["workflow"], places=9)

    def test_long_context_split_per_agent(self):
        path = self.root / fx.SID / "subagents" / "agent-anest1.jsonl"
        lines = [json.loads(l) for l in path.read_text().splitlines()]
        for e in lines:
            if e.get("type") == "assistant":
                e["message"]["usage"]["cache_read_input_tokens"] = 150000
        path.write_text("".join(json.dumps(e) + "\n" for e in lines))
        r = self.orch()
        pricing = cost.load_pricing(PRICING)
        cost.price_record(r, pricing)
        anest = next(a for a in r["agents"] if a["agent_id"] == "anest1")
        lc = pricing["claude-haiku-5-5"]["long_context"]
        want = (100 * lc["input"] + 50 * lc["output"] + 150000 * lc["cache_read"]) / 1e6
        self.assertAlmostEqual(anest["cost_usd"], want, places=9)
        self.assertAlmostEqual(sum(r["cost_by_agent_source"].values()),
                               r["session_cost_usd"], places=6)

    # --- workflows + features --------------------------------------------

    def test_workflow_runs_and_features(self):
        r = self.orch()
        runs = {w["run_id"]: w for w in r["workflow_runs"]}
        self.assertEqual({k: {"name": v["name"], "agents": v["agents"]}
                          for k, v in runs.items()}, fx.EXPECTED["workflow_runs"])
        wf = runs[fx.RUN_ID]
        self.assertEqual(wf["phases"], ["Find", "Verify"])
        self.assertEqual(wf["models"], ["claude-haiku-5-5", "claude-sonnet-5-5"])
        f = r["features"]
        self.assertTrue(f["workflows_used"])
        self.assertEqual(f["workflow_runs"], 1)
        self.assertTrue(f["nested_agents"])
        self.assertEqual(f["max_spawn_depth"], fx.EXPECTED["max_spawn_depth"])
        self.assertTrue(f["subagents_used"])
        self.assertEqual(f["subagent_count"], fx.EXPECTED["agents"])
        self.assertEqual(f["agent_tool_calls"], 2)  # main tu_a1 + aexp1 tu_s2
        self.assertEqual(f["agents_by_source"], fx.EXPECTED["agents_by_source"])
        self.assertEqual(f["background_agents"], fx.EXPECTED["background_agents"])
        self.assertFalse(f["agent_teams_used"])
        plain = self.records()[fx.PLAIN_SID]
        self.assertFalse(plain["features"]["subagents_used"])
        self.assertEqual(plain["agents"], [])
        self.assertNotIn("delegation", plain["classifier_payload"])

    def test_agent_teams_from_meta(self):
        meta = self.root / fx.SID / "subagents" / "agent-aexp1.meta.json"
        m = json.loads(meta.read_text())
        m["teamName"] = "payments-crew"
        meta.write_text(json.dumps(m))
        r = self.orch()
        self.assertTrue(r["features"]["agent_teams_used"])
        self.assertEqual(r["features"]["agent_teams"], ["payments-crew"])
        self.assertEqual(r["features"]["agent_teams_detection"], "meta")
        self.assertIn("claude-sonnet-5-5", r["tokens_by_agent_source"]["team"])

    # --- prompts + payload ------------------------------------------------

    def test_human_prompts_exclude_harness_entries(self):
        r = self.orch()
        self.assertEqual(r["human_prompt_count"], fx.EXPECTED["human_prompts"])
        p = r["classifier_payload"]
        self.assertEqual(len(p["human_prompts"]), fx.EXPECTED["human_prompts"])
        blob = json.dumps(p["human_prompts"])
        for marker in ("task-notification", "agent-message", "Another Claude session",
                       "Stop hook feedback", "Base directory for this skill",
                       "Map src/payments", "Find races"):
            self.assertNotIn(marker, blob)
        # agent text never leaks into the main-thread snippets
        self.assertNotIn("(context)", json.dumps(p["assistant_snippets"]))

    def test_payload_has_delegation_summary(self):
        d = self.orch()["classifier_payload"]["delegation"]
        self.assertEqual(d["agents"], fx.EXPECTED["agents"])
        self.assertEqual(d["by_source"], fx.EXPECTED["agents_by_source"])
        self.assertEqual(d["workflows"], [{"name": "race-audit",
                                           "phases": ["Find", "Verify"], "agents": 3}])
        self.assertEqual(d["delegated_tool_calls"], fx.EXPECTED["delegated_tool_calls"])
        self.assertIn("map payment module", d["agent_descriptions"])

    def test_redact_drops_agent_descriptions(self):
        self.extract("--redact-prompts")
        r = self.records()[fx.SID]
        self.assertNotIn("agent_descriptions", r["classifier_payload"]["delegation"])
        self.assertTrue(all("description" not in a for a in r["agents"]))

    def test_outcome_signals_record_threads(self):
        s = self.orch()["outcome_signals"]
        self.assertEqual(s["test_runs"], {"main": fx.EXPECTED["test_runs_main"],
                                          "delegated": fx.EXPECTED["test_runs_delegated"]})
        self.assertEqual(s["signal_sources"]["tests_run"], ["main", "workflow:awf2"])
        self.assertEqual(s["signal_sources"]["commit_created"], ["main"])
        # main thread's run ("3 passed") is the final state; awf2's "1 failed"
        # repro is recorded separately
        self.assertTrue(s["tests_passed"])
        self.assertIs(s["tests_passed_main"], True)
        self.assertIs(s["tests_passed_delegated"], False)
        self.assertFalse(s["ended_on_unanswered_question"])

    def test_delegated_commit_counts(self):
        path = self.root / fx.SID / "subagents" / "agent-aexp1.jsonl"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(fx.assistant(
                fx.SID, "2026-10-01T10:40:00.000Z", "req_s3", "claude-sonnet-5-5",
                [fx.tool_use("tu_s3", "Bash", {"command": "gh pr create --fill"})],
                True, "aexp1")) + "\n")
        s = self.orch()["outcome_signals"]
        self.assertTrue(s["pr_created"])
        self.assertEqual(s["signal_sources"]["pr_created"], ["agent:aexp1"])

    # --- incremental + range ---------------------------------------------

    def test_changing_agent_file_moves_session_to_todo(self):
        self.extract()
        p = run("report.py", "--out-dir", str(self.out), "--pricing", str(PRICING),
                home=self.tmp)
        self.assertEqual(p.returncode, 0, p.stderr)
        res = self.extract()
        self.assertEqual(sorted(res["cached"]), sorted([fx.SID, fx.PLAIN_SID]))
        wf_agent = self.root / fx.SID / "subagents" / "workflows" / fx.RUN_ID / "agent-awf3.jsonl"
        with open(wf_agent, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(fx.assistant(
                fx.SID, "2026-10-01T10:50:00.000Z", "req_w4", "claude-haiku-5-5",
                [{"type": "text", "text": "more"}], True, "awf3")) + "\n")
        res = self.extract()
        self.assertEqual(res["todo"], [fx.SID])
        self.assertEqual(res["cached"], [fx.PLAIN_SID])

    def test_range_filter_counts_real_sessions(self):
        res = self.extract("--since", "2026-10-02")
        self.assertEqual(res["todo"], [fx.PLAIN_SID])
        self.assertEqual(res["range"]["skipped_out_of_range"], 1)

    # --- report -------------------------------------------------------------

    def test_summary_has_delegated_spend(self):
        self.extract()
        p = run("report.py", "--out-dir", str(self.out), "--pricing", str(PRICING),
                home=self.tmp)
        self.assertEqual(p.returncode, 0, p.stderr)
        md = (self.out / "summary.md").read_text()
        self.assertIn("## Delegated spend", md)
        self.assertIn("### By agent type", md)
        self.assertIn("| race-audit | `wf_k3j9x2ab` | `sess-orch` | 3 |", md)
        self.assertIn("- Sessions: **2**", md)
        s = self.records("sessions.jsonl")
        grand = sum(r["session_cost_usd"] for r in s.values())
        main = sum(r["cost_by_agent_source"]["main"] for r in s.values())
        self.assertIn(f"| Main thread | 2 | ${main:.4f} |", md)
        self.assertIn(f"- Total cost: **${grand:.4f}**", md)


if __name__ == "__main__":
    unittest.main()
