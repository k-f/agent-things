#!/usr/bin/env python3
"""
Extract genuine user messages and per-session structural metrics from
Claude Code JSONL session logs (~/.claude/projects/<encoded-path>/<session>.jsonl).

A "genuine" user message is text a person (or an SDK caller) sent as a prompt.
Entry classification comes from the shared transcripts.py (user_entry_kind):
kept are kinds `human`, `sdk` (tagged "source": "sdk") and slash commands whose
<command-args> text is non-empty. Never messages, but counted:
  - task notifications and peer/subagent hand-backs (evidence of delegation)
  - tool results, isMeta / compaction / skill-body / hook-feedback entries
  - "[Request interrupted" markers (counted as interrupts)
  - local commands recorded after a <local-command-caveat> (counted as
    slash commands, not prompts: the harness says they were not sent to Claude)
Extra text handling kept here: <system-reminder> and <user-prompt-submit-hook>
blocks are stripped from otherwise genuine text, and bash-mode / hook output
prefixes (<bash-stdout>, PreToolUse:, ...) are dropped.

Subagents and workflows: each session's agent transcripts (Agent/Task
subagents at any depth, Workflow run agents, legacy top-level agent-*.jsonl)
are found via transcripts.discover_project and summarised under the session's
"delegation" key. Their prompts are never counted as user messages.

Project matching: Claude Code names each project dir by replacing every
non-alphanumeric character of the absolute path with '-'. --project matches
that encoding exactly. --include-subdirs also takes dirs for subdirectories,
confirmed against the session's recorded cwd. When --project is inside a git
repository with linked worktrees, the matching paths of the main worktree and
every linked worktree are matched too (read from .git files, git not needed).

Usage:
  python3 extract_user_messages.py [OPTIONS]

Output (stdout): JSON or text. Diagnostics (stderr): warnings, stats.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import transcripts  # noqa: E402  (vendored next to this script)

DEFAULT_CLAUDE_DIR = Path.home() / ".claude"
DEFAULT_RETENTION_DAYS = 30  # Claude Code default when cleanupPeriodDays is unset
RECOMMENDED_RETENTION_DAYS = 90
ACTIVE_GAP_CAP_SECONDS = 30 * 60  # gaps longer than this count as idle

COMMAND_NAME_RE = re.compile(r"<command-name>\s*/?([^<\s]+)\s*</command-name>")
COMMAND_MESSAGE_RE = re.compile(r"<command-message>\s*/?([^<\s]+)\s*</command-message>")
COMMAND_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
LOCAL_COMMAND_RE = re.compile(r"^/([^\s]+)\s*(.*)$", re.DOTALL)
LOCAL_COMMAND_CAVEAT = "<local-command-caveat>"
STRIP_BLOCK_RES = (
    re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL),
    re.compile(r"<user-prompt-submit-hook>.*?</user-prompt-submit-hook>", re.DOTALL),
)
INTERRUPT_PREFIX = transcripts.INTERRUPT_PREFIX
# Harness output that transcripts.SYNTHETIC_PREFIXES does not cover.
EXTRA_SYNTH_PREFIXES = (
    "<bash-stdout>", "<bash-stderr>", "<bash-input>", "PreToolUse:", "PostToolUse:",
)
META_FLAGS = ("isMeta", "isVisibleInTranscriptOnly")
SYNTHETIC_MODELS = {None, "", "<synthetic>"}
SUBAGENT_TOOLS = transcripts.AGENT_TOOLS
WORKFLOW_TOOLS = transcripts.WORKFLOW_TOOLS
TEST_RUNNER_RE = re.compile(
    r"\b(pytest|py\.test|unittest|tox|nox|npm\s+(?:run\s+)?test|yarn\s+test|pnpm\s+test|"
    r"jest|vitest|mocha|go\s+test|cargo\s+test|rspec|rails\s+test|"
    r"phpunit|dotnet\s+test|gradle\w*\s+test|mvn\s+test|ctest|make\s+test)\b"
)
# Workflow phase / agent names that indicate a verification step.
VERIFY_PHASE_RE = re.compile(r"verif|review|test|check|validat|judge|critic|audit", re.I)
WORKFLOW_SCRIPT_NAME_RE = re.compile(r"""\bname\s*:\s*['"]([^'"]+)['"]""")


# --------------------------------------------------------------------------
# Project directory matching
# --------------------------------------------------------------------------

def encode_project_path(path: str) -> str:
    """Encode an absolute path the way Claude Code names ~/.claude/projects dirs."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def _read_gitdir_file(git_file: Path):
    """Target of a `gitdir: <path>` file (a linked worktree's .git), or None."""
    try:
        first = git_file.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return None
    if not first.startswith("gitdir:"):
        return None
    target = Path(first[len("gitdir:"):].strip())
    if not target.is_absolute():
        target = git_file.parent / target
    return Path(os.path.normpath(str(target)))


def _common_git_dir(worktree_git_dir: Path):
    """The shared .git dir of a linked worktree's private git dir, or None."""
    try:
        rel = (worktree_git_dir / "commondir").read_text(encoding="utf-8").strip()
    except OSError:
        rel = None
    if rel:
        common = Path(rel)
        if not common.is_absolute():
            common = worktree_git_dir / common
        return Path(os.path.normpath(str(common)))
    # No commondir file: fall back to the standard <main>/.git/worktrees/<name> layout.
    if worktree_git_dir.parent.name == "worktrees":
        return worktree_git_dir.parent.parent
    return None


def related_worktree_paths(path: str) -> list:
    """Paths equivalent to `path` in the other worktrees of the same git repo.

    For a path inside a linked worktree this returns the same relative path in
    the main worktree (and in sibling worktrees); for a path in the main
    worktree, the same path in each linked worktree. Reads .git files only;
    does not need git. Returns [] outside a repo or with no linked worktrees.
    """
    p = Path(path)
    for root in [p, *p.parents]:
        dot_git = root / ".git"
        if dot_git.is_dir():
            common = dot_git
            break
        if dot_git.is_file():
            wt_git = _read_gitdir_file(dot_git)
            common = _common_git_dir(wt_git) if wt_git else None
            if common is None or common.name != ".git":
                return []  # submodule or bare-repo layout: not handled
            break
    else:
        return []
    try:
        rel = p.relative_to(root)
    except ValueError:
        return []
    roots = {common.parent}
    wt_dir = common / "worktrees"
    if wt_dir.is_dir():
        for wt in wt_dir.iterdir():
            try:  # <common>/worktrees/<name>/gitdir holds "<worktree>/.git"
                target = (wt / "gitdir").read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if target:
                roots.add(Path(os.path.normpath(str(wt / target))).parent)
    out = sorted(str(r / rel) if str(rel) != "." else str(r) for r in roots)
    return [o for o in out if os.path.normpath(o) != os.path.normpath(str(p))]


def _project_paths(project: str) -> set:
    raw = os.path.abspath(os.path.expanduser(project))
    paths = {raw, os.path.realpath(raw)}
    for p in list(paths):
        paths.update(related_worktree_paths(p))
    return paths


def _session_cwd(proj_dir: Path):
    """Return the first recorded cwd in any top-level session file, or None."""
    for f in sorted(proj_dir.glob("*.jsonl")):
        for i, entry in enumerate(transcripts.iter_jsonl(f)):
            if i > 50:
                break
            cwd = entry.get("cwd")
            if cwd:
                return cwd
    return None


def find_matching_project_dirs(projects_dir: Path, project: str,
                               include_subdirs: bool = False,
                               report_candidates: bool = True) -> list:
    """Exact match on Claude Code's path encoding. Never guesses.

    Subdirectory dirs (opt-in) share the encoded prefix; because '/' and '-'
    encode identically, each is confirmed via the session's recorded cwd so
    /work/api does not pick up /work/api-gateway. Paths of the same project in
    other git worktrees count as the project.
    """
    if not projects_dir.is_dir():
        return []
    raw = os.path.abspath(os.path.expanduser(project))
    paths = _project_paths(project)
    encodings = {encode_project_path(p) for p in paths}

    dirs = [d for d in projects_dir.iterdir() if d.is_dir()]
    matches = [d for d in dirs if d.name in encodings]

    if include_subdirs:
        for d in dirs:
            if d in matches or not any(d.name.startswith(e + "-") for e in encodings):
                continue
            cwd = _session_cwd(d)
            if cwd and any(cwd.startswith(p.rstrip("/") + "/") for p in paths):
                matches.append(d)

    if not matches and report_candidates:
        target = encode_project_path(raw)
        close = difflib.get_close_matches(target, [d.name for d in dirs], n=5, cutoff=0.5)
        print(f"No exact project log dir for {raw} (expected {projects_dir / target}).",
              file=sys.stderr)
        if close:
            print("Closest candidates (not used):", file=sys.stderr)
            for c in close:
                print(f"  {c}", file=sys.stderr)
        print("Re-run with the right --project, --include-subdirs, or --all.", file=sys.stderr)
    return sorted(matches)


def collect_sessions(project_dirs: list, recent_sessions: int) -> list:
    """SessionFiles (main transcript + agents) for the most recent sessions.

    Sessions are ordered by the newest of their files' mtimes. Agent files whose
    parent main transcript is gone (e.g. pruned) are reported and skipped.
    """
    sessions = transcripts.discover(project_dirs[0].parent, project_dirs) if project_dirs else {}
    out, orphans = [], 0
    for s in sessions.values():
        if s.main is None:
            orphans += 1
            continue
        out.append(s)
    if orphans:
        print(f"Skipping {orphans} session(s) with agent transcripts but no main transcript",
              file=sys.stderr)

    def newest(s):
        try:
            return max(p.stat().st_mtime for p in s.all_paths())
        except (OSError, ValueError):
            return 0
    out.sort(key=newest, reverse=True)
    return out[:recent_sessions]


def collect_session_files(project_dirs: list, recent_sessions: int) -> list:
    """Main <session>.jsonl paths of the most recent sessions (kept for callers)."""
    return [s.main for s in collect_sessions(project_dirs, recent_sessions)]


# --------------------------------------------------------------------------
# User entry classification
# --------------------------------------------------------------------------

def _normalise(entry: dict):
    """Accept both {type:user,message:{...}} and the bare {role,content} shape."""
    if entry.get("type") == "user":
        return entry if isinstance(entry.get("message"), dict) else None
    if not entry.get("type") and entry.get("role") == "user":
        e = {k: v for k, v in entry.items() if k not in ("role", "content")}
        e.update(type="user", message={"role": "user", "content": entry.get("content")})
        return e
    return None


def _text_blocks(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        b["text"] for b in content
        if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
    )


def _strip_blocks(text: str) -> str:
    for rx in STRIP_BLOCK_RES:
        text = rx.sub("", text)
    return text.strip()


def _parse_command(text: str) -> dict:
    m = COMMAND_NAME_RE.search(text) or COMMAND_MESSAGE_RE.search(text)
    args_m = COMMAND_ARGS_RE.search(text)
    return {"kind": "slash_command", "command": m.group(1) if m else "?",
            "text": args_m.group(1).strip() if args_m else ""}


def classify_user_entry(entry: dict, local_command_parents=frozenset()) -> dict:
    """Classify a JSONL entry on top of transcripts.user_entry_kind.

    Returns {"kind": K, ...} where K is one of:
      "not_user", "meta", "compact_summary", "sidechain", "tool_result",
      "task_notification", "peer", "interrupt",
      "slash_command" (with "command" and "text" = args or ""),
      "local_command" (with "command" and "text"; not a prompt),
      "synthetic", "empty", "genuine" (with "text" and "source" human|sdk).
    `local_command_parents` holds uuids of <local-command-caveat> entries; a
    user entry whose parentUuid is one of them is a local command.
    """
    e = _normalise(entry)
    if e is None:
        return {"kind": "not_user"}
    kind = transcripts.user_entry_kind(e)
    if kind in ("not_user", "sidechain", "task_notification", "peer"):
        return {"kind": kind}
    msg = e["message"]
    raw = _text_blocks(msg.get("content"))
    if kind == "tool_result":
        # Text beside a tool_result is system-added; only the interrupt marker matters.
        return {"kind": "interrupt" if INTERRUPT_PREFIX in raw else "tool_result"}
    if e.get("isCompactSummary"):
        return {"kind": "compact_summary"}
    if any(e.get(f) for f in META_FLAGS):
        return {"kind": "meta"}

    text = _strip_blocks(raw)
    if not text:
        return {"kind": "empty"}
    if text != raw.strip():
        # A leading reminder/hook block made the raw text look injected:
        # classify what the person wrote around it.
        stripped = dict(e, message=dict(msg, content=text))
        kind = transcripts.user_entry_kind(stripped)
    if kind == "interrupt":
        return {"kind": "interrupt"}
    if kind == "command":
        if not text.startswith("<command-"):
            # bare "/cmd args" with no origin metadata (headless/local run)
            m = LOCAL_COMMAND_RE.match(text)
            if m:
                return {"kind": "local_command", "command": m.group(1), "text": m.group(2).strip()}
        return _parse_command(text)
    if kind == "meta" or text.startswith(EXTRA_SYNTH_PREFIXES):
        return {"kind": "synthetic"}
    if e.get("parentUuid") and e.get("parentUuid") in local_command_parents:
        m = LOCAL_COMMAND_RE.match(text)
        if m:
            return {"kind": "local_command", "command": m.group(1), "text": m.group(2).strip()}
    return {"kind": "genuine", "text": text, "source": kind if kind == "sdk" else "human"}


def is_genuine_user_message(entry: dict) -> bool:
    """True when the entry carries user-authored text (including slash-command args)."""
    c = classify_user_entry(entry)
    return c["kind"] == "genuine" or (c["kind"] == "slash_command" and bool(c["text"]))


# --------------------------------------------------------------------------
# Session processing
# --------------------------------------------------------------------------

def parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None


def fmt_ts(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


def _ratio(n, d):
    return round(n / d, 2) if d else None


def _tool_uses(entry: dict):
    """tool_use blocks of an assistant entry."""
    msg = entry.get("message") or {}
    content = msg.get("content") if isinstance(msg, dict) else None
    return [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"] \
        if isinstance(content, list) else []


def _tool_result_texts(entry: dict):
    """(tool_use_id, text) for every tool_result block in a user entry."""
    msg = entry.get("message") or {}
    content = msg.get("content") if isinstance(msg, dict) else None
    out = []
    for b in content if isinstance(content, list) else []:
        if isinstance(b, dict) and b.get("type") == "tool_result":
            c = b.get("content")
            out.append((b.get("tool_use_id"), c if isinstance(c, str) else _text_blocks(c)))
    return out


def _workflow_launch_name(inp: dict):
    for k in ("name", "scriptPath"):
        v = inp.get(k)
        if isinstance(v, str) and v:
            return Path(v).stem if k == "scriptPath" else v
    m = WORKFLOW_SCRIPT_NAME_RE.search(str(inp.get("script") or ""))
    return m.group(1) if m else None


def _effective_depths(agents) -> dict:
    """{agent_id: depth}: the larger of meta spawnDepth and the parentAgentId chain.

    spawnDepth does not count a forked skill as a level, so agents a forked
    skill spawns carry spawnDepth 1 with parentAgentId set; the chain gives 2.
    """
    by_id = {a.agent_id: a for a in agents}
    memo = {}

    def depth(aid, seen=()):
        if aid in memo:
            return memo[aid]
        a = by_id[aid]
        parent = a.meta.get("parentAgentId")
        chain = 1
        if parent in by_id and parent not in seen:
            chain = depth(parent, seen + (aid,)) + 1
        memo[aid] = max(a.spawn_depth, chain)
        return memo[aid]

    return {aid: depth(aid) for aid in by_id}


def delegation_metrics(session, seen_tool_ids: set, launches: dict) -> dict:
    """Delegation summary from a session's agent transcripts and meta files.

    `seen_tool_ids` holds tool_use ids already counted in the main thread, so
    parent requests replayed inside agent transcripts are not counted again.
    `launches` maps workflow run ids found in Workflow tool results to the
    launching tool_use input.
    """
    agents = list(getattr(session, "agents", []) or [])
    by_source = {"agent": 0, "workflow": 0, "team": 0}
    by_type = Counter()
    tool_calls = test_runs = 0
    tool_counts = Counter()
    seen = set(seen_tool_ids)
    for a in agents:
        by_source[a.source] = by_source.get(a.source, 0) + 1
        by_type[a.agent_type] += 1
        for entry in transcripts.iter_jsonl(a.path):
            if entry.get("type") != "assistant":
                continue
            for block in _tool_uses(entry):
                bid = block.get("id")
                if bid:
                    if bid in seen:
                        continue
                    seen.add(bid)
                name = block.get("name", "?")
                tool_calls += 1
                tool_counts[name] += 1
                inp = block.get("input") or {}
                if name == "Bash" and isinstance(inp, dict) \
                        and TEST_RUNNER_RE.search(str(inp.get("command", ""))):
                    test_runs += 1

    runs = session.workflow_runs() if hasattr(session, "workflow_runs") else {}
    for rid, inp in launches.items():
        if rid not in runs:  # launched, but no agents or sidecar on disk
            runs[rid] = {"run_id": rid, "name": None, "agents": 0, "phases": [],
                         "agent_types": {}}
    wf_runs = []
    for rid in sorted(runs):
        r = dict(runs[rid])
        if not r.get("name") and rid in launches:
            r["name"] = _workflow_launch_name(launches[rid])
        r["launched_by_tool_use"] = rid in launches
        r["has_verify_phase"] = any(VERIFY_PHASE_RE.search(p) for p in r["phases"])
        wf_runs.append(r)

    depths = list(_effective_depths(agents).values())
    return {
        "agents_total": len(agents),
        "by_source": by_source,
        "by_type": dict(by_type.most_common()),
        "max_spawn_depth": max(depths) if depths else 0,
        "nested_agents": sum(1 for d in depths if d >= 2),
        "background_agents": sum(1 for a in agents if a.meta.get("requestShape") == "background"),
        "stopped_by_user": sum(1 for a in agents if a.meta.get("stoppedByUser")),
        # forked skills (context: fork) run as agents with a meta file but no
        # spawning tool_use id
        "skill_forks": sum(1 for a in agents if a.meta and not a.meta.get("toolUseId")
                           and not a.workflow_run_id and not a.meta.get("teamName")),
        "delegated_tool_calls": tool_calls,
        "delegated_tool_counts": dict(tool_counts.most_common()),
        "delegated_test_runs": test_runs,
        "workflow_runs": wf_runs,
        "workflow_launches": 0,  # filled in by process_session
    }


def process_session(session, max_chars: int):
    """Parse one session. `session` is a transcripts.SessionFiles or a main
    transcript Path (then no agent transcripts are read). Returns (messages, metrics)."""
    if isinstance(session, (str, Path)):
        path = Path(session)
        session = transcripts.SessionFiles(path.stem, path.parent)
        session.main = path
    session_file = Path(session.main)
    if not session_file.is_file():
        print(f"Warning: cannot read {session_file}", file=sys.stderr)
        return [], None

    messages = []
    seen_turns, seen_tool_ids = set(), set()
    tool_counts = Counter()
    slash_commands, local_commands, skills, subagents = [], [], [], []
    interrupts = compactions = test_runs = 0
    task_notifications = peer_messages = sdk_messages = 0
    timestamps = []
    tools_since_user = 0
    runs = []  # tool_use count between consecutive genuine user messages
    user_count = 0
    cwd = None
    workflow_tool_ids = {}   # tool_use id -> input
    launches = {}            # workflow run id -> launching input
    caveat_uuids = set()

    for entry in transcripts.iter_jsonl(session_file):
        ts = parse_ts(entry.get("timestamp"))
        if ts and not entry.get("isSidechain"):
            timestamps.append(ts)
        cwd = cwd or entry.get("cwd")
        if entry.get("isCompactSummary"):
            compactions += 1

        if entry.get("type") == "assistant" and not entry.get("isSidechain"):
            msg = entry.get("message", {}) or {}
            turn_key = entry.get("requestId") or msg.get("id")
            if msg.get("model") not in SYNTHETIC_MODELS and turn_key:
                seen_turns.add(turn_key)
            for block in _tool_uses(entry):
                bid = block.get("id")
                if bid:
                    if bid in seen_tool_ids:
                        continue
                    seen_tool_ids.add(bid)
                name = block.get("name", "?")
                inp = block.get("input", {}) or {}
                tool_counts[name] += 1
                tools_since_user += 1
                if name in SUBAGENT_TOOLS:
                    subagents.append({
                        "subagent_type": inp.get("subagent_type") or inp.get("subagentType")
                        or "general-purpose",
                        "background": bool(inp.get("run_in_background")),
                    })
                elif name in WORKFLOW_TOOLS:
                    workflow_tool_ids[bid] = inp if isinstance(inp, dict) else {}
                elif name == "Skill":
                    sk = inp.get("skill") or inp.get("name")
                    if sk:
                        skills.append(str(sk))
                elif name == "Bash" and TEST_RUNNER_RE.search(str(inp.get("command", ""))):
                    test_runs += 1
            continue

        if entry.get("type") == "user":
            for tid, text in _tool_result_texts(entry):
                if tid in workflow_tool_ids:
                    extra = json.dumps(entry.get("toolUseResult", ""), default=str)
                    for rid in transcripts.workflow_run_ids_in(text + "\n" + extra):
                        launches.setdefault(rid, workflow_tool_ids[tid])
            if _text_blocks((entry.get("message") or {}).get("content")) \
                    .lstrip().startswith(LOCAL_COMMAND_CAVEAT) and entry.get("uuid"):
                caveat_uuids.add(entry["uuid"])

        c = classify_user_entry(entry, caveat_uuids)
        kind = c["kind"]
        if kind == "interrupt":
            interrupts += 1
        elif kind == "slash_command":
            slash_commands.append(c["command"])
        elif kind == "local_command":
            local_commands.append(c["command"])
            slash_commands.append(c["command"])
        elif kind == "task_notification":
            task_notifications += 1
        elif kind == "peer":
            peer_messages += 1
        if kind == "genuine" or (kind == "slash_command" and c["text"]):
            if user_count:
                runs.append(tools_since_user)
            user_count += 1
            tools_since_user = 0
            text = c["text"]
            source = c.get("source", "command")
            if source == "sdk":
                sdk_messages += 1
            msg_out = {
                "timestamp": fmt_ts(ts),
                "session": session_file.stem,
                "project_dir": session_file.parent.name,
                "text": text[:max_chars],
                "truncated": len(text) > max_chars,
                "source": source,
            }
            if kind == "slash_command":
                msg_out["slash_command"] = c["command"]
            messages.append(msg_out)
    if user_count:
        runs.append(tools_since_user)

    delegation = delegation_metrics(session, seen_tool_ids, launches)
    delegation["workflow_launches"] = len(workflow_tool_ids)

    timestamps.sort()
    duration = active = None
    if timestamps:
        duration = int((timestamps[-1] - timestamps[0]).total_seconds())
        active = int(sum(min((b - a).total_seconds(), ACTIVE_GAP_CAP_SECONDS)
                         for a, b in zip(timestamps, timestamps[1:])))
    tool_total = sum(tool_counts.values())
    metrics = {
        "session": session_file.stem,
        "project_dir": session_file.parent.name,
        "cwd": cwd,
        "start": fmt_ts(timestamps[0]) if timestamps else None,
        "end": fmt_ts(timestamps[-1]) if timestamps else None,
        "duration_seconds": duration,
        "active_seconds": active,
        "user_messages": user_count,
        "sdk_messages": sdk_messages,
        "assistant_turns": len(seen_turns),
        "tool_uses": tool_total,
        "assistant_turns_per_user_message": _ratio(len(seen_turns), user_count),
        "tool_uses_per_user_message": _ratio(tool_total, user_count),
        "max_tool_uses_between_user_messages": max(runs) if runs else 0,
        "tool_counts": dict(tool_counts.most_common()),
        "slash_commands": slash_commands,
        "local_commands": local_commands,
        "skills_invoked": skills,
        "subagent_spawns": len(subagents),
        "subagent_types": dict(Counter(s["subagent_type"] for s in subagents)),
        "background_subagents": sum(1 for s in subagents if s["background"]),
        "test_runs": test_runs,
        "interrupts": interrupts,
        "compactions": compactions,
        "task_notifications": task_notifications,
        "peer_messages": peer_messages,
        "delegation": delegation,
    }
    return messages, metrics


def summarise(sessions: list) -> dict:
    users = sum(s["user_messages"] for s in sessions)
    turns = sum(s["assistant_turns"] for s in sessions)
    tools = sum(s["tool_uses"] for s in sessions)
    spawns = sum(s["subagent_spawns"] for s in sessions)
    interrupts = sum(s["interrupts"] for s in sessions)
    sub_types, skills, cmds, tool_counts = Counter(), Counter(), Counter(), Counter()
    agent_types, agent_sources = Counter(), Counter({"agent": 0, "workflow": 0, "team": 0})
    for s in sessions:
        sub_types.update(s["subagent_types"])
        skills.update(s["skills_invoked"])
        cmds.update(s["slash_commands"])
        tool_counts.update(s["tool_counts"])
        agent_types.update(s["delegation"]["by_type"])
        agent_sources.update(s["delegation"]["by_source"])
    dl = [s["delegation"] for s in sessions]
    wf_runs = [r for d in dl for r in d["workflow_runs"]]
    durations = [s["duration_seconds"] for s in sessions if s["duration_seconds"] is not None]
    actives = [s["active_seconds"] for s in sessions if s["active_seconds"] is not None]
    per_session_ratio = [s["tool_uses_per_user_message"] for s in sessions
                         if s["tool_uses_per_user_message"] is not None]
    starts = sorted(s["start"] for s in sessions if s["start"])
    ends = sorted(s["end"] for s in sessions if s["end"])
    delegated_calls = sum(d["delegated_tool_calls"] for d in dl)
    return {
        "sessions": len(sessions),
        "sessions_with_user_messages": sum(1 for s in sessions if s["user_messages"]),
        "date_range": [starts[0], ends[-1]] if starts and ends else None,
        "user_messages": users,
        "sdk_messages": sum(s["sdk_messages"] for s in sessions),
        "assistant_turns": turns,
        "tool_uses": tools,
        "assistant_turns_per_user_message": _ratio(turns, users),
        "tool_uses_per_user_message": _ratio(tools, users),
        "median_session_tool_uses_per_user_message":
            round(statistics.median(per_session_ratio), 2) if per_session_ratio else None,
        "max_tool_uses_between_user_messages":
            max((s["max_tool_uses_between_user_messages"] for s in sessions), default=0),
        "sessions_with_20plus_tool_run":
            sum(1 for s in sessions if s["max_tool_uses_between_user_messages"] >= 20),
        "median_duration_seconds": int(statistics.median(durations)) if durations else None,
        "median_active_seconds": int(statistics.median(actives)) if actives else None,
        "subagent_spawns": spawns,
        "sessions_with_subagents": sum(1 for s in sessions if s["subagent_spawns"]),
        "subagent_types": dict(sub_types.most_common()),
        "skills_invoked": dict(skills.most_common()),
        "slash_commands": dict(cmds.most_common()),
        "sessions_using_skills_or_commands":
            sum(1 for s in sessions if s["skills_invoked"] or s["slash_commands"]),
        "test_runs": sum(s["test_runs"] for s in sessions),
        "sessions_with_test_runs": sum(1 for s in sessions if s["test_runs"]),
        "interrupts": interrupts,
        "interrupts_per_100_user_messages": _ratio(interrupts * 100, users),
        "compactions": sum(s["compactions"] for s in sessions),
        "top_tools": dict(tool_counts.most_common(12)),
        # delegation (from agent transcripts, not just the main thread)
        "delegated_agents": sum(d["agents_total"] for d in dl),
        "sessions_with_delegated_agents": sum(1 for d in dl if d["agents_total"]),
        "delegated_agents_by_source": dict(agent_sources),
        "delegated_agent_types": dict(agent_types.most_common()),
        "max_spawn_depth": max((d["max_spawn_depth"] for d in dl), default=0),
        "nested_agents": sum(d["nested_agents"] for d in dl),
        "sessions_with_nested_agents": sum(1 for d in dl if d["nested_agents"]),
        "background_agents": sum(d["background_agents"] for d in dl),
        "agents_stopped_by_user": sum(d["stopped_by_user"] for d in dl),
        "skill_forks": sum(d["skill_forks"] for d in dl),
        "delegated_tool_calls": delegated_calls,
        "delegated_tool_calls_share":
            round(delegated_calls / (delegated_calls + tools), 2) if delegated_calls + tools else None,
        "delegated_test_runs": sum(d["delegated_test_runs"] for d in dl),
        "sessions_with_any_test_runs":
            sum(1 for s in sessions if s["test_runs"] or s["delegation"]["delegated_test_runs"]),
        "workflow_launches": sum(d["workflow_launches"] for d in dl),
        "workflow_runs": len(wf_runs),
        "workflow_agents": sum(r["agents"] for r in wf_runs),
        "workflow_runs_with_verify_phase": sum(1 for r in wf_runs if r["has_verify_phase"]),
        "sessions_with_workflows": sum(1 for d in dl if d["workflow_runs"] or d["workflow_launches"]),
        "task_notifications": sum(s["task_notifications"] for s in sessions),
        "peer_messages": sum(s["peer_messages"] for s in sessions),
    }


# --------------------------------------------------------------------------
# Retention
# --------------------------------------------------------------------------

def check_log_retention(claude_dir: Path) -> dict:
    """Report cleanupPeriodDays from <claude_dir>/settings.json. Never writes."""
    settings_path = claude_dir / "settings.json"
    result = {
        "settings_exists": settings_path.exists(),
        "path": str(settings_path),
        "cleanup_period_days": None,
        "effective_days": DEFAULT_RETENTION_DAYS,
        "is_default": True,
    }
    if settings_path.exists():
        try:
            with open(settings_path, encoding="utf-8") as fh:
                settings = json.load(fh)
            days = settings.get("cleanupPeriodDays") if isinstance(settings, dict) else None
            if days is not None:
                result.update(cleanup_period_days=days, effective_days=days, is_default=False)
        except (OSError, ValueError) as exc:
            result["read_error"] = str(exc)
            print(f"Warning: cannot parse {settings_path}: {exc}", file=sys.stderr)
    eff = result["effective_days"]
    result["recommend_change"] = not isinstance(eff, (int, float)) or eff < 60
    result["recommended_snippet"] = {"cleanupPeriodDays": RECOMMENDED_RETENTION_DAYS}
    return result


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Extract genuine user messages and session metrics from Claude Code logs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--claude-dir", default=str(DEFAULT_CLAUDE_DIR),
                   help="Claude config dir (default: ~/.claude)")
    p.add_argument("--projects-dir", default=None,
                   help="Session log root (default: <claude-dir>/projects)")
    p.add_argument("--all", action="store_true", help="Scan every project dir")
    p.add_argument("--project", default=os.getcwd(),
                   help="Absolute project path to match (default: cwd)")
    p.add_argument("--include-subdirs", action="store_true",
                   help="Also include logs from sessions started in subdirectories of --project")
    p.add_argument("--limit", type=int, default=300, help="Max messages to output")
    p.add_argument("--max-chars", type=int, default=1200,
                   help="Truncate each message to this many characters")
    p.add_argument("--recent-sessions", type=int, default=30,
                   help="Max most-recent session files to scan")
    p.add_argument("--output-format", choices=["text", "json"], default="text")
    p.add_argument("--stats-only", action="store_true",
                   help="Print stats + aggregate summary only, no message text")
    p.add_argument("--check-retention", action="store_true",
                   help="Print log retention settings and exit (read-only)")
    p.add_argument("--check-projects", action="store_true",
                   help="Print project-dir matching diagnostics and exit (no message content)")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    claude_dir = Path(args.claude_dir).expanduser()
    projects_dir = Path(args.projects_dir).expanduser() if args.projects_dir \
        else claude_dir / "projects"

    if args.check_retention:
        print(json.dumps(check_log_retention(claude_dir), indent=2))
        return 0

    if args.check_projects:
        project = os.path.abspath(os.path.expanduser(args.project))
        matched = find_matching_project_dirs(projects_dir, project, args.include_subdirs)
        all_dirs = sorted(d.name for d in projects_dir.iterdir() if d.is_dir()) \
            if projects_dir.is_dir() else []
        matched_info = []
        for d in matched:
            found = transcripts.discover_project(d)
            matched_info.append({
                "dir": d.name,
                "sessions": sum(1 for s in found.values() if s.main is not None),
                "agent_transcripts": sum(len(s.agents) for s in found.values()),
                "workflow_runs": sum(len(s.workflow_runs()) for s in found.values()),
            })
        print(json.dumps({
            "projects_dir": str(projects_dir),
            "projects_dir_exists": projects_dir.is_dir(),
            "project": project,
            "expected_dir_name": encode_project_path(project),
            "worktree_paths": related_worktree_paths(project),
            "matched": matched_info,
            "all_project_dirs": all_dirs,
        }, indent=2))
        return 0

    empty = {"stats": {}, "summary": {}, "sessions": [], "messages": []}
    if not projects_dir.is_dir():
        print(f"No Claude projects directory at: {projects_dir}", file=sys.stderr)
        print(json.dumps(empty) if args.output_format == "json" else "No logs found.")
        return 0

    if args.all:
        project_dirs = sorted(d for d in projects_dir.iterdir() if d.is_dir())
        print(f"Scanning all {len(project_dirs)} project dir(s) in {projects_dir}", file=sys.stderr)
    else:
        project_dirs = find_matching_project_dirs(projects_dir, args.project, args.include_subdirs)
        if not project_dirs:
            print(json.dumps(empty) if args.output_format == "json"
                  else "No matching project logs found.")
            return 0
        print(f"Matched {len(project_dirs)} project dir(s): "
              f"{', '.join(d.name for d in project_dirs)}", file=sys.stderr)

    session_list = collect_sessions(project_dirs, args.recent_sessions)
    print(f"Processing {len(session_list)} session(s) "
          f"({sum(len(s.agents) for s in session_list)} agent transcript(s))...", file=sys.stderr)

    all_messages, sessions = [], []
    for sf in session_list:
        msgs, metrics = process_session(sf, args.max_chars)
        all_messages.extend(msgs)
        if metrics is not None:
            sessions.append(metrics)

    all_messages.sort(key=lambda m: m.get("timestamp", ""), reverse=True)
    total_messages = len(all_messages)
    all_messages = all_messages[:args.limit]

    retention = check_log_retention(claude_dir)
    stats = {
        "messages_total": total_messages,
        "messages_returned": len(all_messages),
        "messages_truncated": sum(1 for m in all_messages if m["truncated"]),
        "sessions_scanned": len(session_list),
        "agent_transcripts_scanned": sum(len(s.agents) for s in session_list),
        "project_dirs": [d.name for d in project_dirs],
        "log_retention_days": retention["effective_days"],
        "log_retention_is_default": retention["is_default"],
    }
    summary = summarise(sessions)
    print(f"Stats: {json.dumps(stats)}", file=sys.stderr)

    if args.stats_only:
        print(json.dumps({"stats": stats, "summary": summary}, indent=2))
        return 0

    if args.output_format == "json":
        print(json.dumps({"stats": stats, "summary": summary, "sessions": sessions,
                          "messages": all_messages}, indent=2, ensure_ascii=False))
        return 0

    note = (f"{retention['effective_days']} days"
            + (" (default; cleanupPeriodDays not set)" if retention["is_default"] else ""))
    print(f"Log retention: {note}\n")
    print("=== Summary ===")
    print(json.dumps(summary, indent=2))
    print(f"\n=== {len(all_messages)} of {total_messages} user messages from "
          f"{len(session_list)} sessions ===\n")
    for i, msg in enumerate(all_messages, 1):
        cmd = f"  /{msg['slash_command']}" if msg.get("slash_command") else ""
        if msg.get("source") == "sdk":
            cmd += "  [sdk]"
        trunc = " [truncated]" if msg["truncated"] else ""
        print(f"[{i}] {msg['timestamp'] or 'unknown time'}  |  {msg['project_dir'][:40]}{cmd}")
        print(f"    {msg['text']}{trunc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
