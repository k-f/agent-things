# Calibration

> Run-id: `<RUN_ID>`. This file is written once at run start by the manager and never silently
> changed mid-run. If calibration must change, append a new section with timestamp and rationale.

## Inputs from user

- **Project type**: `<poc | internal | production | regulated | safety-critical | unsure>`
- **Depth budget**: `<quick | standard | deep | exhaustive>`

## Effective configuration

- Hunter parallel batch size: `<N, default 5>`
- Per-hunter context budget: `<N tokens, default 600k>`
- Per-verifier context budget: `<N tokens, default 400k>`
- Per-recon context budget: `<N tokens, default 300k>`
- Default confidence threshold for report inclusion: `<0.8>`
- Max wall-clock hours: `<advisory>`

## Severity bar (per project type)

| Type | Critical bar | High bar | Notes |
|---|---|---|---|
| poc | Practical RCE/data theft against demo audience | Practical privilege bypass | Authn issues → Medium; supply-chain → Low |
| internal | Practical RCE; theft of company data | Authn bypass; SSRF to internal | Public-only findings → Low |
| production | RCE; mass user data exposure; auth bypass | IDOR; injection w/ data exfil; SSRF | Theoretical-only → Low |
| regulated | Any breach of compliance control (PHI/PCI/audit) | Crypto weakness; insufficient audit logging | Almost nothing downgrades; Info reported |
| safety-critical | Anything reachable from untrusted input | Anything that violates a safety property | Nothing downgrades; Info escalated to Low |

The selected row is in effect for this run. `sr-triage` enforces the calibration as a final pass.

## Calibration questions sr-triage applies per finding

1. Is this finding's exploit scenario practical given this project's actual deployment context?
2. Does the project's stated risk tolerance (per CLAUDE.md / SECURITY.md if present, else this type)
   warrant treating this as Critical/High?
3. Is the CVSS base score within 1 severity level of the impact actually demonstrable in this
   codebase (not the worst-case CWE)?
4. Would a senior engineer at a peer company at this risk tier prioritise this in the next sprint,
   the next quarter, or the backlog?

Triage records calibration decisions in each finding's `## Discovery notes` section.

## Excluded vulnerability classes (non-goals)

Mirrors `claude-code-security-review` plus our own:

- DoS / resource exhaustion / regex DoS / algorithmic complexity
- Generic rate-limiting absence (only auth/security-op rate-limits in scope)
- Hardcoded secrets WITHOUT a usage path (config-only secrets in dev `.env` files don't count)
- Generic input-validation gaps without identified impact
- Open-redirect without authentication context

## Capabilities NOT used (honestly stated)

This tool reviews source code only. We deliberately do NOT:

- Execute generated PoC exploits (no sandbox; verifier writes test plans for humans)
- Reverse-engineer stripped binaries
- Run dynamic analysis / fuzzing / Address Sanitizer (no runtime ground-truth oracle)
- Modify target source (no automated remediation)
- Send findings off-machine

These are listed in `report.md §8 Coverage Gaps`.
