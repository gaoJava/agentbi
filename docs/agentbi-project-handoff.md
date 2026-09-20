# AgentBI Project Handoff

> Purpose: this is the recovery document for a new ChatGPT / Codex session. It records the current code, repository, documentation, and known runtime evidence. It is a handoff, not a new architecture-design request.
>
> Checkpoint date: 2026-09-18. Do not treat prose as stronger evidence than the current code, published runtime, or an actual formal HTTP result.

## 1. Product vision

AgentBI is not simple Text2SQL. Its intended governed path is:

```text
User Question + ConversationContext + DashboardContext (future)
  -> Semantic Understanding -> SemanticQueryIR
  -> Published Ontology / PlanningContext -> AnalysisPlanner -> QueryPlanner
  -> ExecutionPlan -> Published RuntimeBinding -> Semantic Execution Backend
  -> Observation -> Agent Loop -> EvidencePackage -> Grounded Synthesis -> FinalAnswer
```

Current product roles:

- **SuperSonic** is the semantic execution backend.
- **Superset** is the business-BI dashboard / UI host.
- **Business DB** is the final source of business data.

Superset is not merely a charting surface after an analysis is done. The product direction is to attach/embed AgentBI where a user is already viewing a Superset dashboard. For example, `Region=华东`, `Time=2025-01`, `Focused Metric=Revenue`, followed by “为什么下降了？”, becomes governed analysis using the question plus dashboard and conversation context. AgentBI Core must remain independent of Superset; Superset is a host adapter, not a Core dependency.

## 2. Architecture principles and non-negotiable boundaries

Long-term division of responsibility:

```text
Parser faithful | Ontology authoritative | Planner smart | Executor deterministic
```

LLM responsibilities: natural-language and context interpretation, analysis judgment, proposing `NextAction`, and grounded explanation. The deterministic governed core owns metric definitions, semantic validation, ontology governance, planning constraints, runtime binding, permissions/capabilities, execution, authoritative calculations, evidence, and grounding validation.

The following are prohibited:

- LLM direct business SQL generation/execution; Agent direct SuperSonic query bypass; direct PostgreSQL business-query bypass.
- AgentBI source-metric arithmetic, AgentBI `COUNT DISTINCT`, source-level `DATE_TRUNC` workarounds.
- Fake data, fake success, question-specific acceptance hardcode, or Channel fake binding.
- Planner mapping fallback, runtime guessing missing ontology, or silent legacy direct-query fallback.
- LLM-authoritative business arithmetic/ranking; bypassing Ontology, RuntimeBinding, or authorization.

## 3. Ontology governance

PostgreSQL Registry is the authoritative ontology registry:

```text
ChangeSet -> Review -> Publish -> Immutable Snapshot
```

NebulaGraph may later be a derived published graph/read model, never the source of truth. H2 is SuperSonic internal metadata, not the AgentBI ontology registry.

The current intended published snapshot is `2026.3b-governed-sales-v4`. Do not casually publish v5 unless direct evidence shows the ontology content itself is wrong.

v0.2 semantic decisions already made:

- `ANALYSIS_STRATEGY` and `CAPABILITY` are `AssetKind`s.
- Drivers are `Metric` assets linked by `DRIVEN_BY`; do not create `DRIVER` AssetKind.
- `AnalysisIntent` is runtime IR; `Observation` is runtime state.
- Generic strategy applicability is dynamic. `ANALYZED_BY` is only for approved metric-specific strategies.
- `AnalysisPlanner` sits above `QueryPlanner`; `DRIVEN_BY` is not a metric formula.
- A fixed scope dimension is excluded from default breakdown. Product, Customer, and Channel contribution are separate queries.
- Parser does not invent a baseline. Planner may use `PREVIOUS_PERIOD` / a comparison default and must preserve provenance.

## 4. Current governed semantic backend

Verified binding vocabulary in `src/agentbi/ontology_service/demo.py`:

| Item | Bound value |
| --- | --- |
| Domain | `Domain5` |
| Model | `13` / `enterprise_sales_confirmed_candidate_model` |
| View | `8` |
| Source | `confirmed_sales_order_line_enriched` |
| Connection | `supersonic-primary` |

Source fields: `order_id`, `customer_id`, `customer_name`, `customer_region`, `product_id`, `product_name`, `order_date`, `net_amount`, `order_status`. There is no Channel source field.

Metrics: Revenue = `SUM(net_amount)`; CustomerCount = `COUNT(DISTINCT customer_id)`; OrderCount = `COUNT(DISTINCT order_id)`; PurchaseFrequency = `OrderCount / CustomerCount`; AverageOrderValue = `Revenue / OrderCount`.

Bound dimensions: Region, Product, Customer, OrderDate. RuntimeBinding supports the five metrics above, those four dimensions, DAY/MONTH, and date filters. Channel is intentionally unsupported and must return `UNSUPPORTED_RUNTIME_CAPABILITY`; it must not fall back.

## 5. Verified real execution facts (regression anchors)

These are known real-backend verification anchors, not question-specific hardcode:

- Global Revenue: `107955970.00`.
- 2025-01 global Revenue: `5196770.00`; 2024-01 global Revenue: `6295965.00`.
- 华东 2025-01 root cause: current Revenue `786770.0`; baseline `924760.0`; delta `-137990.0`; delta percentage about `-14.92%`.
- Product contribution negatives: 产品080 `-54000`, 产品028 `-28800`, 产品066 `-28200`; positive offsets: 产品040 `+72000`, 产品022 `+29400`, 产品035 `+18000`.
- Customer contribution negatives: 企业客户161 `-55300`, 企业客户077 `-46500`, 企业客户021 `-36000`.
- Driver movements: CustomerCount `24 -> 24` (FLAT); PurchaseFrequency `1.0416666666666667 -> 1.0` (DOWN); AverageOrderValue `36990.4 -> 32782.083333333336` (DOWN).
- Deterministic Agent Loop stop: `FIRST_LEVEL_EVIDENCE_COMPLETE`.

## 6. Completed core capabilities (code-confirmed)

The following exist in the current working tree (many are currently untracked additions, so preserve them):

- `SemanticQueryIR` and rule-based semantic parser, including alias-span ownership and consumed-span protection.
- Ontology contracts, PostgreSQL persistence, immutable snapshots, PlanningContext, OntologyService APIs, and RuntimeBinding resolution.
- Deterministic AnalysisPlanner and `AnalysisStep -> ExecutionPlan` bridge.
- Query planner, governed `StructuredSemanticRequest`, SuperSonic structured execution, ExecutionRuntime, Observation runtime, and AnalysisState.
- Deterministic Agent Loop / NextAction, evidence-gap handling, EvidencePackage, deterministic contribution ranking, synthesis, GroundingValidator, and deterministic synthesis fallback.
- Clarification and unsupported-runtime-capability semantics.
- Circular-import/bootstrap repair represented by the explicit composition in `main.py` / `orchestrator.py`.

Existence is not a blanket product-completion claim; formal runtime validation and full acceptance remain open.

## 7. Formal product integration

Formal target path:

```text
HTTP -> Formal Orchestrator -> Semantic Parser -> SemanticQueryIR
-> Published Ontology -> AnalysisPlanner -> QueryPlanner -> ExecutionPlan
-> RuntimeBinding -> Governed ExecutionRuntime -> Observation
-> Agent Loop when appropriate -> EvidencePackage -> Synthesizer
-> GroundingValidator -> FinalAnswer
```

Code evidence: `create_app` in `src/agentbi/main.py` composes `Orchestrator` with `RuleBasedSemanticParser`, `PlanningContextBuilder`, `DeterministicAnalysisPlanner`, `AnalysisStepQueryPlanner`, and `ExecutionRuntime(StructuredSemanticRequestBuilder(RuntimeBindingResolver(...)))`. `src/agentbi/orchestrator.py` calls `_execute_governed`; missing composition fails closed, and the legacy free-form SuperSonic `query` API is not passed the user question.

```text
FORMAL_GOVERNED_PRIMARY = YES (code composition and 15-case runtime evidence)
LEGACY_DIRECT_QUERY_PRIMARY = NO (code composition)
SILENT_LEGACY_FALLBACK = NO (code composition)
FORMAL_HTTP_RUNTIME = VERIFIED (15-case Formal HTTP acceptance)
```

The historical legacy shape is `question -> SuperSonicClient.query(question)`. Compatibility code can remain, but cannot become the formal primary path or a silent fallback.

## 8. v0.2 status

```text
CORE_IMPLEMENTATION = SUBSTANTIALLY_IMPLEMENTED / CODE-CONFIRMED
FORMAL_PRODUCT_INTEGRATION = VERIFIED (Formal HTTP governed primary)
PRODUCT_ACCEPTANCE = COMPLETE (15-case matrix; see Acceptance Gap Closure audit)
V0.2_STATUS = NOT_CLOSED (closure audit gaps remain)
```

Closure still requires governed core as formal primary, no silent legacy fallback, formal smoke, build/deploy/restart, post-deploy HTTP verification, unchanged 15-case acceptance, and closure of real v0.2 correctness blockers. Core volume alone does not make v0.2 complete.

## 9. 15-case product acceptance

Original questions (do not rewrite):

1. 华东区2025年1月收入为什么下降？
2. 华北区2025年1月收入为什么变化？
3. 2025年1月哪个产品收入最高？
4. 2025年1月各区域收入是多少？
5. 2025年1月收入是多少？
6. 2025年1月购买频次是多少？
7. 2025年每月收入趋势如何？
8. 华东区2025年1月客户数是多少？
9. 华东区2025年1月客单价是多少？
10. 华东区2025年1月收入按产品拆分
11. 华东区2025年1月收入按渠道拆分
12. 华东区2025年1月收入下降最多的客户有哪些？
13. 华东区2025年1月收入下降主要受哪些因素影响？
14. 2025年1月收入比2024年1月变化多少？
15. 华东区收入怎么样？

Permitted statuses: `PASS`, `PARTIAL`, `UNSUPPORTED`, `CLARIFICATION`, `INCORRECT`. Do not manufacture PASS.

Acceptance semantic contracts:

- Case01 uses 华东 current/baseline/delta, never global comparison.
- Case03 is a Product ranking, not scalar and not root cause.
- Case05 is scalar Revenue, no automatic Product/Customer drilldown.
- Case07 is MONTH trend; Case10 is Product only.
- Case11 may correctly be `UNSUPPORTED_RUNTIME_CAPABILITY`.
- Case12 is current-vs-baseline delta ranking, not current Revenue ranking.
- Case13 may use the root-cause Agent Loop.
- Case14 is explicit comparison and cannot be overwritten by Planner default baseline.
- Case15 is `CLARIFICATION_REQUIRED` with backend execution count `0`.

Current product E2E acceptance state: **complete**. The 15-case matrix and
runtime evidence are recorded in the Acceptance Gap Closure audit below.

## 10. Historical CASE03 checkpoint

Question: **“2025年1月哪个产品收入最高？”**

Previous real failure:

```text
FIRST_FAILURE_STAGE = Semantic Parser
Symptom = parsed as ordinary QUERY / scalar Revenue
Formal HTTP result = 2025-01 global Revenue 5196770.0, not Product ranking
```

Recovery state:

```text
RECOVERED_EXISTING_CHANGES = YES
CASE03_PARSER = IMPLEMENTED
CASE03_FORMAL_HTTP = NOT_RUN
CASE03_STATUS = NEXT
```

CASE03 additions are present in these current working-tree paths:

- `src/agentbi/semantic_parser/rule_based.py` — ranking detection; ranked Product breakdown becomes `BREAKDOWN`, Revenue, explicit `2025-01`, DESC, `limit=1`; alias spans consumed by the metric are not reused as dimensions.
- `src/agentbi/analysis_planner/bridge.py` — propagates IR `order_by` and `limit` to `ExecutionPlan`.
- `src/agentbi/execution/structured.py` — maps governed metric ordering/limit into `StructuredSemanticRequest`.
- `tests/test_semantic_parser.py` — focused CASE03 parser test.
- `tests/test_governed_execution.py` — focused governed metric-ranking request test.

This checkpoint is historical. CASE03 subsequently passed Formal HTTP E2E; the
current checkpoint is `docs/agentbi-current-checkpoint.md`.

## 11. Historical recovery action (completed)

The following was the recovery action before the Formal Runtime was restored:

```text
git status
git diff
git log -5 --oneline
```

Read this file, confirm the CASE03 changes above, then locate the project’s real runtime/deployment mechanism. Check `README.md`, `docs/`, `docker-compose` / compose files, `Makefile` if present, `scripts/`, deployment configuration, `pyproject.toml` / requirements, and `tests/acceptance_round1.py` endpoint/configuration.

The real AgentBI Formal HTTP service was restored and CASE03 was rerun. Formal E2E proved:

- formal governed endpoint used and governed core primary;
- Revenue, `2025-01`, Product ranking, DESC, top1;
- Product breakdown actually executes against real backend;
- result is not global scalar `5196770.0`;
- no legacy silent fallback, fake data, or question-specific hardcode.

CASE03 and the unchanged 15-case acceptance are now complete. For any future
bounded repair, record `FIRST_FAILURE_STAGE`, `EXACT_SYMPTOM`, and
`SMALLEST_REPAIR_BOUNDARY`; do not restart an architecture review.

## 12. Test-environment constraint

Historical/current sandbox failures include `python -m pytest -> No module named pytest` and missing `pydantic`. Do not alter production dependencies, casually `pip install`, or change architecture to suit this sandbox. Prefer the project’s existing container/deployment/runtime environment. If the full suite cannot run:

```text
FULL_REGRESSION_SUITE = ENVIRONMENT_BLOCKED
```

Focused harnesses, formal HTTP, and the acceptance harness remain usable validation paths when their real environment is available.

## 13. Future roadmap (record only; do not implement now)

Keep this order:

1. Governed Analysis Core — basically complete.
2. Formal Product Integration.
3. 15-Case Product Acceptance.
4. Acceptance Gap Closure.

Only after 1–4: **v0.2 CLOSED**.

Then:

- Phase 5: Superset × AgentBI Integration: generic `DashboardContext`, `DashboardContextProvider`, `SupersetContextAdapter`; Superset remains a host adapter outside Core.
- Phase 6: LLM Intelligence Layer: (6.1) LLM semantic interpreter -> ontology validation -> IR; (6.2) governed LLM agent policy proposing `NextAction`, guarded by ontology/capability/planner/execution while retaining deterministic policy fallback; (6.3) evidence-grounded LLM synthesis validated by GroundingValidator. Calculations and ranking remain deterministic.
- Phase 7: multi-turn / deeper autonomous analysis.
- Phase 8: productization.

## 14. Acceptance Gap Closure audit (2026-09-20)

The complete Formal HTTP acceptance matrix is now recorded below. This is an
acceptance result, not a declaration that v0.2 is closed.

| Case | Result |
| --- | --- |
| CASE01 | PASS |
| CASE02 | PASS |
| CASE03 | PASS |
| CASE04 | PASS |
| CASE05 | PASS |
| CASE06 | PASS |
| CASE07 | PASS |
| CASE08 | PASS |
| CASE09 | PASS |
| CASE10 | PASS |
| CASE11 | PASS — expected `UNSUPPORTED_RUNTIME_CAPABILITY` |
| CASE12 | PASS |
| CASE13 | PASS |
| CASE14 | PASS |
| CASE15 | PASS — expected `CLARIFICATION_REQUIRED`, execution count 0 |

### First failures and bounded repairs

- CASE07 first failed at governed structured execution because a month trend had
  no deterministic ordering. `src/agentbi/execution/structured.py` adds the
  governed default `ORDER BY` ascending on the time dimension for `TREND` only.
- CASE12 first failed in the Semantic Parser: “下降最多” was treated as a
  scalar query. `src/agentbi/semantic_parser/rule_based.py` classifies the
  change/contribution phrases as `ROOT_CAUSE`; `src/agentbi/execution/agent_loop.py`
  restricts contribution actions to explicitly requested dimensions. No answer
  value is hardcoded.
- CASE14 first failed at the Execution Runtime: `COMPARISON` could not be
  executed. `src/agentbi/analysis_planner/bridge.py` lowers the current and
  baseline windows to two governed execution plans while preserving the scope;
  `src/agentbi/execution/runtime.py` accepts the explicit comparison type; and
  `src/agentbi/orchestrator.py` forms the deterministic paired observation
  answer. The parser’s explicit-month comparison recognition is in
  `src/agentbi/semantic_parser/rule_based.py`. The 2024-01 baseline remains
  user-explicit for CASE14; no legacy path was added.

The review found no CASE-specific fake data, Channel binding, direct PostgreSQL
query, question-answer hardcode, or silent legacy fallback in these repairs.
The working tree also contains older, unrelated tracked and untracked changes;
they remain uncommitted and require separate review before any release commit.

### Runtime evidence

- AgentBI image: `insightpilot-agentbi:2.0-postgres-dev`, image id
  `sha256:64596749a6f9b9beb8c3bc7255f5042909ed15b1f0b780f9f0a6e222e3b82b64`,
  created 2026-09-20 08:42 UTC. Hashes of the six CASE repair files match the
  files in the running container, ruling out a stale image for this audit.
- Formal AgentBI health: `GET http://127.0.0.1:8090/health` returned `{"status":"ok"}`.
- SuperSonic health: port 9080 actuator health returned `OK`; running image is
  `insightpilot-supersonic:0.8.6-timefix`.
- PostgreSQL and Superset containers were healthy; no container was replaced.
- Published ontology snapshot used by the acceptance run:
  `2026.3b-governed-sales-v4`.

### Regression tools and results

The production AgentBI image intentionally contains runtime dependencies only;
it has no `pytest`, Ruff, Node/npm, or test tree. A disposable Python 3.11
development container installed the already-declared `.[dev]` extra and ran:

```text
162 passed, 2 warnings
```

The 13 legacy/unit-fixture failures were migrated to governed test fakes and
deterministic explicit-time fixtures; no production fallback was restored. Ruff
was then run to completion with `0 findings`. In a Node `v22.23.2` / npm
`10.9.8` container, `npm ci`, both typechecks, and the production build passed.
`npm ci` reported 6 moderate audit vulnerabilities and an npm major-update
notice. No package or production dependency declaration was changed.

### CASE02 baseline provenance closure

CASE02’s planner stores `baseline_source=PLANNER_DEFAULT` on the `AnalysisStep`.
The bridge copies that source into `ExecutionPlan.executor_hints` for both the
current and baseline plans. `ExecutionResult.evidence.executor_hints` retains
the hint, while `Observation` retains the plan/step identity, filters,
structured governed request, and paired current/baseline observation IDs.
`EvidencePackage` retains current and baseline period filters and comparison
references. Therefore the baseline source is internally traceable even though
the public CASE02 response does not expose a dedicated provenance field.

### Closure status

```text
CLOSURE_AUDIT_STATUS = COMPLETE_WITH_OPEN_REGRESSION_GAPS
MINIMAL_FIX_REVIEW = CASE07/12/14 repairs are bounded; unrelated dirty-tree changes remain
FULL_REGRESSION = 162 passed / 2 warnings; Ruff 0 findings; frontend checks/build passed
RUNTIME_IMAGE_CURRENT = YES
CASE02_PROVENANCE_TRACEABLE = YES (internal plan -> execution evidence -> observation/evidence pair)
DOCUMENTATION_UPDATED = YES
REMAINING_BLOCKERS = npm audit reports 6 moderate vulnerabilities; npm major-update notice; unrelated dirty-tree review
V0.2_READY_FOR_CLOSURE_REVIEW = YES (subject to explicit closure review; not a v0.2 declaration)
```
- Phase 9: native infrastructure evaluation only: `SemanticBackend` (SuperSonic, future AgentBI Native) and `DashboardHost` (Superset, future AgentBI Native BI).

Do not interrupt the main line now to rewrite SuperSonic or Superset.

# HOW TO RESUME IN A NEW CHATGPT / CODEX SESSION

1. Read `docs/agentbi-project-handoff.md`.
2. Read `docs/agentbi-current-checkpoint.md` if it exists.
3. Run `git status`.
4. Run `git diff`.
5. Run `git log -5 --oneline`.
6. Trust real code/runtime evidence over stale prose.
7. Do not redo completed work.
8. Continue from **CURRENT CHECKPOINT** / **Exact next action**.
9. After reaching a new stable node, update the checkpoint/handoff.
10. Do not automatically jump to future phases.
