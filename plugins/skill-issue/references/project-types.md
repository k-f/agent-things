# Project Types

The single project-type enum for every skill-issue skill and agent. Use these exact ids.

| id | Description | Quality bar |
|---|---|---|
| `poc` | Proof of concept / experiment. No users except the creator; not going to production. | Minimal: it works, someone can run it |
| `internal` | Team tool. Low external exposure; some reliability requirement. | Moderate: reliable, basic docs, basic security |
| `production` | External users. Downtime or bugs have business impact. | High: tests, error handling, monitoring, security, accessibility if UI |
| `regulated` | Finance, health, legal, government. Compliance requirements, audit trails, significant harm possible. | Very high: formal controls, compliance docs, security testing, audit logging |
| `safety-critical` | Failure causes physical harm or catastrophic loss (medical devices, vehicles, industrial control). | Exceptional: formal verification, redundancy, incident response, human sign-off on every change |
| `library` | Consumed by other developers: library, SDK, CLI, plugin. API stability and versioning matter. | High: API docs, semver, changelog, compatibility tests |

Calibration columns in the rubrics group types as follows when a table has fewer columns:
`library` uses the `production` column plus the library-specific checks; `safety-critical`
uses the `regulated` column plus the safety-critical checks.

## Inferring the type (when no type argument was given)

Gather signals, then pick the **highest-stakes type the evidence supports**. Do not upgrade
on a single weak signal (one mention of "GDPR" in a dependency's README is not `regulated`).

```bash
ls -1A
head -40 README.md 2>/dev/null
head -30 CLAUDE.md 2>/dev/null
# Packaging / publishing signals (library)
ls package.json pyproject.toml setup.py setup.cfg Cargo.toml go.mod *.gemspec \
  .claude-plugin/plugin.json .claude-plugin/marketplace.json 2>/dev/null
grep -E '"(main|exports|bin|publishConfig)"' package.json 2>/dev/null | head -5
# Deployment signals (production)
ls Dockerfile docker-compose.yml compose.yaml Procfile fly.toml vercel.json \
  serverless.yml 2>/dev/null
ls -d k8s kubernetes helm terraform .github/workflows 2>/dev/null
# Compliance / safety signals (regulated / safety-critical)
grep -r -l -i -E 'hipaa|gdpr|soc ?2|pci[- ]dss|iso ?27001|fedramp|sox|audit trail' \
  README.md CLAUDE.md docs/ 2>/dev/null | head -5
grep -r -l -i -E 'iec ?61508|iso ?26262|do-178|iec ?62304|misra|safety integrity' \
  README.md CLAUDE.md docs/ 2>/dev/null | head -5
# Maturity signals
git log --oneline 2>/dev/null | wc -l
git shortlog -sn 2>/dev/null | head -5
```

| Strongest evidence | Infer |
|---|---|
| Safety standard named in project docs (IEC 61508, ISO 26262, DO-178C, IEC 62304, MISRA) | `safety-critical` |
| Compliance regime named in project docs as applying to this project; audit logging code | `regulated` |
| Published package metadata (`exports`/`bin`, PyPI/crates metadata, plugin/marketplace manifest), public API docs, no deploy config | `library` |
| Deploy config (Dockerfile + infra/CI deploy job), auth, monitoring/error-tracking SDKs | `production` |
| CI and tests, a few contributors, no external deploy or publishing | `internal` |
| Few commits, single author, no CI, "experiment"/"prototype"/"spike" in README | `poc` |

State the result at the top of the report as:

> **Project type: `<id>` (inferred)** — <one sentence naming the deciding evidence>.
> Re-run with the type as an argument to override.

When the type was given as an argument, write `(given)` instead and skip inference.
