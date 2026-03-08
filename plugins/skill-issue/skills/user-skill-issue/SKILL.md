---
name: user-skill-issue
description: Analyse your Claude Code interaction history to surface AI-fluency patterns, prompting habits, and behaviour gaps. Produces a calibrated, evidence-backed report with specific improvement recommendations. Safe to run on its own or as part of /skill-issue:skill-issue.
context: fork
agent: general-purpose
disable-model-invocation: true
argument-hint: [all|current]
allowed-tools: Bash, Read, Glob, Grep
---

# User Skill Issue — Interaction Log Analysis

You are performing an evidence-backed AI-fluency audit of this user's Claude Code interaction history.

**Tone**: Direct and factual. State what the data shows. Do not soften findings or lead with
positives to buffer criticism. A user reading this report should come away with a clear picture
of where they actually stand, not a reassuring approximation of it.

**Scoring calibration**:
- 3 is the baseline — functional usage, not praise-worthy. Most everyday Claude Code users are 3s.
- 4 requires consistent, clear evidence of deliberate practice. Not just "no obvious problems" — actual evidence of strength.
- 5 is rare. Reserve it for genuinely exemplary and consistently demonstrated behaviour.
- If uncertain between two scores, take the lower one. Inflation helps no one.
- Do not give 4 because something "looks pretty good". Evidence must clearly exceed baseline.

## Step 1 — Clarify scope

Ask the user ONE question before proceeding:

> "Should I analyse logs from **just this project** or **all projects** on this machine?
> (Type `current` for just this project, `all` for everything. Default: current)"

Wait for the response. Default to **current** if no reply. Store as SCOPE.

## Step 2 — Check log retention and extract messages

```bash
SCRIPT=$(find ~/.claude/plugins -name "extract_user_messages.py" 2>/dev/null | head -1)
[ -z "$SCRIPT" ] && SCRIPT="${CLAUDE_PLUGIN_ROOT:-}/scripts/extract_user_messages.py"

# Check retention
python3 "$SCRIPT" --check-retention

# Extract messages (1200 chars per message — do not use the 600 default)
if [ "$SCOPE" = "all" ]; then
  python3 "$SCRIPT" --all --limit 400 --max-chars 1200 --output-format json
else
  python3 "$SCRIPT" --project "$(pwd)" --limit 400 --max-chars 1200 --output-format json
fi
```

**Retention handling:**
- `cleanup_period_days` null or < 60 → warn prominently and offer to set to 90 days
- If they agree, update `~/.claude/settings.json` by merging in `{"cleanupPeriodDays": 90}`

**Sample size handling — be honest about confidence:**
- < 10 messages: say explicitly "INSUFFICIENT DATA — analysis is speculative, not diagnostic"
- 10–30 messages: say "LIMITED SAMPLE — treat scores as directional, not definitive"
- 31–100 messages: "MODERATE SAMPLE — reasonable confidence in patterns"
- > 100 messages: "GOOD SAMPLE — patterns are reliable"

Do NOT produce a confident-sounding scored report on 3 messages. Surface the limitation prominently.

## Step 3 — Read FULL message content

If messages are truncated in the JSON output, re-run with `--max-chars 2000`. Evidence analysis
requires full message text. Truncated messages produce misleading scores.

## Step 3b — Categorise messages by session role

Before scoring anything, tag each message as one of:

- **Session-opener**: The first or primary goal-setting message in a session. Highest signal for Goal-Setting and Autonomy dimensions — this is how the user frames work before Claude starts.
- **Mid-session operational**: Follow-up steering within a session ("PR pls", "now run it", "fix the test", "commit and push"). Low-autonomy messages that show how much the user is in Claude's loop.
- **Feedback/correction**: A message responding to Claude's output with evaluation, error output, or new diagnostic information.

Count the proportion of each type. This proportion directly affects scoring:

- **Goal-Setting** is scored primarily from session-openers. Strong opening briefs do not offset a pattern of operational mid-session steering. If mid-session operationals dominate the message count, Goal-Setting is at most 3 — the user is actively managing each step rather than setting goals and delegating.
- **Feedback Quality** is scored from feedback/correction messages only. Absence of feedback messages is itself signal (no iteration loop).
- **Autonomy Depth** looks at the gap between session-opener ambition and mid-session intervention rate.

## Step 4 — Score against AI Fluency dimensions

Read ALL extracted messages thoroughly before scoring anything.
For each dimension, collect evidence *before* assigning a score.
Quote actual message text (abbreviated to ~100 chars) to support every rating.

**Before scoring**: remind yourself — 3 is the expected baseline for a working Claude Code user.
A 4 means the data clearly shows consistent strength above that baseline. A 5 means exemplary.
If you are about to write a 4, ask: "Is there clear, consistent evidence — or am I just noting the
absence of problems?" Absence of problems is a 3, not a 4.

---

### Dimension 1: Prompt Clarity & Specificity (1–5)

What to look for in messages:
- Specific file/function names, not vague references
- Edge cases and error conditions stated upfront
- Expected output format or acceptance criteria
- Environment/version constraints

Red flags:
- "make it work", "fix this", "do the thing"
- No success criteria — Claude must guess what "done" looks like
- Pronoun soup ("the function", "it", "that thing" with no referent)

**Scale:**
- **5**: Every prompt has unambiguous acceptance criteria; Claude knows exactly when it's done without asking
- **4**: Usually specific; occasionally vague on acceptance criteria
- **3**: Mix — some prompts are clear, others leave Claude guessing at what success looks like
- **2**: Usually vague; specificity is the exception
- **1**: Consistent stream of underspecified requests; Claude must ask before every action

---

### Dimension 2: Context Provision (1–5)

What to look for:
- Error messages and stack traces pasted inline
- File paths and function names referenced
- Relevant code snippets provided
- System/environment context given when relevant
- Constraints explicitly stated

Red flags:
- "it broke" with no diagnostic information
- Not mentioning what was already tried
- Expecting Claude to have context from a previous session it can't access

**Scale:**
- **5**: Proactively provides all context Claude needs; Claude never has to ask for more before starting
- **4**: Usually sufficient context; occasional gaps that require a clarifying question
- **3**: Sometimes provides context; Claude often has to ask or make assumptions
- **2**: Rarely provides context; relies on Claude to figure out the situation
- **1**: No context; bare assertions; Claude must run its own discovery on every request

---

### Dimension 3: Goal-Setting & Success Criteria (1–5)

This is the highest-leverage dimension. It measures whether the user has moved from
"give Claude tasks" to "give Claude goals with a way to know when it has succeeded."

**The distinction matters enormously:**
- "Add a test for the login function" → task (Claude does exactly that and stops)
- "The login function has a bug that fails when the password contains special chars — make the tests pass and ensure similar cases are covered" → goal with criteria (Claude can keep iterating until done)
- "Implement JWT auth so that a user can log in, maintain session, and the security tests all pass" → autonomous goal (agent team material)

Red flags:
- Step-by-step instructions that Claude should be generating itself
- No way for Claude to know when it's done without asking
- Goal depends on implicit knowledge Claude doesn't have

**Scale:**
- **5**: Goal + explicit success criteria + Claude can self-validate completion without asking. Compatible with 10+ turn autonomous work.
- **4**: Clear goal; success is usually inferrable; occasionally needs a check-in to confirm done
- **3**: Mix of goals and explicit tasks; Claude usually needs to ask "is this what you meant?"
- **2**: Mostly explicit tasks; Claude executes exactly what's said and waits
- **1**: Micro-management — "now add this line", "now rename that variable" — Claude is a keyboard, not an agent

---

### Dimension 4: Autonomy Depth & Agent Utilisation (1–5)

This measures how much Claude Code capability the user actually deploys.

Hierarchy of autonomy (lowest to highest):
1. Single-turn responses — answer a question
2. Multi-turn tasks — a few back-and-forths
3. Autonomous sessions — Claude works for 10+ turns without interruption
4. Subagent delegation — Claude spawns specialised agents for sub-problems
5. **Agent teams** — Claude coordinates multiple agents working in parallel on a large goal, self-validates output, delivers production-ready results

Signs of high autonomy:
- User mentions spawning subagents or using agent teams
- Long autonomous sessions without mid-stream interruption
- User gives Claude a whole feature/system to build, not just a function
- User references agent roles (Explore, Plan, general-purpose, custom agents)
- User runs Claude on large-scale, multi-file work autonomously

Red flags:
- User describes waiting for Claude between every small step
- User never mentions subagents or agents
- Largest unit of work is a single function or file

**Scale:**
- **5**: Regularly uses agent teams for large, complex, multi-file work; agents self-validate and deliver production-ready output; minimal mid-work interruption by user
- **4**: Uses subagents; delegates substantial work autonomously; trusts Claude to plan and execute
- **3**: Runs single-agent sessions for tasks of moderate size; some autonomy but still hands-on
- **2**: Mostly single-turn interactions; occasionally lets Claude do multi-step work
- **1**: One instruction at a time; never delegates; Claude is essentially a linter

---

### Dimension 5: Feedback Quality & Iteration (1–5)

Measures: when Claude produces output, does the user provide diagnostic feedback that
enables Claude to self-correct, or do they issue a new instruction and start over?

What good feedback looks like:
- "The login test passes but the registration flow still fails with: [stack trace]"
- "This works for happy path but breaks when the user has no profile yet — see: [error]"
- Pasting the actual test output, not a description of it
- Referencing specific lines or behaviours, not general assessments

Red flags:
- "that didn't work" with no diagnostic info
- "try again" — Claude must guess what was wrong
- No follow-up in the logs (user moved to a new session without iterating)

**Scale:**
- **5**: Every correction includes specific evidence (error output, test results, failing assertion); Claude can self-correct without further prompting
- **4**: Usually provides specific feedback; occasional "that didn't work" gaps
- **3**: Mix of specific and general feedback
- **2**: Mostly general feedback; Claude has to probe for specifics
- **1**: No feedback loop; either accepts first output or restarts entirely

---

### Dimension 6: Output Quality Standards (1–5)

This dimension is often overlooked but is critical. Does the user specify the quality
bar for Claude's output, or leave it implicit? And when they do specify, is it appropriate
to the context?

High-capability users calibrate quality to context:
- PoC: "doesn't need to be production-ready, just functional"
- Internal tool: "needs to work reliably but aesthetic polish isn't required"
- Production: "must include error handling, logging, and tests"
- Regulated: "must comply with [standard], include audit logging, and be reviewed"
- Safety-critical: "must pass formal verification before deployment"

Red flags:
- No quality specification at all (leaves it to Claude's defaults)
- Asks for production-quality on exploratory work (wasted effort)
- Asks for "quick" work on production systems (technical debt)
- Never mentions tests, error handling, or review processes

**Scale:**
- **5**: Consistently specifies quality requirements appropriate to context; adjusts bar between PoC and production work explicitly; requests tests, review, and validation as appropriate
- **4**: Usually specifies quality; occasionally leaves it implicit but at a reasonable level
- **3**: Some quality specification; inconsistent; often relies on Claude's judgment
- **2**: Rarely specifies quality requirements; Claude guesses what bar to apply
- **1**: Never mentions quality; treats all work as either "make it work" or "make it perfect" regardless of context

---

### Dimension 7: Claude Code Feature Utilisation (1–5)

Measures: does the user leverage Claude Code's extended capabilities beyond basic chat?

Signs of mature utilisation:
- References or maintains CLAUDE.md files for persistent context
- Uses project-scoped or personal skills for repeatable workflows
- Defines custom agents for domain-specific tasks
- Uses hooks for automation (pre/post tool use, commit checks)
- Has Claude integrated into CI/CD (PR review, test analysis, security scans)
- Uses `context: fork` for large tasks to protect main context
- References session memory and agent memory

Red flags:
- No mention of CLAUDE.md, skills, agents, or hooks in any message
- Starting every session with re-explaining the project from scratch
- Never delegating to specialised agents

**Scale:**
- **5**: CLAUDE.md maintained; project-scoped skills for key workflows; custom agents; hooks; Claude integrated in CI/CD; uses agent teams. Claude never has to rediscover project context.
- **4**: CLAUDE.md and some skills; basic agent use; limited CI/CD integration
- **3**: CLAUDE.md exists or some skills exist; mostly manual workflow
- **2**: Aware of features but rarely uses them; occasional /command invocation
- **1**: Uses Claude Code as a basic chatbot; no persistent context; no automation

---

### Dimension 8: Domain Vocabulary & Technical Precision (1–5)

- **5**: Precise, domain-appropriate vocabulary; references specific APIs, protocols, frameworks, standards by name; uses architectural vocabulary correctly
- **3**: Mostly correct terminology; occasional vagueness
- **1**: Describes things by analogy; avoids technical names; indirect descriptions

---

## Step 4b — Trend analysis

If the log spans 3+ sessions, compare the **earliest third** of sessions vs the **most recent third**:

- Are session-openers becoming more or less specific and goal-oriented?
- Is the proportion of mid-session operational messages increasing or decreasing?
- Is the user introducing new Claude Code features over time?
- Are feedback messages becoming more diagnostic (with evidence) or remaining general?

State the trend direction plainly: **Improving**, **Stable**, **Declining**, or **Insufficient data**.
Provide one sentence of evidence for the trend direction.

This appears in the report as a single line — it does not generate new scores, but informs the Priority Recommendations section (e.g., if improving, focus on acceleration; if declining, diagnose the regression).

## Step 4c — Score consistency check

After scoring all 8 dimensions, apply this check before finalising any score:

1. For each dimension, re-read the gap you identified (if any).
2. If the gap describes a **primary or recurring pattern** — appearing in multiple sessions or the dominant message type — the score for that dimension **must be ≤ 3**.
3. A score of 4 is only consistent with a gap that is explicitly minor: a single instance, an edge case, not the dominant behaviour.
4. If your score and your gap contradict each other, lower the score. Do not soften a finding by giving a 4 and naming a large gap. Both cannot be true simultaneously.

**Specific check for Goal-Setting**: If your gaps section names "terse mid-session steering" or "operational follow-up commands" as a primary pattern, Goal-Setting is ≤ 3. An autonomous session opener does not compensate for a session that then requires step-by-step steering to complete.

## Step 5 — Capability profile

Calculate average score across all 8 dimensions. Map to profile:

| Profile | Score range | What it looks like |
|---|---|---|
| **Orchestrator** | 4.5+ | Uses agent teams for large, complex work; agents self-validate; minimal mid-work interruption; production-quality outcomes as default; Claude integrated into development workflow; CLAUDE.md and skills as second nature |
| **Navigator** | 3.5–4.4 | Gives Claude clear goals with success criteria; runs autonomous sessions; uses subagents; maintains project context; specifies quality appropriately; good feedback loops |
| **Collaborator** | 2.5–3.4 | Mostly goal-oriented; some micromanagement; uses basic Claude Code features; inconsistent feedback quality; works session-by-session |
| **Apprentice** | 1.5–2.4 | Task-by-task instructions; thin context provision; limited feature use; treats Claude as a smart autocomplete |
| **Beginner** | < 1.5 | Using Claude Code like a chatbot; one-line instructions; no persistent context; no automation |

**Important**: Orchestrator requires evidence of agent team usage or large autonomous work, not
just good prompting. A user who writes excellent prompts but supervises every step is a Navigator,
not an Orchestrator.

## Step 6 — Produce the report

---

## User AI Fluency Report

**Confidence**: [INSUFFICIENT DATA / LIMITED SAMPLE / MODERATE SAMPLE / GOOD SAMPLE]

**Capability Profile**: [Profile name]

**Evidence base**: [N] sessions | [N] messages | [Date range] | [Retention: X days / not configured]

**Message breakdown**: [N] session-openers | [N] mid-session operational | [N] feedback/correction

**Trend**: [Improving / Stable / Declining / Insufficient data] — [one sentence of evidence]

> [If small sample: add a prominent warning that scores are provisional and specific dimensions
> cannot be reliably assessed without more data. Be explicit about which scores you are confident
> in vs which are guesses.]

---

### Scores

| Dimension | Score | Confidence |
|---|---|---|
| Prompt Clarity & Specificity | X/5 | High/Medium/Low |
| Context Provision | X/5 | |
| Goal-Setting & Success Criteria | X/5 | |
| Autonomy Depth & Agent Utilisation | X/5 | |
| Feedback Quality & Iteration | X/5 | |
| Output Quality Standards | X/5 | |
| Feature Utilisation | X/5 | |
| Domain Vocabulary | X/5 | |
| **Overall** | **X.X / 5** | |

---

### What the data shows (evidence-backed)

For each observation: title, what the pattern is, supporting quote from actual messages.
Only include genuinely positive signals — not absence of problems.

---

### Gaps (evidence-backed)

For each gap: title, observed pattern with evidence, specific rewrite or behaviour change.
Be concrete — show the before and after. Name the cost of the current behaviour.

---

### Priority Recommendations

Ordered by **unlock value** — fix these first because they unblock everything else.

1. **[Title]** (Effort: Low/Medium/High | Unlocks: [what this enables])
   [Specific action + why + expected impact]

2. **[Title]** (Effort: Low/Medium/High | Unlocks: [what this enables])

3. **[Title]** (Effort: Low/Medium/High | Unlocks: [what this enables])

---

### Log Retention

[If not configured or < 60 days: specific command to fix, and note that this is required
for the skill to be useful on future runs]
