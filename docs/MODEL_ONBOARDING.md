# 新增自定义模型与工作流

除了仓库自带的 4 套内置工作流外，你也可以把自己常用的 ComfyUI 工作流导进来，前端会根据你写的 `manifest` 自动生成手机端表单，不需要改前端代码。

## 导入新工作流的步骤

1. **导出 API 格式 JSON**：在本地或云端 ComfyUI 网页里打开开发者模式，把工作流导出为 API 格式（即每个节点带 `class_type` 和 `inputs` 的字典结构），并从 `/object_info` 保存用到的节点定义。
2. **固定版本与权重**：确认所需的 ComfyUI commit，以及每个模型权重在 Hugging Face 上的 `repo_id`、`revision`（40位 commit）、文件名和 `sha256`。
3. **编写 Manifest**：定义网页表单需要暴露给你的参数（如 `prompt`、`width`、`height`、`seed`、参考图或模式切换），以及它们绑定到哪个节点的哪个输入字段。格式可以直接抄 `api/app/gpu/templates/builtin/` 里的现成 JSON。
4. **导入并审核启用**：在网页“工作流”页粘贴或上传组装好的 JSON bundle 导入为草稿，然后按照 `WORKFLOW_REVIEW.md` 的步骤走完校验、审核、冒烟和发布。

## 常用上游开源地址

- ComfyUI：https://github.com/comfyanonymous/ComfyUI
- ComfyUI 官方工作流模板：https://github.com/Comfy-Org/workflow_templates
- MOSS-Image：https://huggingface.co/OpenMOSS-Team/MOSS-Image-SFT
- Krea-2：https://huggingface.co/Comfy-Org/Krea-2
- MiniMax-H3：https://huggingface.co/Comfy-Org/MiniMax-H3
