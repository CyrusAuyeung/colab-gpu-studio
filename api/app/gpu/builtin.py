"""Offline, owner-scoped built-in workflow drafts; never carry execution approval."""

from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from pathlib import Path

from .manifest import validate_manifest

_ROOT = Path(__file__).with_name("templates") / "builtin"
_MODEL_FIELDS = {
    "model_id", "display_name", "revision", "source", "license", "modality", "resource_profile", "variants",
}


def _bundle_loras(filename: str, bundle: dict, manifest: dict) -> None:
    """A bundled template may only reference weights this repository actually ships.

    The runtime refuses to start a job whose graph names a LoRA that the manifest does not
    bring in (``_check_loras``), so a bundle that skips this check dies after a GPU runtime
    has already been allocated.  Fail at import time instead, and never let a bundle point at
    a private Drive file id — every install has a different Drive.
    """
    weights = manifest["dependencies"].get("weight_files") or []
    for item in weights:
        if item.get("source") == "drive":
            raise ValueError(f"内置模板 {filename} 不允许引用私人 Drive 权重：{item.get('target')}")
    shipped = {w.get("target", "").removeprefix("loras/") for w in weights}
    for node_id, node in bundle["api_graph"].items():
        if isinstance(node, dict) and node.get("class_type") == "LoraLoaderModelOnly":
            name = node.get("inputs", {}).get("lora_name")
            if name not in shipped:
                raise ValueError(f"内置模板 {filename} 的节点 {node_id} 引用了未随包下发的 LoRA：{name}")


@lru_cache(maxsize=1)
def _bundles() -> tuple[dict, ...]:
    catalog = json.loads((_ROOT / "catalog.json").read_text(encoding="utf-8"))
    result = []
    identities = set()
    for entry in catalog["workflows"]:
        filename = entry["file"]
        if not isinstance(filename, str) or not re.fullmatch(r"[a-z0-9.-]+\.json", filename):
            raise ValueError("内置模板文件名不安全")
        bundle = json.loads((_ROOT / filename).read_text(encoding="utf-8"))
        manifest = validate_manifest(bundle["manifest"], bundle["api_graph"], bundle["object_info"])
        identity = (bundle["workflow_key"], manifest["version"])
        if identity != (entry["workflow_key"], entry["version"]) or identity in identities:
            raise ValueError("内置模板目录身份不一致或重复")
        if bundle["model_id"] != manifest["model_id"] or bundle["model_id"] != bundle["model"]["model_id"]:
            raise ValueError("内置模板模型身份不一致")
        identities.add(identity)
        bundle["manifest"] = manifest
        _bundle_loras(filename, bundle, manifest)
        result.append(bundle)
    return tuple(result)


def seed_builtin_templates(registry, owner_id: str) -> None:
    """Called under Registry.bootstrap's existing cross-process dispatch lock.

    Add only absent records. Existing approvals, drafts, published versions and
    active pointers are not touched. Import never creates jobs or runtimes.
    """
    bundles = _bundles()
    known_models = {row.get("model_id") for row in registry.models(owner_id)}
    known_workflows = {
        (row.get("workflow_key"), row.get("workflow_version")) for row in registry.workflows(owner_id)
    }
    for bundle in bundles:
        model_id = bundle["model_id"]
        if model_id not in known_models:
            model = {key: copy.deepcopy(value) for key, value in bundle["model"].items() if key in _MODEL_FIELDS}
            model.update({
                "enabled": False,
                "is_mock": False,
                "disabled_reason": "内置模板尚未在当前安装完成许可审查和真实冒烟，不会自动启用。",
            })
            registry.repo.create("models", owner_id, model)
            known_models.add(model_id)
        identity = (bundle["workflow_key"], bundle["manifest"]["version"])
        if identity not in known_workflows:
            registry.import_workflow(owner_id, copy.deepcopy(bundle))
            known_workflows.add(identity)
