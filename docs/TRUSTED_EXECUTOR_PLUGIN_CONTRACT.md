# 管理员可信执行器插件最小契约（#12）

> 这是部署侧加载路径和离线契约，不是真实 GPU/Laya 基准。仓库当前没有启动 GPU、远端 Laya 服务或通用远端插件协议。

## 边界与接缝

现有 `Scheduler` 是租约、预算、取消和远端 runtime 删除的唯一权威：

- `executor_factory(job)` 在 `api/app/gpu/compute.py` 中选择执行器；调度器随后调用 `start(handle, job, execution_id)` 和 `poll(handle, job)`。
- `Scheduler.cancel()` 只写入取消意图；下一 tick 进入 `cleaning_up`，由现有 provider `inspect/delete` 完成租约清理。
- 因为任务要求不改 `scheduler.py`、`repository.py`、`review_routes.py`，插件的 `cancel()`/`cleanup()` 是受同一租约身份、预算快照和截止时间约束的部署适配接缝，不能自行分配预算、创建租约或删除 runtime。现有调度器不会在本改动中偷偷替换这条权威路径。

因此，本实现完成了可测试的部署侧加载路径；通用远端 Laya 控制面仍需在该四方法接缝上提供真实实现和独立验收。

## 管理员配置

插件默认关闭。插件选择必须由已准入 Job 或 immutable snapshot 显式声明 `executor_type`；普通 Colab 任务不会因为管理员启用了插件而自动切换到插件，也不会默认切换到 MCP。`GPU_TRUSTED_EXECUTOR_TYPE` 注册一个管理员允许的 selector，Job/snapshot 的值必须与它精确一致；未知类型或 Job 与 snapshot 冲突会被拒绝。内置 `system-cuda-smoke` 与 CUDA prep 任务始终走 MCP，因为它们没有模型 revision。

插件选择只来自管理员环境变量，Job/Batch JSON 没有 module、entrypoint、script 或 URL 字段，也不会读取客户端传入的同名字段。

```dotenv
GPU_REAL_ENABLED=1
GPU_TRUSTED_EXECUTOR_ENABLED=1
GPU_TRUSTED_EXECUTOR_TYPE=trusted
GPU_TRUSTED_EXECUTOR_MODULE=deployment.laya_executor
GPU_TRUSTED_EXECUTOR_ENTRYPOINT=create_plugin
GPU_TRUSTED_EXECUTOR_VERSION=1.0.0
GPU_TRUSTED_EXECUTOR_ALLOWLIST=deployment.laya_executor:create_plugin@1.0.0
GPU_TRUSTED_EXECUTOR_CACHE_NAMESPACE=laya-reviewed
```

允许名单必须与选择项精确匹配；模块、entrypoint、版本均校验。插件必须报告：

```python
contract_version = "1"
plugin_version = "1.0.0"
```

并实现四个方法：`start(payload) -> dict`、`poll(payload) -> dict`、`cancel(payload) -> dict`、`cleanup(payload) -> dict`。版本不是精确允许项、契约版本不兼容、方法缺失或导入失败都会 fail closed。

## 输入 JSON schema（概念稳定字段）

插件收到 `trusted-executor-input-v1`，只包含从已准入 immutable job snapshot 选择出的字段：

```json
{
  "schema": "trusted-executor-input-v1",
  "contract_version": "1",
  "operation": "start",
  "executor_type": "trusted",
  "execution_id": "execution-id",
  "job_id": "job-id",
  "owner_id": "owner-id",
  "lease": {
    "runtime_name": "runtimes/verified-runtime",
    "runtime_id": "runtime-id",
    "request_id": "request-id"
  },
  "deadline_epoch": 4000000000,
  "model": {"id": "laya-demo", "revision": "reviewed-model-revision"},
  "workflow": {"key": "laya-json", "version": "1.0.0"},
  "parameters": {"prompt": "hello", "seed": 7},
  "cache": {
    "namespace": "laya-reviewed/…model-hash/…revision-hash",
    "model_revision": "reviewed-model-revision"
  },
  "budget": {"reserved_units": 1.0, "estimated_units": 1.0},
  "lifecycle": {
    "cancel_requested": false,
    "requested_outcome": null,
    "cleanup_final_state": null
  },
  "plugin": {
    "module": "deployment.laya_executor",
    "entrypoint": "create_plugin",
    "version": "1.0.0"
  }
}
```

`model_revision` 是强制字段；缓存 namespace 由服务端生成，包含管理员 namespace 和 model/revision 的摘要，插件输出必须原样回显。参数和输出 metadata 中的 `module`、`script`、`url`、`python`、`shell`、`command` 等字段会被拒绝。插件不会收到完整 Comfy graph、Python 源码、URL 或凭据。

## 输出 JSON schema

每次返回都必须是：

```json
{
  "schema": "trusted-executor-output-v1",
  "contract_version": "1",
  "execution_id": "execution-id",
  "state": "running|completed|failed|cancelled|cancelling|cleaned|cleanup_pending",
  "cache": {
    "namespace": "laya-reviewed/…model-hash/…revision-hash",
    "model_revision": "reviewed-model-revision"
  },
  "outputs": [
    {
      "filename": "result.json",
      "mime": "application/json",
      "content_base64": "eyJvayI6dHJ1ZX0=",
      "metadata": {"trusted": true}
    }
  ],
  "timings": {}
}
```

服务端检查 schema、契约版本、execution identity、cache namespace/revision、文件名、base64、metadata 和 100MB/100 项上限，再转换为现有 scheduler 输出协议。错误或不一致不会被当作成功。

## 可信 JSON 批任务

`docs/examples/trusted_executor_batch.json` 是可审阅的批任务请求示例。它只声明现有 API 允许的 provider/workflow/model/params/lifecycle；插件的 Python 模块仍由管理员环境配置决定。真实提交还需要已发布工作流、已审查模型 revision、OAuth、预算和 `GPU_REAL_ENABLED=1`，不能用该 JSON 证明 GPU 或 Laya 已运行。

## 可运行的非 Comfy 离线插件示例

`api/app/gpu/newgpu/example_executor.py` 是一个可实际加载的、非 Comfy 的可信插件示例。它只生成明确标记为 `trusted-executor-offline-fixture` 的 JSON 字节；`offline=true`、`network_used=false`、`model_invoked=false` 表明这不是 GPU、Laya 或任何模型的结果，也不会发起网络请求。管理员可在临时环境中这样选择它：

```dotenv
GPU_REAL_ENABLED=1
GPU_TRUSTED_EXECUTOR_ENABLED=1
GPU_TRUSTED_EXECUTOR_MODULE=app.gpu.newgpu.example_executor
GPU_TRUSTED_EXECUTOR_ENTRYPOINT=create_plugin
GPU_TRUSTED_EXECUTOR_VERSION=1.0.0
GPU_TRUSTED_EXECUTOR_ALLOWLIST=app.gpu.newgpu.example_executor:create_plugin@1.0.0
GPU_TRUSTED_EXECUTOR_CACHE_NAMESPACE=offline-example
```

对应的 `tests/gpu/test_executor_example.py` 通过 `GpuConfig.from_env()` 和 `default_executor_factory()` 加载管理员 allowlist，实际执行 `start`、`poll`（读取并解析真实 JSON bytes）、`cancel`、`cleanup` 全生命周期。测试不启动 GPU、不连接网络，产物仅用于验证本契约的 JSON/base64/生命周期接缝。

## 离线验证

```bash
.venv/bin/pytest tests/gpu/test_executor_plugins.py -q
.venv/bin/ruff check api/app/gpu/compute.py api/app/gpu/newgpu/executor_plugins.py tests/gpu/test_executor_plugins.py
```

测试使用内存中的管理员模块，实际经过 `default_executor_factory` 导入、实例化、`start/poll` 调用和 `cancel/cleanup` 契约校验；不启动 GPU、不联网、不声称真实 Laya 性能。
