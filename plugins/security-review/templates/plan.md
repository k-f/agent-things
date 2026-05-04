# Security Review — Run Plan

> Run-id: `<RUN_ID>` · started: `<START_TS>` · project type: `<PROJECT_TYPE>` · depth: `<DEPTH>`
> Targets:
> - `<TARGET_1>` (commit `<HASH_1>`, `<LOC_1>` LOC)
> - `<TARGET_2>` (commit `<HASH_2>`, `<LOC_2>` LOC)

## Phase status table

The manager is the SOLE owner of this file. Every Task dispatch writes a row; every Task return
updates a row's status. On `resume:<run-id>`, the manager scans this table and re-dispatches any
row whose status is `pending`, `running`, or `failed`.

| Phase | Step | Agent | Assignment | Status | Started | Finished | Notes |
|---|---|---|---|---|---|---|---|
| 1 | scoping | (manager) | — | done | <ts> | <ts> | calibration written |
| 2 | recon | sr-recon | recon/<repo-1>.md | pending | | | |
| 2 | recon | sr-recon | recon/<repo-2>.md | pending | | | |
| 2 | threat-model | sr-threat-modeller | threat-model.md | pending | | | (after all recon done) |
| 3 | distribution | (manager) | assignments/*.md | pending | | | |
| 4 | hunt | sr-injection-hunter | assignments/inj-001.md | pending | | | |
| 4 | hunt | sr-authnz-hunter | assignments/authnz-001.md | pending | | | |
| ... | | | | | | | |
| 5 | verify | sr-verifier | findings/candidates/<id>.md | pending | | | (one per candidate) |
| 6 | triage | sr-triage | (all findings) | pending | | | |
| 6.5 | chain-compose | sr-chain-composer | (all confirmed findings) | pending | | | |
| 7 | report | sr-report-compiler | report.md | pending | | | |

## Status legend

- `pending` — not yet dispatched
- `running` — dispatched, awaiting return
- `done` — completed successfully
- `failed` — Task returned with error; investigate worklog before re-dispatch
- `skipped` — phase was skipped per scope (e.g. no crypto in repo → sr-crypto-hunter skipped)

## Phase transition rules

- Phase N+1 can only begin when ALL rows in phase N have status `done` or `skipped`.
- Phase 4 rows may be added incrementally during phase 3 distribution; manager must finalize the
  assignment list before dispatching the first phase-4 Task.
- Phase 5 rows are added one-per-candidate as candidates appear in `findings/candidates/`.
