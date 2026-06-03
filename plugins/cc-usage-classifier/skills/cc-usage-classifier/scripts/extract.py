#!/usr/bin/env python3
"""
cc-usage-classifier — deterministic transcript extractor.

Implements Components 1, 3, 4, 5, 6 of the cc-usage-classifier skill:
one intermediate JSON record per Claude Code session, built entirely from
local JSONL transcripts. No network, no model calls.

Session id == JSONL filename stem (the join key to any future OTel/admin data).

Key correctness facts (confirmed against real transcripts + ccusage):
  - usage lives at entry["message"]["usage"]; the model id at
    entry["message"]["model"].
  - One logical assistant turn (one requestId / message.id) is written as
    several entries (one per content block) that ALL repeat the identical
    usage object. We therefore DEDUPLICATE per (requestId | message.id) and
    count its usage exactly once, PER MODEL. Naive summation corrupts
    input/output totals.
  - cache_creation is split into ephemeral_5m / ephemeral_1h when present.

Usage:
  python3 extract.py [--projects-dir DIR] [--out-dir DIR]
                     [--jira-projects ABC,DEF] [--redact-prompts]
                     [--no-incremental]

Outputs (under --out-dir, default ~/.claude/cc-usage-classifier/out):
  extracted.jsonl   one intermediate record per session
  cache.json        {session_id: {hash, classification?}} for incremental runs
Stdout: JSON {"todo": [...session_ids needing classification...],
              "cached": [...], "total": N, "out_dir": "..."}
"""

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
DEFAULT_OUT_DIR = Path.home() / ".claude" / "cc-usage-classifier" / "out"

# --- bounds for the classifier payload (no raw files / full tool output) ---
MAX_PROMPTS = 80
MAX_PROMPT_CHARS = 1500
MAX_BASH_CMDS = 120
MAX_BASH_CHARS = 200
MAX_ASSISTANT_SNIPPETS = 25
MAX_ASSISTANT_CHARS = 400
MAX_SCAN_CHARS = 20000  # per tool-result, for identifier/repo scanning

SYNTHETIC_MODELS = {None, "", "<synthetic>"}

JIRA_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")
PR_URL_RE = re.compile(r"github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)")
BARE_PR_RE = re.compile(r"(?<![\w/])#(\d{1,7})\b")
GH_PR_CREATE_RE = re.compile(r"\bgh\s+pr\s+create\b")
REMOTE_URL_RE = re.compile(
    r"(?:git@github\.com:|https?://github\.com/)([\w.-]+)/([\w.-]+?)(?:\.git)?(?:\s|$)"
)
# git commit short-sha output, e.g. "[main 1a2b3c4] message" or "(root-commit) 1a2b3c4"
COMMIT_SHA_RE = re.compile(r"\[[\w./+-]+\s+([0-9a-f]{7,40})\]")
COMMIT_SHA_LONG_RE = re.compile(r"\bcommit\s+([0-9a-f]{40})\b")
BRANCH_CREATE_RE = re.compile(
    r"git\s+(?:checkout\s+-b|switch\s+-c|branch(?!\s+-)\s+)\s*([^\s;&|]+)"
)
TEST_RUNNER_RE = re.compile(
    r"\b(pytest|py\.test|unittest|tox|nox|npm\s+(?:run\s+)?test|yarn\s+test|"
    r"jest|vitest|mocha|go\s+test|cargo\s+test|rspec|rails\s+test|"
    r"phpunit|dotnet\s+test|gradle\s+test|mvn\s+test|ctest)\b"
)
TEST_FAIL_RE = re.compile(
    r"(?i)\b(\d+\s+failed|\d+\s+failures?|FAILED|FAIL\b|AssertionError|"
    r"Error:|tests?\s+failed|✗|✘)"
)
TEST_PASS_RE = re.compile(
    r"(?i)\b(\d+\s+passed|all\s+tests?\s+pass|0\s+failed|0\s+failures?|"
    r"OK\b|PASS\b|✓|passing)"
)
COMMAND_NAME_RE = re.compile(r"<command-name>\s*/?([^<\s]+)\s*</command-name>")
SYNTH_PREFIXES = ("<command-", "<local-command-", "Caveat:", "[Request interrupted")


def parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def parse_arg_dt(s, end_of_day=False):
    """Parse a --since/--until argument. Accepts 'YYYY-MM-DD' or ISO 8601.

    Naive values are treated as UTC. A date-only --until is pushed to the end
    of that day so the whole day is included.
    """
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        raise SystemExit(f"Bad date/time {s!r} — use YYYY-MM-DD or ISO 8601")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # date-only string (midnight, no time component given) → optional EOD
    if end_of_day and "T" not in str(s) and " " not in str(s):
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    return dt


def session_in_range(first_iso, last_iso, since, until):
    """True if the session's [first, last] activity window intersects the
    [since, until] filter. Sessions with no parseable timestamp are excluded
    only when a filter is active."""
    if since is None and until is None:
        return True
    first = parse_ts(first_iso)
    last = parse_ts(last_iso)
    if first is None and last is None:
        return False
    lo = first or last
    hi = last or first
    if since is not None and hi < since:
        return False
    if until is not None and lo > until:
        return False
    return True


def find_sessions(projects_dir):
    """Group every *.jsonl under projects_dir by filename stem (== session id).

    Files under a 'subagents/' path are folded into their parent session so
    Haiku-subagent turns are accounted to the same session.
    """
    sessions = defaultdict(lambda: {"main": None, "extra": []})
    for path in sorted(projects_dir.rglob("*.jsonl")):
        stem = path.stem
        if "subagents" in path.parts:
            sessions[stem]["extra"].append(path)
        else:
            # prefer the first top-level file as the main one
            if sessions[stem]["main"] is None:
                sessions[stem]["main"] = path
            else:
                sessions[stem]["extra"].append(path)
    # sessions whose only files were under subagents/ still get a main file
    for stem, grp in sessions.items():
        if grp["main"] is None and grp["extra"]:
            grp["main"] = grp["extra"].pop(0)
    return sessions


def iter_entries(paths):
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        yield json.loads(raw)
                    except json.JSONDecodeError:
                        continue  # partial/streaming line — skip
        except OSError as exc:
            print(f"Warning: cannot read {path}: {exc}", file=sys.stderr)


def file_hash(paths):
    h = hashlib.sha256()
    for path in paths:
        try:
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(65536), b""):
                    h.update(chunk)
        except OSError:
            pass
    return h.hexdigest()


def text_of_content(content):
    """Flatten a message.content (str | list of blocks) to plain text."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict):
            continue
        t = block.get("type")
        if t == "text" and isinstance(block.get("text"), str):
            parts.append(block["text"])
        elif t == "tool_result":
            c = block.get("content")
            if isinstance(c, str):
                parts.append(c)
            elif isinstance(c, list):
                for sub in c:
                    if isinstance(sub, dict) and isinstance(sub.get("text"), str):
                        parts.append(sub["text"])
    return "\n".join(parts)


def is_human_prompt(entry):
    if entry.get("type") != "user":
        return False
    if entry.get("isSidechain"):
        return False
    if "toolUseResult" in entry:
        return False
    msg = entry.get("message")
    if not isinstance(msg, dict) or msg.get("role") != "user":
        return False
    content = msg.get("content")
    if isinstance(content, str):
        txt = content.strip()
    elif isinstance(content, list):
        # genuine prompt must contain a text block and no tool_result block
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return False
        txt = text_of_content(content).strip()
    else:
        return False
    if not txt:
        return False
    if txt.startswith(SYNTH_PREFIXES):
        return False
    return True


def extract_session(session_id, paths, args):
    entries = list(iter_entries(paths))
    if not entries:
        return None

    # --- identity / per-model usage (deduped) ---
    per_model = defaultdict(lambda: {
        "input": 0, "output": 0, "cache_read": 0,
        "cache_write_5m": 0, "cache_write_1h": 0, "cache_write": 0,
    })
    seen_turn = set()          # (requestId | message.id) -> counted once
    seen_tooluse = set()       # tool_use block ids
    models_used = set()
    first_ts = last_ts = None
    human_prompts = []
    assistant_snippets = []
    bash_commands = []
    tool_counts = Counter()
    skills = []
    branch_seq = []            # chronological gitBranch values
    starting_branch = None
    created_branches = []
    subagent_count = 0
    has_sidechain = False
    agent_teams = False
    # identifier corpora keyed by source
    corpus = defaultdict(list)  # source -> list[str]
    # outcome signal scratch
    bash_all = []
    last_tool_result_error = False
    last_assistant_text = ""

    for entry in entries:
        et = entry.get("type")
        ts = parse_ts(entry.get("timestamp"))
        if ts:
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)
        if entry.get("isSidechain"):
            has_sidechain = True
        gb = entry.get("gitBranch")
        if gb is not None:
            if starting_branch is None:
                starting_branch = gb
            if not branch_seq or branch_seq[-1] != gb:
                branch_seq.append(gb)
        # heuristic agent-team marker
        if "teamId" in entry or "agentTeamId" in entry or "agentTeam" in entry:
            agent_teams = True

        if et == "assistant":
            msg = entry.get("message", {})
            model = msg.get("model")
            content = msg.get("content", [])
            # per-model usage, deduped per turn
            turn_key = entry.get("requestId") or msg.get("id")
            if model not in SYNTHETIC_MODELS and turn_key and turn_key not in seen_turn:
                seen_turn.add(turn_key)
                models_used.add(model)
                u = msg.get("usage", {}) or {}
                pm = per_model[model]
                pm["input"] += u.get("input_tokens", 0) or 0
                pm["output"] += u.get("output_tokens", 0) or 0
                pm["cache_read"] += u.get("cache_read_input_tokens", 0) or 0
                cc = u.get("cache_creation")
                if isinstance(cc, dict):
                    pm["cache_write_5m"] += cc.get("ephemeral_5m_input_tokens", 0) or 0
                    pm["cache_write_1h"] += cc.get("ephemeral_1h_input_tokens", 0) or 0
                else:
                    pm["cache_write"] += u.get("cache_creation_input_tokens", 0) or 0
            # walk content blocks (NOT deduped — distinct calls per turn)
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                bt = block.get("type")
                if bt == "text" and isinstance(block.get("text"), str):
                    txt = block["text"].strip()
                    if txt:
                        last_assistant_text = txt
                        if len(assistant_snippets) < MAX_ASSISTANT_SNIPPETS:
                            assistant_snippets.append(txt[:MAX_ASSISTANT_CHARS])
                        corpus["assistant"].append(txt[:MAX_SCAN_CHARS])
                elif bt == "tool_use":
                    bid = block.get("id")
                    if bid and bid in seen_tooluse:
                        continue
                    if bid:
                        seen_tooluse.add(bid)
                    name = block.get("name", "?")
                    tool_counts[name] += 1
                    inp = block.get("input", {}) or {}
                    if name in ("Task", "Agent"):
                        subagent_count += 1
                        st = inp.get("subagent_type") or inp.get("subagentType") or ""
                        if "team" in str(st).lower():
                            agent_teams = True
                    if isinstance(name, str) and "team" in name.lower():
                        agent_teams = True
                    if name == "Skill":
                        sk = inp.get("skill") or inp.get("name")
                        if sk:
                            skills.append(str(sk))
                    if name == "Bash":
                        cmd = str(inp.get("command", ""))
                        bash_all.append(cmd)
                        if len(bash_commands) < MAX_BASH_CMDS:
                            bash_commands.append(cmd[:MAX_BASH_CHARS])
                        corpus["bash"].append(cmd[:MAX_SCAN_CHARS])
                    else:
                        # scan other tool inputs (bounded) for identifiers
                        corpus["tool_input"].append(json.dumps(inp)[:MAX_SCAN_CHARS])

        elif et == "user":
            msg = entry.get("message", {})
            content = msg.get("content")
            if is_human_prompt(entry):
                txt = (content if isinstance(content, str)
                       else text_of_content(content)).strip()
                if len(human_prompts) < MAX_PROMPTS:
                    human_prompts.append(txt[:MAX_PROMPT_CHARS])
                corpus["human"].append(txt[:MAX_SCAN_CHARS])
                for m in COMMAND_NAME_RE.findall(txt):
                    skills.append(m)
            else:
                # tool result — scan for identifiers / repo / tests / errors
                tr_text = text_of_content(content) if content is not None else ""
                tur = entry.get("toolUseResult")
                if tur is not None:
                    tr_text += "\n" + (tur if isinstance(tur, str)
                                       else json.dumps(tur))
                tr_text = tr_text[:MAX_SCAN_CHARS]
                corpus["tool_result"].append(tr_text)
                # is_error on the tool_result block?
                if isinstance(content, list):
                    err = any(isinstance(b, dict) and b.get("type") == "tool_result"
                              and b.get("is_error") for b in content)
                    last_tool_result_error = err
                if isinstance(tur, dict) and tur.get("is_error"):
                    last_tool_result_error = True

    if first_ts and last_ts:
        duration_s = max(0, int((last_ts - first_ts).total_seconds()))
    else:
        duration_s = None

    # --- project path: authoritative cwd field, else decode dir name ---
    cwds = Counter(e.get("cwd") for e in entries if e.get("cwd"))
    if cwds:
        project_path = cwds.most_common(1)[0][0]
    else:
        # decode "-home-user-foo" -> "/home/user/foo" (best effort)
        project_path = "/" + paths[0].parent.name.lstrip("-").replace("-", "/")

    # --- Component 4: repo + branch ---
    repo_name, repo_source = resolve_repo(corpus, project_path)
    for cmd in bash_all:
        for m in BRANCH_CREATE_RE.finditer(cmd):
            br = m.group(1).strip().strip("'\"")
            if br and br not in ("-d", "-D", "--list") and br not in created_branches:
                created_branches.append(br)
    branch_created = bool(created_branches)
    if branch_created:
        working_branch = created_branches[-1]
    elif branch_seq:
        working_branch = branch_seq[-1]
    else:
        working_branch = starting_branch

    # --- Component 3: identifiers ---
    identifiers = extract_identifiers(corpus, args.jira_projects)

    # --- Component 6: outcome signals ---
    commit_shas = []
    for src in ("tool_result", "bash"):
        for blob in corpus[src]:
            for m in COMMIT_SHA_RE.finditer(blob):
                commit_shas.append(m.group(1))
            for m in COMMIT_SHA_LONG_RE.finditer(blob):
                commit_shas.append(m.group(1))
    commit_shas = list(dict.fromkeys(commit_shas))
    git_commit_invoked = any(re.search(r"\bgit\s+commit\b", c) for c in bash_all)
    commit_created = bool(commit_shas) or git_commit_invoked
    pr_created = any(GH_PR_CREATE_RE.search(c) for c in bash_all) or any(
        i["type"] == "github_pr" and i["confidence"] == "high" for i in identifiers
    )
    tests_run = any(TEST_RUNNER_RE.search(c) for c in bash_all)
    tests_passed = None
    if tests_run:
        joined = "\n".join(corpus["tool_result"])
        fail = bool(TEST_FAIL_RE.search(joined))
        passed = bool(TEST_PASS_RE.search(joined))
        if fail and not passed:
            tests_passed = False
        elif passed and not fail:
            tests_passed = True
        elif passed and fail:
            tests_passed = False  # any failure dominates
    ended_on_question = last_assistant_text.rstrip().endswith("?")

    assistant_turns = len(seen_turn)

    # round/clean per_model: drop all-zero models, drop empty cache buckets
    pm_out = {}
    for model, tok in per_model.items():
        cleaned = {k: v for k, v in tok.items() if v}
        pm_out[model] = cleaned or {"input": 0}

    payload = {
        "human_prompts": human_prompts if not args.redact_prompts else [],
        "prompts_redacted": bool(args.redact_prompts),
        "human_prompt_count": len(corpus["human"]),
        "assistant_snippets": assistant_snippets if not args.redact_prompts else [],
        "tool_counts": dict(tool_counts),
        "bash_commands": bash_commands if not args.redact_prompts else [],
    }

    record = {
        "session_id": session_id,
        "project_path": project_path,
        "models_used": sorted(models_used),
        "first_timestamp": first_ts.isoformat() if first_ts else None,
        "last_timestamp": last_ts.isoformat() if last_ts else None,
        "duration_seconds": duration_s,
        "human_prompt_count": len(corpus["human"]),
        "assistant_turn_count": assistant_turns,
        "file_hash": file_hash(paths),
        "tokens_by_model": pm_out,
        "repo": {"name": repo_name, "source": repo_source},
        "branch": {
            "starting_branch": starting_branch,
            "working_branch": working_branch,
            "branch_created_in_session": branch_created,
            "created_branches": created_branches,
            "branch_sequence": branch_seq,
        },
        "features": {
            "subagents_used": subagent_count > 0 or has_sidechain,
            "subagent_count": subagent_count,
            "agent_teams_used": agent_teams,
            "agent_teams_detection": "heuristic",
            "skills_used": bool(skills),
            "skills": sorted(set(skills)),
        },
        "identifier_candidates": identifiers,
        "outcome_signals": {
            "commit_created": commit_created,
            "commit_shas": commit_shas[:10],
            "pr_created": pr_created,
            "tests_run": tests_run,
            "tests_passed": tests_passed,
            "last_turn_was_error": last_tool_result_error,
            "ended_on_unanswered_question": ended_on_question,
            "turn_count": assistant_turns,
            "duration_seconds": duration_s,
        },
        "classifier_payload": payload,
    }
    return record


def resolve_repo(corpus, project_path):
    for src in ("tool_result", "bash"):
        for blob in corpus[src]:
            m = REMOTE_URL_RE.search(blob)
            if m:
                return f"{m.group(1)}/{m.group(2)}", "remote"
    base = Path(project_path).name if project_path else None
    return base, "path"


def extract_identifiers(corpus, jira_projects):
    allow = set(p.strip().upper() for p in jira_projects.split(",")) if jira_projects else None
    out = []
    seen = set()

    def add(typ, value, confidence, source):
        key = (typ, value)
        if key in seen:
            return
        seen.add(key)
        out.append({"type": typ, "value": value,
                    "confidence": confidence, "source": source})

    for source, blobs in corpus.items():
        text = "\n".join(blobs)
        for m in JIRA_RE.findall(text):
            proj = m.split("-")[0]
            if allow is not None and proj not in allow:
                continue
            conf = "high" if allow is not None else "medium"
            add("jira", m, conf, source)
        for org, repo, num in PR_URL_RE.findall(text):
            add("github_pr", f"{org}/{repo}#{num}", "high", source)
        if GH_PR_CREATE_RE.search(text):
            add("github_pr", "gh-pr-create-invoked", "high", source)
        for num in BARE_PR_RE.findall(text):
            add("github_pr", f"#{num}", "low", source)
    return out


def main():
    ap = argparse.ArgumentParser(description="Deterministic CC transcript extractor")
    ap.add_argument("--projects-dir", default=str(DEFAULT_PROJECTS_DIR))
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--jira-projects", default=None,
                    help="Comma-separated Jira project allowlist (e.g. ABC,DEF)")
    ap.add_argument("--redact-prompts", action="store_true",
                    help="Do not store any prompt/assistant/bash text")
    ap.add_argument("--no-incremental", action="store_true",
                    help="Reclassify everything (ignore cache hashes)")
    ap.add_argument("--since", default=None,
                    help="Only sessions active on/after this date/time "
                         "(YYYY-MM-DD or ISO 8601, UTC if no zone)")
    ap.add_argument("--until", default=None,
                    help="Only sessions active on/before this date/time "
                         "(date-only includes the whole day)")
    ap.add_argument("--last-days", type=float, default=None,
                    help="Shortcut for --since = now minus N days (UTC). "
                         "Ignored if --since is given.")
    args = ap.parse_args()

    since = parse_arg_dt(args.since)
    until = parse_arg_dt(args.until, end_of_day=True)
    if since is None and args.last_days is not None:
        since = datetime.now(timezone.utc) - timedelta(days=args.last_days)
    if since and until and since > until:
        raise SystemExit(f"--since ({since}) is after --until ({until})")

    projects_dir = Path(args.projects_dir).expanduser()
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    if not projects_dir.exists():
        print(f"No transcripts dir at {projects_dir}", file=sys.stderr)
        print(json.dumps({"todo": [], "cached": [], "total": 0,
                          "out_dir": str(out_dir)}))
        return

    cache_path = out_dir / "cache.json"
    cache = {}
    if cache_path.exists() and not args.no_incremental:
        try:
            cache = json.loads(cache_path.read_text())
        except Exception:
            cache = {}

    sessions = find_sessions(projects_dir)
    extracted_path = out_dir / "extracted.jsonl"
    todo, cached = [], []
    records = []
    skipped_out_of_range = 0
    for session_id, grp in sorted(sessions.items()):
        paths = [grp["main"]] + grp["extra"]
        rec = extract_session(session_id, paths, args)
        if rec is None:
            continue
        if not session_in_range(rec["first_timestamp"], rec["last_timestamp"],
                                since, until):
            skipped_out_of_range += 1
            continue
        records.append(rec)
        prior = cache.get(session_id)
        unchanged = (prior and prior.get("hash") == rec["file_hash"]
                     and prior.get("classification"))
        if unchanged and not args.no_incremental:
            cached.append(session_id)
        else:
            todo.append(session_id)

    with open(extracted_path, "w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # update cache hashes (classification filled in by report.py)
    for rec in records:
        sid = rec["session_id"]
        entry = cache.get(sid, {})
        if entry.get("hash") != rec["file_hash"]:
            entry = {"hash": rec["file_hash"]}  # drop stale classification
        else:
            entry["hash"] = rec["file_hash"]
        cache[sid] = entry
    cache_path.write_text(json.dumps(cache, indent=2))

    print(json.dumps({
        "todo": todo, "cached": cached, "total": len(records),
        "out_dir": str(out_dir),
        "extracted": str(extracted_path),
        "range": {
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "skipped_out_of_range": skipped_out_of_range,
        },
    }, indent=2))


if __name__ == "__main__":
    main()
