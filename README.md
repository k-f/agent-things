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
