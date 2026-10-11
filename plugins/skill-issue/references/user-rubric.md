# User Rubric — Claude Code Interaction Analysis

The single source of truth for scoring how a user works with Claude Code. Used by
`/skill-issue:user-skill-issue` and by the `log-analyst` agent inside `/skill-issue:skill-issue`.

Be honest and calibrated. Do not grade on a curve. Every score needs evidence: a metric
from the extraction output, a quoted message (abbreviated to ~100 chars), or both.

## Inputs

You need these before starting. The caller supplies them.

- `SCRIPT` — absolute path to `extract_user_messages.py`
- `SCOPE` — `current` (this project's logs) or `all` (every project on this machine)
- `PROJECT_DIR` — absolute path of the project (for `current` scope)
- `PROJECT_TYPE` — one of the ids in `project-types.md` (used to judge Output Quality Standards)
- `PROJECT_CONTEXT` — optional short summary of the persistent context the project already
  gives Claude (CLAUDE.md coverage, skills, agents, hooks). If absent, build it yourself (Step 2).

## Step 1 — Retention and extraction

```bash
python3 "$SCRIPT" --check-retention
# current scope
python3 "$SCRIPT" --project "$PROJECT_DIR" --limit 400 --max-chars 1200 --output-format json
# all scope
python3 "$SCRIPT" --all --limit 400 --max-chars 1200 --output-format json
```

If the current-scope run matches no project dir, the script prints the closest candidates on
stderr. Run `python3 "$SCRIPT" --check-projects --project "$PROJECT_DIR"` and, if the project
has sessions started from subdirectories, re-run with `--include-subdirs`. Do not switch to
`--all` silently: say in the report that scope was widened and why.

If `stats.messages_truncated` is more than ~10% of returned messages, re-run with `--max-chars 2000`.

The JSON has four parts:
- `stats` — counts, matched project dirs, retention
- `summary` — aggregate structural metrics across sessions (primary evidence for D4, D5, D7),
  including the delegation aggregates listed below
- `sessions` — per-session metrics (`cwd`, `user_messages`, `tool_uses_per_user_message`,
  `max_tool_uses_between_user_messages`, `subagent_types`, `skills_invoked`, `slash_commands`,
  `test_runs`, `interrupts`, `duration_seconds`, `active_seconds`, `compactions`,
  `task_notifications`, `peer_messages`, and a `delegation` block — see below)
- `messages` — genuine prompts only (tool results, injected skill bodies, hook output,
  system reminders, compaction summaries, task notifications, subagent hand-backs and
  local-command output are already removed; slash-command arguments are kept and tagged with
  `slash_command`). Each has a `source`: `human`, `command`, or `sdk` (sent programmatically,
  e.g. `claude -p` or the Agent SDK — judge prompting skill on `human`/`command` messages; an
  sdk prompt may have been written by a script or another agent)

**Delegation metrics come from the subagent and workflow transcripts**, not just the main
thread. The main thread's `subagent_spawns` counts only Agent/Task calls the main agent made;
`delegation` also sees agents those agents spawned, forked skills, and Workflow-run agents.

| Per session `delegation.*` | Summary counterpart | Meaning |
|---|---|---|
| `agents_total`, `by_source` {agent, workflow, team}, `by_type` | `delegated_agents`, `delegated_agents_by_source`, `delegated_agent_types`, `sessions_with_delegated_agents` | Agent transcripts the session produced, how they were started, and their types |
| `max_spawn_depth`, `nested_agents` | `max_spawn_depth`, `nested_agents`, `sessions_with_nested_agents` | Agents spawned by agents (depth ≥ 2) |
| `background_agents` | `background_agents` | Agents run in the background while the session continued |
| `stopped_by_user` | `agents_stopped_by_user` | Agents the person killed |
| `skill_forks` | `skill_forks` | Forked skills (`context: fork`) run as agents |
| `delegated_tool_calls` | `delegated_tool_calls`, `delegated_tool_calls_share` | Tool calls made inside agents (share = of all tool calls) |
| `delegated_test_runs` | `delegated_test_runs`, `sessions_with_any_test_runs` | Test-runner commands run inside agents |
| `workflow_runs` [{run_id, name, agents, phases, agent_types, launched_by_tool_use, has_verify_phase}], `workflow_launches` | `workflow_runs`, `workflow_agents`, `workflow_runs_with_verify_phase`, `workflow_launches`, `sessions_with_workflows` | Dynamic workflows (Workflow tool) and their phases |
| (session level) `task_notifications`, `peer_messages` | same | Background-task completions and subagent hand-backs the session received and handled |

**Retention** — never edit `~/.claude/settings.json`. If `recommend_change` is true, report:

> Log retention is <N> days<, the default>. Claude Code deletes older transcripts, which
> limits this analysis. Add to `~/.claude/settings.json`:
> ```json
> { "cleanupPeriodDays": 90 }
> ```

## Step 2 — Establish persistent context before judging prompts

Short prompts are good when CLAUDE.md, skills and agents already carry the context. Before
scoring Context Provision or Prompt Clarity, know what Claude already had:

- If `PROJECT_CONTEXT` was supplied, use it.
- Otherwise read `~/.claude/CLAUDE.md` and, for each distinct session `cwd` (top 5 by
  message count), `<cwd>/CLAUDE.md` plus a listing of `<cwd>/.claude/skills`,
  `<cwd>/.claude/agents`, `<cwd>/.claude/settings.json`. Summarise in 3–5 lines.

## Step 3 — Sample size

Band by `summary.user_messages`:

| Messages | Band | What to say |
|---|---|---|
| < 10 | INSUFFICIENT | "INSUFFICIENT DATA — provisional only." Score only dimensions with direct evidence; mark the rest `n/a`. No profile. |
| 10–30 | LIMITED | "LIMITED SAMPLE — directional, not definitive." Confidence at most Medium. |
| 31–100 | MODERATE | "MODERATE SAMPLE — reasonable confidence." |
| > 100 | GOOD | "GOOD SAMPLE — patterns reliable." |

Also flag when one session holds more than half the messages: patterns may reflect one task.

## Step 4 — Score the 8 dimensions

Read every message before scoring. For each dimension collect evidence first, then score 1–5
with a confidence (High / Medium / Low). Metrics marked **primary** decide the score band;
quotes refine within it and explain it.

### D1. Prompt Clarity & Specificity

Look for: named files/functions/endpoints, edge cases stated, acceptance criteria, constraints.
Red flags: "make it work", "fix this", pronouns with no referent, no definition of done.
Judge relative to persistent context: "run the release checklist" is clear if a release skill exists.

- **5** Unambiguous requests with acceptance criteria; Claude knows when it is done
- **4** Usually specific; occasionally vague on acceptance criteria
- **3** Mixed; Claude often has to guess what success looks like
- **2** Usually vague; specificity is the exception
- **1** Consistently underspecified; Claude must ask before every action

### D2. Context Provision (relative to persistent context)

Score what the user supplies **on top of** what CLAUDE.md / skills / agents already provide.
Look for: error output and stack traces pasted inline, what was already tried, constraints,
pointers to relevant files. Red flags: "it broke" with no output; re-explaining things the
CLAUDE.md already says (wasted effort, and a sign CLAUDE.md is stale or unknown to the user);
expecting Claude to remember a previous session.

- **5** Every request carries the situational context Claude lacks; nothing redundant with CLAUDE.md
- **4** Usually sufficient; occasional gaps
- **3** Sometimes sufficient; Claude often runs discovery or assumes
- **2** Rarely provides situational context
- **1** None, and no persistent context either; Claude rediscovers everything each time

A terse user with a strong CLAUDE.md can score 4–5. A verbose user who re-pastes project
background every session because there is no CLAUDE.md scores 3 at most — the fix is the
CLAUDE.md, and this is a reinforcing gap to report.

### D3. Goal-Setting & Success Criteria

Can Claude know it is done without asking?
- Task: "Add a test for the login function"
- Goal with criteria: "Login fails when the password has special chars — make the tests pass and cover similar cases"
- Autonomous goal: "Implement JWT auth so a user can log in and keep a session; the security tests must pass"

- **5** Goals with explicit, checkable success criteria (tests pass, command output, metric); compatible with long autonomous runs
- **4** Clear goals; success usually inferable
- **3** Mix of goals and step instructions
- **2** Mostly explicit steps; Claude executes and waits
- **1** Micro-steps ("now add this line"); Claude is a keyboard

### D4. Autonomy Depth & Delegation

**Primary evidence (metrics):** `summary.tool_uses_per_user_message`,
`median_session_tool_uses_per_user_message`, `max_tool_uses_between_user_messages`,
`sessions_with_20plus_tool_run`, `subagent_spawns`, `sessions_with_subagents`,
`subagent_types`, `sessions_with_test_runs`, `median_active_seconds`, and the delegation
aggregates: `delegated_agents`, `delegated_tool_calls` (+ `_share`), `nested_agents`,
`background_agents` with `task_notifications`/`peer_messages`, `workflow_runs`,
`workflow_runs_with_verify_phase`, `delegated_test_runs`, `agents_stopped_by_user`.
**Secondary:** quotes showing the size of work delegated ("build the whole importer", vs
"rename this variable").

`tool_uses_per_user_message` counts main-thread tool calls only. When much of the work ran
in agents (`delegated_tool_calls_share` ≥ ~0.3), judge the size of each delegated unit of
work as main + delegated tool calls per user message, and say so in the evidence.

**Self-validation** counts wherever it ran: main-thread `test_runs`, `delegated_test_runs`
(tests run inside agents), and workflow phases named like verify/review/test/check
(`has_verify_phase`). Strong evidence of delegated autonomous work: many delegated tool
calls per prompt, nested agents, workflow runs with a verify phase, background agents whose
`task_notifications` / `peer_messages` the session handled without the person re-prompting.

| Score | Metric anchors (typical; use judgement at the edges) |
|---|---|
| 5 | Median ≥ 15 tool uses per user message (counting delegated calls, see above) **and** repeated 20+-tool runs **and** delegation with self-validation: subagents/workflows whose work is verified (delegated test runs, verify phases, tests after agents return) and/or test runs inside long runs, or CI-driven Claude runs. Large multi-file work delivered with few corrections. |
| 4 | Median 8–15; some 20+-tool runs; subagents, workflows or in-session test runs present |
| 3 | Median 4–8; moderate single-agent runs; little delegation |
| 2 | Median 2–4; mostly short exchanges |
| 1 | Median < 2; one instruction, one action |

No single feature is required for any score. Agent teams and dynamic workflows are
supporting evidence when used; their absence is never a gap. Subagent use is evidence of
delegation, not a requirement — a user whose long single-agent runs end in green test runs
is delegating well. Reward **demonstrated delegation with verification**, not volume.
High interrupt rates during long runs lower this score (see D5).

**Over-delegation is a quality concern, not a strength.** Flag it (and cap D4 at 3 unless
other evidence is strong) when the evidence shows fan-out without checking, e.g.:
- many agents or workflow agents (say ≥ 10 in a session) with no `delegated_test_runs`, no
  main-thread `test_runs` after them, and no workflow run with `has_verify_phase`;
- `agents_stopped_by_user` high relative to `delegated_agents` (≥ ~20%): the person keeps
  killing agents that went the wrong way — look at the prompts that launched them;
- deep nesting (`max_spawn_depth` ≥ 3) or large workflows on small tasks, judged against the
  quoted prompt.
Quote the numbers in the evidence column either way.

### D5. Feedback Quality & Iteration

**Primary evidence (metrics):** `interrupts_per_100_user_messages` and per-session
`interrupts`; whether follow-up messages after a long run contain diagnostic content.
**Secondary:** quotes of corrections.

Good: "login passes but registration fails with: <trace>", pasted test output, "works for
happy path, breaks when profile is null". Bad: "that didn't work", "try again", restarting
in a new session instead of iterating.

Interpret interrupts with the quotes around them: an interrupt followed by a precise
redirection is good steering; frequent interrupts followed by vague retries are not.
Roughly: > 25 per 100 messages suggests the user is fighting the agent (check why);
0 with long runs and specific follow-ups is healthy.

- **5** Corrections carry evidence (errors, test output, failing case); Claude self-corrects
- **4** Usually specific; occasional "that didn't work"
- **3** Mixed specific and general
- **2** Mostly general; Claude must probe
- **1** No feedback loop: accepts first output or restarts

### D6. Output Quality Standards (calibrated to `PROJECT_TYPE`)

Does the user state the quality bar, and is it right for the stakes?
- `poc`: "doesn't need to be production-ready" is correct
- `internal`: reliable, low polish
- `production` / `library`: error handling, tests, docs, compatibility
- `regulated` / `safety-critical`: compliance, audit logging, review, verification

Red flags: no quality bar ever; production polish on throwaway work; "quick hack" on a
production or regulated system; never mentions tests or review. A strong CLAUDE.md that
encodes the quality bar counts in the user's favour — they set it once.

- **5** Bar stated (or encoded in CLAUDE.md/skills) and matched to stakes; requests tests/review at the right level
- **4** Usually stated; reasonable when implicit
- **3** Inconsistent; often left to Claude
- **2** Rarely stated
- **1** Never stated, or wrong for the stakes

### D7. Claude Code Feature Utilisation

**Primary evidence (metrics):** `skills_invoked`, `slash_commands`,
`sessions_using_skills_or_commands`, `subagent_types` and `delegated_agent_types`
(custom/plugin agent types beyond the built-in `general-purpose`, `Explore`, `Plan`,
`claude-code-guide`, `workflow-subagent` show investment; `delegated_agent_types` also sees
agents spawned by agents and forked skills), `skill_forks`, `workflow_runs` /
`sessions_with_workflows` (dynamic workflows, with their names), `compactions` (manual
`/compact` appears in `slash_commands`), plus the persistent context from Step 2 (CLAUDE.md,
project skills, agents, hooks). **Secondary:** messages that mention features.

Workflows and custom agent types count as leverage like skills do, but none of them is
required for a 5.

Do not score from whether messages mention the words "skill" or "agent". Score what was used.
Separate built-in housekeeping commands (`clear`, `compact`, `model`, `config`, `help`, `resume`,
`cost`, `status`, `permissions`, `context`, `exit`) from workflow commands (project/user/plugin
skills and commands, `init`, `memory`, `review`, `agents`, `hooks`, `mcp`). Housekeeping shows
basic fluency; workflow commands and skills show leverage.

- **5** Skills/commands used in most sessions; custom or plugin agents (or workflows) used; CLAUDE.md maintained; hooks or CI integration present where they make sense
- **4** Regular skill/command use; CLAUDE.md maintained; some custom agents or hooks
- **3** CLAUDE.md exists; occasional skills or workflow commands
- **2** Housekeeping commands only; no skills; thin or no CLAUDE.md
- **1** Plain chat; no persistent context, no commands, no delegation

### D8. Domain Vocabulary & Technical Precision

- **5** Precise domain and technical vocabulary; names APIs, protocols, standards, patterns correctly
- **3** Mostly correct; occasional vagueness
- **1** Indirect descriptions ("the thing that talks to the database")

## Step 5 — Capability profile

Overall = mean of scored dimensions (exclude `n/a`). Then apply the gates: a profile is only
awarded if its requirement is met; otherwise drop one level.

| Profile | Score | Gate (required evidence) |
|---|---|---|
| **Orchestrator** | ≥ 4.5 | Large delegated or autonomous work with self-validation: D4 ≥ 4 with repeated 20+-tool runs (main-thread or delegated) **and** at least one of — subagent/workflow delegation that includes verification (`delegated_test_runs`, a verify-phase workflow run, or tests run after the agents return), in-session test runs closing out the work, CI-driven Claude runs. Not tied to any specific feature. Not awarded when over-delegation is flagged. |
| **Navigator** | 3.5–4.4 | D3 ≥ 4 and D4 ≥ 3: clear goals with criteria, sessions run autonomously |
| **Collaborator** | 2.5–3.4 | Mostly goal-oriented; uses basic features; inconsistent feedback |
| **Apprentice** | 1.5–2.4 | Task-by-task; thin context; little feature use |
| **Beginner** | < 1.5 | Chatbot usage; one-liners; no persistent context |

A user who writes excellent prompts but supervises every step is a Navigator, not an
Orchestrator. With an INSUFFICIENT sample, report no profile.

## Step 6 — Report section

Use this template. Keep quotes short. Every strength and growth area cites a metric or a quote.

```markdown
## User Analysis

**Profile**: <name> — <1–2 sentence characterisation citing evidence>
**Sample**: <INSUFFICIENT|LIMITED|MODERATE|GOOD> — <N> messages, <N> sessions, <date range>, scope `<current|all>`
**Persistent context assumed**: <1 line from Step 2>

> <If LIMITED/INSUFFICIENT or single-session dominated: which scores are provisional and why>

### Session structure
| Metric | Value |
|---|---|
| Tool uses per user message (overall / median session) | x / x |
| Longest autonomous run (tool uses) | x |
| Sessions with 20+-tool runs | x of N |
| Subagent spawns from main thread (types) | x (type: n, ...) |
| Delegated agents (by source; nested; background; stopped by user) | x (agent n, workflow n, team n; nested n; bg n; stopped n) |
| Tool calls inside agents (share of all) | x (x%) |
| Workflow runs (agents; with verify phase) | x (n; n) — names |
| Tests run (main thread / inside agents) | x / x |
| Task notifications + hand-backs handled | x + x |
| Skills / workflow commands used | name (n), ... |
| Sessions with any test runs | x of N |
| Interrupts per 100 messages | x |
| Median session length (active) | x min |

### Scores
| Dimension | Score | Confidence | Key evidence |
|---|---|---|---|
| D1 Prompt Clarity & Specificity | x/5 | H/M/L | |
| D2 Context Provision | x/5 | | |
| D3 Goal-Setting & Success Criteria | x/5 | | |
| D4 Autonomy Depth & Delegation | x/5 | | metric |
| D5 Feedback Quality & Iteration | x/5 | | metric + quote |
| D6 Output Quality Standards | x/5 | | |
| D7 Feature Utilisation | x/5 | | metric |
| D8 Domain Vocabulary | x/5 | | |
| **Overall** | **x.x/5** | | |

### Strengths
1. **<title>** — <evidence: metric and/or "quote">

### Growth areas
1. **<title>** — <observed pattern + evidence> → **Try**: <concrete rewrite or behaviour, before/after>

### Recommendations (unlock order)
1. **<action>** (Effort: Low/Med/High · Unlocks: <what>) — <why, expected effect>

### Log retention
<retention status; settings.json snippet if recommend_change>
```
