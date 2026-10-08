#!/usr/bin/env python3
"""
Extract genuine user messages and per-session structural metrics from
Claude Code JSONL session logs (~/.claude/projects/<encoded-path>/<session>.jsonl).

A "genuine" user message is text the user typed. Filtered OUT:
  - tool_result entries (role=user but automated tool output)
  - isMeta entries (injected skill bodies, hook feedback, local-command caveats)
  - isCompactSummary entries (auto-generated compaction summaries)
  - isSidechain entries and anything under subagents/ (subagent transcripts)
  - <command-name>/<command-message>/<command-args> wrappers: the slash command
    is counted in metrics; the text inside <command-args> IS user-authored and
    is kept as the message text when non-empty
  - <local-command-stdout>/<local-command-stderr>/<bash-stdout>/<bash-stderr>,
    "Caveat:" notices, <task-notification>/<agent-message> injections
  - "[Request interrupted" markers (counted as interrupts)
  - <system-reminder> and <user-prompt-submit-hook> blocks (stripped from
    otherwise genuine text)

Project matching: Claude Code names each project dir by replacing every
non-alphanumeric character of the absolute path with '-'. --project matches
that encoding exactly. --include-subdirs also takes dirs for subdirectories,
confirmed against the session's recorded cwd.

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

DEFAULT_CLAUDE_DIR = Path.home() / ".claude"
DEFAULT_RETENTION_DAYS = 30  # Claude Code default when cleanupPeriodDays is unset
RECOMMENDED_RETENTION_DAYS = 90
ACTIVE_GAP_CAP_SECONDS = 30 * 60  # gaps longer than this count as idle

# Parsing patterns adapted from
# plugins/cc-usage-classifier/skills/cc-usage-classifier/scripts/extract.py
# (COMMAND_NAME_RE, SYNTH_PREFIXES, TEST_RUNNER_RE, Task/Agent + Skill
# tool_use handling, requestId turn dedupe).
# Copied rather than imported: plugins install independently.
COMMAND_NAME_RE = re.compile(r"<command-name>\s*/?([^<\s]+)\s*</command-name>")
COMMAND_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.DOTALL)
STRIP_BLOCK_RES = (
    re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL),
    re.compile(r"<user-prompt-submit-hook>.*?</user-prompt-submit-hook>", re.DOTALL),
)
INTERRUPT_PREFIX = "[Request interrupted"
SYNTH_PREFIXES = (
    "<command-message>", "<command-args>",
    "<local-command-", "<bash-stdout>", "<bash-stderr>", "<bash-input>",
    "<task-notification>", "<agent-message", "Caveat:",
    "Stop hook feedback:", "PreToolUse:", "PostToolUse:",
)
SYNTHETIC_MODELS = {None, "", "<synthetic>"}
SUBAGENT_TOOLS = ("Task", "Agent")
TEST_RUNNER_RE = re.compile(
    r"\b(pytest|py\.test|unittest|tox|nox|npm\s+(?:run\s+)?test|yarn\s+test|pnpm\s+test|"
    r"jest|vitest|mocha|go\s+test|cargo\s+test|rspec|rails\s+test|"
    r"phpunit|dotnet\s+test|gradle\w*\s+test|mvn\s+test|ctest|make\s+test)\b"
)


# --------------------------------------------------------------------------
# Project directory matching
# --------------------------------------------------------------------------

def encode_project_path(path: str) -> str:
    """Encode an absolute path the way Claude Code names ~/.claude/projects dirs."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def _session_cwd(proj_dir: Path):
    """Return the first recorded cwd in any top-level session file, or None."""
    for f in sorted(proj_dir.glob("*.jsonl")):
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                for i, line in enumerate(fh):
                    if i > 50:
                        break
                    try:
                        cwd = json.loads(line).get("cwd")
                    except (json.JSONDecodeError, AttributeError):
                        continue
                    if cwd:
                        return cwd
        except OSError:
            continue
    return None


def find_matching_project_dirs(projects_dir: Path, project: str,
                               include_subdirs: bool = False,
                               report_candidates: bool = True) -> list:
    """Exact match on Claude Code's path encoding. Never guesses.

    Subdirectory dirs (opt-in) share the encoded prefix; because '/' and '-'
    encode identically, each is confirmed via the session's recorded cwd so
    /work/api does not pick up /work/api-gateway.
    """
    if not projects_dir.is_dir():
        return []
    raw = os.path.abspath(os.path.expanduser(project))
    paths = {raw, os.path.realpath(raw)}
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


def collect_session_files(project_dirs: list, recent_sessions: int) -> list:
    """Top-level <session>.jsonl files only (subagent transcripts live in subagents/)."""
    files = []
    for proj_dir in project_dirs:
        for item in proj_dir.iterdir():
            if item.is_file() and item.suffix == ".jsonl":
                files.append(item)
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return files[:recent_sessions]


# --------------------------------------------------------------------------
# User entry classification
# --------------------------------------------------------------------------

def _content_of(entry: dict):
    """Return (role, content) for both {type:user,message:{...}} and bare shapes."""
    if entry.get("type") == "user":
        inner = entry.get("message", {})
        if not isinstance(inner, dict):
            return None, None
        return inner.get("role"), inner.get("content")
    return entry.get("role"), entry.get("content")


def _text_blocks(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        b["text"] for b in content
        if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
    )


def classify_user_entry(entry: dict) -> dict:
    """Classify a JSONL entry.

    Returns {"kind": K, ...} where K is one of:
      "not_user", "meta", "compact_summary", "sidechain", "tool_result",
      "interrupt", "slash_command" (with "command" and "text" = args or ""),
      "synthetic", "empty", "genuine" (with "text").
    """
    etype = entry.get("type", "")
    if etype and etype != "user":
        return {"kind": "not_user"}
    role, content = _content_of(entry)
    if role != "user":
        return {"kind": "not_user"}
    if entry.get("isSidechain"):
        return {"kind": "sidechain"}
    if entry.get("isCompactSummary"):
        return {"kind": "compact_summary"}
    if entry.get("isMeta"):
        return {"kind": "meta"}

    text = _text_blocks(content)
    has_tool_result = isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
    if has_tool_result or "toolUseResult" in entry:
        # Text beside a tool_result is system-added; only the interrupt marker matters.
        if INTERRUPT_PREFIX in text:
            return {"kind": "interrupt"}
        return {"kind": "tool_result"}

    for rx in STRIP_BLOCK_RES:
        text = rx.sub("", text)
    text = text.strip()
    if not text:
        return {"kind": "empty"}
    if text.startswith(INTERRUPT_PREFIX):
        return {"kind": "interrupt"}

    m = COMMAND_NAME_RE.search(text)
    if m:
        args_m = COMMAND_ARGS_RE.search(text)
        args = args_m.group(1).strip() if args_m else ""
        return {"kind": "slash_command", "command": m.group(1), "text": args}
    if text.startswith(SYNTH_PREFIXES):
        return {"kind": "synthetic"}
    return {"kind": "genuine", "text": text}


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


def process_session(session_file: Path, max_chars: int):
    """Parse one session file. Returns (messages, metrics)."""
    messages = []
    seen_turns, seen_tool_ids = set(), set()
    tool_counts = Counter()
    slash_commands, skills, subagents = [], [], []
    interrupts = compactions = test_runs = 0
    timestamps = []
    tools_since_user = 0
    runs = []  # tool_use count between consecutive genuine user messages
    user_count = 0
    cwd = None

    try:
        fh = open(session_file, encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"Warning: cannot read {session_file}: {exc}", file=sys.stderr)
        return [], None

    with fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
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
                content = msg.get("content")
                for block in content if isinstance(content, list) else []:
                    if not isinstance(block, dict) or block.get("type") != "tool_use":
                        continue
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
                    elif name == "Skill":
                        sk = inp.get("skill") or inp.get("name")
                        if sk:
                            skills.append(str(sk))
                    elif name == "Bash" and TEST_RUNNER_RE.search(str(inp.get("command", ""))):
                        test_runs += 1
                continue

            c = classify_user_entry(entry)
            kind = c["kind"]
            if kind == "interrupt":
                interrupts += 1
            elif kind == "slash_command":
                slash_commands.append(c["command"])
            if kind == "genuine" or (kind == "slash_command" and c["text"]):
                if user_count:
                    runs.append(tools_since_user)
                user_count += 1
                tools_since_user = 0
                text = c["text"]
                msg_out = {
                    "timestamp": fmt_ts(ts),
                    "session": session_file.stem,
                    "project_dir": session_file.parent.name,
                    "text": text[:max_chars],
                    "truncated": len(text) > max_chars,
                }
                if kind == "slash_command":
                    msg_out["slash_command"] = c["command"]
                messages.append(msg_out)
    if user_count:
        runs.append(tools_since_user)

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
        "assistant_turns": len(seen_turns),
        "tool_uses": tool_total,
        "assistant_turns_per_user_message": _ratio(len(seen_turns), user_count),
        "tool_uses_per_user_message": _ratio(tool_total, user_count),
        "max_tool_uses_between_user_messages": max(runs) if runs else 0,
        "tool_counts": dict(tool_counts.most_common()),
        "slash_commands": slash_commands,
        "skills_invoked": skills,
        "subagent_spawns": len(subagents),
        "subagent_types": dict(Counter(s["subagent_type"] for s in subagents)),
        "background_subagents": sum(1 for s in subagents if s["background"]),
        "test_runs": test_runs,
        "interrupts": interrupts,
        "compactions": compactions,
    }
    return messages, metrics


def summarise(sessions: list) -> dict:
    users = sum(s["user_messages"] for s in sessions)
    turns = sum(s["assistant_turns"] for s in sessions)
    tools = sum(s["tool_uses"] for s in sessions)
    spawns = sum(s["subagent_spawns"] for s in sessions)
    interrupts = sum(s["interrupts"] for s in sessions)
    sub_types, skills, cmds, tool_counts = Counter(), Counter(), Counter(), Counter()
    for s in sessions:
        sub_types.update(s["subagent_types"])
        skills.update(s["skills_invoked"])
        cmds.update(s["slash_commands"])
        tool_counts.update(s["tool_counts"])
    durations = [s["duration_seconds"] for s in sessions if s["duration_seconds"] is not None]
    actives = [s["active_seconds"] for s in sessions if s["active_seconds"] is not None]
    per_session_ratio = [s["tool_uses_per_user_message"] for s in sessions
                         if s["tool_uses_per_user_message"] is not None]
    starts = sorted(s["start"] for s in sessions if s["start"])
    ends = sorted(s["end"] for s in sessions if s["end"])
    return {
        "sessions": len(sessions),
        "sessions_with_user_messages": sum(1 for s in sessions if s["user_messages"]),
        "date_range": [starts[0], ends[-1]] if starts and ends else None,
        "user_messages": users,
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
        print(json.dumps({
            "projects_dir": str(projects_dir),
            "projects_dir_exists": projects_dir.is_dir(),
            "project": project,
            "expected_dir_name": encode_project_path(project),
            "matched": [{"dir": d.name, "sessions": len(list(d.glob("*.jsonl")))}
                        for d in matched],
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

    session_files = collect_session_files(project_dirs, args.recent_sessions)
    print(f"Processing {len(session_files)} session file(s)...", file=sys.stderr)

    all_messages, sessions = [], []
    for sf in session_files:
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
        "sessions_scanned": len(session_files),
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
          f"{len(session_files)} sessions ===\n")
    for i, msg in enumerate(all_messages, 1):
        cmd = f"  /{msg['slash_command']}" if msg.get("slash_command") else ""
        trunc = " [truncated]" if msg["truncated"] else ""
        print(f"[{i}] {msg['timestamp'] or 'unknown time'}  |  {msg['project_dir'][:40]}{cmd}")
        print(f"    {msg['text']}{trunc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
