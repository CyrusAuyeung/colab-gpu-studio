from __future__ import annotations

import uuid

import pytest
from app.batch_routes import build_batch_router
from app.common import APIError, current_user, get_store
from app.db import Store
from app.job_schemas import JobInput, Policy, normalize_policy
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError


def test_legacy_policy_dump_does_not_gain_v020_defaults():
    policy = Policy()
    assert policy.model_dump() == {
        "provider": "mock",
        "shutdown_after_job": True,
        "max_runtime_minutes": 60,
        "idle_grace_minutes": 0,
    }
    assert normalize_policy(policy) == {
        "provider": "mock",
        "shutdown_after_job": True,
        "max_runtime_minutes": 60,
        "idle_grace_minutes": 0,
        "allocation": "auto",
        "account_id": None,
        "instance_id": None,
        "lifecycle": "save",
        "retain_idle_minutes": 15,
    }


def test_new_policy_is_normalized_and_instance_implies_existing_allocation():
    instance_id = str(uuid.uuid4())
    policy = Policy(instance_id=instance_id, lifecycle="retain", retain_idle_minutes=30)
    assert normalize_policy(policy)["allocation"] == "existing"
    assert normalize_policy(policy)["instance_id"] == instance_id
    assert normalize_policy(policy)["lifecycle"] == "retain"


def test_existing_allocation_requires_instance():
    with pytest.raises(ValidationError):
        Policy(allocation="existing")


def test_explicit_legacy_and_new_lifecycle_conflict():
    with pytest.raises(ValidationError):
        Policy(shutdown_after_job=True, idle_grace_minutes=0, lifecycle="retain")


def test_retain_idle_minutes_is_bounded():
    with pytest.raises(ValidationError):
        Policy(lifecycle="retain", retain_idle_minutes=31)
    with pytest.raises(ValidationError):
        Policy(lifecycle="retain", retain_idle_minutes=0)


def _batch_client(tmp_path, calls):
    app = FastAPI()

    async def api_error(_request: Request, exc: APIError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    def admit(st, user, body, key, *, validate_only=False):
        calls.append(body)
        return {**body, "requested_account_id": body["execution_policy"].get("account_id"),
                "requested_instance_id": body["execution_policy"].get("instance_id")}

    app.add_exception_handler(APIError, api_error)
    app.include_router(build_batch_router(JobInput, admit, lambda job: job), prefix="/api/gpu")
    store = Store(f"sqlite:///{tmp_path / 'batch.sqlite'}")
    app.dependency_overrides[get_store] = lambda: store
    app.dependency_overrides[current_user] = lambda: {"id": "u1", "role": "user"}
    return TestClient(app), store


def _job(policy):
    return {
        "project_id": "p",
        "model_id": "m",
        "workflow_key": "w",
        "workflow_version": 1,
        "execution_policy": policy,
    }


def test_mock_batch_supports_retain_and_unifies_all_children(tmp_path):
    calls = []
    client, store = _batch_client(tmp_path, calls)
    instance_id = str(uuid.uuid4())
    body = {
        "jobs": [_job({"provider": "mock", "instance_id": instance_id}),
                 _job({"provider": "mock", "instance_id": instance_id})],
        "mode": "retain",
    }
    response = client.post("/api/gpu/batches", json=body, headers={"Idempotency-Key": "retain-batch"})
    assert response.status_code == 202, response.text
    assert len(calls) == 2
    assert {call["execution_policy"]["lifecycle"] for call in calls} == {"retain"}
    assert {call["execution_policy"]["instance_id"] for call in calls} == {instance_id}


def test_batch_rejects_mixed_lifecycle_and_allocation(tmp_path):
    calls = []
    client, _ = _batch_client(tmp_path, calls)
    body = {"jobs": [_job({"provider": "mock", "lifecycle": "save"}),
                      _job({"provider": "mock", "lifecycle": "retain"})], "mode": "save"}
    response = client.post("/api/gpu/batches", json=body, headers={"Idempotency-Key": "mixed"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "POLICY_INVALID"
    assert calls == []


def test_mock_account_id_roundtrips_instance_directory():
    from app.job_schemas import Policy
    value = "mock-a31a4c738024542394d0376d8493ceb1"
    assert Policy(provider="mock", account_id=value).account_id == value
