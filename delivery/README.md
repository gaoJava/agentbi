# InsightPilot AgentBI 竞赛交付包

本目录用于生成和核验最终比赛提交物。最终提交前请先阅读 `交付清单.md`，再执行：

```powershell
.\delivery\install.ps1 -UseBundledDemoCredentials
.\scripts\verify.ps1
.\delivery\package-submission.ps1
```

Linux/macOS 或具备 Bash 的评审环境可执行：

```bash
./delivery/install.sh
```

## 目录说明

- `AgentBI项目说明书.docx`：超过 1000 字的正式项目说明书。
- `项目说明书.md`：便于版本审查的同内容 Markdown 版本。
- `测试报告.md`：自动化测试、手工闭环验收及已知限制。
- `部署与用户手册.md`：安装、配置、启动、操作和故障恢复。
- `演示脚本.md`：7 分钟比赛演示流程与备用方案。
- `交付清单.md`：提交前逐项核验和签字清单。
- `env.example`：不含密钥的环境变量样例。
- `database/init_database.py`：调用正式 SQLAlchemy 模型初始化数据库。
- `install.ps1`、`install.sh`：安装与环境预检入口。
- `DOCKER运行说明.md`：镜像构建、Compose 启停与离线导出。
- `export-docker-image.ps1`：导出 Docker TAR 和 SHA256 校验值。
- `package-submission.ps1`：生成不含密钥、数据库和运行缓存的 ZIP。

## 安全提示

提交包不得包含 API Key、Token、真实数据库凭据、`.env`、本地 SQLite 数据库、日志或运行缓存。
模型密钥仅通过管理页面或运行时环境变量注入。
