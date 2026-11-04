"""H3 LoRAs: a short curated SFW list (Hugging Face, pinned) plus the owner's own files in Drive.

Curated files ride along as extra weight files (Drive cache hit after a CPU prep, else Hugging Face).
Own files sit in Drive ``AI-Studio/<owner>/loras/h3`` and are fetched by the runtime at job start.
They are chained as LoraLoaderModelOnly right after the UNET loader, so the Turbo LoRA and the
sigma-shift node keep their place after them.
"""

from __future__ import annotations

import copy

MAX_LORAS = 3
MAX_BYTES = 1_500_000_000
H3_WORKFLOWS = {"minimax-h3-i2v", "minimax-h3-ref2v"}

CURATED = [
    {
        "id": "anime-slider-ref2v",
        "label": "2D 动漫/CG 风格锁定滑块（防偏 3D 真人）",
        "note": "控制整体风格偏向 2D 动漫/CG 或写实，防止二次元 CG 参考图动起来变成 3D/真人感。无需触发词，推荐强度 1.0–3.0（默认 1.5）。",
        "strength": 1.5,
        "workflows": ["minimax-h3-ref2v"],
        "url": "https://huggingface.co/RunningHubAI/rh-minmax-h3-2d-lora",
        "repo_id": "RunningHubAI/rh-minmax-h3-2d-lora",
        "filename": "H3_Ref2V_Anime_Slider_v1.safetensors",
        "revision": "3568edd3d16b8d3c637814b55f628ec4c5cbd121",
        "sha256": "60b0e8523c72a94eb84dca63eaf88bb0f0b844f053c86d8f318c4f8b7d4eac11",
        "size": 77580464,
    },
    {
        "id": "live-wallpaper-r64",
        "label": "CG 动态壁纸 R64（运镜+微动）",
        "note": "适合立绘/CG 微动（发丝、裙摆、光效、呼吸）且镜头听从提示词。提示词建议以 “live_wallpaper: Animate <Picture 1> with ...” 开头；推荐强度 0.8–1.0。",
        "strength": 1.0,
        "workflows": ["minimax-h3-ref2v"],
        "url": "https://huggingface.co/Alissonerdx/Minimax-H3-ComfyUI",
        "repo_id": "Alissonerdx/Minimax-H3-ComfyUI",
        "filename": "loras/minimax_h3_live_wallpaper_v1.0_ref2va_r64.safetensors",
        "revision": "4bb14d4a611179d4cf633e74d3ae33e965ae2301",
        "sha256": "7ea5e75ae1514583a714e10caa7b215c04061c0dc27594284a9d8274b3a53348",
        "size": 620285440,
    },
    {
        "id": "live-wallpaper-r32",
        "label": "CG 动态壁纸 R32（更保五官）",
        "note": "适合单张或多张 CG 微动，五官还原更稳（镜头移动随机性稍高）。提示词建议以 “live_wallpaper: Animate <Picture 1> with ...” 开头；推荐强度 0.8–1.0。",
        "strength": 0.9,
        "workflows": ["minimax-h3-ref2v"],
        "url": "https://huggingface.co/Alissonerdx/Minimax-H3-ComfyUI",
        "repo_id": "Alissonerdx/Minimax-H3-ComfyUI",
        "filename": "loras/minimax_h3_live_wallpaper_v1.0_ref2va_r32.safetensors",
        "revision": "4bb14d4a611179d4cf633e74d3ae33e965ae2301",
        "sha256": "3c9ff5fe4d9adc151d7b52c67fb79250c0012376061b8d44d01ba19ad824c43a",
        "size": 310168936,
    },
    {
        "id": "anti-burn-v1",
        "label": "Anti-Burn（多段续拍防脸崩/防发焦）",
        "note": "用上一段视频尾帧续拍第 2、3、4 段时挂上，防止脸部和线条越来越锐化发焦。第一段不要挂！推荐强度 0.5。",
        "strength": 0.5,
        "workflows": ["minimax-h3-i2v", "minimax-h3-ref2v"],
        "url": "https://huggingface.co/Imagineer13/H3-Anti-Burn-LoRA",
        "repo_id": "Imagineer13/H3-Anti-Burn-LoRA",
        "filename": "h3_antiburn_universal_v1.safetensors",
        "revision": "b98eb1d3f5574d1b8f57d6b0e720d2f047cfadb3",
        "sha256": "8f76dc3c63afa8c556c620b4b7f2ffeeb666f0aa9382a9ce3a07fd6120d0f154",
        "size": 310168728,
    },
    {
        "id": "better-human-motion",
        "label": "Better Human Motion（动作更自然）",
        "note": "人物肢体动作更自然连贯。作者建议 0.4–0.8；提示词写短句，避免串词。训练于 T2V/I2V，二次元 CG 慎叠。",
        "strength": 0.6,
        "workflows": ["minimax-h3-i2v", "minimax-h3-ref2v"],
        "url": "https://huggingface.co/vpakarinen/better-human-motion-h3-lora",
        "repo_id": "vpakarinen/better-human-motion-h3-lora",
        "filename": "better_motion_h3_lora_v1_500.safetensors",
        "revision": "df545ebfbdaf30799196098f6df157ee4388b766",
        "sha256": "285f9ed3fbb735c3625fca0d22166c7450ad7acb552a869fcbbd8e86e79d1f4c",
        "size": 310168816,
    },
    {
        "id": "motion-repair-v2",
        "label": "Motion Continuity Repair V2（防动作崩）",
        "note": "修复转身、翻滚、跳跃等大动作的断裂和慢动作感；原片已经正常时效果不明显。作者建议单独用约 0.9，不要与其他动作 LoRA 混叠。",
        "strength": 0.9,
        "workflows": ["minimax-h3-i2v", "minimax-h3-ref2v"],
        "url": "https://huggingface.co/JOKER141/MiniMax-H3-General-Motion-Continuity-Repair",
        "repo_id": "JOKER141/MiniMax-H3-General-Motion-Continuity-Repair",
        "filename": "Motion_Repair_V2.safetensors",
        "revision": "1676b352bb9f62407f76aee2ad55db2f36d04471",
        "sha256": "edfa0e2858caeba7be9108883f61da8525d3c58af998c94ac7a449d9603d7574",
        "size": 155110272,
    },
    {
        "id": "camera-motion",
        "label": "Camera Motion（运镜）",
        "note": "推近、拉远、环绕、手持跟拍等运镜。提示词必须以 “camera motion,” 开头；作者建议 0.8–1.0，超过 1.2 画面会不稳。",
        "strength": 0.8,
        "workflows": ["minimax-h3-i2v", "minimax-h3-ref2v"],
        "url": "https://huggingface.co/Jojocodex/minimax-h3-Camera-Motion-lora",
        "repo_id": "Jojocodex/minimax-h3-Camera-Motion-lora",
        "filename": "camera_motion_h3_lora_v1_1000_pruned.safetensors",
        "revision": "fac88d6196ca84f3cee014ec79c71dc73572ab0a",
        "sha256": "c126738c887804ace3a4be4a3156fcca70517613c7a7ece4e5938d372419186b",
        "size": 155109488,
    },
    {
        "id": "anime-motion",
        "label": "Anime Motion（二次元小动作，仅图生视频）",
        "note": "二次元风格的循环小动作：眨眼、表情、头发和背景轻动，会让大动作变弱。触发词 “Anime-Motion”；作者没给强度，先试 0.8。仅 I2V。",
        "strength": 0.8,
        "workflows": ["minimax-h3-i2v"],
        "url": "https://huggingface.co/prithivMLmods/MiniMax-H3-I2V-Anime-Motion-LoRA",
        "repo_id": "prithivMLmods/MiniMax-H3-I2V-Anime-Motion-LoRA",
        "filename": "MiniMax-H3-I2V-Anime-Motion-LoRA-1400.safetensors",
        "revision": "ca20427ab7488b80fc3a14363561431e27a8f974",
        "sha256": "28e36c2e62b464b1779fc540ad2dcd6a0ed8a71ea20c6b0ca78745f7f440a2b7",
        "size": 74570616,
    },
]

_PUBLIC = ("id", "label", "note", "strength", "workflows", "url", "size")


def curated_for(workflow_key: str | None) -> list[dict]:
    return [{k: c[k] for k in _PUBLIC} for c in CURATED if workflow_key in c["workflows"]]


def curated_weight(entry: dict) -> dict:
    from pathlib import PurePosixPath

    name = PurePosixPath(entry["filename"]).name
    return {
        "repo_id": entry["repo_id"],
        "filename": entry["filename"],
        "revision": entry["revision"],
        "sha256": entry["sha256"],
        "size": entry["size"],
        "target": f"loras/curated/{name}",
        "lora": True,
    }


def is_lora_file(name: str, size: int | None) -> bool:
    return name.lower().endswith(".safetensors") and size is not None and 0 < int(size) <= MAX_BYTES


def insert_loras(graph: dict, chain: list[tuple[str, float]]) -> dict:
    """UNET -> LoRA 1 -> LoRA 2 -> ... -> (whatever consumed the UNET before: Turbo LoRA, shift, scheduler...)."""
    graph = copy.deepcopy(graph)
    unet = next(k for k, n in graph.items() if n.get("class_type") == "UNETLoader")
    consumers = [(n, inp) for n in graph.values() for inp, v in n["inputs"].items() if v == [unet, 0]]
    source = [unet, 0]
    for i, (name, strength) in enumerate(chain):
        node = str(900 + i)
        graph[node] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": source, "lora_name": name, "strength_model": strength}}
        source = [node, 0]
    for n, inp in consumers:
        n["inputs"][inp] = source
    return graph
