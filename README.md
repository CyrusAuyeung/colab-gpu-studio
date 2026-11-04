# Colab GPU Studio

独立、移动端优先的 AI 创作工作台。React 前端 + FastAPI 后端，将项目、素材与任务队列保存在自己的服务器，按需连接 Google Colab 执行 ComfyUI 工作流；支持 Drive 缓存、参数绑定、工作流版本管理、H3 LoRA 与运行时回收。

> **这是源码开源整理包，不是生产数据备份。** 已移除第三方创作站联动，其他核心业务实现保留。新安装默认只有明确标识的 Mock 演示流程；Mock 不生成真实 AI 媒体，也不消耗 Colab CU。真实模型需自行导入工作流、配置 OAuth、审核并启用。

## 版本说明

- 导出基线：`a2a8869756f638cccbfa73ad06fcf58a73db0245`。
- 基线提交标注程序版本 `0.1.27`，并加入 H3 ref2v 工作流 `1.4.0` 对应后端能力。
- 原仓库的 Python、前端包及镜像声明仍显示 `0.1.7`；为了遵守“其他不变”，本包没有擅自修改这些版本字符串。**工作流 1.4.0 不等于整个应用 1.4.0。**
- 本包包含仓库内的前后端源码；不从生产站拼接额外文件。生产数据库中的已发布工作流、GPU 实测记录、Drive 缓存和账号凭据不在包中。不能把干净安装当成生产站一键克隆。

## 功能

- 项目、参考素材、任务、作品与参数预设。
- 持久任务队列、取消、幂等与独立 watchdog。
- Manifest 驱动表单、工作流导入/校验/审查/发布/回滚。
- Google Colab 与 Drive OAuth，凭据后端加密保存。
- CPU 缓存准备、GPU 执行、输出保存校验与运行时清理。
- H3 参考图、LoRA 及采样配置的后端处理能力。
- 日/月/单任务预算、时长限制与受限 Agent API。

## 本地启动

需要 Python 3.11+、uv、Node.js 与 npm。以下命令均在解压后的项目根目录执行：

```bash
uv sync --extra test
uv run python scripts/init_env.py --site-url http://127.0.0.1:8788
npm --prefix web ci
npm --prefix web run build
uv run --env-file .env uvicorn app.main:app --app-dir api --host 127.0.0.1 --port 8788 --no-access-log
```

在另外两个终端、同一项目目录分别启动：

```bash
uv run --env-file .env python -m app.worker worker
uv run --env-file .env python -m app.worker watchdog
```

浏览器打开 `http://127.0.0.1:8788`，使用新生成 `.env` 中的 `STUDIO_PASSWORD`。初始化脚本不会覆盖已有配置，也不会回显密码。**不要直接复制空的 `.env.example` 作为可用配置。**

## Docker

先生成私密 `.env`，再运行：

```bash
docker compose config -q
docker compose up -d --build
```

默认只绑定 `127.0.0.1:8788`。公网部署应加 HTTPS 反向代理，设置自己的 `SITE_URL`，不要直接暴露 ComfyUI。三服务共享 `studio-data` 卷，**不要使用 `docker compose down -v` 删除持久数据**。

## 启用真实计算

先阅读 [OAuth 配置](docs/OAUTH_SETUP.md)、[模型接入](docs/MODEL_ONBOARDING.md)、[工作流审查](docs/WORKFLOW_REVIEW.md) 和 [运行协议](docs/RUNTIME_PROTOCOL.md)。

`GPU_REAL_ENABLED=0` 为默认值。开启之前必须自行完成账号授权、资源规格核验、工作流/权重许可审查、每分钟费率与预算配置。任何创建 Colab runtime 的操作（包括 CPU 缓存准备）都可能产生费用。余额与实际扣费以 Google 账户为准；不要把估算当实际账单。

本次开源整理没有创建 runtime、下载模型权重或执行付费生成。仓库测试使用隔离的 Mock/契约环境，不证明真实 GPU 生成质量。

## 测试

```bash
uv run pytest -q
uv run ruff check api tests
npm --prefix web run test:contract
npm --prefix web run build
```

测试与扫描的本次实际结果见 [整理与验证报告](docs/RELEASE_AUDIT.md)。

## 安全与许可

- 不提交 `.env`、数据库、备份、运行日志、用户素材、OAuth JSON 或访问令牌。
- `ENCRYPTION_KEY` 用于解密本地凭据，必须单独备份；丢失后不能恢复已保存的授权。
- 本软件是个人工作台，不应当作已完成多租户隔离/安全审计的公共 SaaS。
- 项目原有 MIT 许可证与作者署名保留；ComfyUI、第三方节点、模型与 LoRA 的许可证独立适用，本包不包含模型权重。
- 部分底层 metadata 字段保留历史前缀以兼容现有协议；它们不是被移除的外站联动，也不会因此访问原站。

GitHub 发布步骤见 [发布指南](docs/GITHUB_RELEASE.md)。
