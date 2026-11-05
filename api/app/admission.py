"""Job admission domain service: validate, freeze inputs, estimate and persist."""
import copy

from .common import APIError, own
from .gpu import loras as lora_registry
from .gpu.compute import GpuConfig
from .gpu.inputs import resolve_input_images
from .gpu.manifest import bind_parameters, digest, resolve_parameters, validate_manifest
from .gpu.rates import estimate as estimate_units
from .gpu.rates import length_factor, mode_param
from .gpu.registry import Registry
from .gpu.repository import Repository


def repo_registry(st, user):
    repo = Repository(st)
    registry = Registry(repo)
    registry.bootstrap(user["id"])
    return repo, registry


def _job_estimate(repo, owner, workflow, policy, cfg, params) -> dict:
    """Shown estimate is realistic; budget_units keeps reserving the max-runtime worst case."""
    if policy["provider"] == "mock":
        return {"estimated_units": 0.0, "unit_rate": cfg.unit_rate}
    cap = min(policy["max_runtime_minutes"], cfg.max_runtime_minutes)
    manifest = workflow["manifest"]
    picked = mode_param(manifest)
    mode = params.get(picked[0]) if picked else None
    units, rate = estimate_units(repo, owner, workflow["workflow_key"], manifest, manifest.get("compute_spec"), cap, mode=mode)
    units = round(min(units * length_factor(manifest, params), cap * rate), 3)
    return {"estimated_units": units, "unit_rate": rate, "budget_units": cap * cfg.unit_rate}


def admit_job(st, user, body, key, *, validate_only=False):
    repo, registry = repo_registry(st, user)
    if not key or len(key) > 200:
        raise APIError("IDEMPOTENCY_REQUIRED", "必须提供 Idempotency-Key，重试必须沿用原值")
    existing = next((j for j in repo.list("jobs", user["id"]) if j.get("idempotency_key") == key), None)
    if existing:
        if existing.get("body_hash") != digest(body):
            raise APIError("IDEMPOTENCY_CONFLICT", "相同幂等键不能用于不同参数", 409)
        return existing
    project = own(st, "projects", body["project_id"], user)
    cfg = GpuConfig.from_env()
    policy = body["execution_policy"]
    if policy["provider"] not in {"mock", "colab"}:
        raise APIError("PROVIDER_INVALID", "仅允许已配置的 mock 或 colab Provider")
    if policy["provider"] == "colab" and not cfg.real_enabled:
        raise APIError("REAL_GPU_DISABLED", "真实 GPU 默认关闭：请先配置 OAuth、费率预算并完成模型审查")
    if not policy["shutdown_after_job"] or policy["idle_grace_minutes"] != 0:
        raise APIError("POLICY_RESTRICTED", "当前版本强制任务后释放 GPU，不允许客户端延长闲置")
    workflow = registry.get_workflow(user["id"], body["workflow_key"], body["workflow_version"])
    if workflow.get("state") != "published" or workflow["model_id"] != body["model_id"]:
        raise APIError("WORKFLOW_UNPUBLISHED", "工作流未发布或与模型不匹配")
    model = next((m for m in registry.models(user["id"]) if m["model_id"] == body["model_id"]), None)
    if not model or not model.get("enabled"):
        raise APIError("MODEL_UNAVAILABLE", "模型尚未通过许可、权重与硬件验证")
    if bool(model.get("is_mock")) != (policy["provider"] == "mock"):
        raise APIError("EXECUTOR_MISMATCH", "模拟模板只能使用 Mock，真实模型不能伪装模拟成功")
    validate_manifest(workflow["manifest"], workflow["api_graph"], workflow["object_info"])
    preset = own(repo, "presets", body["preset_id"], user) if body.get("preset_id") else None
    if preset and preset["workflow_version_id"] != workflow["id"]:
        raise APIError("PRESET_INCOMPATIBLE", "预设属于不同工作流版本，请先迁移预设")
    params = resolve_parameters(
        workflow["manifest"], body["params"], preset.get("overrides") if preset else None
    )
    bind_params, input_files = resolve_input_images(
        lambda aid: own(st, "assets", aid, user), project["id"], workflow["manifest"], params
    )
    bound = bind_parameters(workflow["manifest"], workflow["api_graph"], bind_params)
    for asset_id in body["asset_ids"]:
        asset = own(st, "assets", asset_id, user)
        if asset["project_id"] != project["id"]:
            raise APIError("ASSET_PROJECT_MISMATCH", "参考素材不属于当前项目")
    snapshot = {
        "manifest": workflow["manifest"],
        "api_graph": workflow["api_graph"],
        "object_info": workflow["object_info"],
        "resolved_params": params,
        "bound_graph": bound,
        "input_files": input_files,
        "workflow_sha256": workflow["sha256"],
        "model_revision": model.get("revision"),
        "dependency_lock": workflow["manifest"].get("dependencies", {}),
    }
    if policy["provider"] == "colab":
        if not workflow.get("review_approved") or workflow.get("smoke_test_kind") != "real":
            raise APIError("REVIEW_REQUIRED", "真实工作流尚未通过后台审核及真实冒烟")
        snapshot["manifest"] = {**snapshot["manifest"], "state": "published", "reviewed": True}
        if cfg.unit_rate is None:
            raise APIError("RATE_UNKNOWN", "未设置可信的估算费率，拒绝开机")
        from .gpu.google import GoogleService

        status = GoogleService(repo, user["id"]).status()
        if not status["colab"]["connected"]:
            raise APIError("GOOGLE_NOT_CONNECTED", "请先授权个人 Google Colab 账户")
    # After the published stamp, so the curated LoRA weight files stay in the manifest the runtime restores.
    if body.get("loras"):
        _apply_loras(repo, user, workflow, body["loras"], snapshot, policy["provider"])
    if validate_only:
        return {**body, "snapshot": snapshot, "params": params,
                "resolved_seed": params.get("seed"),
                "max_runtime_minutes": min(policy["max_runtime_minutes"], cfg.max_runtime_minutes),
                **_job_estimate(repo, user["id"], workflow, policy, cfg, params)}
    # A worker cannot observe a queued record without its immutable snapshot.
    with st.admission_lock() as acquired:
        if not acquired:
            raise APIError("SCHEDULER_BUSY", "队列正在同步，请用相同幂等键稍后重试", 503, True)
        job, created = repo.create_job(user["id"], body, key, admitted={
            "snapshot": snapshot,
            "params": params,
            "resolved_seed": params.get("seed"),
            "atelier_project_id": project.get("atelier_project_id"),
            "max_runtime_minutes": min(policy["max_runtime_minutes"], cfg.max_runtime_minutes),
            **_job_estimate(repo, user["id"], workflow, policy, cfg, params),
        })
        if created:
            repo.event(job["id"], user["id"], "queued", "任务已持久化；手机关闭不会中断队列")
    return job


def _apply_loras(repo, user, workflow, picks, snapshot, provider):
    """Curated picks become extra weight files; own picks are Drive files the runtime fetches."""
    if workflow["workflow_key"] not in lora_registry.H3_WORKFLOWS:
        raise APIError("LORA_UNSUPPORTED", "这个工作流不支持自定义 LoRA")
    curated = {c["id"]: c for c in lora_registry.CURATED if workflow["workflow_key"] in c["workflows"]}
    own_files = None
    chain, weights, user_files = [], [], []
    for pick in picks:
        ref, strength = str(pick.get("id") or ""), pick.get("strength", 0.8)
        if not isinstance(strength, int | float) or not -3.0 <= strength <= 3.0:
            raise APIError("VALIDATION_ERROR", "LoRA 强度需在 -3.0 到 3.0 之间")
        if ref in curated:
            weight = lora_registry.curated_weight(curated[ref])
            weights.append(weight)
            chain.append((weight["target"].removeprefix("loras/"), float(strength)))
            continue
        if own_files is None:
            own_files = {} if provider == "mock" else {f["id"]: f for f in _drive(repo, user).list_loras()}
        found = own_files.get(ref)
        if not found or not lora_registry.is_lora_file(found["name"], found["size"]):
            raise APIError("LORA_NOT_FOUND", "LoRA 文件不在 Drive 的 loras/h3 文件夹里，或不是 1.5GB 以内的 .safetensors")
        name = f"user/{found['id']}_{found['name']}"
        user_files.append({"id": found["id"], "name": found["name"], "size": found["size"], "md5Checksum": found["md5"], "target": name})
        chain.append((name, float(strength)))
    snapshot["bound_graph"] = lora_registry.insert_loras(snapshot["bound_graph"], chain)
    manifest = copy.deepcopy(snapshot["manifest"])
    manifest["weight_files"] = [*(manifest.get("weight_files") or []), *weights]
    allow = manifest.get("node_allowlist")
    if isinstance(allow, list) and "LoraLoaderModelOnly" not in allow:
        allow.append("LoraLoaderModelOnly")
    snapshot["manifest"] = manifest
    snapshot["lora_files"] = user_files
    snapshot["loras"] = [{"id": p.get("id"), "strength": p.get("strength", 0.8)} for p in picks]


def _drive(repo, user):
    from .gpu.drive import DriveStorage

    return DriveStorage({"owner_id": user["id"]}, repo)
