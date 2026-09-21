# AgentBI 2.0 企业 Ontology 设计

## 定位

AgentBI 2.0 的 ontology 是企业业务语义的唯一事实源。SuperSonic、Superset 和数仓都是
执行或展示适配器，不能反向成为业务指标、实体关系或口径的主数据。

## 核心资产和关系

每一个资产具有稳定 ID、名称、别名、版本及状态（草稿、审核、已发布、退役）。一期支持
`Entity`、`Metric`、`Dimension`、`DataModel`、`Semantic`、`Rule` 六类资产；关系为带版本的
有向边，例如 `MEASURE_OF`、`DESCRIBES`、`IMPLEMENTS`、`DERIVED_FROM`、`GOVERNS`、
`RELATED_TO`。

发布快照是不可变且自洽的：所有资产和边必须属于同一版本，边的两端必须是已发布资产。
这使任意一次查询、血缘分析和影响分析都能固定到一个可复现的业务语义版本。

## 查询路径

自然语言解析器只产生与 ontology version 绑定的 `SemanticQueryIR`，其中引用稳定资产 ID，
不包含 SQL。查询规划器据此检查指标口径、数据权限、实体关系和可用数据模型，再选择
SuperSonic、Superset 或直连数仓作为执行目标。执行器只能接收经过规划的 IR。

## 存储与投影

PostgreSQL 保存资产版本、审批、发布事务和审计；NebulaGraph 保存已发布快照的节点与边，
用于多跳关系、血缘和影响分析。发布事务成功后才投影到图数据库，并以版本标识隔离快照。
SuperSonic Model/View 是可执行子集的下游投影。

## 一期范围

一期首先提供版本固定的快照读取、别名解析和最多五跳的定向关系遍历；这些能力不生成 SQL，
也不改变现有 SuperSonic 查询路径。后续再加入 PostgreSQL/NebulaGraph 适配器、审批 API、
IR 解析器和执行规划器。
