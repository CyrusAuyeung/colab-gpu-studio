# 本地 v0.2 开发增量与回退

> 当前状态以 [CURRENT_STATUS.md](CURRENT_STATUS.md) 为准。下方为历史增量记录，测试数量与“尚未实现”描述可能已过时。

> 尚未完成整份实施规格，不是发布版本；未部署生产。权威需求为用户上传的《Colab GPU Studio v0.2 — 架构重构与移动端 UI 实施指导》。本文件只描述已经存在的代码及已知边界。

## 基线

工作树 `/opt/data/workspace/colab-gpu-studio-offline`，分支 `refactor/v0.2-offline-ui`。GitHub 基线 `ae4211d0f4de4c2335c81120cf5bd07af94a5e3d`，另合入网站 0.1.30 程序快照的修复；详见 DEVELOPMENT_BASELINE.md。未更改线上服务和数据库。

## 本地启动

在仓库根目录：

```sh
uv sync --locked --extra test
npm --prefix web ci
npm --prefix web run build
uv run python scripts/offline_server.py --port 18788
```

仅监听 127.0.0.1。演示密码 `mock-local-only`，不是线上凭据。脚本清除继承的业务配置，创建独立临时 SQLite/媒体目录，生成本地测试加密与会话密钥，忽略 `.env`，强制 GPU_REAL_ENABLED=0。API 与 worker 同进程运行；关闭浏览器不影响任务，退出服务才停止。服务重启目前创建新的临时目录；SQLite 调度恢复另由单元/集成测试验证，不能据此声称跨服务重启持久化已验收。

```sh
uv run pytest -q
uv run ruff check api tests
npm --prefix web run typecheck
npm --prefix web run test:contract
npm --prefix web run build
uv run python scripts/offline_browser_check.py
```

浏览器脚本仅接本机 API，阻止外部资源；当前 Chromium 路径为测试机实际路径，需要在其他机器改为可用安装路径。浏览器报告和截图在 docs/offline-evidence/。网络审计来自拦截层，不来自前端。

## API 增量

所有接口沿用 Session/Bearer 鉴权与 owner 限制。

- POST `/api/gpu/batches`：`jobs` 1..100 项，旧 Job 请求结构；`mode` 为 save/smart/batch，`idle_seconds` 0..300；必须提供 Idempotency-Key。当前仅允许 Mock；202 返回批次 ID。同键异参409，整批入库事务原子。
- GET `/api/gpu/batches/{id}`：逐项任务与完成/失败/取消/等待汇总。
- POST `/api/gpu/batches/{id}/cancel`：阻止后续任务，活动任务进入安全清理。
- GET `/api/gpu/leases`：白名单字段，不含 Runtime token/连接信息。
- 旧 `/api/gpu/jobs` 等保留。内部 uploading/cleaning_up/cleanup_pending 等状态继续沿用，未强改旧客户端语义。

兼容性签名排除提示词、Seed、输入引用与绑定图，包含不可变环境/Manifest、工作流版本与请求规格；保守限制跨工作流复用。原子 lease handoff 更新旧任务完成、新任务引用及租约属主。智能模式空闲120秒由 Fake Clock 验证，硬截止优先。真实批次尚不开放。

## 数据库与回退

新增 `gpu_batches`、`gpu_executions` 表；既有 jobs/leases 通过 JSON 附加字段，不删除旧行，不改已发布工作流。Repository 初始化增量 create_all。批次有数据库 `(owner_id,idempotency_key)` 唯一约束；事务包含所有子任务。

生产迁移尚未执行。未来发布前先停止写入并使用 SQLite backup API 或 PostgreSQL pg_dump 备份，复制媒体并保存原加密密钥；在数据库副本运行新代码，比较任务、作品与工作流计数，再验证旧代码回退可读。不得删除卷、重建密钥或覆盖 .env。

回退代码时保留新表与 JSON 扩展字段，旧代码忽略它们；不要直接 DROP 新表，避免丢失执行证据。已进入智能空闲的租约必须先由新 watchdog 确认释放，再切旧 worker。Alembic 0002_batch_execution 已提供；重复迁移及保留旧行专项通过。完整生产数据库副本回退演练未执行。

## 当前证据及不足

- 后端最新全量执行131通过。最新记录见 offline-evidence/ACCEPTANCE.md。
- 30 Job/1 Lease、100 Job取消10保留90资产、智能空闲、混合版本专项通过。
- 本地150/500MiB续传与调度清单资产幂等专项通过；真实云存储直传未实现/未验证。
- 前端构建/类型与契约通过；七宽度×双主题×五页共70组DOM溢出/页面错误检查通过，后续改动后需复测。
- 独立审查已返回4项问题，三项已修复，一项Worker生产路径未完成；详见offline-evidence/REVIEW_FINDINGS.md。
- 页面已拆分，辅助函数已共享；参数草稿、批次汇总、搜索筛选与分批展示已实现。真实安卓软键盘、断网重试与完整负载验收未完成。
- 常驻 Comfy/热模型目前只有协议与Mock复用证据，没有真实实现验收。
- 详细真实验证边界见 REAL_GPU_VALIDATION.md。

本轮Worker生产端：显式注入LocalArtifactStore后，run_job写artifacts清单；publish_file分块校验/续传，Comfy下载分块落盘。150MiB本地文件生产测试通过（采样函数被替换，未运行Comfy）。远程CLI/MCP云存储适配仍未接入。全量131项通过、1项弃用警告。

## 后端边界拆分

- batch_routes.py：批次创建、查询、取消、脱敏租约接口。通过构造函数注入Admission和Presenter，避免反向导入main。
- job_schemas.py：JobInput和Policy校验模型。
- presenters.py：Job/Asset公开字段白名单。
- main.py：仍负责应用装配与部分既有Admission/资源路由。完整领域服务拆分尚未完成，不把本次提取视为整个重构结束。

拆分后131项全量通过，Ruff和diff check通过；前端类型/契约/构建通过。重启隔离API后health版本0.1.31-dev.1，本机浏览器70组和3任务批次流程通过。图库图片逐张滚动并decode验证，避免把屏外lazy图片尚未加载误报坏图。独立修复复审已派发，待返回。

普通Admission现将任务与snapshot同事务插入；新增故障注入用例验证无第二次snapshot更新。最新132项全量通过。独立复审已返回，详见REVIEW_FINDINGS.md；仍未全项验收通过。

当前已跑通的范围：浏览器批次3任务→串行Mock会话→图库图片→清理；注入Worker流式生产→清单消费→校验→资产→完成闭环；140项全量及70浏览器组合通过。随后新增状态前置条件回归19项通过。当前仍未完成同Job多attempt、真实Comfy服务、远端云存储适配与整份架构拆分。诊断JSON支持和随机seed固定重试已修。

Admission已迁出main至admission.py；Job校验、快照、估算、LoRA和持久化集中，main仅导入兼容入口。执行准备使用prepare_execution同事务写Job.execution_id、attempt_number和Execution，普通Job也有记录，重复准备幂等。最新全量142通过（111.65s），契约与diff check通过。

同Job多attempt已完成Mock后端闭环：首轮start失败→租约删除→重入queued→新Execution attempt2→completed，原记录保留。POST/GET jobs/{id}/attempts已接入，旧retry兼容。150项全量通过（122.35s）；尚未接attempt UI、未验证生产DB多进程并发和真实GPU重试。

任务页已接Mock同Job重试和执行次数。离线启动器--fail-first-start仅用于隔离Mock故障演示。offline_attempt_browser_check.py用真实页面提交、首次失败后点击重试，断言Job ID不变、两Execution failed/completed、attempt=2、作品1、runtime_deleted。已通过，截图/报告在offline-evidence；曾有一次SCHEDULER_BUSY 503，未掩盖，后续新提交成功。150项全量、构建/契约通过。

补充浏览器故障验收：首个批次POST由Playwright明确模拟503，确认弹窗保留、弹窗内显示错误、按钮恢复，第二次点击复用同一Idempotency-Key后进入真实本地API。随后首次Mock执行失败、同Job重试成功。该注入只验证错误UI，不伪装服务端成功。报告rejected_submit_keeps_dialog_and_key=true，零pageerror/外网请求。测试脚本曾因handler内unroute导致route重复处理失败，已改为计数分支，最终实跑通过。

最新151项全量通过（116.70s）。任务详情公开脱敏Execution attempt历史，浏览器已确认首次失败/第二次完成。transition_job新增expected_execution_id，Scheduler始终传入原execution身份，防止旧attempt回调结束新attempt。独立复审deleg_58bd1b73待返回，未声称批准。

长列表合成数据验收：offline_list_browser_check.py明确替换GET列表响应，200任务/1000作品，首批DOM30/24，加载更多和末项搜索通过；390视口无横溢/页面异常/外网请求，操作段实测0.615s。该测试不代表真实生产API性能、真实作品或真实GPU数据；详见long-list-browser-report.json。
