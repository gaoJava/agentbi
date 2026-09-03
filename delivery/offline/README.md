# InsightPilot AgentBI 完整离线运行包

目标机器只需预装 Docker Desktop（Windows）或 Docker Engine + Compose v2（Linux），运行过程中不访问镜像仓库，也不下载 Python、Java 或前端依赖。

## 启动

1. 将整个 `offline` 目录复制到目标机器。
2. Windows 可双击 `install.cmd`，或在 PowerShell 执行：`powershell -ExecutionPolicy Bypass -File .\install.ps1`。
3. 安装脚本自动检测 Docker、校验并加载预构建镜像、初始化配置与数据并启动服务。
4. 打开 `http://127.0.0.1:8090/app`。

### macOS / Linux

先启动 Docker Desktop，然后在终端执行：

```bash
cd /path/to/offline
chmod +x install.sh
./install.sh
```

`install.sh` 会自动识别 macOS、Linux 和 Windows Shell。当前镜像为 `linux/amd64`；Intel Mac 原生运行，Apple Silicon Mac 由 Docker Desktop 使用兼容模式运行，首次启动可能略慢。

首次启动会校验并导入镜像、恢复 Superset 元数据和示例业务库、生成本机随机密钥，并自动取得 SuperSonic 会话令牌。再次启动可执行 `.\install.ps1 -SkipImageLoad`。本包交付的是已完成编译的镜像产物，安装阶段执行的是构建产物校验、装载与容器编排，无需在评审电脑二次编译源码。

## 默认演示账号

- InsightPilot 管理员：`admin / admin`
- InsightPilot 普通用户：`user / user`
- Superset：`admin / admin`
- SuperSonic：`admin / admin`

仅用于离线评审演示。正式交付时请修改演示密码并关闭演示登录。

## 停止与重置

- 停止：`.\stop-offline.ps1`
- 完全重置数据：先停止，再执行 `docker volume rm insightpilot-offline_postgres-data`，然后重新启动。此操作会删除运行后新增的数据。

## 包含内容

- AgentBI、Superset（含扩展）、SuperSonic、PostgreSQL、Redis 镜像
- Superset 仪表盘/图表元数据快照和 `examples` 示例业务库
- AgentBI 工作台、下钻配置和报告种子（不包含会话、审计日志或大模型 API Key）
- SHA256 完整性校验文件

## 双端兼容边界

- Windows 10/11 x86-64：Docker Desktop + PowerShell 5.1 或更高版本；入口为 `install.cmd` 或 `install.ps1`。
- macOS Intel：Docker Desktop；入口为 `install.sh`，镜像原生运行。
- macOS Apple Silicon：Docker Desktop；入口为 `install.sh`，Compose 通过 `linux/amd64` 兼容模式运行预构建镜像，首次初始化可能较慢。
- Linux x86-64：Docker Engine + Compose v2；入口为 `install.sh`。

大模型调用本身仍需要访问所配置的模型服务；在完全断网环境中，规则问答、真实数据库查询、仪表盘和下钻仍可运行，LLM 增强与报告润色会安全回退。

当前离线镜像包大小约 1.44 GiB，SHA256 为 `db84b5e66d75fd963818200900f0eb046e582c4b5ac1410c85eb2a00c3c684d3`。安装脚本会以同目录 `.sha256` 文件为准自动校验。
