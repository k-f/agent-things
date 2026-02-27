---
name: user-skill-issue
description: Analyse your Claude Code interaction history to identify AI-fluency patterns, prompting habits, and behaviour gaps. Produces a scored report with evidence-backed recommendations. Safe to run on its own or as part of /skill-issue:skill-issue.
context: fork
agent: general-purpose
disable-model-invocation: true
argument-hint: [all|current]
allowed-tools: Bash, Read, Glob, Grep
---

# User Skill Issue — Interaction Log Analysis

You are performing an AI-fluency audit of this user's Claude Code interaction history.
Your output will be a rigorous, evidence-backed report that helps them become a more
effective Claude Code user.

## Step 1 — Clarify scope

Ask the user ONE question before proceeding:

> "Should I analyse logs from **just this project** or **all projects** on this machine?
> (Type `current` for just this project, `all` for everything. Default: current)"

Wait for the response. If the user does not reply within the conversation context,
default to **current project** and note this assumption.

Store the answer as SCOPE = "current" or "all".

## Step 2 — Check log retention

Run:
```bash
SCRIPT=$(find ~/.claude/plugins -name "extract_user_messages.py" 2>/dev/null | head -1)
[ -z "$SCRIPT" ] && SCRIPT="${CLAUDE_PLUGIN_ROOT:-}/scripts/extract_user_messages.py"
python3 "$SCRIPT" --check-retention
```

Parse the JSON output:
- If `cleanup_period_days` is null or not set → warn the user and offer to set it
- If `cleanup_period_days` < 60 → suggest increasing to at least 90 days
- If `cleanup_period_days` >= 90 → note it is well configured

**Offering to increase retention**: If retention is low, tell the user:
> "Your logs are only kept for X days (or the default ~30). I can extend this to 90 days
> by adding `\"cleanupPeriodDays\": 90` to `~/.claude/settings.json`. Shall I do that?
> More history gives a richer picture of your usage patterns."

If they agree, edit the file:
```bash
# Read current settings
cat ~/.claude/settings.json 2>/dev/null || echo "{}"
```
Then write back the updated JSON with `cleanupPeriodDays` set to 90 (preserving all existing keys).

## Step 3 — Extract user messages

Run the extraction script based on SCOPE:

```bash
# For current project:
python3 "$SCRIPT" --project "$(pwd)" --limit 300 --max-chars 600 --output-format json

# For all projects:
python3 "$SCRIPT" --all --limit 300 --max-chars 600 --output-format json
```

Parse the JSON. If `stats.total_messages_extracted` is 0 or very low (< 10), warn:
> "Very few messages found. Logs may be new, the retention period may be short, or
> this project may have minimal history. The analysis will be limited."

## Step 4 — Analyse against AI Fluency dimensions

Carefully read through ALL extracted messages before scoring.

For each dimension below, scan all messages for relevant evidence.
Quote specific messages (abbreviated to ~80 chars) to support each rating.

---

### Dimension 1: Prompt Clarity & Specificity

Look for: precise acceptance criteria, specific function/file names, edge cases stated,
expected output format described, version/environment constraints mentioned.

Look out for: "make it work", "fix this", "do the thing", pronouns without referents
("the function", "it", "that thing"), no success criteria.

**Score 1–5:**
- 5: Nearly all prompts are specific, unambiguous, with acceptance criteria
- 4: Usually specific; occasionally vague
- 3: Mixed — some clear, some vague
- 2: Mostly vague; specificity is the exception
- 1: Consistently vague or underspecified

---

### Dimension 2: Context Provision

Look for: pasting error messages, stack traces, file references, mentioning related
files/functions, describing the broader system, stating constraints, environment details.

Look out for: no context given ("it broke"), not mentioning what they already tried,
not sharing relevant code or error output.

**Score 1–5:**
- 5: Proactively shares error logs, file paths, relevant code snippets, system context
- 4: Usually provides context; occasional gaps
- 3: Sometimes shares context; Claude often has to ask or guess
- 2: Rarely shares context; relies on Claude to figure it out
- 1: No context provided; bare assertions like "it's not working"

---

### Dimension 3: Goal-Setting & Autonomy Granting

This is the highest-leverage dimension. High-capability users give Claude a goal +
success criteria and trust it to decide the approach. Low-capability users micro-manage
step by step.

Look for: "implement X so that Y", "make it so the tests pass", "refactor this to
follow our pattern", giving Claude latitude on *how*.

Look out for: "now add this parameter", "now write this exact function", "do only
this one thing and nothing else", excessive step-by-step decomposition that Claude
should be doing itself.

**Score 1–5:**
- 5: Gives Claude goals; trusts it to plan and execute autonomously
- 4: Usually goal-oriented; occasionally over-specifies steps
- 3: Mix of goal and step-by-step; inconsistent
- 2: Mostly step-by-step; rarely gives Claude latitude
- 1: Extreme micro-management; one tiny instruction at a time

---

### Dimension 4: Iterative Efficiency

Look for: referencing previous outputs ("now add X to what you just built"),
building progressively, not repeating context already established, session continuity.

Look out for: restarting from scratch in every session, re-explaining the same
context repeatedly, not building on prior outputs.

**Score 1–5:**
- 5: Smooth iterative building; sessions chain naturally; minimal repetition
- 3: Some continuity; occasional restarts
- 1: Each session starts from zero; no thread maintained

---

### Dimension 5: Feedback Quality

Look for: pasting test output, sharing "X works but Y fails", quoting specific error
messages in follow-ups, referencing exact lines or behaviours that are wrong.

Look out for: "that didn't work", "try again", "it's still broken" with no diagnostic
information.

**Score 1–5:**
- 5: Always provides specific, actionable feedback with evidence
- 3: Some specifics; often general
- 1: Consistently says "it's wrong" with no diagnostic detail

---

### Dimension 6: Claude Code Feature Utilisation

Look for: mentions of `/` commands, references to CLAUDE.md, mentions of agents or
skills, discussion of hooks, project settings, using Claude for CI/CD or automation.

Look out for: treating Claude Code as a plain chatbot with no project memory, no
mention of tooling or automation.

**Score 1–5:**
- 5: Uses CLAUDE.md, skills, agents, hooks; integrates Claude into workflows
- 3: Uses some features (maybe a CLAUDE.md) but not advanced ones
- 1: No evidence of using Claude Code's extended capabilities

---

### Dimension 7: Domain Vocabulary & Precision

Look for: correct use of technical terms, referencing specific APIs/frameworks by name,
precise architectural language.

Look out for: describing things by analogy ("the thing that stores users"), avoiding
technical names, overly informal descriptions of technical concepts.

**Score 1–5:**
- 5: Precise, domain-appropriate vocabulary throughout
- 3: Mostly correct; occasional vagueness
- 1: Avoids technical vocabulary; indirect descriptions

---

## Step 5 — Identify user capability profile

Based on the overall evidence, classify the user as one of:

- **Navigator** (avg 4.5+): Gives Claude goals and frameworks; treats it as a peer engineer; high autonomy; good feedback loops; full feature utilisation
- **Collaborator** (avg 3.5–4.4): Generally effective; some micro-management or context gaps; uses core features; room to extend
- **Delegator** (avg 2.5–3.4): Getting there; inconsistent quality; misses opportunities to leverage Claude's capabilities
- **Apprentice** (avg 1.5–2.4): Still learning the interaction model; tends to micro-manage or provide thin context
- **Beginner** (avg < 1.5): Using Claude Code like a basic chatbot; foundational skills needed

## Step 6 — Produce the report

Output this exact structure:

---

## User AI Fluency Report

**Capability Profile**: [Navigator / Collaborator / Delegator / Apprentice / Beginner]

**Sessions analysed**: N | **Messages reviewed**: N | **Date range**: YYYY-MM-DD – YYYY-MM-DD

**Log retention**: X days [configured] / Not configured (using default ~30 days) — [recommendation if low]

---

### Scores

| Dimension | Score | Rating |
|---|---|---|
| Prompt Clarity & Specificity | X/5 | ⭐⭐⭐ |
| Context Provision | X/5 | |
| Goal-Setting & Autonomy | X/5 | |
| Iterative Efficiency | X/5 | |
| Feedback Quality | X/5 | |
| Feature Utilisation | X/5 | |
| Domain Vocabulary | X/5 | |
| **Overall** | **X.X / 5** | |

---

### Strengths

For each strength, include a supporting quote from the actual messages.

1. **[Strength title]**: [explanation] — *Example: "[message quote ~60 chars]"*
2. **[Strength title]**: [explanation] — *Example: "[message quote ~60 chars]"*

---

### Growth Areas

For each area, include the evidence and a concrete suggestion.

1. **[Area]**: [What you observed] — *Example: "[message quote]"*
   - **Try instead**: [specific rewrite or technique]

2. **[Area]**: [What you observed] — *Example: "[message quote]"*
   - **Try instead**: [specific rewrite or technique]

---

### Top Recommendations (ranked by impact)

1. **[Title]** — [1–2 sentence description of what to change and why it matters]
2. **[Title]** — [1–2 sentence description]
3. **[Title]** — [1–2 sentence description]

---

### Resources

- [Claude Code skills documentation](https://code.claude.com/docs/en/skills) — create project-specific slash commands
- [CLAUDE.md memory guide](https://code.claude.com/docs/en/memory) — persistent project context
- [Custom agents](https://code.claude.com/docs/en/sub-agents) — specialised task delegation
