# agent-things

A plugin marketplace for Claude Code. Distributes plugins that help you and your projects work more effectively with Claude Code.

## Install this marketplace

```shell
/plugin marketplace add k-f/agent-things
```

Or via git URL:

```shell
/plugin marketplace add https://github.com/k-f/agent-things.git
```

---

## Plugins

### `skill-issue` — Claude Code Effectiveness Diagnosis

Diagnose and improve your Claude Code setup and usage. This plugin provides three skills:

| Skill | What it does |
|---|---|
| `/skill-issue:skill-issue` | Full diagnosis — runs both analyses and produces a unified report |
| `/skill-issue:user-skill-issue` | Analyse your interaction logs against AI fluency criteria |
| `/skill-issue:project-skill-issue` | Audit this project's Claude Code configuration |

#### Install

```shell
/plugin install skill-issue@agent-things
```

#### Usage

Run the full diagnosis from any project:

```shell
/skill-issue:skill-issue
```

Or run individual checks:

```shell
# Just your interaction patterns
/skill-issue:user-skill-issue

# Just the project configuration
/skill-issue:project-skill-issue
```

#### What it analyses

**User Skill Issue** — reads your session logs from `~/.claude/projects/` and evaluates:

- Prompt clarity & specificity
- Context provision (do you share error messages, file references?)
- Goal-setting & autonomy granting (do you give Claude goals or micro-manage step by step?)
- Iterative efficiency (do you build on prior work or restart from scratch?)
- Feedback quality (do you give Claude diagnostic info when things go wrong?)
- Claude Code feature utilisation (skills, agents, CLAUDE.md, hooks)
- Domain vocabulary & precision

Output: a scored capability profile (Navigator → Beginner) with evidence-backed recommendations.

**Project Skill Issue** — reads the current project's configuration and evaluates:

- CLAUDE.md files (root + subdirectories)
- `.claude/` directory: settings, skills, agents, hooks
- CI/CD integration (automated PR review, security, test analysis)
- Documentation quality
- Development practices

Output: a scored audit with prioritised quick wins and strategic improvements.

#### Log retention

The user skill analysis works best with 90+ days of history. If your retention period is lower, the skill will offer to increase it automatically.

To set it manually, add to `~/.claude/settings.json`:

```json
{
  "cleanupPeriodDays": 90
}
```

#### Privacy note

All analysis runs locally on your machine. No logs or messages leave your system — the extraction script reads `~/.claude/projects/` and the analysis runs in a Claude Code subagent on your local session.

---

### `security-review` — Deep agent-team-driven security review

A heavyweight security review plugin that coordinates a 14-agent team over a 7-phase workflow to produce a vulnerability report with CVSS v3.1 scores, exploit scenarios, suggested remediations, and per-finding human verification test plans. Designed for Opus 4.7 with the 1M context window and large inference budgets — typical runs take 1-12 hours.

| Skill | What it does |
|---|---|
| `/security-review:security-review` | Full 7-phase deep review of one or more repos (parent orchestrator) |
| `/security-review:review-repo` | Single-repo review (alias for the parent with one target) |
| `/security-review:review-cross-repo` | Multi-repo review with cross-repo trust-boundary analyst enabled |
| `/security-review:review-file` | Single-file scoped review (minutes, not hours) |
| `/security-review:review-diff` | PR / `git diff`-scoped review (mirrors the public claude-code-security-review GH Action) |
| `/security-review:triage-only` | Re-run triage / chain composition / report against an existing run |
| `/security-review:status` | Pretty-print progress for a long-running review |

#### Install

```shell
/plugin install security-review@agent-things
```

#### Usage

Full deep review of the current repo:
```shell
/security-review:security-review
```

Multi-repo review (client + server, microservice mesh):
```shell
/security-review:review-cross-repo /path/to/client,/path/to/server
```

PR-scoped review:
```shell
/security-review:review-diff main HEAD
```

Resume an interrupted review:
```shell
/security-review:security-review resume:20260504-141230-a1b2c3
```

#### How it works

1. **Scoping** — asks for project type (PoC / internal / production / regulated / safety-critical) and depth budget; calibrates the severity bar.
2. **Recon** (`sr-recon` per repo) — maps attack surface, ranks every file 1-5 for vulnerability likelihood.
3. **Threat model** (`sr-threat-modeller`) — STRIDE-style; produces a hunt-priority queue.
4. **Distribution** — manager writes per-hunter assignment files dispatched top-down by priority.
5. **Deep hunts** (parallel, batches of 5) — 8 vulnerability-class hunters: injection, authn/authz, crypto, code-execution & memory safety, web (XSS/SSRF/path-traversal), supply-chain & secrets, business-logic & race, plus cross-repo trust-boundary analyst when ≥2 repos. Each hunter runs a Mythos-style hypothesize-verify loop with adversarial self-challenge before writing any candidate.
6. **Verification** (`sr-verifier`, parallel) — independent adversarial second pass per candidate; promotes confirmed findings, rejects others, refines confidence and CVSS.
7. **Triage** (`sr-triage`) — dedup, CVSS finalization, project-type calibration.
8. **Chain composition** (`sr-chain-composer`) — looks for compositions of individually Medium/Low findings that produce Critical impact.
9. **Report compilation** (`sr-report-compiler`) — produces `.security-review/<run-id>/report.md` with executive summary, methodology, findings by severity, composed chains, coverage gaps.

#### State management

All run state lives at `<repo>/.security-review/<run-id>/` as markdown files (plan, recon, threat-model, assignments, worklogs, findings, triage-summary, chains, report). The manager reads paths, not contents — agents write outputs to disk and return only short summaries — so the orchestrator's context stays small no matter how many findings are produced. `init_run.py` appends `.security-review/` to `.gitignore` (with prompt). The directory is fully resumable: kill the review at any point, run `resume:<run-id>` to continue.

#### What's deliberately out of scope

- DoS / resource exhaustion / regex DoS / generic rate limiting
- Hardcoded secrets without a usage path (config-only `.env` placeholders)
- Generic input validation without identified impact
- Open redirects without authentication context
- Reverse engineering of compiled binaries
- Dynamic analysis / fuzzing / runtime ground-truth oracles
- Execution of generated PoC exploits (verification = test plan handed to human)

#### Privacy note

All analysis runs locally. The plugin reads target source code and writes findings to disk. No findings are sent off-machine. No exploit code is executed by the tool — every finding produces a human-executable test plan instead.

---

## Contributing

Plugins live in `plugins/<plugin-name>/`. Each plugin needs:

```
plugins/my-plugin/
├── .claude-plugin/
│   └── plugin.json
└── ... (skills/, agents/, hooks/, scripts/ as needed)
```

Add an entry to `.claude-plugin/marketplace.json` to register it in this marketplace.

See [Claude Code plugin documentation](https://code.claude.com/docs/en/plugins) for full details.
