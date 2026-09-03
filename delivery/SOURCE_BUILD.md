# AgentBI 源码构建与验收

## 工程组成

- `src/agentbi`：FastAPI 编排后端及原生分析工作台前端资源。
- `src/agentbi/web-src`：工作台 TypeScript 源码。
- `integrations/superset-extension/frontend`：Superset 扩展前端源码。
- `integrations/superset-extension/backend`：Superset 扩展后端源码。
- `tests`：后端单元测试与接口测试。
- `deploy`、`Dockerfile`、`compose.agentbi.yml`：容器构建与部署配置。
- `scripts`：开发、构建、启动和数据同步脚本。
- `docs`、`delivery`：架构、运行、测试与交付文档。

## 编译与测试

需要 Python 3.11、Node.js 20、npm 和 Docker Desktop。

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check src tests
python -m compileall -q src

cd integrations\superset-extension\frontend
npm ci
npm run typecheck
npm run workbench:typecheck
npm run build
```

构建 AgentBI 镜像：

```powershell
docker build -t insightpilot-agentbi:0.1.0 .
```

所有密钥均应由环境变量或运行时 `.env` 文件注入。源码包只包含
`.env.example` 和 `config/*.example.json`，不包含真实凭据及运行数据。
