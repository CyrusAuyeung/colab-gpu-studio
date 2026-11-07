# v0.2.0 开发验收记录

开发分支：`feat/v0.2.0-studio`。
基线提交：`2e37ca048f03bfdb2c6b6aebcf722edce4a84406`。
范围：完整规格见 `docs/specs/GPU_Studio_v0.2.0.md`。

## 改造前基线

- `uv sync --extra test`：成功。
- `STUDIO_OFFLINE_TEST=1 GPU_REAL_ENABLED=0 uv run pytest -q`：221 passed，2 warnings，178.48s。
- `STUDIO_OFFLINE_TEST=1 GPU_REAL_ENABLED=0 uv run ruff check api tests`：失败，原有 `tests/gpu/test_restore_skip.py` I001 导入排序问题 1 处。
- 原测试警告：Starlette TestClient/httpx 兼容弃用提示；旧批次交接测试中空 execution_id 引起 SQLAlchemy warning。

## 开发边界

- 不修改生产 `.env`，不连接生产数据库，不部署，不开真实 GPU，不消耗 CU。
- 外部 Google 传输由离线测试替身隔离；Mock 验证不能证明真实 GPU、Google OAuth、Comfy 常驻效果可用。
- 提交署名沿用仓库原有 `TizenryA`；文档只使用本地地址或占位域名。
- 原工作目录的未提交文件不动，独立 worktree 开发。

## 已执行的离线验收

- 后端全仓回归：282 passed、2 warnings（235.13s）；此结果早于下列浏览器追加修复，不能当作追加修复后的全仓结论。
- 前端 `npm run build` 成功；Node 契约与时间格式测试 12 passed。
- Ruff 与 `git diff --check` 通过。
- 追加回归：未授权 Google 目录下 Mock 分配、Mock 账号 ID 往返、跨进程 Mock handle 恢复已分别先失败后通过。
- 390px 手机浏览器：账号创建、刷新持久化、实例列表、详情和任务历史已验证。
- 手机浏览器通过实际 API 提交 Mock retain 任务，由独立 worker 执行完成；点击续用后第二任务仍使用相同实例，详情同时列出两个 completed 任务。
- 停止先显示 releasing；独立 watchdog 运行后数据库记录 deleted 及 deletion_confirmed_at，手机刷新显示“已删除”，无横向溢出。
- 修复批次 idle_seconds 超过 300 导致的 422；子任务 retain_idle_minutes 保持用户设置，不以批次短闲置参数替代。
- 修复 Unix 秒时间戳按毫秒显示为 1970 年的问题。

- 工作台请求字段已与HTTP统一；手机实际保存参数、读取 Graph、提交原实例任务成功，任务 completed 并持久化产物。
- 重开浏览器读取同一实例：保存的 prompt 恢复，GET 返回 2 个产物及 27 条关联日志；手机产物/日志页实际显示且无横向溢出。
- GET capabilities 与按钮权限统一，缺能力时写按钮禁用；历史实例增加只读工作台入口。
- retention HTTP 和输入上限统一为 30 分钟，拒绝不支持的 release 模式。
- 本轮后端工作台/HTTP/接线/保留测试 24 passed；迁移与列同步测试 4 passed；前端构建及 12 项测试通过；Ruff/diff 通过。

## 生产追加验收（2026-11-07，用户另行授权）

- 已授权部署及单次真实生成；不自动重复创建付费任务。
- 生产 API 原 384MiB 限制触发 OOM，单独提高至 1GiB，worker/watchdog 保留原限制。
- 历史账号沿用既有授权与原站每日/月度预算，恢复允许分配。
- 修复账号目录 projects=[] 与分配器语义不一致：空列表不限项目；非空列表仍限制项目。相关实例测试 23 passed，实例/生命周期/调度器相关 27 passed。
- 单次真实任务已通过 HTTP 入队、账号项目检查、Colab 分配、环境恢复、真实采样、本站产物保存与公网鉴权下载。任务 completed，输出 PNG 729570 bytes，下载 SHA-256 与保存记录一致。
- 本次环境恢复 31.8s、权重恢复 465.1s、Comfy 启动 30.0s、采样 242.6s。未更换原工作流权重或精度。
- 实例与租约均 deleted，runtime_deleted=true，deletion_confirmed_at 已持久化；独立调用 Provider inspect 确认远端 deleted=true。
- 本次只验证单任务 save 生命周期，不能推导真实 retain、批次复用及受控工作台所有动作已通过。

- 真实保留（Retain）与实例复用已完成生产真机复验：
  - 修复 `InstanceService.complete()` 未在后续任务完成时刷新 `idle_deadline_at` 且忽略 `retain_idle_minutes` 的问题，并在 `_claim_existing` 中清空旧 `idle_deadline_at`。
  - 修复保留实例释放后未写入 `gpu_instance_usage_segments` 的问题（`segment_key = instance-lifetime:{instance_id}`，`actual_units = null`）。
  - 修复 `admission_lock` 被 `dispatch_lock` 大快照读取阻塞及 `Policy.model_dump()` 新旧生命周期字段冲突问题。
  - 修复设置页与算力页移动端 UI（`v020.module.css` / `InstanceCenter.tsx` / `AccountDirectory.tsx`）：实例卡片改为 100% 单列、统计栏改为 3 列等宽、UUID 短哈希展示、修复实例详情返回巨型 `snapshot` 导致的加载缓慢。
  - 6 张海报实验（艾莲 2 张、白露 2 张、霍霍 2 张）全部 `completed` 并落盘可下载；其中实例 `#ae0cd856` 连续复用完成前 4 张，修复后实例 `#a37fba09` 完成第 5、6 张（第 6 张完成后 `idle_deadline_at` 成功顺延刷新至 30 分钟后），并通过手机端设置页实例中心点击「停止」触发释放，Watchdog 确认远端 `deleted=true`，生成闭合用量账本段 `bf43dc9b-af27-5fbe-a995-ec8f9bbe736d`（总时长 20.41 分钟，估算 0.612 CU）。当前线上活动任务 0、活动实例 0。

## 尚未完成的验收

- 独立审查任务超时，不能标记审核通过。
- 工作台所有动作的移动端完整交互、PostgreSQL 并发/迁移回退验收尚未完成。
- 已按用户授权部署生产并完成单任务与 Retain 复用及手动停止关机真机实验；批次（batch）与智能（smart）真机模式未单独开机测试。
