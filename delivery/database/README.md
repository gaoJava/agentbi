# InsightPilot / AgentBI 数据库交付说明

源码仓库提供可审查的 SQL/CSV 初始化文件。数据库二进制快照仅随离线交付包提供，不上传公共源码仓库，以避免把运行时状态或潜在敏感数据发布到 GitHub。

## 交付内容

- `postgresql/001_schema.sql`：业务演示表、注释和索引。
- `postgresql/video_game_sales.csv`：UTF-8 完整种子数据，16,595 行。
- `postgresql/002_load_data.sql`：可重复执行的数据导入及行数校验。
- `postgresql/verify.sql`：版本、编码、行数、维度数和核心聚合校验。
- `initialize.ps1` / `initialize.sh`：已有 PostgreSQL 环境的一键初始化入口。
- `init_database.py`：AgentBI 状态库建表入口。
- `TABLES.md`：业务表、Superset、AgentBI、SuperSonic 数据结构说明。
- `snapshots/`（仅离线交付包）：Superset、AgentBI 与 SuperSonic 的预配置数据库快照。
- `compose.database.yml`、`start-database.*`：仅数据库交付包的一键导入入口。
- `CHECKSUMS.sha256`：全部数据文件的完整性校验值。

## 推荐的一键初始化

本数据库交付包不包含应用源码和应用镜像。评委安装 Docker 后，在本目录执行：

- macOS / Linux：`chmod +x start-database.sh && ./start-database.sh`
- Windows PowerShell：`powershell -ExecutionPolicy Bypass -File .\start-database.ps1`

完整离线交付包首次启动空数据卷时会创建 PostgreSQL 角色和 `superset` 库、恢复 Superset 元数据、创建并恢复 `examples` 库。仅从 GitHub 克隆源码时，请使用下方 SQL/CSV 初始化方式；预配置二进制快照需从独立离线交付包获取。重复启动会保留数据，不会重复导入。

停止数据库但保留数据：Windows 执行 `stop-database.ps1`，macOS/Linux 执行 `./stop-database.sh`。只有明确需要重新初始化时才执行 `docker compose -f compose.database.yml down -v`；该命令会删除已导入数据。

## 独立导入业务数据

适用于已经安装 PostgreSQL 客户端的环境。请先自行创建空的 `examples` 数据库，并确保目标用户有建表权限。

Windows：

```powershell
$env:PGPASSWORD='数据库密码'
powershell -ExecutionPolicy Bypass -File .\initialize.ps1 -User superset -Database examples
```

macOS / Linux：

```bash
export PGPASSWORD='数据库密码'
./initialize.sh
```

两套脚本都会先建表，再清空并导入完整种子数据，最后验证必须为 16,595 行。

## 版本与适配环境

| 组件 | 交付版本/格式 | 适配要求 |
|---|---|---|
| PostgreSQL | 17，custom-format dump + UTF-8 SQL/CSV | 推荐 17；SQL/CSV 可用于 14–17 |
| Superset 元数据库 | Alembic `4b2a8c9d3e1f` | 必须使用交付镜像并执行 `superset db upgrade` |
| SuperSonic | 0.8.6，H2 文件库 | 使用交付镜像；不可跨版本直接修改 H2 文件 |
| AgentBI | SQLite 3 / SQLAlchemy | 默认随包运行；也支持 PostgreSQL URL |
| Docker | Docker Desktop / Engine + Compose v2 | 建议 Docker Desktop 4.30+，至少 8 GB 内存 |

离线镜像为 `linux/amd64`。Intel Mac、Windows x86-64 和 Linux x86-64 可直接运行；Apple Silicon Mac 由 Docker Desktop 使用 amd64 模拟运行，首次启动会稍慢。

## 配置参数

敏感值由安装脚本生成到 `delivery/offline/.env.runtime`，该文件不得提交公共仓库。

| 参数 | 默认/示例 | 说明 |
|---|---|---|
| `POSTGRES_PASSWORD` | 自动生成 | PostgreSQL `superset` 用户密码 |
| `SUPERSET_SECRET_KEY` | 自动生成且必须固定 | Superset 元数据加密密钥；更换后旧连接凭据可能无法解密 |
| `AGENTBI_DATABASE_URL` | `sqlite:////app/data/agentbi.db` | AgentBI 状态库连接串 |
| `SUPERSONIC_BASE_URL` | 容器内 `http://supersonic:9080` | SuperSonic 服务地址 |
| `SUPERSET_BASE_URL` | 容器内 `http://superset:8088` | Superset 服务地址 |
| `AGENTBI_API_KEY` | 自动生成 | Superset 与 AgentBI 的服务鉴权密钥 |
| `AGENTBI_SESSION_SECRET` | 自动生成 | AgentBI 会话签名密钥 |
| `BIND_ADDRESS` | `127.0.0.1` | 本机绑定地址；确需局域网访问时再改为 `0.0.0.0` |

完整非敏感示例见 `delivery/env.example`。

## 初始化注意事项

1. 首次安装前确保 5432、8088、8090、9080 未被其他程序占用；离线编排默认只暴露后三个端口。
2. `SUPERSET_SECRET_KEY` 创建后必须保持不变，否则 Superset 已加密的连接密码不可读取。
3. 不要删除 Docker volume 或 `delivery/offline/data`，除非明确要清空演示数据。
4. Windows 脚本必须用 PowerShell 执行；不要在 CMD 中直接执行 `.ps1`。
5. CSV 为 UTF-8，Excel 打开仅用于查看；不要另存后再导入，以免编码和数值格式变化。
6. 自建 PostgreSQL 环境应使用 UTF-8 数据库；若使用远程数据库，请先放通网络与 `pg_hba.conf`。
7. 大模型调用需要公网，但数据库、图表、下钻和已有报告数据可在离线局域环境运行。

## 验收标准

- `video_game_sales` 行数为 `16595`。
- 至少可按 `platform`、`genre`、`publisher` 聚合。
- `global_sales`、`na_sales`、`eu_sales`、`jp_sales` 均可执行 `SUM`。
- Superset 经营总览可加载；SuperSonic 可识别视频游戏销量语义模型；AgentBI 可完成问答、下钻与报告闭环。
