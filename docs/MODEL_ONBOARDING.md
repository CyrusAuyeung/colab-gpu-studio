# 模型与工作流接入

本源码包不导出生产数据库，干净启动只自动注册内置 `demo-mock` 工作流；它仅演示任务调度，不是实际图像模型。真实工作流不会因为后端含 H3 处理逻辑就自动出现在新安装中。

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

第三方模型元数据和许可证会变化，请使用前自行核验。生产站工作流配置不包含在本包，需另行制作仅含 graph/manifest/object_info 的公开模板，不能上传整库来补齐。
