"""
Shared Claude Code transcript discovery and message classification.

This file is vendored, byte-identical, into every plugin that reads
~/.claude/projects (plugins install independently, so they can't import each
other). tests/test_shared_modules.py at the repo root fails if the copies drift.
Edit one copy, then copy it over the others.

On-disk layout (Claude Code 2.1.x; workflow layout checked against a real
run on 2.1.293, run ids look like wf_b4fbf2f4-8b2):

  projects/<project-key>/<session-id>.jsonl              main thread
  projects/<project-key>/<session-id>/subagents/
      agent-<id>.jsonl                                   Agent/Task subagents,
      agent-<id>.meta.json                               forked skills, nested
                                                         spawns (any depth)
      workflows/<run-id>/agent-<id>.jsonl                agents of one Workflow
      workflows/<run-id>/agent-<id>.meta.json            run (run-id = wf_...)
      workflows/<run-id>/journal.jsonl                   workflow journal (internal)
  projects/<project-key>/<session-id>/workflows/<run-id>.json   run sidecar:
      workflowName, status, agentCount, durationMs, phases [{title, model}],
      defaultModel, script, scriptPath, result, taskId
  projects/<project-key>/<session-id>/workflows/scripts/<name>-<run-id>.js

Older versions wrote sidechain transcripts as top-level agent-<id>.jsonl files
next to the session file; those are attached to the session named by the
sessionId field inside them.

Every agent file belongs to its parent session. Counting agent-<id>.jsonl as a
session of its own (grouping by filename stem) turns one session with a
50-agent workflow into 51 "sessions" and strips the delegated spend from the
session that caused it.

Python 3.8+, stdlib only.
"""

import json
import re
import sys
from pathlib import Path

AGENT_FILE_RE = re.compile(r"^agent-(.+)\.jsonl$")
WORKFLOW_RUN_ID_RE = re.compile(r"\bwf_[A-Za-z0-9][A-Za-z0-9_-]{3,}\b")

AGENT_TOOLS = ("Agent", "Task")
WORKFLOW_TOOLS = ("Workflow",)

# Text prefixes that mark harness-injected user entries in transcripts that
# predate the `origin` / `isMeta` fields.
SYNTHETIC_PREFIXES = (
    "<command-", "<local-command-", "Caveat:", "<task-notification>",
    "<agent-message", "<system-reminder>", "<user-prompt-submit-hook>",
    "Stop hook feedback:", "Another Claude session sent a message:",
    "Base directory for this skill:", "This session is being continued from",
)
INTERRUPT_PREFIX = "[Request interrupted"
REMINDER_BLOCK_RE = re.compile(r"<system-reminder>.*?</system-reminder>\s*", re.S)
# A local slash command run in a headless/older session that left no
# origin metadata: "/plugin:skill args"
BARE_COMMAND_RE = re.compile(r"^/[A-Za-z0-9][\w:.-]*(\s|$)")


# ---------------------------------------------------------------- reading


def iter_jsonl(path):
    """Yield dict entries from a JSONL file, skipping blank and corrupt lines."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        return


def read_json(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            obj = json.load(fh)
        return obj if isinstance(obj, dict) else {}
    except (OSError, ValueError):
        return {}


def _first_session_id(path):
    for entry in iter_jsonl(path):
        sid = entry.get("sessionId")
        if isinstance(sid, str) and sid:
            return sid
    return None


# ---------------------------------------------------------------- discovery


class AgentFile(object):
    """One subagent transcript and what its .meta.json says about it."""

    __slots__ = ("path", "agent_id", "meta", "workflow_run_id", "depth")

    def __init__(self, path, agent_id, meta, workflow_run_id):
        self.path = path
        self.agent_id = agent_id
        self.meta = meta
        self.workflow_run_id = workflow_run_id
        self.depth = None  # set by _resolve_depths()

    @property
    def agent_type(self):
        return self.meta.get("agentType") or (
            "workflow-subagent" if self.workflow_run_id else "unknown")

    @property
    def spawn_depth(self):
        """Nesting depth: the larger of meta spawnDepth and the parentAgentId
        chain. Agents spawned inside a forked skill report spawnDepth 1 but
        carry parentAgentId, so spawnDepth alone undercounts."""
        if self.depth is not None:
            return self.depth
        d = self.meta.get("spawnDepth")
        return d if isinstance(d, int) else 1

    @property
    def source(self):
        """How the agent was started: workflow | team | agent."""
        if self.workflow_run_id:
            return "workflow"
        if self.meta.get("teamName"):
            return "team"
        return "agent"

    def summary(self):
        m = self.meta
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "source": self.source,
            "workflow_run_id": self.workflow_run_id,
            "workflow_phase": m.get("workflowPhase"),
            "description": m.get("description"),
            "parent_agent_id": m.get("parentAgentId"),
            "spawn_depth": self.spawn_depth,
            "background": m.get("requestShape") == "background",
            "team_name": m.get("teamName"),
            "stopped_by_user": bool(m.get("stoppedByUser")),
        }


class SessionFiles(object):
    """A session's main transcript plus every agent transcript it spawned."""

    __slots__ = ("session_id", "project_dir", "main", "agents", "workflow_sidecars")

    def __init__(self, session_id, project_dir):
        self.session_id = session_id
        self.project_dir = project_dir
        self.main = None
        self.agents = []
        self.workflow_sidecars = {}

    def all_paths(self):
        """Main transcript first, then agents in a stable order."""
        out = [self.main] if self.main is not None else []
        out.extend(a.path for a in self.agents)
        return out

    def workflow_runs(self):
        """{run_id: run} from the run sidecar and the agents on disk.

        run: run_id, name, agents (transcripts found), phases (run order when
        the sidecar lists them), agent_types, and from the sidecar when
        present: status, agent_count (agents the run reports launching),
        duration_ms, default_model.
        """
        runs = {}
        for rid, side in self.workflow_sidecars.items():
            runs[rid] = _new_run(rid, side)
        for a in self.agents:
            rid = a.workflow_run_id
            if not rid:
                continue
            r = runs.get(rid)
            if r is None:
                r = runs[rid] = _new_run(rid, {})
            r["agents"] += 1
            ph = a.meta.get("workflowPhase")
            if ph and ph not in r["phases"]:
                r["phases"].append(ph)
            t = a.agent_type
            r["agent_types"][t] = r["agent_types"].get(t, 0) + 1
        return runs


def _new_run(rid, side):
    phases = []
    for ph in side.get("phases") or []:
        title = ph.get("title") if isinstance(ph, dict) else ph
        if isinstance(title, str) and title and title not in phases:
            phases.append(title)
    ac = side.get("agentCount")
    dur = side.get("durationMs")
    return {"run_id": rid, "name": _sidecar_name(side), "agents": 0, "phases": phases,
            "agent_types": {},
            "status": side.get("status") if isinstance(side.get("status"), str) else None,
            "agent_count": ac if isinstance(ac, int) else None,
            "duration_ms": dur if isinstance(dur, (int, float)) else None,
            "default_model": side.get("defaultModel")}


def _sidecar_name(side):
    for k in ("workflowName", "name", "title"):
        v = side.get(k)
        if isinstance(v, str) and v:
            return v
    meta = side.get("meta")
    if isinstance(meta, dict) and isinstance(meta.get("name"), str):
        return meta["name"]
    return None


def _resolve_depths(agents):
    by_id = {a.agent_id: a for a in agents}
    for a in agents:
        chain, cur, seen = 1, a, set()
        while True:
            pid = cur.meta.get("parentAgentId")
            if not pid or pid in seen or pid not in by_id:
                if pid and pid not in by_id:
                    chain += 1  # parent transcript missing; still one level up
                break
            seen.add(pid)
            chain += 1
            cur = by_id[pid]
        sd = a.meta.get("spawnDepth")
        a.depth = max(chain, sd if isinstance(sd, int) else 1)


def _agent_files_under(subagents_dir):
    """(path, agent_id, workflow_run_id) for every agent-*.jsonl at any depth."""
    out = []
    if not subagents_dir.is_dir():
        return out
    for path in sorted(subagents_dir.rglob("agent-*.jsonl")):
        m = AGENT_FILE_RE.match(path.name)
        if not m or not path.is_file():
            continue
        rel = path.relative_to(subagents_dir).parts[:-1]
        run_id = rel[1] if len(rel) >= 2 and rel[0] == "workflows" else None
        out.append((path, m.group(1), run_id))
    return out


def discover_project(project_dir):
    """{session_id: SessionFiles} for one ~/.claude/projects/<key> directory."""
    project_dir = Path(project_dir)
    sessions = {}

    def get(sid):
        s = sessions.get(sid)
        if s is None:
            s = sessions[sid] = SessionFiles(sid, project_dir)
        return s

    if not project_dir.is_dir():
        return sessions
    legacy_agents = []
    for item in sorted(project_dir.iterdir()):
        if item.is_file() and item.suffix == ".jsonl":
            m = AGENT_FILE_RE.match(item.name)
            if m:
                legacy_agents.append((item, m.group(1)))
            else:
                get(item.stem).main = item
        elif item.is_dir():
            sub = item / "subagents"
            wf_side = item / "workflows"
            agents = _agent_files_under(sub)
            sides = sorted(wf_side.glob("*.json")) if wf_side.is_dir() else []
            if not agents and not sides:
                continue
            s = get(item.name)
            for path, aid, run_id in agents:
                meta = read_json(path.with_name("agent-%s.meta.json" % aid))
                s.agents.append(AgentFile(path, aid, meta, run_id))
            for side in sides:
                s.workflow_sidecars[side.stem] = read_json(side)
    for path, aid in legacy_agents:
        sid = _first_session_id(path)
        if sid is None:
            sys.stderr.write("transcripts: %s has no sessionId; not attached to any session\n" % path)
            continue
        get(sid).agents.append(AgentFile(path, aid, {}, None))
    for s in sessions.values():
        _resolve_depths(s.agents)
    return sessions


def discover(projects_dir, project_dirs=None):
    """{session_id: SessionFiles} across project dirs (default: all of them)."""
    projects_dir = Path(projects_dir)
    if project_dirs is None:
        project_dirs = sorted(d for d in projects_dir.iterdir() if d.is_dir()) \
            if projects_dir.is_dir() else []
    out = {}
    for d in project_dirs:
        for sid, s in discover_project(d).items():
            if sid in out:  # same session id under two keys: merge, keep first main
                o = out[sid]
                if o.main is None:
                    o.main = s.main
                elif s.main is not None:
                    sys.stderr.write("transcripts: session %s has a main transcript under two "
                                     "project dirs; using %s, ignoring %s\n" % (sid, o.main, s.main))
                o.agents.extend(s.agents)
                o.workflow_sidecars.update(s.workflow_sidecars)
            else:
                out[sid] = s
    return out


# ---------------------------------------------------------------- classification


def _content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text"
                         and isinstance(b.get("text"), str))
    return ""


def is_tool_result(entry):
    """True for a user entry that feeds tool output back (main thread or agent)."""
    if entry.get("type") != "user":
        return False
    if "toolUseResult" in entry:
        return True
    msg = entry.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def strip_reminders(text):
    """Remove <system-reminder> blocks the harness prepends to typed text."""
    return REMINDER_BLOCK_RE.sub("", text or "")


def user_entry_kind(entry):
    """Who authored a type=user transcript entry.

    Returns one of:
      human              typed by the person (interactive)
      sdk                prompt sent programmatically (claude -p, Agent SDK)
      command            slash command invocation (<command-name>..., or a bare
                         "/cmd args" with no origin metadata); the args text,
                         if any, is the person's
      interrupt          "[Request interrupted ..." marker
      tool_result        tool output fed back to the model (checked first, so
                         agent transcripts report it too)
      task_notification  background task / subagent completion notice
      peer               message or hand-back from another agent/session
      sidechain          entry inside a subagent transcript
      meta               harness-injected (skill bodies, hook feedback,
                         compaction summaries, caveats, reminders)
      not_user           not a user entry
    """
    if entry.get("type") != "user":
        return "not_user"
    msg = entry.get("message")
    if not isinstance(msg, dict) or msg.get("role", "user") != "user":
        return "not_user"
    if is_tool_result(entry):
        return "tool_result"
    if entry.get("isSidechain"):
        return "sidechain"
    content = msg.get("content")

    origin = entry.get("origin")
    okind = origin.get("kind") if isinstance(origin, dict) else None
    turn_origin = entry.get("turnOrigin")
    if okind in ("task-notification", "task_notification") or turn_origin == "task_notification":
        return "task_notification"
    if okind == "peer" or turn_origin == "peer":
        return "peer"
    if entry.get("isCompactSummary") or entry.get("isMeta") or entry.get("isVisibleInTranscriptOnly"):
        return "meta"

    text = strip_reminders(_content_text(content)).lstrip()
    if not text.strip():
        return "meta"
    if text.startswith(INTERRUPT_PREFIX):
        return "interrupt"
    if text.startswith("<command-name>") or text.startswith("<command-message>"):
        return "command"
    if text.startswith(SYNTHETIC_PREFIXES):
        return "meta"
    if okind == "human" or turn_origin == "human":
        return "human"
    # promptSource is "sdk" even for typed prompts in the desktop/web apps, so
    # only turnOrigin distinguishes a programmatic prompt.
    if turn_origin == "sdk":
        return "sdk"
    if okind is None and turn_origin is None and BARE_COMMAND_RE.match(text):
        # current interactive prompts carry origin metadata; a bare "/cmd"
        # without it is a local command (headless run or older version)
        return "command"
    return "human"


def workflow_run_ids_in(text):
    """Workflow run ids mentioned in a tool result (Workflow launch output)."""
    return WORKFLOW_RUN_ID_RE.findall(text or "")
