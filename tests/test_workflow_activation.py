from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from app import review_routes
from app.common import APIError
from app.db import Store
from app.gpu.repository import Repository

OWNER = {"id": "owner", "role": "admin"}
GUEST = {"id": "guest", "role": "user"}


def request():
    return SimpleNamespace(headers={}, state=SimpleNamespace(request_id="req-1"))


def body(response):
    return json.loads(response.body)["data"]


def make_rows(repo: Repository):
    repo.create(
        "models",
        OWNER["id"],
        {
            "model_id": "real-model",
            "source": "https://huggingface.co/acme/model",
            "revision": "b" * 40,
            "license": "apache-2.0",
            "resource_profile": {"vram_gb": 8, "ram_gb": 16},
            "enabled": False,
            "is_mock": False,
        },
    )
    return repo.create(
        "workflows",
        OWNER["id"],
        {
            "workflow_key": "krea2-moss-dual-t2i",
            "workflow_version": "1.1.1",
            "model_id": "real-model",
            "state": "draft",
            "active": False,
            "manifest": {"model_id": "real-model", "parameters": {}, "dependencies": {}},
            "api_graph": {},
            "object_info": {},
        },
    )


def test_activation_reports_the_next_step_for_a_fresh_draft():
    store = Store("sqlite:///:memory:")
    row = make_rows(Repository(store))
    data = body(review_routes.workflow_activation(row["id"], request(), user=OWNER, st=store))

    assert data["workflow"]["state"] == "draft"
    assert data["done"] is False
    assert [step["key"] for step in data["steps"]] == [
        "validate",
        "model_review",
        "workflow_review",
        "real_smoke",
        "accept_smoke",
        "publish",
    ]
    assert [step.get("next") for step in data["steps"]].count(True) == 1
    assert data["steps"][0]["next"] is True
    assert data["smoke"] is None
    # 默认离线环境没有开启真实 GPU，前端据此禁用一键启用
    assert any("GPU_REAL_ENABLED" in item for item in data["blockers"])


def test_activation_reflects_evidence_already_on_record():
    store = Store("sqlite:///:memory:")
    repo = Repository(store)
    row = make_rows(repo)
    model = next(item for item in repo.list("models", OWNER["id"]) if item["model_id"] == "real-model")
    repo.update("models", model["id"], {"review_approved": True, "license_accepted": True, "enabled": True}, OWNER["id"])
    repo.update(
        "workflows",
        row["id"],
        {"state": "published", "active": True, "review_approved": True, "smoke_test_passed": True, "smoke_test_kind": "real"},
        OWNER["id"],
    )
    data = body(review_routes.workflow_activation(row["id"], request(), user=OWNER, st=store))

    # Historical flags alone cannot attest the current artifact.
    steps = {step['key']: step for step in data['steps']}
    assert steps['real_smoke']['done'] is False
    assert steps['accept_smoke']['done'] is False
    # Published is an existing deployment state; evidence steps remain invalid.
    assert data["done"] is False
    assert data["model"]["enabled"] is True


def test_activation_is_admin_only():
    store = Store("sqlite:///:memory:")
    row = make_rows(Repository(store))
    with pytest.raises(APIError) as excinfo:
        review_routes.workflow_activation(row["id"], request(), user=GUEST, st=store)
    assert excinfo.value.status == 403
