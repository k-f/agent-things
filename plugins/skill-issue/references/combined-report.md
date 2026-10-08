# Combined Report — Synthesis Template

Used by `/skill-issue:skill-issue` after the `log-analyst` and `project-analyst` agents return.
Do not re-score anything: take each agent's scores as given, and connect them.

## Reinforcing gaps

Look for places where a user habit and a project gap compound. Name the causal chain and
the single fix that breaks it. Check at least these patterns against the evidence:

| User signal | Project signal | Chain | Fix first |
|---|---|---|---|
| Low D2 with project background re-pasted each session | CLAUDE.md missing or thin | Context re-provided every session; less time for autonomous work | CLAUDE.md |
| Low D7: no skills/workflow commands in `summary` | No `.claude/skills` for recurring workflows | User can't use what doesn't exist | Skills for the 2–3 most repeated requests (mine the messages for them) |
| High D4 (long runs) but few `sessions_with_test_runs` | Test command missing/undocumented | Claude works unattended but can't verify; user catches bugs late | Documented, runnable test command in CLAUDE.md |
| High interrupt rate (D5) | No conventions/protected areas in CLAUDE.md | Claude guesses, user interrupts to redirect | Write the conventions the interrupts keep enforcing |
| Low D3 (step-by-step) | Delegation Readiness ≥ 4 | Project is ready for delegation; user isn't using it | Behaviour change: goal + success criteria prompts |
| High D3/D4 | Delegation Readiness ≤ 2 | User delegates into a project that can't verify work | Tests + lint commands, then CI on PRs |
| D6 quality bar mismatched to type | CLAUDE.md silent on quality bar | Bar renegotiated per request | Encode the bar for this project type in CLAUDE.md |

Only report patterns the evidence supports. Quote the metric or message from the user side
and the file/finding from the project side.

## Unlock ordering

Order recommendations so each enables the ones after it. Typical order (adjust to evidence):
persistent context (CLAUDE.md) → self-verification (test/lint commands) → workflow skills →
hooks/CI → delegation habits (subagents, longer goal-driven runs). Behaviour changes that need
no setup can go in "Fix first" if they are the biggest lever.

## Template

```markdown
# Skill Issue Report

> **Project type: `<id>` (<given|inferred>)** — <one-sentence reason>.
> Scope: `<current|all>` · Sample: <band> (<N> messages, <N> sessions) · Retention: <N> days<, default>

## Summary
| Area | Score | Level |
|---|---|---|
| **You** (interaction patterns) | x.x/5 | <profile> |
| **Project** (configuration) | x.x/5 | <Excellent/Good/Developing/Needs Work/Critical> |

<2–3 sentences: the headline finding and the single highest-leverage change.>

---

<User Analysis section from log-analyst, verbatim, minus its retention subsection>

---

<Project Analysis section from project-analyst, verbatim>

---

## Reinforcing gaps
<1–3 short paragraphs, each: user evidence + project evidence → chain → fix>

## Combined recommendations (unlock order)

### Fix first (unlocks the rest)
- [ ] **<action>** (Effort: <L/M/H>) — Enables: <items below it>

### Fix next
- [ ] **<action>** (Effort: <L/M/H>) — <impact>

### Strategic
- [ ] **<action>** (Effort: <L/M/H>) — <impact for this project type>

## Log retention
<status; if recommend_change, the settings.json snippet — this skill never edits settings>

---
*Re-run either half with `/skill-issue:user-skill-issue [current|all]` or
`/skill-issue:project-skill-issue [project-type]`.*
```
