#!/usr/bin/env python3
"""
cc-usage-classifier — deterministic transcript extractor.

Implements Components 1, 3, 4, 5, 6 of the cc-usage-classifier skill:
one intermediate JSON record per Claude Code session, built entirely from
local JSONL transcripts. No network, no model calls.

Session id == the main transcript's filename stem (the join key to any future
OTel/admin data). A session is its main thread PLUS every agent transcript it
spawned — Agent/Task subagents at any depth, forked skills, Workflow-tool agents
and legacy top-level agent-<id>.jsonl files — found by transcripts.discover()
(see transcripts.py for the on-disk layout). Agent requests count toward the
session's per-model tokens and cost; the record also splits them by thread
source (tokens_by_agent_source), per agent (agents) and per workflow run.

Key correctness facts (confirmed against real transcripts + ccusage):
  - usage lives at entry["message"]["usage"]; the model id at
    entry["message"]["model"].
  - One logical assistant turn (one requestId / message.id) is written as
    several entries (one per content block) that ALL repeat the identical
    usage object. We therefore DEDUPLICATE per (requestId | message.id) and
    count its usage exactly once, PER MODEL. Naive summation corrupts
    input/output totals.
  - cache_creation is split into ephemeral_5m / ephemeral_1h when present.
  - Dedup is session-wide: an agent transcript can replay a parent request id;
    it is attributed to the thread that wrote it first (main thread first).
  - Human prompts are transcripts.user_entry_kind() in {human, sdk}: skill
    bodies, hook feedback, task notifications and agent hand-backs are not.

Usage:
  python3 extract.py [--projects-dir DIR] [--out-dir DIR]
                     [--jira-projects ABC,DEF] [--redact-prompts]
                     [--no-incremental] [--last-days N]
                     [--since D] [--until D] [--batch-size N]
  python3 extract.py --args-file FILE     # raw user argument string in FILE
  python3 extract.py --args-string STR    # same, inline (tests / manual use)
  python3 extract.py ... --check-args     # print resolved args; no side effects
  --pricing PATH (default ../pricing.json) supplies long_context thresholds.

User text is split with shlex (never a shell) and validated against the flag
allowlist above; unknown flags, positionals and unsafe path characters are
rejected (exit 2).

Outputs (under --out-dir, default ~/.claude/cc-usage-classifier/out):
  extracted.jsonl          one intermediate record per session
  cache.json               {session_id: {hash, classification?}} (incremental)
  batches/batch-NNN.json   todo sessions' classifier inputs, <= --batch-size
                           sessions and ~150KB per file
  logs/extract.log         warnings + run summary
Stdout: JSON {"todo": [...], "cached": [...], "total": N, "out_dir": "...",
              "batches": [...], "classifications_dir": "...", "range": {...}}
"""

import argparse
import hashlib
import json
import re
import shlex
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import transcripts as tx  # noqa: E402  (vendored, shared with other plugins)

DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
DEFAULT_OUT_DIR = Path.home() / ".claude" / "cc-usage-classifier" / "out"
DEFAULT_PRICING = Path(__file__).resolve().parent.parent / "pricing.json"

# classifier batches: sessions per subagent, and a payload cap per batch file
DEFAULT_BATCH_SIZE = 20
DEFAULT_BATCH_MAX_BYTES = 150_000  # ~40K tokens; one oversize session still
                                   # gets a batch of its own

# Paths may be interpolated into shell commands by the skill (double-quoted),
# so refuse characters that are special inside double quotes or are
# control/quote characters. Spaces are fine.
SAFE_PATH_RE = re.compile(r"^[\w ./~+@:,=%-]+$")
JIRA_LIST_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,9}(,[A-Za-z][A-Za-z0-9]{1,9})*$")

_LOG_FH = None


def log(msg):
    """Warning/diagnostic line: stderr plus <out-dir>/logs/extract.log."""
    print(msg, file=sys.stderr)
    if _LOG_FH is not None:
        _LOG_FH.write(msg + "\n")
        _LOG_FH.flush()

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
# user_entry_kind() values that are the person's own prompt
PROMPT_KINDS = ("human", "sdk")
# thread sources (transcripts.AgentFile.source, plus the main thread)
SOURCES = ("main", "agent", "workflow", "team")
# bounds for the delegation summary in the classifier payload
MAX_AGENT_DESCRIPTIONS = 15
MAX_DESC_CHARS = 100
MAX_PAYLOAD_WORKFLOWS = 10
MAX_PAYLOAD_TYPES = 10
# `export const meta = {name: 'race-audit', ...}` in a Workflow tool script
WORKFLOW_NAME_RE = re.compile(r"""\bname\s*:\s*['"]([^'"\n]{1,80})['"]""")


def parse_ts(ts):
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None
    # naive timestamps are UTC; mixing naive/aware would raise on compare
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


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
            log(f"Warning: cannot read {path}: {exc}")


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


def is_tool_result(entry):
    """A type=user entry carrying tool output (main thread or sidechain)."""
    if entry.get("type") != "user":
        return False
    if "toolUseResult" in entry:
        return True
    content = (entry.get("message") or {}).get("content")
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def is_human_prompt(entry):
    """Typed (or SDK-sent) prompt from the person, per transcripts.user_entry_kind.

    Excludes skill bodies, hook feedback, compaction summaries (isMeta),
    task notifications, subagent hand-backs (origin.kind=peer), slash-command
    wrappers, interrupts, tool results and sidechain entries.
    """
    return tx.user_entry_kind(entry) in PROMPT_KINDS


def _zero_tokens():
    return {"input": 0, "output": 0, "cache_read": 0,
            "cache_write_5m": 0, "cache_write_1h": 0, "cache_write": 0}


def request_tokens(u):
    """Token buckets for one deduplicated request's usage object."""
    t = _zero_tokens()
    t["input"] = u.get("input_tokens", 0) or 0
    t["output"] = u.get("output_tokens", 0) or 0
    t["cache_read"] = u.get("cache_read_input_tokens", 0) or 0
    cc = u.get("cache_creation")
    if isinstance(cc, dict):
        t["cache_write_5m"] = cc.get("ephemeral_5m_input_tokens", 0) or 0
        t["cache_write_1h"] = cc.get("ephemeral_1h_input_tokens", 0) or 0
    else:
        t["cache_write"] = u.get("cache_creation_input_tokens", 0) or 0
    return t


def prompt_tokens(t):
    """Prompt size of one request = everything except output."""
    return sum(v for k, v in t.items() if k != "output")


def load_long_context_thresholds(pricing_path):
    """All long_context.threshold_tokens values in pricing.json (sorted)."""
    try:
        data = json.loads(Path(pricing_path).read_text())
    except (OSError, ValueError) as exc:
        log(f"Warning: cannot read pricing {pricing_path}: {exc} — "
            "long-context tiers will not be split")
        return []
    out = set()
    for k, v in data.items():
        if k.startswith("_") or not isinstance(v, dict):
            continue
        lc = v.get("long_context")
        if isinstance(lc, dict) and lc.get("threshold_tokens"):
            out.add(int(lc["threshold_tokens"]))
    return sorted(out)


def _clean(tok):
    return {k: v for k, v in tok.items() if v}


class _Thread(object):
    """Per-transcript accumulator: the main thread or one agent transcript."""

    def __init__(self, path, agent=None):
        self.path = path
        self.agent = agent                     # transcripts.AgentFile or None
        self.is_main = agent is None
        self.label = "main" if agent is None else agent.agent_id
        self.source = "main" if agent is None else agent.source
        self.tokens = defaultdict(_zero_tokens)
        self.long = defaultdict(lambda: defaultdict(_zero_tokens))
        self.requests = 0
        self.tool_counts = Counter()
        self.bash = []                         # every Bash command (full)
        self.test_tool_ids = set()             # Bash tool_use ids running tests
        self.test_runs = 0
        self.test_results = []                 # output of those test runs
        self.tool_results = []                 # every tool output (bounded)

    @property
    def signal_label(self):
        return "main" if self.is_main else f"{self.source}:{self.label}"


def _add_tokens(dst, src):
    for k, v in src.items():
        dst[k] += v


def _tool_use_ids(content):
    if not isinstance(content, list):
        return []
    return [b.get("tool_use_id") for b in content
            if isinstance(b, dict) and b.get("type") == "tool_result"]


def _test_verdict(texts):
    """True / False / None from test-run output (any failure dominates)."""
    if not texts:
        return None
    joined = "\n".join(texts)
    fail = bool(TEST_FAIL_RE.search(joined))
    passed = bool(TEST_PASS_RE.search(joined))
    if fail:
        return False
    return True if passed else None


def _commit_shas(texts):
    out = []
    for blob in texts:
        for m in COMMIT_SHA_RE.finditer(blob):
            out.append(m.group(1))
        for m in COMMIT_SHA_LONG_RE.finditer(blob):
            out.append(m.group(1))
    return out


def _clean_model_map(per_model):
    return {m: (_clean(t) or {"input": 0}) for m, t in per_model.items()}


def _clean_long_map(long_ctx):
    return {m: {th: _clean(t) for th, t in by_th.items()}
            for m, by_th in long_ctx.items()}


def extract_session(session_id, files, args, thresholds=()):
    """One record for a session = its main thread plus every agent transcript.

    files: a transcripts.SessionFiles, or (tests / legacy callers) a list of
    paths whose first item is the main thread.
    """
    if isinstance(files, tx.SessionFiles):
        threads = ([_Thread(files.main)] if files.main is not None else [])
        threads += [_Thread(a.path, a) for a in files.agents]
        all_paths = files.all_paths()
        project_dir = files.project_dir
        disk_runs = files.workflow_runs()
    else:
        paths = list(files)
        threads = [_Thread(paths[0])] + [
            _Thread(p, tx.AgentFile(p, Path(p).stem, {}, None)) for p in paths[1:]]
        all_paths = paths
        project_dir = paths[0].parent
        disk_runs = {}

    # --- identity / per-model usage (deduped across the whole session) ---
    per_model = defaultdict(_zero_tokens)
    # model -> str(threshold) -> tokens of requests whose prompt > threshold
    long_ctx = defaultdict(lambda: defaultdict(_zero_tokens))
    seen_turn = {}             # (requestId | message.id) -> thread label
    seen_tooluse = set()       # tool_use block ids
    models_used = set()
    first_ts = last_ts = None
    human_prompts = []
    human_prompt_count = 0
    assistant_snippets = []
    bash_commands = []         # main thread, for the payload
    main_tool_counts = Counter()
    skills = []
    branch_seq = []            # chronological gitBranch values (main thread)
    agent_branch_seq = []      # fallback when the main thread is missing
    starting_branch = None
    created_branches = []
    agent_tool_calls = 0       # Agent/Task tool_use blocks, any thread
    workflow_tool_calls = 0
    workflow_tool_names = {}   # Workflow tool_use id -> meta.name in script (or None)
    workflow_run_names = {}    # run id -> name (via the Workflow tool result)
    team_heuristic = False
    # identifier corpora keyed by source
    corpus = defaultdict(list)  # source -> list[str]
    last_tool_result_error = False
    last_assistant_text = ""
    main_cwds, all_cwds = Counter(), Counter()
    n_entries = 0

    for th in threads:
        pfx = "" if th.is_main else "agent_"
        for entry in iter_entries([th.path]):
            if not isinstance(entry, dict):
                continue
            n_entries += 1
            et = entry.get("type")
            ts = parse_ts(entry.get("timestamp"))
            if ts:
                first_ts = ts if first_ts is None else min(first_ts, ts)
                last_ts = ts if last_ts is None else max(last_ts, ts)
            cwd = entry.get("cwd")
            if cwd:
                all_cwds[cwd] += 1
                if th.is_main:
                    main_cwds[cwd] += 1
            gb = entry.get("gitBranch")
            if gb is not None:
                seq = branch_seq if th.is_main else agent_branch_seq
                if th.is_main and starting_branch is None:
                    starting_branch = gb
                if not seq or seq[-1] != gb:
                    seq.append(gb)
            # fallback agent-team marker (meta.json teamName is authoritative)
            if "teamId" in entry or "agentTeamId" in entry or "agentTeam" in entry:
                team_heuristic = True

            if et == "assistant":
                msg = entry.get("message") or {}
                model = msg.get("model")
                content = msg.get("content", [])
                turn_key = entry.get("requestId") or msg.get("id")
                if turn_key:
                    owner = seen_turn.get(turn_key)
                    if owner is not None and owner != th.label:
                        continue  # another thread's request replayed here
                    if owner is None and model not in SYNTHETIC_MODELS:
                        seen_turn[turn_key] = th.label
                        models_used.add(model)
                        th.requests += 1
                        rt = request_tokens(msg.get("usage", {}) or {})
                        _add_tokens(per_model[model], rt)
                        _add_tokens(th.tokens[model], rt)
                        ptok = prompt_tokens(rt)
                        for thr in thresholds:
                            if ptok > thr:
                                _add_tokens(long_ctx[model][str(thr)], rt)
                                _add_tokens(th.long[model][str(thr)], rt)
                # walk content blocks (NOT deduped — distinct calls per turn)
                for block in content if isinstance(content, list) else []:
                    if not isinstance(block, dict):
                        continue
                    bt = block.get("type")
                    if bt == "text" and isinstance(block.get("text"), str):
                        txt = block["text"].strip()
                        if not txt:
                            continue
                        if th.is_main:
                            last_assistant_text = txt
                            if len(assistant_snippets) < MAX_ASSISTANT_SNIPPETS:
                                assistant_snippets.append(txt[:MAX_ASSISTANT_CHARS])
                        corpus[pfx + "assistant"].append(txt[:MAX_SCAN_CHARS])
                    elif bt == "tool_use":
                        bid = block.get("id")
                        if bid and bid in seen_tooluse:
                            continue
                        if bid:
                            seen_tooluse.add(bid)
                        name = block.get("name", "?")
                        th.tool_counts[name] += 1
                        if th.is_main:
                            main_tool_counts[name] += 1
                        inp = block.get("input", {}) or {}
                        if not isinstance(inp, dict):
                            inp = {}
                        if name in tx.AGENT_TOOLS:
                            agent_tool_calls += 1
                            st = inp.get("subagent_type") or inp.get("subagentType") or ""
                            if "team" in str(st).lower():
                                team_heuristic = True
                        elif name in tx.WORKFLOW_TOOLS:
                            workflow_tool_calls += 1
                            m = WORKFLOW_NAME_RE.search(str(inp.get("script", "")))
                            if bid:
                                workflow_tool_names[bid] = m.group(1) if m else None
                        if isinstance(name, str) and "team" in name.lower():
                            team_heuristic = True
                        if name == "Skill":
                            sk = inp.get("skill") or inp.get("name")
                            if sk:
                                skills.append(str(sk))
                        if name == "Bash":
                            cmd = str(inp.get("command", ""))
                            th.bash.append(cmd)
                            if TEST_RUNNER_RE.search(cmd):
                                th.test_runs += 1
                                if bid:
                                    th.test_tool_ids.add(bid)
                            if th.is_main and len(bash_commands) < MAX_BASH_CMDS:
                                bash_commands.append(cmd[:MAX_BASH_CHARS])
                            corpus[pfx + "bash"].append(cmd[:MAX_SCAN_CHARS])
                        else:
                            # scan other tool inputs (bounded) for identifiers
                            corpus[pfx + "tool_input"].append(
                                json.dumps(inp)[:MAX_SCAN_CHARS])

            elif et == "user":
                msg = entry.get("message") or {}
                content = msg.get("content")
                if is_tool_result(entry):
                    # tool output — scan for identifiers / repo / tests / errors
                    tr_text = text_of_content(content) if content is not None else ""
                    tur = entry.get("toolUseResult")
                    if tur is not None:
                        tr_text += "\n" + (tur if isinstance(tur, str)
                                           else json.dumps(tur))
                    tr_text = tr_text[:MAX_SCAN_CHARS]
                    corpus[pfx + "tool_result"].append(tr_text)
                    th.tool_results.append(tr_text)
                    ids = _tool_use_ids(content)
                    if any(i in th.test_tool_ids for i in ids):
                        th.test_results.append(tr_text)
                    for i in ids:
                        if i in workflow_tool_names:  # Workflow launch output
                            for rid in tx.workflow_run_ids_in(tr_text):
                                if not workflow_run_names.get(rid):
                                    workflow_run_names[rid] = workflow_tool_names[i]
                    if th.is_main:
                        err = isinstance(content, list) and any(
                            isinstance(b, dict) and b.get("type") == "tool_result"
                            and b.get("is_error") for b in content)
                        if isinstance(tur, dict) and tur.get("is_error"):
                            err = True
                        last_tool_result_error = err
                    continue
                if not th.is_main:
                    continue  # the prompt handed to the agent, not the person's
                kind = tx.user_entry_kind(entry)
                txt = (content if isinstance(content, str)
                       else text_of_content(content)).strip()
                if kind in PROMPT_KINDS:
                    human_prompt_count += 1
                    if len(human_prompts) < MAX_PROMPTS:
                        human_prompts.append(txt[:MAX_PROMPT_CHARS])
                    corpus["human"].append(txt[:MAX_SCAN_CHARS])
                elif kind == "command":
                    for m in COMMAND_NAME_RE.findall(txt):
                        skills.append(m)
                elif kind in ("task_notification", "peer"):
                    # hand-backs can name PRs/commits the agents produced;
                    # scanned for identifiers only, never sent as prompts
                    corpus["agent_message"].append(txt[:MAX_SCAN_CHARS])

    if not n_entries:
        return None

    if first_ts and last_ts:
        duration_s = max(0, int((last_ts - first_ts).total_seconds()))
    else:
        duration_s = None

    # --- project path: authoritative cwd field, else decode dir name ---
    cwds = main_cwds or all_cwds
    if cwds:
        project_path = cwds.most_common(1)[0][0]
    else:
        # decode "-home-user-foo" -> "/home/user/foo" (best effort)
        project_path = "/" + Path(project_dir).name.lstrip("-").replace("-", "/")

    # --- Component 4: repo + branch ---
    repo_name, repo_source = resolve_repo(corpus, project_path)
    for th in threads:
        for cmd in th.bash:
            for m in BRANCH_CREATE_RE.finditer(cmd):
                br = m.group(1).strip().strip("'\"")
                if br and br not in ("-d", "-D", "--list") and br not in created_branches:
                    created_branches.append(br)
    if not branch_seq:
        branch_seq = agent_branch_seq
        starting_branch = branch_seq[0] if branch_seq else None
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
    # Commits / tests / PRs made by agents count (the work was delegated);
    # signal_sources records which thread produced each. Final-state signals
    # (last tool error, ending on a question) are main-thread only.
    commit_shas = []
    sources = {"commit_created": [], "pr_created": [], "tests_run": []}
    test_runs = {"main": 0, "delegated": 0}
    verdict_main, verdicts_delegated = None, []
    for th in threads:
        shas = _commit_shas(th.tool_results + th.bash)
        commit_shas.extend(shas)
        if shas or any(re.search(r"\bgit\s+commit\b", c) for c in th.bash):
            sources["commit_created"].append(th.signal_label)
        if any(GH_PR_CREATE_RE.search(c) for c in th.bash):
            sources["pr_created"].append(th.signal_label)
        if th.test_runs:
            sources["tests_run"].append(th.signal_label)
            test_runs["main" if th.is_main else "delegated"] += th.test_runs
            # output of the test commands themselves; whole-thread output
            # only when tool_use ids are missing (old transcripts)
            v = _test_verdict(th.test_results or th.tool_results)
            if th.is_main:
                verdict_main = v
            else:
                verdicts_delegated.append(v)
    commit_shas = list(dict.fromkeys(commit_shas))
    commit_created = bool(sources["commit_created"])
    pr_created = bool(sources["pr_created"]) or any(
        i["type"] == "github_pr" and i["confidence"] == "high" for i in identifiers
    )
    tests_run = bool(sources["tests_run"])
    if False in verdicts_delegated:
        verdict_delegated = False
    elif True in verdicts_delegated:
        verdict_delegated = True
    else:
        verdict_delegated = None
    # the main thread's last word is the final state; delegated runs decide
    # only when the main thread ran no tests (agents often run failing repros)
    tests_passed = verdict_main if test_runs["main"] else verdict_delegated
    ended_on_question = last_assistant_text.rstrip().endswith("?")

    main_requests = sum(th.requests for th in threads if th.is_main)
    delegated_requests = sum(th.requests for th in threads if not th.is_main)

    # --- delegation: per-source tokens, per-agent rows, workflow runs ---
    by_source = {s: defaultdict(_zero_tokens) for s in SOURCES}
    long_by_source = {s: defaultdict(lambda: defaultdict(_zero_tokens)) for s in SOURCES}
    agents = []
    delegated_tool_counts = Counter()
    for th in threads:
        src = th.source if th.source in by_source else "agent"
        for model, tok in th.tokens.items():
            _add_tokens(by_source[src][model], tok)
        for model, by_th in th.long.items():
            for thr, tok in by_th.items():
                _add_tokens(long_by_source[src][model][thr], tok)
        if th.is_main:
            continue
        delegated_tool_counts.update(th.tool_counts)
        s = th.agent.summary()
        row = {
            "agent_id": s["agent_id"],
            "agent_type": s["agent_type"],
            "source": s["source"],
            "workflow_run_id": s["workflow_run_id"],
            "workflow_phase": s["workflow_phase"],
            "spawn_depth": s["spawn_depth"],
            "parent_agent_id": s["parent_agent_id"],
            "background": s["background"],
            "team_name": s["team_name"],
            "stopped_by_user": s["stopped_by_user"],
            "models": sorted(th.tokens),
            "requests": th.requests,
            "tool_calls": sum(th.tool_counts.values()),
            "test_runs": th.test_runs,
            "tokens_by_model": _clean_model_map(th.tokens),
            "long_context_tokens_by_model": _clean_long_map(th.long),
        }
        if not args.redact_prompts:
            row["description"] = s["description"]
        agents.append(row)

    runs = {}
    for rid, r in disk_runs.items():
        runs[rid] = {"run_id": rid, "name": r.get("name"), "agents": r.get("agents", 0),
                     "phases": list(r.get("phases", [])),
                     "agent_types": dict(r.get("agent_types", {}))}
    for rid, name in workflow_run_names.items():
        r = runs.setdefault(rid, {"run_id": rid, "name": None, "agents": 0,
                                  "phases": [], "agent_types": {}})
        if not r["name"] and name:
            r["name"] = name
    for r in runs.values():
        members = [a for a in agents if a["workflow_run_id"] == r["run_id"]]
        tok = defaultdict(_zero_tokens)
        lng = defaultdict(lambda: defaultdict(_zero_tokens))
        for a in members:
            for model, t in a["tokens_by_model"].items():
                _add_tokens(tok[model], t)
            for model, by_th in a["long_context_tokens_by_model"].items():
                for thr, t in by_th.items():
                    _add_tokens(lng[model][thr], t)
        r["models"] = sorted(tok)
        r["requests"] = sum(a["requests"] for a in members)
        r["tool_calls"] = sum(a["tool_calls"] for a in members)
        r["stopped_by_user"] = sum(1 for a in members if a["stopped_by_user"])
        r["tokens_by_model"] = _clean_model_map(tok)
        r["long_context_tokens_by_model"] = _clean_long_map(lng)
    workflow_runs = sorted(runs.values(), key=lambda r: r["run_id"])

    agents_by_source = Counter(a["source"] for a in agents)
    agents_by_type = Counter(a["agent_type"] for a in agents)
    team_names = sorted({a["team_name"] for a in agents if a["team_name"]})
    if team_names:
        teams_detection = "meta"
    elif team_heuristic:
        teams_detection = "heuristic"
    else:
        teams_detection = None
    max_depth = max([a["spawn_depth"] for a in agents] or [0])

    pm_out = _clean_model_map(per_model)
    lc_out = _clean_long_map(long_ctx)

    payload = {
        "human_prompts": human_prompts if not args.redact_prompts else [],
        "prompts_redacted": bool(args.redact_prompts),
        "human_prompt_count": human_prompt_count,
        "assistant_snippets": assistant_snippets if not args.redact_prompts else [],
        "tool_counts": dict(main_tool_counts),
        "bash_commands": bash_commands if not args.redact_prompts else [],
    }
    if agents or agent_tool_calls or workflow_tool_calls:
        delegation = {
            "agents": len(agents),
            "by_source": dict(agents_by_source),
            "by_type": dict(agents_by_type.most_common(MAX_PAYLOAD_TYPES)),
            "workflows": [{"name": r["name"] or r["run_id"], "phases": r["phases"],
                           "agents": r["agents"]}
                          for r in workflow_runs[:MAX_PAYLOAD_WORKFLOWS]],
            "delegated_requests": delegated_requests,
            "delegated_tool_calls": sum(delegated_tool_counts.values()),
            "delegated_tool_counts": dict(delegated_tool_counts.most_common(10)),
            "max_spawn_depth": max_depth,
            "stopped_by_user": sum(1 for a in agents if a["stopped_by_user"]),
        }
        if not args.redact_prompts:
            descs = []
            for a in agents:
                d = (a.get("description") or "").strip()
                if d and d[:MAX_DESC_CHARS] not in descs:
                    descs.append(d[:MAX_DESC_CHARS])
            delegation["agent_descriptions"] = descs[:MAX_AGENT_DESCRIPTIONS]
        payload["delegation"] = delegation

    record = {
        "session_id": session_id,
        "project_path": project_path,
        "models_used": sorted(models_used),
        "first_timestamp": first_ts.isoformat() if first_ts else None,
        "last_timestamp": last_ts.isoformat() if last_ts else None,
        "duration_seconds": duration_s,
        "human_prompt_count": human_prompt_count,
        "assistant_turn_count": main_requests,
        "request_count": len(seen_turn),
        "delegated_request_count": delegated_requests,
        "transcript_files": len(all_paths),
        "file_hash": file_hash(all_paths),
        # every model's tokens across the main thread AND all agents
        "tokens_by_model": pm_out,
        # subset of tokens_by_model: requests whose prompt exceeded a
        # pricing.json long_context threshold (cost.py prices these)
        "long_context_thresholds": list(thresholds),
        "long_context_tokens_by_model": lc_out,
        # partition of tokens_by_model by the thread that made the request
        "tokens_by_agent_source": {s: _clean_model_map(v) for s, v in by_source.items()},
        "long_context_tokens_by_agent_source": {
            s: _clean_long_map(v) for s, v in long_by_source.items()},
        "agents": agents,
        "workflow_runs": workflow_runs,
        "repo": {"name": repo_name, "source": repo_source},
        "branch": {
            "starting_branch": starting_branch,
            "working_branch": working_branch,
            "branch_created_in_session": branch_created,
            "created_branches": created_branches,
            "branch_sequence": branch_seq,
        },
        "features": {
            "subagents_used": bool(agents) or agent_tool_calls > 0,
            "subagent_count": len(agents),
            "agent_tool_calls": agent_tool_calls,
            "agents_by_source": dict(agents_by_source),
            "background_agents": sum(1 for a in agents if a["background"]),
            "agents_stopped_by_user": sum(1 for a in agents if a["stopped_by_user"]),
            "nested_agents": any(a["spawn_depth"] > 1 or a["parent_agent_id"]
                                 for a in agents),
            "max_spawn_depth": max_depth,
            "workflows_used": bool(workflow_runs) or workflow_tool_calls > 0,
            "workflow_runs": len(workflow_runs),
            "workflow_tool_calls": workflow_tool_calls,
            "agent_teams_used": teams_detection is not None,
            "agent_teams": team_names,
            "agent_teams_detection": teams_detection,
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
            "tests_passed_main": verdict_main,
            "tests_passed_delegated": verdict_delegated,
            "test_runs": test_runs,
            "signal_sources": sources,
            "last_turn_was_error": last_tool_result_error,
            "ended_on_unanswered_question": ended_on_question,
            "turn_count": main_requests,
            "duration_seconds": duration_s,
        },
        "classifier_payload": payload,
    }
    return record


def resolve_repo(corpus, project_path):
    for src in ("tool_result", "bash", "agent_tool_result", "agent_bash"):
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


def _path_arg(value):
    if not SAFE_PATH_RE.fullmatch(value or ""):
        raise argparse.ArgumentTypeError(
            f"unsafe or empty path {value!r} (allowed: letters, digits, "
            "space and ./~+@:,=%-_)")
    return value


def _days_arg(value):
    try:
        n = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}")
    if not (0 < n <= 36500):
        raise argparse.ArgumentTypeError(f"out of range: {value!r}")
    return n


def _date_arg(value):
    try:
        datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"bad date/time {value!r} — use YYYY-MM-DD or ISO 8601")
    return value


def _jira_arg(value):
    if not JIRA_LIST_RE.fullmatch(value or ""):
        raise argparse.ArgumentTypeError(
            f"bad Jira project list {value!r} — use e.g. ABC,DEF")
    return value


def _batch_size_arg(value):
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an integer: {value!r}")
    if not (1 <= n <= 50):
        raise argparse.ArgumentTypeError("batch size must be 1..50")
    return n


def build_parser():
    """The user-facing flag allowlist. Unknown flags / positionals error."""
    ap = argparse.ArgumentParser(
        prog="extract.py", allow_abbrev=False,
        description="Deterministic CC transcript extractor",
        epilog="Also: --args-file FILE / --args-string STR (user flags, "
               "shlex-split and validated), --pricing PATH, --check-args.")
    ap.add_argument("--projects-dir", type=_path_arg,
                    default=str(DEFAULT_PROJECTS_DIR))
    ap.add_argument("--out-dir", type=_path_arg, default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--jira-projects", type=_jira_arg, default=None,
                    help="Comma-separated Jira project allowlist (e.g. ABC,DEF)")
    ap.add_argument("--redact-prompts", action="store_true",
                    help="Do not store any prompt/assistant/bash text")
    ap.add_argument("--no-incremental", action="store_true",
                    help="Reclassify everything (ignore cache hashes)")
    ap.add_argument("--since", type=_date_arg, default=None,
                    help="Only sessions active on/after this date/time "
                         "(YYYY-MM-DD or ISO 8601, UTC if no zone)")
    ap.add_argument("--until", type=_date_arg, default=None,
                    help="Only sessions active on/before this date/time "
                         "(date-only includes the whole day)")
    ap.add_argument("--last-days", type=_days_arg, default=None,
                    help="Shortcut for --since = now minus N days (UTC). "
                         "Ignored if --since is given.")
    ap.add_argument("--batch-size", type=_batch_size_arg,
                    default=DEFAULT_BATCH_SIZE,
                    help="Max sessions per classifier batch file (default 20)")
    return ap


def parse_args(argv=None):
    """Parse CLI args. User-supplied text never goes through a shell: pass it
    via --args-file PATH (preferred; the file holds the raw argument string)
    or --args-string STR. It is split with shlex (no expansion, no
    substitution) and validated against build_parser()'s allowlist."""
    argv = list(sys.argv[1:] if argv is None else argv)
    meta = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    meta.add_argument("--args-string", default=None)
    meta.add_argument("--args-file", default=None)
    meta.add_argument("--pricing", default=str(DEFAULT_PRICING))
    meta.add_argument("--check-args", action="store_true")
    m, rest = meta.parse_known_args(argv)
    user = []
    raw = None
    if m.args_file is not None:
        try:
            raw = Path(m.args_file).expanduser().read_text(encoding="utf-8")
        except OSError as exc:
            raise SystemExit(f"cannot read --args-file {m.args_file}: {exc}")
    elif m.args_string is not None:
        raw = m.args_string
    if raw is not None:
        try:
            user = shlex.split(raw, comments=False, posix=True)
        except ValueError as exc:
            raise SystemExit(f"cannot parse arguments {raw!r}: {exc}")
        for tok in user:
            if tok in ("--args-file", "--args-string", "--pricing",
                       "--check-args") or tok.startswith(
                           ("--args-file=", "--args-string=", "--pricing=")):
                raise SystemExit(f"{tok} is not allowed inside the argument string")
    args = build_parser().parse_args(rest + user)
    args.pricing = m.pricing
    args.check_args = m.check_args
    return args


def write_batches(out_dir, records_by_id, todo, batch_size,
                  max_bytes=DEFAULT_BATCH_MAX_BYTES):
    """Write todo sessions' classifier inputs to batches/batch-NNN.json.

    JSON array, indent=1, so every long string sits on its own line (the Read
    tool truncates very long lines). Old batch files are removed first.
    Returns the list of batch file paths.
    """
    bdir = out_dir / "batches"
    bdir.mkdir(parents=True, exist_ok=True)
    for old in bdir.glob("*.json"):
        old.unlink()
    batches, cur, cur_bytes = [], [], 0
    for sid in todo:
        r = records_by_id[sid]
        item = {
            "session_id": sid,
            "classifier_payload": r["classifier_payload"],
            "outcome_signals": r["outcome_signals"],
            "identifier_candidates": r["identifier_candidates"],
            "features": r["features"],
            "repo": r["repo"], "branch": r["branch"],
        }
        size = len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
        if cur and (len(cur) >= batch_size or cur_bytes + size > max_bytes):
            batches.append(cur)
            cur, cur_bytes = [], 0
        cur.append(item)
        cur_bytes += size
    if cur:
        batches.append(cur)
    paths = []
    for i, items in enumerate(batches, 1):
        path = bdir / f"batch-{i:03d}.json"
        path.write_text(json.dumps(items, ensure_ascii=False, indent=1),
                        encoding="utf-8")
        paths.append(str(path))
    return paths


def main(argv=None):
    global _LOG_FH
    args = parse_args(argv)

    since = parse_arg_dt(args.since)
    until = parse_arg_dt(args.until, end_of_day=True)
    if since is None and args.last_days is not None:
        since = datetime.now(timezone.utc) - timedelta(days=args.last_days)
    if since and until and since > until:
        raise SystemExit(f"--since ({since}) is after --until ({until})")

    projects_dir = Path(args.projects_dir).expanduser()
    out_dir = Path(args.out_dir).expanduser()

    if args.check_args:
        print(json.dumps({
            "projects_dir": str(projects_dir), "out_dir": str(out_dir),
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "jira_projects": args.jira_projects,
            "redact_prompts": args.redact_prompts,
            "no_incremental": args.no_incremental,
            "batch_size": args.batch_size,
        }, indent=2))
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "logs").mkdir(exist_ok=True)
    _LOG_FH = open(out_dir / "logs" / "extract.log", "w", encoding="utf-8")

    if not projects_dir.exists():
        log(f"No transcripts dir at {projects_dir}")
        print(json.dumps({"todo": [], "cached": [], "total": 0, "batches": [],
                          "out_dir": str(out_dir)}))
        return

    thresholds = load_long_context_thresholds(args.pricing)

    cache_path = out_dir / "cache.json"
    cache = {}
    if cache_path.exists() and not args.no_incremental:
        try:
            cache = json.loads(cache_path.read_text())
        except Exception as exc:
            log(f"Warning: unreadable cache {cache_path}: {exc} — ignoring")
            cache = {}

    # one session = main thread + every subagent / workflow agent transcript
    # (never group by filename stem: agent-<id>.jsonl is not a session)
    sessions = tx.discover(projects_dir)
    extracted_path = out_dir / "extracted.jsonl"
    todo, cached = [], []
    records = []
    skipped_out_of_range = 0
    for session_id, files in sorted(sessions.items()):
        if files.main is None:
            log(f"Note: session {session_id} has agent transcripts but no main "
                f"transcript ({len(files.agents)} agent files) — extracted from agents only")
        rec = extract_session(session_id, files, args, thresholds)
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

    # A todo session must be classified afresh: drop any classification file
    # left by an earlier run, or report.py would merge the stale verdict.
    class_dir = out_dir / "classifications"
    class_dir.mkdir(exist_ok=True)
    for sid in todo:
        stale = class_dir / f"{sid}.json"
        if stale.exists():
            stale.unlink()

    batch_paths = write_batches(out_dir, {r["session_id"]: r for r in records},
                                todo, args.batch_size)

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

    result = {
        "todo": todo, "cached": cached, "total": len(records),
        "out_dir": str(out_dir),
        "extracted": str(extracted_path),
        "batches": batch_paths,
        "classifications_dir": str(class_dir),
        "range": {
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "skipped_out_of_range": skipped_out_of_range,
        },
    }
    _LOG_FH.write(json.dumps({k: v for k, v in result.items()
                              if k not in ("todo", "cached")}) + "\n")
    _LOG_FH.close()
    _LOG_FH = None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
