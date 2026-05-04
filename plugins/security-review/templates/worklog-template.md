# Worklog — `<AGENT_NAME>` · assignment `<ASSIGNMENT_ID>`

> Run-id: `<RUN_ID>` · started: `<TS>`
>
> **Worklog hygiene rules.** Append summarized reasoning, not transcripts. Target ≤5k tokens
> per hour of work. Use the section headers below; don't stream-of-consciousness. If this file
> exceeds 200KB, the next agent reading it must `tail` it instead of loading the whole thing.

## Assignment summary
<one paragraph: scope, must-checks, references>

## Hypothesis log

### Hypothesis 1
- **Statement**: <what bug class might exist where>
- **Why suspected**: <pattern / framework / data-flow signal>
- **Verification step**: <what I read / grepped / inspected>
- **Outcome**: confirmed → candidate written / rejected (reason) / inconclusive (reason)

### Hypothesis 2
…

## Adversarial self-challenge log

For each candidate written, the three strongest reasons it might be a false positive and how each
was refuted or confirmed.

### Candidate `<id>`
- **Challenge 1**: <reason it might be FP>
  - Refutation / confirmation: <evidence>
- **Challenge 2**: …
- **Challenge 3**: …
- **Final confidence**: 0.XX

## Open questions / deferred
<things I noticed but didn't pursue this assignment; for human or future-run follow-up>

## Resume marker
<if interrupted: where to pick up; what was last fully checked>
