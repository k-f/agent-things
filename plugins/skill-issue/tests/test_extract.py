"""Tests for scripts/extract_user_messages.py against synthetic fixtures.

Run: python3 -m unittest discover -s plugins/skill-issue/tests -v
Never touches the real ~/.claude: every call passes --claude-dir.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "scripts" / "extract_user_messages.py"
CLAUDE_DIR = HERE / "fixtures" / "claude"

sys.dont_write_bytecode = True
sys.path.insert(0, str(SCRIPT.parent))
import extract_user_messages as eum  # noqa: E402


def run(*args, claude_dir=CLAUDE_DIR):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--claude-dir", str(claude_dir), *args],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"script failed ({proc.returncode}): {proc.stderr}")
    return proc


def run_json(*args):
    proc = run(*args, "--output-format", "json")
    return json.loads(proc.stdout), proc.stderr


class TestFiltering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, _ = run_json("--project", "/work/api")
        cls.texts = [m["text"] for m in cls.data["messages"]]
        cls.joined = "\n".join(cls.texts)

    def test_only_genuine_messages(self):
        self.assertEqual(sorted(self.texts), sorted([
            "Add rate limiting to the /login endpoint; tests in tests/test_auth.py must pass",
            "do X",
            "Fix the failing test: AssertionError at line 42",
            "thanks, ship it",
        ]))

    def test_excluded_content_absent(self):
        for needle in ("Base directory for this skill", "<command-", "local-command",
                       "Caveat:", "Request interrupted", "SUMMARY TEXT", "SIDECHAIN",
                       "SUBAGENT PROMPT", "1 failed", "task-notification"):
            self.assertNotIn(needle, self.joined, needle)

    def test_system_reminder_stripped_from_mixed_text(self):
        self.assertNotIn("INJECTED REMINDER TEXT", self.joined)
        self.assertIn("Fix the failing test: AssertionError at line 42", self.texts)

    def test_slash_command_args_kept_and_tagged(self):
        [msg] = [m for m in self.data["messages"] if m["text"] == "do X"]
        self.assertEqual(msg["slash_command"], "foo")

    def test_classifier_unit(self):
        c = eum.classify_user_entry
        self.assertEqual(c({"type": "user", "isMeta": True,
                            "message": {"role": "user", "content": "x"}})["kind"], "meta")
        self.assertEqual(c({"type": "user", "message": {
            "role": "user", "content": "<command-name>/clear</command-name><command-args></command-args>"}}),
            {"kind": "slash_command", "command": "clear", "text": ""})
        self.assertFalse(eum.is_genuine_user_message({"type": "user", "message": {
            "role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "x"}]}}))
        self.assertTrue(eum.is_genuine_user_message({"role": "user", "content": "bare shape"}))


class TestProjectMatching(unittest.TestCase):
    def test_encoding(self):
        self.assertEqual(eum.encode_project_path("/home/user/agent-things"), "-home-user-agent-things")
        self.assertEqual(eum.encode_project_path("/Users/a.b/x_y"), "-Users-a-b-x-y")

    def test_exact_match_excludes_sibling_prefix(self):
        data, stderr = run_json("--project", "/work/api")
        self.assertEqual(data["stats"]["project_dirs"], ["-work-api"])
        self.assertNotIn("GATEWAY MESSAGE", json.dumps(data))

    def test_include_subdirs_uses_cwd_not_prefix(self):
        data, _ = run_json("--project", "/work/api", "--include-subdirs")
        self.assertEqual(sorted(data["stats"]["project_dirs"]), ["-work-api", "-work-api-v2"])
        texts = [m["text"] for m in data["messages"]]
        self.assertIn("V2 SUBDIR MESSAGE", texts)
        self.assertNotIn("GATEWAY MESSAGE", texts)

    def test_no_match_reports_candidates_and_returns_nothing(self):
        data, stderr = run_json("--project", "/work/apx")
        self.assertEqual(data["messages"], [])
        self.assertIn("Closest candidates", stderr)
        self.assertIn("-work-api", stderr)

    def test_all_scans_every_dir(self):
        data, _ = run_json("--all")
        self.assertEqual(len(data["stats"]["project_dirs"]), 3)
        self.assertEqual(data["stats"]["messages_total"], 6)

    def test_check_projects(self):
        out = json.loads(run("--check-projects", "--project", "/work/api").stdout)
        self.assertEqual(out["expected_dir_name"], "-work-api")
        self.assertEqual([m["dir"] for m in out["matched"]], ["-work-api"])


class TestMetrics(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data, _ = run_json("--project", "/work/api")
        cls.session = data["sessions"][0]
        cls.summary = data["summary"]

    def test_session_counts(self):
        s = self.session
        self.assertEqual(s["user_messages"], 4)
        self.assertEqual(s["assistant_turns"], 4)      # reqA deduped, <synthetic> excluded
        self.assertEqual(s["tool_uses"], 4)
        self.assertEqual(s["tool_uses_per_user_message"], 1.0)
        self.assertEqual(s["max_tool_uses_between_user_messages"], 3)
        self.assertEqual(s["interrupts"], 2)
        self.assertEqual(s["compactions"], 1)
        self.assertEqual(s["duration_seconds"], 3600)

    def test_features(self):
        s = self.session
        self.assertEqual(s["slash_commands"], ["foo", "clear"])
        self.assertEqual(s["skills_invoked"], ["review"])
        self.assertEqual(s["subagent_spawns"], 2)
        self.assertEqual(s["subagent_types"], {"Explore": 1, "general-purpose": 1})
        self.assertEqual(s["background_subagents"], 1)
        self.assertEqual(s["test_runs"], 1)

    def test_summary(self):
        sm = self.summary
        self.assertEqual(sm["sessions"], 1)
        self.assertEqual(sm["user_messages"], 4)
        self.assertEqual(sm["subagent_spawns"], 2)
        self.assertEqual(sm["sessions_with_subagents"], 1)
        self.assertEqual(sm["sessions_with_test_runs"], 1)
        self.assertEqual(sm["slash_commands"], {"foo": 1, "clear": 1})
        self.assertEqual(sm["interrupts_per_100_user_messages"], 50.0)

    def test_stats_only_has_no_message_text(self):
        out = run("--project", "/work/api", "--stats-only").stdout
        self.assertNotIn("ship it", out)
        self.assertIn("summary", json.loads(out))


class TestRetention(unittest.TestCase):
    def test_fixture_settings(self):
        out = json.loads(run("--check-retention").stdout)
        self.assertEqual(out["cleanup_period_days"], 45)
        self.assertFalse(out["is_default"])
        self.assertTrue(out["recommend_change"])
        self.assertEqual(out["recommended_snippet"], {"cleanupPeriodDays": 90})
        self.assertNotIn("not-a-real-secret", json.dumps(out))  # settings not echoed

    def test_missing_settings_is_default(self):
        with tempfile.TemporaryDirectory() as d:
            out = json.loads(run("--check-retention", claude_dir=d).stdout)
        self.assertFalse(out["settings_exists"])
        self.assertEqual(out["effective_days"], 30)
        self.assertTrue(out["is_default"])
        self.assertTrue(out["recommend_change"])

    def test_missing_projects_dir_is_graceful(self):
        with tempfile.TemporaryDirectory() as d:
            proc = run("--project", "/work/api", "--output-format", "json", claude_dir=d)
        self.assertEqual(json.loads(proc.stdout)["messages"], [])
        self.assertIn("No Claude projects directory", proc.stderr)


if __name__ == "__main__":
    unittest.main()
