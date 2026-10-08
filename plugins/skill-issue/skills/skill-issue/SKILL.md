---
name: skill-issue
description: Full Claude Code effectiveness diagnosis — scores your interaction habits from session logs and your project's configuration in parallel, calibrated to the project type, then delivers one report with reinforcing gaps and unlock-ordered recommendations. Read-only.
context: fork
agent: general-purpose
disable-model-invocation: true
argument-hint: "[current|all] [poc|internal|production|regulated|safety-critical|library]"
allowed-tools: Bash, Read, Glob, Grep, Agent, Task
disallowed-tools: Write, Edit, NotebookEdit
---

# Skill Issue — Full Claude Code Diagnosis

You orchestrate two read-only analyses and synthesise them. You cannot ask the user anything
during this run: parse the arguments, state assumptions, and produce one complete report.

## 1. Parse arguments

Arguments: `$ARGUMENTS`

Split on whitespace, lowercase each token, in any order:
- `current` or `all` → `SCOPE`. Missing → `current`.
- `poc`, `internal`, `production`, `regulated`, `safety-critical`, `library` → `PROJECT_TYPE` (given).
- Anything else → ignore, and list it in the report header.

## 2. Gather shared context (once)

Set `PROJECT_DIR` = output of `pwd`, and `ROOT` = `${CLAUDE_PLUGIN_ROOT}`. Confirm the
plugin files exist:

```bash
ls "${CLAUDE_PLUGIN_ROOT}/references/user-rubric.md" \
   "${CLAUDE_PLUGIN_ROOT}/references/project-rubric.md" \
   "${CLAUDE_PLUGIN_ROOT}/references/project-types.md" \
   "${CLAUDE_PLUGIN_ROOT}/references/combined-report.md" \
   "${CLAUDE_PLUGIN_ROOT}/scripts/extract_user_messages.py"
```

If `PROJECT_TYPE` was not given, infer it now following
`${CLAUDE_PLUGIN_ROOT}/references/project-types.md`. Record the type, `given`/`inferred`,
and the one-sentence reason. Both agents use this one result.

Build `PROJECT_CONTEXT` (3–5 lines) so the log analyst judges the user's prompts relative to
what Claude already knows:

```bash
wc -l CLAUDE.md .claude/CLAUDE.md CLAUDE.local.md ~/.claude/CLAUDE.md 2>/dev/null
grep -E '^#{1,3} ' CLAUDE.md 2>/dev/null | head -25
ls .claude/skills .claude/commands .claude/agents 2>/dev/null
grep -c '"hooks"' .claude/settings.json 2>/dev/null
```

Summarise: does a root CLAUDE.md exist, how long, which topics its headings cover
(commands? conventions? architecture?), which project skills/agents exist, hooks yes/no,
user-level CLAUDE.md yes/no.

## 3. Dispatch both analysts in parallel

Send **one message containing two Agent tool calls** so they run concurrently. Do not set
`run_in_background`: you need both results before synthesising.

Plugin agents are namespaced by plugin name. Use `subagent_type: "skill-issue:log-analyst"`
and `subagent_type: "skill-issue:project-analyst"`. If a call is rejected because the agent
type is unknown, retry that call with the bare name (`log-analyst` / `project-analyst`). If
that also fails, use `subagent_type: "general-purpose"` and start the prompt with
"Read <ROOT>/agents/<name>.md and act as that agent, following its rules and output contract."

Substitute real values for every `<...>` below. Paths must be absolute.

**log-analyst prompt:**
```
RUBRIC: <ROOT>/references/user-rubric.md
SCRIPT: <ROOT>/scripts/extract_user_messages.py
SCOPE: <current|all>
PROJECT_DIR: <PROJECT_DIR>
PROJECT_TYPE: <id> (<given|inferred>: <reason>)
PROJECT_CONTEXT:
<3–5 line summary from step 2>

Read RUBRIC and follow it. Return the User Analysis section and the skill-issue-user JSON block.
```

**project-analyst prompt:**
```
RUBRIC: <ROOT>/references/project-rubric.md
PROJECT_TYPES: <ROOT>/references/project-types.md
PROJECT_DIR: <PROJECT_DIR>
PROJECT_TYPE: <id> (<given|inferred>: <reason>)

Read RUBRIC and follow it. Return the Project Analysis section and the skill-issue-project JSON block.
```

If an agent returns an error or no JSON block, continue with the half you have and say
plainly in the report which half is missing and why. Do not run that analysis yourself.

## 4. Synthesise

Read `${CLAUDE_PLUGIN_ROOT}/references/combined-report.md` and produce the report from its
template using both agents' sections and JSON blocks. Do not re-score; connect.
Identify reinforcing gaps with evidence from both sides and order recommendations by unlock
value.

Never edit `~/.claude/settings.json`. Retention is a recommendation with the snippet.

Your final message is the report and nothing else.
