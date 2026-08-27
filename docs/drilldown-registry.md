# AgentBI 通用下钻注册中心

## 目标

新增图表不再修改下钻页面。图表只声明稳定的 `chartId`，注册中心提供指标、语义模型、维度路径和展示数据，工作台自动生成“三点菜单”，下钻页自动生成原始图、层级、Agent 上下文和证据路径。

## 接入步骤

管理员现在可以直接在页面操作：

1. 登录后进入“仪表盘管理”；
2. 点击“新增图表”；
3. 填写 Chart ID、图表名称、数据集、核心指标和图表类型；
4. 同时选择语义模型，并填写至少两级下钻维度；支持使用逗号、中文逗号、`→` 或 `>` 分隔；
5. 点击“创建并发布”。

系统会把图表写入 `dashboard_charts`，把下钻路径写入
`drilldown_definitions`，随后普通用户工作台自动出现新图表和三点下钻菜单。

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
- 可访问的语义模型 `semanticModel`；
- 至少一个明细维度和数据行；
- 两个可用的 Agent 推荐问题；
- 当前用户拥有 `drilldown:use` 权限。

缺少注册信息的图表保持可查看，但不生成下钻入口，并提示“尚未配置指标、语义模型或下钻维度”。静态图片和说明文字不注册。

## Superset 接入方式

当前注册文件用于本地原型。正式接入时，由后端根据 Superset Chart ID 获取指标、筛选条件和数据集，再与 SuperSonic 语义模型中的维度关系合并，返回同一结构。前端渲染协议不变，并在服务端执行权限校验，不能信任浏览器提交的指标或数据范围。
