# AgentBI Current Checkpoint

Date: 2026-09-20

The 15-case Formal HTTP acceptance is complete. CASE01–CASE10 and CASE12–CASE14
passed; CASE11 passed as the expected controlled
`UNSUPPORTED_RUNTIME_CAPABILITY`; CASE15 passed as the expected controlled
`CLARIFICATION_REQUIRED` with zero backend executions.

Current published/runtime anchors:

- Snapshot: `2026.3b-governed-sales-v4`
- Formal HTTP: `http://127.0.0.1:8090`, health `{"status":"ok"}`
- AgentBI image: `insightpilot-agentbi:2.0-postgres-dev` (current-worktree hashes verified)
- SuperSonic: `insightpilot-supersonic:0.8.6-timefix`, healthy on 9080
- PostgreSQL: healthy, original volume preserved with recovery copy

Python regression is green in a disposable dev container: `162 passed, 2
warnings`. Full Ruff is green with `0 findings`. Frontend validation is green in
Node `v22.23.2` / npm `10.9.8`: `npm ci`, `npm run typecheck`,
`npm run workbench:typecheck`, and production `npm run build` all passed.
`npm ci` reported 6 moderate audit vulnerabilities and an npm major-update
notice; dependencies were not changed.

CASE02 baseline provenance is internally traceable through
`AnalysisStep.baseline_source`, `ExecutionPlan.executor_hints`,
`ExecutionResult.evidence`, paired `Observation` IDs, and `EvidencePackage`
period filters. The public response does not expose a separate provenance field.

## HOW TO RESUME IN A NEW CHATGPT / CODEX SESSION

1. Read `docs/agentbi-project-handoff.md` and this checkpoint.
2. Run `git status`, `git diff`, and `git log -5 --oneline`.
3. Trust real code/runtime evidence over stale prose.
4. Do not redo completed CASE work or redesign the governed architecture.
5. Resolve any remaining closure review items from the existing runtime/toolchain;
   Python, Ruff, and frontend checks are now green.
6. Do not enter Superset, LLM Intelligence Layer, Native Semantic Engine, or
   declare v0.2 closed without an explicit closure review.
