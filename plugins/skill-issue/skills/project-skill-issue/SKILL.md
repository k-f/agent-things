---
name: project-skill-issue
description: Audit the current project's Claude Code configuration and readiness. Evaluates CLAUDE.md files, .claude/ directory, CI/CD integration, agent team readiness, and production posture — calibrated to the project's actual type and stakes. Safe to run on its own or as part of /skill-issue:skill-issue.
context: fork
agent: Explore
disable-model-invocation: true
allowed-tools: Read, Glob, Grep, Bash
---

# Project Skill Issue — Configuration Audit

You are performing a comprehensive audit of this project's Claude Code setup.
The quality bar must be calibrated to the project type — do not apply production standards
to a PoC, and do not apply PoC standards to a production system.

**Tone**: Direct and factual. State what is present and what is absent. Do not soften findings.
An honest 3 is more useful than a flattering 4.

**Scoring calibration**: 3 = functional baseline. 4 requires clear evidence of deliberate investment.
5 is the complete picture. If uncertain between two scores, take the lower one.

Start by classifying the project. Then audit. Then score.

---

## Step 1 — Classify the project

Before auditing anything, determine what kind of project this is.

```bash
# Gather signals
ls -1
cat README.md 2>/dev/null | head -40
cat CLAUDE.md 2>/dev/null | head -30
cat package.json 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('name',''),d.get('description',''))" 2>/dev/null
cat pyproject.toml 2>/dev/null | head -20
```

Also check for:
```bash
# Production signals: docker, kubernetes, CI/CD, monitoring, auth
ls Dockerfile docker-compose.yml kubernetes/ k8s/ .github/workflows/ 2>/dev/null
# Regulated signals: HIPAA, GDPR, SOC2, PCI mentions
grep -r -i "hipaa\|gdpr\|soc2\|pci\|compliance\|audit\|regulatory" README.md docs/ 2>/dev/null | head -5
```

Classify as one of:

| Type | Description | Quality bar |
|---|---|---|
| **PoC / Experiment** | Exploring an idea; not going to production; no users except the creator | Minimal — just needs to work |
| **Internal Tool** | Used by a team; low external exposure; some reliability requirement | Moderate — reliable, some docs, basic security |
| **Production Application** | External users; reliability matters; downtime has business impact | High — tests, error handling, monitoring, security, accessibility |
| **Regulated / High-Stakes** | Financial, healthcare, legal, govt; compliance requirements; audit trails; significant harm if things go wrong | Very high — formal controls, compliance docs, security testing, audit logging |
| **Safety-Critical** | Failure causes physical harm, data loss, financial catastrophe | Exceptional — formal verification, redundancy, incident response |
| **Plugin / Library / SDK** | Consumed by other developers; API stability matters; versioning is important | High — docs, semver, migration guides, compatibility testing |

State the classification clearly at the top of your report. All scoring thresholds in this
skill adjust based on this classification.

---

## Step 2 — Audit CLAUDE.md files (Project Memory)

```bash
# Find all CLAUDE.md files
find . -name "CLAUDE.md" -not -path "*/node_modules/*" -not -path "*/.git/*" -not -path "*/vendor/*" -not -path "*/dist/*" 2>/dev/null | sort

# Read each one
```

For each CLAUDE.md found, evaluate against this checklist:

| Signal | Present? | Quality (brief note) |
|---|---|---|
| Project purpose and business context | | |
| Tech stack, key dependencies, version constraints | | |
| Key commands (build, test, lint, typecheck, deploy, rollback) | | |
| Coding conventions and style | | |
| Architecture decisions and their rationale | | |
| Anti-patterns and off-limits areas | | |
| How to run the project locally | | |
| How to run tests (unit, integration, e2e separately if applicable) | | |
| Non-obvious gotchas that would trip up Claude | | |
| Domain-specific knowledge Claude wouldn't know | | |
| What NOT to change / protected areas | | |
| Agent team operating instructions (if agents are used) | | |

**Subdirectory CLAUDE.md coverage:**
```bash
find . -mindepth 2 -maxdepth 6 -name "CLAUDE.md" -not -path "*/node_modules/*" 2>/dev/null
```

High-maturity projects have CLAUDE.md files in key subdirectories (packages/, services/, apps/,
backend/, frontend/) to give agents context-specific instruction without bloating the root file.

**Length check:** CLAUDE.md files > 600 lines are usually bloated and counter-productive.
Files < 100 lines for a non-trivial project are likely too thin.

**Autonomous agent readiness check:** Could an agent team work in this project for 10+ turns
without hitting a decision point that requires the CLAUDE.md to answer? If not, what's missing?

---

## Step 3 — Audit `.claude/` Directory

```bash
find .claude -type f 2>/dev/null | sort
cat .claude/settings.json 2>/dev/null || echo "(no .claude/settings.json)"
find .claude/skills .claude/commands -type f 2>/dev/null | sort
find .claude/agents -type f 2>/dev/null | sort
```

### settings.json evaluation
- Model preference configured?
- Permissions appropriately scoped (not overly permissive, not overly restrictive)?
- Hooks defined? (PreToolUse validators, PostToolUse linters, Stop guards)
- `cleanupPeriodDays` set to 90+?
- Environment-specific settings documented?

### Skills / Commands evaluation
Read each skill file. For each:
- Does it have a clear, specific description?
- Is it scoped to a real, repeatable workflow in this project?
- Is `context: fork` used for tasks that would pollute the main context?
- Are `allowed-tools` appropriate (no over-permissioning)?

**Coverage check — does this project have skills for:**
- [ ] Standard commit / PR preparation (commit message, changelog entry)
- [ ] Code review checklist
- [ ] Running and interpreting the test suite
- [ ] Deployment / release workflow
- [ ] Debugging approach (how to investigate a production issue in this system)
- [ ] Adding a new [component/service/module] following the project's patterns
- [ ] Domain-specific workflows (e.g., "add a new API endpoint", "create a migration")

### Agents evaluation
Read each agent file. For each:
- Is the description precise enough for Claude to route correctly?
- Are tool restrictions set explicitly?
- Does the system prompt give the agent enough context to work autonomously?
- Is the model appropriate to the task?

**Agent team readiness:**
A mature project should have agents for:
- **Exploration/Research**: Read-only agents that understand the codebase
- **Implementation**: Agents with write access for specific domains
- **Review**: Agents that verify changes against standards
- **Testing**: Agents that run tests and interpret failures
- **Documentation**: Agents that update docs to match code changes

For production/regulated projects, agents should also have:
- Clear instructions on what NOT to do without human approval
- Defined escalation points (what triggers a human review?)
- Audit logging of significant agent actions

---

## Step 4 — CI/CD Integration

```bash
find .github/workflows -name "*.yml" -o -name "*.yaml" 2>/dev/null | xargs grep -l -i "claude\|anthropic" 2>/dev/null
grep -r -l -i "claude\|anthropic" .gitlab-ci.yml Makefile .circleci/ .buildkite/ 2>/dev/null
```

Read any matching workflows. Evaluate:

**Basic integration** (expected for Internal Tool+):
- PR review automation (Claude reviews diff and comments)
- Test quality review (Claude suggests missing test cases)

**Intermediate integration** (expected for Production+):
- Security analysis on every PR
- Documentation freshness checks
- Dependency update assistance
- Changelog generation

**Advanced integration** (expected for Regulated+):
- Compliance check (does this change violate regulatory requirements?)
- Architecture validation (does this change conform to the approved design?)
- Audit trail generation
- Risk assessment on significant changes

---

## Step 5 — Documentation Quality

```bash
wc -l README.md 2>/dev/null
head -50 README.md 2>/dev/null
find . -name "*.md" -path "*/docs/*" -not -path "*/node_modules/*" 2>/dev/null | head -20
find . -name "ARCHITECTURE*" -o -name "DESIGN*" -o -name "ADR*" -o -name "adr-*.md" 2>/dev/null
find . -name "CONTRIBUTING*" -o -name "SECURITY*" 2>/dev/null
```

Evaluate:
- README: setup instructions, purpose, architecture overview?
- Architecture/design docs: key decisions documented?
- API documentation (for libraries/SDKs)?
- Runbook / incident response docs (for Production+)?
- Security policy (for Production+)?
- Contribution guide?

**Agent onboarding test:** Could an agent team read the available documentation and begin
meaningful work on a new feature within one context window, without asking clarifying questions?
If not, what would they need to know first?

---

## Step 6 — Production Posture (skip if PoC)

For Internal Tool, Production, Regulated, Safety-Critical projects:

```bash
# Testing infrastructure
find . \( -name "*.test.*" -o -name "*.spec.*" -o -name "*_test.*" -o -name "test_*.py" \) \
  -not -path "*/node_modules/*" -not -path "*/.git/*" 2>/dev/null | wc -l

# Coverage configuration
cat .nycrc .istanbul.yml jest.config.* pytest.ini setup.cfg 2>/dev/null | grep -i "coverage\|threshold" | head -5

# Security tooling
ls .snyk .semgrep.yml sonar-project.properties .trivyignore 2>/dev/null

# Observability
grep -r -l "logger\|logging\|sentry\|datadog\|opentelemetry\|prometheus" \
  --include="*.{js,ts,py,go,rs,java}" . 2>/dev/null | head -5

# Error handling patterns
grep -r "try\|catch\|except\|Result\|Either" --include="*.{js,ts,py,go,rs}" \
  -l . 2>/dev/null | wc -l
```

**For Production and above, also assess:**
- Auth and authorisation patterns (not implementation, just presence)
- Input validation at system boundaries
- Secrets management (are secrets in code? .env files committed?)
- Rate limiting / abuse prevention signals
- Data retention and privacy handling

**For Regulated / High-Stakes, additionally:**
- Compliance documentation
- Audit logging patterns
- Change management process (is there a formal review gate?)
- Disaster recovery / backup strategy docs

---

## Step 7 — User-Facing Application Posture (skip if not user-facing)

For applications with an end-user interface:

```bash
# Accessibility
grep -r -i "aria-\|role=\|alt=\|tabindex\|a11y\|wcag\|axe\|lighthouse" \
  --include="*.{html,jsx,tsx,vue,svelte}" . 2>/dev/null | wc -l

# i18n / l10n
find . -name "*.po" -o -name "*.mo" -o -name "locales" -type d 2>/dev/null | head -5

# Performance
grep -r -i "lighthouse\|web-vitals\|performance\|lazy\|bundle-size" \
  --include="*.{json,yml,yaml,js,ts}" . 2>/dev/null | head -5
```

Assess:
- Accessibility (WCAG compliance signals, aria attributes, alt text patterns)
- Performance testing (Lighthouse CI, Core Web Vitals monitoring)
- Internationalisation (if relevant to the project scope)
- Browser/device compatibility testing

---

## Step 8 — Score the project

**Calibrate all scores to the project type.**
A PoC scoring 3/5 on CI/CD integration is fine — it doesn't need a pipeline.
A Production application scoring 3/5 on CI/CD integration is a significant gap.

State explicitly how calibration affects each score.

### CLAUDE.md Quality (1–5, calibrated)

| Score | PoC | Internal Tool | Production | Regulated |
|---|---|---|---|---|
| 5 | Root file with purpose + commands | Root + key commands + conventions + gotchas | Root + subdirs + all checklist items + agent operating instructions | Root + subdirs + compliance context + formal conventions + restricted areas |
| 4 | Root file with purpose + most commands | Root + commands + conventions; minor gaps | Root + most checklist items; one subdirectory or agent instruction gap | Root + subdirs; missing compliance context or formal change controls |
| 3 | Root file with purpose only | Root file with basic content | Root file; thin on conventions or architecture | Root file only |
| 2 | Skeleton / placeholder only | Sparse root file; missing commands or conventions | Root file with purpose but little actionable content | Root file without conventions, restricted areas, or compliance context |
| 1 | None | None | None | None |

### Skills & Workflow Automation (1–5, calibrated)

| Score | PoC | Internal Tool | Production | Regulated |
|---|---|---|---|---|
| 5 | 1–2 useful skills | Skills for 3+ key workflows | Skills for all key workflows + agent team definitions + hooks | Full automation + human-in-loop skills + audit trail hooks |
| 4 | 1 useful skill | Skills for 2 key workflows | Skills for most workflows; 1–2 gaps in coverage | Skills + hooks; missing audit trail or one human-in-loop gate |
| 3 | No skills (not needed) | 1 basic skill | Skills exist but missing key workflows | Skills exist but no compliance/audit integration |
| 2 | No skills (not needed) | Awareness of skills; none configured | 1 skill; most workflows uncovered | 1–2 skills; no hooks or compliance integration |
| 1 | No skills (acceptable) | No skills | No skills | No skills or hooks |

### CI/CD Integration (1–5, calibrated)

| Score | PoC | Internal Tool | Production | Regulated |
|---|---|---|---|---|
| 5 | N/A | PR review automation | PR review + security scan + test quality + changelog | All Production + compliance check + architecture validation + risk assessment |
| 4 | N/A | PR review + one additional automation | PR review + security scan; missing changelog or test quality | Most Production requirements; missing one of: compliance, architecture, risk |
| 3 | N/A | Basic CI pipeline (not Claude-integrated) | Claude in CI but limited to one workflow | Basic Claude CI; no compliance or risk checks |
| 2 | N/A | CI exists but no automation or Claude | CI exists but Claude is not involved | CI exists; Claude in one job; no compliance |
| 1 | N/A | No CI | No CI or no Claude in CI | No Claude in CI |

*Note: For PoC projects, CI/CD is not expected. Score N/A — do not penalise its absence.*

### Documentation Quality (1–5, calibrated)

| Score | PoC | Internal Tool | Production | Regulated |
|---|---|---|---|---|
| 5 | README with purpose + setup | README + setup + basic architecture | README + architecture + API docs + runbook + SECURITY.md | All Production + compliance docs + formal change log |
| 4 | README with purpose + setup | README + setup + architecture overview | README + architecture; missing runbook or SECURITY.md | All Production docs; missing one compliance doc |
| 3 | Brief README | README with setup only | Solid README; thin on architecture or ops | README; missing compliance docs |
| 2 | Placeholder README | Minimal README; no setup | README with purpose; no architecture or ops docs | README only; no compliance docs |
| 1 | No README | No README | No README | No README |

### Agent Team Readiness (1–5, calibrated)

This dimension measures whether Claude could independently do sustained, high-quality
autonomous work in this project without constant human oversight.

*Ask*: could a team of Claude agents — an explorer, an implementer, a reviewer, and a tester —
receive a feature spec and deliver it to the point of a human code review, without getting stuck?

| Score | PoC | Internal Tool | Production | Regulated |
|---|---|---|---|---|
| 5 | N/A | Agents could do a PR end-to-end with no blockers | Agents deliver tested, documented features autonomously | Agents deliver compliant, audited, tested features with formal handoff |
| 4 | N/A | Agents could complete a PR with 1–2 minor check-ins | Agents deliver tested features; documentation or edge cases need human review | Agents deliver tested features; compliance checks need human sign-off |
| 3 | N/A | Agents could work on isolated tasks but get stuck on conventions | Agents make progress but get stuck on architecture or convention decisions | Agents work but routinely miss compliance requirements |
| 2 | N/A | Agents could read the codebase but need guidance to make changes | Agents make changes but require frequent clarification and review | Agents make changes but compliance is not managed |
| 1 | N/A | Agents need prompting at every step | Agents need prompting at every step | Agents need prompting at every step |

### Production Posture (1–5 or N/A for PoC)

| Score | What it means |
|---|---|
| 5 | Tests with coverage thresholds enforced + security tooling (SAST/SCA) + observability (logging/tracing/alerting) + secrets management + consistent error handling + accessibility + performance measurement |
| 4 | Tests + most of the above; one clear gap (e.g. no performance testing or no SAST) |
| 3 | Tests present; some security signals; minimal observability; error handling inconsistent |
| 2 | Minimal tests (< 10 files or no configuration); no security tooling; no observability |
| 1 | No tests; no security tooling; no observability; secrets may be in code |

---

## Step 9 — Produce the report

---

## Project Configuration Audit

**Project**: [name] | **Type**: [PoC / Internal Tool / Production / Regulated / Safety-Critical / Plugin-Library]
**Audit date**: [date] | **Standards calibrated to**: [type]

### Configuration Scores

| Dimension | Score | Status | Calibration note |
|---|---|---|---|
| CLAUDE.md Quality | X/5 | ✅/⚠️/❌ | [e.g., "PoC standard: root file sufficient"] |
| Skills & Workflow Automation | X/5 | | |
| CI/CD Integration | X/5 or N/A | | |
| Documentation Quality | X/5 | | |
| Agent Team Readiness | X/5 | | |
| Production Posture | X/5 or N/A | | |
| **Overall** | **X.X / 5** | | |

---

### Observations
[2–3 specific things the configuration does well — concrete evidence only. "Has a CLAUDE.md" is not an observation; "CLAUDE.md covers all 8 checklist items including non-obvious gotchas and agent operating instructions" is.]

---

### Critical Gaps (impact-ordered)

For each gap, state: what's missing, why it matters for this project type, and estimated effort.

1. **[Gap]** (Effort: Low ~30min / Medium ~4h / High ~1 day+)
   [Why it matters for this specific project type]

2. **[Gap]** (Effort: ...)
   [...]

---

### Unlock Chain

Identify the dependency order — which fix enables the most other improvements:

> Fix [A] first because it enables [B] and [C]. Then fix [B] because it directly enables
> autonomous agent work. [D] can wait until [B] is done.

---

### Quick Wins (< 30 minutes each)
- [ ] [Specific action with the exact change to make]
- [ ] [Specific action]

### Strategic Improvements (high effort, high payoff)
- **[Title]**: [What to build and exactly why it matters for this project type]

---

### Autonomous Agent Readiness Assessment

> Could a team of Claude agents — one exploring the codebase, one implementing, one reviewing,
> one testing — receive a feature specification and deliver a pull request without getting stuck?

**Verdict**: [Yes / Partially / No — with specific blockers]

**Blockers** (if any):
- [Specific gap that would cause an agent team to fail or ask for help]
- [...]

**To reach full agent team readiness**:
[3–5 specific changes, ordered by dependency]

---

### File Inventory

| Path | Status | Assessment |
|---|---|---|
| CLAUDE.md (root) | ✅ / ❌ | [brief] |
| .claude/settings.json | ✅ / ❌ | |
| .claude/skills/ | N files | [names] |
| .claude/agents/ | N files | [names] |
| .github/workflows/ Claude integration | ✅ / ❌ | |
| Test infrastructure | ✅ / ⚠️ / ❌ | |
| Security tooling | ✅ / ⚠️ / ❌ | N/A for PoC |
| Observability | ✅ / ⚠️ / ❌ | N/A for PoC |
| Accessibility tooling | ✅ / ⚠️ / ❌ | N/A for non-UI |
