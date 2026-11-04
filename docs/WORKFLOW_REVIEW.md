# 真实模型审核、冒烟测试与发布流程

为了防止没验证过的工作流或来路不明的节点直接开 GPU 烧钱，系统要求所有真实模型和工作流走完 **“审核 → 真实冒烟 → 验收 → 发布”** 四步后才能在创作页正式使用。

以下是管理员 API 的具体调用顺序（所有接口都需要管理员登录态）：

## 1. 审核模型

确认模型的仓库来源、40 位 commit 版本号、许可证和硬件要求无误后，调用：

```http
POST /api/gpu/models/{model-row-id}/review
Content-Type: application/json

{"confirmed": true, "license_accepted": true}
```

> 这里的 `{model-row-id}` 是 `GET /api/gpu/models` 返回列表里的记录 `id`。这一步只记录“已审核”，还不会把模型设为启用。

## 2. 审核工作流

先在网页“工作流”页面点一下 **校验**（或者调 `POST /api/gpu/workflows/{workflow-id}/validate`），然后调用：

```http
POST /api/gpu/workflows/{workflow-id}/review
Content-Type: application/json

{"confirmed": true}
```

后端会自动检查：
- `comfyui_commit` 是否为固定的 40 位 commit；
- `custom_nodes` 是否为空，或者是否在 `runtime_worker.py` 已经审计锁定的白名单里；
- 所有 `weight_files` 权重是否都有明确的 Hugging Face 仓库、40 位 revision 和 64 位 SHA-256 校验值；
- 节点图里是否包含危险节点。

## 3. 跑一次受预算保护的真实冒烟 (`real-smoke`)

审核通过后，发起一次小规模真机测试（限时最多 5 分钟）：

```http
POST /api/gpu/workflows/{workflow-id}/real-smoke
Idempotency-Key: smoke-自定义唯一键
Content-Type: application/json

{
  "confirmed": true,
  "project_id": "你的本地项目ID",
  "params": {"prompt": "test"},
  "asset_ids": [],
  "spec": {"key": {"variant": "GPU", "accelerator": "T4", "shape": "STANDARD"}}
}
```

> 如果是图生视频或参考图工作流，记得把参考图上传到该项目，并在 `params` / `asset_ids` 里传对应的素材 ID，显卡 `accelerator` 按需改成 `"L4"` 等你账号支持的规格。

提交后，Worker 会先在 CPU runtime 把环境和模型缓存到你的 Google Drive（如果还没缓存过），然后开 GPU 跑完出片，最后自动删掉 runtime。

## 4. 验收冒烟结果并启用模型 (`accept-smoke`)

等上面的冒烟任务状态变成 `completed`、并且关联的 runtime 租约已经变成 `deleted` 后，调用验收接口：

```http
POST /api/gpu/workflows/{workflow-id}/accept-smoke
Content-Type: application/json

{"job_id": "刚才跑完的冒烟任务ID", "confirmed": true}
```

后端核对作品哈希和 GPU 诊断记录无误后，会自动把该工作流标记为 `smoke_test_passed=true`，并把对应模型正式设为 **`enabled=true`**。

## 5. 正式发布工作流

最后，在网页“工作流”页点击 **确认发布**（或调用 `POST /api/gpu/workflows/{workflow-id}/publish`，传 `{"confirmed": true}`）。

发布完成后，这个版本就会成为当前默认版本，随时可以在手机端“创作”页正常使用了！
