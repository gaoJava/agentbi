# AgentBI v0.2 静态 BI Planning 验收

日期：2026-09-15。范围仅为已实现的静态链路：`RuleBasedSemanticParser → SemanticQueryIR → PlanningContextBuilder → DeterministicAnalysisPlanner → AnalysisStepQueryPlanner`。未调用 SuperSonic、Orchestrator 或执行器。

## 运行方式与总览

实际将 `examples/ontology/enterprise-sales-demo-2026.1.json` 的 Draft 资产转换为内存中的 Published Snapshot 后运行全部场景；没有为验收补充 relation 或临时规则。

| 分类 | 数量 |
| --- | ---: |
| PASS | 5 |
| PARTIAL | 7 |
| UNSUPPORTED | 1 |
| INCORRECT | 7 |

关键事实：demo 中 Revenue 有 `DRIVEN_BY` 和四条 `BREAKDOWN_BY`，也有 `dimension.order.date`，但没有 `Revenue --HAS_DIMENSION--> 订单日期`。因此 Trend/YoY/MoM Strategy 适用性为不可用，Root Cause 不会产生 baseline step。这是 Ontology 缺口，不是执行器问题。

## Scenario Matrix

缩写：IR 中 `R=Revenue`、`C=CustomerCount`；Plan 用步骤种类；Exec 为每个 step 生成的 ExecutionPlan 数量。所有行均为实际运行摘要。

| # | Question | 实际 IR / Context | Plan / Exec | 分类 | Failure layer / 原因 |
| ---: | --- | --- | --- | --- | --- |
| 1 | 销售额是多少？ | QUERY, R resolved | LOOKUP / 1 | PASS | NONE |
| 2 | 最近30天销售额趋势怎么样？ | TREND, R, LAST_N_DAYS(30) | 无 Plan / 0 | PARTIAL | ONTOLOGY：无 HAS_DIMENSION，`NO_APPLICABLE_STRATEGY` |
| 3 | 今年销售额和去年相比怎么样？ | COMPARISON, R, YOY | 无 Plan / 0 | PARTIAL | ONTOLOGY：无时间维度 |
| 4 | 各渠道销售额是多少？ | BREAKDOWN, R, hint=Channel | BREAKDOWN(Channel) / 1 | PASS | NONE |
| 5 | 为什么最近华东地区销售额下降？ | ROOT_CAUSE, R, Region=华东, RECENT, DECLINE | CONTRIBUTION(3) + DRIVER(3) / 3,3 | PARTIAL | ONTOLOGY：缺 baseline 时间关系；Scope 正确继承 |
| 6 | 为什么最近华东地区销售额下降？按渠道分析。 | ROOT_CAUSE, R, Region=华东, Channel hint | CONTRIBUTION(Product,Customer,Channel)+DRIVER / 3,3 | INCORRECT | ANALYSIS_PLANNER：ROOT_CAUSE 忽略用户 Channel hint |
| 7 | 华东地区最近30天各产品销售额趋势怎么样？ | TREND, R, Region=华东, 30 days；Product 未保留为趋势维度 | 无 Plan / 0 | INCORRECT | PARSER/IR：无法表示 trend breakdown |
| 8 | 为什么客户数下降？ | ROOT_CAUSE, C | 无 Plan / 0 | PARTIAL | ONTOLOGY：C 无 driver/breakdown/time/mapping |
| 9 | 为什么利润下降？ | target=利润 UNRESOLVED | 无 Plan / 0 | UNSUPPORTED | GROUNDING：demo 无 Profit metric |
| 10 | 销售额为什么下降？ | ROOT_CAUSE, R, DECLINE | CONTRIBUTION(4)+DRIVER(3) / 4,3 | PARTIAL | ONTOLOGY：缺 baseline；其余结构正确 |
| 11 | 华东和华南销售额有什么区别？ | QUERY, R；多 value 未表达 | LOOKUP / 1 | INCORRECT | PARSER/IR：不支持 multi-value comparison |
| 12 | 销售额和利润最近为什么都下降？ | ROOT_CAUSE，只保留 R | CONTRIBUTION+DRIVER / 4,3 | INCORRECT | PARSER/IR：不支持 multi-target |
| 13 | 最近销售额下降最多的是哪个渠道？ | QUERY, R | LOOKUP / 1 | INCORRECT | PARSER/IR：不支持 ranking/change ranking |
| 14 | 哪个产品对销售额下降贡献最大？ | QUERY, R | LOOKUP / 1 | INCORRECT | PARSER/IR：不支持 contribution question |
| 15 | 华东地区销售额下降是因为客户数下降吗？ | QUERY，实际 target 误落到 C | LOOKUP / 0 | INCORRECT | PARSER：没有 hypothesis verification，且多指标选错 target |
| 16 | 华东销售额是多少？ | QUERY, R, Region=华东 | LOOKUP / 1 | PASS | NONE |
| 17 | 昨天销售额趋势怎么样？ | TREND, R, YESTERDAY | 无 Plan / 0 | PARTIAL | ONTOLOGY：无 HAS_DIMENSION |
| 18 | 按产品看销售额 | BREAKDOWN, R, Product hint | BREAKDOWN(Product) / 1 | PASS | NONE |
| 19 | 客户数是多少？ | QUERY, C | LOOKUP / 0 + MISSING_EXECUTION_MAPPING | PARTIAL | ONTOLOGY：C 无 execution mapping |
| 20 | 已确认订单收入是多少？ | QUERY, Revenue resolved | LOOKUP / 1 | PASS | NONE |

Failure layer distribution：`NONE=5`，`PARSER/IR=6`，`ONTOLOGY=6`，`GROUNDING=1`，`ANALYSIS_PLANNER=1`，`QUERY_PLANNER=1`。多层问题按最早导致语义错误的层计数。

## Root Cause 特别核验

实际 IR 正确保留：`ROOT_CAUSE`、Revenue、`Region=华东`、`RECENT`、`DECLINE`。Context 正确包含三个 drivers 与 Region/Product/Customer/Channel breakdown。所有 six generated executions 的 predicate 都继承 `Region=华东`。

但实际 Plan 是 `CONTRIBUTION → DRIVER`，没有 `BASELINE`；Contribution 有 Product/Customer/Channel 三个独立 ExecutionPlan，Driver 有 CustomerCount/PurchaseFrequency/AverageOrderValue 三个独立 ExecutionPlan。原因是 demo 缺少 Revenue 的已治理时间维度关系。故该场景只能评为 PARTIAL，不能作为可进入执行的完整 Root Cause Plan。

## 当前 v0.2 支持边界

已支持：单指标 lookup；单一已治理 breakdown；单 EQ 区域 scope 的继承；已发布 Metric alias grounding；有完整 relation/capability/mapping 时的 Driver 与 Contribution 静态拆分。

明确未支持或错误支持：排名、Top-N、变化排名、贡献问题、假设验证、多指标、多值比较、趋势按维度展开、Workbench context inheritance。Parser 目前也不接收 `ScreenContext`，因此“已确认收入趋势图 + 华东 + 最近30天 → 为什么下降了？”只能被视为未绑定上下文的 `为什么下降了？`，无法解析 Metric；这是真实 Gap。

## Phase 6 前 Gap 优先级

P0：

1. 修正 enterprise demo 的发布 ontology：Revenue 需 `HAS_DIMENSION → 订单日期`，并为 CustomerCount 等实际要执行的 Metric 补 execution mapping。
2. Workbench Context Inheritance：Parser/grounding 必须消费 chart metric、filters、time range；否则“AI 分析”按钮没有实际产品价值。
3. Root Cause 用户 breakdown hint 应约束 Contribution candidates，而非一律展开全部维度。

P1：Ranking/Top-N 与 contribution question；hypothesis verification；multi-value comparison。

P2：multi-target、多指标关联根因、复杂时间与多维趋势。

## 六项决策

1. Ranking：不必阻塞最小 Phase 6 execution，但应作为 P1；当前会生成明显错误 Lookup。
2. Contribution Question：应在 Phase 6 前至少避免错误分类；完整能力可 P1。
3. Hypothesis Verification：P1；当前多 Metric target 错选，不能执行。
4. Multi-target：P2，除非产品首批承诺跨指标根因。
5. Multi-value comparison：P1；企业常见区域对比。
6. Workbench Context Inheritance：**必须在 Phase 6 前解决**。

## 建议

选择 **B：存在少量 P0 Gap，应先修复再进入 Execution**。其中 ontology 时间/映射治理与 Workbench context inheritance 是不可绕过的；否则执行器即使成功返回数据，也会执行不完整或缺少上下文的 BI 分析。

## Phase 5.8 P0 Closure — After

Final Gate 实际重新运行了原有全部 20 个问题；没有删除、替换或降低任何场景。

| 分类 | Before | After |
| --- | ---: | ---: |
| PASS | 5 | 12 |
| PARTIAL | 7 | 1 |
| UNSUPPORTED | 1 | 1 |
| INCORRECT | 7 | 6 |

| # | After 实际 IR / Plan / Execution 摘要 | 分类 |
| ---: | --- | --- |
| 1 | QUERY Revenue → LOOKUP / 1 | PASS |
| 2 | TREND Revenue → TREND / 1 | PASS |
| 3 | COMPARISON Revenue, YOY → COMPARISON / 1 | PASS |
| 4 | BREAKDOWN Revenue, Channel → BREAKDOWN / 1 | PASS |
| 5 | ROOT_CAUSE Revenue, 华东、最近 → BASELINE + CONTRIBUTION(Product/Customer/Channel) + DRIVER / 1,3,3 | PASS |
| 6 | ROOT_CAUSE Revenue, 华东、最近、Channel hint → BASELINE + CONTRIBUTION(Channel) + DRIVER / 1,1,3 | PASS |
| 7 | TREND Revenue, 华东、30 天、Product → TREND / 1，未表达 Product trend breakdown | INCORRECT |
| 8 | ROOT_CAUSE CustomerCount → BASELINE / 1，缺受治理 breakdown/driver | PARTIAL |
| 9 | ROOT_CAUSE Profit → UNRESOLVED_TARGET | UNSUPPORTED |
| 10 | ROOT_CAUSE Revenue → BASELINE + CONTRIBUTION + DRIVER / 1,4,3 | PASS |
| 11 | 华东/华南比较 → QUERY LOOKUP / 1，未表达多值比较 | INCORRECT |
| 12 | 销售额与利润 → 仅 Revenue Root Cause，未表达多目标 | INCORRECT |
| 13 | 渠道下降最多 → QUERY LOOKUP / 1，未表达排名 | INCORRECT |
| 14 | 产品贡献最大 → QUERY LOOKUP / 1，未表达贡献问题 | INCORRECT |
| 15 | 客户数假设验证 → CustomerCount LOOKUP / 1，未表达假设验证 | INCORRECT |
| 16 | QUERY Revenue, 华东 → LOOKUP / 1 | PASS |
| 17 | TREND Revenue, 昨天 → TREND / 1 | PASS |
| 18 | BREAKDOWN Revenue, Product → BREAKDOWN / 1 | PASS |
| 19 | QUERY CustomerCount → LOOKUP / 1 | PASS |
| 20 | QUERY 已确认订单收入 → LOOKUP / 1 | PASS |

### P0 Gate

| Gate | 实际结果 | 状态 |
| --- | --- | --- |
| A. 最近 30 天销售额趋势 | `TREND / 1`，使用已治理订单日期 | PASS |
| B. 今年与去年销售额比较 | `COMPARISON(YOY) / 1`，未被替换为上一周期 | PASS |
| C. 华东近期销售额下降 | `BASELINE + CONTRIBUTION + DRIVER / 1,3,3`；所有 execution 保留华东 Scope、时间与 `PLANNER_DEFAULT` baseline | PASS |
| D. 华东近期销售额下降，按渠道 | Contribution 仅为 Channel，`/ 1,1,3` | PASS |
| E. Workbench Context：已确认订单收入、华东、最近 30 天；“为什么下降了？” | Target/Scope/Time 均为 `WORKBENCH_CONTEXT`，计划为 `BASELINE + CONTRIBUTION + DRIVER / 1,3,3` | PASS |
| Override：上述 Context；“华南销售额为什么下降？” | Revenue、华南为 `USER_EXPLICIT`，最近 30 天仍为 `WORKBENCH_CONTEXT`；计划 `/ 1,3,3` | PASS |
| Override：Revenue、最近 30 天 Context；“今年客户数为什么下降？” | CustomerCount 与“今年”均为 `USER_EXPLICIT`，没有继承 Revenue 或最近 30 天 | PASS |

P0 修复仍为：Revenue、CustomerCount、PurchaseFrequency、AverageOrderValue 显式声明 `HAS_DIMENSION → dimension.order.date`；三个 driver 显式 `IMPLEMENTS → datamodel.sales.order_line_enriched`；Parser 使用 `SemanticParseContext(metric, filters, time_range)`。优先级为用户文本 > Workbench Context > Planner Default，Target、Scope 与 TimeScope 都保留来源标记。

### Final Gate 测试

完整套件按两个无排除分段运行：`tests/test_workbench_api.py` 为 **25 passed**；其余全部测试文件为 **112 passed**。合计 **137 passed, 0 failed, 0 skipped**。仅有 FastAPI/Starlette TestClient 的 2 条弃用 warning；没有 Phase 1–5.8 回归。

### 保留的 P1/P2 边界

Ranking/Top-N、Contribution Question、Hypothesis Verification、Multi-target、Multi-value comparison，以及 Trend 的显式多维度展开尚未实现。这些是 After 矩阵中的允许保留问题，不阻断静态核心链路进入 Execution。
