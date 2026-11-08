from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest
from app import review_routes
from app.config import settings
from app.db import Store
from app.gpu.repository import Repository
from app.storage import save_asset

OWNER = {"id": "owner", "role": "admin"}
COMMIT = "a" * 40
REVISION = "b" * 40
SHA256 = "c" * 64
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def request(headers=None):
    return SimpleNamespace(headers=headers or {}, state=SimpleNamespace(request_id="req-1"))


def body(response):
    return json.loads(response.body)["data"]


def valid_model(repo: Repository, *, model_id="real-model"):
    return repo.create(
        "models",
        OWNER["id"],
        {
            "model_id": model_id,
            "source": "https://huggingface.co/acme/model",
            "revision": REVISION,
            "license": "apache-2.0",
            "resource_profile": {"vram_gb": 8, "ram_gb": 16},
            "enabled": False,
            "is_mock": False,
        },
    )


def valid_workflow(repo: Repository, model_id="real-model", *, state="validated"):
    manifest = {
        "manifest_version": 1,
        "model_id": model_id,
        "parameters": {
            "prompt": {"type": "string", "default": "hello", "binding": {"node": "1", "input": "text"}}
        },
        "dependencies": {
            "comfyui_commit": COMMIT,
            "custom_nodes": [],
            "weight_files": [
                {
                    "repo_id": "acme/model",
                    "filename": "model.safetensors",
                    "revision": REVISION,
                    "sha256": SHA256,
                    "target": "checkpoints/model.safetensors",
                }
            ],
        },
    }
    graph = {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"text": "hello"}}}
    info = {"CheckpointLoaderSimple": {"input": {"required": {"text": ["STRING"]}}, "output": ["IMAGE"]}}
    return repo.create(
        "workflows",
        OWNER["id"],
        {
            "workflow_key": "real-demo",
            "workflow_version": 1,
            "model_id": model_id,
            "manifest": manifest,
            "api_graph": graph,
            "object_info": info,
            "state": state,
            "smoke_test_passed": False,
        },
    )


def test_model_review_approves_source_without_enabling(monkeypatch):
    st = Store("sqlite:///:memory:")
    repo = Repository(st)
    row = valid_model(repo)

    result = review_routes.review_model(
        row["id"], {"confirmed": True, "license_accepted": True}, request(), OWNER, st
    )

    reviewed = body(result)
    assert reviewed["review_approved"] is True
    assert reviewed["license_accepted"] is True
    assert reviewed["enabled"] is False


def test_workflow_review_locks_dependencies_and_allows_all_graph_nodes():
    st = Store("sqlite:///:memory:")
    repo = Repository(st)
    model = valid_model(repo)
    model = repo.update(
        "models", model["id"], {"review_approved": True, "license_accepted": True}, OWNER["id"]
    )
    workflow = valid_workflow(repo, model["model_id"])

    result = review_routes.review_workflow(workflow["id"], {"confirmed": True}, request(), OWNER, st)

    reviewed = body(result)
    assert reviewed["review_approved"] is True
    assert reviewed["manifest"].get("state") != "published"
    assert reviewed["manifest"]["comfy_commit"] == COMMIT
    assert reviewed["manifest"]["weight_files"][0]["revision"] == REVISION
    assert reviewed["manifest"]["node_allowlist"] == ["CheckpointLoaderSimple"]


def test_real_smoke_rejects_over_budget_before_create(monkeypatch):
    st = Store("sqlite:///:memory:")
    repo = Repository(st)
    model = valid_model(repo)
    repo.update("models", model["id"], {"review_approved": True, "license_accepted": True}, OWNER["id"])
    workflow = valid_workflow(repo, model["model_id"])
    repo.update("workflows", workflow["id"], {"review_approved": True}, OWNER["id"])
    st.create("projects", OWNER["id"], {"title": "p"}, record_id="project-1")
    repo.create("accounts", OWNER["id"], {"enabled": True, "allow_allocation": True}, record_id="smoke-account")
    # Pure policy injection: never enable the real environment switch.
    from app.gpu.compute import GpuConfig
    monkeypatch.setattr(review_routes.GpuConfig, 'from_env',
                        lambda: GpuConfig(real_enabled=True, unit_rate=2, max_job_units=5))
    monkeypatch.setattr(review_routes.GoogleService, "status", lambda self: {"colab": {"connected": True}})
    monkeypatch.setattr(
        review_routes.GoogleColabProvider,
        "list_specs",
        lambda self: [
            {
                "eligible": True,
                "key": {"variant": "GPU", "accelerator": "T4", "shape": "STANDARD"},
                "vram_gb": 16,
            }
        ],
    )

    with pytest.raises(review_routes.APIError) as exc:
        review_routes.real_smoke(
            workflow["id"],
            {
                "confirmed": True,
                "project_id": "project-1",
                "account_id": "smoke-account",
                "params": {"prompt": "x"},
                "asset_ids": [],
                "spec": {"key": {"variant": "GPU", "accelerator": "T4", "shape": "STANDARD"}},
            },
            request({"Idempotency-Key": "smoke-1"}),
            OWNER,
            st,
        )
    assert exc.value.code == "GPU_BUDGET_EXCEEDED"
    assert repo.list("jobs", OWNER["id"]) == []


def test_accept_smoke_uses_server_job_and_verified_output(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    st = Store("sqlite:///:memory:")
    repo = Repository(st)
    model = valid_model(repo)
    repo.update("models", model["id"], {"review_approved": True, "license_accepted": True}, OWNER["id"])
    workflow = valid_workflow(repo, model["model_id"])
    repo.update("workflows", workflow["id"], {"review_approved": True}, OWNER["id"])
    st.create("projects", OWNER["id"], {"title": "p"}, record_id="project-1")
    asset = save_asset(st, OWNER["id"], "project-1", PNG, "smoke.png", metadata={"real_smoke": True})
    raw = {
        "project_id": "project-1",
        "model_id": model["model_id"],
        "workflow_key": workflow["workflow_key"],
        "workflow_version": 1,
        "params": {"prompt": "x"},
        "asset_ids": [],
        "execution_policy": {"provider": "colab", "max_runtime_minutes": 5},
    }
    job, _ = repo.create_job(OWNER["id"], raw, "smoke-1")
    job = repo.update(
        "jobs",
        job["id"],
        {
            "state": "completed",
            "provider": "colab",
            "target_workflow_id": workflow["id"],
            "target_model_id": model["model_id"],
            "workflow_smoke": True,
            "output_asset_ids": [asset["id"]],
            "requested_spec": {"key": {"variant": "GPU", "accelerator": "T4", "shape": "STANDARD"}},
            "gpu_diagnostics": {"device_name": "Tesla T4", "cuda_available": True},
            "smoke_compatibility_signature": review_routes._smoke_compatibility_signature(workflow['id'], workflow, model['revision']),
            "snapshot": {"workflow_smoke": True},
        },
        OWNER["id"],
    )
    repo.create(
        "leases",
        OWNER["id"],
        {
            "owner_job_id": job["id"],
            "provider": "colab",
            "state": "deleted",
            "runtime_id": "runtime-1",
            "request_id": "request-1",
        },
    )

    result = review_routes.accept_smoke(
        workflow["id"], {"job_id": job["id"], "confirmed": True}, request(), OWNER, st
    )

    assert body(result)["smoke_test_passed"] is True
    assert repo.get("models", model["id"], OWNER["id"])["enabled"] is True
    assert (
        repo.get("models", model["id"], OWNER["id"])["tested_compute_specs"]["actual_gpu"]["device_name"]
        == "Tesla T4"
    )
