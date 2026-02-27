---
name: skill-issue
description: Full Claude Code effectiveness diagnosis — analyses both your interaction patterns (user AI fluency) and your project's configuration quality, calibrated to the project type and stakes. Delivers a unified prioritised improvement report with unlock-chain ordering.
context: fork
agent: general-purpose
disable-model-invocation: true
argument-hint: [all|current]
allowed-tools: Bash, Read, Glob, Grep
---

# Skill Issue — Full Claude Code Diagnosis

You are running a comprehensive diagnosis of Claude Code effectiveness.
Two analyses run in this same forked context to protect the main conversation window:
1. **Project skill issues** — how well this project is configured for Claude Code
2. **User skill issues** — how effectively the user interacts with Claude Code

Both are calibrated to context. A PoC and a regulated production system are judged
by completely different standards. State your calibration assumptions clearly.

---

## Preliminary — Gather context and clarify scope

Ask TWO questions before starting any analysis:

> **Question 1**: For the user log analysis, should I look at:
> - `current` — just this project's logs (default)
> - `all` — logs from all projects on this machine

> **Question 2**: What type of project is this?
> - `poc` — proof of concept / experiment
> - `internal` — internal team tool
> - `production` — external users, reliability matters
> - `regulated` — compliance requirements, audit trails, significant harm possible
> - `library` — consumed by other developers, API stability matters
> - `unsure` — let me figure it out from the codebase

If the user answers `unsure` (or doesn't reply), determine project type yourself:
```bash
ls -1
cat README.md 2>/dev/null | head -40
ls Dockerfile docker-compose.yml .github/workflows/ kubernetes/ 2>/dev/null
grep -r -i "hipaa\|gdpr\|soc2\|pci\|compliance\|regulated" README.md docs/ 2>/dev/null | head -3
```

Store: SCOPE = "current"/"all" and PROJECT_TYPE = "poc"/"internal"/"production"/"regulated"/"library"

---

## Part A — Project Configuration Audit

Work through each section. Gather evidence first. Score after.

### A1. CLAUDE.md files

```bash
find . -name "CLAUDE.md" -not -path "*/node_modules/*" -not -path "*/.git/*" \
  -not -path "*/vendor/*" -not -path "*/dist/*" 2>/dev/null | sort
```

Read each CLAUDE.md found. For the root CLAUDE.md, check against this complete list:

- [ ] Project purpose and business context
- [ ] Tech stack, key dependencies, version constraints
- [ ] Key commands: build, test (unit/integration/e2e), lint, typecheck, deploy, rollback
- [ ] Coding conventions and style
- [ ] Architecture decisions with rationale
- [ ] Anti-patterns and protected areas (what Claude must not change)
- [ ] How to run locally
- [ ] Non-obvious gotchas
- [ ] Domain-specific knowledge
- [ ] Agent operating instructions (if agents are used: what can they do autonomously vs what needs human approval)

Check for subdirectory CLAUDE.md files:
```bash
find . -mindepth 2 -maxdepth 6 -name "CLAUDE.md" -not -path "*/node_modules/*" 2>/dev/null
```

**Autonomous agent readiness test for CLAUDE.md:** Could an agent team start meaningful work
in this project from the CLAUDE.md alone, without asking clarifying questions in the first 5 turns?

### A2. `.claude/` directory

```bash
find .claude -type f 2>/dev/null | sort
cat .claude/settings.json 2>/dev/null || echo "(none)"
find .claude/skills .claude/commands -type f 2>/dev/null | sort
find .claude/agents -type f 2>/dev/null | sort
```

Read each skill and agent file. Evaluate:
- Are there skills for key project workflows?
- Are agents defined for specialised tasks?
- Are hooks configured for automation?
- Is `cleanupPeriodDays` set?

**Key workflow coverage check:**
- commit / PR preparation
- code review checklist
- running and interpreting the test suite
- deployment / release
- debugging a production issue
- adding a new [component/service] following project patterns
- any domain-specific workflows

**Agent team completeness check (Production+):**
- Explorer agent (read-only, codebase understanding)
- Implementer agent (write access, domain-specific)
- Reviewer agent (validates changes against standards)
- Tester agent (runs tests, interprets failures)
- Documentation agent (keeps docs current)

### A3. CI/CD integration

```bash
find .github/workflows -name "*.yml" -o -name "*.yaml" 2>/dev/null | \
  xargs grep -l -i "claude\|anthropic" 2>/dev/null
grep -r -l -i "claude\|anthropic" .gitlab-ci.yml Makefile .circleci/ .buildkite/ 2>/dev/null
```

Read any matching workflow files. Evaluate maturity:
- **Basic** (Internal+): PR review automation
- **Intermediate** (Production+): security scan, test quality review, changelog, docs check
- **Advanced** (Regulated+): compliance check, architecture validation, risk assessment

### A4. Documentation

```bash
wc -l README.md 2>/dev/null
head -40 README.md 2>/dev/null
find . -name "*.md" -path "*/docs/*" -not -path "*/node_modules/*" 2>/dev/null | head -15
find . -name "ARCHITECTURE*" -o -name "DESIGN*" -o -name "ADR*" 2>/dev/null
find . -name "CONTRIBUTING*" -o -name "SECURITY*" -o -name "RUNBOOK*" 2>/dev/null
```

### A5. Production posture (skip for PoC)

```bash
# Test count
find . \( -name "*.test.*" -o -name "*.spec.*" -o -name "*_test.py" -o -name "test_*.py" \) \
  -not -path "*/node_modules/*" 2>/dev/null | wc -l

# Security tooling signals
ls .snyk .semgrep.yml sonar-project.properties 2>/dev/null

# Observability signals
grep -r -l "sentry\|datadog\|opentelemetry\|prometheus\|logging" \
  --include="*.{js,ts,py,go}" . 2>/dev/null | wc -l

# Secrets safety
grep -r "API_KEY\|SECRET\|PASSWORD\|TOKEN" .env* 2>/dev/null | head -3
ls .gitignore 2>/dev/null && grep -i "\.env\|secret\|credential" .gitignore
```

### A6. User-facing posture (skip if not user-facing)

```bash
# Accessibility signals
grep -r -i "aria-\|role=\|alt=\|a11y\|wcag\|axe" --include="*.{html,jsx,tsx,vue,svelte}" \
  . 2>/dev/null | wc -l

# Performance signals
grep -r -i "lighthouse\|web-vitals\|performance" --include="*.{json,yml}" . 2>/dev/null | head -5
```

---

## Part B — User Interaction Log Analysis

### B1. Check log retention

```bash
SCRIPT=$(find ~/.claude/plugins -name "extract_user_messages.py" 2>/dev/null | head -1)
[ -z "$SCRIPT" ] && SCRIPT="${CLAUDE_PLUGIN_ROOT:-}/scripts/extract_user_messages.py"
echo "Script: $SCRIPT"
python3 "$SCRIPT" --check-retention
```

If retention is null or < 60 days: offer to update it to 90 days and do so if agreed.

### B2. Extract messages (use 1200 chars — never the 600-char default)

```bash
if [ "$SCOPE" = "all" ]; then
  python3 "$SCRIPT" --all --limit 400 --max-chars 1200 --output-format json
else
  python3 "$SCRIPT" --project "$(pwd)" --limit 400 --max-chars 1200 --output-format json
fi
```

If messages are still truncated, re-run with `--max-chars 2000`.

**Sample size — be honest:**
- < 10 messages: "INSUFFICIENT DATA — provisional only"
- 10–30: "LIMITED SAMPLE — directional, not definitive"
- 31–100: "MODERATE SAMPLE — reasonable confidence"
- 100+: "GOOD SAMPLE — patterns reliable"

State sample quality prominently. Adjust score confidence accordingly.

### B3. Analyse all 8 user dimensions

For each dimension, collect evidence before scoring.
Quote actual message text to support every rating.

---

#### Dimension 1: Prompt Clarity & Specificity (1–5)
- **5**: Unambiguous acceptance criteria on every request; Claude knows exactly when it's done
- **3**: Mix of clear and vague; occasionally Claude must guess at success criteria
- **1**: Consistent underspecification; Claude must ask before every action

#### Dimension 2: Context Provision (1–5)
- **5**: All relevant context provided proactively; Claude never has to ask before starting
- **3**: Sometimes sufficient; sometimes Claude must probe for more
- **1**: No context; Claude must run discovery on every request

#### Dimension 3: Goal-Setting & Success Criteria (1–5)
The key question: can Claude know when it's done, without asking?
- **5**: Goal + explicit measurable success criteria + Claude can self-validate; compatible with 10+ turn autonomous work
- **3**: Clear goal; success is inferrable but not stated explicitly
- **1**: Step-by-step instructions; Claude cannot judge completion independently

#### Dimension 4: Autonomy Depth & Agent Utilisation (1–5)
- **5**: Agent teams used for large, complex work; agents self-validate; minimal mid-work interruption; production-ready delivery
- **4**: Subagents used; substantial autonomous sessions; trusts Claude to plan
- **3**: Autonomous single-agent sessions of moderate length
- **2**: Mostly single-turn; occasional multi-step delegation
- **1**: One instruction at a time; Claude is a keyboard

#### Dimension 5: Feedback Quality & Iteration (1–5)
- **5**: Every correction includes specific evidence (test output, error traces); Claude self-corrects
- **3**: Mix of specific and general feedback
- **1**: "Try again" with no diagnostic info; or no feedback loop at all

#### Dimension 6: Output Quality Standards (1–5)
Does the user specify the quality bar? Is it calibrated to context?
- **5**: Quality requirements explicitly stated and appropriate to context (PoC vs production); requests tests/review/validation at the right level
- **3**: Implicit quality bar; relies on Claude's judgment
- **1**: Never specifies quality; or applies the same bar to everything regardless of stakes

#### Dimension 7: Claude Code Feature Utilisation (1–5)
- **5**: CLAUDE.md maintained; project skills; custom agents; hooks; CI/CD integration; agent teams
- **3**: CLAUDE.md and/or some skills; mostly manual workflow
- **1**: Basic chatbot usage; no persistent context; no automation

#### Dimension 8: Domain Vocabulary & Technical Precision (1–5)
- **5**: Precise domain vocabulary; correct technical terminology; architectural precision
- **3**: Generally correct; occasional vagueness
- **1**: Indirect descriptions; avoids technical vocabulary

---

**Capability profile:**

| Profile | Score | What it requires |
|---|---|---|
| **Orchestrator** | 4.5+ | Evidence of agent team usage or large autonomous sessions; agents self-validate; production-quality output as default; CI/CD integrated; CLAUDE.md and skills as second nature |
| **Navigator** | 3.5–4.4 | Clear goals with success criteria; autonomous sessions; uses subagents; maintains project context; good feedback loops |
| **Collaborator** | 2.5–3.4 | Mostly goal-oriented; some micromanagement; uses basic features; inconsistent feedback |
| **Apprentice** | 1.5–2.4 | Task-by-task instructions; thin context; limited feature use |
| **Beginner** | < 1.5 | Basic chatbot usage; one-liners; no persistent context |

---

## Part C — Synthesise into Unified Report

Connect the two analyses. Identify reinforcing patterns — where user behaviour and project configuration gaps compound each other.

Common compound patterns to check:
- "User gives thin context each session" + "no CLAUDE.md" → context must be re-provided every session → fix CLAUDE.md first
- "User doesn't use skills" + "no skills configured" → user can't use what doesn't exist → fix skills first
- "User doesn't use agent teams" + "no agent definitions" → agent teams aren't configured → create agent definitions
- "User gives Claude high autonomy" + "no test suite Claude can run" → Claude can't verify its work autonomously → add tests + test-runner skill

---

## Report Format

---

# Skill Issue Report

> Comprehensive diagnosis of your Claude Code effectiveness, calibrated to project type.

## Context

**Project type**: [PoC / Internal Tool / Production / Regulated / Library]
**Standards calibrated to**: [type]
**User log sample quality**: [INSUFFICIENT / LIMITED / MODERATE / GOOD]

## Summary Dashboard

| Area | Score | Level |
|---|---|---|
| **You** (interaction patterns) | X.X / 5 | [Profile] |
| **Project** (configuration) | X.X / 5 | [Excellent / Good / Developing / Needs Work / Critical] |
| **Combined** | X.X / 5 | |

---

## User Analysis

**Profile**: [Name] — [1–2 sentence characterisation with evidence]

**Evidence base**: [N] sessions | [N] messages | [Date range] | [Retention: X days]

> [If small sample: prominent caveat about confidence levels and which scores are provisional]

### Scores

| Dimension | Score | Confidence |
|---|---|---|
| Prompt Clarity & Specificity | X/5 | High/Med/Low |
| Context Provision | X/5 | |
| Goal-Setting & Success Criteria | X/5 | |
| Autonomy Depth & Agent Utilisation | X/5 | |
| Feedback Quality & Iteration | X/5 | |
| Output Quality Standards | X/5 | |
| Feature Utilisation | X/5 | |
| Domain Vocabulary | X/5 | |

### Strengths (with evidence)
1. **[Title]**: [Evidence + message quote]
2. **[Title]**: [Evidence + message quote]

### Growth Areas (with evidence and concrete alternatives)
1. **[Area]**: [Observed pattern + quote] → **Try**: [specific rewrite or behaviour change]
2. **[Area]**: [...]

---

## Project Analysis

**Project**: [name] | Standards: [calibrated type]

### Scores

| Dimension | Score | Status | Note |
|---|---|---|---|
| CLAUDE.md Quality | X/5 | ✅/⚠️/❌ | [calibration note] |
| Skills & Workflow Automation | X/5 | | |
| CI/CD Integration | X/5 or N/A | | |
| Documentation Quality | X/5 | | |
| Agent Team Readiness | X/5 | | |
| Production Posture | X/5 or N/A | | |

### What's Working
[2–3 specific strengths with concrete evidence]

### Critical Gaps (impact-ordered, with effort estimate)
1. **[Gap]** (Effort: Low/Medium/High) — [Why it matters for this project type]
2. **[Gap]** (...)
3. **[Gap]** (...)

### Autonomous Agent Readiness Verdict
> Could a team of Claude agents deliver a tested feature PR without getting stuck?

**[Yes / Partially / No]** — [specific blockers if any]

---

## Combined Priority Recommendations

Ordered by **unlock value** — each item enables the ones below it.

### Fix First (unlocks everything else)
- [ ] **[Action]** (Effort: X) — Enables: [list of other improvements this makes possible]

### Fix Next (high leverage once first items done)
- [ ] **[Action]** (Effort: X) — [impact]
- [ ] **[Action]** (Effort: X) — [impact]

### Strategic (invest when fundamentals are solid)
- [ ] **[Action]** (Effort: X) — [impact for this project type]
- [ ] **[Action]** (Effort: X)

---

## Reinforcing Gap Analysis

[2–3 paragraphs identifying how user behaviour and project configuration gaps compound each other.
Name the specific root cause chain. For example: "Because there is no CLAUDE.md, Claude
rediscovers project context every session — this explains why user messages tend to front-load
context re-explanation, which in turn reduces the proportion of sessions that can run autonomously.
Fixing the CLAUDE.md is the single change that most directly unlocks both the project score and
the user's iterative efficiency score."]

---

*Run `/skill-issue:user-skill-issue` or `/skill-issue:project-skill-issue` individually
to re-run either analysis on its own.*
