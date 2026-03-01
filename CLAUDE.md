# agent-things — Plugin Marketplace for Claude Code

## Project overview

This repo is a **plugin marketplace** for Claude Code. It distributes plugins that improve Claude Code effectiveness for developers and teams. The primary plugin right now is `skill-issue`, which diagnoses AI fluency and project configuration quality.

## Key commands

```bash
# Validate marketplace JSON
claude plugin validate .

# Test a plugin locally during development
claude --plugin-dir ./plugins/<plugin-name>

# Install a skill from the marketplace (after publishing)
/plugin marketplace add ./
/plugin install skill-issue@agent-things
```

## Architecture

```
.claude-plugin/marketplace.json     ← marketplace catalog (add new plugins here)
plugins/<plugin-name>/
├── .claude-plugin/plugin.json      ← plugin manifest (name, version, description)
├── skills/<skill-name>/SKILL.md    ← user/Claude-invocable skills
├── agents/<agent-name>.md          ← specialised subagent definitions
├── scripts/                        ← helper scripts called from skills/agents
└── hooks/                          ← event-driven automation (optional)
README.md                           ← marketplace-level docs
```

## Plugin development conventions

### SKILL.md frontmatter rules
- All skills that protect main context use `context: fork`
- Skills not meant for Claude auto-invocation use `disable-model-invocation: true`
- Always set `argument-hint` if the skill accepts arguments
- Set `allowed-tools` explicitly; don't inherit more than needed

### Scripts
- Python scripts must work with `python3` (no `python` alias assumed)
- Scripts should read from `Path.home() / ".claude"` not hardcoded paths
- Always handle missing files gracefully with informative stderr output
- Scripts should have a `--check-*` mode for diagnostics without side effects

### Agents
- Agents go in `agents/` at the plugin root
- Set `model: sonnet` for analysis agents; use `model: inherit` for simple forwarders
- Always set `tools` explicitly — never leave open-ended inheritance for analysis agents
- System prompts should include structured output format requirements

## Testing requirements — MANDATORY

**Every skill and plugin MUST be tested before committing. No exceptions.**

### How to test skills during development

1. **Start a sub-agent to run the skill against real data:**
   Use the Task tool with `subagent_type: general-purpose` and instruct it to:
   - Read the SKILL.md files to understand what they produce
   - Execute the analysis steps against the actual current project
   - Return the full simulated output

2. **Test the extraction script directly:**
   ```bash
   python3 plugins/<plugin>/scripts/<script>.py --help
   python3 plugins/<plugin>/scripts/<script>.py [args] 2>&1
   ```

3. **Validate the marketplace JSON:**
   ```bash
   # Check JSON syntax at minimum
   python3 -c "import json; json.load(open('.claude-plugin/marketplace.json'))"
   python3 -c "import json; json.load(open('plugins/<plugin>/.claude-plugin/plugin.json'))"
   ```

4. **Review checklist before committing:**
   - [ ] Script runs without errors (`--help` and actual execution)
   - [ ] SKILL.md frontmatter is valid YAML
   - [ ] All `context: fork` skills have actionable tasks (not just reference content)
   - [ ] Agent files have `name`, `description`, `tools`, `model` set
   - [ ] marketplace.json and plugin.json are valid JSON and consistent
   - [ ] Output quality reviewed: does the skill actually produce useful, calibrated output?

5. **After seeing sub-agent test output:**
   - Critique the output: is it too lenient? Too vague? Missing dimensions?
   - Update skills if the analysis reveals gaps in the assessment criteria
   - Commit updates before marking the task done

### What good skill output looks like

A well-functioning diagnostic skill should:
- Make **evidence-backed** claims (quotes from actual data, not assertions)
- Be **calibrated**: distinguish between "fine for a prototype" vs "production system" needs
- Give **specific, actionable** recommendations (not generic "add more tests")
- Apply **appropriate standards**: a PoC has different requirements than a regulated system
- Identify **reinforcing gaps** (how user behaviour and project config interact)

## Adding a new plugin

1. Create `plugins/<new-plugin>/` directory
2. Add `.claude-plugin/plugin.json` with name, description, version
3. Add skills in `skills/<skill-name>/SKILL.md` with proper frontmatter
4. Add agents in `agents/<agent-name>.md` if needed
5. Add entry to `.claude-plugin/marketplace.json`
6. **TEST IT** using the sub-agent method above
7. Commit with a descriptive commit message

## Repository conventions

- Branch naming: Claude works on `claude/` prefixed branches
- Commit messages: include what changed and why; end with session URL
- No secrets or credentials in any file
- Scripts must be Python 3 compatible (3.8+), no external dependencies unless documented
