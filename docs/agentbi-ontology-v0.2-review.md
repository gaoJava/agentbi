# AgentBI Ontology v0.2 架构审查与最小演进方案

> 状态：设计评审稿（未实施）
> 日期：2026-09-15
> 范围：基于当前 `ontology_service`、语义层、规划器与编排器的真实代码；本文不引入 NebulaGraph 实现，也不要求重构现有 v0.1。

## 结论

当前 v0.1 的 Ontology 已经是一个可靠的、可治理的企业语义注册中心，但尚未进入 AgentBI 的运行时推理链路。因此它目前主要回答“有什么”，不能稳定回答“为什么变化、该如何分析、下一步调用什么能力”。

v0.2 的目标不是另起一套知识图谱，而是在现有 PostgreSQL 权威库、版本/变更集/审核/发布/快照机制之上，以极少的新概念，把 Ontology 变成 Planner 可查询的 **Business World Model**。

最小建议是：保留所有 v0.1 核心模型；新增 `ANALYSIS_STRATEGY` 和 `CAPABILITY` 两种资产类型；以现有 `METRIC` 资产加 `DRIVEN_BY` 关系表达驱动因素；把分析意图和范围放入 `SemanticQueryIR`，而不是创建 `BusinessQuestion` 资产。先在 PostgreSQL 内完成受约束查询，后续再将已发布的、对规划真正有价值的关系投影至 NebulaGraph。

## 审查范围与代码事实

已审查：

- `src/agentbi/ontology_service/contracts.py`、`postgres.py`
- `src/agentbi/semantic_query_ir.py`、`semantic_parser/contracts.py`、`query_planner/contracts.py`
- `src/agentbi/orchestrator.py`、`semantic_draft.py`、`semantic_llm.py`
- `src/agentbi/database.py`、`models.py`、`main.py` 中 datasource/dataset/metric/dimension/semantic-model 相关部分
- `docs/agentbi-ontology-design.md`、`docs/ontology-v0.1-sales-domain.md`
- `examples/ontology/`、`examples/semantic/` 与 `tests/test_ontology_architecture.py`、`tests/test_semantic_draft.py`

当前关键事实：

1. `OntologyAsset` 已有稳定 ID、种类、名称、版本、状态、别名和描述；`OntologyRelation` 有源/目标/版本及自由文本 relation。
2. PostgreSQL repository 已支持版本、ChangeSet、审核、发布和不可变 Snapshot，是正确的权威事实源。
3. 当前资产种类为 `ENTITY`、`METRIC`、`DIMENSION`、`DATA_MODEL`、`SEMANTIC`、`RULE`。
4. `SemanticQueryIR` 只表示单次查询的指标、维度、谓词、排序和限制；未表示分析目标、意图或范围。
5. `semantic_parser` 与 `query_planner` 当前是迁移期的 `Noop` 合约；实际运行路径仍由 `Orchestrator` 直接请求 SuperSonic。
6. Ontology 尚未被注入现有 SuperSonic 查询路径。`semantic_draft` 主要根据数据元信息生成 SuperSonic 草稿；并未建立 Ontology 映射。
7. `models.AnalysisPlan` 的字段仍是游戏销售域遗留模型，不能作为企业分析规划的通用核心。

## v0.1 应保留的部分

- **PostgreSQL 权威库**：事务、审批、审计、版本和快照比图数据库更适合承担事实源职责。
- **ChangeSet → Review → Publish → Snapshot**：企业语义不能被 Agent 或 UI 的即时操作直接改写；发布版本应是运行时唯一可读的业务事实。
- **稳定资产 ID、别名解析和有界关系遍历**：这是跨版本、跨数据源 grounding 的基础。
- **资产与关系分离**：资产承载可治理概念，关系承载业务语义；不要退回为全部塞进 JSON description。
- **语义草稿与本体治理分层**：草稿可自动生成，但必须经人为审阅后才成为已发布 Ontology。

## 当前缺口：为什么它仍偏 Metadata Registry

现有资产和关系足以登记数据模型、指标、维度及规则，却没有定义：指标的业务驱动树、问题的分析策略、策略所需能力、工具执行结果如何反馈给下一步规划。

更重要的是，运行时没有 `SemanticQueryIR → Ontology Grounding → Planning` 的调用路径。即使图里已有销售额和区域，当前 Agent 也只会把用户问题作为一次 SuperSonic 查询，而非形成“先验证趋势、再找贡献维度、再钻取驱动因素”的闭环。

## AssetKind 的最小演进

保留现有六类，不改其语义。仅建议增加：

| 类型 | 是否新增 | 原因 | 为什么不是普通属性 |
| --- | --- | --- | --- |
| `ANALYSIS_STRATEGY` | 是 | `TrendAnalysis`、`ContributionAnalysis`、`DriverAnalysis` 可被多种问题和指标复用、治理及版本化 | Planner 需要发现、排序和组合它们 |
| `CAPABILITY` | 是 | 将策略映射为受控工具能力，如 time-series comparison、dimension contribution | 避免 Planner/LLM 自由猜测工具 |
| `EVENT` | 否（暂缓） | 只有出现事件事实、事件序列或事件因果分析时才需要 | 当前销售分析可先用时间范围和规则表达 |
| `DRIVER` | 否 | 多数驱动因素本身是现有指标或维度，例如客户数、复购频次、客单价 | 新 Kind 会制造与 Metric 的重复实体 |
| `BusinessRule` | 否 | 当前 `RULE` 足够；后续可通过 relation/property 区分口径、约束、告警 | 无立即规划收益 |
| `BusinessQuestion/Intent` | 否 | 是用户请求的瞬时状态，应进入 IR | 做成资产会把无限问题库写进本体 |

`DATA_MODEL` 与 `SEMANTIC` 暂时保留；等真实运行时 mapping 稳定后，再评估是否更清晰地区分物理、逻辑和语义模型。

## Relation 的演进

不要继续用 `RELATED_TO` 承载关键运行时知识。v0.2 关系建议分批引入：

| 关系 | 起点 → 终点 | 当前价值 |
| --- | --- | --- |
| `MEASURE_OF` | Metric → Entity | 指标的业务对象 |
| `HAS_DIMENSION` / `BREAKDOWN_BY` | Metric → Dimension | 可合法、可推荐的切分路径 |
| `CALCULATED_BY` | Metric → Rule | 口径/公式治理 |
| `MAPPED_TO` | 语义资产 → Data model / 数据集字段 | 从业务语义落到执行对象 |
| `DRIVEN_BY` | Metric → Metric | 驱动树；例如 Revenue → CustomerCount |
| `ANALYZED_BY` | Metric → AnalysisStrategy | **例外**：某指标专属、已审批的业务分析方法；不用于通用策略 |
| `REQUIRES` | AnalysisStrategy → Capability | 策略可否执行 |
| `PRODUCES` | Capability → 观察类型 | 为下一轮计划提供契约 |

通用 `TrendAnalysis`、`ContributionAnalysis`、`DriverAnalysis` 不应针对每个 Metric 重复建立 `ANALYZED_BY` 边。它们作为全局 `ANALYSIS_STRATEGY` 资产发布，并在自身受控定义中声明 applicability conditions；Planner 根据当前目标的本体结构和可用能力动态决定可否使用。只有企业定义的“Revenue 专用价格-销量拆解”这类确实隶属于某指标的策略，才建立 `Revenue --ANALYZED_BY--> RevenuePriceVolumeAnalysis`。

`DEPENDS_ON`、`AFFECTS` 先不作为首批必需关系：语义边界容易重叠，且没有立刻提升当前分析计划质量。若未来加入，应有严格方向性、资产组合和含义说明。关系应逐步改成受控枚举；少量结构化属性（如 `priority`、`valid_from`、`confidence`）可以补充，不要用 JSON 模糊替代关系类型。

## Driver 如何建模

首版采用“**Metric 资产 + `DRIVEN_BY` 关系**”。

```text
Revenue --DRIVEN_BY--> CustomerCount
Revenue --DRIVEN_BY--> PurchaseFrequency
Revenue --DRIVEN_BY--> AverageOrderValue
Revenue --BREAKDOWN_BY--> Product / Customer / Channel / Region
```

这样既能复用每个指标的口径、映射和数据血缘，也使 Planner 可从目标指标取到有限的驱动候选。只有当某个驱动不是可度量指标、需要独立生命周期、责任人、阈值或因果假设时，才考虑引入 `DRIVER` 资产；这不是 v0.2 的必要条件。

## AnalysisStrategy、Capability 与 applicability

五个概念必须明确分工，避免把策略、数据结构和用户问题混在一起：

| 概念 | 回答的问题 | 归属 |
| --- | --- | --- |
| `AnalysisIntent` | 用户想解决什么问题？例如 `ROOT_CAUSE` | `SemanticQueryIR` 的运行时语义 |
| `AnalysisStrategy` | 用什么分析方法解决？例如贡献分析、驱动分析 | 可版本化、可审核的 Ontology 资产 |
| `Capability` | 系统实际上能执行什么操作？例如时间序列比较、维度贡献查询 | 可版本化、可审核的 Ontology 资产 / 工具契约 |
| Ontology Relations | 当前业务对象有哪些可分析结构？例如驱动、可切分维度、口径和执行映射 | 已发布 Ontology 的事实 |
| `AnalysisPlanner` | 综合以上信息，下一步应做什么？ | 运行时决策组件 |

策略是“分析方法”，能力是“可调用动作”，二者通过 `REQUIRES` 建立稳定关系：

```text
ContributionAnalysis --REQUIRES--> DimensionContributionCapability
DimensionContributionCapability --PRODUCES--> DimensionContributionObservation
DriverAnalysis --REQUIRES--> MetricComparisonCapability
```

策略资产还需保存一个**受控且可审计的 applicability definition**，而非依赖 LLM 解释自由文本。例如概念上的结构如下（具体存储字段在编码阶段再定）：

```text
DriverAnalysis
  applicable_intents: [ROOT_CAUSE]
  requires_target_kind: METRIC
  requires_relation: [DRIVEN_BY]
  requires_capability: [MetricComparisonCapability]

ContributionAnalysis
  applicable_intents: [ROOT_CAUSE, COMPARISON]
  requires_target_kind: METRIC
  requires_any_relation: [BREAKDOWN_BY, HAS_DIMENSION]
  requires_capability: [DimensionContributionCapability]

TrendAnalysis
  applicable_intents: [TREND, ROOT_CAUSE]
  requires_target_kind: METRIC
  requires_time_dimension: true
  requires_capability: [TimeSeriesCapability]
```

首批策略可仅有：`TrendAnalysis`、`YoYAnalysis`、`MoMAnalysis`、`DimensionBreakdown`、`ContributionAnalysis`、`DriverAnalysis`。`AnomalyDetection` 只有当已有稳定的检测实现时才登记为 capability/strategy，不能只写名称。

因此选择规则是：`AnalysisIntent + grounded target + Ontology Context + applicability conditions + Available Capabilities + existing Observations`。每轮真实 observation、临时阈值和“已经做过的步骤”都属于 Analysis Plan 运行时状态，不写回 Ontology。`ANALYZED_BY` 仅追加或提升某个指标专属策略的优先级，不能绕过该策略自身的 capability 与安全前置条件。

## SemanticQueryIR 与规划器

建议在保持原有字段兼容的前提下扩展：

```python
class AnalysisIntent(str, Enum):
    LOOKUP = "LOOKUP"
    TREND = "TREND"
    COMPARISON = "COMPARISON"
    ROOT_CAUSE = "ROOT_CAUSE"

@dataclass(frozen=True)
class SemanticScope:
    time_range: TimeRange | None
    filters: tuple[SemanticPredicate, ...]
    comparison: ComparisonWindow | None

@dataclass(frozen=True)
class SemanticQueryIR:
    # existing query fields...
    intent: AnalysisIntent = AnalysisIntent.LOOKUP
    goal: str | None = None
    scope: SemanticScope | None = None
```

这里的 `intent` 是 Parser 的受控推断结果，`goal` 保留用户期望的自然语言语义，`scope` 将“华东”“最近”和对比窗口统一表达。不要让 LLM 直接产出 SQL 或 nGQL。

`QueryPlanner` 继续负责把一条已 ground 的查询任务转为数据查询；它不应被强行重写。新增它上方的 `AnalysisPlanner`：输入为已 ground 的 IR、受限 Ontology Context、全局及指标专属的 Strategy definitions、Available Capabilities 与已有 observations。它先过滤 applicability 不满足或能力不可用的策略，再根据 intent、优先级、证据缺口和已完成步骤选择下一步，输出有顺序、可终止、每步有目的的 `AnalysisPlan`。遗留的游戏域 `models.AnalysisPlan` 不宜直接扩展，应新建通用 planning contracts，之后再迁移调用方。

## OntologyService 应暴露的高层 API

不要暴露“任意图遍历”给 LLM。建议为 Planner 增加受约束 API：

```python
resolve_metric(name_or_alias, snapshot_id) -> GroundedMetric
resolve_dimension(name_or_alias, snapshot_id) -> GroundedDimension
get_metric_rules(metric_id, snapshot_id) -> list[Rule]
get_breakdown_dimensions(metric_id, snapshot_id) -> list[Dimension]
get_metric_drivers(metric_id, snapshot_id, max_depth=2) -> DriverSubgraph
get_strategy_definitions(intent, snapshot_id) -> list[AnalysisStrategy]
get_metric_specific_strategies(metric_id, snapshot_id) -> list[AnalysisStrategy]
get_available_capabilities(runtime_context) -> list[Capability]
get_execution_mapping(asset_id, snapshot_id) -> ExecutionMapping
get_planning_context(request, snapshot_id) -> PlanningContext
```

`get_planning_context` 应返回目标 Metric 的规则、时间支持情况、`BREAKDOWN_BY`/`HAS_DIMENSION`、`DRIVEN_BY`、执行映射、指标专属策略和所需的有限邻域；它不负责替 Planner 判定 applicability。Strategy definitions 与 runtime capabilities 分开返回，避免把运行时可用性错误写入已发布的企业本体。每个调用必须绑定 published snapshot，并限制深度、节点数和关系白名单。这是未来图检索的稳定边界，不是让模型发出任意 nGQL 的通道。

## PostgreSQL 与 NebulaGraph 的边界

```text
PostgreSQL (authoritative)
  Asset / Relation / ChangeSet / Review / Published Snapshot
          │ publish projection
          ▼
NebulaGraph (derived read model, future)
  仅已发布、面向 Agent 检索的策略/驱动/切分/mapping 子图
          │ bounded service query
          ▼
OntologyService → Planner
```

PostgreSQL 负责写入、审计、版本、发布、回滚和精确单跳/小规模查询；NebulaGraph 将来只负责多跳驱动发现、切分路径、策略发现和有限 subgraph retrieval。图投影不是机械复制全部 `OntologyAsset`/`OntologyRelation`：只投影已发布 snapshot 中 Planner 需要的资产、允许关系和必要属性，并以 `snapshot_id`/版本标识隔离。投影可重建、不可成为事实源。

## 完整运行链路：华东销售额下降

```text
用户：“为什么最近华东地区销售额下降？”
  ↓
SemanticParser（现有 contracts；需实现企业 parser）
  产出 intent=ROOT_CAUSE, metric=Revenue,
  scope={Region=华东, recent window, comparison window}
  ↓
SemanticQueryIR（现有类；需补 intent/goal/scope）
  ↓
Ontology Grounding（OntologyService；新增高层查询）
  解析 Revenue、Region、规则、时间支持、合法 breakdown、drivers、执行 mapping；
  同时取得 ROOT_CAUSE 的全局策略定义、Revenue 专属策略（如有）和运行时可用 Capability
  ↓
Analysis Strategy Selection（新增 AnalysisPlanner）
  依据 applicability 动态过滤：
  Trend 可用（Revenue 是 Metric、有时间维度、TimeSeriesCapability 可用）；
  Contribution 可用（存在 BREAKDOWN_BY、DimensionContributionCapability 可用）；
  Driver 可用（存在 DRIVEN_BY、MetricComparisonCapability 可用）。
  再按 ROOT_CAUSE 的证据缺口选择 Trend → MoM/YoY → Contribution → Driver 的受限步骤
  ↓
Analysis Plan（新增通用 contracts；不复用游戏域遗留模型）
  每步包含 goal、required capability、grounded query、stop condition
  ↓
Tool / Query Execution（现有 QueryPlanner 合约 + SuperSonicClient；需接入）
  执行趋势、按 Product/Channel/Customer 的贡献、驱动指标对比
  ↓
Observation（新增运行时对象）
  保存数值、时间窗、置信度、证据引用；不写回 Ontology
  ↓
Agent 决定下一步（AnalysisPlanner）
  若产品贡献已足够显著则停止；否则沿 `DRIVEN_BY` 查询下一层
  ↓
Root Cause / Insight（Orchestrator；由一次 query 调用升级为闭环编排）
  输出“发生了什么、主要贡献者、证据、未验证假设和建议动作”。
```

## 类与接口的改动边界

应演进：`OntologyAssetKind`、relation type 约束、`OntologyAsset` 的受控定义字段或扩展 metadata、`SemanticQueryIR`、企业 `SemanticParser`、新增 `AnalysisPlanner`/Observation contracts、`OntologyService` 高层 API、`Orchestrator` 的多步编排入口、semantic-to-dataset mapping。

暂不应改动：PostgreSQL 的版本/审核/发布/snapshot 基础流程；既有 SuperSonic 单查询路径（保留作为 lookup 兼容路径）；现有 datasource/dataset 实体模型；NebulaGraph（本阶段不创建实现）；遗留 `models.AnalysisPlan`（不要在其中继续堆企业领域字段）。

## 推荐实施顺序（确认后）

1. 完成 v0.2 relation vocabulary、`ANALYSIS_STRATEGY`/`CAPABILITY` contract 与 sales demo 的审核数据。
2. 扩展 IR 的 intent/goal/scope，先实现确定性 parser 规则与回退路径。
3. 实现 PostgreSQL 上的高层 OntologyService 查询及测试，暂不接图数据库。
4. 新增受限 `AnalysisPlanner`，先支持 Revenue 的趋势、贡献和 driver 分析。
5. 将它以 feature path 接入 Orchestrator；保留原单查询模式。
6. 通过真实执行和 observation 闭环验证价值后，再定义 NebulaGraph projection schema 与同步机制。

## 验收标准

一个已发布 snapshot 下，系统能对“为什么最近华东地区销售额下降”生成可审计的有限计划；每一步都能指出使用的 Ontology 资产/关系、执行能力和观测证据；无法执行的策略会被过滤而非由 LLM 幻觉补全；用户可追溯结论到指标口径、过滤范围和数据查询。
