# Classification taxonomy

This is the rubric the Haiku classifier subagents apply to each session. Edit
the definitions and examples here to tune classification **without touching
code**. The Python extractor and reporter never read this file — only the
classifier subagents do.

A session is described by **one or more tags**, a short **summary**, and an
**outcome** verdict with confidence. Multi-activity sessions get multiple tags
— do **not** force a single tag.

---

## Tags

Tags are two-level. Always emit the relevant **top-level** tag(s); add
**sub-tags** when the activity is clearly one of the listed kinds. A session
can carry tags from more than one top-level area.

### Top-level

- **engineering** — writing, changing, or operating software/systems.
- **testing** — work whose primary object is tests or test outcomes.
- **business** — non-code knowledge work: planning, specs, comms, analysis of
  business problems, product/strategy, pricing, project management.
- **personal-productivity** — the user's own workflow: notes, scheduling,
  email triage, learning unrelated to a shipped deliverable, tool setup for
  themselves.

### Engineering sub-tags

- **analysis/design** — understanding a system, designing an approach,
  architecture, trade-off discussions, reading code to form a plan.
  _Example: "How does our auth flow work and where should rate-limiting go?"_
- **implementation** — building a feature or writing net-new code.
  _Example: "Add a CSV export endpoint to the reports service."_
- **debugging** — diagnosing and fixing a specific defect or failure.
  _Example: "Users get a 500 on checkout — find and fix it."_
- **refactor** — restructuring existing code without changing behaviour.
  _Example: "Extract the pricing logic into its own module."_
- **review** — reviewing a diff/PR, code review, security review.
  _Example: "Review this PR for correctness and style."_
- **devops** — CI/CD, deployment, infra-as-code, build/release, containers.
  _Example: "Fix the failing GitHub Actions deploy job."_
- **data** — data pipelines, SQL, ETL, analytics queries, schema/migrations.
  _Example: "Write a migration and backfill the new column."_
- **documentation** — READMEs, docstrings, guides, changelogs.
  _Example: "Document the new API and update the README."_
- **research** — open-ended investigation, comparing libraries/approaches,
  reading docs to decide direction (vs. designing a specific system).
  _Example: "Compare Redis vs Memcached for our session store."_

> Sub-tags may also accompany **testing** when relevant (e.g. `testing` +
> `implementation` for writing a new test suite; `testing` + `debugging` for
> chasing a flaky test).

### Tagging guidance

- Prefer 1–3 tags. Add a top-level tag whenever its area is clearly present.
- A bug fix that ships with a regression test → `engineering`, `debugging`,
  `testing`.
- A pure "explain this code" session with no edits → `engineering`,
  `analysis/design` (or `research` if it's library/tool comparison).
- A session that only writes/updates tests → `testing` (+ `implementation`).
- Drafting a project plan or Jira tickets with no code → `business`.
- Setting up the user's own dotfiles/editor → `personal-productivity`,
  `devops`.

---

## Outcome

Pick exactly one. Anchor `successful` on **hard signals** (commit created,
tests passed, PR created) supplied in `outcome_signals`. When signals are
weak or contradictory, prefer `unknown` over guessing. Cite the signals you
used in a one-line justification.

- **successful** — the request was fulfilled. Strong evidence: a commit was
  created and/or tests passed and/or a PR was created, and the final exchange
  doesn't contradict that.
  _Example: implemented a feature, `tests_passed=true`, `commit_created=true`._
- **partial** — meaningful progress but not finished, or finished with caveats
  (some tests failing, an open follow-up, ended mid-task on an error).
  _Example: feature built but `tests_passed=false`, or `last_turn_was_error`._
- **abandoned** — the work was dropped without reaching a useful state; little
  was produced and no hard signal of completion.
  _Example: a few exploratory turns, no commit, no answer delivered._
- **unknown** — genuinely cannot tell. A user who got an **answer** to a
  question and left looks like abandonment but may be a success; committed
  code may be broken. When in doubt, choose this.
  _Example: short Q&A with no commit/test signals either way._

### Outcome confidence

A float in `[0, 1]`. High (≥0.7) only when hard signals agree with the
narrative. Q&A sessions and signal-free sessions should be low (≤0.4).

---

## Identifiers

You are given `identifier_candidates` already extracted deterministically
(with confidence tiers and sources). Your job is only to **validate and
de-duplicate** them — keep the ones that plausibly refer to real work in this
session, drop obvious false positives (e.g. a Jira-shaped string that is
clearly an enum value or a UUID fragment, or a bare `#123` that is a markdown
heading rather than a PR). Do not invent identifiers that aren't in the
candidates.
