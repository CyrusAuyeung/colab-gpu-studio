# Krea＋MOSS RH 对齐工作流 1.2.0

这是从参考图片内嵌执行图恢复的 27 节点 ComfyUI API 工作流。正向提示词已替换为中性示例。

## 文件

- `workflow-api.json`：原生 API 图，放入 ComfyUI `/prompt` 请求的 `prompt` 字段。
- `models.json`：13 份模型与 LoRA 的文件名、大小、SHA-256，以及插件固定提交。
- `verification.json`：结构核对与离线测试记录。

## 配置

默认 992×992，两段 seed=42。Krea 8 步 / euler / simple / CFG 1 / denoise 1，经过 Krea VAE 解码和 Flux VAE 编码后，MOSS 25 步 / er_sde / simple / CFG 2.5 / denoise 0.36。AuraFlow shift=5。

MOSS 编码器使用 `qwen3.5_9b_bf16.safetensors`。Krea 正向包含 `ConditioningKrea2Rebalance`，balanced、renormalize=true、multiplier=1。Krea TJFilm 为0.90，MOSS Feverdream 为0.70，其余 LoRA 顺序及强度保留参考值。

## 安装与运行

1. 按 `models.json` 将权重放入 ComfyUI 的 `models/` 对应目录；`target` 为相对路径。自行取得模型并核验 SHA-256。
2. 按清单安装 `comfyui-krea2-conditioning` 插件并检出固定提交。ComfyUI 使用已验证的 `423cbd20511becb1f16122ab518e92e58bfa03d9` 提交。
3. 修改节点 `714` 的 `inputs.value` 为自己的提示词；尺寸在 `801`，seed及采样设置在 `202` 和 `235`。
4. 将 API 图提交至本机 ComfyUI `/prompt`，从历史输出取得图片。

本目录不是 Studio 可直接审批的 bundle，也不会自动替换现有内置模板。Studio 使用者需将工作流导入自己的安装，配置参数绑定、权重来源和插件支持，并自行完成审查、缓存准备及真实测试。旧版运行器可能没有该插件白名单支持。

三份需自行取得的权重标注为 `user-provided`；私人 Drive ID 已移除。模型许可各自独立，本仓库不分发模型权重。

## 实测与限制

2026-11-05 在 Colab T4 高内存规格真实跑完 992×992、8＋25 步，图片保存及13份权重来源核验通过，运行时已删除。采样约292秒。相关运行器改动离线测试130项通过、1项跳过；不代表当前仓库增加此目录后进行了新的GPU测试。

使用的是 BF16 权重文件，但 T4 上 ComfyUI 实际将文本编码器计算转为 FP16。RH 服务端模型哈希未取得，且未进行相同提示词的双平台受控 A/B，因此不承诺像素级或画质完全一致。

此目录仅提供工作流与脱敏说明，不包含部署凭据、私人提示词、运行审批或 Google Drive 文件ID。
