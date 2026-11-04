# 模型与工作流接入

本源码包不导出生产数据库，干净启动保留 `demo-mock` 演示，并额外为当前用户自动注册 4 套真实工作流草稿；只有 Mock 能直接运行，真实模型默认禁用。详见 `BUILTIN_WORKFLOWS.md`。

## 接入步骤

1. 核对模型及 LoRA 的来源、许可、设备要求。
2. 固定 ComfyUI/依赖仓库的 commit。
3. 准备 API graph、目标节点 object_info 与 manifest。
4. 固定权重 repo/revision/文件名/SHA-256。
5. 从工作流页或管理员 API 导入 bundle。
6. 通过静态校验、人工审查和预算核验。
7. 自行授权一次最小真实冒烟并核验输出。
8. 确认 runtime 删除后验收并发布。

具体接口和发布条件见 `WORKFLOW_REVIEW.md`，以当前代码检查为准。不要伪造 smoke 记录、复制他人的发布凭据或跳过许可审核。

## 公共来源

- ComfyUI：https://github.com/comfyanonymous/ComfyUI
- 官方工作流模板：https://github.com/Comfy-Org/workflow_templates
- MOSS：https://huggingface.co/OpenMOSS-Team/MOSS-Image-SFT
- Krea：https://huggingface.co/krea/Krea-2-Turbo
- H3：https://huggingface.co/MiniMaxAI/MiniMax-H3

第三方模型元数据和许可证会变化，请使用前自行核验。当前 4 套纯配置已纳入内置模板目录，审批与实测记录未导出；增加其他工作流时仍只导出纯配置，不能上传整库。
