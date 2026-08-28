# InsightPilot AgentBI

InsightPilot 把 Apache Superset 的大屏上下文与 SuperSonic 的语义查询能力组合成一个可追问、可下钻、可生成报告、可审计的 AgentBI。浏览器只提交当前大屏的标识、筛选条件和问题；身份与角色由 Superset 后端补充，模型生成的原始 SQL 不会返回浏览器。

## 当前效果

- Superset 大屏右下角显示“AI 洞察·已连接”。
- 自动识别大屏 ID、语义模型、时间范围、筛选条件和图表选择。
- 支持连续追问，并使用与 Superset 用户绑定的 AgentBI 会话上下文。
- 支持基于查询结果继续下钻。
- 返回分析步骤、数据表格、查询耗时、行数、查询编号和 SQL 指纹。
- 自动生成“关键观察—建议动作—数据证据”Markdown 报告，并回链来源大屏。
- 对重复提交提供幂等保护，并具备限流、超时、结果截断和结构化审计日志。

## 架构

```text
Superset 大屏与登录会话
  -> InsightPilot 前端扩展
  -> Superset 安全代理（角色、CSRF、输入白名单）
  -> AgentBI 编排器（策略、会话、幂等、限流、报告、证据）
  -> SuperSonic 语义解析与受治理查询
  -> 只读分析数据源
```

详细边界见 [架构说明](docs/architecture.md)，新增图表接入见
[通用下钻注册中心](docs/drilldown-registry.md)，开发和故障排查见
[运行手册](docs/development-runbook.md)，当前进度见 [里程碑计划](docs/AGENTBI_MILESTONES.md)。

## 一键启动本地演示

仅预览新版登录、权限与三栏工作台（不启动 Superset/SuperSonic）：

```powershell
.\scripts\start-workbench.ps1
```

访问 `http://127.0.0.1:8090/app`，普通用户使用 `user/user`，管理员使用
`admin/admin`。账号、RBAC 权限、可撤销会话和身份审计存储在本地
`data/agentbi.db`；数据库设计见 [身份与权限数据库](docs/identity-database.md)。
该演示登录只绑定本机地址，正式环境应切换 PostgreSQL，并接入 Superset SSO/OIDC。

完整集成演示：

前提：Docker Desktop、Python、Java 已安装，两个开源项目位于：

```text
D:\project\ai-coding\superset-main
D:\project\ai-coding\supersonic-stable
```

在本项目根目录执行：

```powershell
.\scripts\start-demo.ps1 -UseBundledDemoCredentials
```

这个开关只适用于本机自带演示数据，会使用 SuperSonic 的 `admin/admin` 演示账户。脚本会为本次启动生成随机 AgentBI 服务密钥，不会把密钥写进仓库。首次启动 Superset 或首次编译镜像可能需要较长时间。
脚本默认从 `agentbi` 的同级目录查找 `superset-main` 和 `supersonic-stable`，因此项目整体
移动到其他磁盘或目录后无需修改脚本。首次 Superset 初始化成功后会写入本地运行标记，
后续启动跳过耗时的重复初始化；需要重建示例数据时传入 `-ReinitializeSuperset`。

比赛提交包必须同时声明这两个运行依赖，但不建议把两个大型上游项目直接复制进 AgentBI
Git 历史。源码提交采用固定版本的 Git submodule（或随包附带源码归档），离线演示包则附带
固定版本镜像与 SuperSonic 构建产物。评审机器最终仍只需执行同一条 `start-demo.ps1`。

生产式凭据启动：

```powershell
$env:SUPERSONIC_USER = '<user>'
$env:SUPERSONIC_PASSWORD = '<password>'
$env:AGENTBI_API_KEY = '<32+ random characters>'
.\scripts\start-demo.ps1
```

停止服务且保留 Docker 数据卷：

```powershell
.\scripts\stop-demo.ps1
```

## 访问地址

| 服务 | 地址 | 用途 |
| --- | --- | --- |
| Superset | http://127.0.0.1:8088 | 登录并打开任意 Dashboard，点击右下角“AI 洞察” |
| AgentBI API | http://127.0.0.1:8090/docs | 健康检查与接口文档 |
| SuperSonic 后端 | http://127.0.0.1:9080 | 语义服务；本演示不需要单独打开其前端 |

当前本地 Superset 示例环境使用 `admin/admin`，只能用于本机演示。推荐演示问题：

```text
alice 停留时长
哪一天停留时长最高？
访问次数最高的部门
```

## 验收

执行完整自动化验收：

```powershell
.\scripts\verify.ps1
```

仅执行 Python 测试和静态检查：

```powershell
.\scripts\verify.ps1 -SkipFrontend
```

当前回归覆盖身份与提示控制、浏览器上下文白名单、网页插件候选拒绝、上游超时、结果截断、SQL 指纹、报告转义、会话隔离、限流和请求幂等。

## 安全上线清单

- 替换 Superset 与 SuperSonic 演示账户，轮换 AgentBI API Key 和 SuperSonic Token。
- 为生产配置独立的 SuperSonic 服务身份或租户隔离策略；不要共享演示管理员令牌。
- 将 Superset、AgentBI、SuperSonic 放在受控网络内，只通过 TLS 反向代理暴露 Superset。
- 数据库账户保持只读和最小表/行权限；继续由 SuperSonic 管理指标、维度和数据权限。
- 使用外部共享存储替换进程内会话、限流和幂等缓存，以支持多副本部署。
- 接入集中日志和告警，但不要记录问题正文、令牌、API Key、原始 SQL 或完整结果集。
- 对 Superset/SuperSonic 上游依赖执行漏洞扫描并固定经过评审的版本。

## 主要目录

```text
src/agentbi/                         AgentBI 编排器
integrations/superset-extension/     Superset 前后端扩展
deploy/superset/                     Superset 本地集成配置
scripts/                             启停与自动化验收脚本
tests/                               安全、协议与工作流测试
docs/                                架构、运行手册和里程碑
```
