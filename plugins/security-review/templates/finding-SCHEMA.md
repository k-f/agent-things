# Finding Schema

Every confirmed finding lives in a single markdown file under `findings/SR-<YYYY>-<NNN>.md`.
Candidate findings (pre-verification) live under `findings/candidates/<id>.md` with the same schema
but `status: candidate` and may have lower-confidence content. Verifier-rejected findings move to
`findings/rejected/<id>.md`.

## Required frontmatter

```yaml
---
id: SR-2026-001                       # SR-<YYYY>-<NNN>; chain findings: SR-<YYYY>-<NNN>-CHAIN
title: <one-line headline, ≤80 chars>
status: confirmed                     # candidate | confirmed | rejected | duplicate-of:SR-2026-002
severity: high                        # critical | high | medium | low | info
cvss_v3_1_vector: "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N"
cvss_v3_1_score: 7.5                  # base score; must match severity band
cwe: CWE-89
owasp_top_10_2021: "A03:2021 — Injection"
confidence: 0.92                      # 0.0–1.0; only ≥0.8 reach the report by default
discovered_by: sr-injection-hunter
verified_by: sr-verifier              # null on candidates, set on confirmed
affected:
  - repo: services/api                # repo-relative or path-relative
    file: app/routes/users.py
    lines: "47-58"
    function: search_users
preconditions:
  - service is exposed to network
  - request reaches /users/search without intermediate WAF
references:
  - https://cwe.mitre.org/data/definitions/89.html
  - https://owasp.org/Top10/A03_2021-Injection/
tags: [injection, sql, unauthenticated]
chain_constituents: []                # only set on SR-...-CHAIN findings; list of constituent SR-IDs
---
```

## Severity ↔ CVSS band cross-check (validated by validate_findings.py)

| Severity | CVSS base score range |
|---|---|
| critical | 9.0–10.0 |
| high | 7.0–8.9 |
| medium | 4.0–6.9 |
| low | 0.1–3.9 |
| info | 0.0 |

`validate_findings.py --strict` fails if `severity` doesn't match the `cvss_v3_1_score` band.

## Required body sections (in this exact order)

```markdown
## Summary
One paragraph, 2–4 sentences, plain English, suitable to be quoted verbatim in the executive summary.

## Detailed description
Data flow from attacker-controlled input to dangerous sink. Quote the relevant code (≤20 lines).
Explain why existing validation/escaping is insufficient. Identify the specific sink and language /
framework features that make the issue exploitable.

## Exploit scenario
Concrete attacker walkthrough: "An [unauthenticated/authenticated/insider] attacker sends ...
which causes ... resulting in ...". Specify what data is exposed or what action is performed.
Reference the preconditions from frontmatter.

## Suggested remediation
- **Primary fix.** Code sketch (5–15 lines) showing the change in context.
- **Defense in depth.** 1–3 supporting controls (limit result size, enforce auth, project columns, etc.)
- **Anti-pattern to avoid.** Common wrong fixes that look correct but aren't (e.g. switching
  string-concat to .format() — same flaw).

## Verification test plan
Concrete steps a HUMAN or test runner can execute to confirm the vulnerability. NOT to be run by
Claude. Include:

  Setup: how to start the service / load the module / prepare test data.
  Test: the exact command, payload, or input to send.
  Expected if vulnerable: what the human will observe (response body, exit code, log line).
  Expected if not vulnerable: what they'll observe instead.
  Unit test stub: a runnable test (pytest/jest/etc.) that fails on the vulnerable code and
                  passes on the fixed code.
  Negative-control: re-run the same test after applying the suggested remediation; expectation.

## Discovery notes
Brief: hypothesis the hunter formed, verification the verifier performed, any caveats. Link to
relevant worklog entries (e.g. `worklog/sr-injection-hunter-003.md#hypothesis-7`).
```

## Chain findings (SR-...-CHAIN)

Chain findings produced by `sr-chain-composer` follow the same schema with these differences:

- `id` ends with `-CHAIN`
- `chain_constituents` lists the SR-IDs that compose the chain (must all be `confirmed`)
- `cvss_v3_1_score` reflects the **composed** impact, which may exceed any individual constituent
- The body's "Exploit scenario" section walks through the chain step by step
- "Discovery notes" must explain why the composition produces greater impact than the sum of parts
