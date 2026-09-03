# AgentBI Docker 运行说明

## 1. 在线构建并启动

在项目根目录执行：

```powershell
.\scripts\start-docker.ps1 -Build
```

脚本会在 `.runtime/docker.env` 生成随机 API Key 和会话密钥，然后通过 Compose 构建并启动。访问 `http://127.0.0.1:8090/app`，默认本地演示账号为 `admin/admin` 和 `user/user`。

默认基础镜像为 `python:3.11-slim-bookworm`，也可以通过 `AGENTBI_PYTHON_IMAGE` 指定内部镜像仓库中的等价镜像。

AgentBI 容器默认通过 `host.docker.internal` 连接宿主机上的 Superset（8088）和 SuperSonic（9080）。如两个服务运行在其他机器或同一个 Compose 网络，请修改 `.runtime/docker.env` 中的 `SUPERSET_BASE_URL` 和 `SUPERSONIC_BASE_URL`。

## 2. 停止

```powershell
.\scripts\stop-docker.ps1
```

该命令保留 `insightpilot-agentbi-data` 数据卷。只有确定不再需要本地用户、模型配置、下钻配置和报告时，才执行：

```powershell
.\scripts\stop-docker.ps1 -RemoveData
```

## 3. 导出离线镜像

```powershell
.\delivery\export-docker-image.ps1
```

产物位于 `delivery/docker/`：

- `insightpilot-agentbi-0.1.0.tar`
- `insightpilot-agentbi-0.1.0.tar.sha256`

评审机导入：

```powershell
docker load --input .\insightpilot-agentbi-0.1.0.tar
.\scripts\start-docker.ps1
```

## 4. 完整离线环境

完整离线包位于 `delivery/offline/`。把该目录整体复制到已安装 Docker 的评审机，执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\start-offline.ps1
```

离线包包含以下固定镜像：

1. `insightpilot-agentbi:0.1.0`
2. `insightpilot-superset:0.1.0`（包含 AgentBI 扩展）
3. `insightpilot-supersonic:0.8.6`
4. `postgres:17`
5. `redis:7`

同时包含当前 Superset 元数据、`examples` 业务库和脱敏后的 AgentBI 配置库快照。大模型密钥、登录会话、审计日志和历史对话不会进入离线种子。

本机已用独立 Compose 项目及 18088/18090/19080 备用端口完成真实启动验证：AgentBI、Superset 健康检查均为 200，工作台页面为 200；恢复后包含 10 个仪表盘、112 张图表和 16,595 行 `video_game_sales` 数据。

重新生成完整离线包：

```powershell
.\delivery\build-offline-bundle.ps1
```

五个服务保持独立容器，便于健康检查、故障定位和数据持久化，同时无需目标机器访问镜像仓库。
