# 真实模型审查、GPU 冒烟与发布流程

本流程把“模型永远 disabled、又必须先 smoke 才能 enabled”的闭环拆成三个不可伪造的服务器状态：人工审查、受预算保护的冒烟任务、以及基于任务结果的验收。

## 1. 模型人工审查

管理员调用：

```http
POST /api/gpu/models/{model-row-id}/review
Content-Type: application/json

{"confirmed": true, "license_accepted": true}
```

服务端重新检查已登记模型的：

- `source` 必须是 Hugging Face 或 GitHub 的 HTTPS 地址；
- `revision` 必须是固定 40 位 commit；
- `license` 非空；
- `resource_profile` 至少含有限、正数且在合理范围内的资源值。

审查成功只写入 `review_approved`、`license_accepted` 等审查记录，**不会**设置 `enabled=true`。Agent token 不能调用这些管理员接口。

## 2. 工作流人工审查

模型审查成功后，管理员调用：

```http
POST /api/gpu/workflows/{workflow-id}/review
{"confirmed": true}
```

工作流必须仍是 `draft` 或 `validated`。服务端重新运行 `validate_manifest`，然后检查：

- `manifest.dependencies.comfyui_commit` 是固定 40 位 commit；
- `manifest.dependencies.custom_nodes` 必须为空或命中 `runtime_worker.py` 中固定仓库/commit 审查名单；
- 每个 `weight_files` 都有合法 Hugging Face `repo_id`、无路径穿越的 `filename`/`target`、40 位 revision 和 64 位 SHA-256；
- 图中所有 `class_type` 都进入服务器生成的 `node_allowlist`，危险节点直接拒绝。

审查结果写回工作流的 manifest 和 `review_approved`，但不会把工作流发布，也不会把 manifest 的 state 改成 `published`。依赖会同时规范化到适配器使用的顶层 `comfyui_commit`、`comfy_commit` 和 `weight_files` 字段。

## 3. 受预算保护的真实冒烟入队

审查完成后，管理员提交：

```http
POST /api/gpu/workflows/{workflow-id}/real-smoke
Idempotency-Key: real-smoke-2026-...
Content-Type: application/json

{
  "confirmed": true,
  "project_id": "project-id",
  "params": {"prompt": "test"},
  "asset_ids": [],
  "spec": {"key": {"variant": "GPU", "accelerator": "T4", "shape": "STANDARD"}}
}
```

入队前服务端按以下顺序检查：

1. `GPU_REAL_ENABLED`、正费率、单任务/日/月预算；预算超限时**不会创建 job**；
2. Google Colab OAuth 已连接；
3. 通过真实 `list_specs()` 获取当前账户规格，且提交的 key 必须精确匹配 `eligible=true` 的 GPU 规格；
4. 工作流和模型审查仍有效，manifest 与参数可验证，项目和素材归属正确；
5. 最长运行时间固定为 `min(5, GPU_MAX_RUNTIME_MINUTES)`。

通过后，路由只在 dispatch lock 内用 `Repository.create_job` 持久化原始请求和不可变 snapshot，随后交给 Scheduler。路由不调用 provider 的 create，不直接开机。snapshot 包含已审工作流的发布态运行副本、节点白名单、锁定的 Comfy commit/权重、`workflow_smoke=true`、目标 workflow/model ID 和 `system_smoke=false`。

相同幂等键可安全重试；不同参数会得到 `IDEMPOTENCY_CONFLICT`。

## 4. 服务器证据验收

GPU 任务结束、输出保存并校验、租约确认删除后，管理员调用：

```http
POST /api/gpu/workflows/{workflow-id}/accept-smoke
{"job_id": "server-job-id", "confirmed": true}
```

服务端只接受同一 owner 的服务器 job，并严格要求：

- `completed`、`provider=colab`、`snapshot.workflow_smoke=true`；
- `target_workflow_id` 和 `target_model_id` 精确匹配；
- `output_asset_ids` 非空，每个文件存在且重新计算 SHA-256/size 一致；
- 所有关联 lease 已是 `deleted`；
- job/lease 中存在服务器保存的真实 GPU 诊断证据。

请求体不接受输出文件、测试结果或其它客户端证据。成功后写入 `smoke_test_passed=true`、`smoke_test_kind=real`，只把 `job.requested_spec` 和服务器 GPU 诊断复制到 `model.tested_compute_specs`，并将模型设为 `enabled=true`。

## 5. 发布边界

冒烟验收不是发布。发布仍必须经过现有 Registry 发布检查和管理员确认；真实模型必须同时具备模型许可审查、工作流审查、真实冒烟验收及固定节点白名单。已发布 workflow 版本不可通过审查或验收接口覆写。

常规正式任务仍应由主 admission 路径根据已审真实模型构造 `state=published`、`reviewed=true` 的后端 snapshot，不能信任客户端 manifest。
