# Colab GPU Studio 0.1.31-dev.3 开发交付

尚未完成原规格全部验收；这是本地开发源码快照，不是生产发布。未部署，未使用真实GPU。

源码基线HEAD：ae4211d0f4de4c2335c81120cf5bd07af94a5e3d；包含未提交开发增量。打包时间：2026-11-05T13:28:43.609714+00:00。未进行本次生产状态快照。

## 阅读顺序

用户原始规格（requirements/原始实施规范.md）优先；其次docs/CURRENT_STATUS.md，再看docs/RUNTIME_PROTOCOL.md及REAL_GPU_VALIDATION.md。OFFLINE_IMPLEMENTATION.md和证据日志包含历史结果，不应把历史全量测试当当前全量批准。

## 本地运行

在解压后的项目根目录执行：

```sh
uv sync --locked --extra test
npm --prefix web ci
npm --prefix web run build
uv run python scripts/offline_server.py --port 18788
```

访问http://127.0.0.1:18788，演示密码mock-local-only。启动器创建独立临时数据库及随机密钥，不继承业务配置，GPU_REAL_ENABLED强制0。退出服务后保留临时目录供定位，下一次启动使用新目录。此密码仅本地演示。安装依赖需要网络；应用测试禁止真实业务网络。

## 定向验证

按修改范围选择对应文件，不反复跑全量：

```sh
uv run pytest tests/gpu/test_poll_recovery.py tests/gpu/test_mcp_chunk_transfer.py -q
uv run pytest tests/gpu/test_drive_file_streaming.py -q
```

手机交互脚本需启动18793本地服务，或用OFFLINE_BROWSER_BASE指定本机端口；OFFLINE_CHROMIUM指定已安装Chromium。物理安卓键盘未验证。verify_offline.py为历史完整验收入口，不是本次开发默认回归命令。

## 迁移与回退

仅新增Batch/Execution表及JSON扩展字段，详见迁移脚本和OFFLINE_IMPLEMENTATION.md。生产迁移必须先停写备库、备媒体和原加密密钥，在副本核对后实施；本包不自动部署。回退保留新表与历史证据，不删除卷、不覆盖.env、不重建密钥。

## 已知边界

1. 真实Colab/MCP/Drive/GPU、模型、热驻留、CU与画质速度均未验证。
2. 真实安卓键盘、前端全部状态矩阵、调度公平性完整证据、生产副本迁移回退尚未全项验收。
3. 内置工作流保留原审批与禁用边界，不因离线检查通过而授权真实执行。
4. 项目LICENSE随包保留；完整依赖/模型许可证法务审查尚未完成，模型权重不随包。
5. 静态敏感信息模式扫描不是对所有生产秘密的精确比对；本次不加载或导出生产凭据。

## 包内容

当前源码、迁移、测试、前端源文件、脱敏后筛选的证据、原始规格和逐文件SHA256。没有.env、数据库、OAuth文件、真实媒体、模型、node_modules、.venv、dist或Git历史。未提交任何代码到远程仓库。

## 本次最终增量

包含双调度器争抢单Job、有限持续到达公平性、500MiB在60%断线重建后续传至图库证据；Worker退出不再永久报告running。所有结果均为本地离线，不承诺真实GPU或所有故障场景。故障矩阵见FAULT_MATRIX.md。
