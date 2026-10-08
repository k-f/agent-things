# Project Rubric — Claude Code Configuration Audit

The single source of truth for auditing how well a project is set up for Claude Code. Used by
`/skill-issue:project-skill-issue` and by the `project-analyst` agent inside `/skill-issue:skill-issue`.

Calibrate everything to the project type. Do not apply production standards to a PoC or PoC
standards to a production system. Gather evidence first, score after. Read-only: never modify
the project.

## Inputs

- `PROJECT_DIR` — absolute path of the project; run every command from there
- `PROJECT_TYPE` — an id from `project-types.md`, either given or inferred. If you were not
  given one, infer it with the procedure in `project-types.md` (same directory as this file).

Shell notes for the snippets below: they use `grep -E` (portable alternation) and repeated
`--include=` flags. Do not write `--include="*.{js,ts}"` — quoted braces are not expanded and
match nothing. Group `find` alternatives in `\( ... \)` so `-o` does not swallow the other tests.
Exclusions are written out in full in each snippet (shell variables do not persist between
Bash calls, and unquoted variables do not word-split in zsh).

## Step 1 — CLAUDE.md files (project memory)

```bash
find . \( -name node_modules -o -name .git -o -name vendor -o -name dist -o -name .venv \) -prune -o -name CLAUDE.md -print | sort
find . \( -name node_modules -o -name .git -o -name vendor -o -name dist -o -name .venv \) -prune -o -name CLAUDE.md -print | xargs wc -l 2>/dev/null
ls CLAUDE.local.md .claude/CLAUDE.md 2>/dev/null
```

Read each file. Check the root file against:

| Signal | Notes |
|---|---|
| Purpose and business/domain context | |
| Tech stack, key dependencies, version constraints | |
| Commands: build, test (unit/integration/e2e), lint, typecheck, run locally, deploy/release | Must be copy-pasteable |
| Conventions and style | |
| Architecture decisions with rationale | |
| Protected areas / what not to change / anti-patterns | |
| Non-obvious gotchas | |
| Domain knowledge Claude would not have | |
| Autonomy boundaries (what Claude may do unattended vs needs approval) | Production+ |

Subdirectory CLAUDE.md files in key areas (packages/, services/, apps/, backend/, frontend/)
keep the root lean for monorepos; not expected for small repos.

Length: > ~600 lines is usually bloated (instructions get ignored); < ~30 lines for a
non-trivial project is usually too thin. Check that commands named in CLAUDE.md exist
(scripts in package.json, Makefile targets, files on disk) — stale commands are worse than none.

## Step 2 — `.claude/` directory, skills, agents, hooks

```bash
find .claude -type f 2>/dev/null | sort
cat .claude/settings.json 2>/dev/null || echo "(no .claude/settings.json)"
find .claude/skills .claude/commands .claude/agents -type f 2>/dev/null | sort
ls .mcp.json 2>/dev/null
```

**settings.json**: permissions scoped sensibly (no blanket `Bash(*)` on production/regulated;
not so tight Claude cannot run tests); hooks (PostToolUse formatters/linters, PreToolUse
guards, Stop checks) where they remove repetitive review.

**Skills / commands** — read each one:
- Specific description? Real, repeatable workflow in this project?
- `context: fork` for large, noisy tasks?
- Args documented (`argument-hint`)? Tools scoped?

Workflow coverage (expect more as stakes rise; `poc` needs none):
commit/PR preparation · code review checklist · running and interpreting tests ·
release/deploy · debugging a production issue · adding a new component following project
patterns · domain workflows (migration, new endpoint).

**Agents** — read each one: precise description for routing, explicit `tools`, enough
context in the prompt, appropriate model. Expect custom agents **only where a workflow
justifies them** (a recurring specialised review, a migration helper, a domain-specific
implementer). A project with no custom agents but good skills and runnable tests is not
deficient. Do not require an explorer/implementer/reviewer/tester set; do not require agent
teams (experimental, off by default).

## Step 3 — Delegation readiness

Can Claude do a unit of work unattended **and verify it itself**? This is the core of
Claude-readiness; check it concretely:

```bash
# Test, lint, typecheck entry points
grep -E '"(test|lint|typecheck|check|build)"\s*:' package.json 2>/dev/null
grep -E '^(test|lint|check|typecheck|build)[a-zA-Z_-]*:' Makefile 2>/dev/null
ls pytest.ini tox.ini noxfile.py setup.cfg pyproject.toml .pre-commit-config.yaml \
  .eslintrc* eslint.config.* .ruff.toml ruff.toml tsconfig.json 2>/dev/null
grep -n -i -E 'pytest|unittest|npm (run )?test|cargo test|go test|make test|lint|typecheck' \
  CLAUDE.md 2>/dev/null | head -10
# Test files
find . \( -name node_modules -o -name .git -o -name vendor -o -name dist -o -name .venv \) -prune -o -type f \( -name '*.test.*' -o -name '*.spec.*' \
  -o -name '*_test.*' -o -name 'test_*.py' \) -print | wc -l
```

If cheap and safe (no network, no side effects, < ~2 minutes), run the documented test
command once to confirm it works; otherwise say you did not run it. Never run deploy,
migration or write-to-external-system commands.

Readiness questions:
- Is there a test command Claude can run, documented in CLAUDE.md, that works locally?
- Are lint/format/typecheck commands documented so Claude can self-check style?
- Can the project be set up locally from the docs without tribal knowledge?
- Are protected areas and approval points written down (Production+)?
- Do custom agents/skills exist for workflows that recur and need specialised context?

## Step 4 — CI/CD integration

```bash
ls .github/workflows 2>/dev/null
grep -r -l -i -E 'claude|anthropic' .github/workflows .gitlab-ci.yml .circleci .buildkite \
  Jenkinsfile 2>/dev/null
grep -l -i -E 'claude|anthropic' Makefile 2>/dev/null
```

Read matching files. Maturity:
- **Basic** (expected `internal`+): Claude PR review or `@claude` issue/PR automation
- **Intermediate** (expected `production`/`library`+): security review, test-gap review, docs/changelog checks
- **Advanced** (expected `regulated`/`safety-critical`): compliance checks, architecture conformance, risk assessment with human sign-off

CI that runs the test suite on every PR (with or without Claude) also supports delegation:
Claude's PRs get verified.

## Step 5 — Documentation

```bash
wc -l README.md 2>/dev/null; head -50 README.md 2>/dev/null
find . \( -name node_modules -o -name .git -o -name vendor -o -name dist -o -name .venv \) -prune -o -path '*/docs/*' -name '*.md' -print | head -20
find . \( -name node_modules -o -name .git -o -name vendor -o -name dist -o -name .venv \) -prune -o -type f \( -iname 'ARCHITECTURE*' -o -iname 'DESIGN*' \
  -o -iname 'ADR*' -o -iname 'adr-*.md' -o -iname 'CONTRIBUTING*' -o -iname 'SECURITY*' \
  -o -iname 'RUNBOOK*' -o -iname 'CHANGELOG*' \) -print
```

Check: README purpose + setup; architecture/design docs; API docs and CHANGELOG + versioning
policy (`library`); runbook and SECURITY.md (`production`+); compliance docs (`regulated`+);
hazard analysis / verification docs (`safety-critical`).

## Step 6 — Production posture (skip for `poc`)

```bash
# Coverage thresholds
grep -i -E 'coverage|threshold|fail_under|--cov' pytest.ini setup.cfg pyproject.toml tox.ini \
  .nycrc .nycrc.json jest.config.* vitest.config.* 2>/dev/null | head -5
# Security tooling
ls .snyk .semgrep.yml .semgrep sonar-project.properties .trivyignore .github/dependabot.yml \
  renovate.json .gitleaks.toml 2>/dev/null
# Observability (count files)
grep -r -l -i -E 'sentry|datadog|opentelemetry|prometheus|structlog|winston|pino|logging\.getLogger' \
  --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=vendor --exclude-dir=dist --exclude-dir=.venv --include='*.js' --include='*.ts' --include='*.py' --include='*.go' \
  --include='*.rs' --include='*.java' . 2>/dev/null | wc -l
# Secrets: list committed env files and filenames containing secret-like keys (never print values)
git ls-files 2>/dev/null | grep -E '(^|/)\.env($|\.)' | grep -v -E '\.example$|\.sample$|\.template$'
grep -r -l -E '(API_KEY|SECRET|PASSWORD|TOKEN)\s*[=:]\s*["'"'"']?[A-Za-z0-9/+_-]{12,}' \
  --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=vendor --exclude-dir=dist --exclude-dir=.venv --include='*.py' --include='*.js' --include='*.ts' --include='*.go' \
  --include='*.json' --include='*.yml' --include='*.yaml' . 2>/dev/null | head -5
grep -n -E '^\.env' .gitignore 2>/dev/null
```

Report secret findings by **file name only**. Never quote a value.

For `production`/`library`+ also note: auth patterns, input validation at boundaries, rate
limiting, data retention/privacy handling (presence, not implementation review).
For `regulated`+: compliance docs, audit logging, formal review gate, DR/backup docs.
For `safety-critical`: hazard analysis, verification evidence, mandatory human sign-off.

## Step 7 — User-facing posture (skip if no UI)

```bash
grep -r -l -i -E 'aria-|role=|alt=|tabindex|a11y|wcag|axe-core' --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=vendor --exclude-dir=dist --exclude-dir=.venv \
  --include='*.html' --include='*.jsx' --include='*.tsx' --include='*.vue' \
  --include='*.svelte' . 2>/dev/null | wc -l
find . \( -name node_modules -o -name .git -o -name vendor -o -name dist -o -name .venv \) -prune -o \( -name '*.po' -o -name '*.mo' -o \( -type d -name locales \) \) -print | head -5
grep -r -l -i -E 'lighthouse|web-vitals|bundlesize|size-limit' --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=vendor --exclude-dir=dist --exclude-dir=.venv \
  --include='*.json' --include='*.yml' --include='*.yaml' --include='*.js' --include='*.ts' \
  . 2>/dev/null | head -5
```

## Step 8 — Score (calibrated)

State how calibration affects each score. `library` uses the Production column plus its
specific checks; `safety-critical` uses the Regulated column plus its specific checks.

### CLAUDE.md Quality
| Score | PoC | Internal | Production / Library | Regulated / Safety-critical |
|---|---|---|---|---|
| 5 | Root file: purpose + run/test commands | Root + commands + conventions + gotchas | All checklist items; commands verified; autonomy boundaries; subdirs where the repo warrants | Production + compliance context, restricted areas, approval points |
| 3 | Root file with purpose | Root file, basic | Root file thin on conventions/architecture or stale commands | Root file without compliance/approval context |
| 1 | None (score 2 if README covers how to run) | None | None | None |

### Skills & Workflow Automation (skills, commands, hooks, settings)
| Score | PoC | Internal | Production / Library | Regulated / Safety-critical |
|---|---|---|---|---|
| 5 | Any useful skill (not expected) | Skills for 2–3 recurring workflows | Skills for key workflows; hooks for lint/format; scoped permissions | Production + human-in-loop gates; audit hooks |
| 3 | No skills — acceptable | One basic skill or command | Some skills, key workflows missing; no hooks | Skills without approval/audit integration |
| 1 | — | No skills, no settings | No skills, no hooks | No skills, no hooks |

### CI/CD Integration
| Score | PoC | Internal | Production / Library | Regulated / Safety-critical |
|---|---|---|---|---|
| 5 | N/A | Tests in CI + Claude PR review | Tests in CI + Claude review + security/test-gap/docs checks | Production + compliance/architecture checks with human sign-off |
| 3 | N/A | Tests in CI, no Claude | Claude in CI, basic only | Claude CI without compliance checks |
| 1 | N/A | No CI | No CI tests | No CI tests |

### Documentation Quality
| Score | PoC | Internal | Production / Library | Regulated / Safety-critical |
|---|---|---|---|---|
| 5 | README with purpose and how to run | README + setup + architecture sketch | README + architecture + API docs + runbook/SECURITY (production) or API docs + CHANGELOG + semver policy (library) | Production + compliance docs, formal change log (+ hazard/verification docs for safety-critical) |
| 3 | Brief README | README with setup | Good README, thin architecture/API docs | Missing compliance docs |
| 1 | No README | No README | No README | No README |

### Delegation Readiness
*Could Claude take a scoped feature or fix, implement it, and prove it works — tests, lint,
typecheck — before a human reviews it, without getting stuck on undocumented decisions?*

| Score | PoC | Internal | Production / Library | Regulated / Safety-critical |
|---|---|---|---|---|
| 5 | Can run it and see it work | Documented, working test + lint commands; Claude can finish a PR unattended | Internal + coverage gate, CI on PRs, conventions + protected areas documented, custom agents/skills where workflows justify | Production + approval points and audit trail for agent changes |
| 3 | Runs, no tests (fine) | Tests exist but command undocumented or flaky | Tests runnable but conventions/architecture decisions undocumented, so Claude stalls or guesses | Works but compliance requirements not encoded |
| 1 | Cannot be run from the docs | No tests, no documented commands | No runnable tests | No runnable tests or no approval process |

### Production Posture (N/A for `poc`)
| Score | Meaning |
|---|---|
| 5 | Tests with coverage gate + security tooling + observability + secrets hygiene + error handling (+ a11y/perf if UI) |
| 4 | Most of the above; one or two gaps |
| 3 | Basic tests; some security; limited observability |
| 2 | Minimal tests; no security tooling; no observability |
| 1 | No tests, no security, no observability; or committed secrets |

Overall = mean of scored dimensions (exclude N/A). Level: ≥ 4.5 Excellent · 3.5–4.4 Good ·
2.5–3.4 Developing · 1.5–2.4 Needs Work · < 1.5 Critical.

## Step 9 — Report section

```markdown
## Project Analysis

**Project**: <name> (<path>) | **Type**: `<id>` (<given|inferred>) | **Audit date**: <date>

### Scores
| Dimension | Score | Status | Calibration note |
|---|---|---|---|
| CLAUDE.md Quality | x/5 | ✅/⚠️/❌ | |
| Skills & Workflow Automation | x/5 | | |
| CI/CD Integration | x/5 or N/A | | |
| Documentation Quality | x/5 | | |
| Delegation Readiness | x/5 | | |
| Production Posture | x/5 or N/A | | |
| **Overall** | **x.x/5** | <level> | |

### What's working
<2–3 specific strengths with evidence. "Has a CLAUDE.md" is not a strength; "CLAUDE.md lists
verified test/lint commands and the protected migrations directory" is.>

### Critical gaps (impact-ordered)
1. **<gap>** (Effort: Low ~30 min / Medium ~4 h / High 1 day+) — <why it matters for this type>

### Delegation readiness verdict
> Could Claude take a scoped change to a reviewed, tested PR without getting stuck?

**<Yes | Partially | No>** — <blockers, each tied to evidence>

### Unlock chain
> Fix <A> first because it enables <B> and <C>. ...

### Quick wins (< 30 min each)
- [ ] <exact change, e.g. the CLAUDE.md lines to add>

### Strategic improvements
- **<title>** — <what and why for this type>

### File inventory
| Path | Status | Assessment |
|---|---|---|
| CLAUDE.md (root) | ✅/❌ | |
| .claude/settings.json | ✅/❌ | |
| .claude/skills, .claude/commands | N | <names> |
| .claude/agents | N | <names> |
| CI with Claude | ✅/❌ | |
| Test command (verified?) | ✅/⚠️/❌ | |
| Security tooling | ✅/⚠️/❌/N/A | |
| Observability | ✅/⚠️/❌/N/A | |
| Accessibility tooling | ✅/⚠️/❌/N/A | |
```
