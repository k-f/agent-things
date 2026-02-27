#!/usr/bin/env python3
"""
Extract genuine user messages from Claude Code JSONL session logs.

Filters OUT:
  - Tool result messages (role=user but content type=tool_result)
  - Sub-agent session files (in subagents/ subdirectories)
  - System/synthetic messages
  - Empty messages

Usage:
  python3 extract_user_messages.py [OPTIONS]

Options:
  --all                    Scan logs from all projects (default: current project only)
  --project PATH           Absolute path to project (default: current directory)
  --limit N                Max messages to return (default: 300)
  --max-chars N            Max characters per message before truncation (default: 600)
  --recent-sessions N      Max recent sessions to scan per project (default: 30)
  --output-format FORMAT   'text' (default) or 'json'
  --stats-only             Print only statistics, no message content

Output (stdout): extracted messages
Diagnostics (stderr): warnings, stats
"""

import json
import os
import sys
import argparse
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter


CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"


def find_matching_project_dirs(projects_dir: Path, cwd: str) -> list[Path]:
    """
    Heuristically find Claude project directories matching the given path.
    Claude encodes absolute paths as directory names (e.g. replacing / with -).
    Returns matching dirs sorted by best match score descending.
    """
    if not projects_dir.exists():
        return []

    cwd_path = Path(cwd).resolve()
    # Build a set of meaningful path parts (skip root, home, very short parts)
    cwd_parts = [p for p in cwd_path.parts if len(p) > 2 and p not in ('/', 'home', 'Users')]

    scored = []
    for proj_dir in projects_dir.iterdir():
        if not proj_dir.is_dir():
            continue
        # Normalise directory name for matching: treat -, _, %, digits-after-percent as separators
        normalised = proj_dir.name.lower().replace('-', '/').replace('%2f', '/').replace('%20', ' ')
        score = sum(1 for part in cwd_parts if part.lower() in normalised)
        if score > 0:
            scored.append((score, proj_dir))

    scored.sort(key=lambda x: -x[0])
    return [d for _, d in scored]


def collect_session_files(project_dirs: list[Path], recent_sessions: int) -> list[Path]:
    """
    Collect main session JSONL files from project dirs.
    Deliberately excludes files inside 'subagents/' subdirectories.
    """
    files = []
    for proj_dir in project_dirs:
        for item in proj_dir.iterdir():
            # Only top-level .jsonl files — sub-agent files are in subagents/<session>/
            if item.is_file() and item.suffix == '.jsonl':
                files.append(item)

    # Sort newest first, then cap per-project limit
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return files[:recent_sessions]


def is_genuine_user_message(entry: dict) -> bool:
    """
    Return True only for messages that represent actual user keyboard input.

    Claude Code JSONL has several entry shapes:
      Shape A: {"type": "user", "message": {"role": "user", "content": [...]}}
      Shape B: {"role": "user", "content": [...]}
      Shape C: {"type": "summary", ...}  — skip

    Within 'user' role messages, content may be:
      - [{"type": "text", "text": "..."}]         → genuine user message
      - [{"type": "tool_result", "tool_use_id": ...}]  → automated tool response, skip
      - mixed text + tool_result                  → keep (text part is user-authored)
    """
    entry_type = entry.get('type', '')

    # Skip non-user entry types
    if entry_type and entry_type not in ('user',):
        return False

    # Resolve the content array depending on shape
    if entry_type == 'user':
        inner = entry.get('message', {})
        if not isinstance(inner, dict):
            return False
        role = inner.get('role', '')
        content = inner.get('content', [])
    else:
        role = entry.get('role', '')
        content = entry.get('content', [])

    if role != 'user':
        return False

    # Content can be a plain string
    if isinstance(content, str):
        return bool(content.strip())

    if not isinstance(content, list):
        return False

    # Must have at least one text block with non-empty text
    for block in content:
        if isinstance(block, dict) and block.get('type') == 'text':
            text = block.get('text', '')
            if isinstance(text, str) and text.strip():
                return True

    return False


def extract_text(entry: dict) -> str:
    """Pull the human-readable text from a validated user entry."""
    entry_type = entry.get('type', '')

    if entry_type == 'user':
        content = entry.get('message', {}).get('content', [])
    else:
        content = entry.get('content', [])

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get('type') == 'text':
                t = block.get('text', '')
                if t and t.strip():
                    parts.append(t.strip())
        return '\n'.join(parts)

    return ''


def extract_timestamp(entry: dict) -> str:
    ts = entry.get('timestamp') or entry.get('ts') or ''
    if ts:
        # Normalise to readable format if ISO
        try:
            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            return dt.strftime('%Y-%m-%d %H:%M')
        except Exception:
            return str(ts)
    return ''


def process_session(session_file: Path, max_chars: int) -> list[dict]:
    """Parse a single JSONL session file and return list of user message dicts."""
    messages = []
    try:
        with open(session_file, encoding='utf-8', errors='replace') as fh:
            for raw_line in fh:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if not is_genuine_user_message(entry):
                    continue

                text = extract_text(entry)
                if not text:
                    continue

                messages.append({
                    'timestamp': extract_timestamp(entry),
                    'session': session_file.stem[:12],
                    'project_dir': session_file.parent.name,
                    'text': text[:max_chars],
                    'truncated': len(text) > max_chars,
                })
    except OSError as exc:
        print(f"Warning: cannot read {session_file}: {exc}", file=sys.stderr)

    return messages


def check_log_retention(claude_dir: Path) -> dict:
    """Read ~/.claude/settings.json and report log retention configuration."""
    settings_path = claude_dir / "settings.json"
    result = {
        'settings_exists': settings_path.exists(),
        'cleanup_period_days': None,
        'path': str(settings_path),
    }
    if settings_path.exists():
        try:
            with open(settings_path, encoding='utf-8') as fh:
                settings = json.load(fh)
            result['cleanup_period_days'] = settings.get('cleanupPeriodDays')
            result['raw_settings'] = settings
        except Exception as exc:
            result['read_error'] = str(exc)
    return result


def main():
    parser = argparse.ArgumentParser(
        description='Extract genuine user messages from Claude Code session logs',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('--all', action='store_true',
                        help='Scan all projects, not just the current one')
    parser.add_argument('--project', default=os.getcwd(),
                        help='Absolute path to current project (default: cwd)')
    parser.add_argument('--limit', type=int, default=300,
                        help='Maximum number of messages to output')
    parser.add_argument('--max-chars', type=int, default=600,
                        help='Truncate each message to this many characters')
    parser.add_argument('--recent-sessions', type=int, default=30,
                        help='Maximum recent session files to scan')
    parser.add_argument('--output-format', choices=['text', 'json'], default='text')
    parser.add_argument('--stats-only', action='store_true',
                        help='Only print statistics, skip message content')
    parser.add_argument('--check-retention', action='store_true',
                        help='Print log retention settings and exit')

    args = parser.parse_args()

    # Retention check mode
    if args.check_retention:
        info = check_log_retention(CLAUDE_DIR)
        print(json.dumps(info, indent=2, default=str))
        sys.exit(0)

    if not PROJECTS_DIR.exists():
        print(f"No Claude projects directory found at: {PROJECTS_DIR}", file=sys.stderr)
        print("[]" if args.output_format == 'json' else "No logs found.")
        sys.exit(0)

    # Discover project directories
    if args.all:
        project_dirs = [d for d in PROJECTS_DIR.iterdir() if d.is_dir()]
        print(f"Scanning all {len(project_dirs)} project(s) in {PROJECTS_DIR}", file=sys.stderr)
    else:
        project_dirs = find_matching_project_dirs(PROJECTS_DIR, args.project)
        if not project_dirs:
            print(f"No matching project logs found for: {args.project}", file=sys.stderr)
            print("Tip: use --all to scan all projects instead.", file=sys.stderr)
            print("[]" if args.output_format == 'json' else "No matching project logs found.")
            sys.exit(0)
        print(f"Found {len(project_dirs)} matching project dir(s) for: {args.project}", file=sys.stderr)

    # Collect + process sessions
    session_files = collect_session_files(project_dirs, args.recent_sessions)
    print(f"Processing {len(session_files)} session file(s)...", file=sys.stderr)

    all_messages: list[dict] = []
    for sf in session_files:
        msgs = process_session(sf, args.max_chars)
        all_messages.extend(msgs)

    # Sort newest first, then apply global limit
    all_messages.sort(key=lambda m: m.get('timestamp', ''), reverse=True)
    all_messages = all_messages[:args.limit]

    # Stats
    retention = check_log_retention(CLAUDE_DIR)
    stats = {
        'total_messages_extracted': len(all_messages),
        'sessions_scanned': len(session_files),
        'projects_scanned': len(project_dirs),
        'log_retention_days': retention.get('cleanup_period_days'),
        'settings_path': retention['path'],
        'settings_exists': retention['settings_exists'],
    }
    print(f"Stats: {json.dumps(stats)}", file=sys.stderr)

    if args.stats_only:
        print(json.dumps(stats, indent=2))
        sys.exit(0)

    # Output
    if args.output_format == 'json':
        output = {
            'stats': stats,
            'messages': all_messages,
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        # --- Retention notice ---
        cleanup = retention.get('cleanup_period_days')
        if cleanup is None:
            print("NOTE: cleanupPeriodDays not set in ~/.claude/settings.json "
                  "(default is likely 30 days). Consider increasing for richer history.\n")
        else:
            print(f"NOTE: Log retention is set to {cleanup} days.\n")

        print(f"=== {len(all_messages)} user messages from {len(session_files)} sessions "
              f"across {len(project_dirs)} project(s) ===\n")

        for i, msg in enumerate(all_messages, 1):
            ts = msg.get('timestamp') or 'unknown time'
            proj = msg.get('project_dir', '')[:30]
            trunc = ' [truncated]' if msg.get('truncated') else ''
            print(f"[{i}] {ts}  |  {proj}")
            print(f"    {msg['text']}{trunc}")
            print()


if __name__ == '__main__':
    main()
