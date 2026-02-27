---
name: project-skill-issue
description: Audit the current project's Claude Code configuration and setup quality. Evaluates CLAUDE.md files, .claude/ directory, CI/CD integration, documentation, and overall Claude-readiness. Safe to run on its own or as part of /skill-issue:skill-issue.
context: fork
agent: Explore
disable-model-invocation: true
allowed-tools: Read, Glob, Grep, Bash
---

# Project Skill Issue — Configuration Audit

You are performing a comprehensive audit of this project's Claude Code setup.
Your job is to evaluate how well the project is configured to let Claude work at
maximum effectiveness, then produce a prioritised improvement report.

Work through each section systematically. Collect evidence before scoring anything.

---

## Section 1: CLAUDE.md Files (Project Memory)

```bash
find . -name "CLAUDE.md" -not -path "*/node_modules/*" -not -path "*/.git/*" -not -path "*/vendor/*" 2>/dev/null
```

**For each CLAUDE.md found, read it and evaluate:**

| Quality Signal | Present? | Notes |
|---|---|---|
| Project purpose / overview | | |
| Tech stack & key dependencies | | |
| Coding conventions & style | | |
| Key commands (build/test/lint/deploy) | | |
| Architecture / non-obvious decisions | | |
| Anti-patterns / what NOT to do | | |
| Subdirectory-specific CLAUDE.md files | | |
| Length appropriate (< 500 lines) | | |
| Up to date (references match current code) | | |

**If no CLAUDE.md exists at the project root** — this is the single highest-impact gap.
Note it clearly in the report.

**Sub-directory CLAUDE.md files** are a mark of mature setup. Check key directories:
```bash
find . -mindepth 2 -maxdepth 5 -name "CLAUDE.md" -not -path "*/node_modules/*" 2>/dev/null
```

---

## Section 2: `.claude/` Directory

```bash
find .claude -type f 2>/dev/null | sort
ls -la .claude/ 2>/dev/null
```

### settings.json
```bash
cat .claude/settings.json 2>/dev/null
```

Evaluate:
- Is a preferred model configured?
- Are permissions appropriately scoped?
- Are hooks defined (PostToolUse, PreToolUse)?
- Is there a `cleanupPeriodDays` setting?

### Skills / Commands
```bash
find .claude/skills .claude/commands -type f 2>/dev/null | sort
```

For each skill/command found, read its SKILL.md or .md file and check:
- Does it have a clear description?
- Is the scope appropriate?
- Is it covering a real project workflow?

**Key workflows that SHOULD have skills in most projects:**
- `/commit` or `/changelog` — standardised commit message creation
- `/review` or `/pr-review` — code review checklist
- `/test` — run tests and interpret failures
- `/deploy` — deployment workflow
- `/debug` — structured debugging approach

### Agents
```bash
find .claude/agents -type f 2>/dev/null | sort
```

For each agent, read and evaluate:
- Is the description precise enough for Claude to know when to use it?
- Are tool restrictions appropriate?
- Is the system prompt focused and actionable?

### Hooks
Look in `.claude/settings.json` and any `hooks.json` for hook definitions.
Also check for a standalone `hooks/` directory.

---

## Section 3: CI/CD Integration

```bash
# GitHub Actions workflows mentioning Claude/Anthropic
find .github/workflows -name "*.yml" -o -name "*.yaml" 2>/dev/null | xargs grep -l -i "claude\|anthropic" 2>/dev/null

# Makefile / scripts
grep -r -l -i "claude\|anthropic" Makefile scripts/ bin/ 2>/dev/null | head -10

# Other CI systems
for f in .gitlab-ci.yml .circleci/config.yml .buildkite/pipeline.yml; do
  [ -f "$f" ] && grep -i "claude\|anthropic" "$f" && echo "Found in $f"
done
```

**Signs of mature CI/CD integration:**
- PR review workflow (Claude reviews diffs and comments)
- Security analysis on every PR
- Test quality review (Claude suggests missing test cases)
- Documentation freshness checks
- Dependency update assistance
- Changelog generation

---

## Section 4: Documentation Quality (Claude-readability)

```bash
# Root README
wc -l README.md 2>/dev/null
head -50 README.md 2>/dev/null

# Docs directory
find . -name "*.md" -path "*/docs/*" -not -path "*/node_modules/*" 2>/dev/null | head -20

# Architecture docs
find . -name "ARCHITECTURE*" -o -name "DESIGN*" -o -name "ADR*" -not -path "*/node_modules/*" 2>/dev/null
```

Evaluate:
- Is the README informative with setup instructions?
- Are there architecture or design decision records?
- Does the code have adequate inline documentation for complex logic?
- Could Claude onboard to this project in one session from these docs alone?

---

## Section 5: Development Practices

```bash
# Test infrastructure
find . -name "*.test.*" -o -name "*.spec.*" -o -name "*_test.*" -not -path "*/node_modules/*" 2>/dev/null | wc -l

# Linting / formatting config
ls -1 .eslintrc* .prettierrc* .rubocop* pyproject.toml setup.cfg .flake8 ruff.toml 2>/dev/null

# Pre-commit / git hooks
ls .husky/ .git/hooks/ 2>/dev/null
cat .pre-commit-config.yaml 2>/dev/null
```

A well-configured project makes Claude's job easier by having automated quality gates.

---

## Scoring

After completing all sections, assign scores:

### CLAUDE.md Quality (1–5)
- **5**: Root file + subdirectory files, concise, covers all quality signals above
- **4**: Root file with good coverage; some subdirectory context
- **3**: Root file exists but thin or missing key sections
- **2**: Minimal CLAUDE.md with very little useful context
- **1**: No CLAUDE.md anywhere

### Skills & Workflow Automation (1–5)
- **5**: Skills for all key workflows; specialised agents; hooks for automation
- **4**: Skills for most key workflows; some automation
- **3**: A few skills; core workflows not fully covered
- **2**: 1–2 skills; minimal automation
- **1**: No skills, commands, or agents

### CI/CD Integration (1–5)
- **5**: Claude integrated into PR review, security, testing, and/or docs pipelines
- **4**: Claude used in 2–3 pipeline stages
- **3**: Claude mentioned but not deeply integrated
- **2**: One basic Claude step in CI
- **1**: No Claude in CI/CD

### Documentation Quality (1–5)
- **5**: Excellent README, architecture docs, inline comments; full onboarding possible
- **4**: Good docs with minor gaps
- **3**: Basic README; architectural documentation sparse
- **2**: Minimal docs; code exploration required to understand the system
- **1**: No meaningful docs

### Settings & Configuration (1–5)
- **5**: Thoughtful settings with model, permissions, hooks, retention
- **4**: Good settings with minor gaps
- **3**: Default or minimal settings
- **2**: settings.json present but near-empty
- **1**: No settings.json; relying on global defaults

---

## Report Format

Output exactly this structure:

---

## Project Configuration Audit

**Project**: [basename of cwd]  |  **Audit date**: [today]

### Configuration Scores

| Dimension | Score | Status |
|---|---|---|
| CLAUDE.md Quality | X/5 | ✅ Good / ⚠️ Needs Work / ❌ Missing |
| Skills & Workflow Automation | X/5 | |
| CI/CD Integration | X/5 | |
| Documentation Quality | X/5 | |
| Settings & Configuration | X/5 | |
| **Overall** | **X.X / 5** | |

---

### What's Working Well
[2–3 specific strengths with evidence — what exists and how it helps]

---

### Critical Gaps (highest impact first)

1. **[Gap title]** — [Why this matters, estimated effort to fix: low/medium/high]
2. **[Gap title]** — [Same format]
3. **[Gap title]** — [Same format]

---

### Quick Wins (< 30 minutes each)

Concrete actions the user can take right now:

- [ ] [Specific action — e.g. "Add key commands to CLAUDE.md root file"]
- [ ] [Specific action]
- [ ] [Specific action]

---

### Strategic Improvements (higher effort, high payoff)

- **[Title]**: [What to build and why — e.g. "Add a /pr-review skill that runs Claude on every PR diff"]
- **[Title]**: [Description]

---

### Discovered Configuration Files

| File | Status | Notes |
|---|---|---|
| CLAUDE.md (root) | ✅ Present / ❌ Missing | [brief assessment] |
| .claude/settings.json | ✅ Present / ❌ Missing | |
| .claude/skills/ | X files | [list names] |
| .claude/agents/ | X files | [list names] |
| CI/CD integration | ✅ Yes / ❌ No | [which workflows] |
