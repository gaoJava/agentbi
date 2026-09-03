# 数据表结构说明

## 1. 业务演示库 `examples`

核心表 `public.video_game_sales` 共 16,595 行。销量单位均为“百万份”。

| 字段 | PostgreSQL 类型 | 业务含义 | AgentBI 用途 |
|---|---|---|---|
| `name` | `text` | 游戏名称 | 明细维度 |
| `platform` | `text` | 游戏平台 | 平台分析、第一层下钻 |
| `year` | `double precision` | 发行年份 | 时间筛选 |
| `genre` | `text` | 游戏类型 | 类型分析、下钻维度 |
| `publisher` | `text` | 发行商 | 发行商排名、下钻维度 |
| `na_sales` | `double precision` | 北美销量 | 区域指标，聚合方式 `SUM` |
| `eu_sales` | `double precision` | 欧洲销量 | 区域指标，聚合方式 `SUM` |
| `jp_sales` | `double precision` | 日本销量 | 区域指标，聚合方式 `SUM` |
| `other_sales` | `double precision` | 其他地区销量 | 区域指标，聚合方式 `SUM` |
| `global_sales` | `double precision` | 全球销量 | 核心指标，聚合方式 `SUM` |
| `rank` | `bigint` | 原始全球销量名次 | 辅助字段，不做求和 |

典型闭环：平台/类型/发行商聚合 → 区域销量比较 → 逐层下钻 → AI 分析 → 报告。

## 2. Superset 元数据库 `superset`

由 Apache Superset Alembic 管理，当前快照迁移版本为 `4b2a8c9d3e1f`。其中保存数据库连接、Dataset、Chart、Dashboard、用户和权限。禁止手工改表；启动时执行 `superset db upgrade`。

## 3. AgentBI 状态库

默认使用 SQLite 文件 `data/agentbi.db`，也可通过 `AGENTBI_DATABASE_URL` 切换 PostgreSQL。表由 `src/agentbi/database.py` 的 SQLAlchemy 元数据创建：

- 身份与权限：`users`、`roles`、`permissions`、`user_roles`、`role_permissions`、`auth_sessions`、`audit_events`
- 问答与工作台：`conversation_states`、`conversation_turns`、`dashboard_charts`、`superset_dashboard_assets`
- 下钻与报告：`drilldown_definitions`、`analysis_reports`
- 数据与语义资产：`data_source_assets`、`semantic_model_assets`、`semantic_domain_metadata`、`dashboard_semantic_bindings`
- 模型配置：`llm_provider_configs`、`supersonic_llm_bindings`

初始化入口为 `python delivery/database/init_database.py`。程序只建缺失表，不覆盖现有数据。

## 4. SuperSonic 配置库

SuperSonic 0.8.6 使用持久化 H2 文件 `semantic.mv.db`，保存数据源、语义模型与指标配置。该文件随离线包提供，并挂载到 `/opt/supersonic/data/semantic.mv.db`。它是应用配置快照，不应使用文本工具编辑。

