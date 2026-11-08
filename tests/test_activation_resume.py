from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from app import review_routes
from app.common import APIError
from app.db import Store
from app.gpu.compute import GpuConfig
from app.gpu.repository import Repository

OWNER = {"id": "owner", "role": "admin"}
OTHER = {"id": "other", "role": "admin"}
COMMIT = "a" * 40
REVISION = "b" * 40
SHA256 = "c" * 64


def request(headers=None):
    return SimpleNamespace(headers=headers or {}, state=SimpleNamespace(request_id="req-1"))


def body(response):
    return json.loads(response.body)["data"]


def setup_workflow(repo: Repository, *, owner: str = "owner", version=1, workflow_key="resume-demo"):
    repo.create(
        "models",
        owner,
        {
            "model_id": "resume-model",
            "source": "https://huggingface.co/acme/model",
            "revision": REVISION,
            "license": "apache-2.0",
            "resource_profile": {"vram_gb": 8, "ram_gb": 16},
            "enabled": False,
            "is_mock": False,
            "review_approved": True,
            "license_accepted": True,
        },
    )
    manifest = {
        "manifest_version": 1,
        "model_id": "resume-model",
        "parameters": {
            "prompt": {
                "type": "string",
                "default": "hello",
                "binding": {"node": "1", "input": "text"},
            }
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
        "max_runtime_minutes": 20,
    }
    return repo.create(
        "workflows",
        owner,
        {
            "workflow_key": workflow_key,
            "workflow_version": version,
            "model_id": "resume-model",
            "manifest": manifest,
            "api_graph": {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"text": "hello"}}},
            "object_info": {"CheckpointLoaderSimple": {"input": {"required": {"text": ["STRING"]}}}},
            "state": "validated",
            "review_approved": True,
            "smoke_test_passed": False,
        },
    )


def enable_fake_real_smoke(monkeypatch):
    monkeypatch.setattr(
        review_routes.GpuConfig,
        "from_env",
        lambda: GpuConfig(
            real_enabled=True,
            unit_rate=0.1,
            max_runtime_minutes=5,
            daily_units=100,
            monthly_units=100,
            max_job_units=5,
        ),
    )
    monkeypatch.setattr(review_routes.GoogleService, "status", lambda self: {"colab": {"connected": True}})
    monkeypatch.setattr(
        review_routes.GoogleColabProvider,
        "list_specs",
        lambda self: [
            {
                "eligible": True,
                "key": {"variant": "GPU", "accelerator": "T4", "shape": "STANDARD"},
            }
        ],
    )


def smoke_body(workflow_id, key, prompt):
    return {
        "confirmed": True,
        "project_id": "project-1",
        "account_id": "account-1",
        "params": {"prompt": prompt},
        "asset_ids": [],
        "spec": {"key": {"variant": "GPU", "accelerator": "T4", "shape": "STANDARD"}},
    }


def test_real_smoke_resumes_compatible_job_even_when_random_params_and_key_change(monkeypatch):
    st = Store("sqlite:///:memory:")
    repo = Repository(st)
    workflow = setup_workflow(repo)
    st.create("projects", OWNER["id"], {"title": "p"}, record_id="project-1")
    repo.create("accounts", OWNER["id"], {"enabled": True, "allow_allocation": True}, record_id="account-1")
    enable_fake_real_smoke(monkeypatch)

    first = body(
        review_routes.real_smoke(
            workflow["id"],
            smoke_body(workflow["id"], "ignored", "first-random-seed"),
            request({"Idempotency-Key": "smoke-first"}),
            OWNER,
            st,
        )
    )
    second = body(
        review_routes.real_smoke(
            workflow["id"],
            smoke_body(workflow["id"], "ignored", "different-random-seed"),
            request({"Idempotency-Key": "smoke-retry-with-new-client-key"}),
            OWNER,
            st,
        )
    )

    assert second["job_id"] == first["job_id"]
    assert len(repo.list("jobs", OWNER["id"])) == 1
    assert second["resumed"] is True


def test_failed_smoke_is_not_reused_and_can_start_a_new_attempt(monkeypatch):
    st = Store("sqlite:///:memory:")
    repo = Repository(st)
    workflow = setup_workflow(repo)
    st.create("projects", OWNER["id"], {"title": "p"}, record_id="project-1")
    repo.create("accounts", OWNER["id"], {"enabled": True, "allow_allocation": True}, record_id="account-1")
    enable_fake_real_smoke(monkeypatch)
    compatibility = review_routes._smoke_compatibility_signature(workflow["id"], workflow, REVISION)
    failed, _ = repo.create_job(
        OWNER["id"],
        {
            **smoke_body(workflow["id"], "ignored", "old"),
            "workflow_id": workflow["id"],
            "workflow_key": workflow["workflow_key"],
            "workflow_version": workflow["workflow_version"],
            "model_id": workflow["model_id"],
            "execution_policy": {"provider": "colab", "account_id": "account-1", "max_runtime_minutes": 5},
        },
        "old-failed-attempt",
    )
    repo.update(
        "jobs",
        failed["id"],
        {
            "state": "failed",
            "provider": "colab",
            "target_workflow_id": workflow["id"],
            "target_model_id": workflow["model_id"],
            "workflow_smoke": True,
            "smoke_compatibility_signature": compatibility,
            "snapshot": {"workflow_smoke": True, "smoke_compatibility_signature": compatibility},
        },
        OWNER["id"],
    )

    result = body(
        review_routes.real_smoke(
            workflow["id"],
            smoke_body(workflow["id"], "ignored", "new-attempt"),
            request({"Idempotency-Key": "new-attempt"}),
            OWNER,
            st,
        )
    )

    assert result["job_id"] != failed["id"]
    assert len(repo.list("jobs", OWNER["id"])) == 2


def test_activation_prefers_completed_pending_acceptance_over_newer_failure_and_reports_cap(monkeypatch):
    monkeypatch.setattr(
        review_routes.GpuConfig,
        "from_env",
        lambda: GpuConfig(real_enabled=True, unit_rate=0.1, max_runtime_minutes=5),
    )
    st = Store("sqlite:///:memory:")
    repo = Repository(st)
    workflow = setup_workflow(repo)
    signature = review_routes._smoke_compatibility_signature(workflow["id"], workflow, REVISION)
    completed, _ = repo.create_job(repo_owner := OWNER["id"], {"project_id": "project-1", "execution_policy": {"provider": "colab"}}, "completed")
    repo.update(
        "jobs",
        completed["id"],
        {
            "state": "completed",
            "provider": "colab",
            "target_workflow_id": workflow["id"],
            "target_model_id": workflow["model_id"],
            "workflow_key": workflow["workflow_key"],
            "workflow_version": workflow["workflow_version"],
            "model_id": workflow["model_id"],
            "workflow_smoke": True,
            "smoke_compatibility_signature": signature,
            "snapshot": {"workflow_smoke": True, "smoke_compatibility_signature": signature},
        },
        repo_owner,
    )
    failed, _ = repo.create_job(repo_owner, {"project_id": "project-1", "execution_policy": {"provider": "colab"}}, "failed")
    repo.update(
        "jobs",
        failed["id"],
        {
            "state": "failed",
            "provider": "colab",
            "target_workflow_id": workflow["id"],
            "target_model_id": workflow["model_id"],
            "workflow_key": workflow["workflow_key"],
            "workflow_version": workflow["workflow_version"],
            "model_id": workflow["model_id"],
            "workflow_smoke": True,
            "smoke_compatibility_signature": signature,
            "snapshot": {"workflow_smoke": True, "smoke_compatibility_signature": signature},
        },
        repo_owner,
    )

    data = body(review_routes.workflow_activation(workflow["id"], request(), user=OWNER, st=st))

    assert data["max_runtime_minutes"] == 5
    assert data["smoke"]["job_id"] == completed["id"]
    assert data["smoke"]["state"] == "completed"
    assert data["smoke"]["acceptance_state"] == "pending"
    assert data["smoke"]["reusable"] is True


def test_resume_matching_isolated_by_owner_and_artifact_signature(monkeypatch):
    st = Store("sqlite:///:memory:")
    repo = Repository(st)
    own_workflow = setup_workflow(repo, owner=OWNER["id"], version=1)
    other_workflow = setup_workflow(repo, owner=OTHER["id"], version=1)
    assert own_workflow["id"] != other_workflow["id"]
    assert review_routes._smoke_compatibility_signature(own_workflow["id"], own_workflow, REVISION) != review_routes._smoke_compatibility_signature(other_workflow["id"], other_workflow, REVISION)

    # A stale snapshot for the same workflow id must not be considered current.
    repo.update("workflows", own_workflow["id"], {"workflow_version": 2}, OWNER["id"])
    stale, _ = repo.create_job(OWNER["id"], {"project_id": "project-1", "execution_policy": {"provider": "colab"}}, "stale")
    repo.update(
        "jobs",
        stale["id"],
        {
            "state": "running",
            "provider": "colab",
            "target_workflow_id": own_workflow["id"],
            "target_model_id": own_workflow["model_id"],
            "workflow_key": own_workflow["workflow_key"],
            "workflow_version": 1,
            "model_id": own_workflow["model_id"],
            "workflow_smoke": True,
            "smoke_compatibility_signature": review_routes._smoke_compatibility_signature(own_workflow["id"], own_workflow, REVISION),
            "snapshot": {"workflow_smoke": True},
        },
        OWNER["id"],
    )

    data = body(review_routes.workflow_activation(own_workflow["id"], request(), user=OWNER, st=st))
    assert data["smoke"] is None

    with pytest.raises(APIError) as excinfo:
        review_routes.workflow_activation(other_workflow["id"], request(), user=OWNER, st=st)
    assert excinfo.value.status == 404


def _matching_job(workflow, *, snapshot=None, signature=None, state="completed"):
    return {
        "state": state,
        "target_workflow_id": workflow["id"],
        "target_model_id": workflow["model_id"],
        "workflow_key": workflow["workflow_key"],
        "workflow_version": workflow["workflow_version"],
        "smoke_compatibility_signature": signature,
        "snapshot": snapshot,
    }


def test_explicit_signature_mismatch_cannot_fall_back_to_matching_legacy_fields():
    workflow = setup_workflow(Repository(Store("sqlite:///:memory:")))
    current = review_routes._smoke_compatibility_signature(workflow["id"], workflow, REVISION)
    snapshot = {
        "workflow_smoke": True,
        "workflow_sha256": review_routes._workflow_digest(workflow),
        "model_revision": REVISION,
        "dependency_lock": {
            "comfyui_commit": COMMIT,
            "weight_files": workflow["manifest"]["dependencies"]["weight_files"],
        },
        "manifest": {"custom_nodes": []},
    }

    assert review_routes._job_matches_smoke(
        _matching_job(workflow, snapshot=snapshot, signature="stale-signature"),
        workflow["id"],
        workflow,
        {"model_id": workflow["model_id"], "revision": REVISION},
        current,
    ) is False


def test_legacy_matching_requires_custom_nodes_to_match():
    workflow = setup_workflow(Repository(Store("sqlite:///:memory:")))
    current = review_routes._smoke_compatibility_signature(workflow["id"], workflow, REVISION)
    snapshot = {
        "workflow_smoke": True,
        "workflow_sha256": review_routes._workflow_digest(workflow),
        "model_revision": REVISION,
        "dependency_lock": {
            "comfyui_commit": COMMIT,
            "weight_files": workflow["manifest"]["dependencies"]["weight_files"],
        },
        "manifest": {"custom_nodes": [{"repo": "https://example.invalid/node", "commit": "d" * 40}]},
    }

    assert review_routes._job_matches_smoke(
        _matching_job(workflow, snapshot=snapshot, signature=None),
        workflow["id"],
        workflow,
        {"model_id": workflow["model_id"], "revision": REVISION},
        current,
    ) is False


def test_activation_does_not_use_stale_workflow_smoke_passed_without_current_evidence(monkeypatch):
    monkeypatch.setattr(
        review_routes.GpuConfig,
        "from_env",
        lambda: GpuConfig(real_enabled=True, unit_rate=0.1, max_runtime_minutes=5),
    )
    st = Store("sqlite:///:memory:")
    repo = Repository(st)
    workflow = setup_workflow(repo)
    stale, _ = repo.create_job("owner", {"project_id": "project-1", "execution_policy": {}}, "stale")
    repo.update(
        "jobs",
        stale["id"],
        _matching_job(
            workflow,
            signature="stale-signature",
            snapshot={"workflow_smoke": True},
        ),
        "owner",
    )
    repo.update(
        "workflows",
        workflow["id"],
        {"smoke_test_passed": True, "smoke_test_kind": "real", "smoke_job_id": stale["id"]},
        "owner",
    )

    data = body(review_routes.workflow_activation(workflow["id"], request(), user=OWNER, st=st))

    assert data["smoke"] is None
    assert data["steps"][3]["done"] is False
    assert data["steps"][4]["done"] is False
    assert data["done"] is False


def test_activation_ignores_non_mapping_snapshot_without_server_error(monkeypatch):
    monkeypatch.setattr(
        review_routes.GpuConfig,
        "from_env",
        lambda: GpuConfig(real_enabled=True, unit_rate=0.1, max_runtime_minutes=5),
    )
    st = Store("sqlite:///:memory:")
    repo = Repository(st)
    workflow = setup_workflow(repo)
    job, _ = repo.create_job("owner", {"project_id": "project-1", "execution_policy": {}}, "bad-snapshot")
    repo.update(
        "jobs",
        job["id"],
        {
            "state": "running",
            "target_workflow_id": workflow["id"],
            "target_model_id": workflow["model_id"],
            "workflow_key": workflow["workflow_key"],
            "workflow_version": workflow["workflow_version"],
            "snapshot": [],
        },
        "owner",
    )

    data = body(review_routes.workflow_activation(workflow["id"], request(), user=OWNER, st=st))

    assert data["smoke"] is None
    assert review_routes._job_smoke_signature({"snapshot": False}) is None


def test_accept_smoke_rejects_completed_job_with_stale_fingerprint():
    st = Store("sqlite:///:memory:")
    repo = Repository(st)
    workflow = setup_workflow(repo)
    job, _ = repo.create_job(
        "owner",
        {"project_id": "project-1", "execution_policy": {"provider": "colab"}},
        "stale-acceptance",
    )
    repo.update(
        "jobs",
        job["id"],
        {
            "state": "completed",
            "provider": "colab",
            "target_workflow_id": workflow["id"],
            "target_model_id": workflow["model_id"],
            "workflow_key": workflow["workflow_key"],
            "workflow_version": workflow["workflow_version"],
            "smoke_compatibility_signature": "stale-signature",
            "snapshot": {"workflow_smoke": True},
        },
        "owner",
    )

    with pytest.raises(APIError) as excinfo:
        review_routes.accept_smoke(
            workflow["id"],
            {"job_id": job["id"], "confirmed": True},
            request(),
            OWNER,
            st,
        )

    assert excinfo.value.code == "SMOKE_TARGET_MISMATCH"


def test_complete_legacy_snapshot_remains_reusable():
    workflow = setup_workflow(Repository(Store("sqlite:///:memory:")))
    current = review_routes._smoke_compatibility_signature(workflow["id"], workflow, REVISION)
    legacy = {
        "workflow_smoke": True,
        "workflow_sha256": review_routes._workflow_digest(workflow),
        "model_revision": REVISION,
        "dependency_lock": {
            "comfyui_commit": COMMIT,
            "weight_files": workflow["manifest"]["dependencies"]["weight_files"],
        },
        "manifest": {"custom_nodes": []},
    }

    assert review_routes._job_matches_smoke(
        _matching_job(workflow, snapshot=legacy, signature=None),
        workflow["id"],
        workflow,
        {"model_id": workflow["model_id"], "revision": REVISION},
        current,
    ) is True
