# AgentBI 通用下钻注册中心

## 目标

新增图表不再修改下钻页面。图表只声明稳定的 `chartId`，注册中心登记指标、语义模型和维度路径，但不生成展示数值。只有绑定 Superset 图表并由 SuperSonic 返回真实查询结果后，工作台才开放下钻层级、Agent 上下文和证据路径。

## 接入步骤

管理员现在可以直接在页面操作：

1. 登录后进入“仪表盘管理”；
2. 点击“新增图表”；
3. 填写 Chart ID、图表名称、数据集、核心指标和图表类型；
4. 同时选择语义模型，并填写至少两级下钻维度；支持使用逗号、中文逗号、`→` 或 `>` 分隔；
5. 点击“创建并发布”。

系统会把图表写入 `dashboard_charts`，把下钻路径写入
`drilldown_definitions`，随后普通用户工作台自动出现新图表和三点下钻菜单。

## 配置生命周期

管理员可在“仪表盘管理”或“下钻配置”中管理数据库图表：

- **修改**：更新标题、数据集、指标、图表类型、语义模型和下钻路径；稳定的 Chart ID 不允许修改。
- **下线**：同步关闭普通用户工作台图表与下钻入口，配置仍保留，可再次上线。
- **上线**：重新发布图表与下钻能力。
- **删除**：只允许删除已下线图表，并同时删除其下钻定义；操作不可恢复且写入审计日志。

`revenue`、`structure` 是代码随包发布的内置配置，页面只允许查看，避免数据库操作覆盖版本化配置。

下列代码注册方式保留给内置图表和 Superset 自动同步场景：

1. 图表容器增加稳定标识：

```html
<article class="chart-card" data-drill-chart="customer_growth">
  <header><div><strong>新增客户趋势</strong></div></header>
  <!-- 图表内容 -->
</article>
```

2. 在 `src/agentbi/web/drilldown-registry.js` 注册同名配置：

```javascript
customer_growth: {
  metric: '新增客户数',
  semanticModel: 'customer_model',
  sourceType: 'revenue',
  sourceTitle: '新增客户趋势',
  breadcrumb: '客户总览 › 2026 Q3 › 华东 › 渠道',
  connectorOne: '点击 2026 Q3，下钻维度：区域',
  levelOneLabel: '第 1 层 · 区域',
  levelOneTitle: '2026 Q3 各区域新增客户',
  bars: [['华东', 88, '820']],
  connectorTwo: '点击 华东，下钻维度：渠道',
  levelTwoLabel: '第 2 层 · 渠道',
  levelTwoTitle: '华东新增客户渠道贡献',
  tableDimension: '渠道',
  total: '820',
  rows: [['线上', '510', '62%']],
  context: '第 2 层 · 华东新增客户渠道贡献',
  questions: ['新增客户主要来自哪里？', '哪个渠道转化率最低？'],
  insight: '线上渠道贡献新增客户的 62%。',
  insightSource: '洞察来源：客户语义模型',
  evidence: '客户总览 → 2026 Q3 → 华东 → 渠道'
}
```

页面会自动生成“设为下钻起点”和“进入下钻分析”，不需要手写菜单事件。

原始图当前支持 `revenue`（趋势/柱状图）、`structure`（结构/环图）和
`table`（指标明细表）。表格型图表额外声明列和原始行：

```javascript
sourceType: 'table',
sourceColumns: ['部门', '新增客户', '同比'],
sourceRows: [['华东一部', '326', '+18.2%']]
```

## 能力判定

只有同时具备以下元数据的图表才启用下钻：

- 受治理指标 `metric`；
- 可访问的真实 SuperSonic 模型映射 `semanticModel`（格式为 `supersonic:<Model ID>`）；
- 至少一个明细维度和数据行；
- 两个可用的 Agent 推荐问题；
- 当前用户拥有 `drilldown:use` 权限。

缺少注册信息的图表保持可查看，但不生成下钻入口，并提示“尚未配置指标、语义模型或下钻维度”。静态图片和说明文字不注册。

## Superset 接入方式

数据库注册记录只保存治理元数据，不再作为展示数据来源。后端根据 Superset Chart ID 获取指标、筛选条件和数据集，再与 SuperSonic 语义模型中的维度关系合并；前端仅渲染服务端返回的真实 `data` 和 `evidence`。身份、角色和数据范围由签名会话补充，不能信任浏览器提交的 actor、指标或数据范围。
