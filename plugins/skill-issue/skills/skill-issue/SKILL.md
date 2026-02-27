---
name: skill-issue
description: Full Claude Code effectiveness diagnosis — analyses both your interaction patterns (user AI fluency) and your project's configuration quality, then delivers a unified prioritised improvement report.
context: fork
agent: general-purpose
disable-model-invocation: true
argument-hint: [all|current]
allowed-tools: Bash, Read, Glob, Grep
---

# Skill Issue — Full Claude Code Diagnosis

You are running a comprehensive diagnosis of Claude Code effectiveness.
This covers TWO areas:
1. **User skill issues** — how well the user interacts with Claude Code
2. **Project skill issues** — how well this project is configured for Claude Code

Both analyses run in this same context to protect the main conversation window.
Work through them sequentially, then synthesise into a single unified report.

---

## Preliminary — Clarify scope

Before starting, ask the user ONE question:

> "I'm going to diagnose both your Claude Code interaction patterns AND this project's
> configuration. For the interaction log analysis, should I look at:
>
> 1. **current** — just this project's logs
> 2. **all** — logs from all projects on this machine
>
> (Reply `current` or `all`, or press Enter to use `current`)"

Wait for the response. Store it as SCOPE. Default to "current" if no reply.

---

## Part A — Project Configuration Audit

### A1. Discover CLAUDE.md files

```bash
echo "=== CLAUDE.md files ==="
find . -name "CLAUDE.md" -not -path "*/node_modules/*" -not -path "*/.git/*" -not -path "*/vendor/*" 2>/dev/null
```

Read each CLAUDE.md found. Evaluate against:
- Project overview / purpose present?
- Tech stack documented?
- Key commands (build / test / lint / deploy) listed?
- Coding conventions described?
- Architecture / non-obvious decisions explained?
- Anti-patterns or off-limits areas noted?
- Subdirectory CLAUDE.md files for specific areas?
- Length appropriate (under ~500 lines)?

### A2. Audit `.claude/` directory

```bash
echo "=== .claude/ structure ==="
find .claude -type f 2>/dev/null | sort
echo "=== settings.json ==="
cat .claude/settings.json 2>/dev/null || echo "(no settings.json found)"
echo "=== skills/commands ==="
find .claude/skills .claude/commands -type f 2>/dev/null | sort
echo "=== agents ==="
find .claude/agents -type f 2>/dev/null | sort
```

Read each skill/command and agent file found. Evaluate quality and coverage.

**Key workflows that SHOULD have skills:**
- commit / changelog creation
- PR/code review
- running and interpreting tests
- deploy workflow
- debugging approach

### A3. CI/CD integration

```bash
echo "=== CI/CD with Claude ==="
find .github/workflows -name "*.yml" -o -name "*.yaml" 2>/dev/null | xargs grep -l -i "claude\|anthropic" 2>/dev/null
grep -r -l -i "claude\|anthropic" Makefile scripts/ bin/ 2>/dev/null | head -5
```

### A4. Documentation quality

```bash
echo "=== README ==="
wc -l README.md 2>/dev/null
head -30 README.md 2>/dev/null
echo "=== Docs ==="
find . -name "*.md" -path "*/docs/*" -not -path "*/node_modules/*" 2>/dev/null | head -10
find . -name "ARCHITECTURE*" -o -name "DESIGN*" 2>/dev/null
```

### A5. Score the project (1–5 per dimension)

| Dimension | Evidence | Score |
|---|---|---|
| CLAUDE.md Quality | [what you found] | X/5 |
| Skills & Workflow Automation | | X/5 |
| CI/CD Integration | | X/5 |
| Documentation Quality | | X/5 |
| Settings & Configuration | | X/5 |
| **Project Overall** | | **X.X/5** |

---

## Part B — User Interaction Log Analysis

### B1. Check log retention

```bash
SCRIPT=$(find ~/.claude/plugins -name "extract_user_messages.py" 2>/dev/null | head -1)
[ -z "$SCRIPT" ] && SCRIPT="${CLAUDE_PLUGIN_ROOT:-}/scripts/extract_user_messages.py"
echo "Script location: $SCRIPT"
python3 "$SCRIPT" --check-retention
```

Note the `cleanup_period_days` value:
- null / not set → warn (default ~30 days); offer to increase
- < 60 days → suggest 90 days
- >= 90 days → note as well configured

If retention is low, offer to update it (read settings.json, add/update cleanupPeriodDays: 90, write back). Only do so if the user agrees.

### B2. Extract messages

```bash
if [ "$SCOPE" = "all" ]; then
  python3 "$SCRIPT" --all --limit 300 --max-chars 600 --output-format json
else
  python3 "$SCRIPT" --project "$(pwd)" --limit 300 --max-chars 600 --output-format json
fi
```

If fewer than 10 messages are found, note the limitation and proceed with what's available.

### B3. Analyse against AI Fluency dimensions

Read ALL extracted messages. For each dimension, find specific message evidence
(quote ~60–80 chars) before assigning a score.

**Dimension 1: Prompt Clarity & Specificity**
- Look for: specific file/function names, acceptance criteria, edge cases stated, expected output described
- Look out for: "make it work", "fix this", no success criteria, vague pronouns

**Dimension 2: Context Provision**
- Look for: error messages, stack traces, file references, environment constraints
- Look out for: bare "it broke" or "it's not working" with no diagnostic info

**Dimension 3: Goal-Setting & Autonomy Granting** ← Highest leverage dimension
- Look for: "implement X so that Y passes", "make the tests green", trusting Claude to choose approach
- Look out for: "now add this parameter", "now write this exact function", one-step-at-a-time micro-management

**Dimension 4: Iterative Efficiency**
- Look for: references to prior outputs, building progressively, session continuity
- Look out for: re-explaining the same context every session, restarts from scratch

**Dimension 5: Feedback Quality**
- Look for: pasting test output, "X works but Y fails", quoting specific errors in follow-ups
- Look out for: "that didn't work", "try again", no diagnostic info

**Dimension 6: Claude Code Feature Utilisation**
- Look for: mentions of CLAUDE.md, skills, agents, hooks, CI automation
- Look out for: treating Claude Code as a plain chatbot; no mention of persistent context

**Dimension 7: Domain Vocabulary & Precision**
- Look for: precise technical terms, API/framework names, architectural vocabulary
- Look out for: indirect descriptions, avoiding technical names

### B4. Score the user (1–5 per dimension)

| Dimension | Evidence quote | Score |
|---|---|---|
| Prompt Clarity & Specificity | "[quote]" | X/5 |
| Context Provision | "[quote]" | X/5 |
| Goal-Setting & Autonomy | "[quote]" | X/5 |
| Iterative Efficiency | "[quote]" | X/5 |
| Feedback Quality | "[quote]" | X/5 |
| Feature Utilisation | "[quote]" | X/5 |
| Domain Vocabulary | "[quote]" | X/5 |
| **User Overall** | | **X.X/5** |

**Capability profile** (based on overall user score):
- Navigator (4.5+): Gives goals + success criteria; treats Claude as peer engineer; high autonomy
- Collaborator (3.5–4.4): Generally effective; some over-specification; good feature use
- Delegator (2.5–3.4): Developing; inconsistent quality; misses autonomy opportunities
- Apprentice (1.5–2.4): Micro-managing tendencies; thin context provision
- Beginner (< 1.5): Using Claude Code like a basic chatbot

---

## Part C — Unified Report

Synthesise both analyses into one coherent report. Critically, identify where **user behaviour** and **project configuration** gaps reinforce each other (e.g. user doesn't use skills because no skills are configured; user gives thin context because there's no CLAUDE.md to carry persistent context).

Output the following report exactly:

---

# Skill Issue Report

> Complete diagnosis of your Claude Code effectiveness.

## Summary Dashboard

| Area | Score | Capability Level |
|---|---|---|
| **You** (interaction patterns) | X.X / 5 | Navigator / Collaborator / Delegator / Apprentice / Beginner |
| **Project** (configuration) | X.X / 5 | Excellent / Good / Developing / Needs Work / Critical |
| **Combined** | X.X / 5 | |

---

## User Analysis

**Profile**: [Capability level] — [1–2 sentence characterisation]

**Log coverage**: [N sessions, N messages, date range, retention period]

### Scores

| Dimension | Score |
|---|---|
| Prompt Clarity & Specificity | X/5 |
| Context Provision | X/5 |
| Goal-Setting & Autonomy | X/5 |
| Iterative Efficiency | X/5 |
| Feedback Quality | X/5 |
| Feature Utilisation | X/5 |
| Domain Vocabulary | X/5 |

### Strengths
1. **[Strength]**: [Evidence with quote]
2. **[Strength]**: [Evidence with quote]

### Growth Areas
1. **[Area]**: [Observed pattern with quote] → **Try**: [concrete alternative]
2. **[Area]**: [Observed pattern with quote] → **Try**: [concrete alternative]

---

## Project Analysis

### Scores

| Dimension | Score |
|---|---|
| CLAUDE.md Quality | X/5 |
| Skills & Workflow Automation | X/5 |
| CI/CD Integration | X/5 |
| Documentation Quality | X/5 |
| Settings & Configuration | X/5 |

### What's Working
[1–2 things configured well]

### Critical Gaps (ordered by impact)
1. **[Gap]**: [Why it matters]
2. **[Gap]**: [Why it matters]
3. **[Gap]**: [Why it matters]

---

## Combined Recommendations

*Ranked by impact — start at the top.*

### Immediate (today)

- [ ] **[Action]** — [Why: 1 sentence benefit]
- [ ] **[Action]** — [Why: 1 sentence benefit]
- [ ] **[Action]** — [Why: 1 sentence benefit]

### Short-term (this week)

- [ ] **[Action]** — [Description]
- [ ] **[Action]** — [Description]

### Strategic (ongoing)

- [ ] **[Action]** — [Description]
- [ ] **[Action]** — [Description]

---

### The Interaction–Configuration Connection

[1–2 paragraphs on how the user's behaviour patterns and the project's configuration
gaps reinforce each other — and how fixing both together creates a compounding effect.
For example: if the user doesn't use CLAUDE.md because there isn't one, fixing the
project gap directly enables a user behaviour improvement.]

---

*Run `/skill-issue:user-skill-issue` or `/skill-issue:project-skill-issue` individually
to re-run either analysis on its own.*
