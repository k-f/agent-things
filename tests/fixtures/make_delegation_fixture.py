#!/usr/bin/env python3
"""
Build a synthetic ~/.claude/projects tree that exercises subagents and
dynamic workflows, in the on-disk layout Claude Code 2.1.x writes (see
plugins/*/scripts/transcripts.py for the layout notes).

  python3 tests/fixtures/make_delegation_fixture.py <out-projects-dir>

Plugin tests call build(out_dir) directly. Expected values live in EXPECTED so
every plugin's tests assert against the same numbers.

Session sess-orch (project /work/orchestra):
  main thread   opus-5-5   4 requests (req_m2 split over two entries)
  agent aexp1   Explore, foreground, depth 1            sonnet-5-5  2 requests
  agent anest1  general-purpose, parent aexp1, depth 2  haiku-5-5   1 request
  workflow wf_k3j9x2ab "race-audit": awf1 (Find), awf2 (Verify, runs pytest),
                awf3 (Verify, no meta file)   sonnet-5-5 x2 + haiku-5-5 x1
  legacy top-level agent-alegacy1.jsonl       haiku-4-5   1 request
  awf1 also replays req_m1 (same requestId as the main thread) -> counted once
User entries in the main thread: 2 human, 1 skill body (meta), 1 stop-hook
(meta), 1 peer hand-back, 1 task notification, tool results.
Session sess-plain (same project): 1 human prompt, 1 opus-5-5 request.
"""

import json
import sys
from pathlib import Path

PROJECT_KEY = "-work-orchestra"
CWD = "/work/orchestra"
SID = "sess-orch"
PLAIN_SID = "sess-plain"
RUN_ID = "wf_k3j9x2ab"

EXPECTED = {
    "sessions": 2,
    "human_prompts": 2,
    "agents": 6,                      # aexp1, anest1, awf1-3, alegacy1
    "agents_by_source": {"agent": 3, "workflow": 3},
    "max_spawn_depth": 2,
    "workflow_runs": {RUN_ID: {"name": "race-audit", "agents": 3}},
    "background_agents": 2,          # awf1, awf2 (requestShape background)
    # deduped requests per model across the whole session
    "requests_by_model": {"claude-opus-5-5": 4, "claude-sonnet-5-5": 4,
                          "claude-haiku-5-5": 2, "claude-haiku-4-5": 1},
    # usage per request is input 100 / output 50 / cache_read 1000 everywhere
    "tokens_per_request": {"input": 100, "output": 50, "cache_read": 1000},
    "main_thread_requests": 4,
    "delegated_tool_calls": 7,        # tool_use blocks inside agent transcripts
    "test_runs_main": 1,
    "test_runs_delegated": 1,
}

USAGE = {"input_tokens": 100, "output_tokens": 50, "cache_read_input_tokens": 1000,
         "cache_creation_input_tokens": 0,
         "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0}}


class Clock(object):
    def __init__(self, start_minute=0):
        self.m = start_minute

    def __call__(self):
        self.m += 1
        return "2026-10-01T10:%02d:00.000Z" % (self.m % 60)


def base(sid, ts, sidechain=False, agent_id=None):
    e = {"sessionId": sid, "cwd": CWD, "gitBranch": "main", "version": "2.1.293",
         "timestamp": ts, "isSidechain": sidechain, "userType": "external"}
    if agent_id:
        e["agentId"] = agent_id
    return e


def human(sid, ts, text):
    e = base(sid, ts)
    e.update(type="user", origin={"kind": "human"}, turnOrigin="human", promptSource="sdk",
             message={"role": "user", "content": text})
    return e


def assistant(sid, ts, req, model, blocks, sidechain=False, agent_id=None):
    e = base(sid, ts, sidechain, agent_id)
    e.update(type="assistant", requestId=req,
             message={"role": "assistant", "model": model, "id": "msg_" + req,
                      "content": blocks, "usage": dict(USAGE)})
    return e


def tool_use(tid, name, inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def tool_result(sid, ts, tid, text, sidechain=False, agent_id=None):
    e = base(sid, ts, sidechain, agent_id)
    e.update(type="user", toolUseResult={"stdout": text},
             message={"role": "user", "content": [
                 {"type": "tool_result", "tool_use_id": tid, "content": text}]})
    return e


def sidechain_prompt(sid, ts, agent_id, text):
    e = base(sid, ts, True, agent_id)
    e.update(type="user", message={"role": "user", "content": text})
    return e


def write_jsonl(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2), encoding="utf-8")


def build(out_projects_dir):
    root = Path(out_projects_dir) / PROJECT_KEY
    t = Clock()
    O, S, H, H4 = "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-haiku-4-5"

    main = [
        human(SID, t(), "Audit the payment module for race conditions; done when each "
                        "finding has a failing test in tests/payments."),
        dict(base(SID, t()), type="user", isMeta=True, sourceToolUseID="tu_skill",
             message={"role": "user", "content": [{"type": "text", "text":
                      "Base directory for this skill: /x/skills/audit\n\n# Audit skill body"}]}),
        assistant(SID, t(), "req_m1", O, [tool_use("tu_a1", "Agent", {
            "subagent_type": "Explore", "description": "map payment module",
            "prompt": "Map src/payments."})]),
        tool_result(SID, t(), "tu_a1", "Mapped 14 files."),
        # one request split over two entries (thinking/text then tool_use): same requestId
        assistant(SID, t(), "req_m2", O, [{"type": "text", "text": "Fanning out a workflow."}]),
        assistant(SID, t(), "req_m2", O, [tool_use("tu_w1", "Workflow", {
            "script": "export const meta = {name: 'race-audit', description: 'x'}\n"})]),
        tool_result(SID, t(), "tu_w1", "Workflow launched in background. Task ID: tk1. "
                                        "runId: %s" % RUN_ID),
        dict(base(SID, t()), type="user", origin={"kind": "task-notification"},
             turnOrigin="task_notification",
             message={"role": "user", "content": "<task-notification>\n<task-id>tk1</task-id>"
                                                 "\n<status>completed</status>\n</task-notification>"}),
        dict(base(SID, t()), type="user", isMeta=True, turnOrigin="peer",
             origin={"kind": "peer", "from": "awf2", "handback": True},
             message={"role": "user", "content": "Another Claude session sent a message:\n"
                                                 "<agent-message from=\"awf2\">done</agent-message>"}),
        dict(base(SID, t()), type="user", isMeta=True,
             message={"role": "user", "content": "Stop hook feedback:\n[check.sh]: uncommitted changes"}),
        assistant(SID, t(), "req_m3", O, [tool_use("tu_b1", "Bash", {
            "command": "python3 -m pytest tests/payments -q"})]),
        tool_result(SID, t(), "tu_b1", "3 passed"),
        human(SID, t(), "Commit it."),
        assistant(SID, t(), "req_m4", O, [tool_use("tu_b2", "Bash", {
            "command": "git commit -m 'Add race tests'"}), {"type": "text", "text": "Committed."}]),
    ]
    write_jsonl(root / (SID + ".jsonl"), main)

    sub = root / SID / "subagents"
    # Explore agent (depth 1) that spawns a nested general-purpose agent (depth 2)
    write_jsonl(sub / "agent-aexp1.jsonl", [
        sidechain_prompt(SID, t(), "aexp1", "Map src/payments."),
        assistant(SID, t(), "req_s1", S, [tool_use("tu_s1", "Grep", {"pattern": "lock"})],
                  True, "aexp1"),
        tool_result(SID, t(), "tu_s1", "src/payments/ledger.py", True, "aexp1"),
        assistant(SID, t(), "req_s2", S, [tool_use("tu_s2", "Agent", {
            "subagent_type": "general-purpose", "description": "read ledger",
            "prompt": "Read ledger.py"}), {"type": "text", "text": "Mapped 14 files."}],
                  True, "aexp1"),
    ])
    write_json(sub / "agent-aexp1.meta.json", {
        "agentType": "Explore", "description": "map payment module", "toolUseId": "tu_a1",
        "spawnDepth": 1, "requestShape": "foreground", "requestNonInteractive": True})
    write_jsonl(sub / "agent-anest1.jsonl", [
        sidechain_prompt(SID, t(), "anest1", "Read ledger.py"),
        assistant(SID, t(), "req_n1", H, [tool_use("tu_n1", "Read", {"file_path": "ledger.py"})],
                  True, "anest1"),
    ])
    write_json(sub / "agent-anest1.meta.json", {
        "agentType": "general-purpose", "description": "read ledger", "toolUseId": "tu_s2",
        "parentAgentId": "aexp1", "spawnDepth": 2, "requestShape": "foreground"})

    # workflow run: three agents, journal, sidecar
    wf = sub / "workflows" / RUN_ID
    write_jsonl(wf / "agent-awf1.jsonl", [
        sidechain_prompt(SID, t(), "awf1", "Find races in ledger.py"),
        # replayed parent request: must not be double-counted
        assistant(SID, t(), "req_m1", O, [{"type": "text", "text": "(context)"}], True, "awf1"),
        assistant(SID, t(), "req_w1", S, [tool_use("tu_w1a", "Grep", {"pattern": "await"})],
                  True, "awf1"),
    ])
    write_json(wf / "agent-awf1.meta.json", {
        "agentType": "workflow-subagent", "description": "find:ledger",
        "workflowPhase": "Find", "spawnDepth": 1, "requestShape": "background"})
    write_jsonl(wf / "agent-awf2.jsonl", [
        sidechain_prompt(SID, t(), "awf2", "Verify race in ledger.py"),
        assistant(SID, t(), "req_w2", S, [tool_use("tu_w2a", "Bash", {
            "command": "pytest tests/payments/test_ledger_race.py"})], True, "awf2"),
        tool_result(SID, t(), "tu_w2a", "1 failed", True, "awf2"),
    ])
    write_json(wf / "agent-awf2.meta.json", {
        "agentType": "workflow-subagent", "description": "verify:ledger",
        "workflowPhase": "Verify", "spawnDepth": 1, "requestShape": "background"})
    write_jsonl(wf / "agent-awf3.jsonl", [   # no meta file on purpose
        sidechain_prompt(SID, t(), "awf3", "Verify race in refunds.py"),
        assistant(SID, t(), "req_w3", H, [tool_use("tu_w3a", "Read", {"file_path": "refunds.py"})],
                  True, "awf3"),
    ])
    write_jsonl(wf / "journal.jsonl", [
        {"k": "put", "addr": "a1", "kind": "agent", "status": "settled"},
        {"k": "settled", "addr": "a1", "status": "ok"},
    ])
    write_json(root / SID / "workflows" / (RUN_ID + ".json"), {
        "runId": RUN_ID, "timestamp": "2026-10-01T10:06:00.000Z", "name": "race-audit"})

    # legacy layout: sidechain transcript as a top-level agent-*.jsonl file
    write_jsonl(root / "agent-alegacy1.jsonl", [
        sidechain_prompt(SID, t(), "alegacy1", "Summarise findings"),
        assistant(SID, t(), "req_l1", H4, [tool_use("tu_l1", "Read", {"file_path": "notes.md"})],
                  True, "alegacy1"),
    ])

    write_jsonl(root / (PLAIN_SID + ".jsonl"), [
        human(PLAIN_SID, "2026-10-02T09:00:00.000Z", "What does ledger.py do?"),
        assistant(PLAIN_SID, "2026-10-02T09:01:00.000Z", "req_p1", O,
                  [{"type": "text", "text": "It records payments."}]),
    ])
    return root


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    print(build(sys.argv[1]))
