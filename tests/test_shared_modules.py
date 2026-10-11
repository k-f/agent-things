"""Repo-level checks for code vendored into several plugins.

Run: python3 -m unittest discover -s tests -v
"""

import importlib.util
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
            self.assertEqual(t.workflow_run_ids_in("runId: wf_k3j9x2ab done"), ["wf_k3j9x2ab"])


if __name__ == "__main__":
    unittest.main()
