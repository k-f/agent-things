"""Repo-level checks for code vendored into several plugins.

Run: python3 -m unittest discover -s tests -v
"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COPIES = [
    ROOT / "plugins/skill-issue/scripts/transcripts.py",
    ROOT / "plugins/cc-usage-classifier/skills/cc-usage-classifier/scripts/transcripts.py",
]
sys.path.insert(0, str(Path(__file__).resolve().parent / "fixtures"))
import make_delegation_fixture as fx  # noqa: E402


def load(path):
    spec = importlib.util.spec_from_file_location("transcripts_%d" % abs(hash(path)), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SharedTranscriptsModule(unittest.TestCase):
    def test_copies_identical(self):
        first = COPIES[0].read_bytes()
        for p in COPIES[1:]:
            self.assertEqual(first, p.read_bytes(),
                             "%s drifted from %s; copy the edited file over the others" % (p, COPIES[0]))

    def test_discovery_and_classification_on_delegation_fixture(self):
        t = load(COPIES[0])
        with tempfile.TemporaryDirectory() as tmp:
            fx.build(tmp)
            sessions = t.discover(tmp)
            self.assertEqual(sorted(sessions), [fx.SID, fx.PLAIN_SID])
            s = sessions[fx.SID]
            self.assertEqual(s.main.name, fx.SID + ".jsonl")
            self.assertEqual(len(s.agents), fx.EXPECTED["agents"])
            by_source = {}
            for a in s.agents:
                by_source[a.source] = by_source.get(a.source, 0) + 1
            self.assertEqual(by_source, fx.EXPECTED["agents_by_source"])
            self.assertEqual(max(a.spawn_depth for a in s.agents), fx.EXPECTED["max_spawn_depth"])
            runs = s.workflow_runs()
            run = runs[fx.RUN_ID]
            self.assertEqual(run["phases"], ["Find", "Verify"])   # sidecar order, not file order
            self.assertEqual((run["status"], run["agent_count"], run["duration_ms"]),
                             ("completed", 3, 61000))
            self.assertEqual({k: {"name": v["name"], "agents": v["agents"]} for k, v in runs.items()},
                             fx.EXPECTED["workflow_runs"])
            kinds = {}
            for e in t.iter_jsonl(s.main):
                k = t.user_entry_kind(e)
                kinds[k] = kinds.get(k, 0) + 1
            self.assertEqual(kinds.get("human"), fx.EXPECTED["human_prompts"])
            self.assertEqual(kinds.get("peer"), 1)
            self.assertEqual(kinds.get("task_notification"), 1)
            self.assertEqual(kinds.get("meta"), 2)
            self.assertEqual(t.workflow_run_ids_in("Run ID: wf_b4fbf2f4-8b2\nok"), ["wf_b4fbf2f4-8b2"])

    def test_entry_kinds_edge_cases(self):
        t = load(COPIES[0])
        def u(content, **kw):
            e = {"type": "user", "message": {"role": "user", "content": content}}
            e.update(kw)
            return e
        tr = [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]
        self.assertEqual(t.user_entry_kind(u(tr, isSidechain=True)), "tool_result")
        self.assertEqual(t.user_entry_kind(u("hi", isSidechain=True)), "sidechain")
        typed = u([{"type": "text", "text": "<system-reminder>ctx</system-reminder>\nFix the bug"}],
                  origin={"kind": "human"})
        self.assertEqual(t.user_entry_kind(typed), "human")
        self.assertEqual(t.user_entry_kind(u("/skill-issue:skill-issue all")), "command")
        self.assertEqual(t.user_entry_kind(u("/tmp/x is full", origin={"kind": "human"})), "human")
        self.assertEqual(t.user_entry_kind(u("x", turnOrigin="sdk")), "sdk")

    def test_depth_from_parent_chain_and_orphan_legacy_file(self):
        t = load(COPIES[0])
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "-p"
            sub = proj / "s1" / "subagents"
            sub.mkdir(parents=True)
            (proj / "s1.jsonl").write_text("")
            for aid, meta in (("fork", {"agentType": "general-purpose", "spawnDepth": 1}),
                              ("child", {"agentType": "x", "spawnDepth": 1, "parentAgentId": "fork"}),
                              ("grand", {"agentType": "y", "spawnDepth": 1, "parentAgentId": "child"})):
                (sub / ("agent-%s.jsonl" % aid)).write_text("{}\n")
                (sub / ("agent-%s.meta.json" % aid)).write_text(json.dumps(meta))
            (proj / "agent-orphan.jsonl").write_text('{"type": "user"}\n')
            import contextlib, io
            with contextlib.redirect_stderr(io.StringIO()) as err:
                sessions = t.discover(tmp)
            self.assertEqual(sorted(sessions), ["s1"])
            self.assertIn("agent-orphan.jsonl", err.getvalue())
            depth = {a.agent_id: a.spawn_depth for a in sessions["s1"].agents}
            self.assertEqual(depth, {"fork": 1, "child": 2, "grand": 3})


if __name__ == "__main__":
    unittest.main()
