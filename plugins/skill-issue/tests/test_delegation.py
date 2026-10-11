"""Subagent / workflow / worktree handling in scripts/extract_user_messages.py.

Run: python3 -m unittest discover -s plugins/skill-issue/tests -v
The delegation fixture generator lives at the repo root
(tests/fixtures/make_delegation_fixture.py); those tests are skipped when the
plugin is installed without the repo.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "extract_user_messages.py"
REPO_FIXTURES = HERE.parent.parent.parent / "tests" / "fixtures"

sys.dont_write_bytecode = True
sys.path.insert(0, str(SCRIPT.parent))
import extract_user_messages as eum  # noqa: E402

try:
    sys.path.insert(0, str(REPO_FIXTURES))
    import make_delegation_fixture as fx  # noqa: E402
except ImportError:
    fx = None
SKIP_MSG = ("delegation fixture generator not found at %s (plugin installed without the "
            "agent-things repo)" % (REPO_FIXTURES / "make_delegation_fixture.py"))


def run(projects_dir, *args, claude_dir=None):
    with tempfile.TemporaryDirectory() as empty_claude:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--claude-dir", str(claude_dir or empty_claude),
             "--projects-dir", str(projects_dir), *args],
            capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise AssertionError("script failed (%d): %s" % (proc.returncode, proc.stderr))
    return proc


def run_json(projects_dir, *args):
    return json.loads(run(projects_dir, *args, "--output-format", "json").stdout)


@unittest.skipIf(fx is None, SKIP_MSG)
class TestDelegationFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        fx.build(cls.tmp.name)
        cls.data = run_json(cls.tmp.name, "--project", fx.CWD)
        cls.sessions = {s["session"]: s for s in cls.data["sessions"]}
        cls.orch = cls.sessions[fx.SID]
        cls.dl = cls.orch["delegation"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_only_human_messages_from_main_thread(self):
        orch_msgs = [m for m in self.data["messages"] if m["session"] == fx.SID]
        self.assertEqual(len(orch_msgs), fx.EXPECTED["human_prompts"])
        self.assertEqual(sorted(m["text"] for m in orch_msgs), sorted([
            "Audit the payment module for race conditions; done when each finding has a "
            "failing test in tests/payments.",
            "Commit it.",
        ]))
        self.assertTrue(all(m["source"] == "human" for m in orch_msgs))
        joined = json.dumps(self.data["messages"])
        for needle in ("task-notification", "Another Claude session", "agent-message",
                       "Stop hook feedback", "Base directory for this skill",
                       "Map src/payments", "Read ledger.py", "Verify race", "Find races",
                       "Summarise findings"):
            self.assertNotIn(needle, joined, needle)
        self.assertEqual(self.orch["user_messages"], 2)

    def test_notifications_and_peers_counted_not_messages(self):
        self.assertEqual(self.orch["task_notifications"], 1)
        self.assertEqual(self.orch["peer_messages"], 1)

    def test_delegation_metrics(self):
        dl, E = self.dl, fx.EXPECTED
        self.assertEqual(dl["agents_total"], E["agents"])
        self.assertEqual({k: v for k, v in dl["by_source"].items() if v},
                         E["agents_by_source"])
        self.assertEqual(dl["by_source"]["team"], 0)
        self.assertEqual(dl["max_spawn_depth"], E["max_spawn_depth"])
        self.assertEqual(dl["nested_agents"], 1)
        self.assertEqual(dl["background_agents"], E["background_agents"])
        self.assertEqual(dl["stopped_by_user"], 0)
        self.assertEqual(dl["delegated_tool_calls"], E["delegated_tool_calls"])
        self.assertEqual(dl["delegated_test_runs"], E["test_runs_delegated"])
        self.assertEqual(self.orch["test_runs"], E["test_runs_main"])
        self.assertEqual(dl["by_type"]["Explore"], 1)
        self.assertEqual(dl["workflow_launches"], 1)

    def test_workflow_run(self):
        [run_] = self.dl["workflow_runs"]
        self.assertEqual(run_["run_id"], fx.RUN_ID)
        exp = fx.EXPECTED["workflow_runs"][fx.RUN_ID]
        self.assertEqual(run_["name"], exp["name"])
        self.assertEqual(run_["agents"], exp["agents"])
        self.assertTrue(run_["launched_by_tool_use"])
        self.assertEqual(run_["phases"], ["Find", "Verify"])
        self.assertTrue(run_["has_verify_phase"])

    def test_main_thread_spawns_unchanged(self):
        self.assertEqual(self.orch["subagent_spawns"], 1)   # one Agent tool_use in main
        self.assertEqual(self.orch["assistant_turns"], fx.EXPECTED["main_thread_requests"])

    def test_plain_session_has_no_delegation(self):
        dl = self.sessions[fx.PLAIN_SID]["delegation"]
        self.assertEqual(dl["agents_total"], 0)
        self.assertEqual(dl["delegated_tool_calls"], 0)
        self.assertEqual(dl["workflow_runs"], [])
        self.assertEqual(dl["workflow_launches"], 0)
        self.assertEqual(dl["max_spawn_depth"], 0)

    def test_summary_aggregates(self):
        sm = self.data["summary"]
        self.assertEqual(sm["sessions"], fx.EXPECTED["sessions"])
        self.assertEqual(sm["user_messages"], 3)
        self.assertEqual(sm["delegated_agents"], 6)
        self.assertEqual(sm["sessions_with_delegated_agents"], 1)
        self.assertEqual(sm["sessions_with_workflows"], 1)
        self.assertEqual(sm["workflow_runs"], 1)
        self.assertEqual(sm["workflow_agents"], 3)
        self.assertEqual(sm["workflow_runs_with_verify_phase"], 1)
        self.assertEqual(sm["sessions_with_nested_agents"], 1)
        self.assertEqual(sm["delegated_tool_calls"], 7)
        self.assertEqual(sm["delegated_test_runs"], 1)
        self.assertEqual(sm["task_notifications"], 1)
        self.assertEqual(sm["peer_messages"], 1)
        self.assertEqual(self.data["stats"]["agent_transcripts_scanned"], 6)

    def test_check_projects_reports_agents_and_workflows(self):
        out = json.loads(run(self.tmp.name, "--check-projects", "--project", fx.CWD).stdout)
        self.assertEqual(out["matched"], [{"dir": fx.PROJECT_KEY, "sessions": 2,
                                           "agent_transcripts": 6, "workflow_runs": 1}])


class TestClassification(unittest.TestCase):
    def test_sdk_prompt_labelled(self):
        c = eum.classify_user_entry({"type": "user", "turnOrigin": "sdk", "promptSource": "sdk",
                                     "message": {"role": "user", "content": "scripted"}})
        self.assertEqual(c, {"kind": "genuine", "text": "scripted", "source": "sdk"})

    def test_task_notification_without_ismeta(self):
        c = eum.classify_user_entry({
            "type": "user", "origin": {"kind": "task-notification"},
            "message": {"role": "user", "content": "<task-notification>x</task-notification>"}})
        self.assertEqual(c["kind"], "task_notification")
        self.assertFalse(eum.is_genuine_user_message({
            "type": "user", "turnOrigin": "task_notification",
            "message": {"role": "user", "content": "background job finished"}}))

    def test_peer_handback(self):
        c = eum.classify_user_entry({
            "type": "user", "isMeta": True, "origin": {"kind": "peer"},
            "message": {"role": "user", "content": "Another Claude session sent a message:"}})
        self.assertEqual(c["kind"], "peer")

    def test_leading_reminder_block_does_not_hide_prompt(self):
        c = eum.classify_user_entry({"type": "user", "message": {"role": "user", "content": [
            {"type": "text", "text": "<system-reminder>ctx</system-reminder>"},
            {"type": "text", "text": "Rename the ledger module"}]}})
        self.assertEqual(c["kind"], "genuine")
        self.assertEqual(c["text"], "Rename the ledger module")

    def test_local_command_after_caveat_is_not_a_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp) / "-work-x"
            proj.mkdir()
            entries = [
                {"type": "user", "uuid": "u1", "origin": {"kind": "human"}, "turnOrigin": "human",
                 "cwd": "/work/x", "message": {"role": "user", "content": "real prompt"}},
                {"type": "user", "uuid": "c1", "isMeta": True, "message": {
                    "role": "user", "content": "<local-command-caveat>The command below was run "
                                               "directly</local-command-caveat>"}},
                {"type": "user", "uuid": "c2", "parentUuid": "c1", "message": {
                    "role": "user", "content": "/skill-issue:skill-issue current library"}},
            ]
            (proj / "s1.jsonl").write_text("\n".join(json.dumps(e) for e in entries))
            data = run_json(tmp, "--project", "/work/x")
        self.assertEqual([m["text"] for m in data["messages"]], ["real prompt"])
        s = data["sessions"][0]
        self.assertEqual(s["local_commands"], ["skill-issue:skill-issue"])
        self.assertIn("skill-issue:skill-issue", s["slash_commands"])


def _make_worktree_layout(root: Path, gitdir_relative=False):
    """main repo at root/main, linked worktree at root/wt-feature."""
    main, wt = root / "main", root / "wt-feature"
    private = main / ".git" / "worktrees" / "wt-feature"
    private.mkdir(parents=True)
    (main / "src").mkdir()
    wt.mkdir()
    (wt / "src").mkdir()
    (private / "commondir").write_text("../..\n")
    (private / "gitdir").write_text(str(wt / ".git") + "\n")
    target = "../main/.git/worktrees/wt-feature" if gitdir_relative else str(private)
    (wt / ".git").write_text("gitdir: %s\n" % target)
    return main, wt


class TestWorktreeMatching(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.main, self.wt = _make_worktree_layout(self.root / "repos")
        self.projects = self.root / "projects"
        for path, text, sid in ((self.main, "MAIN PROMPT", "s-main"),
                                (self.wt, "WORKTREE PROMPT", "s-wt")):
            d = self.projects / eum.encode_project_path(str(path))
            d.mkdir(parents=True)
            (d / (sid + ".jsonl")).write_text(json.dumps({
                "type": "user", "cwd": str(path), "timestamp": "2026-10-01T10:00:00Z",
                "origin": {"kind": "human"}, "message": {"role": "user", "content": text}}))

    def tearDown(self):
        self._tmp.cleanup()

    def test_related_paths_from_worktree(self):
        self.assertEqual(eum.related_worktree_paths(str(self.wt)), [str(self.main)])
        self.assertEqual(eum.related_worktree_paths(str(self.wt / "src")),
                         [str(self.main / "src")])

    def test_related_paths_from_main(self):
        self.assertEqual(eum.related_worktree_paths(str(self.main)), [str(self.wt)])

    def test_relative_gitdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            main, wt = _make_worktree_layout(Path(tmp).resolve(), gitdir_relative=True)
            self.assertEqual(eum.related_worktree_paths(str(wt)), [str(main)])

    def test_not_a_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(eum.related_worktree_paths(tmp), [])

    def test_project_in_worktree_matches_main_logs(self):
        data = run_json(self.projects, "--project", str(self.wt))
        self.assertEqual(sorted(data["stats"]["project_dirs"]), sorted([
            eum.encode_project_path(str(self.main)), eum.encode_project_path(str(self.wt))]))
        self.assertEqual(sorted(m["text"] for m in data["messages"]),
                         ["MAIN PROMPT", "WORKTREE PROMPT"])

    def test_check_projects_lists_worktree_paths(self):
        out = json.loads(run(self.projects, "--check-projects", "--project", str(self.wt)).stdout)
        self.assertEqual(out["worktree_paths"], [str(self.main)])
        self.assertEqual(len(out["matched"]), 2)


if __name__ == "__main__":
    unittest.main()
