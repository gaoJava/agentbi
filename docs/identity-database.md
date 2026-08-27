# AgentBI 身份、权限与会话数据库设计

## 选型

本地开发默认使用 SQLite，数据库文件为 `data/agentbi.db`，无需安装额外服务，适合当前快速调试和比赛演示。生产环境建议使用 PostgreSQL 16；数据访问层采用 SQLAlchemy，表结构未使用 SQLite 专属语法，切换时只需设置：

```text
AGENTBI_DATABASE_URL=postgresql+psycopg://agentbi:<password>@<host>:5432/agentbi
```

SQLite 文件、PostgreSQL 密码和连接串均不得提交到仓库。

## 数据关系

```text
users ──< user_roles >── roles ──< role_permissions >── permissions
  │
  ├──< auth_sessions
  ├──< audit_events
  └──< dashboard_charts ── drilldown_definitions
```

## 核心表

| 表 | 用途 | 关键字段与约束 |
| --- | --- | --- |
| `users` | 用户主数据 | UUID 主键；`username` 唯一；密码哈希；主角色；数据范围；启停、锁定和最后登录时间 |
| `roles` | 角色字典 | `code` 主键；当前内置 `user`、`admin` |
| `permissions` | 原子权限字典 | `code` 主键；页面和接口都使用相同权限码 |
| `user_roles` | 用户—角色多对多 | `(user_id, role_code)` 联合主键 |
| `role_permissions` | 角色—权限多对多 | `(role_code, permission_code)` 联合主键 |
| `auth_sessions` | 服务端会话 | UUID；用户；CSRF 摘要；过期时间；撤销时间 |
| `audit_events` | 身份安全审计 | 登录、退出、拒绝等事件；结果、来源地址和有界详情 |
| `dashboard_charts` | 图表定义 | Chart ID、名称、数据集、指标、可视化类型、发布状态和创建人 |
| `drilldown_definitions` | 下钻配置 | 图表一对一配置；语义模型、维度路径、发布状态和创建人 |

## 安全规则

- 使用 PBKDF2-HMAC-SHA256（随机盐、260,000 次迭代）保存密码，不保存明文或可逆密文。
- 浏览器 Cookie 只携带签名后的用户 ID、会话 ID、CSRF Token 和过期时间，不携带可信权限。
- 每次请求从数据库重新读取用户状态、角色、权限和数据范围；停用账号或调整权限可立即生效。
- 退出和切换账号都会撤销 `auth_sessions` 记录，复制旧 Cookie 不能恢复访问。
- 修改类请求继续校验 CSRF；管理员接口同时检查管理员角色和 `user:manage` 权限。
- 审计日志不记录密码、Cookie、问题正文、原始 SQL 或完整结果。

## 当前初始化数据

本地启动脚本首次运行时创建：

| 用户 | 角色 | 数据范围 | 本地演示密码 |
| --- | --- | --- | --- |
| `user` | 数据分析师 | 华东区域 | `user` |
| `admin` | 系统管理员 | 全部区域 | `admin` |

密码只用于本机演示。已有数据库不会在后续启动时覆盖账号或密码。
